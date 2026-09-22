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

    def story_id(self) -> str:
        """Stable id for caching: identical inputs produce the same story."""
        payload = json.dumps({"schema": SCHEMA_VERSION, **asdict(self)}, sort_keys=True, ensure_ascii=False)
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


def build_scenes(inp: StoryInput, t: List[StageTiming], duration: float) -> List[dict]:
    s1, s2, s3, s4, s5, s6 = t
    score = inp.score
    tone = score_tone(score)
    band = score_band(score)
    meta = credit_engine.categorize_credit_score(score)
    pct, on_time_status, util_status, enq_status = factor_results(inp)
    missed, cards, util, enq = (inp.missed_payments_count, inp.active_credit_cards,
                                inp.credit_utilization_pct, inp.recent_inquiries)
    month_label = f"{inp.month_name} {inp.year}"

    def rel(abs_time, scene_start):
        return round(max(0.0, abs_time - LEAD - scene_start), 2)

    starts = [
        0.0,
        (s2.sentence(1)["start"] if len(s2.sentences) > 1 else (s2.start + s2.end) / 2) - LEAD,
        s3.start - LEAD,
        s4.start - LEAD,
        s5.start - LEAD,
        s6.start - LEAD,
    ]
    ends = starts[1:] + [duration]
    span = [(round(a, 2), round(b, 2)) for a, b in zip(starts, ends)]

    def insight_beats(stage, scene_start):
        return [
            {"at": 0, "action": "intro"},
            {"at": rel(stage.sentence(-2)["start"], scene_start), "action": "value"},
            {"at": rel(stage.sentence(-1)["start"], scene_start), "action": "detail"},
        ]

    # 1. Score dial -> percentile -> how lenders see you
    st, en = span[0]
    reveal = s1.sentence(1)
    score_dial = {
        "id": "score", "type": "score_dial", "chapter": "Your score", "stage_id": "stage_1_score_overview",
        "start": st, "end": en, "theme": theme_for(tone),
        "props": {
            "eyebrow": f"{inp.score_bureau} score · {month_label}",
            "title": f"Hi {inp.customer_name}, here's your *credit score*",
            "score": score, "count_from": 300, "min": 300, "max": 900, "bands": SCORE_BANDS,
            "status": {"text": band["label"], "tone": tone},
            "percentile": {"value": meta["percentile"],
                           "text": f"Better than *{meta['percentile']}%* of active credit users in India"},
            "lenders": {"title": "How lenders see you", "items": lender_view(score)},
        },
        "beats": [
            {"at": 0, "action": "intro"},
            {"at": rel(reveal["start"], st), "action": "reveal",
             "duration": round(min(3.5, max(1.2, reveal["end"] - reveal["start"])), 2)},
            {"at": rel(s1.sentence(2)["start"], st), "action": "percentile"},
            {"at": rel(s2.start, st), "action": "lenders"},
        ],
    }

    # 2. The three factors at a glance
    st, en = span[1]
    items = [
        {"icon": "calendar", "name": "Payment history", "impact": "High impact",
         "result": f"{pct:g}%", "tone": on_time_status["tone"]},
        {"icon": "card", "name": "Credit utilisation", "impact": "High impact",
         "result": "No cards" if cards == 0 else f"{util:g}%", "tone": util_status["tone"]},
        {"icon": "search", "name": "Credit enquiries", "impact": "Medium impact",
         "result": str(enq), "tone": enq_status["tone"]},
    ]
    step = (en - st) / (len(items) + 1)
    overview = {
        "id": "factors", "type": "factor_overview", "chapter": "Score factors", "stage_id": "stage_2_lender_outlook",
        "start": st, "end": en, "theme": "blue",
        "props": {
            "eyebrow": "Score factors", "title": "What shapes *your score*",
            "subtitle": "Three signals lenders read in your report",
            "items": items,
            "note": "Checking your own score is a soft check. It never lowers your score.",
        },
        "beats": [{"at": round(0.3 + i * step, 2), "action": "item", "index": i} for i in range(len(items))],
    }

    # 3. Payment history
    st, en = span[2]
    months = last_months(inp.month)
    late = min(missed, len(months))
    payments = {
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
        "beats": insight_beats(s3, st),
    }

    # 4. Credit utilisation
    st, en = span[3]
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
    utilisation = {
        "id": "utilisation", "type": "factor_insight", "chapter": "Credit utilisation",
        "stage_id": "stage_4_credit_cards", "start": st, "end": en, "theme": theme_for(util_status["tone"]),
        "props": util_props, "beats": insight_beats(s4, st),
    }

    # 5. Enquiries
    st, en = span[4]
    enquiries = {
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
        "beats": insight_beats(s5, st),
    }

    # 6. Action plan: steps appear as each clause of the advice is spoken
    st, en = span[5]
    steps = STEPS_BY_FOCUS[improvement_focus(inp)]
    step_times = s6.clause_starts()[:len(steps)]
    last = step_times[-1] if step_times else s6.start + 1.5
    missing = len(steps) - len(step_times)
    step_times += [last + (s6.end - last) * (i + 1) / (missing + 1) for i in range(missing)]
    target = 800 if score < 800 else 850
    plan = {
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
    }

    return [score_dial, overview, payments, utilisation, enquiries, plan]


