from __future__ import annotations
import pandas as pd
import duckdb
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Column name patterns that indicate a date field
DATE_COLUMN_PATTERNS = [
    'date', 'time', 'timestamp', 'created', 'updated', 'deleted',
    'start', 'end', 'begin', 'finish', 'expire', 'due',
    'birth', 'death', 'published', 'scheduled',
    # Русские заголовки: «Дата продажи», «Время заказа» раньше оставались строкой
    'дата', 'время', 'срок',
]

# Значение похоже на дату: 2026-09-08, 2026-09-08T10:00, 08.09.2026, 08/09/2026
_DATE_VALUE_RE = re.compile(r"^\s*(\d{4}-\d{1,2}-\d{1,2}([ T]\d{1,2}:\d{2}(:\d{2})?(\.\d+)?)?|\d{1,2}[./]\d{1,2}[./]\d{4}( \d{1,2}:\d{2}(:\d{2})?)?)\s*$")


def looks_like_dates(s: pd.Series, min_share: float = 0.8) -> bool:
    """Текстовая колонка, где большинство значений — даты, при любом имени («период», «отгружено»)."""
    sample = s.dropna().astype(str).head(200)
    if len(sample) == 0:
        return False
    return sample.map(lambda v: bool(_DATE_VALUE_RE.match(v))).mean() >= min_share

def detect_column_type(col_name: str, dtype) -> str:
    """Detect column type from pandas dtype and column name.

    Uses both the actual dtype and column name heuristics to correctly
    identify date columns like 'delivery_date', 'purchase_date', etc.
    """
    type_str = str(dtype).lower()

    # Already datetime type
    if 'datetime' in type_str or 'timestamp' in type_str:
        return 'date'

    # Name-based detection for date columns (before numeric check!)
    col_lower = col_name.lower().strip()
    for pattern in DATE_COLUMN_PATTERNS:
        if pattern in col_lower:
            return 'date'

    # Numeric
    if 'int' in type_str or 'float' in type_str:
        return 'numeric'

    return 'string'


def _clean_date_series(s: pd.Series) -> pd.Series:
    """Clean a date column: strip timezone, normalize to YYYY-MM-DDTHH:MM:SS.

    Handles: datetime64[ns, tz], ISO strings with +HH:MM, Unix timestamps.
    """
    if s.dtype == 'object':
        # String column — strip timezone suffix and try to parse
        def strip_tz(val):
            if pd.isna(val):
                return val
            val_str = str(val).strip()
            # Remove timezone suffix like +00:00, +03:00, Z
            val_str = re.sub(r'[+-]\d{2}:\d{2}$', '', val_str)
            val_str = re.sub(r'Z$', '', val_str)
            # If it looks like a Unix timestamp (all digits), convert
            if re.match(r'^\d{10,13}$', val_str):
                ts = int(val_str)
                if ts > 1e12:  # milliseconds
                    ts = ts / 1000
                return pd.Timestamp(ts, unit='s').strftime('%Y-%m-%dT%H:%M:%S')
            return val_str
        s = s.apply(strip_tz)
        return _parse_date_strings(s)

    # datetime64 with timezone — strip tz
    if hasattr(s.dt, 'tz') and s.dt.tz is not None:
        s = s.dt.tz_localize(None)

    return s


_TIME_ONLY_RE = r'\d{1,2}:\d{2}(:\d{2})?'
# Формат — для каждого значения отдельно: в выгрузке рядом бывают «2026-07-15 00:00:00» (ячейка-дата)
# и «15.07.2026» (ячейка-текст). Раньше формат брался по первым значениям, и остальные становились пустыми
_DATE_FORMATS = [
    (r'\d{1,2}\.\d{1,2}\.\d{4}', '%d.%m.%Y'),
    (r'\d{1,2}\.\d{1,2}\.\d{4} \d{1,2}:\d{2}', '%d.%m.%Y %H:%M'),
    (r'\d{1,2}\.\d{1,2}\.\d{4} \d{1,2}:\d{2}:\d{2}', '%d.%m.%Y %H:%M:%S'),
    # Двузначный год — явно: «31.12.24» — 2024 год, а не 0024
    (r'\d{1,2}\.\d{1,2}\.\d{2}', '%d.%m.%y'),
]
_SLASH_RE = r'(\d{1,2})/(\d{1,2})/(\d{4})( \d{1,2}:\d{2}(:\d{2})?)?'


