import os
import asyncio
from pathlib import Path
from typing import Optional, List
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import torch

from app.config import (
    BASE_DIR,
    OUTPUTS_DIR,
    LANGUAGES,
    VOICES,
    DEVICE,
    SAMPLE_RATE
)
from app.kokoro_engine import engine
from app.edge_engine import edge_engine
from app.credit_engine import credit_engine
from app.story_routes import register_story_routes

app = FastAPI(
    title="Fintech Voice Studio API",
    description="Studio-grade neural speech synthesis powered by Azure Neural TTS & Kokoro-82M",
    version="1.1.0"
)

# Enable CORS for local development and embedding
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Story video API (POST /api/story), generated stories (/stories) and the player page (/player)
register_story_routes(app)

# In-memory generation history cache (persisted to outputs folder)
GENERATION_HISTORY = []

@app.on_event("startup")
async def startup_event():
    """Background startup task to pre-warm static advice cache for 1ms responses."""
    asyncio.create_task(credit_engine.warm_advice_cache())

# Pydantic Schemas
class TTSRequest(BaseModel):
    text: str = Field(..., description="Text content to synthesize into speech")
    voice: str = Field(default="hi-IN-SwaraNeural", description="Primary voice identifier")
    secondary_voice: Optional[str] = Field(default=None, description="Optional secondary voice for AI blending (Kokoro)")
    blend_weight: Optional[float] = Field(default=0.0, ge=0.0, le=1.0, description="Interpolation weight (0.0 = 100% primary, 1.0 = 100% secondary)")
    speed: Optional[float] = Field(default=1.0, ge=0.2, le=3.0, description="Speech rate multiplier (e.g. 0.5x, 0.75x, 1.0x, 1.5x)")
    lang_code: Optional[str] = Field(default="h", description="Language code: h (Hindi), a (US English), b (UK English), etc.")
    split_pattern: Optional[str] = Field(default="newline", description="Text chunking strategy: 'newline', 'sentence', or 'none'")
    gap_duration: Optional[float] = Field(default=0.25, ge=0.0, le=5.0, description="Pause / gap duration between script segments in seconds")
    engine: Optional[str] = Field(default="edge", description="TTS Engine: 'edge' (Studio Human Azure Neural) or 'kokoro' (Local 82M)")

class PhonemizeRequest(BaseModel):
    text: str = Field(..., description="Text to analyze phonetically")
    lang_code: Optional[str] = Field(default="a", description="Language code")
    split_pattern: Optional[str] = Field(default="newline", description="Text chunking strategy")

# Endpoints
@app.get("/api/status")
def get_system_status():
    """Retrieve system, hardware acceleration, and model readiness status."""
    mps_active = torch.backends.mps.is_available()
    return {
        "status": "ready",
        "default_engine": "edge-neural",
        "human_voices_available": True,
        "device": engine.device,
        "mps_available": mps_active,
        "torch_version": torch.__version__,
        "sample_rate": SAMPLE_RATE,
        "languages": LANGUAGES,
        "models": ["Azure Neural Voices (Swara, Madhur, Neerja, Prabhat, Jenny)", "Kokoro-82M"]
    }

@app.get("/api/voices")
def list_voices(lang: Optional[str] = None):
    """List available voices with metadata, optional language filter."""
    if lang:
        return [v for v in VOICES if v["lang"] == lang]
    return VOICES

