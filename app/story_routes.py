"""Story video API: POST a credit profile, get back a playable story (narration + timeline).

Generated stories are stored on disk by a hash of the input, so repeating a request within the
retention window is instant. Every story folder is deleted STORY_TTL_HOURS (default 24) after it
was generated. Files are served from /stories/<story_id>/.

Both story types are recorded segment by segment in a background job. The API waits at most
`wait` seconds (default 2.5) for the first, short segment ("stage 1"): usually 1-2 seconds, so the
response carries playable audio, and a client can pick up the other segments while it plays.
Once every segment is recorded they are joined into one MP3 (full.<lang>.mp3) and one animation
timeline on that MP3's clock (full.<lang>.json). Pass ?complete=true to wait for those, or poll
GET /api/story/<story_id>.
"""
import asyncio
import os
import shutil
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Literal, Optional

from fastapi import APIRouter, Body, FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import OUTPUTS_DIR
from app.crif_report import CrifFormatError
from app.crif_story import CrifStoryJob, prepare, report_summary
from app.story_engine import (LANGUAGE_LABELS, SEGMENTED_SCHEMA, QuickStoryJob, SegmentedStoryJob, StoryInput,
                              full_files, is_complete, read_manifest)

STORIES_DIR = OUTPUTS_DIR / "stories"

STORY_TTL_SECONDS = float(os.environ.get("STORY_TTL_HOURS", "24")) * 3600
SWEEP_EVERY_SECONDS = 15 * 60
FIRST_SEGMENT_WAIT = 2.5   # seconds the API waits for stage 1 before answering anyway
COMPLETE_WAIT = 180        # with ?complete=true, seconds the API waits for the full MP3 + JSON

WAIT_QUERY = Query(FIRST_SEGMENT_WAIT, ge=0, le=90, description=(
    "Seconds to wait for the first segment (stage 1) before answering. It usually takes 1-2 seconds; "
    "if it isn't ready in time the response has \"stage1\": null and story_url still plays once it is."))
COMPLETE_QUERY = Query(False, description=(
    "Wait until the whole story is recorded and answer with the single full MP3 and its animation JSON "
    f"(\"full\"). A quick summary takes a few seconds, a CRIF walkthrough about a minute; gives up after {COMPLETE_WAIT} s."))

router = APIRouter(tags=["Story video"])
_jobs: Dict[str, SegmentedStoryJob] = {}   # stories still being recorded, by story id
_sweeper: Optional[asyncio.Task] = None


def _created_at(folder) -> float:
    stamps = [f.stat().st_mtime for f in folder.glob("story.*.json")]
    return min(stamps) if stamps else folder.stat().st_mtime


def _full(folder, lang: str, url) -> Optional[dict]:
    """URLs of the joined MP3 and animation JSON, once both exist."""
    if not all((folder / f).is_file() for f in full_files(lang)):
        return None
    audio, data = full_files(lang)
    duration = (read_manifest(folder, lang) or {}).get("duration")
    return {"language": lang, "audio_url": url(audio), "json_url": url(data), "duration": duration}


def _expires_at(folder) -> str:
    return datetime.fromtimestamp(_created_at(folder) + STORY_TTL_SECONDS, timezone.utc).isoformat(timespec="seconds")


def sweep_expired_stories() -> int:
    """Delete every story folder older than the retention window. Returns how many were removed."""
    if not STORIES_DIR.is_dir():
        return 0
    removed, now = 0, time.time()
    for folder in STORIES_DIR.iterdir():
        if not folder.is_dir() or folder.name in _jobs:
            continue
        if now - _created_at(folder) > STORY_TTL_SECONDS:
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
    return removed


async def _sweep_forever():
    while True:
        try:
            removed = sweep_expired_stories()
            if removed:
                print(f"[story] deleted {removed} expired stor{'y' if removed == 1 else 'ies'}")
        except Exception as err:
            print(f"[!] Story cleanup failed: {err}")
        await asyncio.sleep(SWEEP_EVERY_SECONDS)


async def _start_sweeper():
    global _sweeper
    if _sweeper is None or _sweeper.done():
        _sweeper = asyncio.get_running_loop().create_task(_sweep_forever())


def _start_job(job: SegmentedStoryJob) -> SegmentedStoryJob:
    for lang in job.langs:
        job.write_manifest(lang)   # the story URL works from the first moment, even before stage 1
    job.task = asyncio.get_running_loop().create_task(job.run())
    _jobs[job.sid] = job

    def done(task: asyncio.Task):
        if _jobs.get(job.sid) is job:
            del _jobs[job.sid]
        if not task.cancelled() and task.exception():
            print(f"[!] Story job {job.sid} crashed: {task.exception()}")

    job.task.add_done_callback(done)
    return job


