import json
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.dependencies import db_manager, user_from_token
from app.services.streaming import process_question_streaming

logger = logging.getLogger(__name__)

router = APIRouter()


def _verify_ws_token(token: str) -> Optional[dict]:
    """Verify JWT token from WebSocket query param (пользователь сверяется с базой)."""
    user = user_from_token(token)
    if user is None:
        logger.warning("WS auth: invalid or expired token, or user no longer exists")
    return user


def _owns_session(session_id: str, username: str) -> bool:
    """Сессия принадлежит пользователю или ещё не создана (тогда она станет его)."""
    cur = db_manager.get_connection().cursor()
    try:
        row = cur.execute("SELECT user_id FROM chat_sessions WHERE id = ?", [session_id]).fetchone()
    finally:
        cur.close()
    return row is None or row[0] == username


@router.websocket("/ws/ai")
async def ws_ai(websocket: WebSocket):
    """WebSocket endpoint for streaming AI queries."""
    token = websocket.query_params.get("token")
    user = _verify_ws_token(token) if token else None
    if not user:
        # Сначала accept, потом close(4001): закрытие до accept uvicorn превращает в HTTP 403,
        # браузер видит код 1006 и переподключается бесконечно, не уводя на страницу входа
        logger.warning("WS: connection rejected — missing or invalid token")
        await websocket.accept()
        await websocket.close(code=4001, reason="Invalid token")
        return

    await websocket.accept()
    logger.info(f"WebSocket connected: {user['username']}")

    cancel_event = asyncio.Event()
    clarification_queue: asyncio.Queue = asyncio.Queue()
    active_task: Optional[asyncio.Task] = None
    # Сообщил ли текущий запрос об отмене сам: process_question_streaming при CancelledError
    # шлёт «cancelled», и второе подтверждение от обработчика cancel клиент принял бы за отмену
    # следующего вопроса
    request_state = {"cancel_sent": False}

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                msg = None
            # Не объект ([], 1, "x") раньше падал на msg.get и закрывал сокет вместе с текущим запросом
            if not isinstance(msg, dict):
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
                extended = bool(msg.get("extended", False))
                session_id = msg.get("session_id")
                dataset_id = msg.get("dataset_id")

                if not isinstance(question, str) or not question.strip():
                    await websocket.send_text(
                        json.dumps({"type": "error", "message": "Empty question"})
                    )
                    continue
                if session_id is not None and (not isinstance(session_id, str) or not _owns_session(session_id, user["username"])):
                    # Чужая сессия: её история ушла бы в контекст модели, а ответ — в чужой диалог
                    await websocket.send_text(
                        json.dumps({"type": "error", "message": "Session not found"})
                    )
                    continue

                request_state = {"cancel_sent": False}

                # _state привязан к этому запросу: замыкание в цикле иначе видело бы состояние следующего
                async def ws_send(message: dict, _state=request_state):
                    if message.get("type") == "cancelled":
                        _state["cancel_sent"] = True
                    try:
                        await websocket.send_text(
                            json.dumps(message, ensure_ascii=False, default=str)
                        )
                    except Exception:
                        pass

                async def run_query():
                    # Свой курсор на запрос: общее соединение используют и HTTP-ручки
                    db_conn = db_manager.get_connection().cursor()
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
                    finally:
                        db_conn.close()

                active_task = asyncio.create_task(run_query())

            elif msg_type == "cancel":
                request_state["cancel_sent"] = False
                cancel_event.set()
                if active_task and not active_task.done():
                    active_task.cancel()
                    try:
                        await asyncio.wait_for(active_task, timeout=5.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                        pass
                # Ровно одно подтверждение на каждый cancel
                if not request_state["cancel_sent"]:
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
