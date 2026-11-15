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


# ── Regex helpers ──────────────────────────────────────────────────────────────
# «всего» сюда не входит: «больше всего», «сколько всего» — это про максимум/итог, а не про список
_ALL_KEYWORDS = re.compile(
    r"\b(все|всё|всех|весь|вся|полный\s+список|полностью)\b", re.IGNORECASE
)
# Обороты со словами «все/весь», которые не просят полный список
_ALL_FALSE_POSITIVES = re.compile(
    # «за весь год», «за все годы», «за весь 2024 год», «за всё время», «за весь период»…
    r"\b(?:за\s+(?:все|всё|весь)\s+(?:\d{4}\s+)?"
    r"(?:время|период\w*|год\w*|квартал\w*|месяц\w*|недел\w*|дн\w*|сезон\w*|истори\w*)"
    r"|(?:среди|из)\s+всех"
    r"|(?:больше|меньше|чаще|реже|дороже|дешевле|лучше|хуже)\s+всех)\b",
    re.IGNORECASE,
)
_CURRENT_QUESTION_MARKER = "ТЕКУЩИЙ ВОПРОС:"
# Внешний LIMIT — тот, которым заканчивается запрос (LIMIT внутри CTE/подзапроса закрыт скобкой).
# После него допускаются «;» и комментарии: adjust_limit работает до sanitize_sql, который их вырезает.
# Хвост из комментариев отрезаем токенайзером, а не одной регуляркой: вложенное повторение
# (?:\s*--[^\n]*)* на строке из дефисов давало экспоненциальный перебор и вешало event loop
_OUTER_LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)(\s+OFFSET\s+\d+)?\s*\Z", re.IGNORECASE)
# Каждая альтернатива начинается со своего символа и не перекрывается с другими — разбор линейный.
# Незакрытые строка/комментарий забираются до конца текста, чтобы не сканировать его повторно
_SQL_TOKEN_RE = re.compile(
    r"'(?:[^']|'')*(?:'|\Z)"
    r'|"(?:[^"]|"")*(?:"|\Z)'
    r"|--[^\n]*"
    r"|/\*[\s\S]*?(?:\*/|\Z)"
    r"|\s+"
    r"|[^'\"\s;/-]+"
    r"|[\s\S]"
)


def _code_end(sql: str) -> int:
    """Позиция сразу после последнего значимого токена: дальше только пробелы, «;» и комментарии."""
    end = 0
    for tok in _SQL_TOKEN_RE.finditer(sql):
        text = tok.group()
        is_noise = (
            text.isspace()
            or text == ";"
            or text.startswith("--")
            or (text.startswith("/*") and text.endswith("*/") and len(text) >= 4)
        )
        if not is_noise:
            end = tok.end()
    return end


_ALL_ROWS_LIMIT = 500

# ── Extraction ─────────────────────────────────────────────────────────────────
def extract_sql(raw: str) -> str:
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


# ── Limit adjustment ───────────────────────────────────────────────────────────
def _current_question(question: str) -> str:
    # В question может прийти история диалога: смотрим только на текущий вопрос
    if _CURRENT_QUESTION_MARKER in question:
        question = question.rsplit(_CURRENT_QUESTION_MARKER, 1)[1]
    return question


def _asks_for_all(question: str) -> bool:
    question = _current_question(question)
    return bool(_ALL_KEYWORDS.search(_ALL_FALSE_POSITIVES.sub(" ", question)))


def adjust_limit(question: str, sql: str) -> str:
    """Raise the outer LIMIT to 500 when user asks for 'all' items.

    LIMIT внутри CTE и подзапросов не трогаем: там LIMIT 1 выбирает «самого»
    (WITH top AS (... LIMIT 1) ... WHERE x = (SELECT x FROM top)), и после замены
    скалярный подзапрос возвращает много строк. LIMIT 1 снаружи — это ответ
    одним значением, его тоже не расширяем.
    """
    if not sql or not _asks_for_all(question):
        return sql
    match = _OUTER_LIMIT_RE.search(sql, 0, _code_end(sql))
    if not match or not (1 < int(match.group(1)) < _ALL_ROWS_LIMIT):
        return sql
    # Число из LIMIT пользователь назвал сам («топ 5 товаров за весь май», «топ-5 во всех магазинах») —
    # по правилу промпта это число и остаётся, «весь/всех» там не про полный список
    if re.search(rf"(?<!\d){match.group(1)}(?!\d)", _current_question(question)):
        return sql
    return sql[: match.start(1)] + str(_ALL_ROWS_LIMIT) + sql[match.end(1):]


