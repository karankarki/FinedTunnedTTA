# 🚀 Fintech Credit Report Neural Voice Server - API Documentation

A high-performance, low-latency headless backend service for generating dynamic, multi-stage credit health audio reports in **Hindi (`hi`)** (PhonePe fintech conversational style) and **English (`en`)**, powered by **Kokoro-82M** with Apple Silicon (MPS) and NVIDIA CUDA hardware acceleration.

---

## 📑 Table of Contents
1. [Server Architecture & Performance](#1-server-architecture--performance)
2. [Quickstart & Running the Server](#2-quickstart--running-the-server)
3. [Integration Patterns: Stage-Wise vs Full Together](#3-integration-patterns-stage-wise-vs-full-together)
4. [API Endpoints Reference](#4-api-endpoints-reference)
   - [POST /api/credit-report/generate](#endpoint-1-post-apicredit-reportgenerate) (Full Report + Stage JSON)
   - [POST /api/credit-report/audio](#endpoint-2-post-apicredit-reportaudio) (Direct Binary MP3 Stream)
   - [POST /api/credit-report/preview-script](#endpoint-3-post-apicredit-reportpreview-script) (0ms Script Preview)
   - [GET /api/audio/{filename}](#endpoint-4-get-apiaudiofilename) (Stream Stored Audio)
   - [POST /api/synthesize](#endpoint-5-post-apisynthesize) (Direct Single-Shot TTS)
   - [GET /api/credit-report/scenarios](#endpoint-6-get-apicredit-reportscenarios) (Preset Scenarios)
   - [GET /health & GET /api/status](#endpoint-7-get-health--get-apistatus) (Health Checks)
5. [Code Examples (Python, Node.js/TypeScript, cURL)](#5-code-examples)

---

## 1. Server Architecture & Performance

### Highlights
- **Sub-Second Stage Synthesis**: Each stage script is strictly calibrated to **18–22 words**, allowing the neural network to synthesize in a single forward pass without sequential chunk overhead (**~0.70s–0.90s** on Apple Silicon MPS).
- **End-to-End Latency**: Stage synthesis + LAME MP3 encoding takes **~1.0 to 1.2 seconds**.
- **Deterministic SHA-256 Cache**: Identical stage scripts return from in-memory cache in **< 1ms (0.001s)**.
- **Dedicated Final Stage**: The last MP3 generated (`stage_6_increase_credit_score`) is always dedicated actionable advice on **how to increase the credit score**.
- **PhonePe Conversational Hindi**: Uses natural Indian fintech terms (*नमस्ते, क्रेडिट स्कोर, लेंडर्स, ऑन-टाइम पेमेंट्स, ईएमआई, सिक्योर्ड क्रेडिट कार्ड, 800 प्लस*).

---

## 2. Quickstart & Running the Server

### Development Mode (with hot-reload)
```bash
source .venv/bin/activate
export PYTORCH_ENABLE_MPS_FALLBACK=1
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Production Deployment Mode
In production, run Uvicorn directly or under Gunicorn/Systemd/Docker:
```bash
source .venv/bin/activate
export PYTORCH_ENABLE_MPS_FALLBACK=1
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --timeout-keep-alive 60
```

### Interactive OpenAPI Documentation
Once running, the interactive Swagger UI is available at:
```
http://localhost:8000/docs
```

---

## 3. Integration Patterns: Stage-Wise vs Full Together

### 📱 Pattern A: Progressive Stage-by-Stage Playback (Recommended for Apps)
**Why this is best:** Delivers **near-instant playback** for mobile and web clients.
1. Mobile app calls `POST /api/credit-report/generate`.
2. Response returns in ~1.1s containing metadata and URLs for all stages (`stages[0]` to `stages[5]`).
3. App plays **Stage 1 immediately** (~11 seconds duration).
4. While Stage 1 is playing, the app buffers Stage 2, 3, 4, 5, and 6 in the background.
5. Client UI syncs animations or metric cards smoothly with each stage's audio transition.

### 🎧 Pattern B: Single Full-Length Audio Track
**Why use this:** Simplest integration for background podcast-style listening or single-track audio players.
- Access `summary.full_audio_url` returned from `POST /api/credit-report/generate`.
- All stages are seamlessly stitched into one track (duration: ~60–70s) with a natural pause (default: `0.35s`).

### 🎯 Pattern C: Direct Binary Streaming of the LAST MP3 ("How to Increase Score")
**Why use this:** When you only want to play or download the final advice clip without handling JSON parsing.
- Send a request to `POST /api/credit-report/audio?stage=last`.
- Returns direct binary audio (`Content-Type: audio/mpeg`). Can be fed directly into `<audio src="...">` or downloaded as an MP3 file.

---

## 4. API Endpoints Reference

---

### Endpoint 1: `POST /api/credit-report/generate`
> Alias: `POST /api/credit-report`

Generates all stages individually and stitches them into a full audio report. Returns comprehensive JSON metadata with audio stream URLs for every stage.

#### Request Headers
```http
Content-Type: application/json
```

#### Request Body Parameters
| Field | Type | Required | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `customer_name` | `string` | Optional | `null` | Customer's first name (e.g., `"Karan"`). Personalizes the greeting. |
| `credit_score` | `integer` | **Required** | - | Bureau credit score between `300` and `900` (e.g., `776`). |
| `on_time_repayment_pct` | `float` | Optional | `100.0` | Percentage of repayments made on time (`0.0` to `100.0`). |
| `missed_payments_count`| `integer` | Optional | `0` | Number of delayed or missed payments/EMIs. |
| `active_credit_cards` | `integer` | Optional | `1` | Number of open credit cards. |
| `credit_utilization_pct`| `float` | Optional | `20.0` | Credit limit usage percentage (`0.0` to `100.0`). |
| `score_bureau` | `string` | Optional | `"CIBIL"` | Rating bureau name (`"CIBIL"`, `"Experian"`, `"Equifax"`, `"CRIF"`). |
| `recent_inquiries` | `integer` | Optional | `0` | Hard credit/loan inquiries in past 6 months. |
| `language` | `string` | Optional | `"en"` | Language: `"hi"` (PhonePe Hindi) or `"en"` (English). |
| `include_how_to_increase` | `boolean`| Optional | `true` | When `true`, appends Stage 6 on **how to increase credit score**. |
| `how_to_increase_focus` | `string` | Optional | `"auto"` | Focus strategy: `"auto"`, `"pay_on_time"`, `"lower_utilization"`, `"secured_card"`, `"credit_mix"`. |
| `voice` | `string` | Optional | `null` | Kokoro voice ID (if omitted, automatically selects natural blended voice). |
| `voice_blend` | `boolean` | Optional | `true` | Blends complementary voices for warm human timbre (65% Beta + 35% Alpha for Hindi, 70% Heart + 30% Sarah for English). |
| `master_audio` | `boolean` | Optional | `true` | Applies studio vocal mastering (warm EQ, de-essing, broadcast dynamic compression, loudness normalization). |
| `bg_music` | `boolean` | Optional | `false` | Mixes subtle ambient acoustic pad + opening chime at -26dB under the speech. |
| `speed` | `float` | Optional | `0.92` | Voice speed multiplier (`0.3` to `2.0`). `0.90`–`0.92` is recommended for natural human cadence. |
| `gap_duration` | `float` | Optional | `0.35` | Silence between stages in full combined track (in seconds). |
| `output_format` | `string` | Optional | `"mp3"` | Output format: `"mp3"` or `"wav"`. |
| `return_base64` | `boolean` | Optional | `false` | If `true`, returns base64 string directly inside JSON. |

#### Example Request Body (Hindi - PhonePe Style)
```json
{
  "customer_name": "Karan",
  "credit_score": 776,
  "on_time_repayment_pct": 100,
  "missed_payments_count": 0,
  "active_credit_cards": 0,
  "recent_inquiries": 0,
  "language": "hi",
  "include_how_to_increase": true,
  "speed": 0.95
}
```

#### Example Response Body (`200 OK`)
```json
{
  "status": "success",
  "summary": {
    "customer_name": "Karan",
    "credit_score": 776,
    "language": "hi",
    "language_label": "हिंदी (PhonePe Style)",
    "category": "Good",
    "tier": "Excellent",
    "percentile": 80,
    "outlook": "Positive",
    "score_bureau": "CIBIL",
    "voice_used": "hf_alpha",
    "speed": 0.95,
    "gap_duration": 0.35,
    "total_duration_secs": 69.47,
    "full_audio_filename": "credit_report_full_hi_20260918_124351_8cb23e.mp3",
    "full_audio_url": "/api/audio/credit_report_full_hi_20260918_124351_8cb23e.mp3",
    "last_mp3_filename": "kokoro_hf_alpha_20260918_124351_99fc13.mp3",
    "last_mp3_url": "/api/audio/kokoro_hf_alpha_20260918_124351_99fc13.mp3",
    "last_stage_id": "stage_6_increase_credit_score",
    "last_stage_title": "🎯 क्रेडिट स्कोर कैसे बढ़ाएं (लास्ट MP3)",
    "is_last_stage_how_to_increase": true,
    "stages_count": 6,
    "average_stage_response_time_secs": 0.95,
    "format": "mp3"
  },
  "stages": [
    {
      "stage_id": "stage_1_score_overview",
      "stage_number": 1,
      "title": "नमस्ते और क्रेडिट स्कोर",
      "text": "नमस्ते Karan! आपका क्रेडिट स्कोर 776 है। यह एकदम स्ट्रॉन्ग स्कोर है, आप 80 परसेंट एक्टिव इंडियन्स से बेहतर पोज़िशन में हैं।",
      "word_count": 22,
      "duration_secs": 11.47,
      "response_time_secs": 0.92,
      "filename": "kokoro_hf_alpha_20260918_124342_b00814.mp3",
      "audio_url": "/api/audio/kokoro_hf_alpha_20260918_124342_b00814.mp3",
      "from_cache": false,
      "is_increase_score_stage": false
    },
    {
      "stage_id": "stage_2_lender_outlook",
      "stage_number": 2,
      "title": "लेंडर्स का नजरिया और ऑफर्स",
      "text": "लेंडर्स आपको ट्रस्टेड कस्टमर मानते हैं, जिसका मतलब है ईज़ी अप्रूवल्स और बेटर ऑफर्स। चलिए समझते हैं आपका स्कोर कैसे बूस्ट हो सकता है।",
      "word_count": 24,
      "duration_secs": 11.82,
      "response_time_secs": 0.94,
      "filename": "kokoro_hf_alpha_20260918_124344_afaafd.mp3",
      "audio_url": "/api/audio/kokoro_hf_alpha_20260918_124344_afaafd.mp3",
      "from_cache": false,
      "is_increase_score_stage": false
    },
    {
      "stage_id": "stage_3_payment_history",
      "stage_number": 3,
      "title": "पहला फैक्टर: ऑन-टाइम री-पेमेंट",
      "text": "पहला, समय पर पेमेंट। आपने 100 परसेंट री-पेमेंट्स ऑन टाइम की हैं—ये तो कमाल है! इससे लेंडर्स को पता चलता है कि आप ज़िम्मेदार हैं।",
      "word_count": 25,
      "duration_secs": 10.85,
      "response_time_secs": 0.88,
      "filename": "kokoro_hf_alpha_20260918_124346_c7138c.mp3",
      "audio_url": "/api/audio/kokoro_hf_alpha_20260918_124346_c7138c.mp3",
      "from_cache": false,
      "is_increase_score_stage": false
    },
    {
      "stage_id": "stage_4_credit_cards",
      "stage_number": 4,
      "title": "दूसरा फैक्टर: क्रेडिट कार्ड खर्चे",
      "text": "दूसरा, क्रेडिट कार्ड से मंथली खर्चे। आपके पास कोई एक्टिव कार्ड नहीं है। 1 स्टार्टर क्रेडिट कार्ड पोर्टफोलियो में ऐड करने से स्कोर बढ़ाने में हेल्प मिलेगी।",
      "word_count": 27,
      "duration_secs": 12.6,
      "response_time_secs": 1.05,
      "filename": "kokoro_hf_alpha_20260918_124348_212783.mp3",
      "audio_url": "/api/audio/kokoro_hf_alpha_20260918_124348_212783.mp3",
      "from_cache": false,
      "is_increase_score_stage": false
    },
    {
      "stage_id": "stage_5_credit_inquiries",
      "stage_number": 5,
      "title": "तीसरा फैक्टर: नई एप्लिकेशन्स और समरी",
      "text": "आखिर में नई एप्लिकेशन्स। आपकी कोई नई एप्लिकेशन नहीं है, ये स्कोर बूस्ट करने के लिए एकदम सही है। आप बिल्कुल सही जगह पर हैं।",
      "word_count": 25,
      "duration_secs": 10.78,
      "response_time_secs": 0.89,
      "filename": "kokoro_hf_alpha_20260918_124349_7a9cbf.mp3",
      "audio_url": "/api/audio/kokoro_hf_alpha_20260918_124349_7a9cbf.mp3",
      "from_cache": false,
      "is_increase_score_stage": false
    },
    {
      "stage_id": "stage_6_increase_credit_score",
      "stage_number": 6,
      "title": "🎯 क्रेडिट स्कोर कैसे बढ़ाएं (लास्ट MP3)",
      "text": "क्रेडिट स्कोर 800 प्लस करने के लिए: एक एफडी आधारित सिक्योर्ड क्रेडिट कार्ड लें, छोटे खर्चे करें और समय पर पूरा बिल भरें।",
      "word_count": 23,
      "duration_secs": 10.2,
      "response_time_secs": 0.87,
      "filename": "kokoro_hf_alpha_20260918_124351_99fc13.mp3",
      "audio_url": "/api/audio/kokoro_hf_alpha_20260918_124351_99fc13.mp3",
      "from_cache": false,
      "is_increase_score_stage": true
    }
  ]
}
```

---

### Endpoint 2: `POST /api/credit-report/audio`
Directly returns **binary audio** (`Content-Type: audio/mpeg` or `audio/wav`). Perfect for downloading or directly assigning to an audio element's `src`.

#### Query Parameters
| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `stage` | `string` | `null` | Stage selector: <br>• **`stage=last`**: Returns the final "How to Increase Your Credit Score" MP3.<br>• **`stage=1`** to **`6`**: Returns that specific stage.<br>• **Omitted**: Returns the stitched full credit report MP3. |

#### Example: Download the LAST MP3 directly
```bash
curl -X POST "http://localhost:8000/api/credit-report/audio?stage=last" \
  -H "Content-Type: application/json" \
  -d '{
    "customer_name": "Karan",
    "credit_score": 776,
    "language": "hi",
    "include_how_to_increase": true
  }' \
  --output how_to_increase_score.mp3
```

#### Example: Download the Full Stitched MP3 directly
```bash
curl -X POST "http://localhost:8000/api/credit-report/audio" \
  -H "Content-Type: application/json" \
  -d '{
    "customer_name": "Karan",
    "credit_score": 776,
    "language": "en"
  }' \
  --output full_credit_report.mp3
```

---

### Endpoint 3: `POST /api/credit-report/preview-script`
Instantaneous text preview of all stage scripts without generating audio. **Response latency is < 1ms**. Useful for displaying on-screen text transcripts or reviewing copy.

#### Request Body
Same schema as `/api/credit-report/generate`.

#### Example Response Body
```json
{
  "customer_name": "Karan",
  "credit_score": 776,
  "language": "hi",
  "tier": "Excellent",
  "stages_count": 6,
  "stages": [
    {
      "stage_id": "stage_1_score_overview",
      "stage_number": 1,
      "title": "नमस्ते और क्रेडिट स्कोर",
      "text": "नमस्ते Karan! आपका क्रेडिट स्कोर 776 है। यह एकदम स्ट्रॉन्ग स्कोर है, आप 80 परसेंट एक्टिव इंडियन्स से बेहतर पोज़िशन में हैं।",
      "word_count": 22,
      "is_increase_score_stage": false
    },
    ...
    {
      "stage_id": "stage_6_increase_credit_score",
      "stage_number": 6,
      "title": "🎯 क्रेडिट स्कोर कैसे बढ़ाएं (लास्ट MP3)",
      "text": "क्रेडिट स्कोर 800 प्लस करने के लिए: एक एफडी आधारित सिक्योर्ड क्रेडिट कार्ड लें, छोटे खर्चे करें और समय पर पूरा बिल भरें।",
      "word_count": 23,
      "is_increase_score_stage": true
    }
  ]
}
```

---

### Endpoint 4: `GET /api/audio/{filename}`
Streams any previously generated audio file with HTTP Range support.

```bash
curl -I http://localhost:8000/api/audio/kokoro_hf_alpha_20260918_124351_99fc13.mp3
```
Response:
```http
HTTP/1.1 200 OK
content-type: audio/mpeg
content-length: 245120
accept-ranges: bytes
```

---

### Endpoint 5: `POST /api/synthesize`
Generic single-shot neural speech synthesis for custom text.

#### Request Body
```json
{
  "text": "नमस्ते! आपका लोन आवेदन सफलतापूर्वक स्वीकृत कर लिया गया है।",
  "voice": "hf_alpha",
  "lang_code": "h",
  "speed": 0.95,
  "split_pattern": "none"
}
```

---

### Endpoint 6: `GET /api/credit-report/scenarios`
Returns sample customer profiles (PhonePe Hindi, Super Prime, New to Credit, Maxed-Out Cards).

---

### Endpoint 7: `GET /health` & `GET /api/status`
Liveness checks for monitoring and container orchestration.

```bash
curl -s http://localhost:8000/health
```
```json
{
  "status": "healthy",
  "device": "mps",
  "sample_rate": 24000,
  "model": "Kokoro-82M"
}
```

---

## 5. Code Examples

### Python: Progressive Stage-by-Stage Client
```python
import requests
import json

BASE_URL = "http://127.0.0.1:8000"

payload = {
    "customer_name": "Karan",
    "credit_score": 776,
    "on_time_repayment_pct": 100,
    "missed_payments_count": 0,
    "active_credit_cards": 0,
    "language": "hi",               # "hi" for PhonePe Hindi, "en" for English
    "include_how_to_increase": True # Ensure last MP3 has the actionable advice
}

# 1. Request Stage Metadata & MP3 URLs
response = requests.post(f"{BASE_URL}/api/credit-report/generate", json=payload)
data = response.json()

print(f"Customer: {data['summary']['customer_name']}")
print(f"Total Stages: {data['summary']['stages_count']}")
print(f"Average Response Time: {data['summary']['average_stage_response_time_secs']}s")

# 2. Iterate and stream stages
for stage in data["stages"]:
    num = stage["stage_number"]
    title = stage["title"]
    duration = stage["duration_secs"]
    audio_url = f"{BASE_URL}{stage['audio_url']}"
    is_last = stage["is_increase_score_stage"]

    print(f"\n[Stage {num}] {title} ({duration}s)")
    if is_last:
        print("  ⭐ This is the dedicated 'How to Increase Score' MP3!")
    print(f"  Audio Stream URL: {audio_url}")

# 3. Or directly fetch the last MP3 as raw binary
last_mp3_resp = requests.post(f"{BASE_URL}/api/credit-report/audio?stage=last", json=payload)
with open("how_to_increase_score.mp3", "wb") as f:
    f.write(last_mp3_resp.content)
print("\nSaved 'how_to_increase_score.mp3' directly to disk!")
```

---

### Node.js / TypeScript: Frontend or Backend Integration
```typescript
import fs from 'fs';

const API_URL = "http://127.0.0.1:8000";

interface CreditReportResponse {
  status: string;
  summary: {
    customer_name: string;
    total_duration_secs: number;
    last_mp3_url: string;
  };
  stages: Array<{
    stage_number: number;
    title: string;
    text: string;
    duration_secs: number;
    audio_url: string;
    is_increase_score_stage: boolean;
  }>;
}

async function fetchCreditReportAudio() {
  const body = {
    customer_name: "Karan",
    credit_score: 776,
    on_time_repayment_pct: 100,
    active_credit_cards: 0,
    language: "hi",
    include_how_to_increase: true
  };

  // Option 1: Fetch all stages for progressive playback
  const res = await fetch(`${API_URL}/api/credit-report/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  const data: CreditReportResponse = await res.json();

  console.log(`Generated ${data.stages.length} audio stages.`);
  data.stages.forEach(stage => {
    console.log(`Stage ${stage.stage_number}: ${stage.title} -> ${API_URL}${stage.audio_url}`);
  });

  // Option 2: Download the LAST MP3 directly (How to Increase Score)
  const lastMp3Res = await fetch(`${API_URL}/api/credit-report/audio?stage=last`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  const buffer = Buffer.from(await lastMp3Res.arrayBuffer());
  fs.writeFileSync("last_stage_advice.mp3", buffer);
  console.log("Downloaded last_stage_advice.mp3 directly.");
}

fetchCreditReportAudio();
```

---

## 6. Error Codes & Handling
| HTTP Status | Cause | Resolution |
| :--- | :--- | :--- |
| `200 OK` | Success | Audio generated or loaded from cache. |
| `400 Bad Request` | Invalid query parameter (e.g. invalid `stage` number). | Use valid stage index (`1-6`) or `last`. |
| `422 Unprocessable Entity` | Invalid request body or missing `credit_score`. | Provide required fields matching JSON schema. |
| `404 Not Found` | Requested audio filename not found in storage. | Ensure file was generated and has not been cleaned up. |
| `500 Internal Error` | Neural pipeline or FFmpeg failure. | Check server logs and verify FFmpeg installation. |
