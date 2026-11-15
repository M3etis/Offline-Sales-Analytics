from __future__ import annotations
import httpx
import json
import logging
import time
import re
from typing import Any, Optional

from app.core.config import settings
from app.services.model_catalog import normalize_name

# ── Domain re-exports (backward-compatible) ───────────────────────────────────
# streaming.py and other callers import these names directly from this module.
# Реализации живут только в app.domain.ai: не определяйте здесь одноимённые функции —
# локальная копия молча перекрывает импорт, и правки в domain перестают действовать.
from app.domain.ai.intent_classifier import classify_intent_fast  # noqa: F401
from app.domain.ai.sql_utils import (
    extract_sql       as _extract_sql,        # noqa: F401
    adjust_limit      as _adjust_limit,
    fix_cte_errors    as _fix_cte_errors,
    fix_current_date  as _fix_current_date,   # noqa: F401
    fix_filter_values as _fix_filter_values,  # noqa: F401
    remap_table_names as _remap_table_names,  # noqa: F401
)
from app.domain.ai.response_builder import (
    inject_count_header  as _inject_count_header,   # noqa: F401
    extract_chart_key    as _extract_chart_key,     # noqa: F401
    detect_chart_type    as _detect_chart_type,     # noqa: F401
    normalize_currency   as _normalize_currency,    # noqa: F401
)
from app.shared.prompts.templates import (
    SUMMARY_PROMPT,            # noqa: F401
    SUMMARY_PROMPT_EXTENDED,   # noqa: F401
    get_system_prompt,         # noqa: F401
    get_sql_prompt,            # noqa: F401
    currency_rule,             # noqa: F401
    summary_system_message,    # noqa: F401
)
from app.services.currency import currency_changed_at, get_local_currency

logger = logging.getLogger(__name__)


def recent_history_rows(db_conn, session_id: str, limit: int = 10) -> list[tuple]:
    """Последние сообщения сессии в хронологическом порядке.

    Раньше брались первые 10 сообщений: в длинной сессии свежий контекст до модели не доходил.
    """
    return db_conn.execute(
        "SELECT role, content FROM (SELECT role, content, created_at FROM chat_messages "
        "WHERE session_id = ? ORDER BY created_at DESC LIMIT ?) ORDER BY created_at ASC",
        [session_id, limit],
    ).fetchall()


