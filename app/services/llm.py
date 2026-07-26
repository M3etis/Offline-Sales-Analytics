from __future__ import annotations
import httpx
import json
import logging
import time
import re
from typing import Any, Optional
from datetime import date, timedelta

from app.core.config import settings
from app.utils.sql_safety import full_validate, sanitize_sql
from app.services import analytics as analytics_svc
from app.services.preanalysis import load_manifest
from app.services.column_dict import COLUMN_TRANSLATIONS, get_column_mapping_hint

# ── Domain re-exports (backward-compatible) ───────────────────────────────────
# streaming.py and other callers import these names directly from this module.
from app.domain.ai.intent_classifier import classify_intent_fast  # noqa: F401
from app.domain.ai.sql_utils import (
    extract_sql       as _extract_sql,        # noqa: F401
    adjust_limit      as _adjust_limit,
    fix_cte_errors    as _fix_cte_errors,
    fix_current_date  as _fix_current_date,   # noqa: F401
    fix_filter_values as _fix_filter_values,
    remap_table_names as _remap_table_names,  # noqa: F401
    MAX_SQL_RETRIES,
)
from app.domain.ai.response_builder import (
    inject_count_header  as _inject_count_header,   # noqa: F401
    extract_chart_key    as _extract_chart_key,     # noqa: F401
    detect_chart_type    as _detect_chart_type,     # noqa: F401
)
from app.shared.prompts.templates import (
    INTENT_PROMPT,             # noqa: F401
    SUMMARY_PROMPT,            # noqa: F401
    SUMMARY_PROMPT_EXTENDED,   # noqa: F401
    get_system_prompt,         # noqa: F401
    get_multi_table_system_prompt,  # noqa: F401
    get_sql_prompt,            # noqa: F401
)

logger = logging.getLogger(__name__)

_CHART_KEY_RE = re.compile(r'\[CHART_KEY:(\w+)\]')



def _extract_chart_key(answer: str) -> tuple[str, str | None]:
    """Extract [CHART_KEY:col] tag from answer and strip it. Returns (cleaned_answer, chart_key)."""
    m = _CHART_KEY_RE.search(answer)
    if m:
        return _CHART_KEY_RE.sub('', answer).strip(), m.group(1)
    return answer, None


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


def _inject_count_header(answer: str, data: Any) -> str:
    """Prepend an exact record count header derived from len(data).

    This is the single source of truth for record counts in answers.
    The LLM is instructed to never mention counts — the header carries them.
    """
    if not isinstance(data, list) or len(data) == 0:
        return answer
    count = len(data)
    header = f"Найдено: {count} записей.\n"
    if answer.startswith(header):
        return answer
    return header + answer


def _remap_table_names(sql: str, name_map: dict[str, str]) -> str:
    """Replace original table names with DuckDB dataset_xxx names in SQL.
    
    Only replaces in FROM/JOIN clauses (not in column aliases or expressions).
    Sorts by length descending to avoid partial replacements.
    """
    if not name_map:
        return sql
    
    # Sort by original name length descending to avoid partial replacements
    sorted_names = sorted(name_map.keys(), key=len, reverse=True)
    for orig_name in sorted_names:
        duck_name = name_map[orig_name]
        # Replace only in FROM/JOIN context: FROM table_name or JOIN table_name
        # This avoids replacing column aliases like "as price_change"
        sql = re.sub(
            r'((?:FROM|JOIN)\s+)' + re.escape(orig_name) + r'(\s+)',
            r'\g<1>' + duck_name + r'\2',
            sql,
            flags=re.IGNORECASE
        )
    
    return sql

def get_system_prompt(table_name: str, schema_str: str, column_names: list[str] = None) -> str:
    column_mapping_hint = ""
    if column_names:
        hint = get_column_mapping_hint(column_names)
        if hint:
            column_mapping_hint = f"\nМАППИНГ КОЛОНОК (EN ↔ RU):\n{hint}\n"
    
    return f"""Ты — локальный аналитик данных. Ты работаешь только с данными из таблицы {table_name} в базе DuckDB.

Схема таблицы {table_name}:
{schema_str}
{column_mapping_hint}
Правила:
1. Генерируй только SELECT запросы к таблице {table_name}
2. НИКОГДА не используй DROP, DELETE, UPDATE, INSERT, ALTER, CREATE
3. Добавляй LIMIT: по умолчанию 100. Если пользователь просит "все", "полный список", "всё" — используй LIMIT 500. Если просит конкретное число (топ 5, 10 товаров) — используй это число.
4. Используй ROUND() для числовых значений
5. Отвечай кратко на русском языке
6. Не придумывай данные — используй только результаты запросов
7. Для агрегации по времени используй DATE_TRUNC
8. Для поиска и фильтрации по текстовым полям ОБЯЗАТЕЛЬНО используй LOWER(): WHERE LOWER(column) = LOWER('значение') или WHERE LOWER(column) LIKE '%слово%'
9. ОЧЕНЬ ВАЖНО ДЛЯ ДАТ: В DuckDB строковые колонки с датой (VARCHAR) конвертируй ТОЛЬКО через TRY_CAST(col AS DATE) или TRY_CAST(col AS TIMESTAMP). НИКОГДА не используй TO_TIMESTAMP для строк! TO_TIMESTAMP используй ТОЛЬКО если колонка содержит UNIX timestamp (целое число вида 1621217801).
10. При запросах о странах — пользователь может писать на русском или английском. Всегда используй LOWER() для сравнения. Если пользователь написал "Швеция" — ищи 'sweden', если "Франция" — ищи 'france', если "switzerland" — ищи 'switzerland'. Справочник: швеция=sweden, франция=france, швейцария=switzerland, германия=germany, италия=italy, испания=spain, португалия=portugal, япония=japan, корея=korea, китай=china, сша=usa/сша, россия=russia, турция=turkey, индия=india, бразилия=brazil, египет=egypt, австралия=australia, канада=canada, великобритания=uk/great britain, польша=poland, нидерланды=netherlands, бельгия=belgium, австрия=austria, швейцария=switzerland, норвегия=norway, дания=denmark, финляндия=finland, чехия=czech, венгрия=hungary, греция=greece, мексика=mexico, аргентина=argentina, южная_корея=south korea, таиланд=thailand, вьетнам=vietnam, индонезия=indonesia, малайзия=malaysia, сингапур=singapore, оаэ=uae, саудовская_аравия=saudi arabia, израиль=israel
11. ДАТЫ (БД содержит ИСТОРИЧЕСКИЕ данные, НИКОГДА не используй CURRENT_DATE!):
  - Если date_col — UNIX timestamp (BIGINT): ВСЕГДА оборачивай в TO_TIMESTAMP(date_col)!
    - За месяц: SELECT DATE_TRUNC('month', TO_TIMESTAMP(date_col)) as month, SUM(revenue) FROM {table_name} GROUP BY month ORDER BY month DESC LIMIT 1
    - По месяцам: SELECT DATE_TRUNC('month', TO_TIMESTAMP(date_col)) AS month, SUM(revenue) FROM {table_name} GROUP BY month ORDER BY month
    - Период: WHERE TO_TIMESTAMP(date_col) >= (SELECT TO_TIMESTAMP(MAX(date_col)) - INTERVAL '30 DAY' FROM {table_name})
    - НИКОГДА не пиши: date_col - INTERVAL (BIGINT - INTERVAL не работает!)
  - Если date_col — строка (VARCHAR): используй TRY_CAST(col AS DATE)

МНОГОШАГОВЫЕ ЗАПРОСЫ: для "у самого", "среди тех кто" — используй CTE: WITH top AS (SELECT ... ORDER BY ... LIMIT 1) SELECT ... WHERE col = (SELECT col FROM top)
- ABC-анализ: WITH ranked AS (SELECT product, SUM(revenue) AS rev, SUM(SUM(revenue)) OVER () AS total FROM {table_name} GROUP BY product) SELECT product, ROUND(rev, 2), ROUND(rev * 100.0 / total, 1) AS pct FROM ranked ORDER BY rev DESC
- Парето 80/20: WHERE cum <= total * 0.8 (с оконной функцией SUM OVER)
- RFM: DATEDIFF для recency, COUNT(DISTINCT) для frequency, SUM для monetary

12. ОТСУТСТВИЕ КОНТЕКСТА: Если запрос бессмысленный или абстрактный (например, "TOP N" без указания чего именно) и ты НЕ МОЖЕШЬ составить SQL, начни свой ответ строго со слова CLARIFY: и напиши уточняющий вопрос к пользователю.
"""


