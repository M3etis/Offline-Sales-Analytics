"""
SQL post-processing utilities extracted from app/services/llm.py.

Responsibilities:
 - Extract SQL from raw LLM output
 - Fix common LLM SQL mistakes (hallucinated columns, forward refs, CTE errors)
 - Adjust LIMIT for "all" queries
 - Fix CURRENT_DATE for historical datasets
 - Fix filter values against actual DB data
 - Remap table names for multi-dataset mode
"""
from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from typing import Any

logger = logging.getLogger(__name__)

MAX_SQL_RETRIES = 1

# ── Regex helpers ──────────────────────────────────────────────────────────────
_ALL_KEYWORDS = re.compile(
    r"\b(все|всё|всех|весь|вся|полный\s+список|полностью|всего)\b", re.IGNORECASE
)

_HALLUCINATED_COLUMNS: dict[str, str] = {
    "pu.amount":          "p2.product_count * p2.product_price",
    "pu.revenue":         "p2.product_count * p2.product_price",
    "pu.total_amount":    "p2.product_count * p2.product_price",
    "p2.amount":          "p2.product_count * p2.product_price",
    "p2.revenue":         "p2.product_count * p2.product_price",
    "pr.amount":          "p2.product_count * p2.product_price",
    "pr.revenue":         "p2.product_count * p2.product_price",
    "pr.quantity":        "p2.product_count",
    "pu.quantity":        "p2.product_count",
    "p2.quantity":        "p2.product_count",
    "pr.price":           "p2.product_price",
    "pu.price":           "p2.product_price",
    "pu.price_per_item":  "p2.product_price",
    "p2.price_per_item":  "p2.product_price",
    "pr.price_per_item":  "p2.product_price",
    "pu.customer_name":   "cu.customer_name",
    "pr.store_id":        "de.store_id",
    "pu.product_id":      "p2.product_id",
    "cu.manager":         "pu.customer_id",
    "cu.region":          "st.store_name",
}


