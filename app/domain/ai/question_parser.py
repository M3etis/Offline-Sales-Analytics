"""Разбор вопроса пользователя: периоды, разрез, метрика, фильтры.

Небольшая локальная модель плохо пишет SQL для типовых бизнес-вопросов: путает периоды,
сортирует не по той метрике, угадывает значения фильтров. Здесь вопрос разбирается
детерминированно, чтобы типовые вопросы считались готовыми запросами (app.services.insights),
а модель только формулировала ответ по точным данным.

Все функции чистые: метаданные таблицы передаются аргументами.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

# ── Роли колонок ──────────────────────────────────────────────────────────────
# Роли распознаются в domain.ai.column_roles (имя RU/EN, тип, данные) — для любого набора данных

# ── Разрезы и метрики ─────────────────────────────────────────────────────────
# Порядок важен: первое совпадение — главный разрез
DIMENSION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("weekday", re.compile(r"дн(ям|ю|ей|и)?\s+недел|день\s+недел")),
    ("category", re.compile(r"категори")),
    ("manager", re.compile(r"менеджер|сотрудник|продавц|продавец")),
    ("region", re.compile(r"регион|город|област|филиал")),
    ("channel", re.compile(r"канал")),
    ("brand", re.compile(r"бренд|производител")),
    ("warehouse", re.compile(r"склад|торгов\w*\s+точк|точк\w*\s+продаж|магазин|торговых точ|по точкам")),
    ("status", re.compile(r"статус|отмен[её]н|отмены")),
    ("customer", re.compile(r"клиент|покупател")),
    ("product", re.compile(r"товар|продукт|позици|издели|артикул|sku|наименован|номенклатур")),
]

METRIC_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("avg_check", re.compile(r"средн\w*\s+чек")),
    ("margin_pct", re.compile(r"маржинальн|марж\w*|рентабельн")),
    # «по сумме покупок» — выручка; без этого правила «покупок» срабатывало как число заказов
    ("revenue", re.compile(r"сумм\w*\s+(покуп|заказ|продаж)|потратил|на как\w+ сумм")),
    ("avg_discount", re.compile(r"скидк")),
    ("profit", re.compile(r"прибыл|заработ")),
    ("orders", re.compile(r"заказ|транзакц|покупок|чеков")),
    ("quantity", re.compile(r"штук|единиц|продано|продаваем|популярн|количеств\w+\s+(товар|продаж|прода)")),
    ("revenue", re.compile(r"выручк|продаж|продал|оборот|доход|сумм\w* покуп|потратил")),
]

METRIC_LABELS = {
    "revenue": "выручка",
    "profit": "прибыль",
    "orders": "число заказов",
    "quantity": "продано, шт.",
    "avg_check": "средний чек",
    "margin_pct": "маржинальность, %",
    "avg_discount": "средняя скидка",
}

#: Имя колонки результата для «сколько <разрез>»: COUNT(DISTINCT …) AS customers
COUNT_KEYS = {"customer": "customers", "product": "products", "region": "regions", "manager": "managers",
              "category": "categories", "brand": "brands", "channel": "channels", "warehouse": "warehouses"}

#: Слова в значениях, по которым значение не узнаём: слишком общие («ТРЦ Гранд Плаза» — не «плаза» в вопросе)
_GENERIC_VALUE_WORDS = {"плаза", "молл", "сити", "центр", "площадки", "сайт", "mall", "city", "plaza", "center"}

_STOP_STEMS = ("клиент", "покупател", "товар", "магазин", "продаж", "заказ", "регион", "канал",
               "бренд", "менеджер", "категор", "издели", "вещи", "склад", "статус")


def detect_dimensions(q: str, roles: dict[str, str]) -> list[str]:
    """Разрезы, упомянутые в вопросе и существующие в данных (weekday — если есть дата)."""
    q = q.lower()
    found = []
    for dim, pat in DIMENSION_PATTERNS:
        if not pat.search(q):
            continue
        if dim == "weekday":
            if "date" in roles:
                found.append(dim)
        elif dim in roles:
            found.append(dim)
    return found


def mentioned_dimensions(q: str) -> list[str]:
    """Разрезы, упомянутые в вопросе, — независимо от того, есть ли они в данных."""
    q = q.lower()
    return [dim for dim, pat in DIMENSION_PATTERNS if pat.search(q)]


_LOOSE_STOP = {"код", "номер", "name", "название", "наименование", "code", "number", "kzt", "текст", "дней"}


def column_mentions(q: str, columns: list[str], skip: set[str] = frozenset(), loose: bool = False) -> list[str]:
    """Колонки, названные в вопросе своими словами: «по типу оплаты» → «тип_оплаты».

    Нужны ВСЕ значимые слова имени: иначе «канал_продаж» ловилась бы на любом «продажи».
    loose — короткие основы (4 буквы): «дней хранения» ловит «хранится … дней».
    """
    from app.domain.ai.column_roles import column_stems, tokens

    ql = q.lower()
    found = []
    for col in columns:
        if col in skip:
            continue
        stems = column_stems(col)
        if loose:
            # «хранения» → «хран» (ловит «хранится»), но «просрочено» → «просро» (не ловит «просадки»)
            stems = [t[: max(4, len(t) - 4)] for t in tokens(col) if len(t) >= 4 and t not in _LOOSE_STOP]
        if stems and all(re.search(r"(?<![a-zа-яё])" + re.escape(st), ql) for st in stems):
            found.append(col)
    return found


#: Показатель → роль колонки, без которой его не посчитать
METRIC_REQUIRES = {"profit": "profit", "margin_pct": "profit", "quantity": "quantity", "avg_discount": "discount"}


def detect_metric(q: str) -> Optional[str]:
    q = q.lower()
    for metric, pat in METRIC_PATTERNS:
        if pat.search(q):
            return metric
    return None


def detect_metrics(q: str) -> list[str]:
    """Все упомянутые показатели в порядке упоминания: «выручка, средний чек, маржа»."""
    q = q.lower()
    hits: dict[str, int] = {}
    for metric, pat in METRIC_PATTERNS:
        m = pat.search(q)
        if m and metric not in hits:
            hits[metric] = m.start()
    # «средний чек» содержит «чек» и не должен давать ещё и «число заказов»
    if "avg_check" in hits and "orders" in hits and not re.search(r"заказ|транзакц|покупок", q):
        hits.pop("orders")
    return sorted(hits, key=hits.get)


_WORD_NUMBERS = {"три": 3, "пять": 5, "десять": 10, "двадцать": 20}


def detect_limit(q: str) -> Optional[int]:
    q = q.lower()
    m = re.search(r"топ[\s\-–—]*(\d{1,3})", q) or re.search(r"\b(\d{1,3})\s+(лучш|худш|сам|крупн|популярн|продаваем)", q)
    if m:
        return int(m.group(1))
    m = re.search(r"топ[\s\-–—]*(три|пять|десять|двадцать)", q)
    if m:
        return _WORD_NUMBERS[m.group(1)]
    return None


def detect_ascending(q: str) -> bool:
    return bool(re.search(r"худш|наимен(?!ован)|меньше всего|аутсайдер|слаб|отста|самы\w*\s+(низк|мал)", q.lower()))


def _value_tokens(value: str) -> list[str]:
    raw = [t for t in re.split(r"[^a-zа-яё0-9]+", value.lower()) if t]
    tokens = [t for t in raw if len(t) >= 4]
    if not tokens and len(raw) == 1 and len(raw[0]) == 3 and raw[0].isalpha():
        # Короткое название из одного слова («Уфа», «Орёл»): узнаём, но только целым словом
        tokens = raw
    return [t for t in tokens if not any(t.startswith(s) for s in _STOP_STEMS)]


def _stem(token: str) -> str:
    # Грубая основа: «Астана» ловит «в Астане», «бутик» — «в бутиках»
    return token if len(token) <= 5 else token[: max(5, len(token) - 2)]


_WORD_END = "аеёиоуыэюяьй"


def token_pattern(token: str) -> str:
    """Регэксп слова значения в вопросе с учётом падежа.

    Длинное слово — по основе («Астана» → «в Астане»). Короткое (до 5 букв) — целым словом
    с окончанием: «Пермь» → «в Перми», «Тула» → «в Туле», «Уфа» → «в Уфе». Раньше короткое
    слово искалось без изменений, и «в Перми» не находило «Пермь».
    """
    left = r"(?<![a-zа-яё0-9])"
    if len(token) <= 5:
        base = token[:-1] if len(token) >= 3 and token[-1] in _WORD_END else token
        return left + re.escape(base) + r"[a-zа-яё]{0,3}(?![a-zа-яё0-9])"
    return left + re.escape(_stem(token))


def mask_metric_words(q: str) -> str:
    """Убрать из вопроса слова показателей: «средний чек» не должен находить сегмент «Средний»."""
    ql = q.lower()
    for _, pat in METRIC_PATTERNS:
        ql = pat.sub(lambda m: " " * len(m.group(0)), ql)
    return ql


def detect_filters(q: str, values_by_column: dict[str, list[str]]) -> dict[str, list[str]]:
    """Значения категориальных колонок, упомянутые в вопросе: {колонка: [значения]}."""
    return {c: list(v) for c, v in detect_filters_detailed(q, values_by_column).items()}


def enumerated_columns(q: str, values_by_column: dict[str, list[str]]) -> list[str]:
    """Колонки, где пользователь перечислил несколько значений разными словами («в Астане и Алматы»).

    «В Алматы» при четырёх точках города — одно слово, это фильтр «все точки города», а не разрез.
    """
    return [c for c, m in detect_filters_detailed(q, values_by_column).items() if len(set(m.values())) > 1]


def value_groups(q: str, values_by_column: dict[str, list[str]], col: str) -> dict[str, list[str]]:
    """Группы значений по словам вопроса: «Алматы и Астана» → {«Алматы»: [точки Алматы], «Астана»: […]}."""
    groups: dict[str, list[str]] = {}
    for v, token in detect_filters_detailed(q, values_by_column).get(col, {}).items():
        groups.setdefault(token[:1].upper() + token[1:], []).append(v)
    return groups


def detect_filters_detailed(q: str, values_by_column: dict[str, list[str]]) -> dict[str, dict[str, str]]:
    """{колонка: {значение: слово вопроса, по которому оно найдено}}.

    Значение узнаём по значимым словам: «Онлайн-магазин» — по «онлайн», «Флагман Алматы, ТРЦ Алатау Молл» —
    по «Алматы» (тогда «в Алматы» = все точки города). Если в колонке есть значения, совпавшие большим
    числом слов, остаются только они: «Мега Алматы» — это «ТРЦ Мега Алматы», а не все точки со словом
    «Мега» или «Алматы»; «Иванов Иван» — не «Иванова Анна» и не «Петров Иван».
    """
    ql = mask_metric_words(q)
    found: dict[str, dict[str, str]] = {}
    for col, values in values_by_column.items():
        scored: dict[str, tuple[int, str]] = {}
        for v in values:
            if v is None:
                continue
            sv = str(v)
            if len(sv) < 3:
                continue
            hits = [t for t in _value_tokens(sv)
                    if t not in _GENERIC_VALUE_WORDS and re.search(token_pattern(t), ql)]
            if hits:
                scored[sv] = (len(hits), hits[0])
        if scored:
            best = max(n for n, _ in scored.values())
            found[col] = {sv: t for sv, (n, t) in scored.items() if n == best}
    return found


# Признаки логики, которую готовые запросы не покрывают: условия, вложенность, сравнение порогов
_COMPLEX_RE = re.compile(
    r"\b(среди|у котор|которы[ехм]|где\b|если|больше чем|меньше чем|выше чем|ниже чем|не менее|не более|"
    r"каждого|кажд\w+ (месяц|регион|категор|менеджер)|в среднем на|одновременно|кроме|без учета|без учёта|"
    r"корреляц|зависимост|у самого|у лучшего|у худшего)"
    # Пороги и отрицание: «заказы на сумму больше 50 000», «без скидки» — готовый расчёт их не применит
    r"|(?:больше|меньше|более|менее|выше|ниже|свыше|дороже|дешевле|[<>]=?)\s*(?:чем\s*)?\d"
    r"|(?<![a-zа-яё])без\s+(?!учет|учёт)"
)

#: Максимум или минимум одного показателя без разреза: «какая максимальная скидка» — не средняя скидка
EXTREME_RE = re.compile(r"максимальн|минимальн|наибольш|наименьш|самы\w*\s+(?:больш|крупн|дорог|дешев|мелк|маленьк|высок|низк)")


def is_complex(q: str) -> bool:
    return bool(_COMPLEX_RE.search(q.lower()))


# ── Периоды ───────────────────────────────────────────────────────────────────
_MONTHS = [
    ("январ", 1), ("феврал", 2), ("март", 3), ("апрел", 4), ("ма", 5), ("июн", 6),
    ("июл", 7), ("август", 8), ("сентябр", 9), ("октябр", 10), ("ноябр", 11), ("декабр", 12),
]
MONTH_NAMES = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
               "сентябрь", "октябрь", "ноябрь", "декабрь"]
# Месяц — целое слово с падежным окончанием: «марта», «в мае», но не «Мартынова» и не «маек»
_MONTH_RE = re.compile(
    r"(?<![a-zа-яё])(январ|феврал|март|апрел|ма(?=[йяею])|июн|июл|август|сентябр|октябр|ноябр|декабр)"
    r"(?:ь|я|е|ю|й|у|а|ём|ем|ом)?(?![a-zа-яё])(?:\s+(20\d{2})(?![\d.\-/]))?")
_QUARTER_WORDS = {"1": 1, "2": 2, "3": 3, "4": 4, "перв": 1, "втор": 2, "трет": 3, "четв": 4,
                  "i": 1, "ii": 2, "iii": 3, "iv": 4}
_QTOK = r"(?:[1-4]|перв\w*|втор\w*|трет\w*|четв[её]рт\w*|iv|iii|ii|i)(?:[\s\-]*(?:й|ий|ый|ой|м|го|ом))?"
# Один квартал или перечисление: «3 квартал», «1-м квартале», «1 и 3 квартал», «первый и третий кварталы»
_QUARTER_RE = re.compile(r"(?<![a-zа-яё0-9])((?:" + _QTOK + r"\s*(?:,|и)\s*)*" + _QTOK + r")\s*квартал\w*(?:\s+(20\d{2}))?")
_QTOK_RE = re.compile(r"[1-4]|перв|втор|трет|четв|iv|iii|ii|i")
_Q_RE = re.compile(r"\bq([1-4])(?:\s*(20\d{2}))?")
_LAST_N_QUARTERS_RE = re.compile(r"последни\w*\s+(\d)\s+квартал\w*")
# Явные даты: 15.11.2026, 2026-11-15
_DATE_RE = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(20\d{2})(?![\d.])|(?<![\d\-])(20\d{2})-(\d{1,2})-(\d{1,2})(?![\d\-])")
# Год: «за 2025», «2025 год», но не сумма «больше 2000» и не часть даты
_YEAR_RE = re.compile(r"(?<![\d.\-/])(20\d{2})(?![\d.\-/]|\s*(?:тенге|тг|руб|₸|\$|тыс|млн|шт|%|штук|единиц))")
_AMOUNT_BEFORE_RE = re.compile(r"(?:больше|меньше|более|менее|выше|ниже|свыше|дороже|дешевле|сумм\w*|чем|[<>=]|на)\s*$")
# Связка двух периодов в один диапазон: «с января по март», «2024–2025», «с 01.01 по 31.03»
_RANGE_SEP_RE = re.compile(r"^\s*(?:по|до|[-–—])\s*$")


@dataclass
class Period:
    label: str
    start: date          # включительно
    end: date            # исключительно
    complete: bool = True
    kind: str = "custom"  # month / quarter / year / days / custom
    days: int = field(default=0)

    @property
    def end_inclusive(self) -> date:
        return self.end - timedelta(days=1)


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def _fmt(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def finish_period(p: Period, data_start: Optional[date], data_end: Optional[date]) -> Period:
    """Пометить неполный период и подписать фактическое покрытие данными.

    Нужна для любого периода, а не только названного в вопросе: «предыдущий месяц» к декабрю
    при данных с 15 ноября тоже неполный, и сравнивать его суммы с полным декабрём нельзя.
    """
    if not data_start or not data_end or "неполный" in p.label:
        return p
    covered_start = max(p.start, data_start)
    covered_end = min(p.end_inclusive, data_end)
    p.complete = p.start >= data_start and p.end_inclusive <= data_end
    if not p.complete and covered_start <= covered_end:
        p.label = f"{p.label} (неполный: данные {_fmt(covered_start)}–{_fmt(covered_end)})"
    return p


_finish = finish_period


def month_period(year: int, month: int) -> Period:
    start = date(year, month, 1)
    return Period(f"{MONTH_NAMES[month - 1]} {year}", start, _add_months(start, 1), kind="month")


def quarter_period(year: int, q: int) -> Period:
    start = date(year, 3 * (q - 1) + 1, 1)
    return Period(f"{q} квартал {year}", start, _add_months(start, 3), kind="quarter")


def year_period(year: int) -> Period:
    return Period(f"{year} год", date(year, 1, 1), date(year + 1, 1, 1), kind="year")


def last_full_month(data_end: date) -> Period:
    first = data_end.replace(day=1)
    if data_end == _add_months(first, 1) - timedelta(days=1):
        return month_period(first.year, first.month)
    prev = _add_months(first, -1)
    return month_period(prev.year, prev.month)


def last_full_quarter(data_end: date) -> Period:
    q = (data_end.month - 1) // 3 + 1
    p = quarter_period(data_end.year, q)
    if p.end_inclusive <= data_end:
        return p
    prev_start = _add_months(p.start, -3)
    return quarter_period(prev_start.year, (prev_start.month - 1) // 3 + 1)


def days_period(n: int, data_end: date, label: str) -> Period:
    end = data_end + timedelta(days=1)
    return Period(label, end - timedelta(days=n), end, kind="days", days=n)


def previous_period(p: Period) -> Period:
    """Предыдущий период той же длины: месяц → прошлый месяц, квартал → прошлый квартал."""
    if p.kind == "month":
        s = _add_months(p.start, -1)
        return month_period(s.year, s.month)
    if p.kind == "quarter":
        s = _add_months(p.start, -3)
        return quarter_period(s.year, (s.month - 1) // 3 + 1)
    if p.kind == "year":
        return year_period(p.start.year - 1)
    length = (p.end - p.start).days
    return Period(f"предыдущие {length} дн.", p.start - timedelta(days=length), p.start, kind="days", days=length)


def parse_periods(q: str, data_start: date, data_end: date) -> list[Period]:
    """Периоды, названные в вопросе, в порядке упоминания."""
    ql = q.lower()
    found: list[tuple[int, Period]] = []
    spans: list[tuple[int, int]] = []

    def taken(s: int, e: int) -> bool:
        return any(s < te and ts < e for ts, te in spans)

    for m in _LAST_N_QUARTERS_RE.finditer(ql):
        # «за последние 3 квартала» — три последних полных квартала одним периодом, а не 3-й квартал
        n = int(m.group(1))
        last = last_full_quarter(data_end)
        start = _add_months(last.start, -3 * (n - 1))
        found.append((m.start(), Period(f"последние {n} квартала ({_fmt(start)}–{_fmt(last.end_inclusive)})",
                                        start, last.end, kind="custom")))
        spans.append(m.span())
    for m in _QUARTER_RE.finditer(ql):
        if taken(*m.span()):
            continue
        year = int(m.group(2)) if m.group(2) else None
        # «1 и 3 квартал» — два квартала; точное совпадение («3», «iii») важнее префикса
        for tok in re.split(r"\s*(?:,|\bи\b)\s*", m.group(1)):
            word = (_QTOK_RE.match(tok.strip()) or [None])[0]
            if not word:
                continue
            qn = _QUARTER_WORDS.get(word) or next(
                (v for k, v in _QUARTER_WORDS.items() if len(k) > 2 and word.startswith(k)), None)
            if not qn:
                continue
            y = year if year is not None else (
                data_end.year if quarter_period(data_end.year, qn).start <= data_end else data_end.year - 1)
            found.append((m.start() + m.group(1).find(tok.strip()), quarter_period(y, qn)))
        spans.append(m.span())
    for m in _Q_RE.finditer(ql):
        if taken(*m.span()):
            continue
        qn = int(m.group(1))
        year = int(m.group(2)) if m.group(2) else (data_end.year if quarter_period(data_end.year, qn).start <= data_end else data_end.year - 1)
        found.append((m.start(), quarter_period(year, qn)))
        spans.append(m.span())

    for m in _MONTH_RE.finditer(ql):
        if taken(*m.span()):
            continue
        stem = m.group(1)
        month = next(n for p, n in _MONTHS if p == stem)
        if m.group(2):
            year = int(m.group(2))
        else:
            # Без года — последний такой месяц в данных
            year = data_end.year if month <= data_end.month else data_end.year - 1
        found.append((m.start(), month_period(year, month)))
        spans.append(m.span())

    rel = [
        (r"(прошл\w*|предыдущ\w*|последн\w*)\s+квартал", lambda: last_full_quarter(data_end)),
        (r"(прошл\w*|предыдущ\w*)\s+месяц", lambda: _prev_month_of(data_end)),
        (r"последн\w*\s+месяц(?!ев|а\b)", lambda: last_full_month(data_end)),
        (r"(эт\w+|текущ\w*)\s+месяц", lambda: _current_month(data_end)),
        (r"(прошл\w*|предыдущ\w*)\s+год", lambda: year_period(data_end.year - 1)),
        # «за последний год» — 12 месяцев до конца данных; раньше период не узнавался и ответ был за всё время
        (r"последн\w*\s+год(?!ов)", lambda: days_period(365, data_end, "последние 12 мес.")),
        (r"(эт\w+|текущ\w*)\s+год", lambda: year_period(data_end.year)),
        (r"(прошл\w*|последн\w*|предыдущ\w*)\s+недел\w*|за\s+недел\w*", lambda: days_period(7, data_end, "последние 7 дней")),
    ]
    for pat, make in rel:
        for m in re.finditer(pat, ql):
            if taken(*m.span()):
                continue
            found.append((m.start(), make()))
            spans.append(m.span())

    for m in re.finditer(r"последни\w*\s+(\d{1,3})\s+(дн|недел|месяц)", ql):
        if taken(*m.span()):
            continue
        n = int(m.group(1))
        unit = m.group(2)
        days = n * (7 if unit == "недел" else 30 if unit == "месяц" else 1)
        word = {"дн": "дн.", "недел": "нед.", "месяц": "мес."}[unit]
        found.append((m.start(), days_period(days, data_end, f"последние {n} {word}")))
        spans.append(m.span())

    for m in re.finditer(r"(?<!\d)(20\d{2})\s*[-–—]\s*(20\d{2})(?!\d)", ql):
        # «за 2024–2025 годы» — один период
        y1, y2 = sorted((int(m.group(1)), int(m.group(2))))
        found.append((m.start(), Period(f"{y1}–{y2} годы", date(y1, 1, 1), date(y2 + 1, 1, 1), kind="custom")))
        spans.append(m.span())

    for m in _DATE_RE.finditer(ql):
        if taken(*m.span()):
            continue
        try:
            if m.group(1):
                d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            else:
                d = date(int(m.group(4)), int(m.group(5)), int(m.group(6)))
        except ValueError:
            continue
        found.append((m.start(), Period(_fmt(d), d, d + timedelta(days=1), kind="days", days=1)))
        spans.append(m.span())

    for m in _YEAR_RE.finditer(ql):
        if taken(*m.span()) or _AMOUNT_BEFORE_RE.search(ql[: m.start()]):
            continue
        found.append((m.start(), year_period(int(m.group(1)))))
        spans.append(m.span())

    found.sort(key=lambda x: x[0])
    found = _merge_ranges(ql, found, spans)
    return [_finish(p, data_start, data_end) for _, p in found]


def _merge_ranges(ql: str, found: list[tuple[int, Period]], spans: list[tuple[int, int]]) -> list[tuple[int, Period]]:
    """«С января по март», «2024–2025», «с 01.01.2026 по 31.03.2026» — один период, а не сравнение концов."""
    if len(found) < 2:
        return found
    ends = {s: e for s, e in spans}
    out: list[tuple[int, Period]] = []
    i = 0
    while i < len(found):
        pos, p = found[i]
        if i + 1 < len(found):
            pos2, p2 = found[i + 1]
            end1 = ends.get(pos, pos)
            between = ql[end1:pos2] if end1 <= pos2 else ""
            before = ql[max(0, pos - 4):pos]
            dash = bool(re.fullmatch(r"\s*[-–—]\s*", between))
            is_range = bool(_RANGE_SEP_RE.match(between) and (dash or re.search(r"(?:^|\s)(?:с|со|от)\s*$", before)))
            if is_range and p.kind == "month" and p2.kind == "month" and p2.end <= p.start:
                # «с ноября по март»: ноябрь прошлого года
                p = month_period(p.start.year - 1, p.start.month)
            if is_range and p2.end > p.start:
                label = f"{p.label} – {p2.label}"
                out.append((pos, Period(label, p.start, p2.end, kind="custom")))
                i += 2
                continue
        out.append((pos, p))
        i += 1
    return out


def _prev_month_of(data_end: date) -> Period:
    s = _add_months(data_end.replace(day=1), -1)
    return month_period(s.year, s.month)


def _current_month(data_end: date) -> Period:
    s = data_end.replace(day=1)
    return month_period(s.year, s.month)


# «рост» — целым корнем: не «пРОСТо» и не «Ростов»
COMPARISON_RE = re.compile(r"сравн|\bvs\b|против|по сравнению|изменил|изменени|динамика месяц к месяцу|месяц к месяцу|"
                           r"(?<![а-яё])рост(?!ов)|прирост|упал|вырос")


# ── Неузнанные названия ───────────────────────────────────────────────────────
# «в Караганде», «у Samsung»: название, которого нет среди известных значений. Раньше такое слово
# молча отбрасывалось, и «выручка в Караганде» получала итог по всем городам как точный ответ
_ENTITY_AFTER_PREP_RE = re.compile(r"(?<![a-zа-яё])(?:в|во|из|у)\s+([a-zа-яё][a-zа-яё\-]{2,})")
_LATIN_WORD_RE = re.compile(r"(?<![a-z0-9])([a-z]{3,})(?![a-z0-9])")
_KNOWN_WORD_RE = re.compile(
    r"^(?:эт|прошл|последн|текущ|предыдущ|позапрошл|нынешн|следующ|будущ|квартал|месяц|год|недел|дн[еяи]|день|сутк|"
    r"выходн|будн|сезон|период|начал|конц|середин|перв|втор|трет|четв|пят|шест|целом|итог|сумм|общ|разрез|течени|"
    r"количеств|данн|наличи|процент|рубл|тенге|доллар|евро|валют|баз|таблиц|файл|отч[её]т|динамик|сравнени|топ|"
    r"рейтинг|абсолютн|деньг|штук|единиц|том|числ|как|котор|чем|сколько|средн|кажд|разн|разбивк|зависимост|минус|"
    r"плюс|убыт|прибыл|кого|чего|кем|них|н[её]м|не[йё]|всех|всем|нас|вас|нам|вам|компани|бизнес|сет[иь]|основн|"
    r"отдельн|сумме|онлайн|офлайн|интернет|розниц|опт|рамк|пределах|направлени|целях|виде|форм|порядк|строк|"
    r"колонк|поле|ход|итоге|результат|работ|продаж|выручк|заказ|чек|деталях|подробн|среднем)")
_KNOWN_LATIN = {"abc", "kpi", "sku", "vip", "mom", "top", "csv", "xlsx", "excel", "id", "vs", "yoy", "qoq", "pdf"}


def unresolved_words(q: str, values_by_column: dict[str, list[str]], roles: dict[str, str],
                     columns: list[str] | None = None) -> list[str]:
    """Слова-названия из вопроса, которые не удалось узнать: не период, не разрез, не показатель,
    не значение колонки и не служебное слово. Пустой список — вопрос понят целиком."""
    ql = q.lower()
    matched_tokens = {t for vals in detect_filters_detailed(ql, values_by_column).values() for t in vals.values()}
    col_stems = []
    if columns:
        from app.domain.ai.column_roles import column_stems
        col_stems = [st for c in columns for st in column_stems(c)]
    out = []
    candidates = [m.group(1) for m in _ENTITY_AFTER_PREP_RE.finditer(ql)]
    candidates += [w for w in _LATIN_WORD_RE.findall(ql) if w not in _KNOWN_LATIN and not re.fullmatch(r"q[1-4]", w)]
    for w in dict.fromkeys(candidates):
        if _KNOWN_WORD_RE.match(w) or _MONTH_RE.search(w):
            continue
        if any(p.search(w) for _, p in DIMENSION_PATTERNS) or any(p.search(w) for _, p in METRIC_PATTERNS):
            continue
        if any(re.search(token_pattern(t), w) for t in matched_tokens):
            continue
        if any(w.startswith(st) for st in col_stems):
            continue
        out.append(w)
    return out


def word_value_regex(word: str) -> str:
    """Регэксп (RE2/Python) для поиска значения по слову вопроса: «караганде» → «Караганда», «уфе» → «Уфа»."""
    w = word.lower()
    if len(w) <= 5:
        base = w[:-1] if len(w) >= 3 and w[-1] in _WORD_END else w
        return r"(^|[^a-zа-яё0-9])" + re.escape(base) + r"[a-zа-яё]{0,3}($|[^a-zа-яё0-9])"
    return r"(^|[^a-zа-яё0-9])" + re.escape(w[: max(5, len(w) - 2)])
