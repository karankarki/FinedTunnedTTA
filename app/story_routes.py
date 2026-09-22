"""Story video API: POST a credit profile, get back a playable story (narration + timeline).

Generated stories are stored on disk by a hash of the input, so repeating a request within the
retention window is instant. Every story folder is deleted STORY_TTL_HOURS (default 24) after it
was generated. Files are served from /stories/<story_id>/, and the player page from /player/.

Both story types are recorded segment by segment in a background job. The API waits at most
`wait` seconds (default 2.5) for the first, short segment ("stage 1"): usually 1-2 seconds, so the
response carries playable audio, and the player picks up the other segments while it plays.
"""
import asyncio
import os
import shutil
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Literal, Optional

from fastapi import APIRouter, Body, FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import BASE_DIR, OUTPUTS_DIR
from app.crif_report import CrifFormatError
from app.crif_story import CrifStoryJob, prepare, report_summary
from app.story_engine import (LANGUAGE_LABELS, SEGMENTED_SCHEMA, QuickStoryJob, SegmentedStoryJob, StoryInput,
                              is_complete, read_manifest)

STORIES_DIR = OUTPUTS_DIR / "stories"
FRONTEND_DIR = BASE_DIR / "frontend"

STORY_TTL_SECONDS = float(os.environ.get("STORY_TTL_HOURS", "24")) * 3600
SWEEP_EVERY_SECONDS = 15 * 60
FIRST_SEGMENT_WAIT = 2.5   # seconds the API waits for stage 1 before answering anyway

WAIT_QUERY = Query(FIRST_SEGMENT_WAIT, ge=0, le=90, description=(
    "Seconds to wait for the first segment (stage 1) before answering. It usually takes 1-2 seconds; "
    "if it isn't ready in time the response has \"stage1\": null and story_url still plays once it is."))

router = APIRouter(tags=["Story video"])
_jobs: Dict[str, SegmentedStoryJob] = {}   # stories still being recorded, by story id
_sweeper: Optional[asyncio.Task] = None


def _created_at(folder) -> float:
    stamps = [f.stat().st_mtime for f in folder.glob("story.*.json")]
    return min(stamps) if stamps else folder.stat().st_mtime


def _player_url(story_url: str) -> str:
    """Player page for embedding (iframe / WebView) that opens this story."""
    return f"/player/?embed=1&story={story_url}"


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


async def _serve(story_id: str, langs: List[str], make_job: Callable[[], SegmentedStoryJob], wait: float):
    """Start (or join) the recording of a story and wait up to `wait` seconds for its first segment.

    Returns the response fields both story endpoints share, plus which of the first language's
    segments are recorded.
    """
    folder = STORIES_DIR / story_id
    sweep_expired_stories()
    job = _jobs.get(story_id)
    cached = job is None and is_complete(folder, langs)
    if not cached:
        # A story that was interrupted (failed segments, server restart) resumes from what is on disk.
        job = job or _start_job(make_job())
        try:
            await asyncio.wait_for(job.first_ready.wait(), wait)
        except asyncio.TimeoutError:
            pass   # still recording stage 1: story_url starts playing as soon as it's there
        if job.start_failed:
            raise HTTPException(status_code=502, detail=f"Could not generate the story narration: {job.error}")

    lang = langs[0]
    base = f"/stories/{story_id}"
    if cached:
        segments = (read_manifest(folder, lang) or {}).get("segments") or []
        ready = [True] * len(segments)
        first = segments[0] if segments else {}
        stage1 = {"audio": first.get("audio"), "duration": first.get("duration")} if first.get("audio") else None
    else:
        ready = [d is not None for d in job.durations[lang]]
        stage1 = job.stage1(lang)
    story_url = f"{base}/story.{lang}.json"
    fields = {
        "story_id": story_id,
        "cached": cached,
        "status": "ready" if cached else job.status(lang),
        "story_url": story_url,
        "player_url": _player_url(story_url),
        "stage1": {"language": lang, "audio_url": f"{base}/{stage1['audio']}", "duration": stage1["duration"]}
        if stage1 else None,
        "expires_at": _expires_at(folder),
        "languages": [{"code": l, "label": LANGUAGE_LABELS[l], "story_url": f"{base}/story.{l}.json"} for l in langs],
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
async def create_story(req: StoryRequest, wait: float = WAIT_QUERY):
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
    common, ready = await _serve(story_id, inp.languages, lambda: QuickStoryJob(inp, STORIES_DIR / story_id), wait)
    return {**common, "stages_ready": sum(ready), "input": asdict(inp)}


@router.post("/api/story/crif")
async def create_crif_story(payload: Dict[str, Any] = Body(...), languages: Optional[str] = None,
                            customer_name: Optional[str] = None, voice_speed: Optional[float] = None,
                            wait: float = WAIT_QUERY):
    """Generate the detailed narrated walkthrough (7+ minutes) from a CRIF High Mark report.

    The body is the CRIF response exactly as the bureau API returns it. Options can be passed
    as query parameters (?languages=hi,en&customer_name=Waseem&voice_speed=1.0), or the body can
    wrap the report: {"report": {...}, "languages": ["en"], "customer_name": "...", "voice_speed": 1.0}.

    Answers once stage 1 (the one-line greeting) is recorded, usually in 1-2 seconds, with
    "status": "generating"; the remaining chapters keep recording in the background and the
    player loads them as they appear. A complete story comes back with "status": "ready".
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

    common, ready = await _serve(story_id, langs,
                                 lambda: CrifStoryJob(p, name, chapters, langs, speed, story_id, STORIES_DIR / story_id), wait)
    # The welcome is recorded in two parts; count and list it as one chapter.
    starts = [not c.get("continues") for c in chapters]
    return {**common,
            "chapters_ready": sum(r and s for r, s in zip(ready, starts)),
            "summary": report_summary(p),
            "chapters": [c["chapter"] for c, s in zip(chapters, starts) if s]}


def register_story_routes(app: FastAPI):
    """Add the story API plus static serving for generated stories and the player page."""
    STORIES_DIR.mkdir(parents=True, exist_ok=True)
    app.router.on_startup.append(_start_sweeper)
    app.include_router(router)
    app.mount("/stories", StaticFiles(directory=STORIES_DIR), name="stories")
    if FRONTEND_DIR.is_dir():
        app.mount("/player", StaticFiles(directory=FRONTEND_DIR, html=True), name="player")