def _parse_date_strings(s: pd.Series) -> pd.Series:
    """Строки → даты. День первым для «дд.мм.гггг» и «дд/мм/гггг» (если по данным не видно обратного)."""
    text = s.where(s.notna(), None)
    as_str = text.dropna().astype(str).str.strip()
    if len(as_str) and as_str.str.fullmatch(_TIME_ONLY_RE).mean() > 0.5:
        return s  # «10:30» — время без даты: дата загрузки к нему не относится
    out = pd.Series(pd.NaT, index=s.index, dtype='datetime64[ns]')
    rest = as_str.copy()
    for pattern, fmt in _DATE_FORMATS:
        mask = rest.str.fullmatch(pattern)
        if mask.any():
            out.loc[rest[mask].index] = pd.to_datetime(rest[mask], format=fmt, errors='coerce')
            rest = rest[~mask]
    slash = rest.str.fullmatch(_SLASH_RE)
    if slash.any():
        parts = rest[slash].str.extract(_SLASH_RE)
        first, second = parts[0].astype(int), parts[1].astype(int)
        # «05/11/2025»: если где-то первое число больше 12 — день первым; если второе — месяц первым;
        # если не видно — день первым (русские выгрузки). Раньше pandas решал по первому значению
        dayfirst = not ((second > 12).any() and not (first > 12).any())
        out.loc[rest[slash].index] = pd.to_datetime(rest[slash], dayfirst=dayfirst, errors='coerce')
        rest = rest[~slash]
    if len(rest):
        out.loc[rest.index] = pd.to_datetime(rest, format='ISO8601', errors='coerce')
        left = out.loc[rest.index].isna()
        if left.any():
            out.loc[rest[left].index] = pd.to_datetime(rest[left], errors='coerce')
    return out


#: Доля распознанных значений, начиная с которой колонка с «датным» именем считается датой
DATE_PARSE_MIN_SHARE = 0.8


def _clean_dates_in_df(df: pd.DataFrame, columns_meta: list[dict]) -> pd.DataFrame:
    """Привести колонки дат к datetime — в DuckDB они станут TIMESTAMP.

    Раньше дата после разбора снова превращалась в строку: в таблице получался VARCHAR,
    и модель писала `date - INTERVAL 30 DAY` или `DATE_TRUNC('month', date)` — DuckDB падал.
    Колонку с «датным» именем, где дат почти нет («spend», «trend»), не трогаем:
    разбор с errors='coerce' превратил бы её значения в пустые.
    """
    for col, dtype in df.dtypes.items():
        meta_type = detect_column_type(str(col), dtype)
        if meta_type != 'date' and not (dtype == 'object' and looks_like_dates(df[col])):
            continue
        try:
            parsed = _clean_date_series(df[col])
            non_null = int(df[col].notna().sum())
            if non_null and int(parsed.notna().sum()) / non_null < DATE_PARSE_MIN_SHARE:
                logger.info(f"Column '{col}' looks like a date by name, but values are not dates — kept as is")
                continue
            df[col] = parsed
        except Exception as e:
            logger.warning(f"Failed to clean date column '{col}': {e}")
    return df


# Сигнатуры книг Excel: .xlsx — ZIP-архив, .xls (Excel 97–2003) — составной документ OLE2
_ZIP_SIGNATURE = b'PK\x03\x04'
_OLE2_SIGNATURE = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'


def _excel_engine(file_path: str) -> str:
    with open(file_path, 'rb') as f:
        signature = f.read(len(_OLE2_SIGNATURE))
    if signature.startswith(_ZIP_SIGNATURE):
        return 'openpyxl'
    if signature == _OLE2_SIGNATURE:
        return 'xlrd'
    raise ValueError(
        "Файл не похож на книгу Excel (.xlsx или .xls). "
        "Если это выгрузка из другой системы, откройте её в Excel и сохраните как .xlsx или CSV"
    )


def read_excel_file(file_path: str) -> pd.DataFrame:
    """Читает книгу Excel: .xlsx через openpyxl, .xls (Excel 97–2003) через xlrd.

    Движок выбирается по содержимому, а не по расширению: выгрузки нередко
    сохраняют .xlsx под именем .xls и наоборот.
    """
    engine = _excel_engine(file_path)
    try:
        tables = read_excel_tables(file_path)
        if tables:
            # Одна таблица на книгу: самая большая (в выгрузке 1С — обычно продажи)
            return max(tables.values(), key=len)
        return pd.read_excel(file_path, engine=engine)
    except UnicodeDecodeError as e:
        # Иначе ошибка ушла бы в ветку чтения CSV в cp1251 и выглядела бы как «не тот кодек»
        raise ValueError(
            "Не удалось прочитать текст в книге Excel: неверная кодировка. "
            "Откройте файл в Excel и сохраните как .xlsx или CSV"
        ) from e


# ── Книги Excel из учётных систем (1С и т.п.) ─────────────────────────────────
# Выгрузка 1С: несколько листов, над таблицей — служебные строки («Организация», «Отчёт»,
# «Период»), внизу — «Итого». Раньше читался только первый лист с шапкой из первой строки:
# в базу попадал лист-справка, а «Итого» удваивало суммы.