class OllamaClient:
    """Client for local Ollama LLM API."""

    def __init__(self):
        self.base_url = settings.OLLAMA_URL
        self.model = settings.OLLAMA_MODEL
        self._client: httpx.AsyncClient | None = None

    def _get_active_model(self) -> str:
        return self.model
    
    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=300.0)
        return self._client
    
    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    async def check_health(self) -> dict:
        """Check if Ollama is accessible and model is available."""
        try:
            client = self._get_client()
            # Check if Ollama is running
            response = await client.get(f"{self.base_url}/api/tags", timeout=5.0)
            response.raise_for_status()
            models = response.json().get("models", [])
            model_names = [m.get("name", "") for m in models]

            # Точное имя с тегом, как в разделе моделей: «qwen2.5» — это «qwen2.5:latest», а не «qwen2.5:7b»
            target = normalize_name(self.model)
            model_available = any(normalize_name(name) == target for name in model_names)

            return {
                "status": "ok",
                "model_available": model_available,
                "model": self.model,
                "available_models": model_names[:5],
            }
        except httpx.ConnectError:
            return {
                "status": "offline",
                "error": "Ollama не запущена",
                "hint": "Установите Ollama: https://ollama.com и запустите: ollama serve",
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
            }

    async def generate(self, prompt: str, system: str = "", num_predict: int = 200) -> str:
        """Generate response from Ollama.

        Args:
            num_predict: Max tokens to generate. Use 200 for SQL, 400 for summaries.
        """
        try:
            client = self._get_client()
            response = await client.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self._get_active_model(),
                    "prompt": prompt,
                    "system": system,
                    "stream": False,
                    "options": {
                        "temperature": 0.0,
                        "top_p": 0.9,
                        "num_predict": num_predict,
                        "num_ctx": 8192,
                    },
                    "keep_alive": "30m",
                }
            )
            response.raise_for_status()
            return response.json().get("response", "").strip()
        except httpx.ConnectError:
            health = await self.check_health()
            if health["status"] == "offline":
                raise ConnectionError(
                    "Ollama не запущена. Установите Ollama с https://ollama.com и запустите командой: ollama serve"
                )
            raise ConnectionError(
                "Не удалось подключиться к Ollama. Убедитесь, что Ollama запущена: ollama serve"
            )
        except httpx.ReadTimeout:
            raise TimeoutError(
                "Модель не ответила вовремя. Возможно, она перегружена или слишком велика для текущих ресурсов."
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise ConnectionError(
                    f"Модель '{self.model}' не найдена в Ollama. Скачайте её в разделе «Настройки ассистента» или командой: ollama pull {self.model}"
                )
            raise ConnectionError(f"Ошибка Ollama API: {e.response.status_code}")

    async def generate_stream(self, prompt: str, system: str = "", num_predict: int = 250):
        """Yield tokens from Ollama streaming API (NDJSON)."""
        try:
            client = self._get_client()
            async with client.stream(
                "POST",
                f"{self.base_url}/api/generate",
                json={
                    "model": self._get_active_model(),
                    "prompt": prompt,
                    "system": system,
                    "stream": True,
                    "options": {
                        "temperature": 0.1,
                        "top_p": 0.9,
                        "num_predict": num_predict,
                        "num_ctx": 8192,
                    },
                    "keep_alive": "30m",
                },
            ) as response:
                response.raise_for_status()
                # aiter_lines: aiter_text отдаёт сетевые куски, и JSON-строка, разрезанная
                # между ними, не разбиралась — терялись слова ответа или [CHART_KEY]
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    token = obj.get("response", "")
                    if token:
                        yield token
                    if obj.get("done"):
                        break
        except httpx.ConnectError:
            raise ConnectionError("Не удалось подключиться к Ollama.")
        except httpx.ReadTimeout:
            raise TimeoutError("Модель не ответила вовремя.")
        except httpx.HTTPStatusError as e:
            raise ConnectionError(f"Ошибка Ollama API: {e.response.status_code}")

    async def warmup(self):
        """Pre-load model into Ollama memory with a tiny dummy request."""
        try:
            client = self._get_client()
            await client.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self._get_active_model(),
                    "prompt": "Hi",
                    "stream": False,
                    "options": {"num_predict": 1, "num_ctx": 4096},
                    "keep_alive": "30m",
                },
                timeout=15.0,
            )
            logger.info("✅ Ollama model warmed up and loaded into memory")
        except Exception as e:
            logger.warning(f"⚠️ Ollama warmup failed (model may not be running): {e}")
    
    async def generate_sql(self, question: str, system_prompt: str, table_name: str, column_names: str,
                           examples: list[dict] = None, is_multi_table: bool = False, date_expr: str | None = None) -> str:
        """Generate SQL query from natural language question."""
        prompt = get_sql_prompt(table_name, column_names, question, examples, is_multi_table, date_expr=date_expr)
        try:
            logger.info(f"Generating SQL for question: {question}")
            raw = await self.generate(prompt, system=system_prompt, num_predict=1000)
            sql = _extract_sql(raw)
            sql = _adjust_limit(question, sql)
            sql_before = sql
            sql = _fix_cte_errors(sql)
            if sql != sql_before:
                logger.info(f"SQL CTE fix applied: {sql[:200]}")
            logger.info(f"Extracted SQL: {sql[:200]}")
            return sql
        except Exception as e:
            logger.error(f"Failed to generate SQL: {e}")
            raise

    async def fix_sql(self, question: str, failed_sql: str, error: str, system_prompt: str, is_multi_table: bool = False) -> str:
        """Generate corrected SQL based on an execution error message."""
        fix_prompt = (
            f"Запрос пользователя: {question}\n\n"
            f"Сгенерированный SQL:\n{failed_sql}\n\n"
            f"Ошибка при выполнении:\n{error}\n\n"
            "Исправь SQL-запрос. Используй ТОЛЬКО колонки из схемы в системном промпте.\n"
            "Верни ТОЛЬКО исправленный SQL без объяснений."
        )
        try:
            logger.info(f"Fixing SQL based on error: {error[:100]}")
            raw = await self.generate(fix_prompt, system=system_prompt, num_predict=1000)
            sql = _extract_sql(raw)
            sql = _adjust_limit(question, sql)
            sql = _fix_cte_errors(sql)
            logger.info(f"Fixed SQL: {sql[:200]}")
            return sql
        except Exception as e:
            logger.error(f"Failed to fix SQL: {e}")
            raise