def get_multi_table_system_prompt(tables_info: list[dict], relationships: list[dict], manifests: dict = None) -> str:
    """Build system prompt for multi-table group with relationships."""
    table_map = {}
    table_schemas = {}
    
    # Generate unique aliases
    used_aliases = set()
    for t in tables_info:
        orig_name = t['name']
        duck_name = t['table_name']
        table_map[orig_name] = duck_name
        
        # Generate 2-letter alias, try first 2 chars, then variations
        base_alias = orig_name[:2]
        alias = base_alias
        counter = 1
        while alias in used_aliases:
            alias = orig_name[:1] + str(counter)
            counter += 1
        used_aliases.add(alias)
        
        try:
            cols = json.loads(t["columns"]) if t["columns"] else []
            col_names = [c['key'] for c in cols]
            table_schemas[orig_name] = {'alias': alias, 'duck': duck_name, 'cols': col_names}
        except Exception:
            pass

    # Build concise schema: one line per table with all columns
    schema_lines = []
    for orig, info in table_schemas.items():
        schema_lines.append(f"{info['alias']} ({orig}): {', '.join(info['cols'])}")
    schema_block = "\n".join(schema_lines)

    # Build sample values block from manifests
    sample_block = ""
    if manifests:
        sample_lines = []
        for orig, info in table_schemas.items():
            dataset_id = None
            for t in tables_info:
                if t['name'] == orig:
                    dataset_id = t.get('id')
                    break
            if dataset_id and dataset_id in manifests:
                m = manifests[dataset_id]
                for col_name, profile in m.get('profiles', {}).items():
                    samples = profile.get('sample_values', [])
                    if samples and len(samples) > 0:
                        # Include text columns with discrete values (categories, names, etc.)
                        if profile.get('distinct_count', 0) <= 50 and samples:
                            sample_lines.append(f"{orig}.{col_name}: {', '.join(str(s) for s in samples[:10])}")
        if sample_lines:
            sample_block = "\n\nПРИМЕРЫ ЗНАЧЕНИЙ (для фильтрации, используй ТОЧНЫЕ значения из списка):\n" + "\n".join(sample_lines)

    # Build JOIN rules from relationships
    join_lines = []
    for r in relationships:
        from_info = table_schemas.get(r['from_table'], {})
        to_info = table_schemas.get(r['to_table'], {})
        from_alias = from_info.get('alias', r['from_table'][:2])
        to_alias = to_info.get('alias', r['to_table'][:2])
        join_lines.append(f"{from_alias}.{r['from_col']} = {to_alias}.{r['to_col']}")
    join_block = " AND ".join(join_lines) if join_lines else "(нет связей)"

    # Build mapping for SQL — show how to write FROM clauses
    mapping_lines = [f"FROM {orig} {info['alias']}" for orig, info in table_schemas.items()]
    mapping_block = "\n".join(mapping_lines)

    # Build dynamic examples from relationships
    examples_block = _build_join_examples(table_schemas, relationships)

    # Build column mapping hints for all tables
    all_column_names = []
    for orig, info in table_schemas.items():
        all_column_names.extend(info.get('cols', []))
    column_mapping_hint = ""
    if all_column_names:
        hint = get_column_mapping_hint(all_column_names)
        if hint:
            column_mapping_hint = f"\nМАППИНГ КОЛОНОК (EN ↔ RU):\n{hint}\n"

    prompt = f"""Ты — аналитик данных. У тебя есть доступ к нескольким связанным таблицам в DuckDB.

КРИТИЧЕСКИ ВАЖНО: Используй ТОЛЬКО колонки из списка ниже. НЕ ПРИДУМЫВАЙ колонки (нет 'amount', 'quantity', 'price', 'manager' — если их нет в списке).
Если нужно вычислить выручку — используй существующие числовые колонки (например product_count * product_price).

ТАБЛИЦЫ и КОЛОНКИ:
{schema_block}

СВЯЗИ МЕЖДУ ТАБЛИЦАМИ (для JOIN):
{join_block}

МАППИНГ ИМЁН (для FROM) — пиши ТАК в SQL:
{mapping_block}
{sample_block}
{column_mapping_hint}

ПРАВИЛА:
1. Только SELECT. LIMIT 100. Для "все/всё" — LIMIT 500.
2. В FROM/JOIN используй ПОЛНОЕ имя таблицы с алиасом: FROM price_change p1 (не просто p1!)
3. Колонка принадлежит ТОЛЬКО своей таблице — указывай алиас.
4. Для JOIN: FROM t1 JOIN t2 ON t1.col = t2.col
5. НИКОГДА не придумывай колонки. Используй ТОЛЬКО из списка ТАБЛИЦЫ и КОЛОНКИ выше.
6. Если нужна выручка/сумма — ищи числовые колонки (count, price, amount, product_count, product_price) и перемножай.
7. Для поиска по тексту: LOWER(col) LIKE '%term%'
8. ОЧЕНЬ ВАЖНО ДЛЯ ДАТ: В DuckDB строковые колонки с датой (VARCHAR) конвертируй ТОЛЬКО через TRY_CAST(col AS DATE). НИКОГДА не используй TO_TIMESTAMP для строк! TO_TIMESTAMP(col) используй ТОЛЬКО для UNIX timestamp (целое число).
9. Для CTE: WITH cte AS (SELECT ...) SELECT ... WHERE col = (SELECT col FROM cte)
10. ДАТЫ (БД содержит ИСТОРИЧЕСКИЕ данные, НИКОГДА не используй CURRENT_DATE!):
  - Если date_col — UNIX timestamp (BIGINT): ВСЕГДА оборачивай в TO_TIMESTAMP(date_col) перед любыми операциями с датами!
    - НИКОГДА не пиши: date_col - INTERVAL (BIGINT - INTERVAL не работает!)
  - Если date_col — строка (VARCHAR): используй TRY_CAST(col AS DATE)
  - Пример для "выручка за последний месяц" (UNIX timestamp):
    WITH bounds AS (
      SELECT DATE_TRUNC('month', TO_TIMESTAMP(MAX(date_col))) as month_start,
             DATE_TRUNC('month', TO_TIMESTAMP(MAX(date_col))) + INTERVAL '1 MONTH' as month_end
      FROM table_with_date
    )
    SELECT SUM(qty * price) as revenue
    FROM fact_table f
    JOIN table_with_date d ON f.id = d.id
    WHERE TO_TIMESTAMP(d.date_col) >= (SELECT month_start FROM bounds)
      AND TO_TIMESTAMP(d.date_col) < (SELECT month_end FROM bounds)
  - В CTE всегда указывай FROM с нужной таблицей, не ссылайся на алиасы из основного запроса

ВАЖНО: Сначала определи по схеме какие таблицы содержат данные продаж (факты), а какие — справочники (измерения). Таблицы фактов обычно содержат даты, количества, суммы. Таблицы измерений содержат названия, категории, адреса. Соединяй их через связи из промпта.

11. ОТСУТСТВИЕ КОНТЕКСТА: Если запрос бессмысленный или абстрактный (например, "TOP N" без указания чего именно) и ты НЕ МОЖЕШЬ составить SQL, начни свой ответ строго со слова CLARIFY: и напиши уточняющий вопрос к пользователю.

{examples_block}
"""
    logger.info(f"Multi-table prompt built: {len(tables_info)} tables, {len(relationships)} relationships")
    return prompt