# Ячейка — целиком подпись итога («Итого», «Итого по складу:», «Общий итог», «Grand total»), а не товар
# «Total Quartz 9000» или клиент «Всего Хорошего ТОО»: раньше такие строки удалялись как итоговые
_TOTAL_RE = re.compile(r"^\s*(итого|всего|итог|общий итог|grand total|total|subtotal)(\s+(по|за|for)\b.*)?[\s:.,]*$",
                       re.IGNORECASE)
_DASH_VALUES = {"—", "–", "-", "−", "", "nan", "none", "null", "н/д", "n/a"}
# Целым словом: «Сумма по счету», «Расчётная сумма», «Кабинет» — не идентификаторы (раньше совпадало
# по подстроке, колонка не переводилась в число и DuckDB угадывал INTEGER, теряя копейки)
_ID_NAME_RE = re.compile(r"(^|[\s_.№#-])(бин|иин|инн|код|номер|артикул|телефон|phone|id|code|number|sku|barcode|штрихкод|ean)"
                         r"($|[\s_.:,-])", re.IGNORECASE)


def _is_label(v) -> bool:
    return isinstance(v, str) and v.strip() != "" and not _looks_number(v)


def _looks_number(v: str) -> bool:
    t = re.sub(r"[\s\u00a0\u202f]", "", str(v)).replace(",", ".")
    try:
        float(t)
        return True
    except ValueError:
        return False


def _is_header_cell(v) -> bool:
    """Подпись колонки: текст или год («2024» в шапке «Товар | 2023 | 2024 | 2025»)."""
    if _is_label(v):
        return True
    try:
        f = float(str(v).strip())
        return f.is_integer() and 1990 <= f <= 2100
    except ValueError:
        return False


def find_header_row(raw: pd.DataFrame, max_scan: int = 60) -> int | None:
    """Номер строки-шапки: почти все ячейки — подписи, а ниже идут данные.

    Из подходящих строк берётся самая широкая: блок «Период: | 01.07–31.07 | Склад: | Основной» над
    таблицей из пяти колонок раньше принимался за шапку, а настоящая шапка уходила в данные.
    """
    if raw.empty:
        return None
    width = int(raw.notna().any(axis=0).sum())
    best, best_len = None, 0
    for i in range(min(max_scan, len(raw) - 1)):
        row = raw.iloc[i]
        vals = [v for v in row.tolist() if not (isinstance(v, float) and pd.isna(v)) and v is not None]
        if len(vals) < max(2, int(0.6 * width)) or len(vals) <= best_len:
            continue
        if sum(_is_header_cell(v) for v in vals) / len(vals) < 0.8:
            continue
        nxt = raw.iloc[i + 1]
        if int(nxt.notna().sum()) >= max(2, int(0.5 * len(vals))):
            best, best_len = i, len(vals)
            if len(vals) >= width:
                break  # заполнена вся ширина таблицы — шире не будет
    return best


def dedupe_names(names: list[str]) -> list[str]:
    """«Количество, Сумма, Количество, Сумма» (оборотка 1С) → «…, Количество.1, Сумма.1», как у pandas.

    С одинаковыми именами df[col] возвращает таблицу, а не колонку, и разбор листа падал.
    """
    seen: dict[str, int] = {}
    out = []
    for n in names:
        if n in seen:
            seen[n] += 1
            cand = f"{n}.{seen[n]}"
            while cand in seen:
                seen[n] += 1
                cand = f"{n}.{seen[n]}"
            seen[cand] = 0
            out.append(cand)
        else:
            seen[n] = 0
            out.append(n)
    return out