# ── CTE / hallucination fixes ──────────────────────────────────────────────────
def fix_max_min_subquery(sql: str) -> str:
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


def fix_forward_references(sql: str) -> str:
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


def fix_cte_errors(sql: str) -> str:
    """Fix common errors in multi-step CTE queries, including inside CTE bodies."""
    sql_before = sql
    sql = fix_max_min_subquery(sql)
    if sql != sql_before:
        logger.info(f"MAX/MIN fix applied: {sql[:100]}")
    # Замен «под демо-базу» (cu.region → st.store_name, pu.quantity → p2.product_count) больше нет:
    # на любых других данных они превращали верный запрос в ошибку, которую модель не могла исправить

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
            fixed_body = fix_forward_references(cte_body)

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
            fixed_outer = fix_forward_references(outer_query)
            sql = sql[:last_paren] + '\n' + fixed_outer

    return sql


# ── CURRENT_DATE replacement ───────────────────────────────────────────────────
# CURRENT_DATE() и CURRENT_TIMESTAMP() в DuckDB тоже допустимы — скобки забираем вместе с именем,
# иначе оставалось «(SELECT …)()». Идентификаторы вроде current_date_flag и t.current_date не трогаем
_NOW_RE = re.compile(
    r'(?<![\w."])(?:CURRENT_DATE|CURRENT_TIMESTAMP)(?:\s*\(\s*\))?(?!\w)'
    r'|(?<![\w."])(?:NOW|TODAY)\s*\(\s*\)',
    re.IGNORECASE,
)
_INT_TYPES = ("BIGINT", "INTEGER", "HUGEINT", "UBIGINT")


def _date_candidate(col: str, col_type: str) -> tuple[int, str] | None:
    """(приоритет, выражение последнего момента в данных как TIMESTAMP); меньше — лучше, None — не дата.

    Ingest хранит даты строками, поэтому VARCHAR с «date» в имени надёжнее целого числа:
    целая колонка с «time» в имени чаще длительность (delivery_time), чем UNIX-время.
    """
    quoted = '"' + col.replace('"', '""') + '"'
    col_type = (col_type or "").upper()
    name = col.lower()
    if "DATE" in col_type or "TIMESTAMP" in col_type:
        return 0, f"CAST(MAX({quoted}) AS TIMESTAMP)"
    strong = any(h in name for h in ("date", "дата", "timestamp")) or name.endswith("_at")
    weak = any(h in name for h in ("time", "время"))
    if not (strong or weak):
        return None
    hint = 0 if strong else 1
    if col_type == "VARCHAR":
        from app.services.analytics import text_date

        # «15.11.2025» TRY_CAST не разбирает: конец данных был NULL, и период по нему ничего не находил
        return 1 + hint, f"MAX({text_date(quoted)})"
    if col_type in _INT_TYPES:
        return 3 + hint, f"TO_TIMESTAMP(MAX({quoted}))"
    return None


def fix_current_date(sql: str, manifests: dict = None) -> str:
    """Replace CURRENT_DATE / NOW() with the latest date in the data (historical datasets).

    Промпты запрещают CURRENT_DATE, это страховка на случай, если модель его всё же написала.
    Раньше подстановка работала только для дат в BIGINT, а для DATE и VARCHAR
    «за прошлый год» считался от сегодняшнего дня и давал пустой результат.
    """
    if not _NOW_RE.search(sql):
        return sql

    candidates: list[tuple[int, int, str, str]] = []  # (приоритет, порядок, таблица, выражение)
    for key, manifest in (manifests or {}).items():
        # Ключ словаря — id набора (UUID), а не имя таблицы: подстановка «FROM 3f2a…»
        # давала ParserException. Имя таблицы лежит в самом манифесте
        if not manifest:
            continue
        table_name = manifest.get('table_name') or key
        # Колонка даты, которую выбрал preanalysis, — по ней же analytics.month_periods считает «сейчас»
        mapped = str((manifest.get('column_mapping') or {}).get('date') or '').lower()
        for col_name, info in manifest.get('profiles', {}).items():
            found = _date_candidate(col_name, info.get('type'))
            if found:
                priority = -1 if col_name.lower() == mapped else found[0]
                candidates.append((priority, len(candidates), table_name, found[1]))

    # В группе таблиц берём дату из таблиц самого запроса: birth_date клиентов
    # или дата из несвязанного справочника сдвигали бы период
    used = [c for c in candidates if re.search(rf"\b{re.escape(c[2])}\b", sql, re.IGNORECASE)]
    pool = used or candidates
    if not pool:
        logger.warning("CURRENT_DATE found but no date column detected in manifests")
        return sql

    _, _, table_name, latest = min(pool)
    latest_ts = f"(SELECT {latest} FROM {table_name})"
    latest_date = f"(SELECT CAST({latest} AS DATE) FROM {table_name})"

    def _replace(m: re.Match) -> str:
        # CURRENT_DATE/TODAY — это DATE: TIMESTAMP(TZ) на их месте ломал «CURRENT_DATE - 1»
        # и сравнение с DATE; NOW/CURRENT_TIMESTAMP — момент времени, обрезать до суток нельзя
        return latest_date if m.group().upper().startswith(("CURRENT_DATE", "TODAY")) else latest_ts

    sql = _NOW_RE.sub(_replace, sql)
    logger.info(f"Fixed CURRENT_DATE → {latest_date}")
    return sql


