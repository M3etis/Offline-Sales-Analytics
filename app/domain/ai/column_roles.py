"""Распознавание смысла колонок любого набора данных: где выручка, дата, город, менеджер.

Раньше роли искались по фиксированному списку английских имён (revenue, region, order_id…).
Набор с русскими заголовками («Сумма», «Город») или другими английскими именами
(total_amount, store_city, salesperson) не распознавался вовсе, и все вопросы уходили
в SQL от модели. Здесь роль определяется по трём признакам:
- имя: русские и английские основы слов, последнее слово имени весит больше
  («store_city» — город, а не магазин);
- тип: суммы и количества — числа, разрезы — текст, дата — TIMESTAMP/DATE;
- данные: номер заказа почти уникален, разрез — с небольшим числом вариантов.

Функция чистая: на вход — имена, типы и статистика колонок.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

NUMERIC_TYPES = ("INT", "DOUBLE", "FLOAT", "DECIMAL", "REAL", "NUMERIC", "HUGEINT", "BIGINT", "SMALLINT", "TINYINT")


@dataclass(frozen=True)
class RoleSpec:
    kind: str                    # number / text / date / id
    stems: tuple[str, ...]       # основы слов: совпадение с началом токена имени
    exclude: tuple[str, ...] = ()  # токены, при которых роль не подходит
    priority: int = 0            # при равных баллах — роль важнее
    # Токены, исключающие роль, только если стоят первыми: «скидка_сумма» — не выручка,
    # а «сумма_со_скидкой» — выручка
    exclude_first: tuple[str, ...] = ()


# Порядок — от более специфичных ролей к общим
ROLE_SPECS: dict[str, RoleSpec] = {
    "date": RoleSpec("date", ("date", "dt", "time", "timestamp", "day", "created", "дата", "время", "день", "dat"),
                     exclude=("birth", "рожден", "update", "modified", "deleted", "недел")),
    "order_id": RoleSpec("id", ("order", "invoice", "receipt", "check", "transaction", "txn", "заказ", "чек",
                                "накладн", "документ", "транзакц", "сделк", "orderid"),
                         # «заказчик» — клиент, а не номер заказа
                         exclude=("date", "дата", "status", "статус", "sum", "сумм", "amount", "qty", "count",
                                  "заказчик", "client", "customer", "клиент", "покупател", "тип", "type", "вид",
                                  "канал", "channel", "способ")),
    "customer": RoleSpec("text", ("customer", "client", "buyer", "клиент", "покупател", "контрагент", "заказчик"),
                         exclude=("segment", "type", "тип", "сегмент", "count", "колич")),
    "profit": RoleSpec("number", ("profit", "прибыл", "margin", "маржа", "маржин", "доходност"),
                       exclude=("pct", "percent", "rate", "ratio", "процент", "доля", "share")),
    "cost": RoleSpec("number", ("cost", "cogs", "себестоим", "затрат", "закуп")),
    # «сумма_со_скидкой» — выручка, а не скидка; «скидка_сумма» — скидка
    "discount": RoleSpec("number", ("discount", "скидк"),
                         exclude_first=("сумм", "sum", "amount", "total", "итог", "стоим", "цен", "price")),
    "returns": RoleSpec("number", ("return", "refund", "возврат")),
    "quantity": RoleSpec("number", ("qty", "quantity", "units", "pcs", "колич", "кол", "штук", "шт", "volume",
                                    "объем", "объём", "count", "sold"),
                         exclude=("order", "заказ", "customer", "клиент", "price", "цена", "sum", "сумм", "amount",
                                  "countr")),  # «country» — страна, а не «count»
    "price": RoleSpec("number", ("price", "цена", "цен", "tariff", "тариф"),
                      # «закупочная цена» — себестоимость, а не цена продажи
                      exclude=("total", "итог", "sum", "сумм", "закуп", "purchase", "себест", "cost")),
    "revenue": RoleSpec("number", ("revenue", "sales", "sale", "amount", "total", "turnover", "gmv", "sum",
                                   "выручк", "сумм", "оборот", "продаж", "доход", "стоимост", "итог", "оплат", "реализац"),
                        exclude=("cost", "себестоим", "profit", "прибыл", "tax", "налог", "unit", "price", "цена",
                                 "qty", "колич", "count", "pct", "percent", "процент", "return", "возврат",
                                 "остат", "stock", "закуп", "purchase", "сальдо", "balance",
                                 # «sale_id», «канал_продаж», «тип_оплаты», «salesman» — не суммы
                                 "id", "код", "номер", "number", "тип", "type", "вид", "канал", "channel", "способ",
                                 "method", "статус", "status", "salesm", "seller", "продавец", "менеджер", "weight",
                                 "вес", "ндс", "vat"),
                        exclude_first=("discount", "скидк"), priority=1),
    "product": RoleSpec("text", ("product", "item", "goods", "товар", "продукт", "номенклатур",
                                 "наименован", "позици", "издели", "model", "модел"),
                        exclude=("category", "категор", "group", "групп", "type", "тип", "count", "колич", "id",
                                 "счёт", "счет", "account")),
    # Артикул — отдельно: товаром должно стать читаемое «Наименование», а не код
    "sku": RoleSpec("text", ("sku", "артикул", "article", "barcode", "штрихкод", "ean")),
    "category": RoleSpec("text", ("category", "categ", "group", "segment", "класс", "категор", "групп", "сегмент",
                                  "раздел", "вид", "department", "отдел", "family")),
    "region": RoleSpec("text", ("region", "city", "area", "country", "territory", "district", "state", "location",
                                "регион", "город", "област", "филиал", "район", "страна", "территор", "населен")),
    "manager": RoleSpec("text", ("manager", "salesperson", "seller", "employee", "agent", "rep", "staff", "cashier",
                                 "менеджер", "продав", "сотрудник", "агент", "кассир", "ответствен")),
    "channel": RoleSpec("text", ("channel", "канал", "source", "источник", "platform", "площадк")),
    "brand": RoleSpec("text", ("brand", "vendor", "manufacturer", "supplier", "бренд", "производ", "марк",
                               "поставщик")),
    "warehouse": RoleSpec("text", ("warehouse", "store", "shop", "outlet", "branch", "point", "склад", "магазин",
                                   "точк", "торгов")),
    "status": RoleSpec("text", ("status", "state", "статус", "состоян")),
}

def tokens(name: str) -> list[str]:
    """«store_city» → [store, city]; «Сумма заказа» → [сумма, заказа]; «orderDate» → [order, date]."""
    s = re.sub(r"([a-zа-яё])([A-ZА-ЯЁ])", r"\1_\2", name)
    return [t for t in re.split(r"[^a-zа-яё0-9]+", s.lower()) if t]


def _is_numeric(col_type: str) -> bool:
    t = (col_type or "").upper()
    return any(x in t for x in NUMERIC_TYPES)


def _is_date(col_type: str) -> bool:
    t = (col_type or "").upper()
    return "TIMESTAMP" in t or t == "DATE" or t.startswith("DATE")


def _is_text(col_type: str) -> bool:
    return (col_type or "").upper() in ("VARCHAR", "TEXT", "STRING") or (col_type or "").upper().startswith("VARCHAR")


#: Валюта и единицы в конце имени («Сумма со скидкой, KZT», «Остаток, шт») — не «главное слово»
_UNIT_TOKENS = {"kzt", "rub", "usd", "eur", "руб", "тенге", "тг", "шт", "pcs", "pct", "текст", "text", "дней"}


def _primary_hit(toks: list[str], spec: RoleSpec) -> bool:
    """Имя совпало с главным словом роли: «category» для категории, «region» для региона.

    При равных баллах «category» важнее «segment», «region» — «country», «status» — «state»;
    раньше выигрывала колонка, стоящая левее.
    """
    main = spec.stems[0]
    return any(t == main or (len(main) >= 4 and t.startswith(main)) for t in toks)


_DATE_SECONDARY = ("оплат", "payment", "доставк", "delivery", "отгруз", "shipping", "срок", "due", "плат", "возврат")


def _name_score(toks: list[str], spec: RoleSpec) -> int:
    toks = [t for t in toks if t not in _UNIT_TOKENS] or toks
    if not toks or any(t.startswith(e) for t in toks for e in spec.exclude):
        return 0
    if spec.exclude_first and any(toks[0].startswith(e) for e in spec.exclude_first):
        return 0
    score = 0
    for i, t in enumerate(toks):
        if any(t.startswith(s) for s in spec.stems if len(s) >= 3 or t == s):
            # Последнее слово — главное: «store_city» — город, «order_date» — дата
            score = max(score, 3 if i == len(toks) - 1 else 2)
    return score


def alias_key(name: str) -> str:
    """Имя колонки для сверки со словарём: «Сумма, KZT» и «сумма_kzt» — одно и то же."""
    return "_".join(tokens(str(name)))


#: Значение из словаря колонок пользователя важнее эвристики по имени
_ALIAS_BONUS = 3


def infer_roles(columns: list[tuple[str, str]], stats: Optional[dict[str, dict]] = None,
                aliases: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Роль → имя колонки (или SQL-выражение для вычисляемой выручки).

    columns — [(имя, тип DuckDB)]; stats — {имя: {"distinct": n, "rows": m}} (необязательно);
    aliases — {alias_key(колонка): смысл} из словаря колонок пользователя: «kassa_sum → выручка»
    делает колонку выручкой, даже если по имени эвристика её не узнаёт.
    """
    stats = stats or {}
    aliases = aliases or {}
    candidates: list[tuple[int, int, int, str, str]] = []  # (баллы, приоритет, главное слово, роль, колонка)
    for name, col_type in columns:
        alias = aliases.get(alias_key(name))
        toks = tokens(alias) if alias else tokens(name)
        st = stats.get(name, {})
        distinct, rows = st.get("distinct"), st.get("rows")
        uniq = (distinct / rows) if distinct and rows else None
        for role, spec in ROLE_SPECS.items():
            score = _name_score(toks, spec)
            if not score:
                continue
            if alias:
                score += _ALIAS_BONUS
            if spec.kind == "number" and not _is_numeric(col_type):
                # Выгрузки хранят числа текстом («1 234,5»): аналитика разбирает их сама (_num).
                # Штраф — чтобы текстовая «sales_channel» осталась каналом, а не выручкой
                if not _is_text(col_type):
                    continue
                score -= 1
            if spec.kind == "date" and not (_is_date(col_type) or _is_text(col_type)):
                continue
            if spec.kind == "date" and _is_date(col_type):
                score += 2
            if spec.kind == "date" and any(t.startswith(w) for t in toks for w in _DATE_SECONDARY):
                score -= 1  # «дата оплаты» — запасной вариант: дата продажи важнее
            if role == "revenue" and any(t.startswith(w) for t in toks for w in ("без", "gross", "до")):
                score -= 1  # «сумма без скидки» — запасной вариант: выручка — после скидки
            if spec.kind == "text":
                if not _is_text(col_type) and not (role == "customer" and _is_numeric(col_type)):
                    continue
                # Разрез — немного вариантов; «клиент» и «товар» могут быть с тысячами значений
                if uniq is not None and uniq > 0.9 and role not in ("customer", "product"):
                    score -= 1
            if spec.kind == "id":
                # 3 значения на 2000 строк — не номер заказа. Но чек из 5 строк (доля уникальных 0,2) —
                # номер заказа: раньше порог 0,3 отбрасывал его, и строки считались заказами
                if uniq is not None and uniq < 0.05:
                    continue
                score += 1 if uniq and uniq > 0.5 else 0
            candidates.append((score, spec.priority, int(_primary_hit(toks, spec)), role, name))

    # Дата по типу: TIMESTAMP-колонку берём, даже если имя не говорящее («период», «created»)
    if not any(_is_date(t) for n, t in columns if any(c[4] == n and c[3] == "date" for c in candidates)):
        # Нет даты-TIMESTAMP среди кандидатов (например, текстовая «time_slot» нашлась по имени):
        # колонку с типом даты добавляем, и по типу она важнее текстовой
        for name, col_type in columns:
            if _is_date(col_type):
                candidates.append((4, 0, 0, "date", name))
                break

    roles: dict[str, str] = {}
    used: set[str] = set()
    for score, _prio, _main, role, name in sorted(candidates, key=lambda c: (-c[0], -c[1], -c[2])):
        if score <= 0 or role in roles or name in used:
            continue
        roles[role] = name
        used.add(name)

    # Нет выручки, но есть цена и количество — выручка = цена × количество
    if "revenue" not in roles and "price" in roles and "quantity" in roles:
        roles["revenue"] = f'("{roles["price"]}" * "{roles["quantity"]}")'
    # Нет прибыли, но есть себестоимость — прибыль = выручка − себестоимость. Себестоимость единицы
    # («unit_cost», «закупочная цена») — умножаем на количество: раньше из суммы строки вычиталась
    # цена одной штуки, и маржа выходила 88 % вместо 45 %
    if "profit" not in roles and "cost" in roles and "revenue" in roles:
        rev = roles["revenue"] if roles["revenue"].startswith("(") else f'"{roles["revenue"]}"'
        cost_toks = tokens(roles["cost"])
        per_unit = any(t.startswith(w) for t in cost_toks for w in ("unit", "цен", "price", "ед", "per", "штук", "шт"))
        if not per_unit:
            roles["profit"] = f'({rev} - "{roles["cost"]}")'
        elif "quantity" in roles:
            roles["profit"] = f'({rev} - "{roles["cost"]}" * "{roles["quantity"]}")'
    return roles