# ── Extraction ─────────────────────────────────────────────────────────────────
def extract_sql(raw: str) -> str:
    """Extract pure SQL from raw LLM output that may contain explanatory text."""
    if not raw:
        return ""

    raw = raw.strip()

    # Try ```sql ... ``` or ``` ... ``` blocks
    code_block = re.search(r"```(?:sql)?\s*\n?(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    if code_block:
        sql = code_block.group(1).strip()
        if sql.upper().startswith(("SELECT", "WITH")):
            return sql

    # Look for a standalone SELECT/WITH statement
    sql_match = re.search(
        r"((?:SELECT|WITH)\b.*?)(?:\n{2,}(?![\s\S]*?(?:SELECT|FROM|WHERE|JOIN|GROUP|ORDER|LIMIT|HAVING|UNION|AND|OR|ON|AS|INTO|VALUES|SET|CASE|WHEN|THEN|ELSE|END|CAST|COALESCE|ROUND|SUM|AVG|COUNT|MAX|MIN|DATE_TRUNC|TRY_CAST|LOWER|ROW_NUMBER|RANK|OVER|PARTITION|NULLIF)\b).*|$)",
        raw,
        re.DOTALL | re.IGNORECASE,
    )
    if sql_match:
        sql = sql_match.group(1).strip().rstrip("`").strip()
        if sql.upper().startswith(("SELECT", "WITH")):
            return sql

    cleaned = raw.strip().strip("`").strip()
    if cleaned.lower().startswith("sql"):
        cleaned = cleaned[3:].strip()
    return cleaned


# ── Limit adjustment ───────────────────────────────────────────────────────────
def adjust_limit(question: str, sql: str) -> str:
    """Raise LIMIT to 500 when user asks for 'all' items."""
    if not _ALL_KEYWORDS.search(question):
        return sql
    return re.sub(r"\bLIMIT\s+\d+", "LIMIT 500", sql, flags=re.IGNORECASE)


# ── CTE / hallucination fixes ──────────────────────────────────────────────────
def fix_hallucinated_columns(sql: str) -> str:
    for wrong, correct in _HALLUCINATED_COLUMNS.items():
        sql = sql.replace(wrong, correct)
    if "purchase_items" in sql and "purchases" in sql:
        pu_cols = set(re.findall(r"pu\.(\w+)", sql)) - {"purchase_id"}
        if not pu_cols or pu_cols <= {"purchase_date"}:
            sql = re.sub(
                r"\s+(?:INNER\s+)?JOIN\s+\S+\s+pu\s+ON\s+\S+\s*=\s*pu\.purchase_id\s*",
                " ",
                sql,
                flags=re.IGNORECASE,
            )
    return sql


def fix_max_min_subquery(sql: str) -> str:
    """Convert `WITH max_X AS (SELECT MAX(X) …) SELECT … WHERE X = (SELECT v FROM max_X)` → ORDER BY pattern."""
    cte_match = re.search(
        r"WITH\s+(\w+)\s+AS\s*\(\s*SELECT\s+(MAX|MIN)\((\w+)\)\s+AS\s+\w+\s+FROM\s+(\S+)(?:\s+\w+)?\s*\)",
        sql,
        re.IGNORECASE,
    )
    if not cte_match:
        return sql

    cte_name, func, col, table = cte_match.groups()
    func = func.upper()
    cte_end = cte_match.end()
    main_sql = sql[cte_end:].strip()

    main_pattern = re.compile(
        rf"SELECT\s+(.+?)\s+FROM\s+{re.escape(table)}\s+WHERE\s+(.+?)(?:\s+LIMIT\s+\d+)?(?:;|\s*$)",
        re.IGNORECASE | re.DOTALL,
    )
    main_match = main_pattern.search(main_sql)
    if not main_match:
        return sql

    select_clause = main_match.group(1).strip()
    where_clause  = main_match.group(2).strip()
    and_parts     = re.split(r"\s+AND\s+", where_clause, flags=re.IGNORECASE)

    cte_ref = re.compile(
        rf"^{re.escape(col)}\s*=\s*\(?\s*SELECT\s+\w+\s+FROM\s+{re.escape(cte_name)}\s*\)?$",
        re.IGNORECASE,
    )
    remaining = [p.strip() for p in and_parts if not cte_ref.match(p.strip())]

    order_dir = "DESC" if func == "MAX" else "ASC"
    where_str = f"WHERE {' AND '.join(remaining)}" if remaining else ""
    return f"SELECT {select_clause} FROM {table} {where_str} ORDER BY {col} {order_dir} LIMIT 1"


def fix_forward_references(sql: str) -> str:
    """Fix JOIN ON conditions that reference table aliases before they are defined."""
    join_re = re.compile(
        r"((?:LEFT\s+|RIGHT\s+|INNER\s+|FULL\s+|CROSS\s+)?JOIN)\s+(\S+)\s+(?:AS\s+)?(\w+)\s+ON\s+(.+?)"
        r"(?=\s+(?:LEFT\s+|RIGHT\s+|INNER\s+|FULL\s+|CROSS\s+)?JOIN\s|\s*(?:WHERE|GROUP|ORDER|LIMIT|$))",
        re.IGNORECASE | re.DOTALL,
    )
    joins = list(join_re.finditer(sql))
    if len(joins) < 2:
        return sql

    from_alias = re.search(r"FROM\s+\S+\s+(?:AS\s+)?(\w+)", sql, re.IGNORECASE)
    alias_order = [from_alias.group(1).lower()] if from_alias else []
    for m in joins:
        alias_order.append(m.group(3).lower())

    deferred: dict[str, list] = {}
    join_fixes: dict[int, list] = {}
    defined = set(alias_order[:1])

    for i, m in enumerate(joins):
        condition = m.group(4).strip()
        alias     = m.group(3).lower()
        and_parts = re.split(r"\s+AND\s+", condition, flags=re.IGNORECASE)
        ok: list[str] = []
        for part in and_parts:
            refs = re.findall(r"(\w+)\.\w+", part)
            bad  = [r for r in refs if r.lower() not in defined and r.lower() != alias]
            if bad:
                for ref in bad:
                    deferred.setdefault(ref.lower(), []).append(part)
            else:
                ok.append(part)
        join_fixes[i] = ok
        defined.add(alias)

    if not deferred:
        return sql

    result: list[str] = []
    prev = 0
    for i, m in enumerate(joins):
        result.append(sql[prev:m.start()])
        jt, tbl, alias = m.group(1), m.group(2), m.group(3)
        all_parts = join_fixes[i] + deferred.pop(alias.lower(), [])
        result.append(f'{jt} {tbl} {alias} ON {" AND ".join(all_parts)}')
        prev = m.end()
    result.append(sql[prev:])
    return "".join(result)


def fix_cte_errors(sql: str) -> str:
    """Apply all CTE / hallucination / forward-reference fixes."""
    sql_before = sql
    sql = fix_max_min_subquery(sql)
    if sql != sql_before:
        logger.info(f"MAX/MIN fix applied: {sql[:100]}")
    sql = fix_hallucinated_columns(sql)

    cte_pattern = re.compile(r"(\w+)\s+AS\s*\(", re.IGNORECASE)
    for m in reversed(list(cte_pattern.finditer(sql))):
        body_start = m.end()
        depth, i = 1, body_start
        while i < len(sql) and depth > 0:
            depth += 1 if sql[i] == "(" else (-1 if sql[i] == ")" else 0)
            i += 1
        body_end = i
        cte_body  = sql[body_start : body_end - 1]
        fixed     = fix_forward_references(fix_hallucinated_columns(cte_body))
        if fixed != cte_body:
            sql = sql[:body_start] + fixed + sql[body_end - 1 :]

    # Fix outer query
    last_paren, depth, in_cte = 0, 0, False
    for i, ch in enumerate(sql):
        if ch == "(":
            if not in_cte:
                before = sql[max(0, i - 10) : i].strip().upper()
                if before.endswith("AS"):
                    in_cte = True
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0 and in_cte:
                last_paren, in_cte = i + 1, False

    if last_paren > 0:
        outer = sql[last_paren:].strip()
        if outer:
            sql = sql[:last_paren] + "\n" + fix_forward_references(outer)

    return sql


# ── CURRENT_DATE replacement ───────────────────────────────────────────────────
def fix_current_date(sql: str, manifests: dict | None = None) -> str:
    """Replace CURRENT_DATE with MAX(timestamp) subquery for historical datasets."""
    if "CURRENT_DATE" not in sql.upper():
        return sql

    date_col = date_table = None
    if manifests:
        for table_name, manifest in manifests.items():
            for col_name, info in manifest.get("profiles", {}).items():
                if info.get("type") == "BIGINT" and (
                    "date" in col_name.lower() or "time" in col_name.lower()
                ):
                    date_col, date_table = col_name, table_name
                    break
            if date_col:
                break

    if date_col and date_table:
        replacement = f"(SELECT TO_TIMESTAMP(MAX({date_col})) FROM {date_table})"
        sql = re.sub(r"CURRENT_DATE", replacement, sql, flags=re.IGNORECASE)
        logger.info(f"Fixed CURRENT_DATE → {replacement}")
    else:
        logger.warning("CURRENT_DATE found but no date column detected in manifests")

    return sql


# ── Filter value correction ────────────────────────────────────────────────────
def fix_filter_values(sql: str, db_conn: Any, table_name: str, manifest: dict | None = None) -> str:
    """Fix WHERE-clause values that don't match actual database values."""
    if not manifest:
        return sql

    def similarity(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio()

    def strip_plural(s: str) -> str:
        if s.endswith("ies") and len(s) > 4:
            return s[:-3] + "y"
        if s.endswith(("ses", "xes", "zes")):
            return s[:-2]
        if s.endswith("s") and not s.endswith("ss") and len(s) > 3:
            return s[:-1]
        return s

    def get_all_db_values(col: str) -> list[str]:
        try:
            rows = db_conn.execute(
                f"SELECT DISTINCT CAST({col} AS VARCHAR) FROM {table_name} WHERE {col} IS NOT NULL"
            ).fetchall()
            return [str(r[0]).lower() for r in rows if r[0]]
        except Exception:
            return []

    _TRANSLATIONS = {
        "золото": "yellowgold", "белое золото": "whitegold",
        "розовое золото": "rosegold", "серебро": "silver",
        "мужской": "men", "женский": "women", "детский": "kids",
        "франция": "france", "италия": "italy", "япония": "japan",
        "швейцария": "switzerland", "германия": "germany", "китай": "china",
        "россия": "russia", "сша": "usa", "великобритания": "uk",
    }

    filter_pat = re.compile(
        r"(?:LOWER\s*\(\s*(\w+)\s*\)|(\w+))\s*(?:=\s*'([^']*)'|LIKE\s+'%?([^']*?)%?')",
        re.IGNORECASE,
    )
    profiles = manifest.get("profiles", {})
    result = sql

    for m in filter_pat.finditer(sql):
        col_name     = (m.group(1) or m.group(2)).lower()
        filter_value = (m.group(3) or m.group(4) or "").lower()
        if not filter_value or len(filter_value) < 2:
            continue

        samples: list[str] = []
        for prof_col, profile in profiles.items():
            if prof_col.lower() == col_name:
                samples = [str(s).lower() for s in profile.get("sample_values", [])]
                break

        if len(samples) < 20 and db_conn:
            db_vals = get_all_db_values(col_name)
            if db_vals:
                samples = list(set(samples + db_vals))

        if not samples or filter_value in samples:
            continue

        # Substring match
        partial = [s for s in samples if filter_value in s or s in filter_value]
        if partial:
            result = result.replace(m.group(0), m.group(0).replace(filter_value, partial[0]), 1)
            logger.info(f"Fixed filter (substring): '{filter_value}' → '{partial[0]}'")
            continue

        # Plural match
        stripped = strip_plural(filter_value)
        if stripped != filter_value:
            plural = [s for s in samples if s == stripped or s.startswith(stripped)]
            if plural:
                result = result.replace(m.group(0), m.group(0).replace(filter_value, plural[0]), 1)
                logger.info(f"Fixed filter (plural): '{filter_value}' → '{plural[0]}'")
                continue

        # Fuzzy match (threshold 0.7)
        fuzzy = sorted([(s, similarity(filter_value, s)) for s in samples], key=lambda x: x[1], reverse=True)
        if fuzzy and fuzzy[0][1] >= 0.7:
            result = result.replace(m.group(0), m.group(0).replace(filter_value, fuzzy[0][0]), 1)
            logger.info(f"Fixed filter (fuzzy): '{filter_value}' → '{fuzzy[0][0]}' ({fuzzy[0][1]:.2f})")
            continue

        # Translation
        for ru, en in _TRANSLATIONS.items():
            if ru in filter_value and en in samples:
                result = result.replace(m.group(0), m.group(0).replace(filter_value, en), 1)
                logger.info(f"Fixed filter (translate): '{filter_value}' → '{en}'")
                break

    return result


# ── Table name remapping ───────────────────────────────────────────────────────
def remap_table_names(sql: str, name_map: dict[str, str]) -> str:
    """Replace original table names with DuckDB dataset_xxx names in FROM/JOIN clauses."""
    if not name_map:
        return sql
    for orig_name in sorted(name_map.keys(), key=len, reverse=True):
        duck_name = name_map[orig_name]
        sql = re.sub(
            r"((?:FROM|JOIN)\s+)" + re.escape(orig_name) + r"(\s+)",
            r"\g<1>" + duck_name + r"\2",
            sql,
            flags=re.IGNORECASE,
        )
    return sql
