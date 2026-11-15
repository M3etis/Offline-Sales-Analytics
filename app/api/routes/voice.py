from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
import os

from app.services.voice import voice_service
from app.schemas.models import VoiceTranscribeResponse, VoiceSynthesizeRequest
from app.core.dependencies import get_current_user, user_from_token

router = APIRouter(prefix="/voice", tags=["voice"])

# Роутер подключён без общей зависимости (synthesize_stream проверяет токен из URL сам),
# поэтому остальные ручки требуют вход явно
@router.post("/transcribe", response_model=VoiceTranscribeResponse, dependencies=[Depends(get_current_user)])
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

@router.post("/synthesize", dependencies=[Depends(get_current_user)])
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
    
    # Validate token from URL parameter (<audio> не умеет слать заголовок Authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Token required")
    if user_from_token(token) is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    return StreamingResponse(voice_service.synthesize_stream(text), media_type="audio/mpeg")