#: Основы слов для поиска упоминаний колонок в вопросе («по типу оплаты» → колонка «тип_оплаты»)
_GENERIC_STOP = {"id", "no", "num", "number", "code", "name", "код", "номер", "имя", "название", "the", "of"}


def column_stems(name: str) -> list[str]:
    out = []
    for t in tokens(name):
        if t in _GENERIC_STOP or len(t) < 4:
            continue
        out.append(t if len(t) <= 4 else t[: max(4, len(t) - 2)])
    return out


def _reserved_words() -> frozenset[str]:
    try:
        import duckdb

        con = duckdb.connect(":memory:")
        try:
            return frozenset(r[0].lower() for r in con.execute(
                "SELECT keyword_name FROM duckdb_keywords() WHERE keyword_category = 'reserved'").fetchall())
        finally:
            con.close()
    except Exception:
        return frozenset({"order", "group", "check", "select", "from", "where", "limit", "table", "user", "by", "to",
                          "case", "when", "then", "end", "and", "or", "not", "as", "on", "in", "is", "all", "any"})


RESERVED_WORDS = _reserved_words()


def sql_ident(name: str) -> str:
    """Имя колонки для подстановки в SQL: в кавычках, если это служебное слово или имя с пробелами.

    Роли колонок теперь находятся по смыслу, и колонкой заказа становится «order», а категории — «group».
    Без кавычек `COUNT(DISTINCT order)` — синтаксическая ошибка, и весь дашборд показывал нули.
    Вычисляемое выражение («(цена * количество)») и 'NULL' не трогаем.
    """
    if not name or name == "NULL" or name.startswith("(") or name.startswith('"'):
        return name
    if re.fullmatch(r"[a-zа-яё_][a-zа-яё0-9_]*", name) and name.lower() not in RESERVED_WORDS:
        return name
    return '"' + name.replace('"', '""') + '"'
