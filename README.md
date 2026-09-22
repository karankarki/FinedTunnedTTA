# Fintech Credit Report Neural Voice Server 🎙️⚡

A high-performance, studio-grade headless backend service for generating multi-stage audio credit health reports in **Hindi (PhonePe fintech conversational style)** and **English**, powered by **[Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M)** with Apple Silicon MPS and NVIDIA CUDA acceleration.

![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![PyTorch MPS](https://img.shields.io/badge/Apple_Silicon-MPS_Accelerated-black?style=for-the-badge&logo=apple&logoColor=white)
![Response Time](https://img.shields.io/badge/Response_Time-1.0s--1.2s-brightgreen?style=for-the-badge)
![Languages](https://img.shields.io/badge/Languages-Hindi%20%7C%20English-blue?style=for-the-badge)

> 📖 **[Click here for the Full API Documentation (API_DOCUMENTATION.md)](API_DOCUMENTATION.md)**

---

## 🌟 Key Features

- **Headless Backend Architecture**: Operates as a pure, lightweight REST API server suitable for containerized and microservice environments.
- **Dual Language Support**:
  - **🇮🇳 Hindi (`hi`)**: Authentically tailored to PhonePe fintech conversational style (*नमस्ते, क्रेडिट स्कोर, लेंडर्स, ऑन-टाइम पेमेंट्स, 800 प्लस*).
  - **🇬🇧 / 🇮🇳 English (`en`)**: Clear, professional fintech guidance narrative.
- **Stage-Wise Synthesis & Progressive Streaming**: Generates each stage individually (~1.0s response time) for zero-latency client playback.
- **Dedicated Final Stage**: The last MP3 generated is always actionable guidance on **how to increase the credit score**.
- **Direct MP3 Streaming**: Query `POST /api/credit-report/audio?stage=last` to directly stream the final audio clip as binary `audio/mpeg`.
- **Hardware Accelerated**: Native PyTorch Metal Performance Shaders (`MPS`) acceleration on Apple Silicon (M1/M2/M3/M4) and CUDA on Linux.
- **Deterministic SHA-256 Cache**: Repeated identical audio requests return in **< 1ms (0.001s)**.

---

## 📁 Project Architecture

```
TTS/
├── app/
│   ├── __init__.py
│   ├── config.py              # Configuration, voice registry & hardware detection
│   ├── credit_engine.py       # Stage-wise synthesizer, warm caching & audio stitcher
│   ├── edge_engine.py         # Studio Human Azure Neural TTS engine
│   ├── kokoro_engine.py       # Offline Kokoro-82M neural TTS engine & pipeline
│   ├── main.py                # FastAPI REST API endpoints & audio streaming
│   └── templates/
│       └── tuner.html         # Interactive Voice Studio & Tuner UI
├── outputs/                   # Cached audio segments and reports (.gitkeep)
├── API_DOCUMENTATION.md       # Comprehensive API Reference & Integration Guide
├── Dockerfile                 # Production container image definition
├── docker-compose.yml         # Container orchestration configuration
├── cli.py                     # Command-line interface for testing & generation
├── requirements.txt           # Python dependencies
├── setup.sh                   # Environment setup script
├── start.sh                   # Server launch script
└── README.md                  # Project overview & documentation
```

---

## 🚀 Quickstart

### 1. Automatic Setup
Run the setup script which installs `espeak-ng` via Homebrew, creates the Python virtual environment, and installs all dependencies:

```bash
./setup.sh
```

### 2. Launch the Web Studio
Start the server with Apple Silicon GPU acceleration enabled:

```bash
./start.sh
```

Open your browser and navigate to **[http://localhost:8000](http://localhost:8000)**.

### 3. Memory and startup
- **Kokoro loads on first use.** torch and the Kokoro model (about 400 MB of memory together) are only loaded the first time a Kokoro voice is requested. A server that only uses the Azure Neural (edge-tts) voices, including the whole story video API, starts in under a second and runs in about 100 MB. `/api/status` reports `kokoro_loaded` and shows the torch details once it is loaded.
- **`WARM_CACHE_ON_STARTUP`**: at startup the server pre-records the common `/api/credit-report` stages (about 100 voice requests) so those responses are instant. This is on by default locally and off on Render, which sets `RENDER=true`, because it slows a small instance while it serves its first requests. Set `WARM_CACHE_ON_STARTUP=1` or `0` to choose either way.
- **Docker** installs the CPU-only PyTorch build. The default PyPI build bundles about 3 GB of CUDA libraries this server doesn't use.

---

## 💻 Command-Line Interface (CLI)

The `cli.py` utility allows you to synthesize speech directly from the terminal or automate batch processing.

### Basic Generation
```bash
# Synthesize text to WAV
python cli.py "Hello, welcome to Kokoro Voice Studio!" -o hello.wav

# Specify voice and speed
python cli.py "This is a fast British narration." -v bm_george --speed 1.2 -o british.wav
```

### Synthesize from File
```bash
python cli.py --file chapter1.txt -v af_heart --speed 1.0 -o chapter1.wav
```

### AI Voice Blending
Mix two voice embeddings with a custom ratio (e.g., 60% Heart + 40% Bella):
```bash
python cli.py "Testing blended voice synthesis." -v af_heart --secondary-voice af_bella --blend 0.4 -o blend.wav
```

### Multi-Language Synthesis
```bash
# French
python cli.py "Bonjour le monde!" -l f -v ff_siwis -o french.wav

# Spanish
python cli.py "Hola, bienvenidos a Kokoro!" -l e -v ef_dora -o spanish.wav

# Hindi
python cli.py "नमस्ते! कोकोरो में आपका स्वागत है।" -l h -v hf_alpha -o hindi.wav
```

### Catalog Commands
```bash
# List all voices with accents and tags
python cli.py --list-voices

# List supported languages
python cli.py --list-languages
```

---

## 🌐 REST API Reference

When the server is running, interactive Swagger API docs are accessible at `http://localhost:8000/docs`.

### 1. Synthesize Audio
- **Endpoint**: `POST /api/tts`
- **Request Body**:
```json
{
  "text": "Kokoro is an open-weight TTS model with 82 million parameters.",
  "voice": "af_heart",
  "secondary_voice": "af_bella",
  "blend_weight": 0.3,
  "speed": 1.0,
  "lang_code": "a",
  "split_pattern": "newline"
}
```
- **Response**:
```json
{
  "filename": "kokoro_af_heart_20260918_105211_a1b2c3.wav",
  "filepath": "/path/to/outputs/...",
  "audio_url": "/api/audio/kokoro_af_heart_20260918_105211_a1b2c3.wav",
  "voice_used": "af_heart + af_bella (30%)",
  "lang_code": "a",
  "speed": 1.0,
  "sample_rate": 24000,
  "duration_secs": 5.1,
  "segments_count": 1,
  "segments": [
    {
      "index": 0,
      "graphemes": "Kokoro is an open-weight TTS model with 82 million parameters.",
      "phonemes": "kəkˈɔɹO ɪz ɐn ˈOpᵊnwˌAt tˌitˌiˈɛs mˈɑdᵊl wɪð ˈATi tˈu mˈɪljᵊn pəɹˈæməTəɹz.",
      "duration_secs": 5.1
    }
  ],
  "created_at": "Sep 18, 10:52:11"
}
```

### 2. Preview Phonemes
- **Endpoint**: `POST /api/phonemize`
- **Request Body**:
```json
{
  "text": "[Kokoro](/kˈOkəɹO/) is an open-weight model.",
  "lang_code": "a"
}
```

### 3. Dynamic Credit Report Audio API (Multi-Stage MP3s)
- **Endpoint**: `POST /api/credit-report`
- **Request Body**:
```json
{
  "customer_name": "Rahul",
  "credit_score": 782,
  "on_time_repayment_pct": 98.5,
  "missed_payments_count": 0,
  "active_credit_cards": 2,
  "credit_utilization_pct": 18.0,
  "voice": "hf_alpha",
  "speed": 0.95,
  "output_format": "mp3"
}
```
- **Response**: Returns individual stage MP3 URLs (`stage_1_score`, `stage_2_lender_view`, `stage_3_payments`, `stage_4_cards`, `stage_5_action_plan`) plus full combined report MP3.

### 4. Preview Credit Script Stages (No Audio)
- **Endpoint**: `POST /api/credit-report/preview-script`
- Previews the dynamic 5-stage text without audio synthesis.

### 5. Benchmark Credit Scenarios
- **Endpoint**: `GET /api/credit-report/scenarios`

### 6. List Voices
- **Endpoint**: `GET /api/voices?lang=a`

### 7. System Status
- **Endpoint**: `GET /api/status`

### 8. Stream Audio (MP3 & WAV)
- **Endpoint**: `GET /api/audio/{filename}`

---

## 🎙️ Supported Languages & Sample Voices

| Language Code | Language | Default Voice | Sample Voices |
| :--- | :--- | :--- | :--- |
| **`a`** | American English 🇺🇸 | `af_heart` | `af_bella`, `af_nicole`, `af_sky`, `am_adam`, `am_echo`, `am_onyx` |
| **`b`** | British English 🇬🇧 | `bf_alice` | `bf_emma`, `bf_lily`, `bm_daniel`, `bm_fable`, `bm_george` |
| **`e`** | Spanish 🇪🇸 | `ef_dora` | `em_alex`, `em_santa` |
| **`f`** | French 🇫🇷 | `ff_siwis` | `ff_siwis` |
| **`h`** | Hindi 🇮🇳 | `hf_alpha` | `hf_beta`, `hm_omega`, `hm_psi` |
| **`i`** | Italian 🇮🇹 | `if_sara` | `im_nicola` |
| **`j`** | Japanese 🇯🇵 | `jf_alpha` | `jf_gongitsune`, `jf_nezumi`, `jm_kumo` |
| **`p`** | Brazilian Portuguese 🇧🇷 | `pf_dora` | `pm_alex`, `pm_santa` |
| **`z`** | Mandarin Chinese 🇨🇳 | `zf_xiaobei`| `zf_xiaoni`, `zf_xiaoxiao`, `zm_yunjian`, `zm_yunxi` |

---

## ⚡ Performance & Hardware Acceleration

- **Apple Silicon (M1/M2/M3/M4)**:
  `start.sh` automatically exports `PYTORCH_ENABLE_MPS_FALLBACK=1` and initializes Kokoro on the `mps` device. Real-time inference factor is typically under `0.1x` (synthesizes 5 seconds of audio in < 0.5s).
- **NVIDIA GPU**:
  If CUDA is detected, Kokoro automatically routes tensors to `cuda`.
- **CPU**:
  Clean fallback to CPU execution if no GPU is present.

---

## 📜 Phonetic Customization

Kokoro supports direct IPA phoneme overrides in square-bracket slash format:
```markdown
[Kokoro](/kˈOkəɹO/) is an open-weight model.
```
Use the **Phoneme Tag** button in the Web Studio to easily insert custom pronunciation overrides.

---

## 📄 License

- Model weights: [Apache 2.0](https://huggingface.co/hexgrad/Kokoro-82M) (Hexgrad)
- Codebase: MIT License
