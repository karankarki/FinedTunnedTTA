"""Story API: POST a CRIF High Mark report, get back the narrated video in Hindi and English.

For each language the response carries one MP3 (full.<lang>.mp3) and its animation timeline
JSON (full.<lang>.json, on that MP3's clock), with the JSON included inline.

Stories are stored on disk by a hash of the input, so the same report again within the
retention window comes back instantly. Every story folder is deleted STORY_TTL_HOURS (default
24) after it was generated. Files are served from /stories/<story_id>/.
"""
import asyncio
import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import OUTPUTS_DIR
from app.metrics import RequestMeter, server_stats
from app.crif_report import CrifFormatError
from app.crif_story import CrifStoryJob, prepare, report_summary
from app.story_engine import (LANGUAGE_LABELS, SegmentedStoryJob, full_files, is_complete, join_segments, read_manifest,
                              segment_files, story_meta, write_json)

STORIES_DIR = OUTPUTS_DIR / "stories"

STORY_TTL_SECONDS = float(os.environ.get("STORY_TTL_HOURS", "24")) * 3600
SWEEP_EVERY_SECONDS = 15 * 60
COMPLETE_WAIT = float(os.environ.get("STORY_WAIT_SECONDS", "240"))   # longest the API holds a request
STAGE_WAIT = float(os.environ.get("STAGE_WAIT_SECONDS", "90"))       # longest a stage request waits for it
DEFAULT_LANGUAGES = ["hi", "en"]

log = logging.getLogger("story")
router = APIRouter(tags=["Story"])
_jobs: Dict[str, SegmentedStoryJob] = {}   # stories still being recorded, by story id
_sweeper: Optional[asyncio.Task] = None
_stage_locks: Dict[str, asyncio.Lock] = {}   # one join at a time per stage file


def _created_at(folder) -> float:
    stamps = [f.stat().st_mtime for f in folder.glob("story.*.json")]
    return min(stamps) if stamps else folder.stat().st_mtime


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
                log.info("Deleted %d expired stor%s", removed, "y" if removed == 1 else "ies")
        except Exception as err:
            log.exception("Story cleanup failed")
        await asyncio.sleep(SWEEP_EVERY_SECONDS)


async def _start_sweeper():
    global _sweeper
    if _sweeper is None or _sweeper.done():
        _sweeper = asyncio.get_running_loop().create_task(_sweep_forever())


def _start_job(job: SegmentedStoryJob) -> SegmentedStoryJob:
    job.started = time.time()
    log.info("Story %s: recording started (%s, %d segments, languages %s)",
             job.sid, type(job).__name__, job.count, ",".join(job.langs))
    for lang in job.langs:
        job.write_manifest(lang)   # the story URL works from the first moment, even before stage 1
    job.task = asyncio.get_running_loop().create_task(job.run())
    _jobs[job.sid] = job

    def done(task: asyncio.Task):
        if _jobs.get(job.sid) is job:
            del _jobs[job.sid]
        if task.cancelled():
            log.warning("Story %s: recording cancelled", job.sid)
        elif task.exception():
            log.error("Story %s crashed", job.sid, exc_info=task.exception())
        else:
            log.info("Story %s: finished in %.1f s (%s)%s", job.sid, time.time() - job.started, ", ".join(
                f"{l} {job.ready_count(l)}/{job.count} segments{', full MP3+JSON' if job.full_ready[l] else ''}"
                for l in job.langs), f", error: {job.error}" if job.error else "")

    job.task.add_done_callback(done)
    return job


def _language(request: Request, story_id: str, lang: str, include_json: bool) -> Optional[dict]:
    """One language of a finished story: MP3 URL, JSON URL, duration and (optionally) the JSON itself."""
    folder = STORIES_DIR / story_id
    audio, data = full_files(lang)
    if not ((folder / audio).is_file() and (folder / data).is_file()):
        return None
    base = str(request.base_url).rstrip("/") + f"/stories/{story_id}"
    timeline = json.loads((folder / data).read_text(encoding="utf-8"))
    out = {
        "label": LANGUAGE_LABELS.get(lang, lang),
        "audio_url": f"{base}/{audio}",
        "json_url": f"{base}/{data}",
        "duration": timeline.get("duration"),
    }
    if include_json:
        out["json"] = timeline
    return out