@app.post("/api/tts")
@app.post("/api/synthesize")
def synthesize_speech(req: TTSRequest):
    """Synthesize text to studio audio with chunk details and metrics."""
    if not req.text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Text input cannot be empty."
        )

    try:
        is_hindi = str(req.lang_code).lower() in ["h", "hi", "hindi"]
        use_edge = (
            req.engine == "edge"
            or (req.voice and "neural" in str(req.voice).lower())
            or (is_hindi and req.engine != "kokoro")
        )

        if use_edge:
            voice_to_use = req.voice
            if not voice_to_use or voice_to_use in ["default", "af_heart", "hf_alpha", "hf_beta"]:
                voice_to_use = "hi-IN-SwaraNeural" if is_hindi else "en-IN-NeerjaExpressiveNeural"
            result = edge_engine.synthesize(
                text=req.text,
                voice=voice_to_use,
                language="hi" if is_hindi else "en",
                speed=req.speed or 1.0
            )
        else:
            result = engine.synthesize(
                text=req.text,
                voice=req.voice,
                secondary_voice=req.secondary_voice,
                blend_weight=req.blend_weight or 0.0,
                speed=req.speed or 1.0,
                lang_code=req.lang_code or "a",
                split_pattern=req.split_pattern or "newline",
                gap_duration=req.gap_duration if req.gap_duration is not None else 0.25
            )

        # Store in generation history
        history_item = {
            "filename": result["filename"],
            "audio_url": result["audio_url"],
            "text": req.text[:140] + ("..." if len(req.text) > 140 else ""),
            "voice": result["voice_used"],
            "duration_secs": result["duration_secs"],
            "created_at": result["created_at"]
        }
        GENERATION_HISTORY.insert(0, history_item)
        if len(GENERATION_HISTORY) > 50:
            GENERATION_HISTORY.pop()

        return result
    except Exception as e:
        print(f"[!] Synthesis Error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@app.post("/api/phonemize")
def preview_phonemes(req: PhonemizeRequest):
    """Extract phoneme tokens and segmentation without synthesizing audio."""
    try:
        return engine.phonemize(
            text=req.text,
            lang_code=req.lang_code or "a",
            split_pattern=req.split_pattern or "newline"
        )
    except Exception as e:
        print(f"[!] Phonemize Error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

class TunerPreviewRequest(BaseModel):
    text: str = Field(..., description="Script text to synthesize")
    voice: str = Field(default="af_heart", description="Primary voice")
    secondary_voice: Optional[str] = Field(default=None, description="Secondary blend voice")
    blend_weight: Optional[float] = Field(default=0.0, ge=0.0, le=1.0)
    speed: Optional[float] = Field(default=0.92, ge=0.5, le=2.0)
    language: Optional[str] = Field(default="hi")
    master_audio: Optional[bool] = Field(default=True)
    bg_music: Optional[bool] = Field(default=False)

@app.get("/tuner")
@app.get("/voice-tuner")
def serve_voice_tuner():
    """Interactive Voice Tuner Lab web page for calibrating voices and exporting config."""
    tuner_path = BASE_DIR / "app" / "templates" / "tuner.html"
    if tuner_path.exists():
        return HTMLResponse(content=tuner_path.read_text(), status_code=200)
    raise HTTPException(status_code=404, detail="Tuner page not found")

@app.post("/api/tuner/preview")
def preview_tuned_voice(req: TunerPreviewRequest):
    """Synthesize voice preview with real-time blending, mastering, and ambient bed."""
    import time, soundfile as sf
    try:
        is_hindi = str(req.language).lower() in ["hi", "hindi"]
        lang_code = "h" if is_hindi else "a"
        
        # Phonetic normalization
        synth_text = credit_engine.normalize_fintech_script(req.text, is_hindi=is_hindi)
        
        t_start = time.time()
        use_edge = (
            "neural" in str(req.voice).lower()
            or (is_hindi and not str(req.voice).startswith("af_") and not str(req.voice).startswith("hf_"))
        )
        if use_edge:
            voice_to_use = req.voice if "neural" in str(req.voice).lower() else ("hi-IN-SwaraNeural" if is_hindi else "en-IN-NeerjaExpressiveNeural")
            edge_res = edge_engine.synthesize(
                text=synth_text,
                voice=voice_to_use,
                language="hi" if is_hindi else "en",
                speed=req.speed or 0.92
            )
            mp3_path = Path(edge_res["filepath"])
            t_elapsed = round(time.time() - t_start, 2)
            return {
                "status": "success",
                "audio_url": edge_res["audio_url"],
                "duration_secs": edge_res["duration_secs"],
                "response_time_secs": t_elapsed,
                "configuration": {
                    "voice": voice_to_use,
                    "speed": req.speed or 0.92,
                    "language": req.language or "hi",
                    "engine": "edge-neural"
                }
            }

        res = engine.synthesize(
            text=synth_text,
            voice=req.voice,
            secondary_voice=req.secondary_voice,
            blend_weight=req.blend_weight or 0.0,
            speed=req.speed or 0.92,
            lang_code=lang_code,
            split_pattern="none",
            gap_duration=0.0
        )
        
        wav_path = Path(res["filepath"])
        
        # If background music is requested, mix ambient pad
        if req.bg_music:
            raw_audio, sr = sf.read(str(wav_path))
            if raw_audio.ndim > 1:
                raw_audio = raw_audio.mean(axis=1)
            dur = len(raw_audio) / sr
            bed = credit_engine.generate_ambient_bed(duration_secs=dur, sr=sr)
            min_len = min(len(raw_audio), len(bed))
            raw_audio[:min_len] += bed[:min_len]
            sf.write(str(wav_path), raw_audio, sr)
        
        # Convert to mastered MP3
        mp3_filename = wav_path.stem + "_tuned.mp3"
        mp3_path = OUTPUTS_DIR / mp3_filename
        credit_engine.convert_wav_to_mp3(wav_path, mp3_path, master_audio=req.master_audio if req.master_audio is not None else True)
        
        t_elapsed = round(time.time() - t_start, 2)
        
        return {
            "status": "success",
            "audio_url": f"/api/audio/{mp3_filename}",
            "duration_secs": res["duration_secs"],
            "response_time_secs": t_elapsed,
            "configuration": {
                "voice": req.voice,
                "secondary_voice": req.secondary_voice,
                "blend_weight": req.blend_weight or 0.0,
                "speed": req.speed or 0.92,
                "master_audio": req.master_audio if req.master_audio is not None else True,
                "bg_music": req.bg_music if req.bg_music is not None else False,
                "language": req.language or "hi"
            }
        }
    except Exception as e:
        print(f"[!] Tuner error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class CreditReportRequest(BaseModel):
    credit_score: int = Field(default=782, ge=300, le=900, description="User's credit score (300-900)")
    customer_name: Optional[str] = Field(default="Rahul", description="User's name for personalized greeting")
    language: Optional[str] = Field(default="en", description="Report language: 'en' for English or 'hi' for PhonePe-style Hindi")
    on_time_repayment_pct: Optional[float] = Field(default=98.5, ge=0.0, le=100.0, description="Percentage of on-time repayments")
    missed_payments_count: Optional[int] = Field(default=0, ge=0, description="Count of delayed/missed payments")
    active_credit_cards: Optional[int] = Field(default=2, ge=0, description="Number of currently active credit cards")
    credit_utilization_pct: Optional[float] = Field(default=18.0, ge=0.0, le=100.0, description="Current credit card utilization percentage")
    score_bureau: Optional[str] = Field(default="CIBIL", description="Credit bureau name (CIBIL, Experian, CRIF)")
    recent_inquiries: Optional[int] = Field(default=1, ge=0, description="Hard credit inquiries in the last 6 months")
    include_how_to_increase: Optional[bool] = Field(default=True, description="Add actionable final stage on how to increase credit score (last MP3)")
    how_to_increase_focus: Optional[str] = Field(default="auto", description="Improvement strategy: 'auto', 'pay_on_time', 'lower_utilization', 'secured_card', 'credit_mix'")
    voice: Optional[str] = Field(default=None, description="Voice identifier (e.g. 'hi-IN-SwaraNeural', 'en-IN-NeerjaExpressiveNeural', 'af_heart')")
    voice_blend: Optional[bool] = Field(default=True, description="Blend complementary voices for natural timbre (when using Kokoro)")
    master_audio: Optional[bool] = Field(default=True, description="Apply studio vocal mastering (warm EQ, de-essing, dynamic compression, loudness normalization)")
    bg_music: Optional[bool] = Field(default=False, description="Mix soft ambient background pad + intro chime at -26dB under the speech")
    speed: Optional[float] = Field(default=0.92, ge=0.3, le=2.0, description="Speaking pace (0.90x-0.95x recommended for natural human cadence)")
    gap_duration: Optional[float] = Field(default=0.35, ge=0.0, le=3.0, description="Pause between stages in the combined track")
    output_format: Optional[str] = Field(default="mp3", description="Audio format: 'mp3' or 'wav'")
    return_base64: Optional[bool] = Field(default=False, description="If true, returns base64-encoded audio inline in JSON")
    engine: Optional[str] = Field(default="edge", description="TTS engine: 'edge' (Studio Human Azure Neural) or 'kokoro' (Local 82M)")

@app.post("/api/credit-report")
@app.post("/api/credit-report/generate")
async def generate_credit_report(req: CreditReportRequest):
    """Generate dynamic stage-by-stage credit health audio report in parallel with individual MP3s."""
    try:
        return await credit_engine.generate_full_report_async(
            credit_score=req.credit_score,
            customer_name=req.customer_name,
            on_time_repayment_pct=req.on_time_repayment_pct if req.on_time_repayment_pct is not None else 100.0,
            missed_payments_count=req.missed_payments_count if req.missed_payments_count is not None else 0,
            active_credit_cards=req.active_credit_cards if req.active_credit_cards is not None else 1,
            credit_utilization_pct=req.credit_utilization_pct,
            score_bureau=req.score_bureau or "CIBIL",
            recent_inquiries=req.recent_inquiries,
            include_how_to_increase=req.include_how_to_increase if req.include_how_to_increase is not None else True,
            how_to_increase_focus=req.how_to_increase_focus or "auto",
            language=req.language or "en",
            voice=req.voice,
            voice_blend=req.voice_blend if req.voice_blend is not None else True,
            master_audio=req.master_audio if req.master_audio is not None else True,
            bg_music=req.bg_music if req.bg_music is not None else False,
            speed=req.speed or 0.92,
            gap_duration=req.gap_duration if req.gap_duration is not None else 0.35,
            output_format=req.output_format or "mp3",
            return_base64=req.return_base64 or False,
            engine_type=req.engine or "edge"
        )
    except Exception as e:
        print(f"[!] Credit Report Generation Error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@app.post("/api/credit-report/audio")
async def download_credit_report_audio(req: CreditReportRequest, stage: Optional[str] = None):
    """Directly returns binary MP3 audio (Content-Type: audio/mpeg) in Hindi or English.
    
    Specify ?stage=1 to download Stage 1, or ?stage=last to download the 'How to Increase Your Score' MP3.
    """
    try:
        if stage:
            stage_str = str(stage).strip().lower()
            stages_script = credit_engine.generate_stages_script(
                credit_score=req.credit_score,
                customer_name=req.customer_name,
                on_time_repayment_pct=req.on_time_repayment_pct if req.on_time_repayment_pct is not None else 100.0,
                missed_payments_count=req.missed_payments_count if req.missed_payments_count is not None else 0,
                active_credit_cards=req.active_credit_cards if req.active_credit_cards is not None else 1,
                credit_utilization_pct=req.credit_utilization_pct,
                score_bureau=req.score_bureau or "CIBIL",
                recent_inquiries=req.recent_inquiries,
                include_how_to_increase=req.include_how_to_increase if req.include_how_to_increase is not None else True,
                how_to_increase_focus=req.how_to_increase_focus or "auto",
                language=req.language or "en"
            )

            target_script = None
            if stage_str in ["last", "how_to_increase", "increase", "boost"] and stages_script:
                target_script = stages_script[-1]
            elif stage_str.isdigit():
                idx = int(stage_str) - 1
                if 0 <= idx < len(stages_script):
                    target_script = stages_script[idx]

            if target_script:
                stage_res = await credit_engine.synthesize_stage_async(
                    stage_info=target_script,
                    voice=req.voice,
                    language=req.language or "en",
                    speed=req.speed or 0.92,
                    output_format=req.output_format or "mp3",
                    engine_type=req.engine or "edge"
                )
                file_path = Path(stage_res["filepath"])
                return FileResponse(
                    path=file_path,
                    media_type="audio/mpeg" if file_path.suffix == ".mp3" else "audio/wav",
                    filename=file_path.name
                )

        # Full combined report audio
        data = await credit_engine.generate_full_report_async(
            credit_score=req.credit_score,
            customer_name=req.customer_name,
            on_time_repayment_pct=req.on_time_repayment_pct if req.on_time_repayment_pct is not None else 100.0,
            missed_payments_count=req.missed_payments_count if req.missed_payments_count is not None else 0,
            active_credit_cards=req.active_credit_cards if req.active_credit_cards is not None else 1,
            credit_utilization_pct=req.credit_utilization_pct,
            score_bureau=req.score_bureau or "CIBIL",
            recent_inquiries=req.recent_inquiries,
            include_how_to_increase=req.include_how_to_increase if req.include_how_to_increase is not None else True,
            how_to_increase_focus=req.how_to_increase_focus or "auto",
            language=req.language or "en",
            voice=req.voice,
            voice_blend=req.voice_blend if req.voice_blend is not None else True,
            master_audio=req.master_audio if req.master_audio is not None else True,
            bg_music=req.bg_music if req.bg_music is not None else False,
            speed=req.speed or 0.92,
            gap_duration=req.gap_duration if req.gap_duration is not None else 0.35,
            output_format=req.output_format or "mp3",
            return_base64=False,
            engine_type=req.engine or "edge"
        )

        full_path = OUTPUTS_DIR / data["summary"]["full_audio_filename"]
        return FileResponse(
            path=full_path,
            media_type="audio/mpeg" if full_path.suffix == ".mp3" else "audio/wav",
            filename=full_path.name
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@app.post("/api/credit-report/preview-script")
def preview_credit_script(req: CreditReportRequest):
    """Preview dynamic script stages in Hindi or English without synthesizing audio."""
    try:
        stages = credit_engine.generate_stages_script(
            credit_score=req.credit_score,
            customer_name=req.customer_name,
            on_time_repayment_pct=req.on_time_repayment_pct if req.on_time_repayment_pct is not None else 100.0,
            missed_payments_count=req.missed_payments_count if req.missed_payments_count is not None else 0,
            active_credit_cards=req.active_credit_cards if req.active_credit_cards is not None else 1,
            credit_utilization_pct=req.credit_utilization_pct,
            score_bureau=req.score_bureau or "CIBIL",
            recent_inquiries=req.recent_inquiries,
            include_how_to_increase=req.include_how_to_increase if req.include_how_to_increase is not None else True,
            how_to_increase_focus=req.how_to_increase_focus or "auto",
            language=req.language or "en"
        )
        meta = credit_engine.categorize_credit_score(req.credit_score)
        is_hi = str(req.language or "en").lower() in ["hi", "hindi"]
        return {
            "summary": {
                "customer_name": req.customer_name or ("करण" if is_hi else "Valued Customer"),
                "credit_score": meta["score"],
                "language": "hi" if is_hi else "en",
                "language_label": "हिंदी (PhonePe Style)" if is_hi else "English",
                "category": meta["category"],
                "tier": meta["tier"],
                "percentile": meta["percentile"],
                "outlook": meta["outlook"],
                "include_how_to_increase": req.include_how_to_increase if req.include_how_to_increase is not None else True,
                "how_to_increase_focus": req.how_to_increase_focus or "auto"
            },
            "stages": stages
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@app.get("/api/credit-report/scenarios")
def get_credit_scenarios():
    """Return pre-configured benchmark scenarios for testing in Hindi and English."""
    return [
        {
            "id": "phonepe_hindi_karan",
            "name": "🇮🇳 PhonePe हिंदी रिपोर्ट (करण - 776)",
            "description": "PhonePe-style conversational Hindi voice report for Karan (776 Score)",
            "params": {
                "customer_name": "करण",
                "credit_score": 776,
                "on_time_repayment_pct": 100.0,
                "missed_payments_count": 0,
                "active_credit_cards": 0,
                "credit_utilization_pct": 0.0,
                "language": "hi",
                "voice": "hf_alpha",
                "speed": 0.95
            }
        },
        {
            "id": "flawless_super_prime",
            "name": "🌟 Super-Prime English (Rahul - 795)",
            "description": "Flawless repayments, 3 active cards, low 15% utilization",
            "params": {
                "customer_name": "Rahul",
                "credit_score": 795,
                "on_time_repayment_pct": 100.0,
                "missed_payments_count": 0,
                "active_credit_cards": 3,
                "credit_utilization_pct": 15.0,
                "language": "en",
                "voice": "hf_alpha",
                "speed": 0.95
            }
        },
        {
            "id": "missed_payments_recovery",
            "name": "⚠️ Missed Payments Alert (Priya - 665)",
            "description": "3 delayed payments dragging score down, recovery priority",
            "params": {
                "customer_name": "Priya",
                "credit_score": 665,
                "on_time_repayment_pct": 88.0,
                "missed_payments_count": 3,
                "active_credit_cards": 2,
                "credit_utilization_pct": 35.0,
                "language": "en",
                "voice": "hf_alpha",
                "speed": 0.95
            }
        },
        {
            "id": "thin_file_no_cards",
            "name": "💳 No Credit Cards (Amit - 710)",
            "description": "Zero active cards, thin credit file, starter card recommendation",
            "params": {
                "customer_name": "Amit",
                "credit_score": 710,
                "on_time_repayment_pct": 100.0,
                "missed_payments_count": 0,
                "active_credit_cards": 0,
                "credit_utilization_pct": 0.0,
                "language": "en",
                "voice": "hf_alpha",
                "speed": 0.95
            }
        },
        {
            "id": "maxed_out_utilization",
            "name": "🚨 Maxed-Out Cards (Vikram - 630)",
            "description": "78% high credit card balance, debt reduction recommendation",
            "params": {
                "customer_name": "Vikram",
                "credit_score": 630,
                "on_time_repayment_pct": 94.0,
                "missed_payments_count": 1,
                "active_credit_cards": 4,
                "credit_utilization_pct": 78.0,
                "language": "en",
                "voice": "hf_alpha",
                "speed": 0.95
            }
        }
    ]

@app.get("/api/audio/{filename}")
def stream_audio(filename: str):
    """Serve or stream generated WAV or MP3 audio files."""
    file_path = OUTPUTS_DIR / filename
    if not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audio file not found."
        )
    media_type = "audio/mpeg" if filename.lower().endswith(".mp3") else "audio/wav"
    return FileResponse(
        path=file_path,
        media_type=media_type,
        filename=filename
    )

@app.get("/api/history")
def get_history():
    """Get recent audio generation history."""
    if GENERATION_HISTORY:
        return GENERATION_HISTORY

    # Fallback: recover files from outputs directory
    recovered = []
    audio_files = sorted(list(OUTPUTS_DIR.glob("*.mp3")) + list(OUTPUTS_DIR.glob("*.wav")), key=os.path.getmtime, reverse=True)
    for f in audio_files[:30]:
        recovered.append({
            "filename": f.name,
            "audio_url": f"/api/audio/{f.name}",
            "text": f"Audio clip ({f.stem})",
            "voice": "hf_alpha",
            "duration_secs": round(f.stat().st_size / (24000 * 2), 1),
            "created_at": "Saved Session"
        })
    return recovered

@app.get("/health")
def health_check():
    """Liveness check for container orchestration and load balancers."""
    return {
        "status": "healthy",
        "device": engine.device,
        "sample_rate": SAMPLE_RATE,
        "model": "Kokoro-82M"
    }

@app.get("/")
def api_root():
    """Headless API server root returning service metadata and interactive OpenAPI docs link."""
    return {
        "service": "Fintech Credit Report Neural Voice API Server",
        "version": "1.0.0",
        "mode": "headless_api_server",
        "status": "online",
        "documentation": "/docs",
        "openapi_schema": "/openapi.json",
        "endpoints": {
            "credit_report_generate": {
                "method": "POST",
                "path": "/api/credit-report/generate",
                "description": "Generate multi-stage audio credit report with individual MP3s"
            },
            "credit_report_audio_stream": {
                "method": "POST",
                "path": "/api/credit-report/audio?stage={stage_id|last}",
                "description": "Direct binary MP3 stream (last stage = How to Increase Score)"
            },
            "credit_report_preview_script": {
                "method": "POST",
                "path": "/api/credit-report/preview-script",
                "description": "Instant 0ms script preview in Hindi or English"
            },
            "audio_file_stream": {
                "method": "GET",
                "path": "/api/audio/{filename}",
                "description": "Stream or download generated MP3 audio files"
            },
            "generic_tts": {
                "method": "POST",
                "path": "/api/synthesize",
                "description": "Direct low-level single-shot text-to-speech"
            },
            "scenarios": {
                "method": "GET",
                "path": "/api/credit-report/scenarios",
                "description": "Benchmark credit score scenarios (PhonePe Hindi, etc.)"
            },
            "voices": {
                "method": "GET",
                "path": "/api/voices",
                "description": "Available Kokoro voices"
            }
        }
    }
