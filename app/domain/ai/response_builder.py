from __future__ import annotations
"""Response post-processing utilities.

Responsibilities:
 - Inject record count header into answers
 - Extract [CHART_KEY:col] annotations
 - Replace hallucinated currency (rubles) with the local one
 - Detect appropriate chart type from intent + data
"""

import json
import re
from typing import Any, Optional

# Имя колонки бывает со скобками: [CHART_KEY:count(DISTINCT customer_id)] — \w+ его не вырезал
_CHART_KEY_RE = re.compile(r"\[CHART_KEY:([^\]\n]+)\]")
_CHART_KEY_TAIL_RE = re.compile(r"\[CHART(?:_KEY)?[^\]\n]*$")
_EMPTY_PARENS_RE = re.compile(r"\s*\(\s*\)")
_CLARIFY_RE = re.compile(r"^\s*CLARIFY:\s*", re.MULTILINE)
_CJK_RE = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")


def has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def clean_answer(answer: str) -> str:
    """Убрать служебное: тег [CHART_KEY], в том числе обрезанный лимитом, пустые «()» и «CLARIFY:»."""
    if not answer:
        return answer
    answer = _CHART_KEY_RE.sub("", answer)
    answer = _CHART_KEY_TAIL_RE.sub("", answer.rstrip())
    answer = _EMPTY_PARENS_RE.sub("", answer)
    answer = _CLARIFY_RE.sub("", answer)
    answer = re.sub(r"[ \t]+\n", "\n", answer)
    return answer.strip()


def inject_count_header(answer: str, data: Any) -> str:
    """Prepend an exact record count header derived from len(data).

    This is the single source of truth for record counts in answers.
    The LLM is instructed to never mention counts — the header carries them.
    """
    if not isinstance(data, list) or len(data) <= 1:
        # Одна строка — итоговое число («общая выручка»): «Найдено: 1 записей» над ним только мешает
        return answer
    header = count_header(len(data))
    if answer.startswith(header):
        return answer
    return header + answer


def plural_records(n: int) -> str:
    """1 запись, 2 записи, 5 записей, 21 запись, 11 записей."""
    if n % 10 == 1 and n % 100 != 11:
        return "запись"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "записи"
    return "записей"


def count_header(n: int) -> str:
    return f"Найдено: {n} {plural_records(n)}.\n"


def extract_chart_key(answer: str) -> tuple[str, str | None]:
    """Extract [CHART_KEY:col] tag from answer and strip it. Returns (cleaned_answer, chart_key)."""
    m = _CHART_KEY_RE.search(answer)
    key = m.group(1).strip() if m else None
    return clean_answer(answer), key


# ── Currency normalization ────────────────────────────────────────────────────
# «руб.» посреди фразы съедаем вместе с точкой, в конце предложения точку оставляем
_RUB_ABBR_MID_RE = re.compile(r"\b(?i:руб)\.(?=[ \t]+[а-яёa-z0-9(])")
_RUB_WORD_RE = re.compile(r"\bруб(?:л(?:ь|я|ей|ям|ями|ях|е|ю))?\b|₽", re.IGNORECASE)
# «руб» — отдельным словом или в формах «рубль/рублей» (в т.ч. сумма_руб); «Рубашка», «рубин»,
# «рубрика» — не валюта и не должны отключать замену. Латиница: price_rub, RUB, но не rubber/ruby
_RUB_IN_DATA_RE = re.compile(r"(?<![а-яё])руб(?![а-км-яё])|₽|(?<![a-z])(?:rub|rur)(?![a-z])", re.IGNORECASE)


def normalize_currency(answer: str, data: Any = None, currency: str | None = None) -> str:
    """Заменить рубли в ответе на локальную валюту, если рублей нет в данных.

    Модель, отвечая по-русски, по привычке подписывает суммы рублями, даже когда
    валюта в данных не указана. Промпт это запрещает, но небольшие модели
    правило нарушают — здесь страховка на уровне кода.
    """
    if not answer:
        return answer
    if currency is None:
        from app.core.config import settings
        currency = settings.LOCAL_CURRENCY
    if "руб" in currency.lower():
        return answer  # локальная валюта и есть рубли — менять нечего
    if data is not None:
        try:
            data_text = json.dumps(data, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            data_text = str(data)
        if _RUB_IN_DATA_RE.search(data_text):
            return answer  # рубли действительно есть в данных
    # Функция вместо строки: валюту вводят в настройках, и «\1» или «\» в ней
    # не должны разбираться как шаблон замены re.sub (re.error ронял весь ответ)
    replacement = lambda _m: currency  # noqa: E731
    answer = _RUB_ABBR_MID_RE.sub(replacement, answer)
    return _RUB_WORD_RE.sub(replacement, answer)


# ── Chart type detection ───────────────────────────────────────────────────────
_DATE_KEYWORDS = {"date", "month", "year", "day", "period", "time", "week", "quarter"}


def _analyze_columns(data: list[dict]) -> dict:
    """Analyze data columns to determine types for chart selection."""
    if not data or not isinstance(data, list) or not isinstance(data[0], dict):
        return {"cat_cols": [], "num_cols": [], "date_cols": [], "row_count": 0}

    keys = list(data[0].keys())
    cat_cols, num_cols, date_cols = [], [], []

    for k in keys:
        k_lower = k.lower()
        sample_val = data[0].get(k)

        # Date column detection
        if any(w in k_lower for w in _DATE_KEYWORDS):
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
                # Константа из SQL (`'Спад в регионе' AS type`) одинакова во всех строках и
                # ничего не различает — категорией её не считаем, иначе ложный heatmap
                if len(data) > 1 and all(row.get(k) == sample_val for row in data):
                    continue
                cat_cols.append(k)

    return {
        "cat_cols": cat_cols,
        "num_cols": num_cols,
        "date_cols": date_cols,
        "row_count": len(data),
    }


def _has_negative_values(data: Any) -> bool:
    """Есть ли в числовых колонках отрицательные значения (падения в %, убытки)."""
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return False
    return any(isinstance(v, (int, float)) and not isinstance(v, bool) and v < 0 for row in data for v in row.values())


def detect_chart_type(intent: str, question: str, data: Any = None) -> Optional[str]:
    """Detect appropriate chart type based on question intent and data structure."""
    chart = _detect_chart_type(intent, question, data)
    if chart and (not isinstance(data, list) or len(data) < 2):
        # Один показатель («доля повторных клиентов 73 %») — график из одного элемента ничего не показывает
        return None
    # Доля от отрицательных чисел бессмысленна: столбцы с красными отрицательными значениями честнее
    if chart == 'pie' and (_has_negative_values(data) or not _has_additive_column(data)):
        return 'bar'
    return chart


_NON_ADDITIVE_RE = re.compile(r"avg|average|mean|средн|pct|percent|rate|margin|ratio|discount|price|check|чек|скидк")


def _has_additive_column(data: list) -> bool:
    """Есть ли числовая колонка, которую имеет смысл делить на доли (сумма, количество, доля)."""
    row = data[0] if data and isinstance(data[0], dict) else {}
    for k, v in row.items():
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        kl = k.lower()
        if "share" in kl or "доля" in kl or not _NON_ADDITIVE_RE.search(kl):
            return True
    return False


def _detect_chart_type(intent: str, question: str, data: Any = None) -> Optional[str]:
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
        s = _analyze_columns(data)

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
