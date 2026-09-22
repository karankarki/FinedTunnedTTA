"""Story engine: turns a credit profile into narration audio plus a timed story JSON.

The narration text comes from the credit engine's stage scripts, so the story says exactly
what the credit report API would. Audio is synthesized with Azure Neural voices via
edge-tts while capturing word-boundary events; those timings place every scene, beat and
caption of the story JSON that frontend/index.html plays.
"""
import asyncio
import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

import edge_tts
import numpy as np
import soundfile as sf

from app.credit_engine import credit_engine
from app.edge_engine import edge_engine

SCHEMA_VERSION = "2.1"
SAMPLE_RATE = 24000
VOICES = {"hi": "hi-IN-SwaraNeural", "en": "en-IN-NeerjaExpressiveNeural"}
LANGUAGE_LABELS = {"hi": "हिंदी", "en": "English"}
BRAND = {"name": "Oct Credit"}

LEAD = 0.15        # scenes and beats land slightly before the voice does
LEAD_IN = 0.6      # silence before the first stage
STAGE_GAP = 0.35   # same as the credit engine's default gap_duration
TAIL = 1.2         # silence after the last stage

SENTENCE_RE = re.compile(r"[^।!?.]+[।!?.]*")
CLAUSE_SPLIT_RE = re.compile(r"[,:;]|\sऔर\s|\sand\s")

# Band labels follow credit_engine.categorize_credit_score (650 Fair, 725 Good, 775 Excellent),
# so the label on screen always matches the category the narration speaks.
SCORE_BANDS = [
    {"from": 300, "to": 549, "label": "Poor", "color": "#f04438"},
    {"from": 550, "to": 649, "label": "Needs work", "color": "#fb6514"},
    {"from": 650, "to": 724, "label": "Fair", "color": "#fdb022"},
    {"from": 725, "to": 774, "label": "Good", "color": "#66c61c"},
    {"from": 775, "to": 900, "label": "Excellent", "color": "#12b76a"},
]

STEPS_BY_FOCUS = {
    "secured_card": [
        {"icon": "card", "title": "Get an FD-backed secured card",
         "text": "Approval is easy because your deposit backs the limit."},
        {"icon": "wallet", "title": "Use it for small monthly spends",
         "text": "Keep usage under 30% of the card limit."},
        {"icon": "calendar", "title": "Pay the full bill on time",
         "text": "Every on-time payment adds to your credit history."},
    ],
    "pay_on_time": [
        {"icon": "wallet", "title": "Clear all overdue amounts",
         "text": "Settle anything past due before it is reported again."},
        {"icon": "bolt", "title": "Turn on auto-debit for EMIs",
         "text": "Automatic payments make sure nothing slips."},
        {"icon": "calendar", "title": "Never miss a due date",
         "text": "Consistency over months rebuilds lender trust."},
    ],
    "lower_utilization": [
        {"icon": "calendar", "title": "Pay before the statement date",
         "text": "A lower reported balance means lower utilisation."},
        {"icon": "card", "title": "Keep usage under 30%",
         "text": "Spread spends across cards and stay below the limit."},
        {"icon": "shield", "title": "Avoid maxing out any card",
         "text": "A single maxed card can pull your score down."},
    ],
    "maintain": [
        {"icon": "calendar", "title": "Keep paying dues on time",
         "text": "Your payment record is your biggest strength."},
        {"icon": "card", "title": "Keep credit spends under 30%",
         "text": "Low utilisation keeps your profile healthy."},
        {"icon": "search", "title": "Limit new applications",
         "text": "Three or fewer per quarter keeps enquiries low."},
    ],
}


