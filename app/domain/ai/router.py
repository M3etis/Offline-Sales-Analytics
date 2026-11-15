"""Маршрутизация вопроса: готовый расчёт (app.services.insights) или генерация SQL моделью.

Возвращает план — имя расчёта и параметры — или None, если вопрос не типовой
(условия «среди тех, у кого…», ссылки «он/его» на прошлый ответ, нестандартные метрики).
Функция чистая: ей нужны только роли колонок, значения категорий и диапазон дат.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from app.domain.ai.question_parser import (
    COMPARISON_RE,
    COUNT_KEYS,
    detect_ascending,
    detect_dimensions,
    detect_filters,
    detect_filters_detailed,
    enumerated_columns,
    value_groups,
    detect_limit,
    detect_metric,
    detect_metrics,
    is_complex,
    EXTREME_RE,
    finish_period,
    METRIC_REQUIRES,
    column_mentions,
    mentioned_dimensions,
    last_full_month,
    parse_periods,
    previous_period,
)


@dataclass
class Plan:
    kind: str                      # имя функции в app.services.insights
    intent: str                    # тип запроса для истории, графика и примеров
    kwargs: dict[str, Any] = field(default_factory=dict)
    chart_type: Optional[str] = None
    value_key: Optional[str] = None
    extended: bool = False         # развёрнутый ответ с рекомендациями
    # Показатели, которые модель должна увидеть: все колонки сразу (8 метрик разного масштаба)
    # сбивали 7B-модель — она путала лидера и порядок строк
    show_metrics: list[str] = field(default_factory=list)


_ADDITIVE = {"revenue", "profit", "orders", "quantity"}
_SHARE_RE = re.compile(r"дол[яиюе]\b|доля|процент|структур|распределени")
_PRONOUN_RE = re.compile(r"\b(он|она|оно|они|его|её|ее|их|него|неё|нее|них|этот|эта|этого|этой|этих|там|тот|той)\b")
_ELLIPTIC_RE = re.compile(r"^\s*(а|и|ну а|а что|а если|а как|а теперь)\b")
# Короткое уточнение начинается с предлога: «по прибыли?», «за октябрь?», «в Алматы?».
# «Общая выручка?» и «Средний чек?» — самостоятельные вопросы, хоть и короткие
_ELLIPTIC_START_RE = re.compile(r"^\s*(по|за|в|во|на|среди|у|из|с|со|для)\b")
# «Сколько клиентов», «сколько всего уникальных товаров» — но не «сколько заработал менеджер»
# и не «сколько продано товаров» (там спрашивают сумму и штуки, а не число различных значений)
_COUNT_DIM_RE = re.compile(
    r"сколько\s+(?:(?:у\s+нас|всего|уникальн\w+|разн\w+|различн\w+|активн\w+|нов\w+)\s+)*"
    r"(клиент|покупател|товар|продукт|позици|регион|город|менеджер|сотрудник|категори|бренд|канал|склад)"
)
#: Возвраты товара — но не «возвращаемость клиентов» и не «клиенты не возвращаются»
RETURNS_RE = re.compile(r"возврат|возвращ[её]нн|(?<!не )возвраща(?:ют|ли|ет|ются|лись|ло)(?![а-яё])")
_RETENTION_RE = re.compile(r"повторн|больше одного раза|более одного раза|несколько раз|лояльн|удержан|возвращаемост")
_CHURN_RE = re.compile(r"перестал|отток|ушл\w*\s+клиент|ушедш|потерян|неактивн|давно не покуп|не возвраща")
_PERIOD_WORDS_RE = re.compile(r"(?<![а-яё])(?:месяц|квартал|недел|год[ау]?(?![а-яё])|дн[ея]й|вчера|сегодня|20\d{2})")
_TIMELINE_RE = re.compile(r"по месяц|помесячн|динамик|тренд|по недел|по дням|сезонн|в течение года")
_COUNT_DIM_MAP = {"клиент": "customer", "покупател": "customer", "товар": "product", "продукт": "product",
                  "позици": "product", "регион": "region", "город": "region", "менеджер": "manager",
                  "сотрудник": "manager", "категори": "category", "бренд": "brand", "канал": "channel",
                  "склад": "warehouse"}


_DIM_LABELS = {"category": "категория", "region": "регион / город", "manager": "менеджер", "channel": "канал продаж",
               "brand": "бренд", "warehouse": "склад / магазин", "status": "статус заказа", "customer": "клиент",
               "product": "товар", "weekday": "дата"}
_METRIC_LABELS_REQ = {"profit": "прибыль", "margin_pct": "прибыль (для маржинальности)", "quantity": "количество",
                      "avg_discount": "скидка"}


def _is_elliptic(q: str) -> bool:
    ql = q.lower()
    return bool(_ELLIPTIC_RE.search(ql)) or (len(re.findall(r"\w+", q)) <= 3 and bool(_ELLIPTIC_START_RE.search(ql)))


def _drop_modifier_dims(q: str, dims: list[str]) -> list[str]:
    """«Продажи товаров по регионам» — разрез по регионам, «товаров» лишь уточняет показатель.

    Слово разреза сразу после показателя («продажи товаров», «средний чек клиентов») или после
    другого слова разреза («категориям товаров») — определение, а не второй разрез.
    """
    if not dims:
        return dims
    from app.domain.ai.question_parser import DIMENSION_PATTERNS, METRIC_PATTERNS

    # Единственный разрез-определение без признаков рейтинга («сколько продано товаров») — это итог
    sole_ok = len(dims) == 1 and bool(re.search(r"сколько|на как\w+ сумм|(?<![а-яё])общ", q)) and not re.search(
        r"топ|рейтинг|лучш|худш|самы|больше всего|меньше всего|кажд|(?<![а-яё])по\s|разрез|разбивк|лидер|"
        r"(?<![а-яё])(?:какой|какая|какое|какие|кто)(?![а-яё])", q)
    if len(dims) < 2 and not sole_ok:
        return dims

    pats = dict(DIMENSION_PATTERNS)
    keep = []
    for d in dims:
        m = pats[d].search(q)
        before = q[: m.start()] if m else ""
        prev_word = (re.findall(r"[a-zа-яё]+", before) or [""])[-1]
        modifier = bool(prev_word) and (
            any(p.search(prev_word) for _, p in METRIC_PATTERNS) or any(p.search(prev_word) for _, p in DIMENSION_PATTERNS)
            or prev_word in ("средний", "средняя", "среднее", "чек"))
        if not modifier:
            keep.append(d)
    if sole_ok:
        return keep
    return keep or dims


def route(question: str, roles: dict[str, str], values: dict[str, list[str]],
          data_start: Optional[date], data_end: Optional[date],
          prev_question: Optional[str] = None,
          extra_filters: Optional[dict[str, list[str]]] = None) -> Optional[Plan]:
    """extra_filters — значения, найденные в данных за пределами списка values (колонки с сотнями значений)."""
    q = question.lower()
    # «этот месяц / этот год» — период, а не ссылка на прошлый ответ
    if _PRONOUN_RE.search(re.sub(r"(?<![а-яё])(?:эт\w+|текущ\w*)\s+(?:месяц|год|квартал|недел)\w*", " ", q)):
        # «в каких регионах он продаёт» — «он» из прошлого ответа: это SQL с контекстом диалога
        return None
    if "product" not in roles and "sku" in roles:
        # Товар без названия — по артикулу: «топ товаров» на выгрузке «артикул, количество, сумма»
        roles = {**roles, "product": roles["sku"]}

    dims = _drop_modifier_dims(q, detect_dimensions(q, roles))
    metric = detect_metric(q)
    metrics_all = detect_metrics(q)
    periods = parse_periods(q, data_start, data_end) if data_end and data_start else []
    filters = detect_filters(q, values)
    for col, vals in (extra_filters or {}).items():
        filters.setdefault(col, list(vals))
    limit = detect_limit(q)
    asc = detect_ascending(q)

    # Уточнение к прошлому вопросу: «а по прибыли?» — разрез и период оттуда, метрика отсюда
    if prev_question and _is_elliptic(question):
        pq = prev_question.lower()
        dims = dims or detect_dimensions(pq, roles)
        metric = metric or detect_metric(pq)
        metrics_all = metrics_all or detect_metrics(pq)
        if not periods and data_end and data_start:
            periods = parse_periods(pq, data_start, data_end)
        filters = filters or detect_filters(pq, values)
        limit = limit or detect_limit(pq)
        q = pq + " " + q

    # Колонки, названные своими словами («по типу оплаты»), — разрезы любого набора данных
    role_cols = {v for v in roles.values()}
    extra_cols = column_mentions(q, list(values), skip=role_cols)
    # Колонка с ролью, названная своими словами («по торговым точкам» → торговая_точка) — тоже разрез
    for col in column_mentions(q, [c for c in values if c in role_cols]):
        role = next(r for r, c in roles.items() if c == col)
        if role not in dims:
            dims.append(role)

    # Вопрос про разрез или показатель, которого в данных нет («категории» при данных без категорий):
    # готовый расчёт ответил бы общим итогом — не на тот вопрос. Пусть разбирается SQL-путь
    # Слова, узнанные как значения («канал» в «Онлайн-канал»), — не упоминание разреза «канал продаж»
    q_dims = q
    for matched in detect_filters_detailed(q, values).values():
        for v in matched:
            for t in re.split(r"[^a-zа-яё0-9]+", str(v).lower()):
                if len(t) >= 4:
                    q_dims = re.sub(r"(?<![a-zа-яё])" + re.escape(t[: max(4, len(t) - 2)]) + r"[a-zа-яё]*", " ", q_dims)
    known_dims = set(dims) | ({"product"} if "product" in roles else set())
    missing = [_DIM_LABELS.get(d, d) for d in mentioned_dimensions(q_dims) if d not in known_dims and d not in roles] \
        if not extra_cols else []
    # Нужная колонка — только для главного показателя: «на какую сумму продано товаров» — вопрос о выручке
    if metric and METRIC_REQUIRES.get(metric) and METRIC_REQUIRES[metric] not in roles:
        missing.append(_METRIC_LABELS_REQ[metric])
    if (_RETENTION_RE.search(q) or _CHURN_RE.search(q)) \
            and "customer" not in roles and not extra_cols:  # «с картой лояльности» — колонка, а не клиенты
        missing.append("клиент")
    if RETURNS_RE.search(q) and "returns" not in roles and "status" not in roles:
        missing.append("возвраты")
    if "date" not in roles and (periods or parse_periods(q, date(2000, 1, 1), date(2099, 12, 31))
                                or _PERIOD_WORDS_RE.search(q) or _TIMELINE_RE.search(q)):
        # Без колонки даты период не применить: раньше отвечали итогом за всё время
        missing.append("дата")
    if re.search(r"убыт|в минус", q) and "profit" not in roles:
        missing.append("прибыль")
    if missing:
        # SQL-путь при нехватке колонки выдумывал: «297 клиентов» без колонки клиента, склады = города.
        # Отвечаем честно без модели
        return Plan("missing", "sql", {"concepts": list(dict.fromkeys(missing))})

    # Колонки фильтров с несколькими значениями («в Астане и Алматы») — это разрез, а не фильтр
    multi = [c for c in enumerated_columns(q, values) if c in filters]
    dim_cols = {roles.get(d, d) for d in dims} | set(extra_cols)
    # «Продажи по типам оплаты»: «оплат» есть и в имени колонки, и в значении «Онлайн-оплата» —
    # колонка-разрез не может быть ещё и фильтром, если значения не перечислены явно
    filters = {c: v for c, v in filters.items() if c not in dim_cols or c in multi}
    # Перечисление, узнанное по части значения («Алматы» в «Флагман Алматы, ТРЦ …»), группируем по словам
    groups = {}
    for c in multi:
        g = value_groups(q, values, c)
        if any(len(v) > 1 for v in g.values()):
            groups = {"col": c, "groups": g}
            break
    group_dims = ([] if groups else multi) + [d for d in dims if roles.get(d) not in multi] + \
        [c for c in extra_cols if c not in multi]
    period = periods[0] if len(periods) == 1 else None
    fl = filters or None

    def bd(intent: str = "top", chart: Optional[str] = None) -> Plan:
        m = metric or "revenue"
        gd = group_dims[:1] if groups else group_dims[:2]
        if chart is None:
            chart = "heatmap" if len(gd) > 1 else ("pie" if _SHARE_RE.search(q) and m in _ADDITIVE and not asc else "bar")
        shown = list(dict.fromkeys([m] + metrics_all))
        kwargs = dict(dims=gd, metric=m, ascending=asc, limit=limit, period=period, filters=filters or None)
        if groups:
            kwargs["value_groups"] = groups
            if chart is None or chart == "bar":
                chart = "heatmap" if gd else "bar"
        return Plan("breakdown", intent, kwargs, chart, m, show_metrics=shown)

    # ── Особые вопросы ───────────────────────────────────────────────────────
    if re.search(r"прогноз|спрогноз|предскаж|ожидаем\w*\s+выручк|что будет", q):
        return Plan("forecast", "forecast", {}, "bar", "revenue")
    if re.search(r"\babc\b|\bавс\b|abc-|парето", q):
        dim = dims[0] if dims else "product"
        return Plan("abc", "top", {"dim": dim, "period": period, "filters": fl}, "bar", "revenue")
    # Клиентские вопросы — раньше возвратов: «возвращаемость клиентов» — не возвраты товара
    if "customer" in roles and _CHURN_RE.search(q):
        return Plan("churned_customers", "sql", {"filters": fl}, None, "revenue")
    if "customer" in roles and _RETENTION_RE.search(q):
        return Plan("customers_overview", "sql", {"period": period, "filters": fl}, None, None)
    if RETURNS_RE.search(q) and not re.search(r"статус", q):
        return Plan("returns_overview", "sql", {"period": period, "filters": fl, "dim": dims[0] if dims else None},
                    "bar", "returned_units")
    if re.search(r"скидк", q) and re.search(r"влия|эффект|зависи|связ|окупа", q):
        return Plan("discount_effect", "sql", {"period": period, "filters": fl}, "bar", "margin_pct",
                    show_metrics=["orders", "avg_check", "margin_pct"])
    if re.search(r"убыт|в минус|отрицательн\w*\s+прибыл", q):
        return Plan("loss_items", "drop", {"dim": dims[0] if dims else "product", "period": period, "filters": fl},
                    "bar", "profit", show_metrics=["profit", "margin_pct"])
    if re.search(r"сводк|резюме|обзор|итоги\b|общ\w+ картин|рекомендац|как увеличить|как повысить|что улучшить|для руководства|точки роста", q):
        return Plan("summary_pack", "summary", {}, "pie", "revenue",
                    extended=bool(re.search(r"рекомендац|как увеличить|как повысить|что улучшить|точки роста", q)))
    # «падают» — но не «попадают в топ»
    if re.search(r"(?<![а-яё])пада|упал|снижа|сниз|просед|просадк|падени|аномал|слаб\w*\s+мест|проблемн|отстающ", q):
        if dims:
            gran = "quarter" if "квартал" in q else "month"
            return Plan("change_by_dimension", "drop",
                        {"dim": dims[0], "granularity": gran, "metric": metric or "revenue", "filters": fl},
                        "bar", "change_pct")
        return Plan("weak_spots", "drop", {}, "bar", "value")

    if is_complex(q):
        return None

    # ── Сравнения ────────────────────────────────────────────────────────────
    if re.search(r"месяц к месяцу|м/м|mom\b|по месяцам.*((?<![а-яё])рост|измен)|((?<![а-яё])рост|измен)\w*.*по месяцам", q):
        if group_dims or groups:
            return None  # динамика по каждому значению разреза — готового расчёта нет, пусть строит SQL
        return Plan("timeline", "chart", {"granularity": "month", "filters": filters or None}, "line", "revenue",
                    show_metrics=["revenue", "revenue_change_pct"])
    if COMPARISON_RE.search(q):
        if len(periods) >= 2:
            ps = sorted(periods[:2], key=lambda p: p.start)
            return Plan("compare", "comparison", {"periods": ps, "filters": filters or None}, "bar", metric or "revenue")
        if len(periods) == 1:
            prev = finish_period(previous_period(periods[0]), data_start, data_end)
            return Plan("compare", "comparison", {"periods": [prev, periods[0]],
                                                  "filters": filters or None}, "bar", metric or "revenue")
        if group_dims or groups:
            return bd("comparison")
        if data_end:
            cur = finish_period(last_full_month(data_end), data_start, data_end)
            prev = finish_period(previous_period(cur), data_start, data_end)
            return Plan("compare", "comparison", {"periods": [prev, cur], "filters": filters or None},
                        "bar", metric or "revenue")

    # ── Динамика ─────────────────────────────────────────────────────────────
    if _TIMELINE_RE.search(q) and group_dims:
        return None  # «динамика продаж по категориям»: ряд по каждой категории — это SQL, а не итог за всё время
    if _TIMELINE_RE.search(q) and not group_dims:
        gran = "week" if "недел" in q else "day" if "по дням" in q else "month"
        return Plan("timeline", "chart", {"granularity": gran, "period": period, "filters": filters or None},
                    "line", "revenue", show_metrics=list(dict.fromkeys(["revenue", "revenue_change_pct"] + metrics_all)))

    # ── Подсчёт уникальных: «сколько клиентов», «сколько товаров» ───────────
    m = _COUNT_DIM_RE.search(q)
    if m:
        dim = _COUNT_DIM_MAP[m.group(1)]
        if dim in roles:
            return Plan("totals", "kpi", {"period": period, "filters": filters or None, "count_dims": [dim]}, None, None,
                        show_metrics=[COUNT_KEYS.get(dim, dim)])

    # ── Разрезы ──────────────────────────────────────────────────────────────
    if group_dims or groups:
        return bd()

    # ── Итоги ────────────────────────────────────────────────────────────────
    if len(periods) >= 2:
        ps = sorted(periods[:2], key=lambda p: p.start)
        return Plan("compare", "comparison", {"periods": ps, "filters": filters or None}, "bar", metric or "revenue")
    if EXTREME_RE.search(q):
        return None  # «какая максимальная скидка», «самый крупный заказ» — не сумма и не среднее
    if metric or period or filters or re.search(r"сколько|общ\w+|итог|всего|ключев|kpi|кпи|показател", q):
        broad = re.search(r"ключев|kpi|кпи|показател|итог", q)
        return Plan("totals", "kpi", {"period": period, "filters": filters or None}, None, None,
                    show_metrics=[] if broad else metrics_all)
    return None