def clean_table(df: pd.DataFrame) -> pd.DataFrame:
    """Строки итогов, пустые строки/колонки, числа текстом («59 027 500,00», «—») → числа."""
    df = df.dropna(axis=1, how="all").dropna(axis=0, how="all")
    if df.empty:
        return df
    if df.columns.duplicated().any():
        df = df.copy()
        df.columns = dedupe_names([str(c) for c in df.columns])
    # «Итого» в одной из первых трёх колонок (после «№» и «Даты») — строка итогов, а не продажа
    first_cols = list(df.columns[:3])
    is_total = pd.Series(False, index=df.index)
    for c in first_cols:
        is_total |= df[c].map(lambda v: isinstance(v, str) and bool(_TOTAL_RE.match(v)))
    df = df[~is_total]
    for col in df.columns:
        if df[col].dtype != object:
            continue
        if _ID_NAME_RE.search(str(col)):
            # Коды и номера — текстом: смесь 5-значных кодов и 13-значного штрихкода давала INTEGER
            # по выборке и ошибку переполнения при загрузке
            df[col] = df[col].map(lambda v: v if v is None or (isinstance(v, float) and pd.isna(v))
                                  else (str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)))
            continue
        sample = df[col].dropna().astype(str).str.strip()
        sample = sample[~sample.str.lower().isin(_DASH_VALUES)]
        if len(sample) == 0:
            continue
        # Ведущие нули («090340098765») — идентификатор, а не число
        if (sample.str.match(r"^0\d")).mean() > 0.1:
            continue
        # Все значения — числа (кроме прочерков): при 90 % размеры «XL» среди «42» становились пустыми
        if sample.head(2000).map(_looks_number).all() and sample.map(_looks_number).mean() >= 0.999:
            df[col] = pd.to_numeric(
                df[col].map(lambda v: None if (v is None or (isinstance(v, float) and pd.isna(v))
                                               or str(v).strip().lower() in _DASH_VALUES)
                            else re.sub(r"[\s\u00a0\u202f]", "", str(v)).replace(",", ".")),
                errors="coerce")
    return df.reset_index(drop=True)


def read_excel_tables(file_path: str) -> dict[str, pd.DataFrame]:
    """Все листы-таблицы книги: {имя листа: таблица}. Служебные строки и «Итого» отброшены."""
    engine = _excel_engine(file_path)
    sheets = pd.read_excel(file_path, sheet_name=None, header=None, engine=engine)
    tables: dict[str, pd.DataFrame] = {}
    for name, raw in sheets.items():
        raw = raw.dropna(axis=1, how="all").dropna(axis=0, how="all")
        h = find_header_row(raw)
        if h is None:
            continue
        header = [str(v).strip() if not (isinstance(v, float) and pd.isna(v)) else f"col_{j + 1}"
                  for j, v in enumerate(raw.iloc[h].tolist())]
        df = raw.iloc[h + 1:].copy()
        df.columns = dedupe_names(header)
        df = clean_table(df)
        if len(df) < 2:
            continue
        tables[str(name)] = df
    if len(tables) > 1:
        # В многолистовой книге лист из 1–2 колонок — справка «параметр: значение», а не таблица
        tables = {n: t for n, t in tables.items() if t.shape[1] >= 3} or tables
    return tables


#: Слова-ключи в имени колонки: «товар_id», «код_товара», «product_code»
_KEY_TOKENS = {"id", "код", "code", "key", "ключ", "uid", "guid"}


def _key_values(s: pd.Series) -> set[str]:
    """Значения ключа как строки: 1.0 из Excel и «1» из справочника — одно и то же."""
    return {str(int(v)) if isinstance(v, float) and v.is_integer() else str(v).strip() for v in s.dropna()}


def _common_prefix(a: str, b: str) -> int:
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return n


def _entity_match(stems: list[str], table: str) -> int:
    """Насколько имя таблицы совпадает с сущностью из имени колонки: «товар» ↔ «Товары» (0 — нет)."""
    from app.domain.ai.column_roles import column_stems

    best = 0
    for a in stems:
        for b in column_stems(table):
            n = _common_prefix(a, b)
            if n >= 4 and n == min(len(a), len(b)):
                best = max(best, n)
    return best


def _named_key_relationships(tables: dict[str, pd.DataFrame], cleaned: dict[str, list[str]],
                             known: set[tuple]) -> list[dict]:
    """Связи «X_id / код_X / X_code → ключ таблицы X» (товар_id → Товары.id) по имени и значениям."""
    from app.domain.ai.column_roles import column_stems, tokens

    rels = []
    for a, ta in tables.items():
        for j, orig in enumerate(ta.columns):
            toks = tokens(str(orig))
            if not toks or not (set(toks) & _KEY_TOKENS) or set(toks) <= _KEY_TOKENS:
                continue
            col = cleaned[a][j]
            stems = column_stems(" ".join(t for t in toks if t not in _KEY_TOKENS))
            if not stems:
                continue
            va = _key_values(ta.iloc[:, j])
            if not va:
                continue
            small_ints = all(re.fullmatch(r"\d{1,3}", v) for v in va)
            cands = []
            for b, tb in tables.items():
                score = _entity_match(stems, str(b)) if b != a else 0
                if not score:
                    continue
                for k, borig in enumerate(tb.columns):
                    bcol = cleaned[b][k]
                    btoks = tokens(str(borig))
                    # Ключ справочника: «id» / «код» или «товар_id» в «Товарах»
                    if not btoks or not (set(btoks) & _KEY_TOKENS):
                        continue
                    rest = [t for t in btoks if t not in _KEY_TOKENS]
                    if rest and not _entity_match(column_stems(" ".join(rest)), str(b)):
                        continue
                    sb = tb.iloc[:, k]
                    # Текст с числом DuckDB не сравнит: JOIN упал бы с ошибкой типов
                    if pd.api.types.is_numeric_dtype(sb) != pd.api.types.is_numeric_dtype(ta.iloc[:, j]):
                        continue
                    if not sb.dropna().is_unique:
                        continue
                    vb = _key_values(sb)
                    # Почти все ссылки находятся в справочнике; для мелких целых — все, иначе совпадение случайно
                    if len(va & vb) < (0.9 if small_ints else 0.5) * len(va):
                        continue
                    cands.append((score, b, bcol))
            if not cands:
                continue
            top = max(c[0] for c in cands)
            best = [c for c in cands if c[0] == top]
            # Два одинаково подходящих справочника — связь неоднозначна, не угадываем
            if len({(c[1], c[2]) for c in best}) != 1:
                continue
            _, b, bcol = best[0]
            if (a, col, b) in known or (b, bcol, a) in known:
                continue
            rels.append({"from_table": a, "from_col": col, "to_table": b, "to_col": bcol})
    return rels


