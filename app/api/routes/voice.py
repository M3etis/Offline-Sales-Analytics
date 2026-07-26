from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
import os
import jwt

from app.services.voice import voice_service
from app.schemas.models import VoiceTranscribeResponse, VoiceSynthesizeRequest
from app.core.config import settings

router = APIRouter(prefix="/voice", tags=["voice"])

@router.post("/transcribe", response_model=VoiceTranscribeResponse)
async def transcribe_voice(file: UploadFile = File(...)):
    if not file.filename.endswith(('.wav', '.mp3', '.ogg', '.m4a', '.webm')):
        raise HTTPException(status_code=400, detail="Неподдерживаемый формат аудио")
        
    audio_bytes = await file.read()
    ext = os.path.splitext(file.filename)[1] or ".webm"
    try:
        result = await voice_service.transcribe(audio_bytes, ext)
        return VoiceTranscribeResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка транскрибации: {str(e)}")

@router.post("/synthesize")
async def synthesize_voice(request: VoiceSynthesizeRequest):
    try:
        audio_bytes = await voice_service.synthesize(request.text)
        if not audio_bytes:
            raise HTTPException(status_code=500, detail="Ошибка генерации аудио")
        return Response(content=audio_bytes, media_type="audio/wav")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/synthesize_stream")
async def synthesize_voice_stream(text: str, token: str = Query(None)):
    if not text:
        raise HTTPException(status_code=400, detail="Текст не предоставлен")
    
    # Validate token from URL parameter
    if token:
        try:
            jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token expired")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid token")
    else:
        raise HTTPException(status_code=401, detail="Token required")
    
    return StreamingResponse(voice_service.synthesize_stream(text), media_type="audio/mpeg")
