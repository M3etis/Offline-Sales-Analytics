from __future__ import annotations
import logging
import os
import re
import asyncio
from tempfile import NamedTemporaryFile
from concurrent.futures import ProcessPoolExecutor

from app.core.config import settings

logger = logging.getLogger(__name__)

def clean_text_for_tts(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'\*{1,2}', '', text)
    text = re.sub(r'#{1,6}\s*', '', text)
    text = re.sub(r'_{1,2}', '', text)
    text = re.sub(r'`', '', text)
    currency_pattern = r'(?:[тТ][гГ]|[тТ][нН][гГ]|₸)'
    text = re.sub(r'(\d)\s*' + currency_pattern + r'(?=$|\W)', r'\g<1> тенге', text)
    text = re.sub(r'(^|\s)' + currency_pattern + r'(?=$|\W)', r'\g<1>тенге', text)
    return text.strip()

_whisper_executor = ProcessPoolExecutor(max_workers=1)

# Global cache for the worker process (since ProcessPoolExecutor uses isolated memory)
_WORKER_MODEL = None

def _transcribe_worker(tmp_path: str, model_name: str, language: str) -> dict:
    """Run transcription in a separate process with its own memory/GIL."""
    global _WORKER_MODEL
    
    # Lazy load model inside the worker process
    if _WORKER_MODEL is None:
        try:
            from faster_whisper import WhisperModel
            import logging
            w_logger = logging.getLogger(__name__)
            w_logger.info(f"Worker loading faster-whisper model: {model_name}")
            _WORKER_MODEL = WhisperModel(
                model_name,
                device="cpu",
                compute_type="int8",
                cpu_threads=4,
            )
        except ImportError:
            raise RuntimeError("faster-whisper is not installed.")
            
    # Perform transcription
    segments, info = _WORKER_MODEL.transcribe(
        tmp_path,
        language=language,
        beam_size=5,
        best_of=5,
        vad_filter=True,
        vad_parameters=dict(
            min_silence_duration_ms=500,
            speech_pad_ms=200,
        ),
        initial_prompt="Транскрипция русской речи.",
        word_timestamps=False,
    )
    
    text = " ".join([segment.text for segment in segments]).strip()
    return {
        "text": text,
        "language": info.language,
        "confidence": info.language_probability
    }

class VoiceService:
    def __init__(self):
        self._cached_voice: str | None = None
        self._edge_tts = None

    def _get_edge_tts(self):
        if self._edge_tts is None:
            import edge_tts
            self._edge_tts = edge_tts
        return self._edge_tts

    def _get_voice(self) -> str:
        if self._cached_voice is not None:
            return self._cached_voice
        try:
            from app.core.dependencies import db_manager
            conn = db_manager.get_connection()
            voice_row = conn.execute("SELECT setting_value FROM system_settings WHERE setting_key = 'tts_voice'").fetchone()
            voice_name = voice_row[0] if voice_row else "ru-RU-SvetlanaNeural"
            if voice_name not in ["ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural"]:
                voice_name = "ru-RU-SvetlanaNeural"
            self._cached_voice = voice_name
            return voice_name
        except Exception as e:
            logger.error(f"Failed to fetch voice setting: {e}")
            return "ru-RU-SvetlanaNeural"

    def invalidate_voice_cache(self):
        self._cached_voice = None
    async def transcribe(self, audio_bytes: bytes, ext: str = ".webm") -> dict:
        with NamedTemporaryFile(delete=False, suffix=ext) as tmp_file:
            tmp_file.write(audio_bytes)
            tmp_path = tmp_file.name

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                _whisper_executor, 
                _transcribe_worker, 
                tmp_path, 
                settings.WHISPER_MODEL, 
                settings.WHISPER_LANGUAGE
            )
            return result
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
                
    async def synthesize(self, text: str) -> bytes:
        text = clean_text_for_tts(text)
        if not text:
            return b""

        voice_name = self._get_voice()
        edge_tts = self._get_edge_tts()
        
        with NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_file:
            tmp_path = tmp_file.name
            
        try:
            communicate = edge_tts.Communicate(text, voice_name)
            await communicate.save(tmp_path)

            if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                with open(tmp_path, "rb") as f:
                    return f.read()
            else:
                logger.error("TTS output file is empty or missing")
                return b""
                
        except Exception as e:
            logger.error(f"TTS Synthesis error: {e}")
            return b""
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    async def synthesize_stream(self, text: str):
        text = clean_text_for_tts(text)
        if not text:
            return

        voice_name = self._get_voice()
        edge_tts = self._get_edge_tts()
        
        try:
            communicate = edge_tts.Communicate(text, voice_name)
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    yield chunk["data"]
        except Exception as e:
            logger.error(f"TTS Streaming error: {e}")

voice_service = VoiceService()
