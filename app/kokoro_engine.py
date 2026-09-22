from __future__ import annotations

import os
import sys
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Any, Union
import numpy as np
import soundfile as sf

from app.config import (
    OUTPUTS_DIR,
    SAMPLE_RATE,
    LANGUAGES,
    VOICES,
    get_device
)

# Ensure Apple Silicon MPS fallback is set
if sys.platform == "darwin":
    os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"

if TYPE_CHECKING:
    import torch
    from kokoro import KPipeline

class KokoroEngine:
    """Core synthesis engine wrapping Kokoro-82M with multi-language pipelines,

    voice blending, audio stitching, and phonetic analysis.

    torch and kokoro (together ~400 MB of memory) are imported the first time a Kokoro voice
    is used, not at startup, so a server that only uses the edge-tts voices never loads them.
    """

    def __init__(self):
        self._device: Optional[str] = None
        self.pipelines: Dict[str, KPipeline] = {}

    @property
    def loaded(self) -> bool:
        """True once torch and a Kokoro pipeline have been loaded."""
        return bool(self.pipelines)

    @property
    def device(self) -> str:
        """Device Kokoro runs on; working it out imports torch, so it happens on first use."""
        if self._device is None:
            self._device = get_device()
        return self._device

    def get_pipeline(self, lang_code: str = "a") -> KPipeline:
        """Get or lazily instantiate KPipeline for a specific language code."""
        lang_code = lang_code.lower()
        if lang_code in self.pipelines:
            return self.pipelines[lang_code]

        from kokoro import KPipeline
        print(f"[*] Loading Kokoro pipeline for language '{lang_code}' on device '{self.device}'...")
        try:
            pipeline = KPipeline(lang_code=lang_code, device=self.device)
        except Exception as e:
            print(f"[!] Warning: Failed to load on {self.device} ({e}). Falling back to CPU...")
            self._device = "cpu"
            pipeline = KPipeline(lang_code=lang_code, device="cpu")

        self.pipelines[lang_code] = pipeline
        return pipeline

    def resolve_split_pattern(self, split_option: Optional[str]) -> Optional[str]:
        """Convert friendly split option string to regex pattern."""
        if not split_option or split_option == "newline":
            return r"\n+"
        elif split_option == "sentence":
            return r"(?<=[.!?])\s+"
        elif split_option == "none":
            return None
        return split_option

    def resolve_voice(
        self,
        pipeline: KPipeline,
        voice: str,
        secondary_voice: Optional[str] = None,
        blend_weight: float = 0.0
    ) -> Union[str, torch.FloatTensor]:
        """Load single voice or blend two voices with custom interpolation weight."""
        if not secondary_voice or blend_weight <= 0.0 or voice == secondary_voice:
            return voice

        # Blend two voices: primary (1 - weight) + secondary (weight)
        weight = max(0.0, min(1.0, float(blend_weight)))
        try:
            v1 = pipeline.load_single_voice(voice)
            v2 = pipeline.load_single_voice(secondary_voice)
            # Both v1 and v2 are tensors of shape [N, 1, 256] or similar
            blended = (1.0 - weight) * v1 + weight * v2
            return blended
        except Exception as e:
            print(f"[!] Warning: Voice blending failed ({e}). Falling back to primary voice '{voice}'.")
            return voice

    def synthesize(
        self,
        text: str,
        voice: str = "af_heart",
        secondary_voice: Optional[str] = None,
        blend_weight: float = 0.0,
        speed: float = 1.0,
        lang_code: str = "a",
        split_pattern: str = "newline",
        gap_duration: float = 0.25
    ) -> Dict[str, Any]:
        """Synthesize text into a high-quality 24kHz audio WAV file with chunk metrics and customizable segment gaps."""
        text = text.strip()
        if not text:
            raise ValueError("Input text cannot be empty.")

        pipeline = self.get_pipeline(lang_code)
        voice_target = self.resolve_voice(
            pipeline=pipeline,
            voice=voice,
            secondary_voice=secondary_voice,
            blend_weight=blend_weight
        )

        pattern = self.resolve_split_pattern(split_pattern)
        generator = pipeline(
            text=text,
            voice=voice_target,
            speed=float(speed),
            split_pattern=pattern
        )

        segments = []
        audio_arrays = []
        total_samples = 0

        for idx, result in enumerate(generator):
            gs = getattr(result, "graphemes", "")
            ps = getattr(result, "phonemes", "")
            audio_tensor = getattr(result, "audio", None)

            if audio_tensor is not None:
                # Convert torch tensor on MPS/CUDA to numpy array
                if hasattr(audio_tensor, "cpu"):
                    audio_np = audio_tensor.cpu().numpy()
                elif hasattr(audio_tensor, "numpy"):
                    audio_np = audio_tensor.numpy()
                else:
                    audio_np = np.array(audio_tensor, dtype=np.float32)

                audio_arrays.append(audio_np)
                seg_samples = len(audio_np)
                total_samples += seg_samples
                seg_duration = seg_samples / SAMPLE_RATE
            else:
                seg_duration = 0.0

            segments.append({
                "index": idx,
                "graphemes": gs,
                "phonemes": ps,
                "duration_secs": round(seg_duration, 3)
            })

        if not audio_arrays:
            raise RuntimeError("Speech synthesis produced no audio samples.")

        # Stitch segments together with customizable silence/gap between them
        gap_duration = max(0.0, float(gap_duration))
        if gap_duration > 0.0 and len(audio_arrays) > 1:
            gap_samples = int(SAMPLE_RATE * gap_duration)
            silence = np.zeros(gap_samples, dtype=np.float32)
            stitched = []
            for i, arr in enumerate(audio_arrays):
                stitched.append(arr)
                if i < len(audio_arrays) - 1:
                    stitched.append(silence)
            combined_audio = np.concatenate(stitched, axis=0)
        else:
            combined_audio = np.concatenate(audio_arrays, axis=0)

        total_duration = len(combined_audio) / SAMPLE_RATE

        # Generate unique filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        short_id = uuid.uuid4().hex[:6]
        voice_name_clean = voice.replace("/", "_")
        filename = f"kokoro_{voice_name_clean}_{timestamp}_{short_id}.wav"
        output_filepath = OUTPUTS_DIR / filename

        # Write 24kHz WAV file
        sf.write(str(output_filepath), combined_audio, SAMPLE_RATE)

        voice_display = f"{voice} + {secondary_voice} ({int(blend_weight*100)}%)" if secondary_voice and blend_weight > 0 else voice

        return {
            "filename": filename,
            "filepath": str(output_filepath),
            "audio_url": f"/api/audio/{filename}",
            "voice_used": voice_display,
            "lang_code": lang_code,
            "speed": speed,
            "gap_duration": gap_duration,
            "sample_rate": SAMPLE_RATE,
            "duration_secs": round(total_duration, 2),
            "segments_count": len(segments),
            "segments": segments,
            "text": text,
            "created_at": datetime.now().strftime("%b %d, %H:%M:%S")
        }

    def phonemize(
        self,
        text: str,
        lang_code: str = "a",
        split_pattern: str = "newline"
    ) -> Dict[str, Any]:
        """Preview phoneme tokens for given text without synthesizing audio."""
        text = text.strip()
        if not text:
            return {"segments": []}

        pipeline = self.get_pipeline(lang_code)
        pattern = self.resolve_split_pattern(split_pattern)

        # Split input into segments
        if pattern:
            chunks = re.split(pattern, text)
        else:
            chunks = [text]

        chunks = [c.strip() for c in chunks if c.strip()]
        segments = []

        for idx, chunk in enumerate(chunks):
            try:
                # English pipelines have en_tokenize or g2p
                if lang_code in "ab":
                    _, tokens = pipeline.g2p(chunk)
                    for gs, ps, _ in pipeline.en_tokenize(tokens):
                        if ps:
                            segments.append({
                                "index": len(segments),
                                "graphemes": gs,
                                "phonemes": ps,
                                "duration_secs": round(len(ps) * 0.05, 2)
                            })
                else:
                    ps, _ = pipeline.g2p(chunk)
                    segments.append({
                        "index": len(segments),
                        "graphemes": chunk,
                        "phonemes": ps,
                        "duration_secs": round(len(ps) * 0.05, 2)
                    })
            except Exception as e:
                segments.append({
                    "index": len(segments),
                    "graphemes": chunk,
                    "phonemes": f"[phonemize error: {e}]",
                    "duration_secs": 0.0
                })

        return {"segments": segments, "total": len(segments)}

# Global singleton instance
engine = KokoroEngine()