async def _serve(request: Request, story_id: str, langs: List[str], make_job: Callable[[], SegmentedStoryJob],
                 wait: float, complete: bool):
    """Start (or join) the recording of a story and wait up to `wait` seconds for its first segment
    (or, with `complete`, for the whole story and its joined MP3 + JSON).

    Returns the response fields both story endpoints share, plus which of the first language's
    segments are recorded. URLs are absolute so a mobile app can use them as they are.
    """
    folder = STORIES_DIR / story_id
    sweep_expired_stories()
    job = _jobs.get(story_id)
    cached = job is None and is_complete(folder, langs)
    if not cached:
        # A story that was interrupted (failed segments, server restart) resumes from what is on disk.
        job = job or _start_job(make_job())
        try:
            if complete:
                await asyncio.wait_for(asyncio.shield(job.task), COMPLETE_WAIT)
            else:
                await asyncio.wait_for(job.first_ready.wait(), wait)
        except asyncio.TimeoutError:
            pass   # still recording: story_url / GET /api/story/<id> pick it up once it is there
        except Exception:
            pass   # the job logs its own crash; its status says failed
        if job.start_failed:
            raise HTTPException(status_code=502, detail=f"Could not generate the story narration: {job.error}")
    fields, ready = _story_fields(request, story_id, langs, None if cached or job.task.done() else job)
    return {"cached": cached, **fields}, ready


def _story_fields(request: Request, story_id: str, langs: List[str], job: Optional[SegmentedStoryJob]):
    """Response fields for a story, from its running job or (when finished) from its files on disk."""
    folder = STORIES_DIR / story_id
    base = str(request.base_url).rstrip("/") + f"/stories/{story_id}"
    url = lambda path: f"{base}/{path}"
    lang = langs[0]
    manifest = read_manifest(folder, lang) or {}
    if job is None:
        segments = manifest.get("segments") or []
        ready = [bool(s.get("ready")) for s in segments]
        first = segments[0] if segments else {}
        stage1 = {"audio": first.get("audio"), "duration": first.get("duration")} if first.get("ready") else None
        status = "ready" if is_complete(folder, langs) else manifest.get("status", "failed")
    else:
        ready = [d is not None for d in job.durations[lang]]
        stage1 = job.stage1(lang)
        status = job.status(lang)
    fields = {
        "story_id": story_id,
        "status": status,
        **({"error": manifest["error"]} if manifest.get("error") else {}),
        # The whole story as one MP3 plus the animation timeline on that MP3's clock (null until recorded).
        "full": _full(folder, lang, url),
        # Segmented form, playable while it records: the manifest lists each segment's MP3 + JSON.
        "story_url": url(f"story.{lang}.json"),
        "stage1": {"language": lang, "audio_url": url(stage1["audio"]), "duration": stage1["duration"]}
        if stage1 else None,
        "expires_at": _expires_at(folder),
        "languages": [{"code": l, "label": LANGUAGE_LABELS[l], "story_url": url(f"story.{l}.json"),
                       "full": _full(folder, l, url)} for l in langs],
    }
    return fields, ready


class StoryRequest(BaseModel):
    customer_name: str = Field("Customer", min_length=1, max_length=40, description="Name shown on screen and spoken in English")
    customer_name_hi: Optional[str] = Field(None, max_length=40, description="Name as spoken in the Hindi narration, e.g. करण")
    credit_score: int = Field(..., ge=300, le=900)
    score_bureau: str = Field("CIBIL", min_length=1, max_length=20)
    report_month: Optional[str] = Field(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="YYYY-MM; defaults to the current month")
    on_time_repayment_pct: float = Field(100.0, ge=0, le=100)
    missed_payments_count: int = Field(0, ge=0, le=36, description="Missed payments in the last 6 months")
    active_credit_cards: int = Field(0, ge=0, le=30)
    credit_utilization_pct: float = Field(0.0, ge=0, le=100, description="Ignored when there are no active cards")
    recent_inquiries: int = Field(0, ge=0, le=50, description="New loan/card applications in the last 6 months")
    languages: List[Literal["hi", "en"]] = Field(default_factory=lambda: ["hi", "en"], min_length=1,
                                                 description="Narrations to generate; the first one plays by default")
    voice_speed: float = Field(0.92, ge=0.7, le=1.3)