# ── Filter value correction ────────────────────────────────────────────────────
def fix_filter_values(sql: str, db_conn, table_name: str, manifest: dict = None) -> str:
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

    def _get_all_values(col: str) -> list[str] | None:
        """Все значения колонки (в исходном регистре). None — их слишком много для подбора.

        Колонка номеров чеков на 130 тыс. значений: подбор по сходству занимал секунду на каждый
        литерал (весь сервер стоял) и подменял номер на соседний.
        """
        try:
            rows = db_conn.execute(
                f"SELECT DISTINCT CAST({col} AS VARCHAR) FROM {table_name} WHERE {col} IS NOT NULL LIMIT 2001"
            ).fetchall()
        except Exception:
            return []
        if len(rows) > 2000:
            return None
        return [str(r[0]) for r in rows if r[0]]

    def _digits(v: str) -> str:
        return "".join(re.findall(r"\d+", v))

    filter_pattern = re.compile(
        r"(?:LOWER\s*\(\s*(\w+)\s*\)|(\w+))\s*(?:=\s*'([^']*)'|LIKE\s+'%?([^']*?)%?')",
        re.IGNORECASE
    )

    # Длинные фразы раньше коротких: «белое золото» не должно стать «золотом»
    translations = sorted({
        'золото': 'yellowgold', 'белое золото': 'whitegold', 'розовое золото': 'rosegold',
        'желтое золото': 'yellowgold', 'серебро': 'silver',
        'мужской': 'men', 'женский': 'women', 'детский': 'kids',
        'франция': 'france', 'италия': 'italy', 'япония': 'japan',
        'швейцария': 'switzerland', 'германия': 'germany', 'китай': 'china',
        'россия': 'russia', 'сша': 'usa', 'великобритания': 'uk',
    }.items(), key=lambda kv: len(kv[0]), reverse=True)

    profiles = manifest.get('profiles', {})
    replacements: list[tuple[int, int, str]] = []

    for m in filter_pattern.finditer(sql):
        # LIKE — намеренно частичное совпадение: подмена на одно значение отбрасывала
        # остальные строки (LIKE '%gold%' → '%rosegold%'). Исправляем только «=»
        if m.group(3) is None:
            continue
        col_name = (m.group(1) or m.group(2)).lower()
        filter_value = m.group(3).lower()
        lowered_col = m.group(1) is not None

        if not filter_value or len(filter_value) < 2:
            continue

        # Get samples from manifest, supplemented by DB query
        originals: dict[str, str] = {}
        for prof_col, profile in profiles.items():
            if prof_col.lower() == col_name:
                for v in profile.get('sample_values', []):
                    originals.setdefault(str(v).lower(), str(v))
                break

        # If samples are limited, query DB for all distinct values
        if len(originals) < 20 and db_conn:
            all_values = _get_all_values(col_name)
            if all_values is None:
                continue  # почти уникальная колонка (номера, коды): подбирать значение бессмысленно
            for v in all_values:
                originals.setdefault(v.lower(), v)

        if not originals:
            continue
        samples = sorted(originals)  # порядок не зависит от set — результат детерминирован

        # 1. Exact match. Без LOWER() сравнение в DuckDB регистрозависимое: «= 'алматы'» при значении
        # «Алматы» ничего не находило — подставляем значение в исходном регистре
        if filter_value in samples:
            if not lowered_col and originals[filter_value] != m.group(3):
                replacements.append((m.start(3), m.end(3), originals[filter_value].replace("'", "''")))
            continue

        # Значение с цифрами (артикул, номер) подменяем только на значение с теми же цифрами:
        # 'A-11', которого нет, не должен стать существующим 'A-1'
        digits = _digits(filter_value)
        if digits:
            samples = [s for s in samples if _digits(s) == digits]
            if not samples:
                continue

        best = None
        how = ''
        # 2. Substring match — самое похожее из подходящих. Длины близки: «Часы Rolex Daytona»
        # не должен стать «Часы Rolex» (другой товар), а «Астана» — «г. Астана» может
        partial = [s for s in samples if (filter_value in s or s in filter_value)
                   and min(len(s), len(filter_value)) / max(len(s), len(filter_value)) >= 0.6]
        if partial:
            best, how = max(partial, key=lambda s: (_similarity(filter_value, s), -len(s))), 'substring'
        # 3. Plural/suffix match
        if best is None:
            stripped = _strip_plural(filter_value)
            if stripped != filter_value:
                plural = [s for s in samples if s == stripped or s.startswith(stripped)]
                if plural:
                    best, how = plural[0], 'plural'
        # 4. Fuzzy match
        if best is None:
            fuzzy = sorted(((s, _similarity(filter_value, s)) for s in samples), key=lambda x: (-x[1], x[0]))
            if fuzzy and fuzzy[0][1] >= 0.7:
                best, how = fuzzy[0][0], 'fuzzy'
        # 5. Russian → English translation
        if best is None:
            for ru, en in translations:
                if ru in filter_value and en in samples:
                    best, how = en, 'translate'
                    break

        if best is None:
            continue
        # Меняем только сам литерал (раньше replace по всему фрагменту мог задеть имя колонки);
        # без LOWER() сравнение регистрозависимое — берём значение в исходном регистре
        value = best if lowered_col else originals.get(best, best)
        replacements.append((m.start(3), m.end(3), value.replace("'", "''")))
        logger.info(f"Fixed filter ({how}): '{filter_value}' → '{value}'")

    result = sql
    for start, end, value in reversed(replacements):
        result = result[:start] + value + result[end:]
    return result