@dataclass
class StoryInput:
    """Everything a story is generated from. Mirrors the /api/story request body."""
    customer_name: str = "Customer"
    customer_name_hi: Optional[str] = None      # name as spoken in Hindi; defaults to customer_name
    credit_score: int = 750
    score_bureau: str = "CIBIL"
    report_month: Optional[str] = None          # "YYYY-MM"; defaults to the current month
    on_time_repayment_pct: float = 100.0
    missed_payments_count: int = 0
    active_credit_cards: int = 0
    credit_utilization_pct: float = 0.0
    recent_inquiries: int = 0
    languages: List[str] = field(default_factory=lambda: ["hi", "en"])
    voice_speed: float = 0.92

    def __post_init__(self):
        if not self.report_month:
            self.report_month = datetime.now().strftime("%Y-%m")
        self.languages = [lang for lang in dict.fromkeys(self.languages) if lang in VOICES] or ["hi"]

    @property
    def month(self) -> datetime:
        return datetime.strptime(self.report_month, "%Y-%m")

    @property
    def month_name(self) -> str:
        return self.month.strftime("%B")

    @property
    def year(self) -> int:
        return self.month.year

    @property
    def score(self) -> int:
        return max(300, min(900, int(self.credit_score)))

    def story_id(self, schema: str = SCHEMA_VERSION) -> str:
        """Stable id for caching: identical inputs (and story format) produce the same story."""
        payload = json.dumps({"schema": schema, **asdict(self)}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


class StageTiming:
    """Word and sentence timings for one stage, shifted to absolute story time."""

    def __init__(self, text, words, offset):
        self.text = text
        cursor = 0
        for w in words:
            pos = text.find(w["text"], cursor)
            if pos >= 0:
                cursor = pos + len(w["text"])
            w["pos"] = pos if pos >= 0 else cursor
            w["start"] += offset
            w["end"] += offset
        self.words = words
        self.sentences = []
        for m in SENTENCE_RE.finditer(text):
            if not m.group().strip():
                continue
            sw = [w for w in words if m.start() <= w["pos"] < m.end()]
            if sw:
                self.sentences.append({"text": m.group().strip(), "start": sw[0]["start"],
                                       "end": sw[-1]["end"], "words": sw})
        if not self.sentences:
            raise RuntimeError(f"No word timings came back for: {text[:60]}")

    @property
    def start(self):
        return self.sentences[0]["start"]

    @property
    def end(self):
        return self.sentences[-1]["end"]

    def sentence(self, k):
        """Sentence k (negative counts from the end), clamped to what exists."""
        n = len(self.sentences)
        if k < 0:
            k += n
        return self.sentences[max(0, min(n - 1, k))]

    def clause_starts(self):
        """Start time of each clause after the first one, e.g. the items of a list."""
        times = []
        for m in CLAUSE_SPLIT_RE.finditer(self.text):
            nxt = next((w for w in self.words if w["pos"] >= m.end()), None)
            if nxt and (not times or nxt["start"] - times[-1] > 0.5):
                times.append(nxt["start"])
        return times


async def _synthesize(text, voice, rate):
    comm = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    audio, words = bytearray(), []
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            audio.extend(chunk["data"])
        elif chunk["type"] == "WordBoundary":
            start = chunk["offset"] / 1e7
            words.append({"text": chunk["text"], "start": start, "end": start + chunk["duration"] / 1e7})
    return bytes(audio), words


def _decode_mp3(data):
    with tempfile.NamedTemporaryFile(suffix=".mp3") as f:
        f.write(data)
        f.flush()
        samples, sr = sf.read(f.name, dtype="float32")
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    if sr != SAMPLE_RATE:
        raise RuntimeError(f"Expected {SAMPLE_RATE} Hz audio from edge-tts, got {sr} Hz")
    return samples


async def encode_mp3(samples: np.ndarray, audio_path: Path, bitrate: str):
    """Encode mono SAMPLE_RATE float samples to an MP3 file (raw PCM is piped straight into ffmpeg)."""
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        subprocess.run,
        ["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i", "pipe:0",
         "-codec:a", "libmp3lame", "-b:a", bitrate, str(audio_path)],
        input=samples.astype(np.float32).tobytes(), check=True)


def score_band(score):
    return next(b for b in SCORE_BANDS if b["from"] <= score <= b["to"])


def score_tone(score):
    return "good" if score >= 725 else "warn" if score >= 650 else "bad"


def theme_for(tone):
    return {"good": "green", "warn": "amber", "bad": "amber", "neutral": "violet"}.get(tone, "blue")


def improvement_focus(inp: StoryInput):
    if inp.missed_payments_count > 0:
        return "pay_on_time"
    if inp.active_credit_cards == 0:
        return "secured_card"
    if inp.credit_utilization_pct > 30:
        return "lower_utilization"
    return "maintain"


def lender_view(score):
    # Same thresholds as the lender-outlook stage of the narration (750 / 680).
    if score >= 750:
        values = ("Low", "Faster", "Best rates")
    elif score >= 680:
        values = ("Moderate", "Standard", "Fair rates")
    else:
        values = ("High", "Limited", "Higher rates")
    return [
        {"icon": "shield", "label": "Risk profile", "value": values[0]},
        {"icon": "bolt", "label": "Approvals", "value": values[1]},
        {"icon": "trend", "label": "Loan offers", "value": values[2]},
    ]


def last_months(month: datetime, count=6):
    """Short labels of the `count` months before the report month, oldest first."""
    return [datetime(2000, (month.month - 1 - back) % 12 + 1, 1).strftime("%b") for back in range(count, 0, -1)]


def factor_results(inp: StoryInput):
    """Per-factor status shared by the overview, the detail scenes and the recap."""
    pct = max(0.0, min(100.0, float(inp.on_time_repayment_pct)))
    missed, cards, util, enq = (inp.missed_payments_count, inp.active_credit_cards,
                                inp.credit_utilization_pct, inp.recent_inquiries)
    on_time = ({"text": "Excellent", "tone": "good"} if missed == 0 and pct >= 99
               else {"text": "Good", "tone": "good"} if pct >= 95
               else {"text": "Needs attention", "tone": "bad"})
    if cards == 0:
        utilisation = {"text": "No history", "tone": "neutral"}
    elif util <= 10:
        utilisation = {"text": "Excellent", "tone": "good"}
    elif util <= 30:
        utilisation = {"text": "Good", "tone": "good"}
    else:
        utilisation = {"text": "High", "tone": "bad"}
    enquiries = ({"text": "Excellent", "tone": "good"} if enq == 0
                 else {"text": "Good", "tone": "good"} if enq <= 3
                 else {"text": "Too many", "tone": "bad"})
    return pct, on_time, utilisation, enquiries