def detect_shared_key_relationships(tables: dict[str, pd.DataFrame]) -> list[dict]:
    """Связи листов по общей колонке-ключу (артикул, код точки): в одной таблице ключ уникален.

    И по ссылке «товар_id → Товары.id»: имя колонки называет лист, значения находятся в его ключе.
    """
    rels = []
    cleaned = {n: clean_column_names(t.columns) for n, t in tables.items()}
    names = list(tables)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for col in set(cleaned[a]) & set(cleaned[b]):
                if not _ID_NAME_RE.search(col):
                    continue
                ca = tables[a].iloc[:, cleaned[a].index(col)]
                cb = tables[b].iloc[:, cleaned[b].index(col)]
                # Ключ связи — общие значения: «номер документа» поступлений и возвратов совпадает
                # по имени, но это разные документы
                va, vb = set(ca.dropna().astype(str)), set(cb.dropna().astype(str))
                if not va or not vb or len(va & vb) < 0.5 * min(len(va), len(vb)):
                    continue
                # Маленькие целые («код» 1..3 у магазинов и у товаров) совпадают случайно: это не связь
                small_ints = all(re.fullmatch(r"\d{1,3}", v) for v in va | vb)
                if small_ints and len(va & vb) < 0.9 * min(len(va), len(vb)):
                    continue
                ua, ub = ca.dropna().is_unique, cb.dropna().is_unique
                if ua and not ub:
                    rels.append({"from_table": b, "from_col": col, "to_table": a, "to_col": col, "_small": small_ints})
                elif ub and not ua:
                    rels.append({"from_table": a, "from_col": col, "to_table": b, "to_col": col, "_small": small_ints})
                elif ua and ub and not small_ints:
                    # Один к одному («Номенклатура» и «Остатки» по артикулу): связь есть, JOIN безопасен —
                    # раньше её не было, и любой JOIN между листами отвергался
                    big, small = (a, b) if len(ca) >= len(cb) else (b, a)
                    rels.append({"from_table": big, "from_col": col, "to_table": small, "to_col": col, "_small": False})
    # Колонка из маленьких целых, «связанная» сразу с несколькими справочниками (код 1..3 есть и у
    # магазинов, и у товаров), — неоднозначна: такую связь не предлагаем вовсе
    targets: dict[tuple, int] = {}
    for r in rels:
        if r["_small"]:
            targets[(r["from_table"], r["from_col"])] = targets.get((r["from_table"], r["from_col"]), 0) + 1
    out = [{k: v for k, v in r.items() if k != "_small"} for r in rels
           if not (r["_small"] and targets.get((r["from_table"], r["from_col"]), 0) > 1)]
    # Ссылки по имени листа ищем и для колонок, отвергнутых выше: «код» 1..3 общий у магазинов и товаров
    # неоднозначен, а «код_товара» → «Товары.код» — нет
    known = {(r["from_table"], r["from_col"], r["to_table"]) for r in out}
    return out + _named_key_relationships(tables, cleaned, known)


#: Форматы файлов, которые принимают загрузка и превью
def clean_column_names(columns) -> list[str]:
    """Имена колонок для DuckDB: нижний регистр, только буквы/цифры/подчёркивания, без повторов.

    Раньше заголовок из одних пробелов падал с IndexError (500 при загрузке), «Revenue» и
    «revenue » сливались в одно имя (DuckDB тихо переименовывал второе, а метаданные — нет),
    а «ё» превращалась в «_».
    """
    import re

    result: list[str] = []
    seen: set[str] = set()
    for i, col in enumerate(columns):
        c = str(col).lower().strip().replace('%', ' pct ')
        c = re.sub(r'[^a-zа-яё0-9_]', '_', c)
        # «Наценка, %» → «наценка_pct», а не «наценка___»: лишние подчёркивания модель искажала
        # («дата__» вместо «дата__текст_») и запрос падал
        c = re.sub(r'_+', '_', c).strip('_') or c
        if not c.strip('_'):
            c = f'col_{i + 1}'
        elif c[0].isdigit():
            c = 'col_' + c
        base, n = c, 2
        while c in seen:
            c = f'{base}_{n}'
            n += 1
        seen.add(c)
        result.append(c)
    return result


