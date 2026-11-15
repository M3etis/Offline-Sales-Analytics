from __future__ import annotations
import json
import re
import logging
import threading
from typing import Optional

import duckdb

logger = logging.getLogger(__name__)

ALLOWED_TABLES = ['sales']

ALLOWED_COLUMNS = [
    'date', 'order_id', 'product', 'category', 'region', 'manager',
    'channel', 'quantity', 'price', 'discount', 'revenue', 'cost',
    'profit', 'customer_id', 'brand', 'warehouse', 'returns', 'status'
]

DANGEROUS_KEYWORDS = [
    'DROP', 'DELETE', 'UPDATE', 'INSERT', 'ALTER', 'CREATE', 'TRUNCATE',
    # REPLACE не в списке: это обычная строковая функция, а CREATE OR REPLACE / INSERT OR REPLACE
    # отсекаются по CREATE / INSERT
    'GRANT', 'REVOKE', 'EXEC', 'EXECUTE', 'MERGE',
    'CALL', 'COPY', 'ATTACH', 'DETACH', 'LOAD', 'INSTALL',
    'READ_CSV', 'READ_CSV_AUTO', 'READ_PARQUET', 'ST_READ',
    'HTTPFS', 'SPATIAL', 'AZURE', 'GCS', 'S3',
    'SECRET', 'SET', 'RESET', 'VACUUM', 'CHECKPOINT',
    'EXPORT', 'IMPORT', 'FORCE'
]


_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")
_QUOTED_IDENT_RE = re.compile(r'"(?:[^"]|"")*"')


def _strip_literals(sql: str) -> str:
    """Заменяет строковые литералы и имена в кавычках на заглушки, чтобы
    фильтры вроде region = 'S3' или 'Gift Set' не принимались за команды."""
    return _QUOTED_IDENT_RE.sub('"_"', _STRING_LITERAL_RE.sub("''", sql))


