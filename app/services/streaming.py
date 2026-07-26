from __future__ import annotations
import json
import re
import time
import logging
import asyncio
from typing import Any, Optional, Callable, Awaitable
from datetime import datetime, date, timedelta

from app.services.llm import (
    ollama_client,
    _extract_sql,
    _remap_table_names,
    _fix_current_date,
    _detect_chart_type,
    _save_to_session,
    _save_user_message,
    _save_assistant_message,
    _inject_count_header,
    _extract_chart_key,
    classify_intent_fast,
    get_system_prompt,
    get_multi_table_system_prompt,
    get_sql_prompt,
    _get_few_shot_examples,
    INTENT_PROMPT,
    SUMMARY_PROMPT,
    SUMMARY_PROMPT_EXTENDED,
)
from app.services import analytics as analytics_svc
from app.services.preanalysis import load_manifest
from app.utils.sql_safety import full_validate, sanitize_sql

logger = logging.getLogger(__name__)

WsSend = Callable[[dict], Awaitable[None]]

MAX_SQL_RETRIES = 1


async def _retry_sql_fix(
    question: str,
    failed_sql: str,
    error: str,
    sys_prompt: str,
    is_multi_table: bool,
    ollama_client,
) -> str | None:
    """Try to fix a failed SQL by asking the LLM with the error context."""
    for attempt in range(MAX_SQL_RETRIES):
        try:
            logger.info(f"SQL retry attempt {attempt + 1}/{MAX_SQL_RETRIES}")
            fixed = await ollama_client.fix_sql(
                question, failed_sql, error, sys_prompt, is_multi_table
            )
            if fixed and fixed != failed_sql:
                return fixed
        except Exception as e:
            logger.error(f"SQL fix attempt failed: {e}")
    return None


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

    contextual_question = question

    # Load session history
    if session_id:
        try:
            history_rows = db_conn.execute(
                "SELECT role, content FROM chat_messages WHERE session_id = ? ORDER BY created_at ASC LIMIT 10",
                [session_id],
            ).fetchall()
            if history_rows:
                history_text = "ПРЕДЫДУЩИЙ КОНТЕКСТ ДИАЛОГА:\n"
                for r, c in history_rows:
                    history_text += f"{'Пользователь' if r == 'user' else 'Аналитик'}: {c}\n"
                history_text += "\nТЕКУЩИЙ ВОПРОС: "
                contextual_question = history_text + question
        except Exception as e:
            logger.error(f"Failed to load session history: {e}")

    # Save user message first (for session history)
    _save_user_message(session_id, db_conn, question)

    # Check cache via chat_messages
    dataset_key = str(dataset_id) if dataset_id else "global"
    normalized_question = re.sub(r'[-–—]', ' ', question.strip().lower())

    try:
        last_update_row = db_conn.execute(
            "SELECT MAX(uploaded_at) FROM datasets"
        ).fetchone()
        last_db_update = (
            last_update_row[0] if last_update_row and last_update_row[0] else datetime.min
        )
        if isinstance(last_db_update, str):
            last_db_update = datetime.fromisoformat(last_db_update)

        cache_row = db_conn.execute("""
            SELECT cm.content as answer, cm.data_summary, cm.created_at
            FROM chat_messages cm
            JOIN chat_sessions cs ON cm.session_id = cs.id
            WHERE cm.role = 'assistant'
            AND cm.data_summary IS NOT NULL
            AND (cs.dataset_id = ? OR cs.dataset_id = 'default')
            ORDER BY cm.created_at DESC LIMIT 10
        """, [dataset_key]).fetchall()

        # Find best match by question text in data_summary
        cache_rows = cache_row
        cache_row = None
        for row in cache_rows or []:
            try:
                summary = json.loads(row[1])
                cached_q = re.sub(r'[-–—]', ' ', summary.get("question", "").strip().lower())
                if cached_q == normalized_question:
                    cache_row = row
                    break
            except:
                continue

        if cache_row:
            ans, summary_str, created_at = cache_row
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)
            if created_at >= last_db_update and summary_str:
                cached_result = json.loads(summary_str)
                if not (cached_result.get("error") or "Произошла ошибка" in (ans or "")):
                    cached_result["answer"] = _inject_count_header(ans, cached_result.get("data"))
                    cached_result["error"] = None
                    cached_result["is_cached"] = True
                    cached_result["cached_at"] = created_at.isoformat()
                    cached_result["processing_time"] = time.time() - start_time
                    cached_result["_question"] = question
                    _save_assistant_message(session_id, db_conn, cached_result)
                    await ws_send({"type": "result", "data": cached_result})
                    return cached_result
    except Exception as e:
        logger.error(f"Cache check failed: {e}")

    try:
        # Stage: Manifest
        _check_cancel()
        await _status("Анализирую запрос...")
        manifest = load_manifest(db_conn, dataset_id)

        # Stage: Intent classification (rule-based, instant — no LLM call)
        _check_cancel()
        await _status("Определяю тип запроса...")
        intent = classify_intent_fast(contextual_question)
        result["intent"] = intent

        # Stage: Data pre-fetch
        _check_cancel()
        await _status("Извлекаю данные...")
        data = None

        if intent == "kpi":
            data = analytics_svc.get_kpis(db_conn, dataset_id=dataset_id, manifest=manifest)
            result["data"] = [data] if isinstance(data, dict) else data
        elif intent == "comparison":
            raw_comp = analytics_svc.compare_periods(
                db_conn,
                current_start=(date.today().replace(day=1)).strftime("%Y-%m-%d"),
                current_end=date.today().strftime("%Y-%m-%d"),
                prev_start=(date.today().replace(day=1) - timedelta(days=1))
                .replace(day=1)
                .strftime("%Y-%m-%d"),
                prev_end=(date.today().replace(day=1) - timedelta(days=1)).strftime(
                    "%Y-%m-%d"
                ),
                dataset_id=dataset_id,
                manifest=manifest,
            )
            data = [
                {"Период": "Предыдущий", **raw_comp["previous"]},
                {"Период": "Текущий", **raw_comp["current"]},
            ]
            result["data"] = data
        elif intent == "top":
            question_lower = question.lower()
            if any(kw in question_lower for kw in ["категор", "групп"]):
                data = analytics_svc.sales_by_category(
                    db_conn, dataset_id=dataset_id, manifest=manifest
                )
                result["data"] = data
            elif any(kw in question_lower for kw in ["менеджер", "сотрудник", "продавец"]):
                data = analytics_svc.sales_by_manager(
                    db_conn, dataset_id=dataset_id, manifest=manifest
                )
                result["data"] = data
        elif intent == "drop":
            data = analytics_svc.find_weak_spots(
                db_conn, dataset_id=dataset_id, manifest=manifest
            )
            result["data"] = data
        elif intent == "chart":
            group_datasets_check = analytics_svc.get_group_datasets(db_conn, dataset_id)
            if len(group_datasets_check) <= 1:
                data = analytics_svc.sales_timeline(
                    db_conn, dataset_id=dataset_id, manifest=manifest
                )
                result["data"] = data
        elif intent == "summary":
            kpis = analytics_svc.get_kpis(
                db_conn, dataset_id=dataset_id, manifest=manifest
            )
            categories = analytics_svc.sales_by_category(
                db_conn, dataset_id=dataset_id, manifest=manifest
            )
            data = {"kpis": kpis, "categories": categories}
            result["data"] = [data] if isinstance(data, dict) else data

        if result.get("data") is None:
            # Stage: Schema preparation
            _check_cancel()
            group_datasets = analytics_svc.get_group_datasets(db_conn, dataset_id)
            is_group = len(group_datasets) > 1

            if is_group:
                relationships = []
                try:
                    rel_json = group_datasets[0].get("relationships")
                    if rel_json:
                        relationships = (
                            json.loads(rel_json) if isinstance(rel_json, str) else rel_json
                        )
                except Exception:
                    pass

                group_manifests = {}
                for gd in group_datasets:
                    ds_id = gd.get("id")
                    if ds_id:
                        m = load_manifest(db_conn, ds_id)
                        if m:
                            group_manifests[ds_id] = m

                sys_prompt = get_multi_table_system_prompt(
                    group_datasets,
                    relationships,
                    group_manifests if group_manifests else None,
                )
                all_schema_parts = []
                for gd in group_datasets:
                    try:
                        cols_json = gd["columns"]
                        cols = (
                            json.loads(cols_json)
                            if cols_json and isinstance(cols_json, str)
                            else (cols_json or [])
                        )
                        for c in cols:
                            all_schema_parts.append(f"{gd['name']}.{c['key']} ({c['type']})")
                    except Exception:
                        pass
                schema_str = (
                    ", ".join(all_schema_parts)
                    if all_schema_parts
                    else "(schema could not be loaded)"
                )
                table_name = group_datasets[0]["name"]
            else:
                if manifest:
                    table_name = manifest["table_name"]
                    schema_lines = []
                    for c in manifest["columns"]:
                        profile = manifest["profiles"].get(c["name"], {})
                        stats = profile.get("stats", {})
                        extra = ""
                        if stats.get("min") is not None and stats.get("max") is not None:
                            extra = f" [{stats['min']}..{stats['max']}]"
                        null_pct = round(profile.get("null_rate", 0) * 100)
                        null_info = f" null:{null_pct}%" if null_pct > 0 else ""
                        schema_lines.append(f"- {c['name']} {c['type']}{extra}{null_info}")
                    schema_str = "\\n".join(schema_lines)
                else:
                    table_name = analytics_svc._get_dataset_meta(db_conn, dataset_id)
                    try:
                        cols_query = db_conn.execute(
                            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ?",
                            [table_name],
                        ).fetchall()
                        schema_lines = [f"- {c[0]} {c[1]}" for c in cols_query]
                    except Exception:
                        schema_lines = ["- (schema could not be loaded)"]
                    schema_str = "\\n".join(schema_lines)
            
                # Get column names for mapping hints
                col_names = []
                if manifest and manifest.get("columns"):
                    col_names = [c["name"] for c in manifest["columns"]]
                elif manifest and manifest.get("profiles"):
                    col_names = list(manifest["profiles"].keys())
            
                sys_prompt = get_system_prompt(table_name, schema_str, col_names)

            few_shot_examples = _get_few_shot_examples(db_conn, dataset_id, result["intent"])

            # Stage: SQL generation
            _check_cancel()
            await _status("Формирую SQL-запрос...")
            result["sql"] = await ollama_client.generate_sql(
                contextual_question,
                sys_prompt,
                table_name,
                schema_str,
                few_shot_examples,
                is_multi_table=is_group,
            )

            if is_group and group_datasets:
                name_map = {gd["name"]: gd["table_name"] for gd in group_datasets}
                result["sql"] = _remap_table_names(result["sql"], name_map)
                result["sql"] = _fix_current_date(result["sql"], group_manifests)
            elif manifest:
                result["sql"] = _fix_current_date(result["sql"], {dataset_id: manifest})

            # Handle explicit clarification requests from the LLM
            if result["sql"] and result["sql"].strip().startswith("CLARIFY:"):
                clarification = result["sql"].replace("CLARIFY:", "").strip()
                result["answer"] = clarification
                result["sql"] = None
                result["data"] = []
                result["chart_type"] = None
                _save_assistant_message(session_id, db_conn, result)
                await ws_send({"type": "result", "data": result})
                return result

            # Stage: SQL execution with progress
            _check_cancel()
            await _status("Выполняю запрос к базе данных...")

            is_valid, error = full_validate(result["sql"])
            if is_valid:
                safe_sql = sanitize_sql(result["sql"])

                # Estimate total rows from manifest for progress
                total_rows = 0
                if manifest and manifest.get("profiles"):
                    for col_profile in manifest["profiles"].values():
                        if col_profile.get("stats", {}).get("count"):
                            total_rows = max(total_rows, col_profile["stats"]["count"])
                if total_rows > 0:
                    await ws_send(
                        {"type": "sql_progress", "rows": 0, "total": int(total_rows)}
                    )

                try:
                    raw_data = db_conn.execute(safe_sql).fetchall()
                    cols = (
                        [desc[0] for desc in db_conn.description]
                        if db_conn.description
                        else []
                    )
                    result["data"] = [dict(zip(cols, row)) for row in raw_data]

                    if total_rows > 0:
                        await ws_send(
                            {
                                "type": "sql_progress",
                                "rows": len(result["data"]),
                                "total": int(total_rows),
                            }
                        )
                except Exception as e:
                    logger.error(f"SQL execution error: {e}")
                    # Try to fix SQL with LLM
                    fixed_sql = await _retry_sql_fix(
                        contextual_question, result["sql"], str(e),
                        sys_prompt, is_group, ollama_client
                    )
                    if fixed_sql:
                        if is_group and group_datasets:
                            name_map = {gd["name"]: gd["table_name"] for gd in group_datasets}
                            fixed_sql = _remap_table_names(fixed_sql, name_map)
                        result["sql"] = fixed_sql
                        is_valid2, error2 = full_validate(fixed_sql)
                        if is_valid2:
                            try:
                                safe_sql2 = sanitize_sql(fixed_sql)
                                raw_data2 = db_conn.execute(safe_sql2).fetchall()
                                cols2 = [desc[0] for desc in db_conn.description] if db_conn.description else []
                                result["data"] = [dict(zip(cols2, row)) for row in raw_data2]
                                logger.info(f"Retry succeeded: {len(result['data'])} rows")
                            except Exception as e2:
                                logger.error(f"Retry also failed: {e2}")
                                result["error"] = str(e2)
                                result["answer"] = "Произошла ошибка при выполнении запроса к базе данных."
                                await ws_send({"type": "error", "message": result["answer"]})
                                return result
                        else:
                            result["error"] = f"Retry validation failed: {error2}"
                            result["answer"] = "Не удалось исправить запрос."
                            await ws_send({"type": "error", "message": result["answer"]})
                            return result
                    else:
                        result["error"] = str(e)
                        result["answer"] = "Произошла ошибка при выполнении запроса к базе данных."
                        await ws_send({"type": "error", "message": result["answer"]})
                        return result
            else:
                # Validation failed — try to fix SQL
                logger.warning(f"SQL validation failed: {error}")
                fixed_sql = await _retry_sql_fix(
                    contextual_question, result["sql"], error,
                    sys_prompt, is_group, ollama_client
                )
                if fixed_sql:
                    if is_group and group_datasets:
                        name_map = {gd["name"]: gd["table_name"] for gd in group_datasets}
                        fixed_sql = _remap_table_names(fixed_sql, name_map)
                    result["sql"] = fixed_sql
                    is_valid2, error2 = full_validate(fixed_sql)
                    if is_valid2:
                        try:
                            safe_sql2 = sanitize_sql(fixed_sql)
                            raw_data2 = db_conn.execute(safe_sql2).fetchall()
                            cols2 = [desc[0] for desc in db_conn.description] if db_conn.description else []
                            result["data"] = [dict(zip(cols2, row)) for row in raw_data2]
                            logger.info(f"Validation retry succeeded: {len(result['data'])} rows")
                        except Exception as e2:
                            logger.error(f"Validation retry execution failed: {e2}")
                            result["error"] = str(e2)
                            result["answer"] = "Не удалось исправить запрос."
                            await ws_send({"type": "error", "message": result["answer"]})
                            return result
                    else:
                        result["error"] = f"Retry validation failed: {error2}"
                        result["answer"] = "Не могу выполнить запрос из-за ограничений безопасности."
                        await ws_send({"type": "error", "message": result["answer"]})
                        return result
                else:
                    result["error"] = f"Generated invalid SQL: {error}"
                    result["answer"] = "Не могу выполнить запрос из-за ограничений безопасности."
                    await ws_send({"type": "error", "message": result["answer"]})
                return result

        # Stage: Chart type
        result["chart_type"] = _detect_chart_type(intent, question, result.get("data"))

        # Stage: Summary generation (STREAMING)
        _check_cancel()
        await _status("Формирую ответ...")

        if result["data"]:
            # Build summary prompt
            row_count = len(result["data"]) if isinstance(result["data"], list) else 0
            data_str = json.dumps(result["data"], ensure_ascii=False, default=str)[:2000]
            if row_count > 0:
                data_str = f"[ВСЕГО ЗАПИСЕЙ: {row_count}]\n{data_str}"
            prompt_template = SUMMARY_PROMPT_EXTENDED if extended else SUMMARY_PROMPT
            prompt = prompt_template.format(question=contextual_question, data=data_str)

            system_msg = (
                "Ты — бизнес-аналитик. Твоя задача — объяснять данные понятным человеческим языком. "
                "Пиши ответ ТОЛЬКО на русском языке (Russian ONLY). "
                "КАТЕГОРИЧЕСКИ ЗАПРЕЩАЕТСЯ выводить китайские иероглифы (NO CHINESE) или английский текст. "
                "Ни в коем случае не выводи SQL-код или JSON-структуры. "
                "Не указывай валюту денежных сумм, если она явно не указана в данных."
            )

            # Inject custom instructions
            try:
                instructions = db_conn.execute(
                    "SELECT category, content FROM assistant_instructions"
                ).fetchall()
                if instructions:
                    custom_rules = "\n\nПОЛЬЗОВАТЕЛЬСКИЕ ИНСТРУКЦИИ:\n"
                    for cat, content in instructions:
                        custom_rules += f"- [{cat.upper()}]: {content}\n"
                    system_msg += custom_rules
            except Exception:
                pass

            # Stream count header first, then LLM tokens
            answer_parts = []
            if row_count > 0:
                count_header = f"Найдено: {row_count} записей.\n"
                answer_parts.append(count_header)
                await ws_send({"type": "token", "text": count_header})

            async for token in ollama_client.generate_stream(prompt, system=system_msg):
                _check_cancel()

                # Check for clarification
                try:
                    clarification = clarification_queue.get_nowait()
                    await ws_send({"type": "ack_clarification"})
                    answer_parts.append(f"\n[Уточнение получено: {clarification}]\n")
                except asyncio.QueueEmpty:
                    pass

                answer_parts.append(token)
                await ws_send({"type": "token", "text": token})

            raw_answer = "".join(answer_parts).strip()
            raw_answer, chart_key = _extract_chart_key(raw_answer)
            result["answer"] = raw_answer
            if chart_key:
                result["chart_value_key"] = chart_key
        else:
            result["answer"] = "По вашему запросу нет данных."

    except asyncio.CancelledError:
        await ws_send({"type": "cancelled"})
        result["answer"] = "Запрос отменён."
        result["processing_time"] = time.time() - start_time
        return result
    except TimeoutError as e:
        result["error"] = str(e)
        result["answer"] = "Модель не успела ответить — запрос занял слишком много времени. Попробуйте ещё раз."
        result["retryable"] = True
        await ws_send({"type": "error", "message": result["answer"], "retryable": True})
    except ConnectionError as e:
        result["error"] = str(e)
        result["answer"] = "Сервис AI временно недоступен. Проверьте запуск Ollama."
        result["retryable"] = True
        await ws_send({"type": "error", "message": result["answer"], "retryable": True})
    except Exception as e:
        logger.exception("Error in streaming process_question")
        result["error"] = str(e)
        result["answer"] = "Произошла непредвиденная ошибка при обработке вашего запроса."
        result["retryable"] = True
        await ws_send({"type": "error", "message": result["answer"], "retryable": True})

    result["processing_time"] = round(time.time() - start_time, 2)
    result["_question"] = question
    _save_assistant_message(session_id, db_conn, result)

    # Cache is handled by _save_to_session() which writes to chat_messages

    await ws_send({"type": "result", "data": result})
    return result