def _build_join_examples(table_schemas: dict, relationships: list[dict]) -> str:
    """Dynamically build JOIN examples based on actual relationships.
    
    Finds the longest chain of connected tables, preferring chains
    that include tables with numeric columns for aggregation.
    """
    if not relationships or not table_schemas:
        return ""
    
    # Build adjacency graph from relationships
    graph = {}
    reverse_graph = {}
    for r in relationships:
        ft, tt = r['from_table'], r['to_table']
        if ft not in graph:
            graph[ft] = []
        graph[ft].append((tt, r['from_col'], r['to_col']))
        if tt not in reverse_graph:
            reverse_graph[tt] = []
        reverse_graph[tt].append((ft, r['to_col'], r['from_col']))
    
    # Find all possible paths using DFS (limit depth for readability)
    def find_paths(start, max_depth=6):
        """Find all paths from start table."""
        paths = []
        
        def dfs(node, path, edges, direction):
            if len(path) > 1:
                paths.append((path[:], edges[:], direction))
            if len(path) >= max_depth:
                return
            
            # Try forward direction
            if node in graph:
                for next_node, from_col, to_col in graph[node]:
                    if next_node not in path:
                        dfs(next_node, path + [next_node],
                            edges + [(node, from_col, next_node, to_col)],
                            'forward')
            
            # Try reverse direction
            if node in reverse_graph:
                for next_node, from_col, to_col in reverse_graph[node]:
                    if next_node not in path:
                        dfs(next_node, path + [next_node],
                            edges + [(node, from_col, next_node, to_col)],
                            'reverse')
        
        dfs(start, [start], [], None)
        return paths
    
    # Collect all paths from all tables
    all_paths = []
    for table in table_schemas:
        for path, edges, direction in find_paths(table):
            all_paths.append((path, edges))
    
    if not all_paths:
        return ""
    
    # Score paths
    def score_path(path_info):
        path, edges = path_info
        
        # Find numeric column in any table
        num_col = None
        num_table = None
        for t in path:
            if t in table_schemas:
                nc = _find_numeric_column(table_schemas[t]['cols'])
                if nc:
                    num_col = nc
                    num_table = t
                    break
        
        # Find name column in first table
        name_col = _find_name_column(table_schemas[path[0]]['cols']) if path[0] in table_schemas else None
        
        score = 0
        if num_col:
            score += 100
            # Bonus if numeric column is in last table (natural fact table)
            if num_table == path[-1]:
                score += 30
        if name_col:
            score += 50
        # Strongly prefer longer chains - they demonstrate more JOIN patterns
        score += len(path) * 25
        
        return score, num_col, name_col
    
    # Sort by score descending
    all_paths.sort(key=score_path, reverse=True)
    
    # Use the best path
    best_path, best_edges = all_paths[0]
    _, num_col, name_col = score_path(all_paths[0])
    
    if not num_col or not name_col or len(best_path) < 2:
        return ""
    
    # Find which table has the numeric column
    num_table = None
    for t in best_path:
        if t in table_schemas and _find_numeric_column(table_schemas[t]['cols']):
            num_table = t
            break
    
    table_count = len(best_path)
    
    # Build aliases
    aliases = {}
    for t in best_path:
        if t in table_schemas:
            aliases[t] = table_schemas[t]['alias']
    
    # SELECT clause
    first_alias = aliases[best_path[0]]
    num_alias = aliases.get(num_table, first_alias)
    select_clause = f"SELECT {first_alias}.{name_col}, SUM({num_alias}.{num_col}) as total"
    
    # FROM + JOIN clauses
    from_clause = f"FROM {best_path[0]} {aliases[best_path[0]]}"
    join_clauses = []
    for from_t, from_col, to_t, to_col in best_edges:
        a_from = aliases.get(from_t, from_t[:2])
        a_to = aliases.get(to_t, to_t[:2])
        join_clauses.append(f"JOIN {to_t} {a_to} ON {a_from}.{from_col} = {a_to}.{to_col}")
    
    sql = f"""{select_clause}
{from_clause}
{chr(10).join(join_clauses)}
GROUP BY {first_alias}.{name_col}
ORDER BY total DESC
LIMIT 100"""
    
    return f"""
ПРИМЕР JOIN ({table_count} таблиц):
{sql}"""


def _find_name_column(cols: list[str]) -> str:
    """Find a name-like column for display."""
    for c in cols:
        cl = c.lower()
        if any(w in cl for w in ['name', 'название', 'наименование', 'title', 'имя']):
            return c
    # Fallback: first string-looking column
    for c in cols:
        if not any(w in c.lower() for w in ['id', 'date', 'time', 'count', 'sum', 'price', 'amount']):
            return c
    return cols[0] if cols else None


def _find_numeric_column(cols: list[str]) -> str:
    """Find a numeric column for aggregation."""
    # Priority: explicit numeric column names
    for c in cols:
        cl = c.lower()
        if any(w in cl for w in ['count', 'quantity', 'qty', 'amount', 'total', 'price', 'revenue', 'profit', 'sum', 'value', 'cost', 'stock', 'balance']):
            return c
    # Skip id, text, date columns
    for c in cols:
        cl = c.lower()
        if not any(w in cl for w in ['id', 'name', 'название', 'title', 'description', 'text', 'email', 'phone', 
                                      'address', 'city', 'country', 'status', 'type', 'code',
                                      'date', 'time', 'timestamp', 'created', 'updated', 'deleted']):
            return c
    return None

INTENT_PROMPT = """Определи тип запроса пользователя. Возможные типы:
- kpi: общий запрос о ключевых показателях БЕЗ указания периода (например: "какие показатели", "общиe KPI")
- comparison: СРАВНЕНИЕ двух периодов друг с другом (например: "сравни январь и февраль", "динамика месяц к месяцу")
- top: топ товаров, категорий, менеджеров, магазинов, регионов, поставщиков, клиентов
- drop: поиск просадок, падений, слабых мест, аномалий, убытков, проблемных зон
- chart: запрос на построение графика, диаграммы, визуализации, тренда
- summary: общая сводка, обзор данных, итоги, резюме
- sql: произвольный аналитический запрос, включая запросы с указанием периода ("выручка за прошлый год", "прибыль за 2024", "средний чек за месяц", ABC-анализ, возвраты, остатки)

Верни ТОЛЬКО одно слово — тип запроса. Без объяснений.

Вопрос: {question}"""

