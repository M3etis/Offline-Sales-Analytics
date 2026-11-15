"""Подготовка данных для текстового ответа модели и проверка чисел в ответе.

Модель 7B сама переводила 1409569 в «14,09 млн» и 22063152 в «220,63 млн»: ошибка разряда
в каждом третьем ответе, разная от прогона к прогону. Поэтому числа форматируются здесь,
а модель только переносит готовые строки. После генерации числа с «млн/млрд/тыс.»
сверяются с данными, и ошибка разряда исправляется.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

# «change» без pct — не процент: revenue - LAG(revenue) AS revenue_change — абсолютная разница
_PCT_RE = re.compile(r"(pct|percent|share|_rate$|доля|процент)", re.IGNORECASE)
_COUNT_RE = re.compile(r"(^days$|order|count|quantity|qty|customers|products|regions|managers|categories|brands|channels|"
                       r"warehouses|units|клиент|заказ|штук|кол|число|days|^n_|^n$|_n$)", re.IGNORECASE)
# Средние и отношения — дробные: «avg_quantity» 1,42 нельзя округлять до 1
_FRACTION_RE = re.compile(r"avg|mean|average|средн|ratio|_per_|per_|на_", re.IGNORECASE)
# Коды, номера, годы — не деньги и не счётчики: «100245» — клиент, а не «100,2 тыс.»
_ID_KEY_RE = re.compile(r"(^|_)(id|код|code|номер|number|no|артикул|sku|barcode|штрихкод|ean|инн|бин|иин|телефон|phone|"
                        r"year|год)($|_)|^customer$|^client$|^клиент$", re.IGNORECASE)

KEY_LABELS = {
    "revenue": "выручка", "total_revenue": "выручка", "profit": "прибыль", "total_profit": "прибыль",
    "orders": "заказов", "order_count": "заказов", "quantity": "продано, шт.", "total_quantity": "продано, шт.",
    "avg_check": "средний чек", "margin_pct": "маржинальность, %", "avg_discount": "средняя скидка, %",
    "revenue_share_pct": "доля выручки, %", "customers": "клиентов", "repeat_customers": "повторных клиентов",
    "repeat_share_pct": "доля повторных клиентов, %", "one_time_customers": "клиентов с одной покупкой",
    "orders_per_customer": "заказов на клиента", "revenue_per_customer": "выручка на клиента",
    "churned_customers": "клиентов в оттоке", "revenue_change_pct": "изменение выручки к пред. периоду, %",
    "change_pct": "изменение, %", "previous_value": "предыдущий период", "current_value": "текущий период",
    "cumulative_share_pct": "накопленная доля, %", "abc_class": "класс ABC", "returned_units": "возвращено, шт.",
    "orders_with_returns": "заказов с возвратом", "return_rate_pct": "доля возвратов, %",
    "days_without_purchase": "дней без покупок", "last_purchase": "последняя покупка",
    "discount_range": "скидка, %", "period": "период", "weekday": "день недели", "type": "тип",
    "profit_share_pct": "доля прибыли, %", "orders_share_pct": "доля заказов, %", "quantity_share_pct": "доля в штуках, %",
    "days": "дней с данными", "revenue_per_day": "выручка в день", "compared_to": "к периоду",
    "product": "товар", "category": "категория", "region": "регион", "manager": "менеджер", "channel": "канал",
    "brand": "бренд", "warehouse": "склад", "status": "статус", "customer_id": "клиент", "customer": "клиент",
    "products": "товаров", "regions": "регионов", "managers": "менеджеров", "categories": "категорий",
    "brands": "брендов", "channels": "каналов", "warehouses": "складов",
}

#: Подписи только для таблиц и графиков интерфейса: в тексте для модели «amount» и «date» остаются
#: именами колонок, иначе она путала их с колонками таблицы
UI_ONLY_LABELS = {
    "sold_units": "продано, шт.", "price": "цена", "discount": "скидка", "cost": "себестоимость",
    "value": "значение", "count": "количество", "total": "итого", "amount": "сумма", "total_sold": "продано",
    "total_sales": "продажи", "sales": "продажи", "product_name": "товар", "month": "месяц", "date": "дата",
    "point_of_sale": "филиал", "store": "магазин", "manufacturer": "производитель",
    # Проверка качества данных
    "table": "таблица", "check": "проверка", "found": "найдено", "example": "пример",
}


def column_labels(aliases: dict[str, str] | None = None) -> dict[str, str]:
    """Подписи колонок результата для интерфейса — единый источник (раньше дублировались в ChatMessage.tsx).

    aliases — записи словаря колонок пользователя: «kassa_itog → выручка» подписывает и его колонку.
    """
    return {**(aliases or {}), **UI_ONLY_LABELS, **KEY_LABELS}


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool) and not (
        isinstance(v, float) and (math.isnan(v) or math.isinf(v)))


def _ru(num_str: str) -> str:
    return num_str.replace(".", ",")


def humanize(value: float) -> str:
    """2819139036 → «2,82 млрд», 1409569 → «1,41 млн», 413000 → «413 тыс.»."""
    v = float(value)
    a = abs(v)
    # Порог — по округлённому значению: 999 960 — это «1,00 млн», а не «1000 тыс.»
    if round(a / 1e9, 2) >= 1:
        return f"{_ru(f'{v / 1e9:.2f}')} млрд"
    if round(a / 1e6, 2) >= 1:
        return f"{_ru(f'{v / 1e6:.2f}')} млн"
    if a >= 1e4:
        return f"{_ru(f'{v / 1e3:.1f}').replace(',0', '')} тыс."
    return group_int(v) if float(v).is_integer() else _ru(f"{v:.2f}")


def group_int(v: float) -> str:
    return f"{int(round(v)):,}".replace(",", " ")


def kind_of(key: str, values: list[Any]) -> str:
    """money / pct / count / plain — по имени колонки и значениям."""
    k = key.lower()
    if _PCT_RE.search(k):
        return "pct"
    if _ID_KEY_RE.search(k):
        nums = [x for x in values if _is_number(x)]
        if nums and all(float(x).is_integer() for x in nums):
            return "id"
    if _FRACTION_RE.search(k) and not _PCT_RE.search(k):
        return "money" if any(w in k for w in ("revenue", "amount", "sum", "check", "чек", "выруч", "сумм")) else "plain"
    if "_per_" in k:
        # «заказов на клиента» — дробное среднее, а не счётчик: 2,37 нельзя округлять до 2
        return "money" if any(w in k for w in ("revenue", "amount", "sum")) else "plain"
    if "discount" in k or "скидк" in k:
        nums = [float(x) for x in values if _is_number(x)]
        return "pct" if nums and max(nums) <= 100 else "money"
    if _COUNT_RE.search(k):
        return "count"
    nums = [float(x) for x in values if _is_number(x)]
    if nums and max(abs(x) for x in nums) >= 1e4:
        return "money"
    return "plain"


def format_value(v: Any, kind: str) -> str:
    if v is None:
        return "нет данных"
    if isinstance(v, datetime):
        return v.strftime("%d.%m.%Y") if v.hour == v.minute == 0 else v.strftime("%d.%m.%Y %H:%M")
    if isinstance(v, date):
        return v.strftime("%d.%m.%Y")
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return "нет данных"  # деление на ноль в SQL модели: DuckDB возвращает inf/NaN
    if not _is_number(v):
        return str(v)
    if kind == "id":
        return str(int(v))
    if kind == "pct":
        return f"{_ru(f'{float(v):.1f}')} %"
    if kind == "count":
        return group_int(float(v))
    if kind == "money":
        return humanize(float(v))
    return humanize(float(v))


def format_rows_for_prompt(rows: list[dict], max_rows: int = 40) -> str:
    """Компактная текстовая таблица с готовыми числами для промпта ответа."""
    if not rows:
        return "(нет строк)"
    if not isinstance(rows[0], dict):
        return str(rows)[:3000]
    keys = list(rows[0].keys())
    kinds = {k: kind_of(k, [r.get(k) for r in rows]) for k in keys}
    lines = []
    for i, r in enumerate(rows[:max_rows], 1):
        cells = [f"{KEY_LABELS.get(k, k)}: {format_value(r.get(k), kinds[k])}" for k in keys]
        lines.append(f"{i}. " + "; ".join(cells))
    if len(rows) > max_rows:
        lines.append(f"… показаны первые {max_rows} из {len(rows)} строк")
    return "\n".join(lines)


# ── Проверка разрядов в ответе ────────────────────────────────────────────────
_UNIT = {"млрд": 1e9, "млн": 1e6, "тыс": 1e3}
_NUM_UNIT_RE = re.compile(r"(?<![\d,.])(\d{1,3}(?:[  ]\d{3})*(?:[.,]\d+)?|\d+(?:[.,]\d+)?)\s*(млрд|млн|тыс)\.?")


def _money_values(rows: list[dict]) -> list[float]:
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        for k, v in r.items():
            if _is_number(v) and abs(float(v)) >= 1e3 and kind_of(k, [v]) != "pct":
                out.append(abs(float(v)))
    return out


def _significant(raw: str) -> str:
    """Значащие цифры числа: «14,09» → «1409», «150» → «15», «0,82» → «82»."""
    return re.sub(r"\D", "", raw).strip("0")


def fix_magnitudes(answer: str, rows: list[dict], extra_values: list[float] | None = None) -> str:
    """Исправить в ответе числа с ошибкой разряда: «14,09 млн» при значении 1409569 → «1,41 млн».

    Только если это точно перенос числа из данных со сдвигом разряда: в числе ответа не меньше трёх
    значащих цифр, и они совпадают с началом значения из данных. Раньше хватало попадания ×10 в любое
    из сотни значений таблицы, и верная разница «на 1,5 млн» становилась «на 15,00 млн».
    """
    known = _money_values(rows) + [abs(x) for x in (extra_values or [])]
    if not answer or not known:
        return answer

    def close(a: float, b: float) -> bool:
        return b and abs(a - b) / b < 0.006

    def repl(m: re.Match) -> str:
        raw = m.group(1).replace(" ", "").replace(" ", "").replace(",", ".")
        try:
            num = float(raw)
        except ValueError:
            return m.group(0)
        value = num * _UNIT[m.group(2)]
        if any(close(value, k) for k in known):
            return m.group(0)
        digits = _significant(m.group(1))
        if len(digits) < 3:
            return m.group(0)
        for factor in (10, 0.1, 100, 0.01, 1000, 0.001):
            target = value / factor
            match = next((k for k in known if close(target, k) and _significant(f"{k:.0f}").startswith(digits[:3])), None)
            if match is not None:
                # Точку в конце предложения сохраняем: «1,41 млн.» не должно стать «1,41 млн»
                tail = "." if m.group(0).endswith(".") and m.end() >= len(answer.rstrip()) else ""
                return humanize(match) + tail
        return m.group(0)

    return _NUM_UNIT_RE.sub(repl, answer)