async def build_story(inp: StoryInput, lang: str, audio_path: Path, audio_src: str,
                      language_srcs: Dict[str, str]) -> dict:
    """Synthesize one language's narration to audio_path and return its story JSON."""
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
    texts = [credit_engine.normalize_fintech_script(s["text"], is_hindi=is_hi) for s in stages]
    voice = VOICES[lang]
    results = await asyncio.gather(*(_synthesize(tx, voice, edge_engine.speed_to_rate_str(inp.voice_speed))
                                     for tx in texts))

    # Stitch: lead-in, stages separated by a fixed gap, tail. Offsets are exact.
    pieces, timings, cursor = [np.zeros(int(LEAD_IN * SAMPLE_RATE), np.float32)], [], LEAD_IN
    for i, (text, (mp3, words)) in enumerate(zip(texts, results)):
        samples = _decode_mp3(mp3)
        timings.append(StageTiming(text, words, cursor))
        pieces.append(samples)
        cursor += len(samples) / SAMPLE_RATE
        gap = STAGE_GAP if i < len(texts) - 1 else TAIL
        pieces.append(np.zeros(int(gap * SAMPLE_RATE), np.float32))
        cursor += gap
    audio = np.concatenate(pieces)
    duration = round(len(audio) / SAMPLE_RATE, 3)

    audio_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".wav") as wav:
        sf.write(wav.name, audio, SAMPLE_RATE)
        await asyncio.to_thread(subprocess.run, ["ffmpeg", "-y", "-v", "error", "-i", wav.name, "-codec:a",
                                                 "libmp3lame", "-b:a", "96k", str(audio_path)], check=True)

    score = inp.score
    pct, on_time_status, util_status, enq_status = factor_results(inp)
    return {
        "schema_version": SCHEMA_VERSION,
        "story_id": inp.story_id(),
        "title": f"Credit score story · {inp.customer_name} · {inp.month_name} {inp.year}",
        "language": lang,
        "languages": [{"code": c, "label": LANGUAGE_LABELS[c], "src": src} for c, src in language_srcs.items()],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "brand": BRAND,
        "audio": {"src": audio_src, "duration": duration, "voice": voice, "speed": inp.voice_speed,
                  "engine": "edge-neural"},
        "player": {"captions": True},
        "input": asdict(inp),
        "intro": {
            "title": f"Hi {inp.customer_name}, your *{inp.month_name}* credit story is ready",
            "subtitle": "A short walk through your score, what shapes it and how to grow it.",
            "badges": [{"icon": "shield", "text": "100% Secure & Private"},
                       {"icon": "check", "text": "No impact on your score"}],
        },
        "scenes": build_scenes(inp, timings, duration),
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
        "captions": [
            {"start": round(s["start"], 3), "end": round(s["end"], 3), "text": s["text"],
             "words": [{"start": round(w["start"], 3), "end": round(w["end"], 3), "text": w["text"]} for w in s["words"]]}
            for tm in timings for s in tm.sentences
        ],
    }