def get_sql_prompt(table_name: str, column_names: str, question: str, examples: list[dict] = None, is_multi_table: bool = False) -> str:
    examples_block = ""
    if examples:
        examples_lines = []
        for i, ex in enumerate(examples, 1):
            if ex.get("sql"):
                examples_lines.append(f"Пример {i}:\nВопрос: {ex.get('answer', '')[:80]}...\nSQL: {ex['sql']}")
        if examples_lines:
            examples_block = "\n\nПРИМЕРЫ УСПЕШНЫХ ЗАПРОСОВ:\n" + "\n\n".join(examples_lines) + "\n"

    if is_multi_table:
        return f"""Сгенерируй SQL SELECT запрос по вопросу пользователя.

Все таблицы, связи и примеры JOIN'ов описаны в системном промпте. Используй их.
{examples_block}
Правила:
- Только SELECT, LIMIT 100
- Используй ROUND() для чисел
- DATE_TRUNC для группировки по времени
- Для текстового поиска: LOWER(col) LIKE LOWER('%term%')
- Определи какие таблицы нужны → соедини по связям из системного промпта
- Если нужна промежуточная таблица — JOIN'и через неё
- Для запросов "топ", "самые продаваемые", "рейтинг" — ORDER BY + LIMIT
- Для "X с самой большой/наименьшей Y" — сначала WHERE (фильтр по X), потом ORDER BY Y DESC/ASC LIMIT 1. НЕ используй подзапрос с MAX/MIN по всей таблице!
  Пример: "браслет с самой большой наценкой" → SELECT name, markup FROM t WHERE LOWER(name) LIKE '%браслет%' ORDER BY markup DESC LIMIT 1
- Для запросов "сколько", "количество" — COUNT() или SUM(quantity)
- Для запросов "средний чек" — AVG() по сумме продаж
- Для запросов "динамика", "по месяцам" — GROUP BY DATE_TRUNC + ORDER BY
- Для запросов "сравнение" — используй CASE WHEN или два подзапроса
- Для запросов "доля", "процент" — подзапрос с SUM для общего итога
- Если колонка даты — UNIX timestamp (целое число) — конвертируй: TO_TIMESTAMP(date_col)
- Для фильтрации по тексту ОБЯЗАТЕЛЬНО используй LOWER(): WHERE LOWER(col) = LOWER('value')
- При запросах о странах: швеция=sweden, франция=france, швейцария=switzerland, германия=germany, италия=italy, испания=spain, япония=japan, корея=korea, китай=china, сша=usa, россия=russia
- Для многошаговых вопросов ("у самого", "среди тех кто", "который больше всего") используй CTE: WITH top AS (SELECT ... ORDER BY ... LIMIT 1) SELECT ... WHERE col = (SELECT col FROM top)
- ДАТЫ — ПРОСТО (не усложняй!):
  - "за прошлый год": WHERE EXTRACT(YEAR FROM date_col) = EXTRACT(YEAR FROM CURRENT_DATE) - 1
  - "за этот год": WHERE EXTRACT(YEAR FROM date_col) = EXTRACT(YEAR FROM CURRENT_DATE)
  - "за прошлый месяц": WHERE DATE_TRUNC('month', date_col) = DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1' MONTH)
  - "за последние 30 дней": WHERE date_col >= CURRENT_DATE - INTERVAL '30' DAY
  - НЕ используй TO_TIMESTAMP(CAST(EXTRACT(EPOCH FROM ...)))

Вопрос: {question}

Верни ТОЛЬКО SQL запрос без объяснений и без ```."""
    else:
        return f"""Сгенерируй SQL SELECT запрос для таблицы {table_name} по вопросу пользователя.

Доступные колонки: {column_names}
{examples_block}
Правила:
- Только SELECT
- Используй ROUND() для чисел
- Добавь LIMIT 100
- Используй DATE_TRUNC для группировки по времени
- Для DuckDB синтаксис
- Для запросов "топ", "самые продаваемые", "рейтинг" — ORDER BY + LIMIT
- Для "X с самой большой/наименьшей Y" — сначала WHERE (фильтр по X), потом ORDER BY Y DESC/ASC LIMIT 1. НЕ используй подзапрос с MAX/MIN по всей таблице!
- Для запросов "сколько", "количество" — COUNT() или SUM(quantity)
- Для запросов "средний чек" — AVG() по сумме продаж
- Для запросов "динамика", "по месяцам" — GROUP BY DATE_TRUNC + ORDER BY
- Для запросов "сравнение" — используй CASE WHEN или два подзапроса
- Для запросов "доля", "процент" — подзапрос с SUM для общего итога
- Если колонка даты — UNIX timestamp (целое число) — конвертируй: TO_TIMESTAMP(date_col)
- Для фильтрации по тексту ОБЯЗАТЕЛЬНО используй LOWER(): WHERE LOWER(col) = LOWER('value')
- При запросах о странах: швеция=sweden, франция=france, швейцария=switzerland, германия=germany, италия=italy, испания=spain, япония=japan, корея=korea, китай=china, сша=usa, россия=russia
- Для многошаговых вопросов ("у самого", "среди тех кто", "который больше всего") используй CTE: WITH top AS (SELECT ... ORDER BY ... LIMIT 1) SELECT ... WHERE col = (SELECT col FROM top)
- ДАТЫ — ПРОСТО (не усложняй!):
  - "за прошлый год": WHERE EXTRACT(YEAR FROM date_col) = EXTRACT(YEAR FROM CURRENT_DATE) - 1
  - "за этот год": WHERE EXTRACT(YEAR FROM date_col) = EXTRACT(YEAR FROM CURRENT_DATE)
  - "за прошлый месяц": WHERE DATE_TRUNC('month', date_col) = DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1' MONTH)
  - "за последние 30 дней": WHERE date_col >= CURRENT_DATE - INTERVAL '30' DAY
  - НЕ используй TO_TIMESTAMP(CAST(EXTRACT(EPOCH FROM ...)))

Вопрос: {question}

Верни ТОЛЬКО SQL запрос без объяснений и без ```."""

SUMMARY_PROMPT = """Ты эксперт-аналитик продаж розничных сетей. Дай краткий ответ (2-3 предложения).
Правила:
- Отвечай ТОЛЬКО на русском языке
- Форматируй числа: 3.04 млрд (3041454711), 4.5 млн (4500000), 150 тыс. (150000)
- Не используй китайские иероглифы
- Используй бизнес-терминологию: выручка, маржа, средний чек, конверсия, товарооборот
- Указывай тренды (рост/снижение) если данные позволяют
- Отвечай по существу, без оговорок и дисклеймеров о полноте данных
- НИКОГДА не упоминай количество записей/товаров/позиций в данных (это будет добавлено отдельно)
- Выводи числа как есть, БЕЗ указания валюты, если она явно не указана в базе
- Данные УЖЕ отфильтрованы SQL-запросом. Если в вопросе указан фильтр (страна, категория) — эти данные УЖЕ соответствуют фильтру
- При расчёте процентов используй ТОЛЬКО выручку/сумму. НЕ путай количество транзакций с процентом
- В САМОМ КОНЦЕ ответа добавь строку: [CHART_KEY:имя_колонки] — числовую колонку из данных, которую ты использовал для анализа (например [CHART_KEY:total_sales_amount])

Данные: {data}
Вопрос: {question}
Ответ:"""

SUMMARY_PROMPT_EXTENDED = """Ты эксперт-аналитик продаж розничных сетей. Дай развернутый ответ: тренды, аномалии, выводы для бизнеса.
Правила:
- Отвечай ТОЛЬКО на русском языке
- Форматируй числа: 3.04 млрд (3041454711), 4.5 млн (4500000), 150 тыс. (150000)
- Не используй китайские иероглифы
- Используй бизнес-терминологию: выручка, маржа, средний чек, конверсия, товарооборот, ROMI, LTV
- Отвечай по существу, без оговорок и дисклеймеров о полноте данных
- Выделяй ключевые метрики и их изменения
- Если есть аномалии (резкие скачки/падения) — объясни возможные причины
- Давай конкретные рекомендации для бизнеса
- НИКОГДА не упоминай количество записей/товаров/позиций в данных (это будет добавлено отдельно)
- Выводи числа как есть, БЕЗ указания валюты, если она явно не указана в базе
- Данные УЖЕ отфильтрованы SQL-запросом. Если в вопросе указан фильтр (страна, категория) — эти данные УЖЕ соответствуют фильтру
- При расчёте процентов используй ТОЛЬКО выручку/сумму. НЕ путай количество транзакций с процентом
- В САМОМ КОНЦЕ ответа добавь строку: [CHART_KEY:имя_колонки] — числовую колонку из данных, которую ты использовал для анализа (например [CHART_KEY:total_sales_amount])

Данные: {data}
Вопрос: {question}
Ответ:"""


def _extract_sql(raw: str) -> str:
    """Extract pure SQL from LLM response that may contain explanatory text."""
    if not raw:
        return ""

    raw = raw.strip()

    # Try to find SQL inside ```sql ... ``` or ``` ... ``` blocks
    code_block = re.search(r'```(?:sql)?\s*\n?(.*?)```', raw, re.DOTALL | re.IGNORECASE)
    if code_block:
        sql = code_block.group(1).strip()
        if sql.upper().startswith(('SELECT', 'WITH')):
            return sql

    # Try to find a standalone SELECT/WITH statement
    # Match from SELECT/WITH to the end, stopping at blank lines followed by non-SQL text
    sql_match = re.search(r'((?:SELECT|WITH)\b.*?)(?:\n{2,}(?![\s\S]*?(?:SELECT|FROM|WHERE|JOIN|GROUP|ORDER|LIMIT|HAVING|UNION|AND|OR|ON|AS|INTO|VALUES|SET|CASE|WHEN|THEN|ELSE|END|CAST|COALESCE|ROUND|SUM|AVG|COUNT|MAX|MIN|DATE_TRUNC|TRY_CAST|LOWER|ROW_NUMBER|RANK|OVER|PARTITION|NULLIF)\b).*|$)', raw, re.DOTALL | re.IGNORECASE)
    if sql_match:
        sql = sql_match.group(1).strip()
        # Remove trailing backticks if any
        sql = sql.rstrip('`').strip()
        if sql.upper().startswith(('SELECT', 'WITH')):
            return sql

    # Last resort: strip common prefixes and return
    cleaned = raw.strip().strip('`').strip()
    if cleaned.lower().startswith('sql'):
        cleaned = cleaned[3:].strip()
    return cleaned


_ALL_KEYWORDS = re.compile(r'\b(все|всё|всех|весь|вся|полный\s+список|полностью|всего)\b', re.IGNORECASE)


def _adjust_limit(question: str, sql: str) -> str:
    """If the user asks for 'all' items, raise LIMIT to 500."""
    if not _ALL_KEYWORDS.search(question):
        return sql
    # Replace existing LIMIT N with LIMIT 500
    return re.sub(r'\bLIMIT\s+\d+', 'LIMIT 500', sql, flags=re.IGNORECASE)


