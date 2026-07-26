import json
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.config import settings
from app.core.dependencies import db_manager
from app.services.streaming import process_question_streaming

logger = logging.getLogger(__name__)

router = APIRouter()


def _verify_ws_token(token: str) -> Optional[dict]:
    """Verify JWT token from WebSocket query param."""
    try:
        import jwt

        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username = payload.get("sub")
        role = payload.get("role")
        if not username or not role:
            logger.warning("WS auth: token decoded but missing sub/role")
            return None
        return {"username": username, "role": role}
    except jwt.ExpiredSignatureError:
        logger.warning("WS auth: token expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning(f"WS auth: invalid token: {e}")
        return None
    except Exception as e:
        logger.warning(f"WS auth: unexpected error: {e}")
        return None


@router.websocket("/ws/ai")
async def ws_ai(websocket: WebSocket):
    """WebSocket endpoint for streaming AI queries."""
    token = websocket.query_params.get("token")
    if not token:
        logger.warning("WS: connection rejected — no token in query params")
        await websocket.close(code=4001, reason="Missing token")
        return

    user = _verify_ws_token(token)
    if not user:
        logger.warning("WS: connection rejected — token verification failed")
        await websocket.close(code=4001, reason="Invalid token")
        return

    await websocket.accept()
    logger.info(f"WebSocket connected: {user['username']}")

    cancel_event = asyncio.Event()
    clarification_queue: asyncio.Queue = asyncio.Queue()
    active_task: Optional[asyncio.Task] = None

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(
                    json.dumps({"type": "error", "message": "Invalid JSON"})
                )
                continue

            msg_type = msg.get("type")

            if msg_type == "ask":
                # Cancel any previous task
                if active_task and not active_task.done():
                    cancel_event.set()
                    active_task.cancel()
                    try:
                        await active_task
                    except (asyncio.CancelledError, Exception):
                        pass

                cancel_event.clear()
                # Drain clarification queue
                while not clarification_queue.empty():
                    try:
                        clarification_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                question = msg.get("question", "")
                extended = msg.get("extended", False)
                session_id = msg.get("session_id")
                dataset_id = msg.get("dataset_id")

                if not question.strip():
                    await websocket.send_text(
                        json.dumps({"type": "error", "message": "Empty question"})
                    )
                    continue

                async def ws_send(message: dict):
                    try:
                        await websocket.send_text(
                            json.dumps(message, ensure_ascii=False, default=str)
                        )
                    except Exception:
                        pass

                async def run_query():
                    db_conn = db_manager.get_connection()
                    try:
                        await process_question_streaming(
                            ws_send=ws_send,
                            cancel_event=cancel_event,
                            clarification_queue=clarification_queue,
                            question=question,
                            db_conn=db_conn,
                            extended=extended,
                            session_id=session_id,
                            dataset_id=dataset_id,
                            user_id=user.get("username"),
                        )
                    except Exception as e:
                        logger.exception(f"Query processing error: {e}")

                active_task = asyncio.create_task(run_query())

            elif msg_type == "cancel":
                cancel_event.set()
                if active_task and not active_task.done():
                    active_task.cancel()
                    try:
                        await asyncio.wait_for(active_task, timeout=5.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                        pass
                await websocket.send_text(
                    json.dumps({"type": "cancelled"})
                )

            elif msg_type == "clarification":
                text = msg.get("text", "")
                if text.strip():
                    await clarification_queue.put(text)
                    await websocket.send_text(
                        json.dumps({"type": "ack_clarification"})
                    )

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {user['username']}")
        cancel_event.set()
        if active_task and not active_task.done():
            active_task.cancel()
    except Exception as e:
        logger.exception(f"WebSocket error: {e}")
        cancel_event.set()
        if active_task and not active_task.done():
            active_task.cancel()