async def generate_stories(inp: StoryInput, json_path: Callable[[str], Path],
                           audio_path: Callable[[str], Path]) -> Dict[str, dict]:
    """Generate every requested language in parallel and write each story JSON.

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

CHAPTER_GAP = 0.7


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


async def narrate_chapters(chapters: List[dict], lang: str, voice: str, speed: float, audio_path: Path,
                           slots: asyncio.Semaphore):
    """Synthesize every chapter's sentences in `lang` into one MP3; return (duration, per-chapter sentence timings)."""
    texts, spans = [], []
    for ch in chapters:
        text, sp = "", []
        for i, sentence in enumerate(ch["sentences"]):
            part = sentence[lang].strip()
            if i:
                text += " "
            sp.append((len(text), len(text) + len(part)))
            text += part
        texts.append(text)
        spans.append(sp)

    rate = edge_engine.speed_to_rate_str(speed)

    async def synth(text):
        async with slots:
            for attempt in range(3):
                try:
                    return await _synthesize(text, voice, rate)
                except Exception:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(1.5 * (attempt + 1))

    results = await asyncio.gather(*(synth(t) for t in texts))
    pieces, timings, cursor = [np.zeros(int(LEAD_IN * SAMPLE_RATE), np.float32)], [], LEAD_IN
    for i, (text, sp, (mp3, words)) in enumerate(zip(texts, spans, results)):
        samples = _decode_mp3(mp3)
        length = len(samples) / SAMPLE_RATE
        timings.append(_sentence_times(text, sp, words, cursor, length))
        pieces.append(samples)
        cursor += length
        gap = CHAPTER_GAP if i < len(texts) - 1 else TAIL
        pieces.append(np.zeros(int(gap * SAMPLE_RATE), np.float32))
        cursor += gap
    audio = np.concatenate(pieces)
    duration = round(len(audio) / SAMPLE_RATE, 3)
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".wav") as wav:
        sf.write(wav.name, audio, SAMPLE_RATE)
        await asyncio.to_thread(subprocess.run, ["ffmpeg", "-y", "-v", "error", "-i", wav.name, "-codec:a",
                                                 "libmp3lame", "-b:a", "80k", str(audio_path)], check=True)
    return duration, timings


def assemble_scenes(chapters: List[dict], timings: List[List[dict]], duration: float) -> List[dict]:
    """One scene per chapter; each sentence's beats fire when that sentence starts being spoken."""
    starts = [0.0] + [max(0.0, tm[0]["start"] - LEAD) for tm in timings[1:]]
    ends = starts[1:] + [duration]
    scenes = []
    for ch, tm, st, en in zip(chapters, timings, starts, ends):
        beats = [{"at": 0, "action": "intro"}]
        for sentence, t in zip(ch["sentences"], tm):
            base = max(0.0, t["start"] - LEAD - st)
            for k, b in enumerate(sentence.get("beats") or []):
                beat = {**b, "at": round(base + k * 0.14, 2)}
                if beat.get("duration") == "sentence":
                    beat["duration"] = round(min(3.5, max(1.2, t["end"] - t["start"])), 2)
                beats.append(beat)
        scenes.append({"id": ch["id"], "type": ch["type"], "chapter": ch["chapter"], "theme": ch.get("theme", "blue"),
                       "start": round(st, 2), "end": round(en, 2), "props": ch["props"], "beats": beats})
    return scenes


def captions_from(timings: List[List[dict]]) -> List[dict]:
    return [{"start": s["start"], "end": s["end"], "text": s["text"], "words": s["words"]}
            for tm in timings for s in tm]