# Common hallucinated column patterns and their fixes
_HALLUCINATED_COLUMNS = {
    'pu.amount': 'p2.product_count * p2.product_price',
    'pu.revenue': 'p2.product_count * p2.product_price',
    'pu.total_amount': 'p2.product_count * p2.product_price',
    'p2.amount': 'p2.product_count * p2.product_price',
    'p2.revenue': 'p2.product_count * p2.product_price',
    'pr.amount': 'p2.product_count * p2.product_price',
    'pr.revenue': 'p2.product_count * p2.product_price',
    'pr.quantity': 'p2.product_count',
    'pu.quantity': 'p2.product_count',
    'p2.quantity': 'p2.product_count',
    'pr.price': 'p2.product_price',
    'pu.price': 'p2.product_price',
    'pu.price_per_item': 'p2.product_price',
    'p2.price_per_item': 'p2.product_price',
    'pr.price_per_item': 'p2.product_price',
    'pu.customer_name': 'cu.customer_name',
    'pr.store_id': 'de.store_id',
    'pu.product_id': 'p2.product_id',
    'cu.manager': 'pu.customer_id',
    'cu.region': 'st.store_name',
}


def _fix_max_min_subquery(sql: str) -> str:
    """Convert MAX/MIN subquery pattern to ORDER BY pattern.

    Transforms:
      WITH max_X AS (SELECT MAX(X) AS v FROM t) SELECT ... FROM t WHERE X = (SELECT v FROM max_X) AND filter
    Into:
      SELECT ... FROM t WHERE filter ORDER BY X DESC LIMIT 1
    """
    # Pattern: CTE with MAX/MIN
    cte_match = re.search(
        r'WITH\s+(\w+)\s+AS\s*\(\s*SELECT\s+(MAX|MIN)\((\w+)\)\s+AS\s+\w+\s+FROM\s+(\S+)(?:\s+\w+)?\s*\)',
        sql, re.IGNORECASE
    )
    if not cte_match:
        return sql

    cte_name, func, col, table = cte_match.groups()
    func = func.upper()

    # Find the main query AFTER the CTE (skip the CTE body)
    cte_end = cte_match.end()
    main_sql = sql[cte_end:].strip()

    # Match: SELECT cols FROM table WHERE full_where_clause
    main_pattern = re.compile(
        rf'SELECT\s+(.+?)\s+FROM\s+{re.escape(table)}\s+WHERE\s+(.+?)(?:\s+LIMIT\s+\d+)?(?:;|\s*$)',
        re.IGNORECASE | re.DOTALL
    )
    main_match = main_pattern.search(main_sql)
    if not main_match:
        return sql

    select_clause = main_match.group(1).strip()
    where_clause = main_match.group(2).strip()

    # Split WHERE clause by AND and remove the CTE reference
    and_parts = re.split(r'\s+AND\s+', where_clause, flags=re.IGNORECASE)
    cte_ref_pattern = re.compile(
        rf'^{re.escape(col)}\s*=\s*\(?\s*SELECT\s+\w+\s+FROM\s+{re.escape(cte_name)}\s*\)?$',
        re.IGNORECASE
    )

    remaining_parts = []
    for part in and_parts:
        if not cte_ref_pattern.match(part.strip()):
            remaining_parts.append(part.strip())

    # Build the corrected SQL
    order_dir = "DESC" if func == "MAX" else "ASC"
    where_str = f"WHERE {' AND '.join(remaining_parts)}" if remaining_parts else ""
    result = f"SELECT {select_clause} FROM {table} {where_str} ORDER BY {col} {order_dir} LIMIT 1"
    return result


def _fix_hallucinated_columns(sql: str) -> str:
    """Replace common hallucinated column references and remove unnecessary JOINs."""
    for wrong, correct in _HALLUCINATED_COLUMNS.items():
        sql = sql.replace(wrong, correct)

    # Remove unnecessary JOINs with purchases if no pu.* columns used (besides JOIN condition)
    if 'purchase_items' in sql and 'purchases' in sql:
        pu_cols = set(re.findall(r'pu\.(\w+)', sql)) - {'purchase_id'}
        if not pu_cols or pu_cols <= {'purchase_date'}:
            sql = re.sub(
                r'\s+(?:INNER\s+)?JOIN\s+\S+\s+pu\s+ON\s+\S+\s*=\s*pu\.purchase_id\s*',
                ' ',
                sql,
                flags=re.IGNORECASE
            )
    return sql


def _fix_forward_references(sql: str) -> str:
    """Fix JOIN conditions that reference table aliases before they are defined.

    Strategy: collect deferred conditions (those with forward refs), then
    insert them right after the JOIN where the referenced alias is defined.
    """
    join_re = re.compile(
        r'((?:LEFT\s+|RIGHT\s+|INNER\s+|FULL\s+|CROSS\s+)?JOIN)\s+(\S+)\s+(?:AS\s+)?(\w+)\s+ON\s+(.+?)(?=\s+(?:LEFT\s+|RIGHT\s+|INNER\s+|FULL\s+|CROSS\s+)?JOIN\s|\s*(?:WHERE|GROUP|ORDER|LIMIT|$))',
        re.IGNORECASE | re.DOTALL
    )
    joins = list(join_re.finditer(sql))
    if len(joins) < 2:
        return sql

    # Build ordered list of aliases
    from_alias = re.search(r'FROM\s+\S+\s+(?:AS\s+)?(\w+)', sql, re.IGNORECASE)
    alias_order = [from_alias.group(1).lower()] if from_alias else []
    for m in joins:
        alias_order.append(m.group(3).lower())

    # Pass 1: extract deferred conditions
    # deferred[alias] = list of conditions that should be added after alias's JOIN
    deferred = {}  # target_alias -> [condition_parts]
    join_fixes = {}  # join_index -> (ok_parts, has_deferred)

    defined = set(alias_order[:1])  # start with FROM alias
    for i, m in enumerate(joins):
        condition = m.group(4).strip()
        alias = m.group(3).lower()
        and_parts = re.split(r'\s+AND\s+', condition, flags=re.IGNORECASE)

        ok = []
        for part in and_parts:
            refs = re.findall(r'(\w+)\.\w+', part)
            bad = [r for r in refs if r.lower() not in defined and r.lower() != alias]
            if bad:
                # Find which alias this part should attach to
                for ref in bad:
                    target = ref.lower()
                    deferred.setdefault(target, []).append(part)
            else:
                ok.append(part)

        join_fixes[i] = ok
        defined.add(alias)

    if not deferred:
        return sql

    # Pass 2: rebuild SQL
    result = []
    prev = 0
    for i, m in enumerate(joins):
        result.append(sql[prev:m.start()])
        jt, tbl, alias = m.group(1), m.group(2), m.group(3)
        ok_parts = join_fixes[i]

        # Add any deferred conditions that target this alias
        extra = deferred.pop(alias.lower(), [])
        all_parts = ok_parts + extra
        result.append(f'{jt} {tbl} {alias} ON {" AND ".join(all_parts)}')
        prev = m.end()

    result.append(sql[prev:])
    return ''.join(result)


def _fix_cte_errors(sql: str) -> str:
    """Fix common errors in multi-step CTE queries, including inside CTE bodies."""
    sql_before = sql
    sql = _fix_max_min_subquery(sql)
    if sql != sql_before:
        logger.info(f"MAX/MIN fix applied: {sql[:100]}")
    sql = _fix_hallucinated_columns(sql)

    # Find all CTE bodies and apply fixes to each
    cte_pattern = re.compile(r'(\w+)\s+AS\s*\(', re.IGNORECASE)
    cte_matches = list(cte_pattern.finditer(sql))

    if cte_matches:
        # Process from end to start to preserve positions
        for m in reversed(cte_matches):
            cte_name = m.group(1)
            body_start = m.end()  # position after opening (
            depth = 1
            i = body_start
            while i < len(sql) and depth > 0:
                if sql[i] == '(':
                    depth += 1
                elif sql[i] == ')':
                    depth -= 1
                i += 1
            body_end = i  # position after closing )
            cte_body = sql[body_start:body_end - 1]  # exclude closing )

            # Apply fixes to CTE body
            fixed_body = _fix_hallucinated_columns(cte_body)
            fixed_body = _fix_forward_references(fixed_body)

            if fixed_body != cte_body:
                sql = sql[:body_start] + fixed_body + sql[body_end - 1:]

    # Fix the outer query only (after all CTEs)
    # Find where the last CTE ends
    last_paren = 0
    depth = 0
    in_cte = False
    for i, ch in enumerate(sql):
        if ch == '(':
            if not in_cte:
                # Check if this is a CTE opening (preceded by AS)
                before = sql[max(0, i-10):i].strip().upper()
                if before.endswith('AS'):
                    in_cte = True
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0 and in_cte:
                last_paren = i + 1
                in_cte = False

    if last_paren > 0:
        outer_query = sql[last_paren:].strip()
        if outer_query:
            fixed_outer = _fix_forward_references(outer_query)
            sql = sql[:last_paren] + '\n' + fixed_outer

    return sql


