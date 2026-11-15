"""Эталонные SQL-примеры, собранные из реальных колонок набора данных.

Раньше примеры в промпт попадали только из прошлых ответов с 👍 — на новой базе их нет,
и модель писала маржу как прибыль/штуки, «повторных клиентов» без фильтра и CTE без FROM.
Здесь примеры строятся по ролям колонок, а к вопросу подбираются ближайшие по словам.
"""
from __future__ import annotations

import re
from typing import Optional


def _quoted(roles: dict[str, str]) -> dict[str, str]:
    """Имена колонок для SQL: «order», «group» — в кавычках, иначе примеры не разбираются."""
    from app.domain.ai.column_roles import sql_ident

    return {k: sql_ident(v) for k, v in roles.items()}


def build_definitions(roles: dict[str, str]) -> list[str]:
    """Определения показателей с реальными именами колонок."""
    r = _quoted(roles)
    out = []
    if "revenue" in r:
        out.append(f"выручка = SUM({r['revenue']})")
    if "profit" in r:
        out.append(f"прибыль = SUM({r['profit']})")
        out.append(f"маржинальность, % = ROUND(SUM({r['profit']}) * 100.0 / NULLIF(SUM({r['revenue']}), 0), 1) — НЕ делить на количество")
    orders = f"COUNT(DISTINCT {r['order_id']})" if "order_id" in r else "COUNT(*)"
    out.append(f"число заказов = {orders}")
    if "revenue" in r:
        out.append(f"средний чек = ROUND(SUM({r['revenue']}) / NULLIF({orders}, 0), 2)")
        out.append(f"доля, % = ROUND(SUM({r['revenue']}) * 100.0 / NULLIF(SUM(SUM({r['revenue']})) OVER (), 0), 1)")
    if "quantity" in r:
        out.append(f"продано штук = SUM({r['quantity']})")
    if "customer" in r:
        out.append(f"повторные клиенты — у кого {orders} > 1 в группировке по {r['customer']}")
        out.append("отток — клиенты, у которых MAX(дата) раньше MAX(дата по всей таблице) - INTERVAL 90 DAY")
    return out