SUPPORTED_EXTENSIONS = ('.csv', '.xlsx', '.xls', '.db')


def _csv_separator(file_path: str, encoding: str) -> str | None:
    """Разделитель по строке заголовка: «;» в «Дата;…;Скидка, %;…» — автоопределение pandas выбирало «,»
    из-за запятой в заголовке, и файл ложился одной колонкой."""
    try:
        with open(file_path, encoding=encoding) as f:
            head = f.readline()
    except (UnicodeDecodeError, OSError):
        return None
    head = re.sub(r'"[^"]*"', '', head)
    counts = {sep: head.count(sep) for sep in (';', '\t', ',', '|')}
    sep, n = max(counts.items(), key=lambda kv: kv[1])
    return sep if n else None


def read_table_file(file_path: str, excel_tables: dict | None = None) -> tuple[pd.DataFrame, list[str]]:
    """CSV или книга Excel → таблица и предупреждения. Одно чтение для загрузки и превью.

    CSV в Windows-кодировке (cp1251) читается со вторым заходом и предупреждением.
    excel_tables — уже прочитанные листы книги: книга не читается второй раз.
    """
    warnings: list[str] = []
    ext = Path(file_path).suffix.lower()
    if ext == '.csv':
        try:
            sep = _csv_separator(file_path, 'utf-8')
            df = pd.read_csv(file_path, sep=sep, engine='python', encoding='utf-8')
        except UnicodeDecodeError:
            sep = _csv_separator(file_path, 'cp1251')
            df = pd.read_csv(file_path, sep=sep, engine='python', encoding='cp1251')
            warnings.append("File was read with cp1251 encoding")
    elif ext in ('.xlsx', '.xls'):
        if excel_tables:
            # Уже очищено при чтении листов — повторно не чистим
            return max(excel_tables.values(), key=len), warnings
        df = read_excel_file(file_path)
    else:
        raise ValueError(f"Unsupported file format: {ext}. Use CSV, XLSX, XLS or DB.")
    return clean_table(df), warnings


