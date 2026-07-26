from __future__ import annotations
"""Response post-processing utilities.

Responsibilities:
 - Inject record count header into answers
 - Extract [CHART_KEY:col] annotations
 - Detect appropriate chart type from intent + data
"""

import re
from typing import Any, Optional

_CHART_KEY_RE = re.compile(r"\[CHART_KEY:(\w+)\]")


def inject_count_header(answer: str, data: Any) -> str:
    """Prepend an exact record count header.

    This is the single source of truth for row counts in responses.
    The LLM is instructed never to mention counts — the header carries them.
    """
    if not isinstance(data, list) or len(data) == 0:
        return answer
    header = f"Найдено: {len(data)} записей.\n"
    return answer if answer.startswith(header) else header + answer


def extract_chart_key(answer: str) -> tuple[str, str | None]:
    """Strip [CHART_KEY:col] tag from answer and return (cleaned_answer, key)."""
    m = _CHART_KEY_RE.search(answer)
    if m:
        return _CHART_KEY_RE.sub("", answer).strip(), m.group(1)
    return answer, None


# ── Chart type detection ───────────────────────────────────────────────────────
_DATE_KEYWORDS = {"date", "month", "year", "day", "period", "time", "week", "quarter"}


def _analyze_columns(data: list[dict]) -> dict:
    """Classify columns of first row into cat / num / date."""
    if not data or not isinstance(data[0], dict):
        return {"cat_cols": [], "num_cols": [], "date_cols": [], "row_count": 0}

    cat_cols, num_cols, date_cols = [], [], []
    for k, v in data[0].items():
        k_lower = k.lower()
        if any(w in k_lower for w in _DATE_KEYWORDS):
            date_cols.append(k)
        elif isinstance(v, (int, float)):
            num_cols.append(k)
        elif isinstance(v, str):
            try:
                float(v.replace(",", "."))
                num_cols.append(k)
            except ValueError:
                cat_cols.append(k)

    return {
        "cat_cols": cat_cols,
        "num_cols": num_cols,
        "date_cols": date_cols,
        "row_count": len(data),
    }


def detect_chart_type(intent: str, question: str, data: Any = None) -> Optional[str]:
    """Detect appropriate chart type from intent + question keywords + data structure."""
    q = question.lower()

    # 1. Explicit user request
    if any(w in q for w in ["тепловая карта", "heatmap"]):
        return "heatmap"
    if any(w in q for w in ["scatter", "точечн", "корреляц"]):
        return "scatter"

    # 2. Strong intent / keyword signals
    if intent == "top" or any(w in q for w in ["топ", "лучш", "рейтинг", "сравни", "сравнение"]):
        return "bar"
    if any(w in q for w in ["график", "динамика", "тренд", "по месяцам", "по дням", "по неделям"]):
        return "line"
    if any(w in q for w in ["доля", "распределение", "структура", "процент"]):
        return "pie"

    # 3. Data-structure based
    if isinstance(data, list) and len(data) > 1 and isinstance(data[0], dict):
        s = _analyze_columns(data)
        if s["date_cols"] and s["num_cols"]:
            return "line"
        if len(s["cat_cols"]) >= 1 and len(s["num_cols"]) >= 1 and s["row_count"] <= 8:
            return "pie"
        if len(s["cat_cols"]) >= 2 and len(s["num_cols"]) >= 1 and 4 <= s["row_count"] <= 100:
            return "heatmap"
        if s["cat_cols"] and s["num_cols"]:
            return "bar"
        if len(s["num_cols"]) >= 2 and not s["cat_cols"] and not s["date_cols"] and s["row_count"] >= 3:
            return "scatter"

    # 4. Fallback for chart intent
    return "line" if intent == "chart" else None