def build_examples(table: str, roles: dict[str, str], date_expr: Optional[str]) -> list[tuple[str, str]]:
    r = _quoted(roles)
    if "revenue" not in r:
        return []
    rev = r["revenue"]
    orders = f"COUNT(DISTINCT {r['order_id']})" if "order_id" in r else "COUNT(*)"
    ex: list[tuple[str, str]] = []
    dim = next((r[k] for k in ("region", "category", "channel", "manager") if k in r), None)
    if dim:
        ex.append((f"Выручка и доля по {dim}",
                   f"SELECT {dim}, ROUND(SUM({rev}), 2) AS revenue, ROUND(SUM({rev}) * 100.0 / NULLIF(SUM(SUM({rev})) OVER (), 0), 1) AS share_pct "
                   f"FROM {table} GROUP BY {dim} ORDER BY revenue DESC LIMIT 100"))
        ex.append((f"Какой {dim} лучший по выручке",
                   f"SELECT {dim}, ROUND(SUM({rev}), 2) AS revenue FROM {table} GROUP BY {dim} ORDER BY revenue DESC LIMIT 10"))
        ex.append((f"Средний чек по {dim}",
                   f"SELECT {dim}, ROUND(SUM({rev}) / NULLIF({orders}, 0), 2) AS avg_check, {orders} AS orders FROM {table} "
                   f"GROUP BY {dim} ORDER BY avg_check DESC LIMIT 100"))
    if "profit" in r and "product" in r:
        ex.append(("Топ-5 товаров по прибыли",
                   f"SELECT {r['product']}, ROUND(SUM({r['profit']}), 2) AS profit, ROUND(SUM({rev}), 2) AS revenue "
                   f"FROM {table} GROUP BY {r['product']} ORDER BY profit DESC LIMIT 5"))
    if "profit" in r and "category" in r:
        ex.append(("Маржинальность по категориям",
                   f"SELECT {r['category']}, ROUND(SUM({r['profit']}) * 100.0 / NULLIF(SUM({rev}), 0), 1) AS margin_pct, ROUND(SUM({rev}), 2) AS revenue "
                   f"FROM {table} GROUP BY {r['category']} ORDER BY margin_pct DESC LIMIT 100"))
    if "manager" in r and "region" in r:
        m, reg = r["manager"], r["region"]
        ex.append(("В каких регионах продаёт лучший менеджер",
                   f"WITH best AS (SELECT {m} FROM {table} GROUP BY {m} ORDER BY SUM({rev}) DESC LIMIT 1) "
                   f"SELECT {reg}, ROUND(SUM({rev}), 2) AS revenue, {orders} AS orders FROM {table} "
                   f"WHERE {m} = (SELECT {m} FROM best) GROUP BY {reg} ORDER BY revenue DESC LIMIT 100"))
    if date_expr:
        d = date_expr
        ex.append(("Выручка за сентябрь 2026",
                   f"SELECT ROUND(SUM({rev}), 2) AS revenue, {orders} AS orders FROM {table} "
                   f"WHERE {d} >= TIMESTAMP '2026-09-01' AND {d} < TIMESTAMP '2026-10-01'"))
        ex.append(("Сколько заказов за последние 30 дней",
                   f"SELECT {orders} AS orders FROM {table} WHERE {d} > (SELECT MAX({d}) - INTERVAL 30 DAY FROM {table})"))
        ex.append(("Рост выручки месяц к месяцу",
                   f"WITH m AS (SELECT DATE_TRUNC('month', {d}) AS month, SUM({rev}) AS revenue FROM {table} GROUP BY 1) "
                   f"SELECT month, ROUND(revenue, 2) AS revenue, ROUND((revenue - LAG(revenue) OVER (ORDER BY month)) * 100.0 / "
                   f"NULLIF(ABS(LAG(revenue) OVER (ORDER BY month)), 0), 1) AS growth_pct FROM m ORDER BY month"))
        ex.append(("В какой день недели больше всего продаж",
                   f"SELECT dayname({d}) AS weekday, ROUND(SUM({rev}), 2) AS revenue, {orders} AS orders FROM {table} "
                   f"GROUP BY 1 ORDER BY revenue DESC"))
        if "customer" in r:
            c = r["customer"]
            ex.append(("Какие клиенты перестали покупать",
                       f"SELECT {c}, MAX({d}) AS last_purchase, ROUND(SUM({rev}), 2) AS revenue FROM {table} GROUP BY {c} "
                       f"HAVING MAX({d}) < (SELECT MAX({d}) - INTERVAL 90 DAY FROM {table}) ORDER BY revenue DESC LIMIT 100"))
    if "customer" in r:
        c = r["customer"]
        ex.append(("Доля повторных клиентов",
                   f"WITH c AS (SELECT {c}, {orders} AS n FROM {table} GROUP BY {c}) "
                   f"SELECT COUNT(*) FILTER (WHERE n > 1) AS repeat_customers, COUNT(*) AS customers, "
                   f"ROUND(COUNT(*) FILTER (WHERE n > 1) * 100.0 / NULLIF(COUNT(*), 0), 1) AS repeat_share_pct FROM c"))
    if "product" in r:
        p = r["product"]
        ex.append(("ABC-анализ товаров",
                   f"WITH s AS (SELECT {p}, SUM({rev}) AS revenue FROM {table} GROUP BY {p}), "
                   f"c AS (SELECT *, SUM(revenue) OVER (ORDER BY revenue DESC) * 100.0 / NULLIF(SUM(revenue) OVER (), 0) AS cum_pct FROM s) "
                   f"SELECT {p}, ROUND(revenue, 2) AS revenue, ROUND(cum_pct, 1) AS cum_pct, "
                   f"CASE WHEN cum_pct <= 80 THEN 'A' WHEN cum_pct <= 95 THEN 'B' ELSE 'C' END AS abc_class FROM c ORDER BY revenue DESC"))
    return ex


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-zа-яё]{4,}", text.lower())}


def select_examples(question: str, examples: list[tuple[str, str]], k: int = 3) -> list[tuple[str, str]]:
    qs = _stems(question)
    scored = sorted(examples, key=lambda e: len(qs & _stems(e[0])), reverse=True)
    best = [e for e in scored if qs & _stems(e[0])][:k]
    return best or scored[:2]
