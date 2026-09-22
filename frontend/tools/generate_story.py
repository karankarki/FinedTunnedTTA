#!/usr/bin/env python3
"""Regenerate the bundled sample story (frontend/data + frontend/audio) from the command line.

The generation itself lives in app/story_engine.py and is shared with POST /api/story;
this script only maps CLI flags onto a StoryInput and picks the output paths.

Usage (from the repo root):
    .venv/bin/python frontend/tools/generate_story.py
    .venv/bin/python frontend/tools/generate_story.py --name Priya --score 665 --cards 2 --util 45 --missed 2
"""
import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.story_engine import VOICES, StoryInput, generate_stories  # noqa: E402

FRONTEND_DIR = ROOT / "frontend"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="Karan", help="name shown on screen and spoken in English")
    ap.add_argument("--name-hi", default="करण", help="name spoken in the Hindi narration")
    ap.add_argument("--score", type=int, default=776)
    ap.add_argument("--bureau", default="CIBIL")
    ap.add_argument("--month", default=datetime.now().strftime("%Y-%m"), help="report month, YYYY-MM")
    ap.add_argument("--on-time", type=float, default=100.0, help="on-time repayment percent")
    ap.add_argument("--missed", type=int, default=0, help="missed payments in the last 6 months")
    ap.add_argument("--cards", type=int, default=0, help="active credit cards")
    ap.add_argument("--util", type=float, default=0.0, help="credit utilization percent")
    ap.add_argument("--inquiries", type=int, default=0, help="new applications in the last 6 months")
    ap.add_argument("--speed", type=float, default=0.92)
    ap.add_argument("--languages", nargs="+", default=["hi", "en"], choices=sorted(VOICES))
    a = ap.parse_args()

    inp = StoryInput(
        customer_name=a.name, customer_name_hi=a.name_hi, credit_score=a.score, score_bureau=a.bureau,
        report_month=a.month, on_time_repayment_pct=a.on_time, missed_payments_count=a.missed,
        active_credit_cards=a.cards, credit_utilization_pct=a.util if a.cards else 0.0,
        recent_inquiries=a.inquiries, languages=a.languages, voice_speed=a.speed,
    )
    stories = asyncio.run(generate_stories(
        inp,
        json_path=lambda lang: FRONTEND_DIR / "data" / f"story.{lang}.json",
        audio_path=lambda lang: FRONTEND_DIR / "audio" / f"credit_story_{lang}.mp3",
    ))
    for lang, story in stories.items():
        print(f"[{lang}] {story['audio']['duration']:.1f}s -> frontend/data/story.{lang}.json")


if __name__ == "__main__":
    main()