# ── Table name remapping ───────────────────────────────────────────────────────
#: Слова, которые могут идти сразу после имени таблицы и не являются псевдонимом
_SQL_FOLLOW_WORDS = {"WHERE", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "FULL", "CROSS", "ON", "GROUP", "ORDER",
                     "LIMIT", "HAVING", "UNION", "EXCEPT", "INTERSECT", "WINDOW", "QUALIFY", "USING", "NATURAL"}


def remap_table_names(sql: str, name_map: dict[str, str]) -> str:
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
        def _sub(m: re.Match, _orig=orig_name, _duck=duck_name) -> str:
            # Без псевдонима («FROM Остатки WHERE Остатки.артикул …») исходное имя остаётся псевдонимом:
            # иначе квалификатор «Остатки.артикул» ссылался на таблицу, которой после замены нет
            rest = sql[m.end():]
            nxt = re.match(r'\s*(\w+)', rest)
            has_alias = bool(nxt) and nxt.group(1).upper() not in _SQL_FOLLOW_WORDS
            return m.group(1) + _duck + ("" if has_alias else f' AS "{_orig}"')

        # Имя листа — как есть, в кавычках («"Остатки на складе"», единственная верная форма для имени
        # с пробелом) или с подчёркиваниями вместо пробелов. Раньше узнавалась только голая форма,
        # и запрос отвергался как обращение к запрещённой таблице
        variants = {orig_name, orig_name.replace(" ", "_")}
        alt = "|".join('"' + re.escape(v) + '"|' + re.escape(v) for v in sorted(variants, key=len, reverse=True))
        sql = re.sub(
            # После имени может идти пробел, «)», «,», «;» или конец запроса:
            # раньше требовался пробел, и FROM purchases) оставался без замены
            r'((?:FROM|JOIN)\s+)(?:' + alt + r')(?=[\s),;]|$)',
            _sub,
            sql,
            flags=re.IGNORECASE
        )

        # Список таблиц через запятую: «FROM Продажи t1, Возвраты t2» — вторая таблица тоже
        def _comma(m: re.Match, _orig=orig_name, _duck=duck_name) -> str:
            before = sql[: m.start()]
            kw = re.findall(r'\b(SELECT|FROM|WHERE|GROUP|ORDER|HAVING|LIMIT|ON|JOIN)\b', before, flags=re.IGNORECASE)
            if not kw or kw[-1].upper() != "FROM":
                return m.group(0)
            rest = sql[m.end():]
            nxt = re.match(r'\s*(\w+)', rest)
            has_alias = bool(nxt) and nxt.group(1).upper() not in _SQL_FOLLOW_WORDS
            return m.group(1) + _duck + ("" if has_alias else f' AS "{_orig}"')

        sql = re.sub(r'(,\s*)(?:' + alt + r')(?=[\s),;]|$)', _comma, sql, flags=re.IGNORECASE)

    return sql