ollama_client = OllamaClient()


import uuid
from datetime import datetime


def _get_few_shot_examples(db_conn, dataset_id: Optional[str], intent: str, limit: int = 3) -> list[dict]:
    """Retrieve few-shot examples from successful past queries for the same dataset and intent."""
    if not db_conn or not dataset_id:
        return []

    try:
        # Find past assistant messages with positive feedback and matching intent
        rows = db_conn.execute("""
            SELECT cm.content, cm.sql_query, cm.data_summary
            FROM chat_messages cm
            JOIN chat_sessions cs ON cm.session_id = cs.id
            WHERE cm.role = 'assistant'
            AND cm.feedback = 'positive'
            AND cm.intent = ?
            AND cs.dataset_id = ?
            AND cm.sql_query IS NOT NULL
            ORDER BY cm.created_at DESC
            LIMIT ?
        """, [intent, dataset_id, limit]).fetchall()

        examples = []
        for row in rows:
            examples.append({
                "answer": row[0],
                "sql": row[1],
                "intent": intent,
            })
        return examples
    except Exception as e:
        logger.error(f"Failed to get few-shot examples: {e}")
        return []


def _save_user_message(session_id: Optional[str], db_conn, question: str) -> None:
    """Save user question to chat session history. Only works with existing sessions."""
    if not session_id or not db_conn:
        return
    try:
        now = datetime.now().isoformat()
        user_msg_id = str(uuid.uuid4())
        db_conn.execute(
            "INSERT INTO chat_messages (id, session_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
            [user_msg_id, session_id, "user", question, now]
        )
        # Auto-rename session from default title to first question
        session_row = db_conn.execute(
            "SELECT title FROM chat_sessions WHERE id = ?", [session_id]
        ).fetchone()
        if session_row and session_row[0] == "Новая сессия":
            new_title = question[:80] + ("..." if len(question) > 80 else "")
            db_conn.execute("UPDATE chat_sessions SET title = ? WHERE id = ?", [new_title, session_id])
    except Exception as e:
        logger.error(f"Failed to save user message: {e}")


def _ensure_session(db_conn, dataset_key: str = "default") -> str:
    """Get or create a default session for API calls without session_id."""
    try:
        existing = db_conn.execute(
            "SELECT id FROM chat_sessions WHERE title = 'API Cache' AND user_id = 'system' LIMIT 1"
        ).fetchone()
        if existing:
            return existing[0]
        sid = str(uuid.uuid4())
        now = datetime.now().isoformat()
        db_conn.execute(
            "INSERT INTO chat_sessions (id, title, dataset_id, user_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            [sid, 'API Cache', dataset_key, 'system', now, now]
        )
        return sid
    except Exception:
        return None


def _save_assistant_message(session_id: Optional[str], db_conn, result: dict[str, Any]) -> None:
    """Save assistant answer to chat session history (called after cache hit or query)."""
    # id прежнего сохранения (например, из ответа в кэше) к новому сообщению не относится
    result.pop("message_id", None)
    if not db_conn:
        return
    if not session_id:
        session_id = _ensure_session(db_conn)
        if not session_id:
            return
    try:
        cache_payload = {
            "question": result.get("_question", ""),
            "intent": result.get("intent"),
            "sql": result.get("sql"),
            "chart_type": result.get("chart_type"),
            # Без ключа метрики график повторного ответа и открытой заново сессии рисовал все колонки сразу
            "chart_value_key": result.get("chart_value_key"),
            # Подпись условий ответа (модель, правила, подробность) и признак контекста диалога — для кеша
            "cache_signature": result.get("_cache_signature"),
            "contextual": bool(result.get("_contextual")),
            "data": result.get("data"),
            "processing_time": result.get("processing_time"),
            "is_cached": result.get("is_cached", False),
            "cached_at": result.get("cached_at"),
            "error": result.get("error")
        }
        data_summary = json.dumps(cache_payload, ensure_ascii=False, default=str)
        assistant_msg_id = str(uuid.uuid4())
        db_conn.execute(
            "INSERT INTO chat_messages (id, session_id, role, content, data_summary, sql_query, intent, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [assistant_msg_id, session_id, "assistant", result.get("answer", ""), data_summary, result.get("sql"), result.get("intent"), datetime.now().isoformat()]
        )
        # Фронтенд ставит оценку 👍/👎 по этому id, не дожидаясь перезагрузки диалога
        result["message_id"] = assistant_msg_id
        db_conn.execute("UPDATE chat_sessions SET updated_at = ? WHERE id = ?", [datetime.now().isoformat(), session_id])
        logger.info(f"💾 Saved assistant msg to session {session_id}")
    except Exception as e:
        logger.error(f"Failed to save assistant message: {e}")


