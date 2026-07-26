from __future__ import annotations
"""Prompt templates for Ollama / QWEN2.5.
Single source of truth for all LLM prompts.
"""
import json, logging, re
from typing import Optional
logger = logging.getLogger(__name__)

INTENT_PROMPT = """Определи тип запроса пользователя. Возможные типы:
- kpi, comparison, top, drop, chart, summary, sql
Верни ТОЛЬКО одно слово. Вопрос: {question}"""

SUMMARY_PROMPT = """Ты эксперт-аналитик продаж. Дай краткий ответ (2-3 предложения).
Правила: только русский язык, форматируй числа (3.04 млрд, 4.5 млн, 150 тыс.),
не упоминай количество записей, в конце добавь [CHART_KEY:имя_колонки].
Данные: {data}
Вопрос: {question}
Ответ:"""

SUMMARY_PROMPT_EXTENDED = """Ты эксперт-аналитик продаж. Развёрнутый ответ: тренды, аномалии, рекомендации.
Правила: только русский язык, форматируй числа, не упоминай количество записей,
в конце добавь [CHART_KEY:имя_колонки].
Данные: {data}
Вопрос: {question}
Ответ:"""

_COUNTRY_DICT = ("швеция=sweden, франция=france, швейцария=switzerland, германия=germany, "
    "италия=italy, испания=spain, япония=japan, китай=china, сша=usa, россия=russia")

def get_system_prompt(table_name: str, schema_str: str, column_names: list[str] | None = None) -> str:
    from app.services.column_dict import get_column_mapping_hint
    hint_block = ""
    if column_names:
        hint = get_column_mapping_hint(column_names)
        if hint:
            hint_block = f"\nМАППИНГ КОЛОНОК (EN ↔ RU):\n{hint}\n"
    return f"""Ты — локальный аналитик данных. Таблица {table_name} в DuckDB.
Схема: {schema_str}
{hint_block}
Правила: только SELECT, LIMIT 100 (500 для "все"), ROUND() для чисел,
LOWER() для текстового поиска, TRY_CAST для дат VARCHAR,
TO_TIMESTAMP только для BIGINT unix-timestamps.
Страны: {_COUNTRY_DICT}
НИКОГДА не используй CURRENT_DATE (данные исторические).
Если не можешь составить SQL — начни ответ с CLARIFY:"""

def get_multi_table_system_prompt(tables_info: list[dict], relationships: list[dict], manifests: dict | None = None) -> str:
    from app.services.column_dict import get_column_mapping_hint
    table_schemas: dict[str, dict] = {}
    used_aliases: set[str] = set()
    for t in tables_info:
        orig, duck = t["name"], t["table_name"]
        alias = orig[:2]
        counter = 1
        while alias in used_aliases:
            alias = orig[:1] + str(counter); counter += 1
        used_aliases.add(alias)
        try:
            cols = json.loads(t["columns"]) if t["columns"] else []
            table_schemas[orig] = {"alias": alias, "duck": duck, "cols": [c["key"] for c in cols]}
        except Exception:
            pass
    schema_block = "\n".join(f"{info['alias']} ({orig}): {', '.join(info['cols'])}" for orig, info in table_schemas.items())
    join_block = " AND ".join(
        f"{table_schemas.get(r['from_table'],{}).get('alias',r['from_table'][:2])}.{r['from_col']} = "
        f"{table_schemas.get(r['to_table'],{}).get('alias',r['to_table'][:2])}.{r['to_col']}" for r in relationships
    ) or "(нет связей)"
    mapping_block = "\n".join(f"FROM {orig} {info['alias']}" for orig, info in table_schemas.items())
    sample_block = ""
    if manifests:
        lines = []
        for orig, info in table_schemas.items():
            ds_id = next((t.get("id") for t in tables_info if t["name"] == orig), None)
            if ds_id and ds_id in manifests:
                for col, profile in manifests[ds_id].get("profiles", {}).items():
                    samples = profile.get("sample_values", [])
                    if samples and profile.get("distinct_count", 0) <= 50:
                        lines.append(f"{orig}.{col}: {', '.join(str(s) for s in samples[:10])}")
        if lines:
            sample_block = "\nПРИМЕРЫ ЗНАЧЕНИЙ:\n" + "\n".join(lines)
    all_cols = [c for info in table_schemas.values() for c in info.get("cols", [])]
    hint_block = ""
    if all_cols:
        hint = get_column_mapping_hint(all_cols)
        if hint:
            hint_block = f"\nМАППИНГ КОЛОНОК:\n{hint}\n"
    logger.info(f"Multi-table prompt: {len(tables_info)} tables, {len(relationships)} rels")
    return f"""Ты — аналитик данных. Несколько таблиц в DuckDB.
ТАБЛИЦЫ: {schema_block}
СВЯЗИ: {join_block}
МАППИНГ FROM: {mapping_block}
{sample_block}{hint_block}
Правила: SELECT LIMIT 100, указывай алиас таблицы, только объявленные колонки.
НИКОГДА не используй CURRENT_DATE. Если не можешь — начни с CLARIFY:"""

def get_sql_prompt(table_name: str, column_names: str, question: str, examples: list[dict] | None = None, is_multi_table: bool = False) -> str:
    ex_block = ""
    if examples:
        lines = [f"Пример {i}:\nВопрос: {e.get('answer','')[:80]}\nSQL: {e['sql']}" for i, e in enumerate(examples, 1) if e.get("sql")]
        if lines:
            ex_block = "\nПРИМЕРЫ:\n" + "\n\n".join(lines) + "\n"
    common = f"""- SELECT только, LIMIT 100 (500 для "все")
- ROUND() для чисел, DATE_TRUNC для группировки
- LOWER(col) LIKE '%term%' для текста
- TO_TIMESTAMP(col) для BIGINT дат, TRY_CAST(col AS DATE) для VARCHAR дат
- CTE: WITH top AS (SELECT ... LIMIT 1) SELECT ... WHERE col=(SELECT col FROM top)
- Для стран: швеция=sweden, франция=france, германия=germany, китай=china, сша=usa
{ex_block}
Вопрос: {question}
Верни ТОЛЬКО SQL без объяснений."""
    if is_multi_table:
        return f"Сгенерируй SQL. Таблицы описаны в системном промпте.\n{common}"
    return f"Сгенерируй SQL для таблицы {table_name}.\nКолонки: {column_names}\n{common}"
