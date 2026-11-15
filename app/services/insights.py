"""Готовые расчёты для типовых вопросов аналитика и маркетолога.

Модель 7B ошибается в SQL для стандартных вопросов: периоды, сортировка по метрике,
определения (маржа, средний чек, повторные клиенты). Здесь эти вопросы считаются
проверенными запросами, а модель только формулирует ответ по точным цифрам.

Каждая функция возвращает (rows, note): строки для графика и таблицы и пояснение
для модели — что именно посчитано и за какой период.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Optional

from app.domain.ai.column_roles import infer_roles
from app.domain.ai.question_parser import (
    COUNT_KEYS,
    METRIC_LABELS,
    MONTH_NAMES,
    Period,
    finish_period,
    last_full_month,
    last_full_quarter,
    previous_period,
)
from app.services import analytics as an

logger = logging.getLogger(__name__)

STATUS_LABELS = {
    "completed": "завершён", "complete": "завершён", "done": "завершён", "paid": "оплачен",
    "cancelled": "отменён", "canceled": "отменён", "returned": "возвращён", "refunded": "возвращён",
    "pending": "в обработке", "processing": "в обработке", "new": "новый", "shipped": "отправлен",
}

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

#: Порог оттока: клиент без покупок столько дней до последней даты в данных
CHURN_DAYS = 90


@dataclass
class TableCtx:
    table: str
    roles: dict[str, str]
    columns: list[str]
    col_types: dict[str, str]
    date_expr: Optional[str] = None
    data_start: Optional[date] = None
    data_end: Optional[date] = None
    values: dict[str, list[str]] = field(default_factory=dict)
    #: Скидка в процентах при любом хранении (доля 0,15, проценты 15 или сумма) — SQL-выражение
    discount_pct: Optional[str] = None

    def col(self, role: str) -> Optional[str]:
        c = self.roles.get(role)
        if not c:
            return None
        # Вычисляемая роль («цена × количество») — уже SQL-выражение
        return c if c.startswith("(") else f'"{c}"'


def _to_date(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.fromisoformat(str(v)[:19]).date()
    except ValueError:
        return None


_VALUES_CACHE: dict[tuple[str, int], dict[str, list[str]]] = {}


def build_ctx(conn, dataset_id: Optional[str], manifest: Optional[dict]) -> Optional[TableCtx]:
    """Метаданные таблицы для расчётов; None — если таблица не похожа на продажи."""
    from app.shared.prompts.templates import date_expression

    try:
        table = manifest["table_name"] if manifest else an._get_dataset_meta(conn, dataset_id)
        cols = conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position",
            [table],
        ).fetchall()
    except Exception as e:
        logger.warning(f"insights.build_ctx failed: {e}")
        return None
    if not cols:
        return None
    columns = [c[0] for c in cols]
    col_types = {c[0]: c[1] for c in cols}
    from app.services.column_dict import user_aliases

    roles = infer_roles(cols, _column_stats(conn, table, cols, manifest), user_aliases(conn))
    if "revenue" not in roles:
        return None
    ctx = TableCtx(table=table, roles=roles, columns=columns, col_types=col_types)
    if "date" in roles:
        ctx.date_expr = date_expression(f'"{roles["date"]}"', col_types.get(roles["date"]))
        if col_types.get(roles["date"]) == "VARCHAR":
            # Дата строкой, которую миграция не перевела («15.11.2025») — то же выражение, что у дашборда
            ctx.date_expr = an.text_date(f'"{roles["date"]}"')
        try:
            lo, hi = conn.execute(f"SELECT MIN({ctx.date_expr}), MAX({ctx.date_expr}) FROM {table}").fetchone()
            ctx.data_start, ctx.data_end = _to_date(lo), _to_date(hi)
        except Exception as e:
            logger.warning(f"insights: date range failed: {e}")
            ctx.date_expr = None
    ctx.values = categorical_values(conn, table, col_types)
    ctx.discount_pct = _discount_pct(ctx, conn)
    return ctx


def _column_stats(conn, table: str, cols: list, manifest: Optional[dict]) -> dict[str, dict]:
    """Число различных значений и строк по колонкам — из манифеста или одним запросом."""
    profiles = (manifest or {}).get("profiles") or {}
    rows = (manifest or {}).get("row_count")
    if profiles and rows:
        return {n: {"distinct": p.get("distinct_count"), "rows": rows} for n, p in profiles.items()}
    try:
        parts = ", ".join(f'COUNT(DISTINCT "{c[0]}")' for c in cols)
        r = conn.execute(f"SELECT COUNT(*), {parts} FROM {table}").fetchone()
        return {c[0]: {"distinct": r[i + 1], "rows": r[0]} for i, c in enumerate(cols)}
    except Exception:
        return {}


def categorical_values(conn, table: str, col_types: dict[str, str], max_distinct: int = 60) -> dict[str, list[str]]:
    """Все значения текстовых колонок с небольшим числом вариантов — для распознавания фильтров."""
    try:
        row_count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except Exception:
        return {}
    key = (table, row_count)
    if key in _VALUES_CACHE:
        return _VALUES_CACHE[key]
    out: dict[str, list[str]] = {}
    for col, typ in col_types.items():
        if typ != "VARCHAR":
            continue
        q = f'"{col}"'
        try:
            n = conn.execute(f"SELECT COUNT(DISTINCT {q}) FROM {table}").fetchone()[0]
            if 0 < n <= max_distinct:
                out[col] = [r[0] for r in conn.execute(f"SELECT DISTINCT {q} FROM {table} WHERE {q} IS NOT NULL ORDER BY 1").fetchall()]
        except Exception:
            continue
    _VALUES_CACHE[key] = out
    return out


# ── SQL-строительные блоки ────────────────────────────────────────────────────

def metric_exprs(ctx: TableCtx) -> dict[str, str]:
    rev = an._num(ctx.col("revenue"))
    exprs = {"revenue": f"ROUND(SUM({rev}), 2)"}
    if ctx.col("profit"):
        prof = an._num(ctx.col("profit"))
        exprs["profit"] = f"ROUND(SUM({prof}), 2)"
        exprs["margin_pct"] = f"ROUND(SUM({prof}) * 100.0 / NULLIF(SUM({rev}), 0), 1)"
    orders = f"COUNT(DISTINCT {ctx.col('order_id')})" if ctx.col("order_id") else "COUNT(*)"
    exprs["orders"] = orders
    exprs["avg_check"] = f"ROUND(SUM({rev}) / NULLIF({orders}, 0), 2)"
    if ctx.col("quantity"):
        exprs["quantity"] = f"SUM({an._num(ctx.col('quantity'))})"
    if ctx.col("discount"):
        exprs["avg_discount"] = f"ROUND(AVG({ctx.discount_pct or an._num(ctx.col('discount'))}), 2)"
    return exprs


def _where(ctx: TableCtx, period: Optional[Period] = None, filters: Optional[dict[str, list[str]]] = None) -> tuple[str, list]:
    conds, params = ["1=1"], []
    if period and ctx.date_expr:
        conds.append(f"{ctx.date_expr} >= ? AND {ctx.date_expr} < ?")
        params += [datetime.combine(period.start, datetime.min.time()), datetime.combine(period.end, datetime.min.time())]
    for col, vals in (filters or {}).items():
        if not vals:
            continue
        conds.append(f'CAST("{col}" AS VARCHAR) IN ({", ".join("?" for _ in vals)})')
        params += [str(v) for v in vals]
    return " AND ".join(conds), params


def _dim_expr(ctx: TableCtx, dim: str) -> Optional[tuple[str, str]]:
    """(SQL-выражение, имя колонки результата) для разреза."""
    if dim == "weekday":
        if not ctx.date_expr:
            return None
        cases = " ".join(f"WHEN {i + 1} THEN '{d}'" for i, d in enumerate(WEEKDAYS))
        return f"CASE isodow({ctx.date_expr}) {cases} END", "weekday"
    col = ctx.roles.get(dim)
    if not col:
        # Разрез по колонке фильтра (например, region) — передаётся реальным именем
        if dim in ctx.columns:
            return f'CAST("{dim}" AS VARCHAR)', dim
        return None
    if col.startswith("("):
        return None  # вычисляемая роль — не разрез
    # Сначала в текст, потом подпись: COALESCE(число, 'Не указано') в DuckDB — ошибка преобразования
    return f"COALESCE(CAST(\"{col}\" AS VARCHAR), 'Не указано')", col


def _run(conn, sql: str, params: list) -> list[dict]:
    return an._safe_query_params(conn, sql, params or None)


def _label_status(rows: list[dict], col: Optional[str]) -> None:
    if not col:
        return
    for r in rows:
        v = r.get(col)
        if isinstance(v, str) and v.lower() in STATUS_LABELS:
            r[col] = STATUS_LABELS[v.lower()]


# ── Расчёты ───────────────────────────────────────────────────────────────────

def totals(conn, ctx: TableCtx, period: Optional[Period] = None, filters: Optional[dict] = None,
           count_dims: Optional[list[str]] = None) -> tuple[list[dict], str]:
    m = metric_exprs(ctx)
    extra = ""
    for dim in dict.fromkeys(["customer"] + list(count_dims or [])):
        if ctx.col(dim) and dim in COUNT_KEYS:
            extra += f", COUNT(DISTINCT {ctx.col(dim)}) AS {COUNT_KEYS[dim]}"
    where, params = _where(ctx, period, filters)
    sql = f"SELECT {', '.join(f'{e} AS {k}' for k, e in m.items())}{extra} FROM {ctx.table} WHERE {where}"
    rows = _run(conn, sql, params)
    notes = [_period_note(ctx, period), _filters_note(filters), DEFINITIONS_NOTE]
    return rows, " ".join(n for n in notes if n)


DEFINITIONS_NOTE = ("Средний чек = выручка / число заказов; маржинальность = прибыль / выручка × 100 %; "
                    "выручка включает все статусы заказов, если не сказано иное.")


def _period_note(ctx: TableCtx, period: Optional[Period]) -> str:
    if period:
        # «Выручка за прошлый месяц» — модель писала «за прошлый месяц нет данных, а за октябрь…»
        return f"Запрошенный период: {_period_span(period)} — все данные ниже именно за него."
    if ctx.data_start and ctx.data_end:
        return f"Период: все данные ({ctx.data_start:%d.%m.%Y}–{ctx.data_end:%d.%m.%Y})."
    return ""


def _filters_note(filters: Optional[dict]) -> str:
    if not filters:
        return ""
    parts = [f"{col} = {', '.join(map(str, vals))}" for col, vals in filters.items() if vals]
    return f"Фильтр: {'; '.join(parts)}." if parts else ""


def breakdown(conn, ctx: TableCtx, dims: list[str], metric: str = "revenue", ascending: bool = False,
              limit: Optional[int] = None, period: Optional[Period] = None,
              filters: Optional[dict] = None, value_groups: Optional[dict] = None) -> tuple[list[dict], str]:
    """Метрики в разрезе одного-двух измерений, отсортированные по нужной метрике.

    value_groups — {"col": колонка, "groups": {подпись: [значения]}}: «Алматы и Астана», когда город —
    часть названия точки. Группа становится первым измерением.
    """
    m = metric_exprs(ctx)
    if metric not in m:
        metric = "revenue"
    dim_parts = [d for d in (_dim_expr(ctx, d) for d in dims) if d]
    if value_groups and value_groups.get("groups"):
        col = value_groups["col"]
        cases = " ".join(
            "WHEN CAST(\"{c}\" AS VARCHAR) IN ({vals}) THEN '{lbl}'".format(
                c=col, vals=", ".join("'" + str(v).replace("'", "''") + "'" for v in vs), lbl=str(lbl).replace("'", "''"))
            for lbl, vs in value_groups["groups"].items())
        dim_parts = [(f"CASE {cases} END", "группа")] + dim_parts
    if not dim_parts:
        return [], ""
    select_dims = ", ".join(f"{expr} AS \"{name}\"" for expr, name in dim_parts)
    group = ", ".join(str(i + 1) for i in range(len(dim_parts)))
    rev = an._num(ctx.col("revenue"))
    share = f"ROUND(SUM({rev}) * 100.0 / NULLIF(SUM(SUM({rev})) OVER (), 0), 1) AS revenue_share_pct"
    if metric in ("profit", "orders", "quantity"):
        # «Распределение заказов по статусам» — доля заказов, а не выручки
        share += f", ROUND({m[metric]} * 100.0 / NULLIF(SUM({m[metric]}) OVER (), 0), 1) AS {metric}_share_pct"
    # Главная метрика — первой после разреза: по ней строится график
    ordered = [metric] + [k for k in m if k != metric]
    metrics_sql = ", ".join(f"{m[k]} AS {k}" for k in ordered)
    where, params = _where(ctx, period, filters)
    order = "ASC" if ascending else "DESC"
    lim = f" LIMIT {int(limit)}" if limit else " LIMIT 100"
    sql = (f"SELECT {select_dims}, {metrics_sql}, {share} FROM {ctx.table} WHERE {where} "
           f"GROUP BY {group} ORDER BY {metric} {order} NULLS LAST{lim}")
    rows = _run(conn, sql, params)
    if "status" in dims:
        _label_status(rows, ctx.roles.get("status"))
    sort_label = METRIC_LABELS.get(metric, metric)
    shown = int(limit) if limit else 100
    truncated = ""
    if len(rows) >= shown:
        # Сколько групп всего: строки выше — только первые, их сумма — не итог
        exprs = ", ".join(expr for expr, _ in dim_parts)
        n = _run(conn, f"SELECT COUNT(*) AS n FROM (SELECT {exprs} FROM {ctx.table} WHERE {where} GROUP BY {group})", params)
        if n and n[0]["n"] > len(rows):
            truncated = f"Всего групп: {n[0]['n']}; ниже — первые {len(rows)} по этому порядку, их сумма не равна итогу."
    ratio = ""
    if len(rows) == 2 and len(dim_parts) == 1 and metric in ("revenue", "profit", "orders", "quantity"):
        # Отношение двух групп считает код: модель писала «в 7,4 раза» при реальных 4,06
        name = dim_parts[0][1]
        (a, b) = sorted(rows, key=lambda r: r.get(metric) or 0, reverse=True)
        # Отношение отрицательных значений («убыток −50 и −400 — в 0,12 раза») бессмысленно
        if (b.get(metric) or 0) > 0 and (a.get(metric) or 0) > 0:
            k = a[metric] / b[metric]
            ratio = (f"«{a[name]}» больше «{b[name]}» по показателю «{sort_label}» в {f'{k:.2f}'.replace('.', ',')} раза "
                     f"(на {f'{(k - 1) * 100:.1f}'.replace('.', ',')} %).")
    leaders = _group_leaders(conn, ctx, dim_parts, m, metric, where, params) if len(dim_parts) == 2 else ""
    notes = [f"Отсортировано по показателю «{sort_label}» ({'по возрастанию' if ascending else 'по убыванию'}).", truncated,
             ratio, leaders, _period_note(ctx, period), _filters_note(filters), DEFINITIONS_NOTE]
    return rows, " ".join(n for n in notes if n)


def _group_leaders(conn, ctx: TableCtx, dim_parts: list[tuple[str, str]], m: dict[str, str], metric: str,
                   where: str, params: list) -> str:
    """Для разреза из двух измерений — итог и лидер внутри каждой группы первого измерения.

    Считается отдельным запросом по всем данным: строки разреза обрезаны лимитом, и сумма по ним
    давала «Астана: итого 13,72 млн» при настоящих 184,5 млн и не того лидера.
    """
    from app.domain.ai.data_format import format_value, kind_of

    (g_expr, g), (i_expr, item) = dim_parts
    additive = metric in ("revenue", "profit", "orders", "quantity")
    totals = {}
    if additive:
        for r in _run(conn, f"SELECT {g_expr} AS g, {m[metric]} AS v FROM {ctx.table} WHERE {where} GROUP BY 1", params):
            totals[r["g"]] = r["v"]
    best = _run(conn, f"""
        WITH p AS (SELECT {g_expr} AS g, {i_expr} AS i, {m[metric]} AS v FROM {ctx.table} WHERE {where} GROUP BY 1, 2)
        SELECT g, arg_max(i, v) AS leader, MAX(v) AS best FROM p WHERE v IS NOT NULL GROUP BY 1""", params)
    if not best:
        return ""
    kind = kind_of(metric, [r["best"] for r in best] + list(totals.values()))
    order = sorted(best, key=lambda r: (totals.get(r["g"]) if additive else r["best"]) or 0, reverse=True)[:30]
    parts = []
    for r in order:
        total = f"итого {format_value(totals.get(r['g']), kind)}, " if additive else ""
        parts.append(f"{r['g']}: {total}лидер {r['leader']} — {format_value(r['best'], kind)}")
    return f"По группам «{g}» ({METRIC_LABELS.get(metric, metric)}): " + "; ".join(parts) + "."


def compare(conn, ctx: TableCtx, periods: list[Period], filters: Optional[dict] = None) -> tuple[list[dict], str]:
    """Сравнение периодов: строка на период, изменения — в пояснении."""
    m = metric_exprs(ctx)
    keys = [k for k in ("revenue", "profit", "orders", "avg_check", "margin_pct", "quantity") if k in m]
    # Полноту проверяем здесь для любого периода: «предыдущий месяц», построенный роутером или сводкой,
    # тоже бывает покрыт данными наполовину — и тогда сравнивать суммы нельзя
    periods = [finish_period(replace(p), ctx.data_start, ctx.data_end) for p in periods]
    rows = []
    for p in periods:
        where, params = _where(ctx, p, filters)
        sql = f"SELECT {', '.join(f'{m[k]} AS {k}' for k in keys)} FROM {ctx.table} WHERE {where}"
        r = _run(conn, sql, params)
        row = {"period": p.label, **(r[0] if r else {})}
        if not p.complete and ctx.data_start and ctx.data_end:
            days = (min(p.end_inclusive, ctx.data_end) - max(p.start, ctx.data_start)).days + 1
            row["days"] = days
            row["revenue_per_day"] = round((row.get("revenue") or 0) / days, 2) if days > 0 else None
        rows.append(row)
    note_parts = [f"Сравниваются периоды: {'; '.join(_period_span(p) for p in periods)}."]
    if len(rows) == 2:
        a, b = rows
        changes = []
        for k in keys:
            va, vb = a.get(k), b.get(k)
            if va in (None, 0) or vb is None:
                continue
            if k == "margin_pct":
                changes.append(f"{METRIC_LABELS[k]}: {_signed(vb - va)} п.п.")
            else:
                # База по модулю: прибыль −100 → +50 — рост на 150 %, а не «−150 %»
                changes.append(f"{METRIC_LABELS[k]}: {_signed((vb - va) * 100.0 / abs(va))} %")
        if changes:
            note_parts.append(f"Изменение «{periods[1].label}» относительно «{periods[0].label}»: {'; '.join(changes)}.")
    if any(not p.complete for p in periods):
        note_parts.append("ВНИМАНИЕ: периоды покрыты данными не полностью — суммы сравнивать некорректно, обязательно скажи об этом в ответе.")
        if len(rows) == 2 and all("days" in r or p.complete for r, p in zip(rows, periods)):
            per_day = []
            for r, p in zip(rows, periods):
                days = r.get("days") or (p.end - p.start).days
                per_day.append((r.get("revenue") or 0) / days if days else 0)
            if per_day[0]:
                from app.domain.ai.data_format import humanize
                note_parts.append(
                    f"Корректнее сравнивать среднедневную выручку: {humanize(per_day[0])} против {humanize(per_day[1])} "
                    f"в день ({_signed((per_day[1] - per_day[0]) * 100.0 / abs(per_day[0]))} %).")
    note_parts.append(_filters_note(filters))
    note_parts.append(DEFINITIONS_NOTE)
    return rows, " ".join(n for n in note_parts if n)


def _signed(x: float) -> str:
    return f"{x:+.1f}".replace(".", ",").replace("-", "−")


def _period_span(p: Period) -> str:
    # У неполного периода фактические даты уже в подписи — не повторяем их в скобках
    return p.label if "неполный" in p.label else f"{p.label} ({p.start:%d.%m.%Y}–{p.end_inclusive:%d.%m.%Y})"


def _month_label(d: date) -> str:
    return f"{MONTH_NAMES[d.month - 1]} {d.year}"


def timeline(conn, ctx: TableCtx, granularity: str = "month", period: Optional[Period] = None,
             filters: Optional[dict] = None) -> tuple[list[dict], str]:
    """Динамика по месяцам (неделям, дням) с изменением к предыдущему периоду."""
    if not ctx.date_expr:
        return [], ""
    m = metric_exprs(ctx)
    keys = [k for k in ("revenue", "profit", "orders", "avg_check") if k in m]
    where, params = _where(ctx, period, filters)
    trunc = f"DATE_TRUNC('{granularity}', {ctx.date_expr})"
    sql = (f"SELECT {trunc} AS p, {', '.join(f'{m[k]} AS {k}' for k in keys)} FROM {ctx.table} "
           f"WHERE {where} AND {ctx.date_expr} IS NOT NULL GROUP BY 1 ORDER BY 1")
    raw = _run(conn, sql, params)
    rows, prev_rev, prev_label, prev_partial = [], None, None, False
    partial = []
    # Границы, внутри которых период полный: данные И запрошенный период. «За последние 3 месяца»
    # обрезает первый месяц — раньше он шёл как полный, и следующий месяц показывал «+137,8 %»
    lo, hi = ctx.data_start, ctx.data_end
    if period:
        lo = max(lo, period.start) if lo else period.start
        hi = min(hi, period.end_inclusive) if hi else period.end_inclusive
    for r in raw:
        start = _to_date(r.pop("p"))
        if start is None:
            continue
        if granularity == "month":
            label = _month_label(start)
            end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        elif granularity == "week":
            end = start + timedelta(days=6)
            label = f"неделя {start:%d.%m}–{end:%d.%m.%Y}"
        else:
            label = f"{start:%d.%m.%Y}"
            end = start
        is_partial = bool(lo and hi and (start < lo or end > hi))
        if is_partial:
            label += " (неполный)"
            partial.append(label)
        rev = r.get("revenue")
        # У неполного периода изменение к полному соседу бессмысленно: «ноябрь −46,8 %» за 15 дней
        change = None if is_partial or prev_partial else (
            round((rev - prev_rev) * 100.0 / abs(prev_rev), 1) if prev_rev and rev is not None else None)
        rows.append({"period": label, **r, "revenue_change_pct": change,
                     "compared_to": prev_label if change is not None else None})
        prev_rev, prev_label, prev_partial = rev, label, is_partial
    notes = ["«Изменение выручки» в строке — к периоду из поля «к периоду» (предыдущий месяц)."]
    if partial:
        notes.append(f"Неполные периоды (данные покрывают не весь период, сравнивать с соседями нельзя): {', '.join(partial)}.")
    notes.append(_period_note(ctx, period))
    notes.append(_filters_note(filters))
    return rows, " ".join(n for n in notes if n)


def forecast(conn, ctx: TableCtx, months: int = 6) -> tuple[list[dict], str]:
    """Прогноз выручки на следующий месяц: линейный тренд по последним полным месяцам."""
    rows, _ = timeline(conn, ctx, "month")
    full = [r for r in rows if "неполный" not in r["period"]][-months:]
    if len(full) < 3:
        return _forecast_by_days(conn, ctx)
    ys = [float(r["revenue"]) for r in full]
    n = len(ys)
    xs = list(range(n))
    mx, my = sum(xs) / n, sum(ys) / n
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    last_full = last_full_month(ctx.data_end)
    # «Следующий месяц» — после месяца, на котором кончаются данные. Если он неполный (идёт сейчас),
    # прогноз — через шаг: раньше прогнозировался текущий месяц, а в ответе он назывался следующим
    current_is_full = last_full.end_inclusive == ctx.data_end or last_full.start.month == ctx.data_end.month
    steps = 1 if current_is_full else 2
    nxt = last_full.end if steps == 1 else (last_full.end.replace(day=28) + timedelta(days=4)).replace(day=1)
    trend = my + slope * (n - 1 + steps - mx)
    avg = my
    out = [{"period": r["period"], "revenue": r["revenue"], "type": "факт"} for r in full]
    out.append({"period": f"{_month_label(nxt)} (прогноз)", "revenue": round(trend, 2), "type": "прогноз"})
    note = (f"Прогноз на {_month_label(nxt)} — линейный тренд по {n} последним полным месяцам: {trend:,.0f}; "
            f"среднее за эти месяцы: {avg:,.0f}; тренд {'растёт' if slope > 0 else 'снижается'} примерно на "
            f"{abs(slope):,.0f} в месяц. Это простая оценка без учёта сезонности и акций.").replace(",", " ")
    if any("неполный" in r["period"] for r in rows[-1:]):
        note += f" Текущий месяц ({rows[-1]['period']}) в расчёт не входит."
    return out, note


def _forecast_by_days(conn, ctx: TableCtx) -> tuple[list[dict], str]:
    """Прогноз на следующий месяц, когда полных месяцев меньше трёх: среднедневная выручка × дни месяца."""
    if not ctx.data_end or not ctx.data_start or (ctx.data_end - ctx.data_start).days < 13:
        return [], ""
    days_rows, _ = timeline(conn, ctx, "day")
    by_day = {}
    for r in days_rows:
        try:
            by_day[datetime.strptime(r["period"].split(" ")[0], "%d.%m.%Y").date()] = float(r.get("revenue") or 0)
        except (ValueError, KeyError):
            continue
    span = min(28, (ctx.data_end - ctx.data_start).days + 1)
    # Календарные дни: дни без продаж — нули. Раньше брались 28 дней с продажами, и при торговле
    # по будням среднее умножалось на календарные дни — прогноз завышался в полтора раза
    days = [ctx.data_end - timedelta(days=i) for i in range(span - 1, -1, -1)]
    vals = [by_day.get(d, 0.0) for d in days]
    if len(vals) < 14:
        return [], ""
    avg = sum(vals) / len(vals)
    first_half, second_half = vals[: len(vals) // 2], vals[len(vals) // 2:]
    trend = (sum(second_half) / len(second_half)) / (sum(first_half) / len(first_half)) - 1 if sum(first_half) else 0
    nxt = (ctx.data_end.replace(day=28) + timedelta(days=4)).replace(day=1)
    days_next = ((nxt.replace(day=28) + timedelta(days=4)).replace(day=1) - nxt).days
    value = round(avg * days_next, 2)
    out = [{"period": f"факт: последние {len(vals)} дн.", "revenue": round(sum(vals), 2), "type": "факт"},
           {"period": f"{_month_label(nxt)} (прогноз)", "revenue": value, "type": "прогноз"}]
    from app.domain.ai.data_format import humanize
    note = (f"ОТВЕТ НА ВОПРОС: прогноз выручки на {_month_label(nxt)} — около {humanize(value)}. Метод: полных месяцев "
            f"в данных меньше трёх, поэтому прогноз = среднедневная выручка за последние {len(vals)} календарных дн. "
            f"({humanize(avg)} в день, дни без продаж — нули) × {days_next} дн. Вторая половина периода к первой: "
            f"{_signed(trend * 100)} %. Это грубая оценка без сезонности; точнее — после 3 полных месяцев данных.")
    return out, note


def _orders_expr(ctx: TableCtx) -> str:
    return f"COUNT(DISTINCT {ctx.col('order_id')})" if ctx.col("order_id") else "COUNT(*)"


def customers_overview(conn, ctx: TableCtx, period: Optional[Period] = None,
                       filters: Optional[dict] = None) -> tuple[list[dict], str]:
    cust = ctx.col("customer")
    if not cust:
        return [], ""
    orders = _orders_expr(ctx)
    rev = an._num(ctx.col("revenue"))
    where, params = _where(ctx, period, filters)
    churn_sql = ""
    # Отток — по последней покупке за ВСЕ данные. Внутри периода «последняя покупка» обрезана его концом,
    # и за 1 квартал в отток попадали все 393 клиента из 393
    if ctx.date_expr and ctx.data_end and not period:
        churn_sql = (f", COUNT(*) FILTER (WHERE last_purchase < TIMESTAMP '{ctx.data_end:%Y-%m-%d}' - INTERVAL {CHURN_DAYS} DAY) AS churned_customers")
    sql = f"""
        WITH c AS (
            SELECT {cust} AS customer, {orders} AS n_orders, SUM({rev}) AS revenue
                   {', MAX(' + ctx.date_expr + ') AS last_purchase' if ctx.date_expr else ''}
            FROM {ctx.table} WHERE {where} AND {cust} IS NOT NULL GROUP BY 1
        )
        SELECT COUNT(*) AS customers,
               COUNT(*) FILTER (WHERE n_orders > 1) AS repeat_customers,
               ROUND(COUNT(*) FILTER (WHERE n_orders > 1) * 100.0 / NULLIF(COUNT(*), 0), 1) AS repeat_share_pct,
               COUNT(*) FILTER (WHERE n_orders = 1) AS one_time_customers,
               ROUND(AVG(n_orders), 2) AS orders_per_customer,
               ROUND(AVG(revenue), 2) AS revenue_per_customer
               {churn_sql}
        FROM c
    """
    rows = _run(conn, sql, params)
    basis = ("Заказ — уникальный номер заказа. " if ctx.col("order_id") else
             "Колонки номера заказа нет: каждая строка считается отдельной покупкой. ")
    note = (basis + "Повторный клиент — сделал больше одного заказа. "
            + (f"Отток — клиенты без покупок {CHURN_DAYS}+ дней до последней даты в данных "
               f"({ctx.data_end:%d.%m.%Y}). " if churn_sql else "")
            + _period_note(ctx, period) + " " + _filters_note(filters)).strip()
    return rows, note


def churned_customers(conn, ctx: TableCtx, limit: int = 20, filters: Optional[dict] = None) -> tuple[list[dict], str]:
    cust = ctx.col("customer")
    if not cust or not ctx.date_expr or not ctx.data_end:
        return [], ""
    orders = _orders_expr(ctx)
    rev = an._num(ctx.col("revenue"))
    end = f"TIMESTAMP '{ctx.data_end:%Y-%m-%d}'"
    where, params = _where(ctx, None, filters)
    base = (f"SELECT {cust} AS customer, MAX({ctx.date_expr}) AS last_purchase, {orders} AS orders, "
            f"ROUND(SUM({rev}), 2) AS revenue FROM {ctx.table} WHERE {where} AND {cust} IS NOT NULL GROUP BY 1")
    total = _run(conn, f"SELECT COUNT(*) AS n, ROUND(SUM(revenue), 2) AS rev FROM ({base}) WHERE last_purchase < {end} - INTERVAL {CHURN_DAYS} DAY", params)
    rows = _run(conn, f"""
        SELECT customer, CAST(last_purchase AS DATE) AS last_purchase,
               DATE_DIFF('day', last_purchase, {end}) AS days_without_purchase, orders, revenue
        FROM ({base}) WHERE last_purchase < {end} - INTERVAL {CHURN_DAYS} DAY
        ORDER BY revenue DESC LIMIT {int(limit)}""", params)
    n = total[0]["n"] if total else 0
    from app.domain.ai.data_format import humanize
    flt = _filters_note(filters)
    if not n:
        return [], (f"ОТВЕТ НА ВОПРОС: клиентов, переставших покупать, нет — у всех была покупка за последние "
                    f"{CHURN_DAYS} дней до {ctx.data_end:%d.%m.%Y}. {flt}").strip()
    rev_txt = f", их выручка за всё время — {humanize(total[0]['rev'])}" if total and total[0]["rev"] else ""
    top = ", ".join(f"{r['customer']} ({humanize(r['revenue'])}, {r['days_without_purchase']} дн. без покупок)" for r in rows[:5])
    note = (f"ОТВЕТ НА ВОПРОС: перестали покупать {n} клиентов — нет покупок {CHURN_DAYS}+ дней до "
            f"{ctx.data_end:%d.%m.%Y}{rev_txt}. Самые ценные из них по выручке: {top}. "
            f"Таблица — {len(rows)} самых ценных по выручке. {flt}").strip()
    return rows, note


#: Статусы возврата — сравниваются без регистра и с «е» вместо «ё»
_RETURNED_STATUSES = {"returned", "refunded", "return", "refund", "возврат", "возвращен", "возвращено", "возвращена",
                      "возвращены", "возврат товара"}


def returns_overview(conn, ctx: TableCtx, limit: int = 10, period: Optional[Period] = None,
                     filters: Optional[dict] = None, dim: Optional[str] = None) -> tuple[list[dict], str]:
    ret = ctx.col("returns")
    status = ctx.col("status")
    if not ret and not status:
        return [], ""
    where, params = _where(ctx, period, filters)
    oid = ctx.col("order_id")
    orders = _orders_expr(ctx)
    notes = []
    if ret:
        qty = an._num(ctx.col("quantity")) if ctx.col("quantity") else "1"
        # Заказы с возвратом — тоже уникальные номера: при нескольких строках в заказе раньше
        # выходило «Заказов с возвратом: 120 из 100»
        with_ret = (f"COUNT(DISTINCT {oid}) FILTER (WHERE {an._num(ret)} > 0)" if oid
                    else f"COUNT(*) FILTER (WHERE {an._num(ret)} > 0)")
        res = _run(conn, f"""SELECT SUM({an._num(ret)}) AS returned_units, SUM({qty}) AS sold_units,
                    {with_ret} AS orders_with_returns, {orders} AS orders
                    FROM {ctx.table} WHERE {where}""", params)
        if res:
            r = res[0]
            units_pct = round((r["returned_units"] or 0) * 100.0 / r["sold_units"], 2) if r["sold_units"] else None
            orders_pct = round(r["orders_with_returns"] * 100.0 / r["orders"], 2) if r["orders"] else None
            notes.append(f"Возвращено единиц: {int(r['returned_units'] or 0)} из {int(r['sold_units'] or 0)} проданных "
                         f"({str(units_pct).replace('.', ',')} %). Заказов с возвратом: {r['orders_with_returns']} из "
                         f"{r['orders']} ({str(orders_pct).replace('.', ',')} %).")
    if status:
        st = _run(conn, f"SELECT LOWER(CAST({status} AS VARCHAR)) AS s, COUNT(*) AS n FROM {ctx.table} WHERE {where} GROUP BY 1",
                  params)
        returned = sum(x["n"] for x in st if (x["s"] or "").replace("ё", "е").strip() in _RETURNED_STATUSES)
        total = sum(x["n"] for x in st)
        if returned:
            share = f"{returned * 100.0 / total:.1f}".replace(".", ",")
            notes.append(f"Строк со статусом «возвращён»: {returned} из {total} ({share} %).")
    rows = []
    dexpr = _dim_expr(ctx, dim or "product")
    if dexpr and ret:
        expr, name = dexpr
        qty = an._num(ctx.col("quantity")) if ctx.col("quantity") else "1"
        with_ret = (f"COUNT(DISTINCT {oid}) FILTER (WHERE {an._num(ret)} > 0)" if oid
                    else f"COUNT(*) FILTER (WHERE {an._num(ret)} > 0)")
        rows = _run(conn, f"""SELECT {expr} AS "{name}", SUM({an._num(ret)}) AS returned_units,
                     {with_ret} AS orders_with_returns,
                     ROUND(SUM({an._num(ret)}) * 100.0 / NULLIF(SUM({qty}), 0), 1) AS return_rate_pct
                     FROM {ctx.table} WHERE {where} GROUP BY 1 HAVING SUM({an._num(ret)}) > 0
                     ORDER BY returned_units DESC, orders_with_returns DESC LIMIT {int(limit)}""", params)
        label = {"product": "товары", "region": "регионы", "category": "категории", "manager": "менеджеры",
                 "channel": "каналы", "brand": "бренды", "warehouse": "склады", "customer": "клиенты"}.get(dim or "product", name)
        notes.append(f"Таблица — {label} по числу возвращённых единиц.")
    notes += [_period_note(ctx, period), _filters_note(filters)]
    return rows, " ".join(n for n in notes if n)


def _discount_pct(ctx: TableCtx, conn) -> Optional[str]:
    """Скидка в процентах при любом хранении: доля (0,15), проценты (15) или сумма скидки."""
    disc = ctx.col("discount")
    if not disc:
        return None
    d = an._num(disc)
    try:
        mx = conn.execute(f"SELECT MAX({d}) FROM {ctx.table}").fetchone()[0]
    except Exception:
        mx = None
    if mx is not None and 0 < mx <= 1:
        return f"({d} * 100)"
    if mx is not None and mx > 100:
        # Сумма скидки: процент от цены до скидки
        return f"({d} * 100.0 / NULLIF({an._num(ctx.col('revenue'))} + {d}, 0))"
    return d


def discount_effect(conn, ctx: TableCtx, period: Optional[Period] = None,
                    filters: Optional[dict] = None) -> tuple[list[dict], str]:
    d = ctx.discount_pct or _discount_pct(ctx, conn)
    if not d:
        return [], ""
    m = metric_exprs(ctx)
    where, params = _where(ctx, period, filters)
    bucket = (f"CASE WHEN {d} IS NULL OR {d} = 0 THEN '0 (без скидки)' WHEN {d} <= 5 THEN '1–5' "
              f"WHEN {d} <= 10 THEN '6–10' WHEN {d} <= 20 THEN '11–20' ELSE 'больше 20' END")
    order = (f"CASE WHEN {d} IS NULL OR {d} = 0 THEN 0 WHEN {d} <= 5 THEN 1 WHEN {d} <= 10 THEN 2 "
             f"WHEN {d} <= 20 THEN 3 ELSE 4 END")
    keys = [k for k in ("orders", "avg_check", "margin_pct", "revenue", "profit") if k in m]
    rows = _run(conn, f"""SELECT {bucket} AS discount_range, {', '.join(f'{m[k]} AS {k}' for k in keys)}
                 FROM {ctx.table} WHERE {where} GROUP BY {bucket}, {order} ORDER BY {order}""", params)
    corr = None
    if ctx.col("profit"):
        c = _run(conn, f"SELECT ROUND(CORR({d}, {an._num(ctx.col('profit'))} / NULLIF({an._num(ctx.col('revenue'))}, 0)), 3) AS c "
                       f"FROM {ctx.table} WHERE {where}", params)
        corr = c[0]["c"] if c else None
        if corr is not None and (corr != corr or abs(corr) == float("inf")):
            corr = None  # постоянная скидка или маржа: корреляция не определена (NaN), а не «заметная связь»
    note = ("Группы по размеру скидки (в %): число заказов, средний чек, маржинальность. Суммы прибыли по группам "
            "не сравнивай — группы разного размера (без скидки заказов намного больше).")
    # Тренд считаем сами: модель придумывала «средний чек растёт со скидкой», когда он падал
    if len(rows) >= 2:
        from app.domain.ai.data_format import humanize
        first, last = rows[0], rows[-1]
        for key, label, fmt in (("avg_check", "Средний чек", humanize),
                                ("margin_pct", "Маржинальность", lambda v: f"{v:.1f} %".replace(".", ","))):
            a_, b_ = first.get(key), last.get(key)
            if a_ is None or b_ is None:
                continue
            trend = "почти не меняется" if abs(b_ - a_) / max(abs(a_), 1e-9) < 0.02 else ("выше" if b_ > a_ else "ниже")
            note += (f" {label}: {fmt(a_)} в группе «{first['discount_range']}» и {fmt(b_)} в группе "
                     f"«{last['discount_range']}» — при большой скидке {trend}.")
    elif len(rows) == 1:
        note += " Все заказы в одной группе скидки — сравнивать не с чем."
    if corr is not None:
        strength = "практически нет связи" if abs(corr) < 0.1 else "слабая связь" if abs(corr) < 0.3 else "заметная связь"
        note += f" Корреляция скидки и маржинальности заказа: {str(corr).replace('.', ',')} ({strength})."
    # Вывод — первой фразой: модель делала его из абсолютной прибыли групп («скидка снижает прибыль»)
    trend = note.split("не сравнивай — группы разного размера (без скидки заказов намного больше). ", 1)[-1]
    extra = " ".join(n for n in (_period_note(ctx, period), _filters_note(filters)) if n)
    return rows, f"ОТВЕТ НА ВОПРОС: {trend} {note} {extra}".strip()


def abc(conn, ctx: TableCtx, dim: str = "product", period: Optional[Period] = None,
        filters: Optional[dict] = None) -> tuple[list[dict], str]:
    dexpr = _dim_expr(ctx, dim)
    if not dexpr:
        return [], ""
    expr, name = dexpr
    rev = an._num(ctx.col("revenue"))
    where, params = _where(ctx, period, filters)
    rows = _run(conn, f"""
        WITH r AS (SELECT {expr} AS "{name}", SUM({rev}) AS revenue FROM {ctx.table} WHERE {where} GROUP BY 1),
        s AS (SELECT *, revenue * 100.0 / SUM(revenue) OVER () AS share,
                     SUM(revenue) OVER (ORDER BY revenue DESC ROWS UNBOUNDED PRECEDING) * 100.0 / SUM(revenue) OVER () AS cum
              FROM r)
        SELECT "{name}", ROUND(revenue, 2) AS revenue, ROUND(share, 1) AS revenue_share_pct, ROUND(cum, 1) AS cumulative_share_pct,
               CASE WHEN cum - share < 80 THEN 'A' WHEN cum - share < 95 THEN 'B' ELSE 'C' END AS abc_class
        FROM s ORDER BY revenue DESC""", params)
    counts = {c: sum(1 for r in rows if r["abc_class"] == c) for c in "ABC"}
    # Доли классов — из выручки, а не из округлённых долей позиций: 2000 позиций по 0,0 % давали «C — 0,0 %»
    total = sum(r["revenue"] or 0 for r in rows) or None
    shares = {c: round(sum(r["revenue"] or 0 for r in rows if r["abc_class"] == c) * 100.0 / total, 1) if total else 0.0
              for c in "ABC"}
    note = (f"ABC по выручке: A — позиции, дающие первые 80 % выручки ({counts['A']} шт., {shares['A']} %), "
            f"B — следующие 15 % ({counts['B']} шт., {shares['B']} %), C — остальные 5 % ({counts['C']} шт., {shares['C']} %).")
    extra = " ".join(n for n in (_period_note(ctx, period), _filters_note(filters)) if n)
    return rows, f"{note} {extra}".strip()


def loss_items(conn, ctx: TableCtx, dim: str = "product", period: Optional[Period] = None,
               filters: Optional[dict] = None) -> tuple[list[dict], str]:
    if not ctx.col("profit"):
        return [], ""
    rows, _ = breakdown(conn, ctx, [dim], metric="profit", ascending=True, limit=None, period=period, filters=filters)
    losing = [r for r in rows if (r.get("profit") or 0) < 0]
    label = {"product": "товаров", "category": "категорий", "region": "регионов", "manager": "менеджеров",
             "channel": "каналов"}.get(dim, "позиций")
    scope = " ".join(n for n in (_period_note(ctx, period), _filters_note(filters)) if n)
    if losing:
        # Сколько убыточных всего — отдельным запросом: строки разреза ограничены сотней
        n_losing = len(losing)
        dexpr = _dim_expr(ctx, dim)
        if dexpr and len(rows) >= 100:
            where, params = _where(ctx, period, filters)
            cnt = _run(conn, f"SELECT COUNT(*) AS n FROM (SELECT {dexpr[0]} AS k, SUM({an._num(ctx.col('profit'))}) AS p "
                             f"FROM {ctx.table} WHERE {where} GROUP BY 1) WHERE p < 0", params)
            n_losing = cnt[0]["n"] if cnt else n_losing
        more = f" Ниже — {len(losing)} с наибольшим убытком." if n_losing > len(losing) else ""
        return losing, f"Убыточные по сумме прибыли: {n_losing} {label}.{more} {scope}".strip()
    by_margin = sorted(rows, key=lambda r: r.get("margin_pct") if r.get("margin_pct") is not None else 0)[:5]
    # Только название и маржа: с колонкой прибыли модель добавляла «лидер по прибыли — …» к списку худших
    name = next((k for k, v in (rows[0] if rows else {}).items() if isinstance(v, str)), None)
    by_margin = [{name: r[name], "margin_pct": r.get("margin_pct")} for r in by_margin] if name else by_margin
    return by_margin, (f"Убыточных {label} нет: прибыль по всем положительная (строк с отрицательной прибылью "
                       f"{_negative_rows(conn, ctx)}). Ниже — 5 наименее маржинальных для сведения. {scope}").strip()


def _negative_rows(conn, ctx: TableCtx) -> int:
    r = _run(conn, f"SELECT COUNT(*) AS n FROM {ctx.table} WHERE {an._num(ctx.col('profit'))} < 0", [])
    return r[0]["n"] if r else 0


def change_periods(ctx: TableCtx, granularity: str = "month") -> Optional[tuple[Period, Period]]:
    """Последний полный период и предыдущий; при данных за один месяц — последние полные недели."""
    if not ctx.data_end:
        return None
    cur = last_full_quarter(ctx.data_end) if granularity == "quarter" else last_full_month(ctx.data_end)
    prev = previous_period(cur)
    if ctx.data_start and prev.start < ctx.data_start:
        # Выгрузка за один месяц: прошлого месяца в данных нет — сравниваем последние полные недели
        cur, prev = _last_full_weeks(ctx.data_end)
        if prev.start < ctx.data_start:
            return None
    return cur, prev


def change_by_dimension(conn, ctx: TableCtx, dim: str, granularity: str = "month",
                        metric: str = "revenue", filters: Optional[dict] = None) -> tuple[list[dict], str]:
    """Последний полный период против предыдущего по каждому значению разреза."""
    periods = change_periods(ctx, granularity)
    if not periods:
        return [], ""
    cur, prev = periods
    m = metric_exprs(ctx)
    if metric not in m:
        metric = "revenue"
    dexpr = _dim_expr(ctx, dim)
    if not dexpr:
        return [], ""
    expr, name = dexpr
    w1, p1 = _where(ctx, cur, filters)
    w2, p2 = _where(ctx, prev, filters)
    # Значение, которое в текущем периоде пропало совсем, — падение на 100 %, а не «нет данных»
    cur_v = "COALESCE(a.v, 0)" if metric in ("revenue", "profit", "orders", "quantity") else "a.v"
    sql = f"""
        WITH a AS (SELECT {expr} AS k, {m[metric]} AS v FROM {ctx.table} WHERE {w1} GROUP BY 1),
             b AS (SELECT {expr} AS k, {m[metric]} AS v FROM {ctx.table} WHERE {w2} GROUP BY 1)
        SELECT COALESCE(a.k, b.k) AS "{name}", b.v AS previous_value, {cur_v} AS current_value,
               ROUND(({cur_v} - b.v) * 100.0 / NULLIF(ABS(b.v), 0), 1) AS change_pct
        FROM a FULL JOIN b ON a.k = b.k ORDER BY change_pct ASC NULLS LAST"""
    rows = _run(conn, sql, p1 + p2)
    total_rows = len(rows)
    falling = [r for r in rows if (r.get("change_pct") or 0) < 0]
    shown = ", ".join(str(r[name]) for r in falling[:15])
    more = f" и ещё {len(falling) - 15}" if len(falling) > 15 else ""
    gone = [str(r[name]) for r in rows if r.get("previous_value") and not r.get("current_value")]
    note = (f"Показатель «{METRIC_LABELS.get(metric, metric)}»: {cur.label} (current_value) против {prev.label} (previous_value); "
            f"оба периода полные. Снижение у: {shown + more if falling else 'нет'}."
            + (f" Без продаж в текущем периоде: {', '.join(gone[:10])}." if gone else "")
            + (f" Всего значений: {total_rows}; в таблице — первые 100 по снижению." if total_rows > 100 else "")
            + (" " + _filters_note(filters) if filters else ""))
    # Тысячи клиентов или товаров целиком уходили в чат и в историю (мегабайты JSON) — показываем сотню
    return rows[:100], note


def _last_full_weeks(data_end: date) -> tuple[Period, Period]:
    """Последняя полная неделя (пн–вс) в данных и предыдущая."""
    end = data_end if data_end.isoweekday() == 7 else data_end - timedelta(days=data_end.isoweekday())
    start = end - timedelta(days=6)
    cur = Period(f"неделя {start:%d.%m}–{end:%d.%m.%Y}", start, end + timedelta(days=1), kind="days", days=7)
    pstart = start - timedelta(days=7)
    prev = Period(f"неделя {pstart:%d.%m}–{(start - timedelta(days=1)):%d.%m.%Y}", pstart, start, kind="days", days=7)
    return cur, prev


def summary_pack(conn, ctx: TableCtx) -> tuple[list[dict], str]:
    """Сводка для руководства: итоги, структура, динамика последнего полного месяца, слабые места."""
    tot, _ = totals(conn, ctx)
    parts = []
    if tot:
        t = tot[0]
        from app.domain.ai.data_format import format_value, kind_of
        span = f" ({ctx.data_start:%d.%m.%Y}–{ctx.data_end:%d.%m.%Y})" if ctx.data_start and ctx.data_end else ""
        parts.append(f"ИТОГИ ЗА ВЕСЬ ПЕРИОД ДАННЫХ{span}: " + "; ".join(
            f"{METRIC_LABELS.get(k, k)} — {format_value(v, kind_of(k, [v]))}" for k, v in t.items() if v is not None) + ".")
    for dim in ("category", "channel", "region"):
        if dim in ctx.roles:
            rows, _ = breakdown(conn, ctx, [dim], "revenue", limit=6)
            col = ctx.roles[dim]
            parts.append(f"Доля выручки по «{col}»: " + ", ".join(f"{r[col]} {r['revenue_share_pct']} %" for r in rows) + ".")
    if ctx.data_end and ctx.date_expr:
        cur = last_full_month(ctx.data_end)
        _, note = compare(conn, ctx, [previous_period(cur), cur])
        # Отдельной меткой: модель писала «выручка в декабре — 79,83 млн», беря итог за весь период
        parts.append(f"ПОСЛЕДНИЙ ПОЛНЫЙ МЕСЯЦ ({cur.label}) К ПРЕДЫДУЩЕМУ — только изменения, не итоги: "
                     + note.split(" Средний чек =")[0])
        # Подпись — от периодов, которые реально сравнивались: при данных за один месяц это недели
        cp = change_periods(ctx, "month")
        for dim in ("channel", "region", "category"):
            if dim in ctx.roles and cp:
                rows_c, _ = change_by_dimension(conn, ctx, dim, "month")
                col = ctx.roles[dim]
                items = [f"{r[col]} {_signed(r['change_pct'])} %" for r in rows_c if r.get("change_pct") is not None]
                if items:
                    parts.append(f"Изменение выручки по «{col}» ({cp[0].label} к {cp[1].label}): {', '.join(items)}.")
    main_rows = []
    if "category" in ctx.roles:
        main_rows, _ = breakdown(conn, ctx, ["category"], "revenue")
    elif tot:
        main_rows = tot
    return main_rows, " ".join(parts)


# ── Качество данных ───────────────────────────────────────────────────────────

def _norm_name(v: str) -> str:
    """«ТОО "Люкс Бьюти"» и «ТОО Люкс Бьюти» — одно название в разных написаниях."""
    import re as _re
    return _re.sub(r"[\W_]+", "", str(v).lower().replace("ё", "е"))


_ID_COL_RE = re.compile(r"номер|документ|^id$|_id$|^код|number|^no$|чек")
_DOC_COL_RE = re.compile(r"документ|document|накладн|invoice")


def _pretty_col(c: str) -> str:
    """«остаток__шт» → «остаток, шт»: имена колонок после очистки при загрузке нечитаемы."""
    import re as _re
    return _re.sub(r"_+$", "", _re.sub(r"__+", ", ", c)).replace("_", " ")


def data_quality(conn, tables: list[dict], relationships: Optional[list[dict]] = None) -> tuple[list[dict], str]:
    """Проверки качества данных по таблицам: [{name, table_name}].

    Строка результата — одна найденная проблема: таблица, проверка, сколько найдено, пример.
    """
    from app.domain.ai.column_roles import infer_roles
    from app.services.column_dict import user_aliases

    aliases = user_aliases(conn)
    issues: list[dict] = []

    def add(table, check, n, example=""):
        check = re.sub(r"«([^»]+)»", lambda m: "«" + _pretty_col(m.group(1)) + "»", check)
        if n:
            issues.append({"table": table, "check": check, "found": int(n), "example": str(example)[:160]})

    by_name = {t["name"]: t["table_name"] for t in tables}
    for t in tables:
        name, tbl = t["name"], t["table_name"]
        try:
            cols = conn.execute(
                "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position",
                [tbl]).fetchall()
            total = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        except Exception:
            continue
        if not cols or not total:
            continue
        quoted = [f'"{c}"' for c, _ in cols]
        full_dups_count = None
        # 1. Полностью одинаковые строки
        try:
            r = conn.execute(f"SELECT COUNT(*) - COUNT(DISTINCT ({', '.join(quoted)})) FROM {tbl}").fetchone()[0]
            full_dups_count = r
            ex = ""
            if r:
                row = conn.execute(
                    f"SELECT {', '.join(quoted)} FROM {tbl} GROUP BY {', '.join(quoted)} HAVING COUNT(*) > 1 LIMIT 1").fetchone()
                ex = ", ".join(str(v) for v in (row or [])[:4])
            add(name, "полностью одинаковые строки (дубли)", r, ex)
        except Exception:
            pass
        # 1б. Одинаковые записи, отличающиеся только номером документа: повторно проведённый документ
        # Только документы (поступления, накладные): одинаковые строки в разных чеках продаж — обычные
        # продажи одного товара разным покупателям, а не дубль
        doc_cols = [q for (c, _), q in zip(cols, quoted) if _DOC_COL_RE.search(c)]
        id_cols = [q for (c, _), q in zip(cols, quoted) if _ID_COL_RE.search(c)]
        rest = [q for q in quoted if q not in id_cols]
        if doc_cols and len(rest) >= 3:
            try:
                r = conn.execute(f"SELECT COUNT(*) - COUNT(DISTINCT ({', '.join(rest)})) FROM {tbl}").fetchone()[0]
                full_dups = full_dups_count if full_dups_count is not None else 0
                if r > full_dups:
                    row = conn.execute(
                        f"SELECT STRING_AGG(CAST({doc_cols[0]} AS VARCHAR), ' и ') FROM {tbl} GROUP BY {', '.join(rest)} "
                        f"HAVING COUNT(*) > 1 LIMIT 1").fetchone()
                    add(name, "одинаковые записи под разными номерами (возможно, документ проведён дважды)",
                        r - full_dups, row[0] if row else "")
            except Exception:
                pass
        stats = {}
        for c, typ in cols:
            q = f'"{c}"'
            try:
                nn, dist = conn.execute(f"SELECT COUNT({q}), COUNT(DISTINCT {q}) FROM {tbl}").fetchone()
            except Exception:
                continue
            stats[c] = {"distinct": dist, "rows": total}
            # 2. Пропуски в обычно заполненной колонке
            missing = total - nn
            if 0 < missing <= 0.2 * total:
                add(name, f"не заполнена колонка «{c}»", missing)
            numeric = any(x in typ.upper() for x in ("INT", "DOUBLE", "FLOAT", "DECIMAL"))
            if typ.upper() == "VARCHAR":
                # 3. Почти уникальная колонка (номер документа, чека) с повторами
                if total >= 20 and 0.97 * nn <= dist < nn and any(w in c for w in ("номер", "чек", "документ", "id", "код", "number", "no")):
                    v = conn.execute(f"SELECT {q} FROM {tbl} WHERE {q} IS NOT NULL GROUP BY 1 HAVING COUNT(*) > 1 LIMIT 1").fetchone()
                    add(name, f"повторяющиеся значения в колонке «{c}»", nn - dist, v[0] if v else "")
                # 4. Одно название в разных написаниях
                if 1 < dist <= 500:
                    vals = [r[0] for r in conn.execute(f"SELECT DISTINCT {q} FROM {tbl} WHERE {q} IS NOT NULL").fetchall()]
                    groups: dict[str, list] = {}
                    for v in vals:
                        groups.setdefault(_norm_name(v), []).append(v)
                    variants = [g for g in groups.values() if len(g) > 1]
                    if variants:
                        add(name, f"одно значение в разных написаниях в колонке «{c}»", len(variants),
                            " / ".join(map(str, variants[0][:3])))
            elif numeric:
                # 5. Отрицательные значения в колонке, где они почти не встречаются
                neg, pos = conn.execute(f"SELECT COUNT(*) FILTER (WHERE {q} < 0), COUNT(*) FILTER (WHERE {q} > 0) FROM {tbl}").fetchone()
                if neg and pos and neg <= 0.05 * (neg + pos):
                    add(name, f"отрицательные значения в колонке «{c}»", neg)
        roles = infer_roles(cols, stats, aliases)
        # 6. Продажа дешевле себестоимости (не для остатков: там нет продаж)
        is_stock = any(w in c for c, _ in cols for w in ("остат", "stock", "balance", "сальдо"))
        if not is_stock and "revenue" in roles and "cost" in roles and not roles["revenue"].startswith("("):
            try:
                # Через _num: числа текстом сравнивались бы как строки («1000» < «900»)
                n = conn.execute(f'SELECT COUNT(*) FROM {tbl} WHERE {an._num(chr(34) + roles["revenue"] + chr(34))} '
                                 f'< {an._num(chr(34) + roles["cost"] + chr(34))}').fetchone()[0]
                add(name, "сумма продажи ниже себестоимости", n)
            except Exception:
                pass
    # 7. Ссылки на отсутствующие записи справочника
    for rel in relationships or []:
        a, b = by_name.get(rel.get("from_table")), by_name.get(rel.get("to_table"))
        if not a or not b:
            continue
        try:
            n = conn.execute(
                f'SELECT COUNT(DISTINCT "{rel["from_col"]}") FROM {a} WHERE "{rel["from_col"]}" IS NOT NULL AND '
                f'"{rel["from_col"]}" NOT IN (SELECT "{rel["to_col"]}" FROM {b} WHERE "{rel["to_col"]}" IS NOT NULL)').fetchone()[0]
            add(rel["from_table"], f"«{rel['from_col']}» нет в справочнике «{rel['to_table']}»", n)
        except Exception:
            pass
    note = (f"Проверено таблиц: {len(tables)}. Найдено проблем: {len(issues)}. "
            "Каждая строка — тип проблемы, таблица, сколько найдено и пример.")
    return issues, note


def data_quality_answer(issues: list[dict]) -> str:
    if not issues:
        return "Явных несостыковок не найдено: дублей, пропусков в обязательных колонках, отрицательных значений и разных написаний одного значения нет."
    lines = []
    for i in issues:
        ex = f" (например: {i['example']})" if i.get("example") else ""
        lines.append(f"- {i['table']}: {i['check']} — {i['found']}{ex}")
    return "Найденные несостыковки в данных:\n" + "\n".join(lines)
