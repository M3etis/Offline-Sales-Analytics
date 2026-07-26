from __future__ import annotations
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

ALLOWED_TABLES = ['sales']

ALLOWED_COLUMNS = [
    'date', 'order_id', 'product', 'category', 'region', 'manager',
    'channel', 'quantity', 'price', 'discount', 'revenue', 'cost',
    'profit', 'customer_id', 'brand', 'warehouse', 'returns', 'status'
]

DANGEROUS_KEYWORDS = [
    'DROP', 'DELETE', 'UPDATE', 'INSERT', 'ALTER', 'CREATE', 'TRUNCATE',
    'GRANT', 'REVOKE', 'EXEC', 'EXECUTE', 'MERGE', 'REPLACE',
    'CALL', 'COPY', 'ATTACH', 'DETACH', 'LOAD', 'INSTALL',
    'READ_CSV', 'READ_CSV_AUTO', 'READ_PARQUET', 'ST_READ',
    'HTTPFS', 'SPATIAL', 'AZURE', 'GCS', 'S3',
    'SECRET', 'SET', 'RESET', 'VACUUM', 'CHECKPOINT',
    'EXPORT', 'IMPORT', 'FORCE'
]


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
    upper = cleaned.upper().strip()
    if not (upper.startswith('SELECT') or upper.startswith('WITH')):
        return False, "Only SELECT queries are allowed"
    
    # Check for dangerous keywords
    # Use word boundary matching to avoid false positives
    for keyword in DANGEROUS_KEYWORDS:
        pattern = r'\b' + keyword + r'\b'
        if re.search(pattern, upper):
            return False, f"Forbidden operation: {keyword}"
    
    # Check for semicolons (possible injection)
    if ';' in cleaned:
        return False, "Multiple statements not allowed"
    
    logger.info(f"SQL validated: {sql[:100]}...")
    return True, None


def sanitize_sql(sql: str, max_rows: int = 1000) -> str:
    """Sanitize SQL query: strip comments, add LIMIT."""
    # Remove comments
    cleaned = re.sub(r'--.*$', '', sql, flags=re.MULTILINE)
    cleaned = re.sub(r'/\*.*?\*/', '', cleaned, flags=re.DOTALL)
    cleaned = cleaned.strip().rstrip(';')
    
    # Add LIMIT if not present
    if 'LIMIT' not in cleaned.upper():
        cleaned = f"{cleaned} LIMIT {max_rows}"
    
    return cleaned


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


def validate_tables(sql: str) -> tuple[bool, Optional[str]]:
    """Check that only allowed tables are referenced."""
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
        if table.lower() in ALLOWED_TABLES or table.lower() in ('sales',) or table.lower().startswith('dataset_'):
            continue
        if table in cte_names:
            continue
        if table in SQL_KEYWORDS_AFTER_FROM:
            continue
        if table in func_from_names:
            continue
        # Subquery aliases are OK (FROM (...)) — skip if preceded by subquery
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