def ingest_file(file_path: str, db_conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Ingest a file into the database.

    Args:
        file_path: Path to the file to ingest.
        db_conn: DuckDB connection.

    Returns:
        Dict with rows_loaded, columns, warnings.
    """
    ext = Path(file_path).suffix.lower()

    # Read file
    if ext == '.db':
        return _ingest_db_file(file_path, db_conn)
    tables = None
    if ext in ('.xlsx', '.xls'):
        tables = read_excel_tables(file_path)
        if len(tables) > 1:
            # Книга с несколькими листами-таблицами — группа связанных таблиц, как .db
            return _ingest_tables(tables, Path(file_path).name, detect_shared_key_relationships(tables),
                                  "книга Excel", db_conn)
    df, warnings = read_table_file(file_path, excel_tables=tables)

    import uuid
    import json

    # Clean column names
    df.columns = clean_column_names(df.columns)

    if df.empty:
        raise ValueError("File is empty")

    dataset_id = str(uuid.uuid4())
    dataset_name = Path(file_path).name
    table_name = f"dataset_{dataset_id.replace('-', '_')}"

    # Extract column metadata for frontend
    columns_meta = []
    for col, dtype in df.dtypes.items():
        meta_type = detect_column_type(str(col), dtype)
        columns_meta.append({"key": col, "label": col, "type": meta_type})

    # Clean dates: strip timezones, normalize format
    df = _clean_dates_in_df(df, columns_meta)

    # Create and populate table
    db_conn.register('_ingest_df', df)
    db_conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM _ingest_df")
    db_conn.unregister('_ingest_df')

    rows_loaded = len(df)
    columns_json = json.dumps(columns_meta, ensure_ascii=False)

    db_conn.execute(
        "INSERT INTO datasets (id, name, rows, columns, table_name) VALUES (?, ?, ?, ?, ?)",
        (dataset_id, dataset_name, rows_loaded, columns_json, table_name)
    )

    # Pre-analyze dataset for fast runtime lookups
    from app.services.preanalysis import preanalyze_dataset
    preanalyze_dataset(db_conn, dataset_id, table_name)

    return {
        "rows_loaded": rows_loaded,
        "total_rows": rows_loaded,
        "dataset_id": dataset_id,
        "columns": list(df.columns),
        "warnings": warnings
    }


def _ingest_db_file(file_path: str, db_conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Ingest a .db file (DuckDB or SQLite) into the database."""

    # Try DuckDB first, then SQLite
    tables = _try_duckdb_tables(file_path)
    if tables is None:
        tables = _try_sqlite_tables(file_path)
    if tables is None:
        raise ValueError("Не удалось прочитать файл .db. Поддерживаются DuckDB и SQLite.")

    # Detect relationships between tables
    relationships = _detect_relationships(tables, file_path)
    return _ingest_tables(tables, Path(file_path).name, relationships, "файл .db", db_conn)


def _ingest_tables(tables: dict, source_file: str, relationships: list[dict], kind: str,
                   db_conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Загрузить несколько таблиц одной группой (таблицы .db или листы книги Excel)."""
    import uuid
    import json

    relationships_json = json.dumps(relationships, ensure_ascii=False)
    source_group = str(uuid.uuid4())

    total_rows = 0
    all_columns = []
    dataset_ids = []

    # Если упадёт таблица N, таблицы 1..N-1 не должны остаться в базе: клиент получит ошибку
    # и загрузит файл снова, а старые половинки висели бы дублями. Транзакцию здесь не открыть:
    # предварительный анализ гасит ошибки отдельных запросов, а в DuckDB они обрывают транзакцию
    created: list[tuple[str, str]] = []
    try:
        for table_name, df in tables.items():
            if df.empty:
                continue

            # Clean column names
            df.columns = clean_column_names(df.columns)

            dataset_id = str(uuid.uuid4())
            target_table = f"dataset_{dataset_id.replace('-', '_')}"

            # Extract column metadata
            columns_meta = []
            for col, dtype in df.dtypes.items():
                meta_type = detect_column_type(str(col), dtype)
                columns_meta.append({"key": col, "label": col, "type": meta_type})

            # Clean dates: strip timezones, normalize format
            df = _clean_dates_in_df(df, columns_meta)

            db_conn.register('_ingest_df', df)
            db_conn.execute(f"CREATE TABLE {target_table} AS SELECT * FROM _ingest_df")
            db_conn.unregister('_ingest_df')
            created.append((dataset_id, target_table))

            rows = len(df)
            total_rows += rows
            columns_json = json.dumps(columns_meta, ensure_ascii=False)

            db_conn.execute(
                "INSERT INTO datasets (id, name, rows, columns, table_name, source_group, source_file, relationships) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (dataset_id, table_name, rows, columns_json, target_table, source_group, source_file, relationships_json)
            )
        
            # Pre-analyze dataset for fast runtime lookups
            from app.services.preanalysis import preanalyze_dataset
            preanalyze_dataset(db_conn, dataset_id, target_table)
        
            dataset_ids.append(dataset_id)
            all_columns.extend(list(df.columns))
    except Exception:
        for ds_id, table in created:
            for sql, params in (
                (f"DROP TABLE IF EXISTS {table}", None),
                ("DELETE FROM datasets WHERE id = ?", [ds_id]),
                ("DELETE FROM dataset_manifest WHERE dataset_id = ?", [ds_id]),
            ):
                try:
                    if params is None:
                        db_conn.execute(sql)
                    else:
                        db_conn.execute(sql, params)
                except Exception:
                    pass
        raise

    if not dataset_ids:
        raise ValueError(f"{kind[:1].upper()}{kind[1:]} не содержит таблиц с данными.")

    return {
        "rows_loaded": total_rows,
        "total_rows": total_rows,
        "dataset_id": dataset_ids[0] if len(dataset_ids) == 1 else ",".join(dataset_ids),
        "columns": list(dict.fromkeys(all_columns)),
        "warnings": [f"Импортировано таблиц: {len(dataset_ids)}"]
    }


def _detect_relationships(tables: dict, file_path: str) -> list[dict]:
    """Связи таблиц файла .db: объявленные внешние ключи, иначе — по именам и значениям ключей."""
    fk_relationships = _try_duckdb_foreign_keys(file_path)
    if fk_relationships is None:
        fk_relationships = _try_sqlite_foreign_keys(file_path)
    if fk_relationships:
        # Колонки при загрузке чистятся («ProductID» → «productid»): связь должна ссылаться на те же имена
        names = {t: dict(zip(df.columns, clean_column_names(df.columns))) for t, df in tables.items()}
        return [{**r, "from_col": names.get(r["from_table"], {}).get(r["from_col"], r["from_col"]),
                 "to_col": names.get(r["to_table"], {}).get(r["to_col"], r["to_col"])} for r in fk_relationships]
    # Раньше «*_id → таблица.id» бралось только по подстроке имени, без сверки значений
    # («s_id» связывался с любой таблицей, где есть «s»)
    return detect_shared_key_relationships(tables)


def _try_duckdb_foreign_keys(file_path: str):
    """Try to read foreign keys from a DuckDB file."""
    try:
        tmp_conn = duckdb.connect(file_path, read_only=True)
        tables = tmp_conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
        ).fetchall()
        relationships = []
        for (tname,) in tables:
            try:
                fks = tmp_conn.execute(f"PRAGMA foreign_keys('{tname}')").fetchall()
                for fk in fks:
                    if len(fk) >= 4:
                        relationships.append({
                            "from_table": tname,
                            "from_col": str(fk[0]),
                            "to_table": str(fk[2]),
                            "to_col": str(fk[3]) if len(fk) > 3 else "id"
                        })
            except Exception:
                pass
        tmp_conn.close()
        return relationships if relationships else None
    except Exception:
        return None


def _try_sqlite_foreign_keys(file_path: str):
    """Try to read foreign keys from a SQLite file."""
    try:
        import sqlite3
        conn = sqlite3.connect(file_path)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = cursor.fetchall()
        relationships = []
        for (tname,) in tables:
            try:
                fk_cursor = conn.execute(f"PRAGMA foreign_key_list('{tname}')")
                for fk in fk_cursor.fetchall():
                    if len(fk) >= 4:
                        relationships.append({
                            "from_table": tname,
                            "from_col": str(fk[3]),
                            "to_table": str(fk[2]),
                            "to_col": str(fk[4]) if len(fk) > 4 and fk[4] else "id"
                        })
            except Exception:
                pass
        conn.close()
        return relationships if relationships else None
    except Exception:
        return None


def _try_duckdb_tables(file_path: str):
    """Try to read tables from a DuckDB file. Returns dict of {table_name: DataFrame} or None."""
    try:
        tmp_conn = duckdb.connect(file_path, read_only=True)
        tables = tmp_conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
        ).fetchall()
        result = {}
        for (tname,) in tables:
            df = tmp_conn.execute(f'SELECT * FROM "{tname}"').fetchdf()
            result[tname] = df
        tmp_conn.close()
        return result if result else None
    except Exception:
        return None


def _try_sqlite_tables(file_path: str):
    """Try to read tables from a SQLite file. Returns dict of {table_name: DataFrame} or None."""
    try:
        import sqlite3
        conn = sqlite3.connect(file_path)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = cursor.fetchall()
        result = {}
        for (tname,) in tables:
            df = pd.read_sql_query(f'SELECT * FROM "{tname}"', conn)
            result[tname] = df
        conn.close()
        return result if result else None
    except Exception:
        return None


def analyze_and_cache_schema(db_conn, table_name: str) -> dict:
    """Analyze table structure and cache the profile for fast agent lookups."""
    import json
    
    try:
        row_count = db_conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        cols = db_conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position",
            [table_name]
        ).fetchall()

        columns = []
        null_rates = {}
        distinct_counts = {}
        sample_values = {}

        for col_name, col_type in cols:
            columns.append({"name": col_name, "type": col_type})
            try:
                nc = db_conn.execute(f"SELECT COUNT(*) FROM {table_name} WHERE {col_name} IS NULL").fetchone()[0]
                null_rates[col_name] = round(nc / max(row_count, 1), 4)
            except Exception:
                null_rates[col_name] = 0.0
            try:
                dc = db_conn.execute(f"SELECT COUNT(DISTINCT {col_name}) FROM {table_name}").fetchone()[0]
                distinct_counts[col_name] = dc
            except Exception:
                distinct_counts[col_name] = 0
            try:
                samples = db_conn.execute(
                    f"SELECT DISTINCT {col_name} FROM {table_name} WHERE {col_name} IS NOT NULL LIMIT 5"
                ).fetchall()
                sample_values[col_name] = [s[0] for s in samples]
            except Exception:
                sample_values[col_name] = []

        profile = {
            "table_name": table_name,
            "row_count": row_count,
            "columns": columns,
            "null_rates": null_rates,
            "distinct_counts": distinct_counts,
            "sample_values": sample_values,
        }
        db_conn.execute(
            "INSERT OR REPLACE INTO schema_cache (table_name, profile_json, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            [table_name, json.dumps(profile, ensure_ascii=False, default=str)]
        )
        logger.info(f"Cached schema profile for {table_name} ({row_count} rows, {len(columns)} cols)")
        return profile
    except Exception as e:
        logger.error(f"Failed to analyze schema for {table_name}: {e}")
        return {"table_name": table_name, "error": str(e)}
