"""
Rule-based intent classifier for user questions.

Replaces the LLM-based classify_intent() which took ~30-50s per call.
Falls back to 'sql' for any analytical query that does not match known patterns.
"""
from __future__ import annotations

import re

# ── Pattern table (order matters: first match wins) ───────────────────────────
_INTENT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("kpi",        re.compile(r"\b(kpi|кпи|показател[иья]|ключев[ыеой]+ метрик[а-яё]*|общие показатели)\b", re.IGNORECASE)),
    ("comparison", re.compile(r"\b(сравни|сравнение|сравнить|динамика месяц к месяцу|vs|против|по сравнению|изменени[еяй])\b", re.IGNORECASE)),
    ("top",        re.compile(r"\b(топ|лучш[а-яё]*|худш[а-яё]*|рейтинг|наибол[а-яё]*|наимен[а-яё]*|самы[а-яё]*|больше всего|меньше всего)\b", re.IGNORECASE)),
    ("drop",       re.compile(r"\b(просадк[а-яё]*|падени[еяй]|слаб[а-яё]*|аномал[а-яё]*|убыт[а-яё]*|проблем[а-яё]*|выброс[а-яё]*|снижени[еяй])\b", re.IGNORECASE)),
    ("chart",      re.compile(r"\b(график|диаграмм[а-яё]*|визуализ[а-яё]*|тренд|построй|покажи.*график|нарисуй|динамик[ауи])\b", re.IGNORECASE)),
    ("summary",    re.compile(r"\b(общ[иеая]+ (итог|сводк|обзор)|резюме|обзор данных|общая картина|итоги)\b", re.IGNORECASE)),
]

# If the question also contains a period specifier, KPI → sql
_PERIOD_RE = re.compile(r"\b(за|в|период|год|месяц|квартал|недел|\d{4})\b")


def classify_intent(question: str) -> str:
    """Classify question intent using rule-based approach (O(n) regex, ~0ms).

    Returns one of: 'kpi', 'comparison', 'top', 'drop', 'chart', 'summary', 'sql'.
    """
    q = question.lower()
    has_period = bool(_PERIOD_RE.search(q))

    for intent, pattern in _INTENT_PATTERNS:
        if pattern.search(q):
            # KPI with a specific period indicator → treat as SQL
            if intent == "kpi" and has_period:
                return "sql"
            return intent

    return "sql"


# Backward-compatible alias used in llm.py
classify_intent_fast = classify_intent
