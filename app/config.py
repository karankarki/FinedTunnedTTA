import os
import sys
from pathlib import Path
import torch

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = BASE_DIR / "app"
OUTPUTS_DIR = BASE_DIR / "outputs"
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

# Hardware acceleration setup
# On Apple Silicon (M1/M2/M3/M4), enable MPS fallback for PyTorch
if sys.platform == "darwin":
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

def get_device():
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"

DEVICE = get_device()
SAMPLE_RATE = 24000
DEFAULT_TTS_ENGINE = "edge"

# Language mapping
LANGUAGES = {
    "a": {
        "name": "American English",
        "flag": "🇺🇸",
        "code": "a",
        "default_voice": "af_heart",
        "sample_text": "Kokoro is an open-weight TTS model with 82 million parameters, delivering studio-grade quality in a lightweight package."
    },
    "b": {
        "name": "British English",
        "flag": "🇬🇧",
        "code": "b",
        "default_voice": "bf_alice",
        "sample_text": "Good afternoon. The weather today across the United Kingdom is remarkably mild with occasional sunshine."
    },
    "e": {
        "name": "Spanish",
        "flag": "🇪🇸",
        "code": "e",
        "default_voice": "ef_dora",
        "sample_text": "La tecnología de síntesis de voz ha alcanzado un nivel de naturalidad y expresividad verdaderamente asombroso."
    },
    "f": {
        "name": "French",
        "flag": "🇫🇷",
        "code": "f",
        "default_voice": "ff_siwis",
        "sample_text": "Le dromadaire resplendissant déambulait tranquillement dans les méandres en mastiquant de petites feuilles vernissées."
    },
    "h": {
        "name": "Hindi",
        "flag": "🇮🇳",
        "code": "h",
        "default_voice": "hi-IN-SwaraNeural",
        "sample_text": "नमस्ते! आपका सिबिल स्कोर सात सौ बयासी है, जो कि बहुत बढ़िया है।"
    },
    "i": {
        "name": "Italian",
        "flag": "🇮🇹",
        "code": "i",
        "default_voice": "if_sara",
        "sample_text": "Allora cominciava l'insonnia, o un dormiveglia peggiore dell'insonnia, che talvolta assumeva i caratteri dell'incubo."
    },
    "j": {
        "name": "Japanese",
        "flag": "🇯🇵",
        "code": "j",
        "default_voice": "jf_alpha",
        "sample_text": "こんにちは！Kokoroは日本語を含む多言語に対応した、非常に自然で表現力豊かな音声合成エンジンです。"
    },
    "p": {
        "name": "Brazilian Portuguese",
        "flag": "🇧🇷",
        "code": "p",
        "default_voice": "pf_dora",
        "sample_text": "A inteligência artificial está transformando a maneira como interagimos com computadores e criamos conteúdo sonoro."
    },
    "z": {
        "name": "Mandarin Chinese",
        "flag": "🇨🇳",
        "code": "z",
        "default_voice": "zf_xiaobei",
        "sample_text": "欢迎体验Kokoro语音合成系统！它以极小的模型体积，提供媲美人类发音的高品质自然语音。"
    }
}