# ── Общие для HTTP и WebSocket: контекст диалога, кеш ответов, текст ошибки ────────────────────

#: Версия логики ответов: ответы, сохранённые прежней версией (до готовых расчётов), из кеша не выдаются
ANSWER_LOGIC_VERSION = "2026-11-pipeline-2"


def load_context(db_conn, session_id: Optional[str], question: str) -> tuple[str, list[tuple]]:
    """(вопрос с контекстом диалога, последние сообщения сессии)."""
    if not session_id:
        return question, []
    try:
        history_rows = recent_history_rows(db_conn, session_id)
    except Exception as e:
        logger.error(f"Failed to load session history: {e}")
        return question, []
    if not history_rows:
        return question, []
    history_text = "ПРЕДЫДУЩИЙ КОНТЕКСТ ДИАЛОГА:\n"
    for r, c in history_rows:
        history_text += f"{'Пользователь' if r == 'user' else 'Аналитик'}: {c}\n"
    return history_text + "\nТЕКУЩИЙ ВОПРОС: " + question, history_rows


def cache_signature(db_conn, extended: bool) -> str:
    """Всё, от чего зависит текст ответа, кроме вопроса и данных: модель, правила ассистента, подробность.

    Раньше ответ из кеша выдавался после смены модели, правки правил или с другим режимом «подробно».
    """
    import hashlib

    try:
        rules = sorted(db_conn.execute("SELECT category, content FROM assistant_instructions").fetchall())
    except Exception:
        rules = []
    from app.services.column_dict import user_aliases

    # Словарь колонок пользователя меняет роли колонок, а значит и ответ
    aliases = sorted(user_aliases(db_conn).items())
    raw = json.dumps([ANSWER_LOGIC_VERSION, ollama_client.model, bool(extended), rules, aliases],
                     ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def find_cached_answer(db_conn, question: str, dataset_id: Optional[str], signature: str) -> Optional[dict]:
    """Готовый ответ на тот же вопрос по тем же данным и при тех же условиях. None — считать заново.

    Не берём: ответы с контекстом диалога («а по прибыли?» значит разное в разных сессиях), с ошибкой,
    с дизлайком, старше загрузки данных или смены валюты, с другой подписью условий.
    """
    normalized = re.sub(r'[-–—]', ' ', question.strip().lower())
    last_update_row = db_conn.execute("SELECT MAX(uploaded_at) FROM datasets").fetchone()
    last_db_update = last_update_row[0] if last_update_row and last_update_row[0] else datetime.min
    if isinstance(last_db_update, str):
        last_db_update = datetime.fromisoformat(last_db_update)
    changed_at = currency_changed_at(db_conn)
    if changed_at and changed_at > last_db_update:
        last_db_update = changed_at
    rows = db_conn.execute("""
        SELECT cm.content as answer, cm.data_summary, cm.created_at
        FROM chat_messages cm
        JOIN chat_sessions cs ON cm.session_id = cs.id
        WHERE cm.role = 'assistant'
        AND cm.data_summary IS NOT NULL
        AND cs.dataset_id = ?
        AND cm.feedback IS DISTINCT FROM 'negative'
        ORDER BY cm.created_at DESC LIMIT 10
    """, [str(dataset_id) if dataset_id else 'default']).fetchall()
    for ans, summary_str, created_at in rows or []:
        try:
            summary = json.loads(summary_str)
        except (TypeError, ValueError):
            continue
        if re.sub(r'[-–—]', ' ', summary.get("question", "").strip().lower()) != normalized:
            continue
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        if created_at < last_db_update or summary.get("contextual") or summary.get("cache_signature") != signature:
            return None
        if summary.get("error") or "Произошла ошибка" in (ans or "") or "непредвиденная ошибка" in (ans or ""):
            return None
        # Ответы из кэша — тоже через нормализацию валюты: сохранённые до неё могут быть в рублях.
        # Заголовок «Найдено: N» не добавляем: сохранённый ответ уже в том виде, в каком его показали
        summary["answer"] = _normalize_currency(ans, summary.get("data"), get_local_currency(db_conn))
        summary["error"] = None
        summary["is_cached"] = True
        summary["cached_at"] = created_at.isoformat()
        return summary
    return None


def error_answer(exc: BaseException) -> str:
    """Текст для пользователя по ошибке модели — один для HTTP и WebSocket."""
    msg = str(exc)
    if isinstance(exc, TimeoutError):
        return "Модель не успела ответить — запрос занял слишком много времени. Попробуйте ещё раз."
    if isinstance(exc, ConnectionError):
        if "не найдена" in msg or "not found" in msg.lower():
            return f"Модель AI не найдена. {msg}"
        if "не запущена" in msg or "not running" in msg.lower():
            return f"Сервис AI недоступен. {msg}"
        return "Сервис AI временно недоступен. Проверьте запуск Ollama."
    return "Произошла непредвиденная ошибка при обработке вашего запроса."


async def process_question(question: str, db_conn, extended: bool = False, session_id: Optional[str] = None, dataset_id: Optional[str] = None, user_id: Optional[str] = None, status_callback=None) -> dict[str, Any]:
    """Process a user question through the full AI pipeline."""
    start_time = time.time()

    def _status(msg: str):
        if status_callback:
            try:
                status_callback(msg)
            except Exception:
                pass

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

    signature = cache_signature(db_conn, extended)
    result["_cache_signature"] = signature
    result["_contextual"] = contextual_question != question
    try:
        cached = find_cached_answer(db_conn, question, dataset_id, signature) if contextual_question == question else None
        if cached:
            logger.info(f"✅ CACHE HIT from chat_messages: '{question[:50]}...'")
            cached["processing_time"] = time.time() - start_time
            cached["_question"] = question
            cached["_cache_signature"] = signature
            _save_assistant_message(session_id, db_conn, cached)
            return cached
    except Exception as e:
        logger.error(f"Cache check failed: {e}")

    logger.info(f"❌ CACHE MISS: Processing new query '{question[:50]}...'")

    try:
        from app.services import pipeline

        async def _astatus(msg: str):
            _status(msg)

        prep = await pipeline.prepare(
            question, contextual_question, db_conn, dataset_id,
            status=_astatus, history_rows=history_rows, ollama_client=ollama_client,
            few_shot_history=lambda: _get_few_shot_examples(db_conn, dataset_id, classify_intent_fast(question)),
        )
        result["intent"] = prep.intent
        result["data"] = prep.data
        result["sql"] = prep.sql
        result["chart_type"] = prep.chart_type
        if prep.chart_value_key:
            result["chart_value_key"] = prep.chart_value_key

        if prep.answer is not None:
            result["answer"] = prep.answer
            if prep.error:
                result["error"] = prep.error
        elif result["data"]:
            _status("Формирую ответ...")
            prompt, system_msg = pipeline.build_summary_request(prep, contextual_question, db_conn, extended)
            raw = await ollama_client.generate(prompt, system=system_msg, num_predict=pipeline.SUMMARY_NUM_PREDICT)
            raw = await pipeline.regenerate_if_needed(raw, prompt, system_msg, ollama_client, prep)
            result["answer"] = _inject_count_header(pipeline.finalize_answer(raw, prep, db_conn), result["data"])
        else:
            result["answer"] = pipeline.EMPTY_ANSWER if result.get("sql") else "По вашему запросу нет данных."

    except Exception as e:
        if not isinstance(e, (TimeoutError, ConnectionError)):
            logger.exception("Error processing question")
        result["error"] = str(e)
        result["answer"] = error_answer(e)
        result["retryable"] = True
    
    result["processing_time"] = round(time.time() - start_time, 2)

    # Save to session history
    result["_question"] = question
    # Вопрос уже сохранён в начале — второй раз сохраняем только ответ (раньше вопрос дублировался)
    _save_assistant_message(session_id, db_conn, result)
            
    return result