def _result(request: Request, story_id: str, langs: List[str], include_json: bool, cached: bool) -> dict:
    folder = STORIES_DIR / story_id
    manifest = read_manifest(folder, langs[0]) or {}
    job = _jobs.get(story_id)
    if is_complete(folder, langs):
        status = "ready"
    elif job is not None and not job.task.done():
        status = "generating"
    else:
        status = "failed"
    out = {
        "story_id": story_id,
        "status": status,
        "cached": cached,
        **({"error": manifest["error"]} if status != "ready" and manifest.get("error") else {}),
        "expires_at": _expires_at(folder),
        "languages": langs,
    }
    for lang in langs:
        out[lang] = _language(request, story_id, lang, include_json)
    if status == "generating":
        done = sum(d is not None for l in langs for d in job.durations[l])
        out["progress"] = {"segments_ready": done, "segments_total": job.count * len(langs)}
        out["poll_url"] = str(request.base_url).rstrip("/") + f"/api/story/{story_id}"
    return out


def _options(payload: Any, languages: Optional[str], customer_name: Optional[str], voice_speed: Optional[float]):
    """The report plus options, from the query string or a {"report": {...}, ...} wrapper."""
    wrapped = isinstance(payload, dict) and "report" in payload
    report = payload["report"] if wrapped else payload
    langs = (payload.get("languages") if wrapped else None) or (languages.split(",") if languages else DEFAULT_LANGUAGES)
    name = (payload.get("customer_name") if wrapped else None) or customer_name
    speed = float((payload.get("voice_speed") if wrapped else None) or voice_speed or 1.05)
    if not 0.7 <= speed <= 1.3:
        raise HTTPException(status_code=422, detail="voice_speed must be between 0.7 and 1.3")
    if name is not None and len(str(name).strip()) > 40:
        raise HTTPException(status_code=422, detail="customer_name must be at most 40 characters")
    return report, [str(l).strip() for l in langs], name, speed


@router.post("/api/story")
@router.post("/api/story/crif", include_in_schema=False)   # old path, same behaviour
async def create_story(request: Request, payload: Dict[str, Any] = Body(...), languages: Optional[str] = None,
                       customer_name: Optional[str] = None, voice_speed: Optional[float] = None,
                       include_json: bool = True):
    """Generate (or reuse) the narrated credit report video from a CRIF High Mark report.

    Body: the CRIF response exactly as the bureau API returns it. Waits until the story is
    recorded and answers with, for each language ("hi" and "en" by default):
    {"audio_url", "json_url", "duration", "json": <the full animation timeline>}.
    Options: ?languages=hi,en  ?customer_name=Karan  ?voice_speed=1.05  ?include_json=false
    (URLs only). A new report takes a few seconds to a minute; the same report again is instant.
    If recording takes longer than the server will wait, the answer is HTTP 202 with
    "status": "generating" and a poll_url (GET /api/story/<story_id>).
    """
    meter = RequestMeter()
    try:
        return await _create_story(request, payload, languages, customer_name, voice_speed, include_json, meter)
    finally:
        meter.finish()   # stops the memory sampler even when the request fails


def _begin(payload, languages, customer_name, voice_speed):
    """Parse the report and start (or join, or reuse) its recording. Returns the parsed story, the
    running job (None when cached) and whether it was cached."""
    report, langs, name, speed = _options(payload, languages, customer_name, voice_speed)
    try:
        p, name, chapters, langs, story_id = prepare(report, name, langs, speed)
    except CrifFormatError as err:
        log.warning("Story rejected: %s", err)
        raise HTTPException(status_code=422, detail=str(err))
    log.info("Story %s: %d chapters, languages %s", story_id, len(chapters), ",".join(langs))
    folder = STORIES_DIR / story_id
    sweep_expired_stories()
    job = _jobs.get(story_id)
    cached = job is None and is_complete(folder, langs)
    if cached:
        log.info("Story %s: served from cache", story_id)
    else:
        # A story that was interrupted (failed segments, server restart) resumes from what is on disk.
        job = job or _start_job(CrifStoryJob(p, name, chapters, langs, speed, story_id, folder))
    return p, chapters, langs, story_id, job, cached


async def _create_story(request, payload, languages, customer_name, voice_speed, include_json, meter: RequestMeter):
    p, chapters, langs, story_id, job, cached = _begin(payload, languages, customer_name, voice_speed)
    if not cached:
        try:
            await asyncio.wait_for(asyncio.shield(job.task), COMPLETE_WAIT)
        except asyncio.TimeoutError:
            log.warning("Story %s: still recording after %.0f s, answering 202", story_id, COMPLETE_WAIT)
        except Exception:
            pass   # the job logs its own crash; the status below says failed
        if job.start_failed:
            raise HTTPException(status_code=502, detail=f"Could not generate the story narration: {job.error}")

    starts = [not c.get("continues") for c in chapters]   # the welcome is recorded in two parts
    out = _result(request, story_id, langs, include_json, cached)
    out["summary"] = report_summary(p)
    out["chapters"] = [c["chapter"] for c, s in zip(chapters, starts) if s]
    if out["status"] == "failed":
        raise HTTPException(status_code=502, detail=f"Could not generate the story: {out.get('error', 'unknown error')}")
    # CPU and memory of the whole server (plus ffmpeg) while this request ran
    out["metrics"] = meter.finish()
    log.info("Story %s: %.1f s, %.1f CPU-s (%.2f cores avg), memory peak %.0f MB",
             story_id, out["metrics"]["response_time_s"], out["metrics"]["cpu_seconds"],
             out["metrics"]["cpu_cores_used_avg"], out["metrics"]["memory_peak_mb"])
    return JSONResponse(out, status_code=200 if out["status"] == "ready" else 202)