# Kokoro Voice Registry
VOICES = [
    # American English (a)
    {"id": "af_heart", "lang": "a", "gender": "Female", "name": "Heart (Recommended)", "tags": ["Warm", "Warmth", "Featured"]},
    {"id": "af_bella", "lang": "a", "gender": "Female", "name": "Bella", "tags": ["Expressive", "Casual"]},
    {"id": "af_nicole", "lang": "a", "gender": "Female", "name": "Nicole", "tags": ["Smooth", "Professional"]},
    {"id": "af_aoede", "lang": "a", "gender": "Female", "name": "Aoede", "tags": ["Melodic", "Crisp"]},
    {"id": "af_kore", "lang": "a", "gender": "Female", "name": "Kore", "tags": ["Gentle", "Clear"]},
    {"id": "af_sarah", "lang": "a", "gender": "Female", "name": "Sarah", "tags": ["Friendly", "Conversational"]},
    {"id": "af_sky", "lang": "a", "gender": "Female", "name": "Sky", "tags": ["Bright", "Energetic"]},
    {"id": "af_alloy", "lang": "a", "gender": "Female", "name": "Alloy", "tags": ["Balanced", "Direct"]},
    {"id": "af_jessica", "lang": "a", "gender": "Female", "name": "Jessica", "tags": ["Corporate", "Authoritative"]},
    {"id": "af_river", "lang": "a", "gender": "Female", "name": "River", "tags": ["Calm", "Soothing"]},
    {"id": "am_adam", "lang": "a", "gender": "Male", "name": "Adam", "tags": ["Narrator", "Deep"]},
    {"id": "am_echo", "lang": "a", "gender": "Male", "name": "Echo", "tags": ["Resonant", "Warm"]},
    {"id": "am_eric", "lang": "a", "gender": "Male", "name": "Eric", "tags": ["Casual", "Modern"]},
    {"id": "am_fenrir", "lang": "a", "gender": "Male", "name": "Fenrir", "tags": ["Dramatic", "Low"]},
    {"id": "am_liam", "lang": "a", "gender": "Male", "name": "Liam", "tags": ["Youthful", "Articulate"]},
    {"id": "am_michael", "lang": "a", "gender": "Male", "name": "Michael", "tags": ["News", "Confident"]},
    {"id": "am_onyx", "lang": "a", "gender": "Male", "name": "Onyx", "tags": ["Deep", "Podcast"]},
    {"id": "am_puck", "lang": "a", "gender": "Male", "name": "Puck", "tags": ["Playful", "Quick"]},
    {"id": "am_santa", "lang": "a", "gender": "Male", "name": "Santa", "tags": ["Jovial", "Character"]},

    # British English (b)
    {"id": "bf_alice", "lang": "b", "gender": "Female", "name": "Alice", "tags": ["Refined", "Classic"]},
    {"id": "bf_emma", "lang": "b", "gender": "Female", "name": "Emma", "tags": ["Crisp", "Warm"]},
    {"id": "bf_isabella", "lang": "b", "gender": "Female", "name": "Isabella", "tags": ["Elegant", "Formal"]},
    {"id": "bf_lily", "lang": "b", "gender": "Female", "name": "Lily", "tags": ["Soft", "Narrative"]},
    {"id": "bm_daniel", "lang": "b", "gender": "Male", "name": "Daniel", "tags": ["Sophisticated", "Deep"]},
    {"id": "bm_fable", "lang": "b", "gender": "Male", "name": "Fable", "tags": ["Storyteller", "Atmospheric"]},
    {"id": "bm_george", "lang": "b", "gender": "Male", "name": "George", "tags": ["BBC News", "Stately"]},
    {"id": "bm_lewis", "lang": "b", "gender": "Male", "name": "Lewis", "tags": ["Conversational", "Friendly"]},

    # Spanish (e)
    {"id": "ef_dora", "lang": "e", "gender": "Female", "name": "Dora", "tags": ["Natural", "Clear"]},
    {"id": "em_alex", "lang": "e", "gender": "Male", "name": "Alex", "tags": ["Energetic", "Modern"]},
    {"id": "em_santa", "lang": "e", "gender": "Male", "name": "Santa", "tags": ["Warm", "Expressive"]},

    # French (f)
    {"id": "ff_siwis", "lang": "f", "gender": "Female", "name": "Siwis", "tags": ["Chic", "Melodic"]},

    # Hindi (h)
    {"id": "hi-IN-SwaraNeural", "lang": "h", "gender": "Female", "name": "Swara (Human Hindi - Recommended)", "tags": ["Studio", "PhonePe-Style", "Human", "Featured"], "engine": "edge"},
    {"id": "hi-IN-MadhurNeural", "lang": "h", "gender": "Male", "name": "Madhur (Human Hindi Male)", "tags": ["Authoritative", "Advisor", "Human"], "engine": "edge"},
    {"id": "en-IN-NeerjaExpressiveNeural", "lang": "a", "gender": "Female", "name": "Neerja (Human Indian English - Recommended)", "tags": ["Expressive", "Fintech", "Human", "Featured"], "engine": "edge"},
    {"id": "en-IN-PrabhatNeural", "lang": "a", "gender": "Male", "name": "Prabhat (Human Indian English Male)", "tags": ["Professional", "Human"], "engine": "edge"},
    {"id": "en-US-JennyNeural", "lang": "a", "gender": "Female", "name": "Jenny (Human US English)", "tags": ["Conversational", "Human"], "engine": "edge"},
    {"id": "en-US-GuyNeural", "lang": "a", "gender": "Male", "name": "Guy (Human US English Male)", "tags": ["Confident", "Human"], "engine": "edge"},
    {"id": "hf_alpha", "lang": "h", "gender": "Female", "name": "Alpha (Kokoro)", "tags": ["Experimental", "Alpha"]},
    {"id": "hf_beta", "lang": "h", "gender": "Female", "name": "Beta (Kokoro)", "tags": ["Experimental", "Soft"]},
    {"id": "hm_omega", "lang": "h", "gender": "Male", "name": "Omega (Kokoro)", "tags": ["Experimental", "Radio"]},
    {"id": "hm_psi", "lang": "h", "gender": "Male", "name": "Psi (Kokoro)", "tags": ["Experimental", "Fast"]},

    # Italian (i)
    {"id": "if_sara", "lang": "i", "gender": "Female", "name": "Sara", "tags": ["Passionate", "Clear"]},
    {"id": "im_nicola", "lang": "i", "gender": "Male", "name": "Nicola", "tags": ["Deep", "Expressive"]},

    # Japanese (j)
    {"id": "jf_alpha", "lang": "j", "gender": "Female", "name": "Alpha", "tags": ["Sweet", "Anime"]},
    {"id": "jf_gongitsune", "lang": "j", "gender": "Female", "name": "Gongitsune", "tags": ["Story", "Folk"]},
    {"id": "jf_nezumi", "lang": "j", "gender": "Female", "name": "Nezumi", "tags": ["Bright", "Crisp"]},
    {"id": "jf_tebukuro", "lang": "j", "gender": "Female", "name": "Tebukuro", "tags": ["Gentle", "Warm"]},
    {"id": "jm_kumo", "lang": "j", "gender": "Male", "name": "Kumo", "tags": ["Calm", "Deep"]},

    # Brazilian Portuguese (p)
    {"id": "pf_dora", "lang": "p", "gender": "Female", "name": "Dora", "tags": ["Warm", "Conversational"]},
    {"id": "pm_alex", "lang": "p", "gender": "Male", "name": "Alex", "tags": ["Clear", "Dynamic"]},
    {"id": "pm_santa", "lang": "p", "gender": "Male", "name": "Santa", "tags": ["Friendly", "Rich"]},

    # Mandarin Chinese (z)
    {"id": "zf_xiaobei", "lang": "z", "gender": "Female", "name": "Xiaobei", "tags": ["Standard", "Sweet"]},
    {"id": "zf_xiaoni", "lang": "z", "gender": "Female", "name": "Xiaoni", "tags": ["Lively", "Clear"]},
    {"id": "zf_xiaoxiao", "lang": "z", "gender": "Female", "name": "Xiaoxiao", "tags": ["Narrative", "Gentle"]},
    {"id": "zf_xiaoyi", "lang": "z", "gender": "Female", "name": "Xiaoyi", "tags": ["Warm", "Natural"]},
    {"id": "zm_yunjian", "lang": "z", "gender": "Male", "name": "Yunjian", "tags": ["Audiobook", "Deep"]},
    {"id": "zm_yunxi", "lang": "z", "gender": "Male", "name": "Yunxi", "tags": ["Young", "Vibrant"]},
    {"id": "zm_yunxia", "lang": "z", "gender": "Male", "name": "Yunxia", "tags": ["Storyteller", "Expressive"]},
    {"id": "zm_yunyang", "lang": "z", "gender": "Male", "name": "Yunyang", "tags": ["News", "Formal"]}
]