def validate_sql(sql: str) -> tuple[bool, Optional[str]]:
    """Validate that SQL is safe to execute.
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not sql or not sql.strip():
        return False, "Empty SQL query"
    
    # Remove comments
    cleaned = re.sub(r'--.*$', '', sql, flags=re.MULTILINE)
    cleaned = re.sub(r'/\*.*?\*/', '', cleaned, flags=re.DOTALL)
    cleaned = cleaned.strip().rstrip(';')
    
    # Must start with SELECT or WITH (for CTEs)
    upper = _strip_literals(cleaned).upper().strip()
    if not (upper.startswith('SELECT') or upper.startswith('WITH')):
        return False, "Only SELECT queries are allowed"
    
    # Check for dangerous keywords
    # Use word boundary matching to avoid false positives
    for keyword in DANGEROUS_KEYWORDS:
        pattern = r'\b' + keyword + r'\b'
        if re.search(pattern, upper):
            return False, f"Forbidden operation: {keyword}"
    
    # Check for semicolons (possible injection)
    if ';' in upper:
        return False, "Multiple statements not allowed"
    
    logger.info(f"SQL validated: {sql[:100]}...")
    return True, None


def sanitize_sql(sql: str, max_rows: int = 1000) -> str:
    """Sanitize SQL query: strip comments, add LIMIT."""
    # Remove comments
    cleaned = re.sub(r'--.*$', '', sql, flags=re.MULTILINE)
    cleaned = re.sub(r'/\*.*?\*/', '', cleaned, flags=re.DOTALL)
    cleaned = cleaned.strip().rstrip(';')
    
    # Add LIMIT if the outer query has none. Раньше любое слово LIMIT (в CTE, подзапросе или
    # в имени колонки) считалось внешним ограничением — и вся таблица уходила в память и в чат
    if not _has_outer_limit(cleaned):
        cleaned = f"{cleaned} LIMIT {max_rows}"

    return cleaned


def _has_outer_limit(sql: str) -> bool:
    tree = _parse_sql_tree(sql)
    if tree is None:
        return re.search(r'\bLIMIT\s+\d+\s*(OFFSET\s+\d+\s*)?$', sql, flags=re.IGNORECASE) is not None
    statements = tree.get('statements') or []
    if not statements:
        return False
    modifiers = (statements[0].get('node') or {}).get('modifiers') or []
    return any(isinstance(m, dict) and m.get('type') in ('LIMIT_MODIFIER', 'LIMIT_PERCENT_MODIFIER') for m in modifiers)


# SQL keywords/functions that can appear after FROM but are NOT table names
SQL_KEYWORDS_AFTER_FROM = {
    'CURRENT_TIMESTAMP', 'CURRENT_DATE', 'CURRENT_TIME', 'NOW',
    'TRUE', 'FALSE', 'NULL', 'LATERAL', 'UNNEST', 'GENERATE_SERIES',
}

# Patterns inside SQL functions that use FROM keyword (not table references)
# e.g., EXTRACT(YEAR FROM date_col), DATE_DIFF(... FROM ... TO ...)
SQL_FROM_IN_FUNCTIONS = [
    r'EXTRACT\s*\(\s*\w+\s+FROM\s+',  # EXTRACT(YEAR FROM col)
    r'DATEDIFF\s*\([^)]*FROM\s+',       # DATEDIFF(... FROM ... TO ...)
    r'DATE_DIFF\s*\([^)]*FROM\s+',       # DATE_DIFF variant
]


def _find_from_in_functions(sql_upper: str) -> set[str]:
    """Extract column/keyword names that appear inside SQL functions using FROM.

    e.g., EXTRACT(YEAR FROM date_of_sold) → {'DATE_OF_SOLD'}
    """
    found = set()
    for pattern in SQL_FROM_IN_FUNCTIONS:
        for m in re.finditer(pattern, sql_upper):
            # Find the word after FROM inside the function
            after_from = sql_upper[m.end():]
            word_match = re.match(r'([A-Za-z_][A-Za-z0-9_]*)', after_from)
            if word_match:
                found.add(word_match.group(1))
    return found


# Табличные функции, которые не читают ни файлы, ни служебные таблицы
SAFE_TABLE_FUNCTIONS = {'range', 'generate_series', 'unnest'}
# Служебные таблицы с префиксом dataset_, которые не являются данными пользователя
PRIVATE_DATASET_TABLES = {'dataset_manifest'}

# Отдельное in-memory соединение только для разбора SQL: к данным приложения доступа не имеет
_parser_conn: duckdb.DuckDBPyConnection | None = None
_parser_lock = threading.Lock()


def _parse_sql_tree(sql: str) -> dict | None:
    """Дерево разбора запроса от самого DuckDB или None, если разобрать не удалось."""
    global _parser_conn
    try:
        with _parser_lock:
            if _parser_conn is None:
                _parser_conn = duckdb.connect(':memory:')
            raw = _parser_conn.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()[0]
        tree = json.loads(raw)
    except Exception as e:  # noqa: BLE001 — любой сбой разбора уводит в запасную проверку
        logger.debug(f"SQL parse failed: {e}")
        return None
    if not isinstance(tree, dict) or tree.get('error'):
        return None
    return tree


def _collect_refs(node, scope: frozenset, tables: list, functions: list) -> None:
    """Собирает обращения к таблицам и табличным функциям с учётом области видимости CTE."""
    if isinstance(node, list):
        for item in node:
            _collect_refs(item, scope, tables, functions)
        return
    if not isinstance(node, dict):
        return
    cte_map = node.get('cte_map')
    if isinstance(cte_map, dict):
        names = {str(e.get('key', '')).lower() for e in cte_map.get('map', []) if isinstance(e, dict)}
        if names:
            scope = scope | names
    node_type = node.get('type')
    if node_type == 'BASE_TABLE':
        tables.append((
            str(node.get('catalog_name') or ''),
            str(node.get('schema_name') or ''),
            str(node.get('table_name') or ''),
            scope,
        ))
    elif node_type == 'TABLE_FUNCTION':
        func = node.get('function') or {}
        functions.append(str(func.get('function_name') or '') if isinstance(func, dict) else '')
    for value in node.values():
        if isinstance(value, (dict, list)):
            _collect_refs(value, scope, tables, functions)


def _is_data_table(name: str) -> bool:
    lowered = name.lower()
    if lowered in PRIVATE_DATASET_TABLES:
        return False
    return lowered in ALLOWED_TABLES or lowered.startswith('dataset_')


def validate_tables(sql: str) -> tuple[bool, Optional[str]]:
    """Check that only allowed tables are referenced.

    Таблицы берутся из дерева разбора DuckDB, а не регулярным выражением: так ловятся
    перечисление через запятую, имена в кавычках, подзапросы и табличные функции
    (read_text, glob, read_json…), которыми можно прочитать файлы или служебные таблицы.
    """
    tree = _parse_sql_tree(sql)
    if tree is None:
        # Не разобралось — запрос всё равно упадёт при выполнении; оставляем прежнюю проверку,
        # чтобы пользователь и цикл автоисправления получили привычную ошибку DuckDB
        return _validate_tables_regex(sql)

    tables: list = []
    functions: list = []
    _collect_refs(tree.get('statements', []), frozenset(), tables, functions)

    for func in functions:
        if func.lower() not in SAFE_TABLE_FUNCTIONS:
            return False, f"Table function '{func}' is not allowed"

    for catalog, schema, table, scope in tables:
        if not catalog and not schema and table.lower() in scope:
            continue  # ссылка на CTE из видимой области
        if catalog or schema.lower() not in ('', 'main'):
            return False, f"Access to table '{'.'.join(p for p in (catalog, schema, table) if p)}' is not allowed"
        if not _is_data_table(table):
            return False, f"Access to table '{table}' is not allowed"

    return True, None


def _validate_tables_regex(sql: str) -> tuple[bool, Optional[str]]:
    """Прежняя проверка регулярным выражением — только для запросов, которые DuckDB не разобрал."""
    upper = sql.upper()

    # Extract CTE names from WITH clause to allow them in FROM/JOIN
    cte_names = set(re.findall(r'WITH\s+(\w+)\s+AS\s*\(', upper))
    # Also handle chained CTEs: WITH a AS (...), b AS (...)
    cte_names.update(re.findall(r',\s*(\w+)\s+AS\s*\(', upper))

    # Find column names inside SQL functions (EXTRACT, DATEDIFF, etc.)
    func_from_names = _find_from_in_functions(upper)

    # Simple check: look for FROM or JOIN followed by table name
    table_pattern = r'(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)'
    tables = re.findall(table_pattern, upper)

    for table in tables:
        if _is_data_table(table):
            continue
        if table in cte_names:
            continue
        if table in SQL_KEYWORDS_AFTER_FROM:
            continue
        if table in func_from_names:
            continue
        return False, f"Access to table '{table}' is not allowed"

    return True, None


def full_validate(sql: str) -> tuple[bool, Optional[str]]:
    """Run all validations on SQL."""
    is_valid, error = validate_sql(sql)
    if not is_valid:
        return False, error
    
    is_valid, error = validate_tables(sql)
    if not is_valid:
        return False, error
    
    return True, None
