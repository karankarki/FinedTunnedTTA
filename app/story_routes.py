"""Story video API: POST a credit profile, get back a playable story (narration + timeline).

Generated stories are stored on disk by a hash of the input, so repeating a request within the
retention window is instant. Every story folder is deleted STORY_TTL_HOURS (default 24) after it
was generated. Files are served from /stories/<story_id>/, and the player page from /player/.
"""
import asyncio
import os
import shutil
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Body, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import BASE_DIR, OUTPUTS_DIR
from app.crif_report import CrifFormatError
from app.crif_story import generate_crif_stories, prepare, report_summary
from app.story_engine import LANGUAGE_LABELS, StoryInput, generate_stories

STORIES_DIR = OUTPUTS_DIR / "stories"
FRONTEND_DIR = BASE_DIR / "frontend"

STORY_TTL_SECONDS = float(os.environ.get("STORY_TTL_HOURS", "24")) * 3600
SWEEP_EVERY_SECONDS = 15 * 60

router = APIRouter(tags=["Story video"])
_locks: Dict[str, asyncio.Lock] = {}
_sweeper: Optional[asyncio.Task] = None


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
        lock = _locks.get(folder.name)
        if not folder.is_dir() or (lock and lock.locked()):
            continue
        if now - _created_at(folder) > STORY_TTL_SECONDS:
            shutil.rmtree(folder, ignore_errors=True)
            _locks.pop(folder.name, None)
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


def _files(story_id: str):
    folder = STORIES_DIR / story_id
    return folder, (lambda lang: folder / f"story.{lang}.json"), (lambda lang: folder / f"audio.{lang}.mp3")


@router.post("/api/story")
async def create_story(req: StoryRequest):
    """Generate (or reuse) the narrated story video for a credit profile."""
    fields = req.model_dump()
    if fields["active_credit_cards"] == 0:
        fields["credit_utilization_pct"] = 0.0
    fields["customer_name"] = fields["customer_name"].strip()
    if fields["customer_name_hi"]:
        fields["customer_name_hi"] = fields["customer_name_hi"].strip() or None
    inp = StoryInput(**fields)
    story_id = inp.story_id()
    folder, json_path, audio_path = _files(story_id)
    sweep_expired_stories()

    lock = _locks.setdefault(story_id, asyncio.Lock())
    async with lock:
        cached = all(json_path(l).is_file() and audio_path(l).is_file() for l in inp.languages)
        if not cached:
            try:
                await generate_stories(inp, json_path, audio_path)
            except Exception as err:
                shutil.rmtree(folder, ignore_errors=True)
                print(f"[!] Story generation failed: {err}")
                raise HTTPException(status_code=502, detail=f"Could not generate the story narration: {err}")

    return {
        "story_id": story_id,
        "cached": cached,
        "story_url": f"/stories/{story_id}/story.{inp.languages[0]}.json",
        "expires_at": _expires_at(folder),
        "languages": [{"code": l, "label": LANGUAGE_LABELS[l], "story_url": f"/stories/{story_id}/story.{l}.json"}
                      for l in inp.languages],
        "input": asdict(inp),
    }


@router.post("/api/story/crif")
async def create_crif_story(payload: Dict[str, Any] = Body(...), languages: Optional[str] = None,
                            customer_name: Optional[str] = None, voice_speed: Optional[float] = None):
    """Generate the detailed narrated walkthrough (7+ minutes) from a CRIF High Mark report.

    The body is the CRIF response exactly as the bureau API returns it. Options can be passed
    as query parameters (?languages=hi,en&customer_name=Waseem&voice_speed=1.0), or the body can
    wrap the report: {"report": {...}, "languages": ["en"], "customer_name": "...", "voice_speed": 1.0}.
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

    folder, json_path, audio_path = _files(story_id)
    sweep_expired_stories()
    lock = _locks.setdefault(story_id, asyncio.Lock())
    async with lock:
        cached = all(json_path(l).is_file() and audio_path(l).is_file() for l in langs)
        if not cached:
            try:
                await generate_crif_stories(p, name, chapters, langs, speed, story_id, json_path, audio_path)
            except Exception as err:
                shutil.rmtree(folder, ignore_errors=True)
                print(f"[!] CRIF story generation failed: {err}")
                raise HTTPException(status_code=502, detail=f"Could not generate the story narration: {err}")

    return {
        "story_id": story_id,
        "cached": cached,
        "story_url": f"/stories/{story_id}/story.{langs[0]}.json",
        "expires_at": _expires_at(folder),
        "languages": [{"code": l, "label": LANGUAGE_LABELS[l], "story_url": f"/stories/{story_id}/story.{l}.json"} for l in langs],
        "summary": report_summary(p),
        "chapters": [c["chapter"] for c in chapters],
    }


def register_story_routes(app: FastAPI):
    """Add the story API plus static serving for generated stories and the player page."""
    STORIES_DIR.mkdir(parents=True, exist_ok=True)
    app.router.on_startup.append(_start_sweeper)
    app.include_router(router)
    app.mount("/stories", StaticFiles(directory=STORIES_DIR), name="stories")
    if FRONTEND_DIR.is_dir():
        app.mount("/player", StaticFiles(directory=FRONTEND_DIR, html=True), name="player")
