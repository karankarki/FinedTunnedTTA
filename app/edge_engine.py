import os
import sys
import asyncio
import hashlib
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
import edge_tts
import soundfile as sf
import numpy as np

from app.config import OUTPUTS_DIR

# Standard recommended Azure Neural voices
DEFAULT_VOICES = {
    "hi": "hi-IN-SwaraNeural",           # Warm, conversational Hindi female
    "hi-male": "hi-IN-MadhurNeural",     # Professional Hindi male
    "en-in": "en-IN-NeerjaExpressiveNeural", # Natural Indian English female
    "en-in-male": "en-IN-PrabhatNeural", # Articulate Indian English male
    "en-us": "en-US-JennyNeural",        # Friendly, clear US female
    "en-us-male": "en-US-GuyNeural",     # Energetic US male
    "en-gb": "en-GB-SoniaNeural",        # Refined British female
    "en-gb-male": "en-GB-RyanNeural"     # Natural British male
}

class EdgeTTSEngine:
    """Production-grade neural speech synthesis engine leveraging Microsoft Azure Neural TTS.
    
    Provides studio-quality human voice actors with zero latency overhead, natural prosody,
    breathing cadence, and native Hindi & English pronunciation without API keys.
    """

    def __init__(self):
        self.cache: Dict[str, Dict[str, Any]] = {}

    def resolve_voice(self, voice: Optional[str], language: str = "hi") -> str:
        """Resolve voice identifier or fallback to language default."""
        lang_clean = str(language).lower().strip()
        v_clean = str(voice or "").strip()

        # If already a valid Azure Neural voice name, return directly
        if "neural" in v_clean.lower():
            return v_clean

        # Fallbacks for Kokoro voice names or generic keywords
        if lang_clean in ["hi", "h", "hindi"]:
            if "m" in v_clean.lower() or "male" in v_clean.lower() or "omega" in v_clean.lower() or "psi" in v_clean.lower():
                return DEFAULT_VOICES["hi-male"]
            return DEFAULT_VOICES["hi"]
        elif lang_clean in ["en-in", "indian-english"]:
            if "m" in v_clean.lower() or "male" in v_clean.lower():
                return DEFAULT_VOICES["en-in-male"]
            return DEFAULT_VOICES["en-in"]
        elif lang_clean in ["b", "en-gb", "british"]:
            if "m" in v_clean.lower() or "male" in v_clean.lower() or "daniel" in v_clean.lower() or "george" in v_clean.lower():
                return DEFAULT_VOICES["en-gb-male"]
            return DEFAULT_VOICES["en-gb"]
        else:
            # US / General English
            if "m" in v_clean.lower() or "male" in v_clean.lower() or "adam" in v_clean.lower() or "michael" in v_clean.lower():
                return DEFAULT_VOICES["en-us-male"]
            return DEFAULT_VOICES["en-us"]

    def speed_to_rate_str(self, speed: float) -> str:
        """Convert speed multiplier (e.g. 0.95) to edge-tts rate format (e.g. '-5%')."""
        pct = int(round((speed - 1.0) * 100))
        if pct >= 0:
            return f"+{pct}%"
        return f"{pct}%"

    async def synthesize_async(
        self,
        text: str,
        voice: Optional[str] = None,
        language: str = "hi",
        speed: float = 1.0,
        pitch: str = "+0Hz",
        output_format: str = "mp3"
    ) -> Dict[str, Any]:
        """Synthesize text asynchronously into high-fidelity studio audio."""
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("Text cannot be empty.")

        resolved_voice = self.resolve_voice(voice, language=language)
        rate_str = self.speed_to_rate_str(speed)

        # Deterministic cache lookup
        cache_key = hashlib.sha256(f"{clean_text}_{resolved_voice}_{rate_str}_{pitch}_{output_format}".encode()).hexdigest()
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if Path(cached["filepath"]).exists():
                return {**cached, "from_cache": True}

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        short_id = uuid.uuid4().hex[:6]
        voice_slug = resolved_voice.replace("-", "_").lower()
        mp3_filename = f"azure_{voice_slug}_{timestamp}_{short_id}.mp3"
        mp3_filepath = OUTPUTS_DIR / mp3_filename

        # Call edge_tts Communicate
        communicate = edge_tts.Communicate(
            text=clean_text,
            voice=resolved_voice,
            rate=rate_str,
            pitch=pitch
        )
        await communicate.save(str(mp3_filepath))

        # Get audio duration
        duration_secs = 0.0
        try:
            info = sf.info(str(mp3_filepath))
            duration_secs = round(info.duration, 2)
            sr = info.samplerate
        except Exception:
            duration_secs = round(len(clean_text.split()) / 2.5, 2)
            sr = 24000

        result = {
            "filename": mp3_filename,
            "filepath": str(mp3_filepath),
            "audio_url": f"/api/audio/{mp3_filename}",
            "voice_used": resolved_voice,
            "language": language,
            "speed": speed,
            "duration_secs": duration_secs,
            "sample_rate": sr,
            "format": "mp3",
            "text": clean_text,
            "created_at": datetime.now().strftime("%b %d, %H:%M:%S"),
            "engine": "edge-neural"
        }

        self.cache[cache_key] = result
        return result

    def synthesize(
        self,
        text: str,
        voice: Optional[str] = None,
        language: str = "hi",
        speed: float = 1.0,
        pitch: str = "+0Hz",
        output_format: str = "mp3"
    ) -> Dict[str, Any]:
        """Synchronous wrapper for synthesize_async."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # For environments where loop is already running (e.g. within FastAPI or IPython)
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    return executor.submit(
                        asyncio.run,
                        self.synthesize_async(text, voice, language, speed, pitch, output_format)
                    ).result()
            else:
                return loop.run_until_complete(
                    self.synthesize_async(text, voice, language, speed, pitch, output_format)
                )
        except RuntimeError:
            return asyncio.run(
                self.synthesize_async(text, voice, language, speed, pitch, output_format)
            )

# Global singleton
edge_engine = EdgeTTSEngine()