@router.post("/api/story")
async def create_story(req: StoryRequest, request: Request, wait: float = WAIT_QUERY, complete: bool = COMPLETE_QUERY):
    """Generate (or reuse) the narrated quick-summary video for a credit profile.

    Answers once stage 1 (the score) is recorded, usually in 1-2 seconds; the other five stages
    keep recording in the background.
    """
    fields = req.model_dump()
    if fields["active_credit_cards"] == 0:
        fields["credit_utilization_pct"] = 0.0
    fields["customer_name"] = fields["customer_name"].strip()
    if fields["customer_name_hi"]:
        fields["customer_name_hi"] = fields["customer_name_hi"].strip() or None
    inp = StoryInput(**fields)
    story_id = inp.story_id(SEGMENTED_SCHEMA)
    common, ready = await _serve(request, story_id, inp.languages,
                                 lambda: QuickStoryJob(inp, STORIES_DIR / story_id), wait, complete)
    return {**common, "stages_ready": sum(ready), "input": asdict(inp)}


@router.post("/api/story/crif")
async def create_crif_story(request: Request, payload: Dict[str, Any] = Body(...), languages: Optional[str] = None,
                            customer_name: Optional[str] = None, voice_speed: Optional[float] = None,
                            wait: float = WAIT_QUERY, complete: bool = COMPLETE_QUERY):
    """Generate the detailed narrated walkthrough (7+ minutes) from a CRIF High Mark report.

    The body is the CRIF response exactly as the bureau API returns it. Options can be passed
    as query parameters (?languages=hi,en&customer_name=Waseem&voice_speed=1.0), or the body can
    wrap the report: {"report": {...}, "languages": ["en"], "customer_name": "...", "voice_speed": 1.0}.

    Answers once stage 1 (the one-line greeting) is recorded, usually in 1-2 seconds, with
    "status": "generating"; the remaining chapters keep recording in the background. A complete
    story comes back with "status": "ready" and "full" (one MP3 + one animation JSON).
    """
    report = payload.get("report", payload) if isinstance(payload, dict) else payload
    langs = (payload.get("languages") if "report" in payload else None) or (languages.split(",") if languages else ["hi", "en"])
    name = (payload.get("customer_name") if "report" in payload else None) or customer_name
    speed = float((payload.get("voice_speed") if "report" in payload else None) or voice_speed or 1.05)
    if not 0.7 <= speed <= 1.3:
        raise HTTPException(status_code=422, detail="voice_speed must be between 0.7 and 1.3")
    if name is not None and len(str(name).strip()) > 40:
        raise HTTPException(status_code=422, detail="customer_name must be at most 40 characters")
    try:
        p, name, chapters, langs, story_id = prepare(report, name, [str(l).strip() for l in langs], speed)
    except CrifFormatError as err:
        raise HTTPException(status_code=422, detail=str(err))

    common, ready = await _serve(request, story_id, langs,
                                 lambda: CrifStoryJob(p, name, chapters, langs, speed, story_id, STORIES_DIR / story_id),
                                 wait, complete)
    # The welcome is recorded in two parts; count and list it as one chapter.
    starts = [not c.get("continues") for c in chapters]
    return {**common,
            "chapters_ready": sum(r and s for r, s in zip(ready, starts)),
            "summary": report_summary(p),
            "chapters": [c["chapter"] for c, s in zip(chapters, starts) if s]}


@router.get("/api/story/{story_id}")
def get_story(story_id: str, request: Request, languages: Optional[str] = None):
    """Status of a story made earlier: poll this until "status" is "ready" and "full" is set.

    `languages` (e.g. hi,en) picks the order; by default every language on disk, Hindi first.
    """
    folder = STORIES_DIR / story_id
    if not story_id.isalnum() or not folder.is_dir():
        raise HTTPException(status_code=404, detail="Story not found (it may have expired)")
    langs = [l for l in (languages.split(",") if languages else ["hi", "en"]) if (folder / f"story.{l}.json").is_file()]
    if not langs:
        raise HTTPException(status_code=404, detail="Story not found in the requested language")
    job = _jobs.get(story_id)
    fields, ready = _story_fields(request, story_id, langs, job)
    return {**fields, "segments_ready": sum(ready), "segments_total": len(ready)}


def register_story_routes(app: FastAPI):
    """Add the story API plus static serving for generated stories (MP3 + JSON)."""
    STORIES_DIR.mkdir(parents=True, exist_ok=True)
    app.router.on_startup.append(_start_sweeper)
    app.include_router(router)
    app.mount("/stories", StaticFiles(directory=STORIES_DIR), name="stories")