# ── Ремонт неизвестных колонок по ошибке DuckDB ───────────────────────────────
_MISSING_COL_RES = (
    re.compile(r'Referenced column "([^"]+)" not found'),
    re.compile(r'does not have a column (?:named|with name) "([^"]+)"'),
    re.compile(r'Column "([^"]+)" not found'),
)


def _walk_refs(node, refs: list[str], aliases: set[str]) -> None:
    if isinstance(node, dict):
        if node.get("class") == "COLUMN_REF" and node.get("column_names"):
            refs.append(str(node["column_names"][-1]))
        if node.get("alias"):
            aliases.add(str(node["alias"]).lower())
        for key in ("column_name_alias", "aliases"):
            for a in node.get(key) or []:
                if isinstance(a, str):
                    aliases.add(a.lower())
        for v in node.values():
            _walk_refs(v, refs, aliases)
    elif isinstance(node, list):
        for v in node:
            _walk_refs(v, refs, aliases)


def unknown_column(sql: str, error: str, columns: list[str]) -> str | None:
    """Имя колонки, которой нет в таблицах: по тексту ошибки DuckDB, а если формулировка
    сменилась — по разбору запроса (json_serialize_sql) и сверке со списком колонок.
    """
    missing = next((m.group(1) for rx in _MISSING_COL_RES for m in [rx.search(error or "")] if m), None)
    if missing or "binder" not in (error or "").lower():
        return missing
    try:
        import json

        import duckdb

        con = duckdb.connect(":memory:")
        try:
            tree = json.loads(con.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()[0])
        finally:
            con.close()
    except Exception:
        return None
    if tree.get("error"):
        return None
    refs: list[str] = []
    aliases: set[str] = set()
    _walk_refs(tree.get("statements"), refs, aliases)
    known = {c.lower() for c in columns} | aliases
    unknown = list(dict.fromkeys(r for r in refs if r.lower() not in known))
    # Имя в кавычках из ошибки, если оно среди неизвестных; иначе — только единственная неизвестная
    quoted = [q for q in re.findall(r'"([^"]+)"', error) if q in unknown]
    if quoted:
        return quoted[0]
    return unknown[0] if len(unknown) == 1 else None


def fix_unknown_column(sql: str, error: str, columns: list[str]) -> str | None:
    """Заменить колонку, которой нет, на ближайшую существующую. None — замены не нашлось.

    Модель 7B искажает имена: «дата__» вместо «дата_текст», «brand» вместо «бренд». DuckDB называет
    неизвестную колонку — подставляем похожую реальную и выполняем снова без модели.
    Замена — только уверенная: начало имени (от 4 букв), полный перевод EN→RU или очень близкое
    написание. Раньше хватало одной буквы перевода («прибыль» → колонка «п_п») или сходства 0,6
    («amount» → «count»), и запрос молча считал не ту колонку.
    """
    from app.services.column_dict import COLUMN_TRANSLATIONS

    missing = unknown_column(sql, error, columns) if columns else None
    if not missing:
        return None
    bare = missing.split(".")[-1].strip('"')
    low = bare.lower()
    norm = re.sub(r"_+", "_", low).strip("_")
    translated = COLUMN_TRANSLATIONS.get(norm, "").lower().replace(" ", "_")
    candidates = []
    for col in columns:
        cl = col.lower()
        score = 0.0
        same_script = bool(re.search(r"[a-z]", norm)) == bool(re.search(r"[a-z]", cl))
        if same_script:
            ratio = SequenceMatcher(None, norm, cl).ratio()
            if ratio >= 0.8:
                score = ratio
        shorter = min(len(norm), len(cl))
        if norm and shorter >= 4 and (cl.startswith(norm) or norm.startswith(cl)):
            score = max(score, 0.9)
        if translated and (cl == translated or (len(translated) >= 4 and cl.startswith(translated))):
            score = max(score, 0.85)
        candidates.append((score, col))
    score, best = max(candidates)
    if score < 0.8:
        return None
    # Заменяем целое имя вне строковых литералов: «WHERE магазин = 'Магазин №1'» — значение не трогаем
    word = re.compile(r'(?<![\w"])' + re.escape(bare) + r'(?![\w"])', re.IGNORECASE)
    quoted = re.compile(r'"' + re.escape(bare) + r'"', re.IGNORECASE)
    parts = re.split(r"('(?:[^']|'')*')", sql)
    for i in range(0, len(parts), 2):
        parts[i] = quoted.sub(lambda _m: f'"{best}"', word.sub(lambda _m: best, parts[i]))
    fixed = "".join(parts)
    return fixed if fixed != sql else None