# ------------------------------------------------------------------------------------ stages
# One stage (chapter) at a time, so an app can start playing within seconds and fetch the next
# stage while the current one plays. A stage is one recorded segment, or several joined (the
# welcome is recorded in two parts); its audio and JSON are on the stage's own clock.

def _stage_groups(manifest: dict) -> List[List[int]]:
    """Segment indices of each stage: a segment marked `continues` belongs to the stage before it."""
    groups: List[List[int]] = []
    for i, seg in enumerate(manifest.get("segments") or []):
        if seg.get("continues") and groups:
            groups[-1].append(i)
        else:
            groups.append([i])
    return groups


def _segments_ready(story_id: str, langs: List[str], idxs: List[int]) -> bool:
    folder, job = STORIES_DIR / story_id, _jobs.get(story_id)
    for lang in langs:
        for i in idxs:
            if job is not None and lang in job.durations:
                if job.durations[lang][i] is None:
                    return False
            elif not all((folder / f).is_file() for f in segment_files(lang, i)):
                return False
    return True


async def _wait_for_stage(story_id: str, langs: List[str], idxs: List[int], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if _segments_ready(story_id, langs, idxs):
            return True
        job = _jobs.get(story_id)
        if job is None or job.task.done() or time.monotonic() >= deadline:
            return _segments_ready(story_id, langs, idxs)
        await asyncio.sleep(0.25)


async def _stage_media(request: Request, story_id: str, lang: str, number: int, idxs: List[int],
                       manifest: dict, include_json: bool) -> dict:
    """Audio and JSON of one stage in one language, joining its segments first if it has several."""
    folder = STORIES_DIR / story_id
    base = str(request.base_url).rstrip("/") + f"/stories/{story_id}"
    if len(idxs) == 1:
        audio, data = segment_files(lang, idxs[0])
        content = json.loads((folder / data).read_text(encoding="utf-8"))
        timeline = {"duration": content.get("duration"), "scenes": content.get("scenes") or [],
                    "captions": content.get("captions") or []}
    else:
        audio, data = f"stages/{lang}/{number:02d}.mp3", f"stages/{lang}/{number:02d}.json"
        lock = _stage_locks.setdefault(f"{story_id}/{audio}", asyncio.Lock())
        async with lock:
            if not ((folder / audio).is_file() and (folder / data).is_file()):
                segs = []
                for i in idxs:
                    a, d = segment_files(lang, i)
                    dur = json.loads((folder / d).read_text(encoding="utf-8"))["duration"]
                    segs.append({**manifest["segments"][i], "audio": a, "data": d, "duration": dur})
                scenes, captions, _, duration = await join_segments(folder, segs, folder / audio)
                write_json(folder / data, {"duration": duration, "scenes": scenes, "captions": captions})
        timeline = json.loads((folder / data).read_text(encoding="utf-8"))
    out = {"label": LANGUAGE_LABELS.get(lang, lang), "audio_url": f"{base}/{audio}",
           "json_url": f"{base}/{data}", "duration": timeline.get("duration")}
    if include_json:
        out["json"] = timeline
    return out


async def _stage_response(request: Request, story_id: str, langs: List[str], number: int,
                          include_json: bool, wait: float, with_meta: bool):
    folder = STORIES_DIR / story_id
    manifest = read_manifest(folder, langs[0])
    if manifest is None:
        raise HTTPException(status_code=404, detail="Story not found (it may have expired). POST the report again.")
    groups = _stage_groups(manifest)
    total = len(groups)
    if not 1 <= number <= total:
        raise HTTPException(status_code=404, detail=f"This story has stages 1 to {total}.")
    idxs = groups[number - 1]
    root = str(request.base_url).rstrip("/")
    head = {"story_id": story_id, "stage": number, "total_stages": total,
            "chapter": manifest["segments"][idxs[0]].get("chapter"), "languages": langs}
    if not await _wait_for_stage(story_id, langs, idxs, wait):
        job = _jobs.get(story_id)
        if job is not None and not job.task.done():
            return JSONResponse({**head, "status": "generating", "retry_after": 2,
                                 "retry_url": f"{root}/api/story/{story_id}/stage/{number}"}, status_code=202)
        error = (read_manifest(folder, langs[0]) or {}).get("error")
        raise HTTPException(status_code=502 if error else 404,
                            detail=f"Stage {number} could not be recorded: {error}" if error
                            else "Stage not recorded (the story expired or the server restarted). POST the report again.")
    out = {**head, "status": "ready"}
    for lang in langs:
        out[lang] = await _stage_media(request, story_id, lang, number, idxs, manifest, include_json)
    out["next_stage"] = number + 1 if number < total else None
    out["next_url"] = f"{root}/api/story/{story_id}/stage/{number + 1}" if number < total else None
    if with_meta:
        out["chapters"] = [manifest["segments"][g[0]].get("chapter") for g in groups]
        out["story"] = story_meta(manifest)   # intro, end card, canvas, palette: same for every stage
    return JSONResponse(out)


@router.post("/api/story/stages")
async def start_story_stages(request: Request, payload: Dict[str, Any] = Body(...), languages: Optional[str] = None,
                             customer_name: Optional[str] = None, voice_speed: Optional[float] = None,
                             include_json: bool = True):
    """Start a story from a CRIF report and get stage 1 as soon as it is recorded (a few seconds).

    Body: the CRIF High Mark response, unchanged. The response has stage 1 in each language
    ({"audio_url", "json_url", "duration", "json": {"scenes", "captions"}}), total_stages, the
    chapter list, the story's intro/end card/canvas/palette, and next_url: call it to get stage 2,
    and keep following next_url until it is null. The rest of the story records in the background.
    """
    meter = RequestMeter()
    try:
        p, chapters, langs, story_id, job, cached = _begin(payload, languages, customer_name, voice_speed)
        if job is not None:
            await asyncio.wait_for(asyncio.shield(job.first_ready.wait()), STAGE_WAIT)
            if job.start_failed:
                raise HTTPException(status_code=502, detail=f"Could not generate the story narration: {job.error}")
        response = await _stage_response(request, story_id, langs, 1, include_json, STAGE_WAIT, with_meta=True)
        if response.status_code == 200:
            body = json.loads(response.body)
            body["summary"] = report_summary(p)
            body["metrics"] = meter.finish()
            log.info("Story %s: stage 1 of %d answered in %.1f s", story_id, body["total_stages"],
                     body["metrics"]["response_time_s"])
            return JSONResponse(body)
        return response
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Stage 1 took too long to record. Try again.")
    finally:
        meter.finish()


@router.get("/api/story/{story_id}/stage/{number}")
async def get_story_stage(story_id: str, number: int, request: Request, languages: Optional[str] = None,
                          include_json: bool = True, wait: float = STAGE_WAIT):
    """Stage `number` (1-based) of a story started with POST /api/story/stages (or POST /api/story).

    Waits up to `wait` seconds while it records, then answers 200 with the stage, or 202 with
    retry_url if it is still recording. Each response has next_url for the following stage.
    """
    folder = STORIES_DIR / story_id
    if not story_id.isalnum() or not folder.is_dir():
        raise HTTPException(status_code=404, detail="Story not found (it may have expired). POST the report again.")
    wanted = languages.split(",") if languages else DEFAULT_LANGUAGES
    langs = [l for l in wanted if (folder / f"story.{l}.json").is_file()]
    if not langs:
        raise HTTPException(status_code=404, detail="Story not found in the requested language")
    return await _stage_response(request, story_id, langs, number, include_json,
                                 max(0.0, min(wait, STAGE_WAIT)), with_meta=number == 1)


@router.get("/api/metrics")
def get_metrics():
    """Live CPU and memory utilisation of the server, plus how many stories are recording now."""
    return {**server_stats(), "stories_recording": len(_jobs)}


@router.get("/api/story/{story_id}")
def get_story(story_id: str, request: Request, languages: Optional[str] = None, include_json: bool = True):
    """A story made earlier: same shape as POST /api/story. Poll it after a 202 until "status" is "ready"."""
    folder = STORIES_DIR / story_id
    if not story_id.isalnum() or not folder.is_dir():
        raise HTTPException(status_code=404, detail="Story not found (it may have expired)")
    wanted = languages.split(",") if languages else DEFAULT_LANGUAGES
    langs = [l for l in wanted if (folder / f"story.{l}.json").is_file()]
    if not langs:
        raise HTTPException(status_code=404, detail="Story not found in the requested language")
    return _result(request, story_id, langs, include_json, cached=False)


def register_story_routes(app: FastAPI):
    """Add the story API plus static serving for generated stories (MP3 + JSON)."""
    STORIES_DIR.mkdir(parents=True, exist_ok=True)
    app.router.on_startup.append(_start_sweeper)
    app.include_router(router)
    app.mount("/stories", StaticFiles(directory=STORIES_DIR), name="stories")