def _fix_filter_values(sql: str, db_conn, table_name: str, manifest: dict = None) -> str:
    """Fix WHERE clause filter values that don't match actual database values.

    Finds patterns like LOWER(col) = 'wrong_value' and replaces with closest match.
    Priority: exact → substring → plural → fuzzy → translation.
    """
    if not manifest:
        return sql

    from difflib import SequenceMatcher

    def _similarity(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio()

    def _strip_plural(s: str) -> str:
        if s.endswith('ies') and len(s) > 4:
            return s[:-3] + 'y'
        if s.endswith(('ses', 'xes', 'zes')):
            return s[:-2]
        if s.endswith('s') and not s.endswith('ss') and len(s) > 3:
            return s[:-1]
        return s

    def _get_all_values(col: str) -> list[str]:
        """Get all distinct values for a column from the database."""
        try:
            rows = db_conn.execute(
                f"SELECT DISTINCT CAST({col} AS VARCHAR) FROM {table_name} WHERE {col} IS NOT NULL"
            ).fetchall()
            return [str(r[0]).lower() for r in rows if r[0]]
        except Exception:
            return []

    filter_pattern = re.compile(
        r"(?:LOWER\s*\(\s*(\w+)\s*\)|(\w+))\s*(?:=\s*'([^']*)'|LIKE\s+'%?([^']*?)%?')",
        re.IGNORECASE
    )

    profiles = manifest.get('profiles', {})
    result = sql

    for m in filter_pattern.finditer(sql):
        col_name = (m.group(1) or m.group(2)).lower()
        filter_value = (m.group(3) or m.group(4) or '').lower()

        if not filter_value or len(filter_value) < 2:
            continue

        # Get samples from manifest, supplemented by DB query
        samples = []
        for prof_col, profile in profiles.items():
            if prof_col.lower() == col_name:
                samples = [str(s).lower() for s in profile.get('sample_values', [])]
                break

        # If samples are limited, query DB for all distinct values
        if len(samples) < 20 and db_conn:
            db_values = _get_all_values(col_name)
            if db_values:
                samples = list(set(samples + db_values))

        if not samples:
            continue

        # 1. Exact match
        if filter_value in samples:
            continue

        # 2. Substring match
        partial = [s for s in samples if filter_value in s or s in filter_value]
        if partial:
            best = partial[0]
            old = m.group(0)
            result = result.replace(old, old.replace(filter_value, best), 1)
            logger.info(f"Fixed filter (substring): '{filter_value}' → '{best}'")
            continue

        # 3. Plural/suffix match
        stripped = _strip_plural(filter_value)
        if stripped != filter_value:
            plural = [s for s in samples if s == stripped or s.startswith(stripped)]
            if plural:
                best = plural[0]
                old = m.group(0)
                result = result.replace(old, old.replace(filter_value, best), 1)
                logger.info(f"Fixed filter (plural): '{filter_value}' → '{best}'")
                continue

        # 4. Fuzzy match
        fuzzy = [(s, _similarity(filter_value, s)) for s in samples]
        fuzzy.sort(key=lambda x: x[1], reverse=True)
        if fuzzy and fuzzy[0][1] >= 0.7:
            best = fuzzy[0][0]
            old = m.group(0)
            result = result.replace(old, old.replace(filter_value, best), 1)
            logger.info(f"Fixed filter (fuzzy): '{filter_value}' → '{best}' ({fuzzy[0][1]:.2f})")
            continue

        # 5. Russian → English translation
        translations = {
            'золото': 'yellowgold', 'белое золото': 'whitegold', 'розовое золото': 'rosegold',
            'желтое золото': 'yellowgold', 'серебро': 'silver',
            'мужской': 'men', 'женский': 'women', 'детский': 'kids',
            'франция': 'france', 'италия': 'italy', 'япония': 'japan',
            'швейцария': 'switzerland', 'германия': 'germany', 'китай': 'china',
            'россия': 'russia', 'сша': 'usa', 'великобритания': 'uk',
        }
        for ru, en in translations.items():
            if ru in filter_value and en in samples:
                old = m.group(0)
                result = result.replace(old, old.replace(filter_value, en), 1)
                logger.info(f"Fixed filter (translate): '{filter_value}' → '{en}'")
                break

    return result


def _fix_current_date(sql: str, manifests: dict = None) -> str:
    """Replace CURRENT_DATE with MAX(timestamp) subquery for historical data."""
    if 'CURRENT_DATE' not in sql.upper():
        return sql

    # Try to find a date column from manifests
    date_col = None
    date_table = None
    if manifests:
        for table_name, manifest in manifests.items():
            profiles = manifest.get('profiles', {})
            for col_name, info in profiles.items():
                if info.get('type') == 'BIGINT' and ('date' in col_name.lower() or 'time' in col_name.lower()):
                    date_col = col_name
                    date_table = table_name
                    break
            if date_col:
                break

    if date_col and date_table:
        replacement = f"(SELECT TO_TIMESTAMP(MAX({date_col})) FROM {date_table})"
        sql = re.sub(r'CURRENT_DATE', replacement, sql, flags=re.IGNORECASE)
        logger.info(f"Fixed CURRENT_DATE → {replacement}")
    else:
        # Fallback: just remove CURRENT_DATE usage by replacing with a warning comment
        logger.warning("CURRENT_DATE found but no date column detected in manifests")

    return sql


def _validate_and_fix_sql(sql: str, schema_info: dict[str, list[str]]) -> str:
    """Validate SQL against schema and fix common errors."""
    return _fix_cte_errors(sql)


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

            # Check if our model is available (qwen2.5:14b or similar)
            model_available = any(self.model in name for name in model_names)

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
                    f"Модель '{self.model}' не найдена в Ollama. Скачайте её: ollama pull {self.model}"
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
                async for line in response.aiter_text():
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

    async def classify_intent(self, question: str) -> str:
        """Classify user question intent using fast rule-based method.

        Delegates to domain.ai.intent_classifier — no LLM round-trip needed.
        """
        return classify_intent_fast(question)

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
    
    async def generate_sql(self, question: str, system_prompt: str, table_name: str, column_names: str, examples: list[dict] = None, is_multi_table: bool = False) -> str:
        """Generate SQL query from natural language question."""
        prompt = get_sql_prompt(table_name, column_names, question, examples, is_multi_table)
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
    
    async def generate_summary(self, question: str, data: Any, db_conn=None, extended: bool = False) -> str:
        """Generate human-readable summary of query results."""
        # Include row count explicitly so LLM doesn't have to count
        row_count = len(data) if isinstance(data, list) else 0
        data_str = json.dumps(data, ensure_ascii=False, default=str)[:2000]
        if row_count > 0:
            data_str = f"[ВСЕГО ЗАПИСЕЙ: {row_count}]\n{data_str}"
        prompt_template = SUMMARY_PROMPT_EXTENDED if extended else SUMMARY_PROMPT
        prompt = prompt_template.format(question=question, data=data_str)
        
        system_msg = "Ты — бизнес-аналитик. Твоя задача — объяснять данные понятным человеческим языком. Пиши ответ ТОЛЬКО на русском языке (Russian ONLY). КАТЕГОРИЧЕСКИ ЗАПРЕЩАЕТСЯ выводить китайские иероглифы (NO CHINESE) или английский текст. Ни в коем случае не выводи SQL-код или JSON-структуры. Не указывай валюту денежных сумм, если она явно не указана в данных."
        
        # Inject custom instructions
        if db_conn:
            try:
                instructions = db_conn.execute("SELECT category, content FROM assistant_instructions").fetchall()
                if instructions:
                    custom_rules = "\n\nПОЛЬЗОВАТЕЛЬСКИЕ ИНСТРУКЦИИ:\n"
                    for cat, content in instructions:
                        custom_rules += f"- [{cat.upper()}]: {content}\n"
                    system_msg += custom_rules
            except Exception as e:
                logger.error(f"Failed to fetch instructions: {e}")
                
        return await self.generate(prompt, system=system_msg, num_predict=400)


ollama_client = OllamaClient()


def _analyze_data_structure(data: list[dict]) -> dict:
    """Analyze data columns to determine types for chart selection."""
    if not data or not isinstance(data, list) or not isinstance(data[0], dict):
        return {"cat_cols": [], "num_cols": [], "date_cols": [], "row_count": 0}

    keys = list(data[0].keys())
    cat_cols, num_cols, date_cols = [], [], []
    date_keywords = {'date', 'month', 'year', 'day', 'period', 'time', 'week', 'quarter'}

    for k in keys:
        k_lower = k.lower()
        sample_val = data[0].get(k)

        # Date column detection
        if any(w in k_lower for w in date_keywords):
            date_cols.append(k)
            continue

        # Check actual values
        if isinstance(sample_val, (int, float)):
            num_cols.append(k)
        elif isinstance(sample_val, str):
            # Check if values look numeric
            try:
                float(sample_val.replace(',', '.'))
                num_cols.append(k)
            except ValueError:
                cat_cols.append(k)

    return {
        "cat_cols": cat_cols,
        "num_cols": num_cols,
        "date_cols": date_cols,
        "row_count": len(data),
    }


def _detect_chart_type(intent: str, question: str, data: Any = None) -> Optional[str]:
    """Detect appropriate chart type based on question intent and data structure."""
    q_lower = question.lower()

    # 1. Explicit user request overrides everything
    if any(w in q_lower for w in ['тепловая карта', 'heatmap']):
        return 'heatmap'
    if any(w in q_lower for w in ['scatter', 'точечн', 'корреляц']):
        return 'scatter'

    # 2. Strong intent signals override data structure
    if intent == 'top' or any(w in q_lower for w in ['топ', 'лучш', 'рейтинг', 'сравни', 'сравнение']):
        return 'bar'
    if any(w in q_lower for w in ['график', 'динамика', 'тренд', 'по месяцам', 'по дням', 'по неделям']):
        return 'line'
    if any(w in q_lower for w in ['доля', 'распределение', 'структура', 'процент']):
        return 'pie'

    # 3. Data-structure-based detection
    if isinstance(data, list) and len(data) > 1 and isinstance(data[0], dict):
        s = _analyze_data_structure(data)

        # Time series: date column + numeric
        if s["date_cols"] and s["num_cols"]:
            return 'line'

        # Pie: 1 categorical + 1 numeric, ≤8 rows (before scatter!)
        if len(s["cat_cols"]) >= 1 and len(s["num_cols"]) >= 1 and s["row_count"] <= 8:
            return 'pie'

        # Heatmap: 2+ categorical + 1+ numeric, moderate row count
        if len(s["cat_cols"]) >= 2 and len(s["num_cols"]) >= 1 and 4 <= s["row_count"] <= 100:
            return 'heatmap'

        # Bar: categorical + numeric (before scatter — most common case)
        if s["cat_cols"] and s["num_cols"]:
            return 'bar'

        # Scatter: 2+ numeric columns, no categorical, no date
        if len(s["num_cols"]) >= 2 and not s["cat_cols"] and not s["date_cols"] and s["row_count"] >= 3:
            return 'scatter'

    # 4. Fallback
    if intent == 'chart':
        return 'line'

    return None


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
            data_summary = json.loads(row[2]) if row[2] else {}
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
        db_conn.execute("UPDATE chat_sessions SET updated_at = ? WHERE id = ?", [datetime.now().isoformat(), session_id])
        logger.info(f"💾 Saved assistant msg to session {session_id}")
    except Exception as e:
        logger.error(f"Failed to save assistant message: {e}")


def _save_to_session(session_id: Optional[str], db_conn, question: str, result: dict[str, Any]) -> None:
    """Save question and answer to chat session history (legacy wrapper)."""
    _save_user_message(session_id, db_conn, question)
    _save_assistant_message(session_id, db_conn, result)


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
    
    contextual_question = question
    history_rows = []
    
    if session_id:
        try:
            # Load history
            history_rows = db_conn.execute(
                "SELECT role, content FROM chat_messages WHERE session_id = ? ORDER BY created_at ASC LIMIT 10",
                [session_id]
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
    
    # Check Cache via chat_messages (single source of truth)
    dataset_key = str(dataset_id) if dataset_id else "global"
    normalized_question = re.sub(r'[-–—]', ' ', question.strip().lower())
    
    try:
        last_update_row = db_conn.execute("SELECT MAX(uploaded_at) FROM datasets").fetchone()
        last_db_update = last_update_row[0] if last_update_row and last_update_row[0] else datetime.min
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
                if not (cached_result.get("error") or "Произошла ошибка" in (ans or "") or "непредвиденная ошибка" in (ans or "")):
                    logger.info(f"✅ CACHE HIT from chat_messages: '{question[:50]}...'")
                    cached_result["answer"] = _inject_count_header(ans, cached_result.get("data"))
                    cached_result["error"] = None
                    cached_result["is_cached"] = True
                    cached_result["cached_at"] = created_at.isoformat()
                    cached_result["processing_time"] = time.time() - start_time
                    cached_result["_question"] = question
                    _save_assistant_message(session_id, db_conn, cached_result)
                    return cached_result
    except Exception as e:
        logger.error(f"Cache check failed: {e}")

    logger.info(f"❌ CACHE MISS: Processing new query '{question[:50]}...'")

    try:
        # Load manifest once for the entire request (fast path for schema/mapping)
        manifest = load_manifest(db_conn, dataset_id)
        if manifest:
            logger.info(f"✅ Using precomputed manifest for dataset {dataset_id[:8]}...")
        
        _status("Анализирую запрос...")

        # Step 1: Classify intent
        _status("Определяю тип запроса...")
        intent = await ollama_client.classify_intent(contextual_question)
        result["intent"] = intent
        
        # Step 2: Route based on intent
        _status("Извлекаю данные...")
        data = None

        if intent == 'kpi':
            data = analytics_svc.get_kpis(db_conn, dataset_id=dataset_id, manifest=manifest)
            result["data"] = [data] if isinstance(data, dict) else data
            
        elif intent == 'comparison':
            from app.utils.helpers import get_default_date_range
            end_str, _ = get_default_date_range()
            raw_comp = analytics_svc.compare_periods(
                db_conn,
                current_start=(date.today().replace(day=1)).strftime('%Y-%m-%d'),
                current_end=date.today().strftime('%Y-%m-%d'),
                prev_start=(date.today().replace(day=1) - timedelta(days=1)).replace(day=1).strftime('%Y-%m-%d'),
                prev_end=(date.today().replace(day=1) - timedelta(days=1)).strftime('%Y-%m-%d'),
                dataset_id=dataset_id,
                manifest=manifest
            )
            data = [
                {"Период": "Предыдущий", **raw_comp["previous"]},
                {"Период": "Текущий", **raw_comp["current"]}
            ]
            result["data"] = data
            
        elif intent == 'top':
            question_lower = question.lower()
            if any(kw in question_lower for kw in ['категор', 'групп']):
                data = analytics_svc.sales_by_category(db_conn, dataset_id=dataset_id, manifest=manifest)
                result["data"] = data
            elif any(kw in question_lower for kw in ['менеджер', 'сотрудник', 'продавец']):
                data = analytics_svc.sales_by_manager(db_conn, dataset_id=dataset_id, manifest=manifest)
                result["data"] = data
            # For branch/region queries, let SQL generation handle it
            
        elif intent == 'drop':
            data = analytics_svc.find_weak_spots(db_conn, dataset_id=dataset_id, manifest=manifest)
            result["data"] = data
            
        elif intent == 'chart':
            # Check if dataset is from a .db group (multi-table) — let SQL generation handle it
            group_datasets_check = analytics_svc.get_group_datasets(db_conn, dataset_id)
            if len(group_datasets_check) <= 1:
                data = analytics_svc.sales_timeline(db_conn, dataset_id=dataset_id, manifest=manifest)
                result["data"] = data
            
        elif intent == 'summary':
            kpis = analytics_svc.get_kpis(db_conn, dataset_id=dataset_id, manifest=manifest)
            categories = analytics_svc.sales_by_category(db_conn, dataset_id=dataset_id, manifest=manifest)
            data = {"kpis": kpis, "categories": categories}
            result["data"] = [data] if isinstance(data, dict) else data

        # Prepare dataset schema metadata for AI SQL generation
        # Check if dataset belongs to a group (multi-table mode)
        group_datasets = analytics_svc.get_group_datasets(db_conn, dataset_id)
        is_group = len(group_datasets) > 1

        if is_group:
            # Multi-table mode: build combined schema with relationships
            relationships = []
            try:
                rel_json = group_datasets[0].get("relationships")
                if rel_json:
                    relationships = json.loads(rel_json) if isinstance(rel_json, str) else rel_json
            except Exception:
                pass
            
            # Load manifests for all tables in the group
            group_manifests = {}
            for gd in group_datasets:
                ds_id = gd.get("id")
                if ds_id:
                    m = load_manifest(db_conn, ds_id)
                    if m:
                        group_manifests[ds_id] = m
            
            sys_prompt = get_multi_table_system_prompt(group_datasets, relationships, group_manifests if group_manifests else None)
            all_schema_parts = []
            for gd in group_datasets:
                try:
                    cols_json = gd["columns"]
                    cols = json.loads(cols_json) if cols_json and isinstance(cols_json, str) else (cols_json or [])
                    for c in cols:
                        all_schema_parts.append(f"{gd['name']}.{c['key']} ({c['type']})")
                except Exception:
                    pass
            schema_str = ", ".join(all_schema_parts) if all_schema_parts else "(schema could not be loaded)"
            table_name = group_datasets[0]["name"]
        else:
            # Single-table mode — use manifest loaded earlier, fallback to information_schema
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
                    cols_query = db_conn.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ?", [table_name]).fetchall()
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
            
        # Get few-shot examples for learning
        few_shot_examples = _get_few_shot_examples(db_conn, dataset_id, result["intent"])

        # Route query
        if result["intent"] in ['kpi', 'comparison', 'top', 'drop', 'chart']:
            # For specific intents, we use LLM to generate SQL too
            _status("Формирую SQL-запрос...")
            result["sql"] = await ollama_client.generate_sql(contextual_question, sys_prompt, table_name, schema_str, few_shot_examples, is_multi_table=is_group)

            # For multi-table: replace original table names with dataset_xxx names
            if is_group and group_datasets:
                name_map = {gd['name']: gd['table_name'] for gd in group_datasets}
                result["sql"] = _remap_table_names(result["sql"], name_map)
                result["sql"] = _fix_current_date(result["sql"], group_manifests)
            elif manifest:
                result["sql"] = _fix_current_date(result["sql"], {dataset_id: manifest})

            # Fix filter values that don't match actual database values
            result["sql"] = _fix_filter_values(result["sql"], db_conn, table_name, manifest)

            # Always execute SQL to get fresh data (overrides pre-computed intent data)
            _status("Выполняю запрос к базе данных...")
            is_valid, error = full_validate(result["sql"])
            logger.info(f"SQL valid: {is_valid}, error: {error}")
            if is_valid:
                try:
                    safe_sql = sanitize_sql(result["sql"])
                    logger.info(f"Executing SQL: {safe_sql[:300]}")
                    raw_data = db_conn.execute(safe_sql).fetchall()
                    cols = [desc[0] for desc in db_conn.description] if db_conn.description else []
                    result["data"] = [dict(zip(cols, row)) for row in raw_data]
                    logger.info(f"SQL returned {len(result['data'])} rows")
                except Exception as e:
                    logger.error(f"SQL execution error: {e}")
            else:
                logger.warning(f"SQL validation failed: {error}")
        else:
            _status("Формирую SQL-запрос...")
            result["sql"] = await ollama_client.generate_sql(contextual_question, sys_prompt, table_name, schema_str, few_shot_examples, is_multi_table=is_group)

            # For multi-table: replace original table names with dataset_xxx names
            if is_group and group_datasets:
                name_map = {gd['name']: gd['table_name'] for gd in group_datasets}
                result["sql"] = _remap_table_names(result["sql"], name_map)
                result["sql"] = _fix_current_date(result["sql"], group_manifests)
            elif manifest:
                result["sql"] = _fix_current_date(result["sql"], {dataset_id: manifest})

            # Fix filter values that don't match actual database values
            result["sql"] = _fix_filter_values(result["sql"], db_conn, table_name, manifest)

            # Validate SQL
            _status("Выполняю запрос к базе данных...")
            is_valid, error = full_validate(result["sql"])
            if not is_valid:
                # Try to fix validation error
                fixed = await _retry_sql_fix(contextual_question, result["sql"], error, sys_prompt, is_group, ollama_client)
                if fixed:
                    result["sql"] = fixed
                    is_valid, error = full_validate(fixed)
                if not is_valid:
                    result["error"] = f"Generated invalid SQL: {error}"
                    result["answer"] = "Не могу выполнить запрос из-за ограничений безопасности."
                    return result

            # Sanitize and run
            safe_sql = sanitize_sql(result["sql"])
            try:
                raw_data = db_conn.execute(safe_sql).fetchall()
                cols = [desc[0] for desc in db_conn.description] if db_conn.description else []
                data = [dict(zip(cols, row)) for row in raw_data]
                result["data"] = data
            except Exception as e:
                logger.error(f"SQL execution error: {e}")
                # Try to fix execution error
                fixed = await _retry_sql_fix(contextual_question, result["sql"], str(e), sys_prompt, is_group, ollama_client)
                if fixed:
                    is_valid2, error2 = full_validate(fixed)
                    if is_valid2:
                        try:
                            safe_sql2 = sanitize_sql(fixed)
                            raw_data2 = db_conn.execute(safe_sql2).fetchall()
                            cols2 = [desc[0] for desc in db_conn.description] if db_conn.description else []
                            result["data"] = [dict(zip(cols2, row)) for row in raw_data2]
                            result["sql"] = fixed
                            logger.info(f"Retry succeeded: {len(result['data'])} rows")
                        except Exception as e2:
                            logger.error(f"Retry also failed: {e2}")
                            result["error"] = str(e2)
                            result["answer"] = "Произошла ошибка при выполнении запроса к базе данных."
                            return result
                    else:
                        result["error"] = f"Retry validation failed: {error2}"
                        result["answer"] = "Не удалось исправить запрос."
                        return result
                else:
                    result["error"] = str(e)
                    result["answer"] = "Произошла ошибка при выполнении запроса к базе данных."
                    return result
        
        # Determine chart type
        result["chart_type"] = _detect_chart_type(intent, question, result.get("data"))
        
        # Step 3: Generate Summary Answer
        _status("Формирую ответ...")
        if result["data"]:
            summary = await ollama_client.generate_summary(contextual_question, result["data"], db_conn, extended)
            summary, chart_key = _extract_chart_key(summary)
            result["answer"] = _inject_count_header(summary, result["data"])
            if chart_key:
                result["chart_value_key"] = chart_key
        else:
            result["answer"] = "По вашему запросу нет данных."
            
    except TimeoutError as e:
        result["error"] = str(e)
        result["answer"] = "Модель не успела ответить — запрос занял слишком много времени. Попробуйте ещё раз."
        result["retryable"] = True
    except ConnectionError as e:
        result["error"] = str(e)
        error_msg = str(e)
        if "не найдена" in error_msg or "not found" in error_msg.lower():
            result["answer"] = f"Модель AI не найдена. {error_msg}"
        elif "не запущена" in error_msg or "not running" in error_msg.lower():
            result["answer"] = f"Сервис AI недоступен. {error_msg}"
        else:
            result["answer"] = "Сервис AI временно недоступен. Проверьте запуск Ollama."
        result["retryable"] = True
    except Exception as e:
        logger.exception("Error processing question")
        result["error"] = str(e)
        result["answer"] = "Произошла непредвиденная ошибка при обработке вашего запроса."
        result["retryable"] = True
    
    result["processing_time"] = round(time.time() - start_time, 2)

    # Save to session history (single save — _save_to_session already saves both user+assistant)
    result["_question"] = question
    _save_to_session(session_id, db_conn, question, result)
            
    return result
