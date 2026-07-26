from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from datetime import datetime
import json
import asyncio

from app.core.dependencies import get_db
from app.services.llm import process_question, ollama_client
from app.schemas.models import AskRequest, AskResponse

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/ollama-status")
async def ollama_status():
    """Check Ollama availability and model status."""
    return await ollama_client.check_health()


@router.post("/ask", response_model=AskResponse)
async def ask_question(request: AskRequest, db=Depends(get_db)):
    result = await process_question(request.question, db, request.extended, request.session_id, request.dataset_id)

    with open("logs/queries.log", "a", encoding="utf-8") as f:
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "question": request.question,
            "dataset_id": request.dataset_id,
            "intent": result["intent"],
            "processing_time": result["processing_time"],
            "status": "success" if not result.get("error") else "error"
        }
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

    return AskResponse(**result)


@router.post("/ask-stream")
async def ask_question_stream(request: AskRequest, db=Depends(get_db)):
    """SSE endpoint that streams status updates during processing."""
    status_queue = asyncio.Queue()

    def status_callback(msg: str):
        try:
            status_queue.put_nowait(msg)
        except Exception:
            pass

    async def event_stream():
        # Run process_question in background
        async def run_query():
            try:
                result = await process_question(
                    request.question, db, request.extended,
                    request.session_id, request.dataset_id,
                    status_callback=status_callback
                )
                with open("logs/queries.log", "a", encoding="utf-8") as f:
                    log_entry = {
                        "timestamp": datetime.now().isoformat(),
                        "question": request.question,
                        "dataset_id": request.dataset_id,
                        "intent": result["intent"],
                        "processing_time": result["processing_time"],
                        "status": "success" if not result.get("error") else "error"
                    }
                    f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
                await status_queue.put(("done", result))
            except Exception as e:
                await status_queue.put(("error", {"error": str(e)}))

        task = asyncio.create_task(run_query())

        while True:
            try:
                item = await asyncio.wait_for(status_queue.get(), timeout=120.0)
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'status': 'error', 'error': 'Timeout'}, ensure_ascii=False)}\n\n"
                break

            if isinstance(item, tuple) and item[0] == "done":
                result = item[1]
                yield f"data: {json.dumps({'status': 'done', 'result': result}, ensure_ascii=False)}\n\n"
                break
            elif isinstance(item, tuple) and item[0] == "error":
                yield f"data: {json.dumps({'status': 'error', 'error': item[1]}, ensure_ascii=False)}\n\n"
                break
            else:
                yield f"data: {json.dumps({'status': 'progress', 'message': item}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )
