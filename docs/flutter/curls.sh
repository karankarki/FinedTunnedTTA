#!/usr/bin/env bash
# Credit Story API: curl examples. Run one block at a time, or the whole file: bash docs/flutter/curls.sh
# The first call after the server has been idle can take 30-60 s (Render free tier waking up).
BASE=${BASE:-https://finedtunnedtta.onrender.com}

# 0. Wake up / health check
curl -s "$BASE/api/status" | head -c 200; echo

# 1. Quick summary, Hindi + English, wait for the finished MP3 + JSON (one call, ~3-10 s when warm)
curl -s -X POST "$BASE/api/story?complete=true" \
  -H "Content-Type: application/json" \
  -d '{
    "customer_name": "Karan",
    "customer_name_hi": "करण",
    "credit_score": 776,
    "on_time_repayment_pct": 100,
    "missed_payments_count": 0,
    "active_credit_cards": 0,
    "credit_utilization_pct": 0,
    "recent_inquiries": 0,
    "languages": ["hi", "en"],
    "voice_speed": 0.95
  }' | tee /tmp/story.json | python3 -m json.tool

# 2. Other dummy profiles
curl -s -X POST "$BASE/api/story?complete=true" -H "Content-Type: application/json" \
  -d '{"customer_name":"Priya","credit_score":665,"missed_payments_count":2,"active_credit_cards":2,"credit_utilization_pct":45,"languages":["en"]}' \
  | python3 -m json.tool

curl -s -X POST "$BASE/api/story?complete=true" -H "Content-Type: application/json" \
  -d '{"customer_name":"Vikram","credit_score":630,"on_time_repayment_pct":94,"missed_payments_count":1,"active_credit_cards":4,"credit_utilization_pct":78,"recent_inquiries":5,"languages":["en"]}' \
  | python3 -m json.tool

# 3. Answer immediately (1-2 s) and poll for progress instead of waiting
SID=$(curl -s -X POST "$BASE/api/story?wait=0" -H "Content-Type: application/json" \
  -d '{"customer_name":"Rahul","credit_score":795,"active_credit_cards":3,"credit_utilization_pct":15,"recent_inquiries":1,"languages":["en"]}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['story_id'])")
echo "story_id=$SID"
until curl -s "$BASE/api/story/$SID" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['status'], d['segments_ready'], '/', d['segments_total']); sys.exit(0 if d['full'] else 1)"; do
  sleep 2
done
curl -s "$BASE/api/story/$SID" | python3 -m json.tool

# 4. Download the MP3 and the animation JSON of story 1
AUDIO=$(python3 -c "import json; print(json.load(open('/tmp/story.json'))['full']['audio_url'])")
JSON=$(python3 -c "import json; print(json.load(open('/tmp/story.json'))['full']['json_url'])")
curl -s -o /tmp/full.mp3 "$AUDIO" && ls -lh /tmp/full.mp3
curl -s "$JSON" | python3 -c "import json,sys; t=json.load(sys.stdin); print(t['duration'], 's,', len(t['scenes']), 'scenes,', len(t['captions']), 'captions'); print([s['type'] for s in t['scenes']])"

# 5. Detailed story from a CRIF High Mark response (send the bureau response file as-is)
# curl -s -X POST "$BASE/api/story/crif?complete=true&languages=hi,en&customer_name=Waseem" \
#   -H "Content-Type: application/json" --data-binary @path/to/CrifResponse.json | python3 -m json.tool

# 6. Errors: invalid score gives 422 with a readable "detail"
curl -s -X POST "$BASE/api/story" -H "Content-Type: application/json" -d '{"credit_score": 1200}'; echo