def score_dial_props(inp: StoryInput) -> dict:
    score = inp.score
    meta = credit_engine.categorize_credit_score(score)
    return {
        "eyebrow": f"{inp.score_bureau} score · {inp.month_name} {inp.year}",
        "title": f"Hi {inp.customer_name}, here's your *credit score*",
        "score": score, "count_from": 300, "min": 300, "max": 900, "bands": SCORE_BANDS,
        "status": {"text": score_band(score)["label"], "tone": score_tone(score)},
        "percentile": {"value": meta["percentile"],
                       "text": f"Better than *{meta['percentile']}%* of active credit users in India"},
        "lenders": {"title": "How lenders see you", "items": lender_view(score)},
    }


def stage_scenes(inp: StoryInput, k: int, t: StageTiming, st: float, en: float) -> List[dict]:
    """The scenes shown while stage k is spoken, on the same clock as its timings `t`.

    [st, en] is the stretch of that clock the stage owns: its own segment (0 to its duration) in
    a segmented story, or its slice of the single narration file. Each stage only needs its own
    timings, which is what lets stages be recorded and published one at a time.
    """
    score = inp.score
    tone = score_tone(score)
    pct, on_time_status, util_status, enq_status = factor_results(inp)
    missed, cards, util, enq = (inp.missed_payments_count, inp.active_credit_cards,
                                inp.credit_utilization_pct, inp.recent_inquiries)
    month_label = f"{inp.month_name} {inp.year}"

    def rel(abs_time, scene_start):
        return round(max(0.0, abs_time - LEAD - scene_start), 2)

    def insight_beats():
        return [
            {"at": 0, "action": "intro"},
            {"at": rel(t.sentence(-2)["start"], st), "action": "value"},
            {"at": rel(t.sentence(-1)["start"], st), "action": "detail"},
        ]

    # 1. Score dial -> percentile
    if k == 0:
        reveal = t.sentence(1)
        return [{
            "id": "score", "type": "score_dial", "chapter": "Your score", "stage_id": "stage_1_score_overview",
            "start": st, "end": en, "theme": theme_for(tone), "props": score_dial_props(inp),
            "beats": [
                {"at": 0, "action": "intro"},
                {"at": rel(reveal["start"], st), "action": "reveal",
                 "duration": round(min(3.5, max(1.2, reveal["end"] - reveal["start"])), 2)},
                {"at": rel(t.sentence(2)["start"], st), "action": "percentile"},
            ],
        }]

    # 2. The dial stays up for "how lenders see you" (first sentence), then the three factors
    if k == 1:
        split = round((t.sentence(1)["start"] if len(t.sentences) > 1 else (t.start + t.end) / 2) - LEAD, 2)
        items = [
            {"icon": "calendar", "name": "Payment history", "impact": "High impact",
             "result": f"{pct:g}%", "tone": on_time_status["tone"]},
            {"icon": "card", "name": "Credit utilisation", "impact": "High impact",
             "result": "No cards" if cards == 0 else f"{util:g}%", "tone": util_status["tone"]},
            {"icon": "search", "name": "Credit enquiries", "impact": "Medium impact",
             "result": str(enq), "tone": enq_status["tone"]},
        ]
        step = (en - split) / (len(items) + 1)
        return [{
            # Continues the dial from stage 1: everything already shown, only the lender card animates in.
            "id": "score", "type": "score_dial", "chapter": "Your score", "stage_id": "stage_2_lender_outlook",
            "start": st, "end": split, "theme": theme_for(tone), "props": score_dial_props(inp), "continues": True,
            "beats": [{"at": -30, "action": "intro"}, {"at": -30, "action": "reveal", "duration": 1.2},
                      {"at": -30, "action": "percentile"}, {"at": rel(t.start, st), "action": "lenders"}],
        }, {
            "id": "factors", "type": "factor_overview", "chapter": "Score factors", "stage_id": "stage_2_lender_outlook",
            "start": split, "end": en, "theme": "blue",
            "props": {
                "eyebrow": "Score factors", "title": "What shapes *your score*",
                "subtitle": "Three signals lenders read in your report",
                "items": items,
                "note": "Checking your own score is a soft check. It never lowers your score.",
            },
            "beats": [{"at": round(0.3 + i * step, 2), "action": "item", "index": i} for i in range(len(items))],
        }]

    # 3. Payment history
    if k == 2:
        months = last_months(inp.month)
        late = min(missed, len(months))
        return [{
            "id": "payment_history", "type": "factor_insight", "chapter": "Payment history",
            "stage_id": "stage_3_payment_history", "start": st, "end": en, "theme": theme_for(on_time_status["tone"]),
            "props": {
                "eyebrow": "Factor 1 of 3", "impact": "High impact", "title": "Payment *history*",
                "explainer": "Paying EMIs and card bills on time is the strongest signal of trust for lenders.",
                "metric": {"visual": "ring", "value": f"{pct:g}%", "progress": round(pct / 100, 3),
                           "label": "On-time payments", "sublabel": "Last 36 months", "status": on_time_status},
                "detail": {
                    "type": "months", "title": "Last 6 months",
                    "months": [{"label": m, "state": "late" if i >= len(months) - late else "ok"}
                               for i, m in enumerate(months)],
                    "summary": "No missed payments" if missed == 0
                    else f"{missed} missed payment{'s' if missed > 1 else ''}",
                },
            },
            "beats": insight_beats(),
        }]

    # 4. Credit utilisation
    if k == 3:
        util_props = {
            "eyebrow": "Factor 2 of 3", "impact": "High impact", "title": "Credit *utilisation*",
            "explainer": "How much of your card limit you use. Staying under 30% shows you borrow responsibly.",
        }
        if cards == 0:
            util_props.update(
                metric={"visual": "count", "value": "0", "label": "Active credit cards",
                        "sublabel": f"As of {month_label}", "status": util_status},
                detail={"type": "meter", "title": "Card utilisation", "value": None, "ideal_max": 30,
                        "summary": "Not available, you have no active credit cards"},
                tip={"icon": "bulb", "title": "Add a starter credit card",
                     "text": "Using a card lightly and paying in full builds your credit history."},
            )
        else:
            util_props.update(
                metric={"visual": "ring", "value": f"{util:g}%", "progress": round(min(util, 100) / 100, 3),
                        "label": "Credit limit used", "sublabel": f"Across {cards} card{'s' if cards > 1 else ''}",
                        "status": util_status},
                detail={"type": "meter", "title": "Card utilisation", "value": util, "ideal_max": 30,
                        "summary": "Within the healthy limit" if util <= 30 else "Above the healthy 30% limit"},
            )
            if util > 30:
                util_props["tip"] = {"icon": "bulb", "title": "Pay before your statement date",
                                     "text": "A lower reported balance brings utilisation down quickly."}
        return [{
            "id": "utilisation", "type": "factor_insight", "chapter": "Credit utilisation",
            "stage_id": "stage_4_credit_cards", "start": st, "end": en, "theme": theme_for(util_status["tone"]),
            "props": util_props, "beats": insight_beats(),
        }]

    # 5. Enquiries
    if k == 4:
        return [{
            "id": "enquiries", "type": "factor_insight", "chapter": "Credit enquiries",
            "stage_id": "stage_5_credit_inquiries", "start": st, "end": en, "theme": theme_for(enq_status["tone"]),
            "props": {
                "eyebrow": "Factor 3 of 3", "impact": "Medium impact", "title": "Credit *enquiries*",
                "explainer": "Every loan or card application triggers a hard check. Many in a short time worry lenders.",
                "metric": {"visual": "count", "value": str(enq), "label": "New applications",
                           "sublabel": "Last 6 months", "status": enq_status},
                "detail": {"type": "slots", "title": "Against the healthy limit", "value": enq, "limit": 3,
                           "summary": "Recommended: 3 or fewer applications per quarter"},
            },
            "beats": insight_beats(),
        }]

    # 6. Action plan: steps appear as each clause of the advice is spoken
    steps = STEPS_BY_FOCUS[improvement_focus(inp)]
    step_times = t.clause_starts()[:len(steps)]
    last = step_times[-1] if step_times else t.start + 1.5
    missing = len(steps) - len(step_times)
    step_times += [last + (t.end - last) * (i + 1) / (missing + 1) for i in range(missing)]
    target = 800 if score < 800 else 850
    return [{
        "id": "action_plan", "type": "action_plan", "chapter": "Action plan",
        "stage_id": "stage_6_increase_credit_score", "start": st, "end": en, "theme": "blue",
        "props": {
            "eyebrow": "Action plan", "title": f"Your path to *{target}+*",
            "current": score, "target": target, "target_label": f"{target}+",
            "range": [max(300, (score // 50) * 50 - 50), min(900, target + 50)],
            "gap_label": f"{target - score} points to go" if score < target else "Keep it up",
            "steps": steps,
            "note": "Bureaus refresh your score every month as lenders report your activity.",
        },
        "beats": [{"at": 0, "action": "reveal", "duration": 1.6}]
        + [{"at": rel(tt, st), "action": "step", "index": i} for i, tt in enumerate(step_times)],
    }]


def build_scenes(inp: StoryInput, t: List[StageTiming], duration: float) -> List[dict]:
    """Every stage's scenes on one clock, for a single-file story: each stage's stretch starts
    just before its voice does and ends where the next one starts."""
    starts = [0.0] + [round(tm.start - LEAD, 2) for tm in t[1:]]
    ends = starts[1:] + [round(duration, 2)]
    return [scene for k, (tm, st, en) in enumerate(zip(t, starts, ends)) for scene in stage_scenes(inp, k, tm, st, en)]


def stage_texts(inp: StoryInput, lang: str) -> List[str]:
    """The quick summary's narration in `lang`, one text per stage, from the credit engine's scripts."""
    is_hi = lang == "hi"
    stages = credit_engine.generate_stages_script(
        credit_score=inp.score,
        customer_name=(inp.customer_name_hi or inp.customer_name) if is_hi else inp.customer_name,
        on_time_repayment_pct=inp.on_time_repayment_pct,
        missed_payments_count=inp.missed_payments_count,
        active_credit_cards=inp.active_credit_cards,
        credit_utilization_pct=inp.credit_utilization_pct,
        score_bureau=inp.score_bureau,
        recent_inquiries=inp.recent_inquiries,
        include_how_to_increase=True,
        language=lang,
    )
    return [credit_engine.normalize_fintech_script(s["text"], is_hindi=is_hi) for s in stages]


def quick_story_fields(inp: StoryInput) -> dict:
    """Title, input, start screen and end card of a quick-summary story (the same in every language)."""
    score = inp.score
    pct, on_time_status, util_status, enq_status = factor_results(inp)
    return {
        "title": f"Credit score story · {inp.customer_name} · {inp.month_name} {inp.year}",
        "input": asdict(inp),
        "intro": {
            "title": f"Hi {inp.customer_name}, your *{inp.month_name}* credit story is ready",
            "subtitle": "A short walk through your score, what shapes it and how to grow it.",
            "badges": [{"icon": "shield", "text": "100% Secure & Private"},
                       {"icon": "check", "text": "No impact on your score"}],
        },
        "end_card": {
            "title": f"That's your *{inp.month_name}* story",
            "text": "Your next update will be ready in 1 day",
            "recap": [
                {"label": "Credit score", "value": f"{score} · {score_band(score)['label']}", "tone": score_tone(score)},
                {"label": "Payment history", "value": f"{pct:g}% on time", "tone": on_time_status["tone"]},
                {"label": "Credit utilisation",
                 "value": "No active cards" if inp.active_credit_cards == 0 else f"{inp.credit_utilization_pct:g}% used",
                 "tone": util_status["tone"]},
                {"label": "Enquiries", "value": f"{inp.recent_inquiries} in 6 months", "tone": enq_status["tone"]},
            ],
            "primary_label": "Replay story", "secondary_label": "Close",
            "disclaimer": "This video is for educational purposes only and should not be construed as financial advice.",
        },
    }


def timing_captions(timings: List[StageTiming]) -> List[dict]:
    return [{"start": round(s["start"], 3), "end": round(s["end"], 3), "text": s["text"],
             "words": [{"start": round(w["start"], 3), "end": round(w["end"], 3), "text": w["text"]} for w in s["words"]]}
            for tm in timings for s in tm.sentences]


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SAMPLE_RATE), np.float32)


async def build_story(inp: StoryInput, lang: str, audio_path: Path, audio_src: str,
                      language_srcs: Dict[str, str]) -> dict:
    """Synthesize one language's narration to a single audio_path and return its story JSON."""
    texts = stage_texts(inp, lang)
    voice = VOICES[lang]
    results = await asyncio.gather(*(_synthesize(tx, voice, edge_engine.speed_to_rate_str(inp.voice_speed))
                                     for tx in texts))

    # Stitch: lead-in, stages separated by a fixed gap, tail. Offsets are exact.
    pieces, timings, cursor = [silence(LEAD_IN)], [], LEAD_IN
    for i, (text, (mp3, words)) in enumerate(zip(texts, results)):
        samples = _decode_mp3(mp3)
        timings.append(StageTiming(text, words, cursor))
        pieces.append(samples)
        cursor += len(samples) / SAMPLE_RATE
        gap = STAGE_GAP if i < len(texts) - 1 else TAIL
        pieces.append(silence(gap))
        cursor += gap
    audio = np.concatenate(pieces)
    duration = round(len(audio) / SAMPLE_RATE, 3)
    await encode_mp3(audio, audio_path, "96k")

    return {
        "schema_version": SCHEMA_VERSION,
        "story_id": inp.story_id(),
        "language": lang,
        "languages": [{"code": c, "label": LANGUAGE_LABELS[c], "src": src} for c, src in language_srcs.items()],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "brand": BRAND,
        "audio": {"src": audio_src, "duration": duration, "voice": voice, "speed": inp.voice_speed,
                  "engine": "edge-neural"},
        "player": {"captions": True},
        **quick_story_fields(inp),
        "scenes": build_scenes(inp, timings, duration),
        "captions": timing_captions(timings),
    }


async def generate_stories(inp: StoryInput, json_path: Callable[[str], Path],
                           audio_path: Callable[[str], Path]) -> Dict[str, dict]:
    """Generate every requested language in parallel and write each story JSON (single-file format).

    json_path / audio_path map a language code to where its files go; the paths written into
    the JSON (audio.src, languages[].src) are relative to the JSON file, so the output folder
    can be served from anywhere.
    """
    def rel(target: Path, lang: str) -> str:
        return Path(os.path.relpath(target, json_path(lang).parent)).as_posix()

    async def one(lang):
        srcs = {c: rel(json_path(c), lang) for c in inp.languages}
        story = await build_story(inp, lang, audio_path(lang), rel(audio_path(lang), lang), srcs)
        out = json_path(lang)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(story, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return lang, story

    return dict(await asyncio.gather(*(one(lang) for lang in inp.languages)))


# ---------------------------------------------------------------------------------------------
# Chapter narration: long-form stories (e.g. the CRIF report walkthrough) are written as
# chapters of sentences, each sentence available in every language and optionally carrying
# beats. Sentence boundaries are known by construction, so numbers like "1.99" never split
# a sentence the way a regex would.
#
# Every chapter is recorded into its own MP3 (a "segment"), so a player can start on the first
# chapter while the rest are still being recorded. The 0.7s pause between chapters is split
# into silence at the end of one segment and the start of the next, so the scene changes a
# moment before the voice starts, as in the single-file stories.

CHAPTER_LEAD = 0.3   # silence at the start of every chapter but the first (which gets LEAD_IN)
CHAPTER_TAIL = 0.4   # silence at the end of every chapter but the last (which gets TAIL)
SEGMENT_ATTEMPT_TIMEOUT = 25        # a chapter normally records in 3-8s; a stalled request is retried instead of waited out
FIRST_SEGMENT_ATTEMPT_TIMEOUT = 6   # the first segment is short (about 1-2s), so the API isn't held up by a stalled request


async def synthesize_retry(text: str, voice: str, speed: float, timeout: float = SEGMENT_ATTEMPT_TIMEOUT):
    """edge-tts with a timeout per attempt and up to 3 attempts. Returns (mp3 bytes, word timings)."""
    rate = edge_engine.speed_to_rate_str(speed)
    for attempt in range(3):
        try:
            return await asyncio.wait_for(_synthesize(text, voice, rate), timeout)
        except Exception:
            if attempt == 2:
                raise
            await asyncio.sleep(0.5 * (attempt + 1))


def _sentence_times(text, spans, words, offset, length):
    cursor, located = 0, []
    for w in words:
        pos = text.find(w["text"], cursor)
        if pos >= 0:
            cursor = pos + len(w["text"])
        located.append((pos if pos >= 0 else cursor, w))
    out = []
    for a, b in spans:
        ws = [{"text": w["text"], "start": round(offset + w["start"], 3), "end": round(offset + w["end"], 3)}
              for p, w in located if a <= p < b]
        out.append({"text": text[a:b], "start": ws[0]["start"] if ws else None,
                    "end": ws[-1]["end"] if ws else None, "words": ws})
    # A sentence the voice returned no word marks for sits between its neighbours.
    for i, s in enumerate(out):
        if s["start"] is None:
            prev_end = next((x["end"] for x in reversed(out[:i]) if x["end"] is not None), offset)
            next_start = next((x["start"] for x in out[i + 1:] if x["start"] is not None), offset + length)
            s["start"], s["end"] = prev_end, max(prev_end, next_start)
    return out


def chapter_text(chapter: dict, lang: str):
    """The chapter's narration in `lang` as one string, plus each sentence's (start, end) in it."""
    text, spans = "", []
    for i, sentence in enumerate(chapter["sentences"]):
        part = sentence[lang].strip()
        if i:
            text += " "
        spans.append((len(text), len(text) + len(part)))
        text += part
    return text, spans


async def narrate_chapter(chapter: dict, lang: str, voice: str, speed: float, audio_path: Path,
                          lead: float, tail: float, timeout: float = SEGMENT_ATTEMPT_TIMEOUT):
    """Record one chapter in `lang` to its own MP3: `lead` seconds of silence, the voice, `tail` seconds.

    Returns (duration, sentence timings), with times in seconds from the start of this MP3.
    """
    text, spans = chapter_text(chapter, lang)
    mp3, words = await synthesize_retry(text, voice, speed, timeout)
    samples = await asyncio.to_thread(_decode_mp3, mp3)
    length = len(samples) / SAMPLE_RATE
    timings = _sentence_times(text, spans, words, lead, length)
    audio = np.concatenate([silence(lead), samples, silence(tail)])
    await encode_mp3(audio, audio_path, "80k")
    return round(len(audio) / SAMPLE_RATE, 3), timings


def chapter_scene(chapter: dict, timings: List[dict], duration: float) -> dict:
    """The chapter's scene on its own clock (0 = start of its MP3); each sentence's beats fire as it starts.

    A chapter marked `continues` is the second part of the previous one: its intro is already
    done (a beat far in the past), so the player swaps it in with nothing changing on screen.
    """
    continues = bool(chapter.get("continues"))
    beats = [{"at": -30 if continues else 0, "action": "intro"}]
    for sentence, t in zip(chapter["sentences"], timings):
        base = max(0.0, t["start"] - LEAD)
        for k, b in enumerate(sentence.get("beats") or []):
            beat = {**b, "at": round(base + k * 0.14, 2)}
            if beat.get("duration") == "sentence":
                beat["duration"] = round(min(3.5, max(1.2, t["end"] - t["start"])), 2)
            beats.append(beat)
    return {"id": chapter["id"], "type": chapter["type"], "chapter": chapter["chapter"],
            "theme": chapter.get("theme", "blue"), "start": 0, "end": round(duration, 2),
            "props": chapter["props"], "beats": beats, **({"continues": True} if continues else {})}


def captions_from(timings: List[List[dict]]) -> List[dict]:
    return [{"start": s["start"], "end": s["end"], "text": s["text"], "words": s["words"]}
            for tm in timings for s in tm]


# ---------------------------------------------------------------------------------------------
# Segmented stories: the narration is recorded as segments, each its own MP3 plus a small JSON
# with its scenes and captions (times from the start of that MP3), listed by a manifest,
# story.<lang>.json. The manifest is rewritten as each segment finishes, so the API can answer
# as soon as the first (short) segment exists and a player picks up the rest while it plays.

SEGMENTED_SCHEMA = "4.0"
CHARS_PER_SECOND = {"en": 12.7, "hi": 10.4}  # measured narration pace at 1.0x, pauses included
CONCURRENT_SEGMENTS = 8
CONTINUE_GAP = 0.05  # silence either side of a split inside a chapter: the voice's own pause is enough


def speech_seconds(chars: int, lang: str, speed: float) -> float:
    """Rough length of narration that hasn't been recorded yet, so the seek bar can show a total."""
    return round(chars / (CHARS_PER_SECOND[lang] * speed), 1)


def write_json(path: Path, data: dict):
    """Write JSON atomically, so a file being served is never half-written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def segment_files(lang: str, index: int):
    """Audio and data file of one segment, relative to the story folder (and to its manifest)."""
    return f"{lang}/{index:02d}.mp3", f"{lang}/{index:02d}.json"


def read_manifest(folder: Path, lang: str) -> Optional[dict]:
    try:
        return json.loads((folder / f"story.{lang}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def is_complete(folder: Path, langs: List[str]) -> bool:
    """True when every language's manifest says ready and all its segment files exist."""
    for lang in langs:
        manifest = read_manifest(folder, lang) or {}
        segments = manifest.get("segments") or []
        if manifest.get("status") != "ready" or not segments or not all(
                (folder / s.get("audio", "")).is_file() and (folder / s.get("data", "")).is_file() for s in segments):
            return False
    return True


class SegmentedStoryJob:
    """Records a story segment by segment and keeps each language's manifest current.

    Subclasses describe the segments: how many (`count`), their manifest entry
    (`segment_info`), a length estimate while unrecorded (`estimate`), how to record one
    (`record`) and the story's own manifest fields (`story_fields`). Segments are recorded in
    playing order, first language first, CONCURRENT_SEGMENTS at a time. `first_ready` is set
    once the first language's first segment is on disk, or recording it has failed. Segments
    left on disk by an interrupted run are reused rather than recorded again.
    """
    count = 0              # number of segments; set by the subclass before calling __init__
    lead = CHAPTER_LEAD    # silence at the start of a segment (the first gets LEAD_IN)
    tail = CHAPTER_TAIL    # silence at the end of a segment (the last gets TAIL)

    def __init__(self, sid: str, folder: Path, langs: List[str], speed: float):
        self.sid, self.folder, self.langs, self.speed = sid, folder, langs, speed
        self.durations = {lang: [self._on_disk(lang, i) for i in range(self.count)] for lang in langs}
        self.first_ready = asyncio.Event()
        self.error: Optional[str] = None     # first failure, if any
        self.start_failed = False            # the first segment failed, so nothing is playable
        self.finished = False
        self.generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.task: Optional[asyncio.Task] = None

    # ----- described by subclasses
    def segment_info(self, index: int) -> dict:
        """{"id", "chapter", "theme"}, plus "continues": True for the second part of a chapter."""
        raise NotImplementedError

    def estimate(self, lang: str, index: int) -> float:
        raise NotImplementedError

    async def record(self, lang: str, index: int, audio_path: Path, lead: float, tail: float, timeout: float):
        """Record segment `index` to audio_path. Returns (duration, {"scenes": [...], "captions": [...]})."""
        raise NotImplementedError

    def story_fields(self, lang: str, total: float) -> dict:
        """Title, intro, end card and whatever else the story's manifest carries."""
        raise NotImplementedError

    # ----- shared
    def _on_disk(self, lang, index) -> Optional[float]:
        audio, data = (self.folder / f for f in segment_files(lang, index))
        if not (audio.is_file() and data.is_file()):
            return None
        try:
            return float(json.loads(data.read_text(encoding="utf-8"))["duration"])
        except (ValueError, KeyError, TypeError):
            return None

    def ready_count(self, lang) -> int:
        return sum(d is not None for d in self.durations[lang])

    def status(self, lang) -> str:
        if self.ready_count(lang) == self.count:
            return "ready"
        return "failed" if self.error and self.finished else "generating"

    def gaps(self, index):
        """(lead, tail) silence of a segment. A split inside a chapter keeps just the voice's own pause."""
        continues = self.segment_info(index).get("continues")
        lead = LEAD_IN if index == 0 else CONTINUE_GAP if continues else self.lead
        if index == self.count - 1:
            tail = TAIL
        else:
            tail = CONTINUE_GAP if self.segment_info(index + 1).get("continues") else self.tail
        return lead, tail

    def manifest(self, lang) -> dict:
        segments, total = [], 0.0
        for i, duration in enumerate(self.durations[lang]):
            seg = dict(self.segment_info(i))
            if duration is None:
                seg.update(ready=False, estimate=self.estimate(lang, i))
                total += seg["estimate"]
            else:
                audio, data = segment_files(lang, i)
                seg.update(ready=True, duration=duration, audio=audio, data=data)
                total += duration
            segments.append(seg)
        return {
            "schema_version": SEGMENTED_SCHEMA,
            "format": "segmented",
            "story_id": self.sid,
            "status": self.status(lang),
            **({"error": self.error} if self.error else {}),
            "language": lang,
            "languages": [{"code": c, "label": LANGUAGE_LABELS[c], "src": f"story.{c}.json"} for c in self.langs],
            "generated_at": self.generated_at,
            "brand": BRAND,
            "audio": {"voice": VOICES[lang], "speed": self.speed, "engine": "edge-neural"},
            "duration": round(total, 2),
            "player": {"captions": True},
            **self.story_fields(lang, total),
            "segments": segments,
        }

    def write_manifest(self, lang):
        write_json(self.folder / f"story.{lang}.json", self.manifest(lang))

    async def _record(self, lang, index, slots):
        first = lang == self.langs[0] and index == 0
        async with slots:
            if self.start_failed or self.durations[lang][index] is not None:
                return
            audio, data = segment_files(lang, index)
            lead, tail = self.gaps(index)
            try:
                duration, content = await self.record(
                    lang, index, self.folder / audio, lead, tail,
                    FIRST_SEGMENT_ATTEMPT_TIMEOUT if first else SEGMENT_ATTEMPT_TIMEOUT)
                write_json(self.folder / data, {"duration": duration, **content})
            except Exception as err:
                reason = str(err) or ("the voice service timed out" if isinstance(err, asyncio.TimeoutError) else type(err).__name__)
                self.error = self.error or f"'{self.segment_info(index)['chapter']}' ({lang}): {reason}"
                if first:
                    self.start_failed = True
                    self.first_ready.set()
                raise
            self.durations[lang][index] = duration
            self.write_manifest(lang)
            if first:
                self.first_ready.set()

    async def run(self):
        """Record every missing segment. Returns once all of them are done or have failed."""
        for lang in self.langs:
            self.write_manifest(lang)
        if self.durations[self.langs[0]][0] is not None:
            self.first_ready.set()
        slots = asyncio.Semaphore(CONCURRENT_SEGMENTS)
        results = await asyncio.gather(*(self._record(lang, i, slots) for lang in self.langs
                                         for i in range(self.count)), return_exceptions=True)
        failures = [r for r in results if isinstance(r, BaseException)]
        if failures:
            print(f"[!] Story {self.sid}: {len(failures)} segment(s) failed: {self.error}")
        self.finished = True
        for lang in self.langs:
            self.write_manifest(lang)   # marks it ready, or failed with the error
        self.first_ready.set()

    def stage1(self, lang) -> Optional[dict]:
        """The first segment once recorded: its audio file (relative to the story folder) and length."""
        duration = self.durations[lang][0]
        return None if duration is None else {"audio": segment_files(lang, 0)[0], "duration": duration}


QUICK_SEGMENTS = [("score", "Your score"), ("factors", "Score factors"), ("payment_history", "Payment history"),
                  ("utilisation", "Credit utilisation"), ("enquiries", "Credit enquiries"), ("action_plan", "Action plan")]


class QuickStoryJob(SegmentedStoryJob):
    """The quick summary recorded as one segment per stage. Stage 1 (the score) takes about two
    seconds, so the API can answer with it while the other five stages record."""
    lead, tail = LEAD, STAGE_GAP - LEAD   # the same pause between stages as the single-file story

    def __init__(self, inp: StoryInput, folder: Path):
        self.inp = inp
        self.texts = {lang: stage_texts(inp, lang) for lang in inp.languages}
        self.count = len(QUICK_SEGMENTS)
        super().__init__(inp.story_id(SEGMENTED_SCHEMA), folder, inp.languages, inp.voice_speed)

    def segment_info(self, index):
        pct, on_time_status, util_status, enq_status = factor_results(self.inp)
        themes = [theme_for(score_tone(self.inp.score)), "blue", theme_for(on_time_status["tone"]),
                  theme_for(util_status["tone"]), theme_for(enq_status["tone"]), "blue"]
        sid, chapter = QUICK_SEGMENTS[index]
        return {"id": sid, "chapter": chapter, "theme": themes[index]}

    def estimate(self, lang, index):
        return round(speech_seconds(len(self.texts[lang][index]), lang, self.speed) + 1.0, 1)

    async def record(self, lang, index, audio_path, lead, tail, timeout):
        text = self.texts[lang][index]
        mp3, words = await synthesize_retry(text, VOICES[lang], self.speed, timeout)
        samples = await asyncio.to_thread(_decode_mp3, mp3)
        timing = StageTiming(text, words, lead)
        audio = np.concatenate([silence(lead), samples, silence(tail)])
        await encode_mp3(audio, audio_path, "96k")
        duration = round(len(audio) / SAMPLE_RATE, 3)
        return duration, {"scenes": stage_scenes(self.inp, index, timing, 0.0, round(duration, 2)),
                          "captions": timing_captions([timing])}

    def story_fields(self, lang, total):
        return quick_story_fields(self.inp)
