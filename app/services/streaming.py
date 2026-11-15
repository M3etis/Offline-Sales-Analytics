from __future__ import annotations
import time
import logging
import asyncio
from typing import Any, Optional, Callable, Awaitable

from app.services.llm import (
    ollama_client,
    _save_user_message,
    _save_assistant_message,
    _inject_count_header,
    summary_system_message,  # noqa: F401 — тест проверяет, что оба входа используют одно сообщение
    classify_intent_fast,
    _get_few_shot_examples,
    cache_signature,
    error_answer,
    find_cached_answer,
    load_context,
)
from app.services import pipeline

logger = logging.getLogger(__name__)

WsSend = Callable[[dict], Awaitable[None]]

# Выполнение SQL живёт в общем конвейере; имя оставлено для обратной совместимости
_run_sql = pipeline._run_sql


async def process_question_streaming(
    ws_send: WsSend,
    cancel_event: asyncio.Event,
    clarification_queue: asyncio.Queue,
    question: str,
    db_conn,
    extended: bool = False,
    session_id: Optional[str] = None,
    dataset_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> dict[str, Any]:
    """Streaming variant of process_question for WebSocket."""

    async def _status(msg: str):
        if cancel_event.is_set():
            raise asyncio.CancelledError()
        await ws_send({"type": "status", "message": msg})

    def _check_cancel():
        if cancel_event.is_set():
            raise asyncio.CancelledError()

    start_time = time.time()

    result = {
        "answer": "",
        "sql": None,
        "intent": None,
        "data": None,
        "chart_type": None,
        "error": None,
        "processing_time": 0,
    }

    contextual_question, history_rows = load_context(db_conn, session_id, question)

    # Save user message first (for session history)
    _save_user_message(session_id, db_conn, question)

    # Кеш — общий с HTTP-путём (llm.find_cached_answer): те же условия и тот же ключ
    signature = cache_signature(db_conn, extended)
    result["_cache_signature"] = signature
    result["_contextual"] = contextual_question != question
    try:
        cached = find_cached_answer(db_conn, question, dataset_id, signature) if contextual_question == question else None
        if cached:
            cached["processing_time"] = time.time() - start_time
            cached["_question"] = question
            cached["_cache_signature"] = signature
            _save_assistant_message(session_id, db_conn, cached)
            await ws_send({"type": "result", "data": cached})
            return cached
    except Exception as e:
        logger.error(f"Cache check failed: {e}")

    try:
        _check_cancel()
        prep = await pipeline.prepare(
            question, contextual_question, db_conn, dataset_id,
            status=_status, history_rows=history_rows, ollama_client=ollama_client,
            few_shot_history=lambda: _get_few_shot_examples(db_conn, dataset_id, classify_intent_fast(question)),
        )
        result["intent"] = prep.intent
        result["data"] = prep.data
        result["sql"] = prep.sql
        result["chart_type"] = prep.chart_type
        if prep.chart_value_key:
            result["chart_value_key"] = prep.chart_value_key

        if prep.answer is not None:
            # Готовый ответ без генерации: встречный вопрос модели или ошибка SQL
            result["answer"] = prep.answer
            result["processing_time"] = round(time.time() - start_time, 2)
            result["_question"] = question
            if prep.error:
                result["error"] = prep.error
                _save_assistant_message(session_id, db_conn, result)
                await ws_send({"type": "error", "message": result["answer"]})
                return result
            _save_assistant_message(session_id, db_conn, result)
            await ws_send({"type": "result", "data": result})
            return result

        _check_cancel()
        await _status("Формирую ответ...")
        if result["data"]:
            prompt, system_msg = pipeline.build_summary_request(prep, contextual_question, db_conn, extended)
            answer_parts = []
            header = _inject_count_header("", result["data"])
            if header:
                answer_parts.append(header)
                await ws_send({"type": "token", "text": header})

            async for token in ollama_client.generate_stream(prompt, system=system_msg,
                                                             num_predict=pipeline.SUMMARY_NUM_PREDICT):
                _check_cancel()
                try:
                    clarification = clarification_queue.get_nowait()
                    await ws_send({"type": "ack_clarification"})
                    answer_parts.append(f"\n[Уточнение получено: {clarification}]\n")
                except asyncio.QueueEmpty:
                    pass
                answer_parts.append(token)
                await ws_send({"type": "token", "text": token})

            body = "".join(answer_parts)[len(header):].strip()
            # Финальный ответ из «result» заменяет в интерфейсе стриминговый текст —
            # поэтому исправления (разряды, иероглифы, служебные теги) пользователь увидит
            body = await pipeline.regenerate_if_needed(body, prompt, system_msg, ollama_client, prep)
            result["answer"] = header + pipeline.finalize_answer(body, prep, db_conn)
        else:
            result["answer"] = pipeline.EMPTY_ANSWER if result.get("sql") else "По вашему запросу нет данных."

    except asyncio.CancelledError:
        await ws_send({"type": "cancelled"})
        result["answer"] = "Запрос отменён."
        result["processing_time"] = time.time() - start_time
        return result
    except Exception as e:
        if not isinstance(e, (TimeoutError, ConnectionError)):
            logger.exception("Error in streaming process_question")
        result["error"] = str(e)
        # Тот же текст, что в HTTP-пути: «модель не найдена — скачайте её в настройках», а не общее «недоступен»
        result["answer"] = error_answer(e)
        result["retryable"] = True
        await ws_send({"type": "error", "message": result["answer"], "retryable": True})

    result["processing_time"] = round(time.time() - start_time, 2)
    result["_question"] = question
    _save_assistant_message(session_id, db_conn, result)

    await ws_send({"type": "result", "data": result})
    return result
