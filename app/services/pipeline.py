"""Общий конвейер ответа на вопрос: данные → промпт ответа → постобработка.

Им пользуются оба входа: WebSocket (streaming.process_question_streaming, его использует UI)
и HTTP (llm.process_question). Раньше это были две копии логики, и они разошлись:
HTTP-путь затирал готовые данные сгенерированным SQL, а исправление значений фильтров
было только в нём.

Порядок:
1. Типовой вопрос (domain.ai.router) — готовый расчёт (services.insights), без SQL от модели.
2. Остальное — SQL от модели: схема со значениями колонок, эталонные примеры,
   до трёх попыток «выполнить → взять ошибку → исправить».
3. Ответ модель пишет по уже отформатированным числам (domain.ai.data_format),
   после генерации разряды сверяются с данными.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Awaitable, Callable, Optional

from app.domain.ai import router as router_mod
from app.domain.ai.data_format import KEY_LABELS, fix_magnitudes, format_rows_for_prompt, format_value, kind_of
from app.domain.ai.question_parser import detect_ascending
from app.domain.ai.intent_classifier import classify_intent
from app.domain.ai.response_builder import (
    clean_answer,
    detect_chart_type,
    has_cjk,
    normalize_currency,
)
from app.domain.ai.sql_examples import build_definitions, build_examples, select_examples
from app.domain.ai.sql_utils import fix_current_date, fix_filter_values, fix_unknown_column, remap_table_names
from app.services import analytics as analytics_svc
from app.services import insights
from app.services.currency import get_local_currency
from app.services.preanalysis import load_manifest
from app.shared.prompts.templates import (
    SUMMARY_PROMPT,
    SUMMARY_PROMPT_EXTENDED,
    currency_rule,
    get_system_prompt,
    get_tables_system_prompt,
    summary_system_message,
)
from app.utils.sql_safety import full_validate, sanitize_sql

logger = logging.getLogger(__name__)

Status = Callable[[str], Awaitable[None]]

#: Запрос выполнен, но условию ничего не соответствует — это ответ («таких товаров нет»), а не сбой
EMPTY_ANSWER = "Запрос выполнен: подходящих записей в данных нет."

#: Сколько раз просить модель исправить SQL после ошибки выполнения
SQL_FIX_ATTEMPTS = 3

#: Лимит токенов ответа: 250 обрывали топ-10 и разрезы «регион × категория» на полуслове
SUMMARY_NUM_PREDICT = 700


@dataclass
class Prepared:
    intent: str = "sql"
    data: Optional[list] = None
    sql: Optional[str] = None
    note: str = ""
    chart_type: Optional[str] = None
    chart_value_key: Optional[str] = None
    extended: bool = False
    answer: Optional[str] = None      # готовый ответ без генерации (уточнение, ошибка)
    prompt_rows: Optional[list] = None  # строки для модели — только нужные колонки (None — все)
    summary_question: Optional[str] = None  # вопрос с конкретным периодом вместо «прошлый месяц»
    error: Optional[str] = None
    extra: dict = field(default_factory=dict)


async def _noop(_: str) -> None:
    return None


# ── Готовые расчёты ───────────────────────────────────────────────────────────

def _run_plan(conn, ctx: insights.TableCtx, plan: router_mod.Plan, dataset_id, manifest) -> tuple[list, str]:
    if plan.kind == "weak_spots":
        rows = analytics_svc.find_weak_spots(conn, dataset_id=dataset_id, manifest=manifest)
        return rows, "Найденные слабые места и аномалии: просадки регионов (полные месяцы), низкая маржа, убыточные скидки, отстающие менеджеры."
    fn = getattr(insights, plan.kind)
    return fn(conn, ctx, **plan.kwargs)


#: Расчёты, у которых результат — рейтинг: лидера и порядок модель путала, поэтому их пишет код
# loss_items не здесь: там строки — наименее маржинальные, и «1-е место» модель читала как «лидер по прибыли»
_RANKED = {"breakdown", "returns_overview", "abc", "change_by_dimension", "churned_customers"}


def _is_num(v) -> bool:
    # Decimal — тоже число: DuckDB возвращает его для SUM(целое) * 1.2 и CAST(... AS DECIMAL)
    return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)


def ranking_note(rows: list[dict], value_key: str, ascending: bool = False) -> str:
    """«Рейтинг: 1-е место — Омаров Т. (713,44 млн); …; последнее — …» — порядок считает код."""
    if not rows or not isinstance(rows[0], dict) or value_key not in rows[0]:
        return ""
    labels = [k for k, v in rows[0].items() if isinstance(v, str)]
    if len(labels) != 1:
        return ""
    lk = labels[0]
    vals = [(r[lk], r[value_key]) for r in rows if _is_num(r.get(value_key))]
    if len(vals) < 2:
        return ""
    negative = all(v <= 0 for _, v in vals)
    if negative and not ascending:
        # Суммы возвратов со знаком «−»: «наибольший возврат» — наибольший по модулю, а не ближайший к нулю
        order = sorted(vals, key=lambda x: abs(x[1]), reverse=True)
    else:
        # По возрастанию («сильнее всего упали») — самое отрицательное первым
        order = sorted(vals, key=lambda x: x[1], reverse=not ascending)
    kind = kind_of(value_key, [v for _, v in vals])
    # Название показателя — как в «Отсортировано по показателю …»: раньше в одном пояснении было
    # «заказов» и «число заказов», и модель не понимала, что это одно и то же
    from app.domain.ai.question_parser import METRIC_LABELS

    label = METRIC_LABELS.get(value_key) or KEY_LABELS.get(value_key, value_key)
    metric = label + (" (по модулю, значения со знаком минус)" if negative and not ascending else "")
    places = "; ".join(f"{i + 1}-е место — {n} ({format_value(v, kind)})" for i, (n, v) in enumerate(order[:3]))
    tail = f"; последнее место — {order[-1][0]} ({format_value(order[-1][1], kind)})" if len(order) > 3 else ""
    direction = "от меньшего к большему" if ascending else "от большего к меньшему"
    return f"Рейтинг по показателю «{metric}» ({direction}), посчитан точно: {places}{tail}."


_SUPERLATIVE_RE = re.compile(r"самы|больше всего|меньше всего|лучш|худш|наибол|наимен|лидер|популярн")


_RELATIVE_PERIOD_RE = re.compile(
    r"(за\s+)?(прошл\w*|предыдущ\w*|последн\w*|эт\w+|текущ\w*)\s+(месяц\w*|квартал\w*|год\w*|недел\w*)"
    r"|(за\s+)?последни\w*\s+\d+\s+\w+", re.IGNORECASE)


def concrete_question(question: str, plan: router_mod.Plan) -> Optional[str]:
    """«Выручка за прошлый месяц» → «Выручка за октябрь 2026»: модель 7B считала «прошлым месяцем»
    сентябрь (по своему представлению о дате) и отвечала, что данных нет."""
    period = plan.kwargs.get("period")
    periods = plan.kwargs.get("periods")
    if period:
        label = period.label.split(" (")[0]
        if period.kind == "days":
            label = f"период {period.start:%d.%m.%Y}–{period.end_inclusive:%d.%m.%Y}"
        replaced, n = _RELATIVE_PERIOD_RE.subn(f"за {label}", question, count=1)
        return replaced if n else f"{question} (период: {label})"
    if periods:
        return f"{question} (периоды: {'; '.join(p.label for p in periods)})"
    return None


def direct_answer(question: str, plan: router_mod.Plan, rows: list[dict], value_key: Optional[str]) -> str:
    """Готовый ответ для однозначных вопросов. Модель 7B при верном рейтинге в промпте
    всё равно называла не того лидера (отвлекалась на соседнюю колонку) и писала
    «за прошлый месяц данных нет» при выручке за октябрь в данных."""
    if not rows or not isinstance(rows[0], dict):
        return ""
    if plan.kind == "totals" and plan.show_metrics and len(rows) == 1:
        r = rows[0]
        parts = [f"{KEY_LABELS.get(k, k)} — {format_value(r.get(k), kind_of(k, [r.get(k)]))}"
                 for k in plan.show_metrics if k in r]
        period = plan.kwargs.get("period")
        when = f" за {period.label} (это и есть запрошенный период)" if period else ""
        return f"ОТВЕТ НА ВОПРОС{when}: {'; '.join(parts)}." if parts else ""
    if plan.kind == "timeline" and re.search(r"рост|измен|месяц к месяцу|динамик|по месяц", question.lower()):
        # Полный ряд: на «рост месяц к месяцу» модель перечисляла только месяцы с ростом
        if len(rows) > 40:
            return ""  # по дням за год — сотни периодов: в пояснение не помещаются, хватит таблицы
        parts = []
        for r in rows:
            ch = r.get("revenue_change_pct")
            val = format_value(r.get("revenue"), "money")
            parts.append(f"{r['period']} — {val}" + (f" ({ch:+.1f} %)".replace(".", ",").replace("-", "−") if ch is not None else ""))
        return "ОТВЕТ НА ВОПРОС (все периоды по порядку, перечисли каждый): " + "; ".join(parts) + "."
    if plan.kind == "abc" and "abc_class" in rows[0]:
        label = next(k for k, v in rows[0].items() if isinstance(v, str) and k != "abc_class")
        groups = {c: [r for r in rows if r["abc_class"] == c] for c in "ABC"}
        total = sum(r.get("revenue") or 0 for r in rows) or None
        parts = []
        for c, rs in groups.items():
            if not rs:
                continue
            # Из выручки, а не суммой округлённых долей: длинный хвост по 0,0 % давал «класс C — 0,0 %»
            share = sum(r.get("revenue") or 0 for r in rs) * 100.0 / total if total else 0.0
            names = ", ".join(str(r[label]) for r in rs[:8])
            more = f" и ещё {len(rs) - 8}" if len(rs) > 8 else ""
            parts.append(f"класс {c} — {len(rs)} поз., {f'{share:.1f}'.replace('.', ',')} % выручки ({names}{more})")
        return "ОТВЕТ НА ВОПРОС: " + "; ".join(parts) + "."
    if plan.kind == "churned_customers":
        return ""  # общее число и правило — уже в пояснении первой фразой
    if plan.kind == "breakdown" and value_key and _SUPERLATIVE_RE.search(question.lower()):
        labels = [k for k, v in rows[0].items() if isinstance(v, str)]
        if len(labels) != 1:
            return ""
        asc = plan.kwargs.get("ascending", False)
        vals = [r for r in rows if _is_num(r.get(value_key))]
        if not vals:
            return ""
        best = (min if asc else max)(vals, key=lambda r: r[value_key])
        kind = kind_of(value_key, [r[value_key] for r in vals])
        return (f"ОТВЕТ НА ВОПРОС: {best[labels[0]]} — {format_value(best[value_key], kind)} "
                f"(показатель «{KEY_LABELS.get(value_key, value_key)}»).")
    return ""


def _select_columns(rows: list[dict], show: list[str], value_key: Optional[str]) -> Optional[list[dict]]:
    """Оставить для модели подписи, нужные показатели и доли."""
    if not rows or not isinstance(rows[0], dict):
        return None
    labels = [k for k, v in rows[0].items() if isinstance(v, str)]
    if len(labels) >= 2:
        # Разрез из двух измерений: доля от общей выручки у пары «регион × менеджер» модель
        # принимала за долю внутри группы и выводила бессмысленные сравнения. Итоги групп — в пояснении
        rows = [{k: v for k, v in r.items() if not k.endswith("_share_pct")} for r in rows]
    if not show:
        return rows if len(labels) >= 2 else None
    keep = set(show) | ({value_key} if value_key else set())
    keys = [k for k, v in rows[0].items()
            if not _is_num(v) or k in keep or (k.endswith("_share_pct") and k.split("_share")[0] in keep)]
    return [{k: r.get(k) for k in keys} for r in rows]


_DQ_RE = re.compile(r"несостыков|(?<![а-яё])дубл(?:и|ей|ь|ям|ями|ях|ир|икат)|качеств\w* данн|ошибк\w* в данн|противореч\w* в данн|некорректн\w* данн|"
                    r"проверь данн|пропуск\w* в данн|аномали\w* в данн|неполн\w* данн|расхожден")


def _data_quality(db_conn, group: list[dict], question: str) -> Prepared:
    from app.domain.ai.column_roles import column_stems

    tables = [{"name": g.get("name"), "table_name": g.get("table_name")} for g in group if g.get("table_name")]
    ql = question.lower()
    named = [t for t in tables
             if any(re.search(r"(?<![a-zа-яё])" + re.escape(st[:5]), ql) for st in column_stems(str(t["name"])))]
    if named:
        tables = named
    rels = []
    try:
        raw = group[0].get("relationships") if group else None
        rels = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except Exception:
        pass
    rows, note = insights.data_quality(db_conn, tables, rels)
    return Prepared(intent="sql", data=rows, note=note, chart_type=None,
                    answer=insights.data_quality_answer(rows), extra={"plan": "data_quality"})


#: Предлоги, союзы и единицы — не аббревиатуры («по» в «отсрочка_по_договору» совпадало с каждым вопросом)
_SHORT_WORDS = {"по", "на", "во", "со", "до", "от", "за", "из", "не", "но", "ли", "или", "для", "при", "без", "под",
                "над", "kzt", "rub", "usd", "eur", "руб", "шт", "id", "no", "num", "the", "of", "and", "ед", "изм"}

_SALES_Q_RE = re.compile(r"выруч|продаж|продал|прода[её]т|продав|оборот|\bчек")


#: Расчёты, которые применяют фильтры: неузнанное название в вопросе для них — повод уйти в SQL
_FILTERABLE = {"totals", "breakdown", "compare", "timeline", "returns_overview", "abc", "loss_items",
               "churned_customers", "customers_overview", "discount_effect", "change_by_dimension"}


def resolve_entities(conn, ctx: insights.TableCtx, question: str) -> tuple[dict[str, list[str]], list[str]]:
    """Найти в данных названия, которых нет в списке значений: город среди 80 городов, бренд среди сотен.

    Возвращает ({колонка: значения} — фильтры для роутера, [слова, которых в данных нет]).
    """
    from app.domain.ai.question_parser import unresolved_words, word_value_regex

    words = unresolved_words(question, ctx.values, ctx.roles, ctx.columns)
    if not words:
        return {}, []
    text_cols = [c for c, t in ctx.col_types.items() if str(t).upper().startswith("VARCHAR")]
    # Порядок поиска: колонки с ролью разреза — раньше прочих
    role_cols = [ctx.roles[r] for r in ("region", "customer", "product", "brand", "manager", "warehouse", "channel",
                                        "category", "sku") if r in ctx.roles and ctx.roles[r] in text_cols]
    order = role_cols + [c for c in text_cols if c not in role_cols]
    extra: dict[str, list[str]] = {}
    left = []
    for w in words:
        rx = word_value_regex(w)
        hit = None
        for col in order:
            if col in ctx.values:
                vals = [v for v in ctx.values[col] if v is not None and re.search(rx, str(v).lower())]
            else:
                try:
                    vals = [r[0] for r in conn.execute(
                        f'SELECT DISTINCT CAST("{col}" AS VARCHAR) FROM {ctx.table} '
                        f'WHERE regexp_matches(lower(CAST("{col}" AS VARCHAR)), ?) LIMIT 50', [rx]).fetchall()]
                except Exception:
                    vals = []
            if vals:
                hit = (col, vals)
                break
        if hit:
            extra.setdefault(hit[0], [])
            extra[hit[0]] += [v for v in hit[1] if v not in extra[hit[0]]]
        else:
            left.append(w)
    return extra, left


def _select_tables(db_conn, group: list[dict], question: str):
    """Какие таблицы группы нужны вопросу.

    Возвращает (ctx, id, манифест, sql_tables): ctx главной таблицы (продажи) — если вопрос про неё,
    иначе None и список из 1–2 таблиц для SQL. Раньше модели давали все листы сразу (10 таблиц
    выгрузки 1С) — она путала листы и колонки, и ни один такой запрос не выполнялся.
    """
    from app.domain.ai.column_roles import column_stems, infer_roles, tokens
    from app.domain.ai.question_parser import _stem, _value_tokens, column_mentions, detect_filters_detailed

    built = []
    for gd in group:
        m = load_manifest(db_conn, gd.get("id"))
        c = insights.build_ctx(db_conn, gd.get("id"), m)
        if c and "date" in c.roles:
            built.append(((sales_score(gd, c), (m or {}).get("row_count") or 0), gd, c, m))
    if not built:
        return None, None, None, None
    # Главная таблица — по признакам продаж, и только при равенстве — по числу строк: лист «Поступления»
    # на 900 строк с «датой» и «суммой» раньше считался продажами вместо «Продаж» на 300 строк
    _, primary, ctx, m = max(built, key=lambda x: x[0])
    ql = question.lower()
    # Слова, уже узнанные как значения главной таблицы («онлайн-канал» — точка продаж), из поиска убираем:
    # иначе «канал» находил колонку «канал» листа возвратов
    for col, matched in detect_filters_detailed(question, ctx.values).items():
        for v in matched:
            # Все слова значения, включая служебные («канал» в «Онлайн-канал»)
            for t in re.split(r"[^a-zа-яё0-9]+", str(v).lower()):
                if len(t) >= 4:
                    ql = re.sub(r"(?<![a-zа-яё])" + re.escape(_stem(t)) + r"[a-zа-яё]*", " ", ql)
    own_stems = {_stem(t) for vs in ctx.values.values() for v in vs for t in _value_tokens(str(v))}
    own_stems |= {st for c in ctx.columns for st in column_stems(c)}

    scores: list[tuple[int, dict]] = []
    for gd in group:
        if gd.get("id") == primary.get("id"):
            continue
        score = 0
        # Название листа в вопросе: «остатки», «поступления», «поставщики»
        if any(re.search(r"(?<![a-zа-яё])" + re.escape(st[:4]), ql) for st in column_stems(str(gd.get("name", "")))):
            score += 5
        # Колонки, которых нет в главной таблице: «дней хранения», «причина», «банк»
        try:
            cols = json.loads(gd["columns"]) if isinstance(gd.get("columns"), str) else (gd.get("columns") or [])
        except Exception:
            cols = []
        other = []
        for col in cols:
            key = col.get("key")
            if not key or key in ctx.columns:
                continue
            # «склад» поступлений — тот же смысл, что «торговая точка» продаж: не признак другого листа
            kind = {"numeric": "DOUBLE", "date": "TIMESTAMP"}.get(col.get("type"), "VARCHAR")
            if set(infer_roles([(key, kind)])) & set(ctx.roles):
                continue
            other.append(key)
        strict = column_mentions(ql, other)
        loose = [c for c in column_mentions(ql, other, loose=True) if c not in strict]
        score += 2 * len(strict) + len(loose)
        # Аббревиатура целым словом («ДДС» в «статья_ддс», «ОСВ») — сильный признак листа
        acr = {t for c in other + [str(gd.get("name", ""))] for t in tokens(c)
               if 2 <= len(t) <= 3 and t.isalpha() and t not in _SHORT_WORDS}
        score += 3 * sum(1 for t in acr if re.search(r"(?<![a-zа-яё])" + re.escape(t) + r"(?![a-zа-яё])", ql))
        # Значения только этого листа: «приход / расход», «не проведён»
        try:
            types = dict(db_conn.execute(
                "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ?",
                [gd.get("table_name")]).fetchall())
            values = insights.categorical_values(db_conn, gd.get("table_name"), types)
        except Exception:
            values = {}
        if _mentions_foreign_values(ql, values, own_stems):
            score += 2
        if score:
            scores.append((score, gd))
    if not scores:
        return ctx, primary.get("id"), m, None
    best = max(scores, key=lambda x: x[0])[1]
    # «Выручка по брендам» (бренд — в номенклатуре), «процент возвратов от продаж» — две таблицы
    tables = [primary, best] if _SALES_Q_RE.search(question.lower()) else [best]
    return None, None, None, with_related(group, tables)


_SALES_NAME_RE = re.compile(r"продаж|реализац|выручк|sales|orders|заказ|чек|транзакц")
_NOT_SALES_NAME_RE = re.compile(r"поступлен|закуп|приход|поставщик|ддс|движени|денежн|касс|банк|плат[её]ж|остат|"
                                r"расход|списан|инвентар|purchase|supplier|stock|inventory|cash|payment")
_NOT_SALES_COL_RE = re.compile(r"поставщик|supplier|закуп|purchase|статья|ддс|контрагент_поставщик")


def sales_score(gd: dict, ctx: insights.TableCtx) -> int:
    """Насколько таблица похожа на продажи: название листа, роли товара, клиента, заказа; минус — закупки и деньги."""
    name = str(gd.get("name") or "").lower()
    score = 4 if _SALES_NAME_RE.search(name) else 0
    if _NOT_SALES_NAME_RE.search(name):
        score -= 4
    if any(_NOT_SALES_COL_RE.search(c.lower()) for c in ctx.columns):
        score -= 2
    return score + sum(1 for r in ("product", "customer", "order_id", "quantity", "manager", "channel") if r in ctx.roles)


def with_related(group: list[dict], tables: list[dict], limit: int = 3) -> list[dict]:
    """Добавить к таблицам вопроса связанные справочники (по связям, найденным при загрузке).

    Без них в SQL уходила одна таблица продаж: «выручка по категориям», когда категория — в справочнике
    товаров, отвечалась только по артикулам. Не больше трёх таблиц: со всеми листами выгрузки 1С
    модель путалась.
    """
    try:
        raw = group[0].get("relationships") if group else None
        rels = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except Exception:
        rels = []
    out = list(tables)
    names = {t.get("name") for t in out}
    for r in rels:
        for a, b in ((r.get("from_table"), r.get("to_table")), (r.get("to_table"), r.get("from_table"))):
            if len(out) >= limit:
                return out
            if a in names and b not in names:
                gd = next((g for g in group if g.get("name") == b), None)
                if gd:
                    out.append(gd)
                    names.add(b)
    return out


#: Лексика продаж: эти слова встречаются в значениях других листов («Выручка розница» в статьях ДДС),
#: но вопрос с ними — про продажи
_SALES_WORDS = ("выруч", "прода", "оплат", "сумм", "товар", "клиен", "заказ", "прибы", "марж", "скидк",
                "катег", "бренд", "город", "регио", "магаз", "точк", "чек", "покуп", "розни")


def _mentions_foreign_values(question: str, values: dict[str, list], own_stems: set[str]) -> bool:
    """Есть ли в вопросе слово из значений другого листа, которого нет в главной таблице."""
    from app.domain.ai.question_parser import _GENERIC_VALUE_WORDS, _stem, _value_tokens

    ql = question.lower()
    for vs in values.values():
        if len(vs) > 20:
            continue  # свободный текст (назначение платежа) — слишком шумно
        for v in vs:
            for t in _value_tokens(str(v)):
                st = _stem(t)
                if len(t) < 5 or t in _GENERIC_VALUE_WORDS or st in own_stems or st.startswith(_SALES_WORDS):
                    continue
                if re.search(r"(?<![a-zа-яё])" + re.escape(st), ql):
                    return True
    return False


_GROWTH_RE = re.compile(r"(?<![а-яё])рост(?!ов)|прирост|изменени|изменил|месяц к месяцу|неделя к неделе|м/м|mom\b")


def _pct(v: float) -> str:
    return f"{v:+.1f} %".replace(".", ",").replace("-", "−")


_QUESTION_WORDS_RE = re.compile(r"^\s*(какой|какая|какое|какие|каков[аоы]?|сколько|чему равн\w+|покажи|посчитай|найди)\s+",
                                re.IGNORECASE)


_RETURNS_WORDS_RE = re.compile(r"возврат|возвращ|return|refund|списан|сторно")
_MONEY_KEY_RE = re.compile(r"revenue|sum|amount|total|price|cost|profit|check|сумм|выручк|прибыл|себест|стоим|чек|цен|оборот|"
                           r"доход|сальдо|оплат|выплат|расход|затрат", re.IGNORECASE)
_COUNT_Q_RE = re.compile(r"сколько\s+(?:всего\s+)?(?:строк|запис|заказ|чек|штук|единиц|клиент|покупател|товар|позици|"
                         r"документ|дней|раз|сделок|транзакц)")


def single_value_answer(question: str, row: dict, currency: str = "") -> str:
    """«Какой процент возвратов от продаж?» + {pct: −2,69} → «Процент возвратов от продаж: 2,7 %».

    Знак сохраняется: убыток −1,25 млн — это убыток, а не «прибыль 1,25 млн». По модулю показываем
    только возвраты и списания, которые учётные системы пишут со знаком минус.
    """
    key, value = next(iter(row.items()))
    value = float(value) if isinstance(value, Decimal) else value
    subject = _QUESTION_WORDS_RE.sub("", question.strip()).rstrip("?. ").strip()
    subject = subject[:1].upper() + subject[1:] if subject else KEY_LABELS.get(key, key)
    kind = kind_of(key, [value])
    if kind == "plain" and re.search(r"процент|доля|%", question.lower()):
        kind = "pct"
    if kind == "money" and _COUNT_Q_RE.search(question.lower()) and not _MONEY_KEY_RE.search(key):
        kind = "count"  # «сколько строк в таблице» — число, а не «25 тыс. тенге»
    returns_sign = value < 0 and bool(_RETURNS_WORDS_RE.search(f"{question} {key}".lower()))
    shown = abs(value) if returns_sign else value
    text = f"{subject}: {format_value(shown, kind)}"
    if kind == "money" and currency and (_MONEY_KEY_RE.search(key) or _MONEY_KEY_RE.search(question)):
        text += f" {currency}"
    if returns_sign:
        text += " (в данных эти суммы записаны со знаком минус)"
    return text + "."


def list_answer(rows: list[dict], total: Optional[int] = None, show: int = 20) -> str:
    """Ответ-список: сколько найдено и первые позиции."""
    n = total or len(rows)
    lines = []
    for r in rows[:show]:
        vals = [format_value(v, "plain") for v in r.values() if v is not None and str(v).strip()]
        lines.append("- " + " — ".join(vals))
    more = f"\n… и ещё {n - len(lines)}" if n > len(lines) else ""
    return f"Найдено: {n}.\n" + "\n".join(lines) + more


def growth_answer(rows: list[dict]) -> str:
    """Изменение выручки по периодам — все периоды подряд, с самым сильным ростом и падением."""
    lines, changes = [], []
    for r in rows:
        ch = r.get("revenue_change_pct")
        val = format_value(r.get("revenue"), "money")
        if ch is None:
            if "неполный" in str(r.get("period")):
                note = " — неполный период, изменение не считаем"
            elif lines:
                note = " — предыдущий период неполный, сравнивать с ним некорректно"
            else:
                note = ""
            lines.append(f"- {r['period']}: {val}{note}")
        else:
            lines.append(f"- {r['period']}: {val} ({_pct(ch)} к периоду «{r.get('compared_to')}»)")
            changes.append((ch, r["period"]))
    text = "Изменение выручки к предыдущему периоду:\n" + "\n".join(lines)
    if changes:
        up, down = max(changes), min(changes)
        # «Выросла» — только при росте: при одном снижении было «выросла: октябрь (−16,3 %)»
        if up[0] > 0:
            text += f"\n\nСильнее всего выручка выросла: {up[1]} ({_pct(up[0])})."
        if down[0] < 0:
            text += f"{chr(10) * 2 if up[0] <= 0 else ' '}Сильнее всего снизилась: {down[1]} ({_pct(down[0])})."
    return text


def _prev_user_question(history_rows: list[tuple] | None) -> Optional[str]:
    for role, content in reversed(history_rows or []):
        if role == "user" and content:
            return content
    return None


# ── SQL-путь ──────────────────────────────────────────────────────────────────

def _known_columns(conn, tables: list[dict], single_table: Optional[str]) -> list[str]:
    names = [single_table] if single_table else [t.get("table_name") for t in tables]
    cols: list[str] = []
    for n in names:
        try:
            cols += [r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?", [n]).fetchall()]
        except Exception:
            pass
    return cols


def _column_keys(gd: dict) -> list[str]:
    try:
        cols = json.loads(gd["columns"]) if isinstance(gd.get("columns"), str) else (gd.get("columns") or [])
        return [c["key"] for c in cols]
    except Exception:
        return []


def _values_ctx(conn, gd: dict, ctx: Optional[insights.TableCtx], table_name: Optional[str] = None):
    """Контекст со значениями текстовых колонок для схемы любой таблицы (не только продаж)."""
    table = table_name or gd.get("table_name")
    if ctx and ctx.table == table:
        return ctx
    try:
        types = dict(conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ?", [table]).fetchall())
    except Exception:
        return None
    return insights.TableCtx(table=table, roles={}, columns=list(types), col_types=types,
                             values=insights.categorical_values(conn, table, types))


def _schema_lines(manifest: Optional[dict], ctx: Optional[insights.TableCtx], conn, table_name: str) -> str:
    """Схема для модели: тип, диапазон и — для текстовых колонок — все значения."""
    lines = []
    values = ctx.values if ctx else {}
    if manifest and manifest.get("columns"):
        cols = [(c["name"], c["type"]) for c in manifest["columns"]]
        profiles = manifest.get("profiles", {})
    else:
        try:
            cols = conn.execute(
                "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ?", [table_name]
            ).fetchall()
        except Exception:
            cols = []
        profiles = {}
    # Общий бюджет на списки значений: широкий лист 1С (40 справочных колонок) давал промпт в 56 тыс.
    # символов при контексте модели 8192 токена — начало промпта с правилами обрезалось
    budget = 6000
    per_col = 30 if len(values) <= 8 else 12
    for name, typ in cols:
        if ctx and name in ctx.col_types:
            typ = ctx.col_types[name]
        stats = profiles.get(name, {}).get("stats", {}) or {}
        extra = ""
        if stats.get("min") is not None and stats.get("max") is not None:
            extra = f" [{stats['min']}..{stats['max']}]"
        if name in values and budget > 0:
            vals = values[name]
            shown = ", ".join("'" + (str(v)[:40] + "…" if len(str(v)) > 40 else str(v)) + "'" for v in vals[:per_col])
            shown = shown[:budget]
            budget -= len(shown)
            extra += f" значения: {shown}{' …' if len(vals) > per_col else ''}"
        elif profiles.get(name, {}).get("sample_values") and typ == "VARCHAR":
            extra += " примеры: " + ", ".join(f"'{v}'" for v in profiles[name]["sample_values"][:3])
        lines.append(f"- {name} {typ}{extra}")
    return "\n".join(lines) if lines else "- (schema could not be loaded)"


_TO_CHAR_RE = re.compile(r"\bTO_CHAR\s*\(\s*([^,()]+(?:\([^()]*\))?)\s*,\s*'([^']*)'\s*\)", re.IGNORECASE)
#: Шаблоны Postgres/Oracle → strftime. Длинные — раньше коротких: «YYYY» до «YY», «MM» после «MON»
_PG_FORMAT = [("YYYY", "%Y"), ("YY", "%y"), ("Month", "%B"), ("MONTH", "%B"), ("Mon", "%b"), ("MON", "%b"),
              ("MM", "%m"), ("DD", "%d"), ("HH24", "%H"), ("HH", "%I"), ("MI", "%M"), ("SS", "%S"), ("Day", "%A"),
              ("DAY", "%A"), ("Dy", "%a"), ("Q", "%Q")]


def _pg_to_strftime(fmt: str) -> Optional[str]:
    out, i = "", 0
    while i < len(fmt):
        for pg, st in _PG_FORMAT:
            if fmt.startswith(pg, i):
                if st == "%Q":
                    return None  # квартала в strftime нет — пусть модель перепишет по ошибке
                out += st
                i += len(pg)
                break
        else:
            if fmt[i].isalpha():
                return None  # неизвестный шаблон: подставлять его буквами — тихо неверный результат
            out += fmt[i]
            i += 1
    return out


def deterministic_sql_fixes(sql: str, ctx: Optional[insights.TableCtx] = None) -> str:
    """Правки известных ошибок модели до выполнения: TO_CHAR (нет в DuckDB) → strftime с переводом формата.

    Раньше заменялось только имя функции: strftime(дата, 'YYYY-MM') возвращал строку «YYYY-MM» для
    каждой строки, и все месяцы сливались в одну группу без ошибки.
    """
    if not sql:
        return sql

    def repl(m: re.Match) -> str:
        fmt = _pg_to_strftime(m.group(2))
        return m.group(0) if fmt is None else f"strftime({m.group(1).strip()}, '{fmt}')"

    return _TO_CHAR_RE.sub(repl, sql)


_OUTER_LIMIT_RE = re.compile(r"\s+LIMIT\s+(\d+)\s*;?\s*$", re.IGNORECASE)


async def _total_rows(db_conn, sql: Optional[str], shown: int) -> Optional[int]:
    """Сколько строк было бы без внешнего LIMIT — если результат упёрся в лимит."""
    if not sql or not shown:
        return None
    m = _OUTER_LIMIT_RE.search(sql)
    if not m or int(m.group(1)) != shown:
        return None
    try:
        # С потолком: без него SQL модели (например, перекрёстное соединение листов) выполнялся целиком
        rows = await _run_sql(db_conn, f"SELECT COUNT(*) AS n FROM (SELECT * FROM ({sql[:m.start()]}) AS _q LIMIT 100001) AS _c")
        n = rows[0]["n"] if rows else None
        return n if n and n > shown else None
    except Exception:
        return None


async def _run_sql(db_conn, sql: str) -> list[dict]:
    """Выполнить SQL модели в отдельном потоке; при отмене прервать запрос в DuckDB."""
    def clean(v):
        # Деление на ноль: DuckDB возвращает inf/NaN, а в JSON это «NaN» — браузер не разбирал ответ
        return None if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))) else v

    def run() -> list[dict]:
        rel = db_conn.execute(sql)
        cols = [d[0] for d in rel.description] if rel.description else []
        return [{c: clean(v) for c, v in zip(cols, row)} for row in rel.fetchall()]

    try:
        return await asyncio.to_thread(run)
    except asyncio.CancelledError:
        try:
            db_conn.interrupt()
        except Exception:
            pass
        raise


async def _sql_path(question: str, contextual_question: str, db_conn, dataset_id, manifest,
                    ctx: Optional[insights.TableCtx], status: Status, ollama_client,
                    few_shot_history: list[dict], tables: Optional[list[dict]] = None) -> Prepared:
    prep = Prepared(intent=classify_intent(question))
    if callable(few_shot_history):
        # Примеры из прошлых ответов нужны только SQL-пути: на готовые расчёты запрос к истории не тратим
        few_shot_history = few_shot_history() or []
    # tables — уже выбранные для вопроса таблицы группы (1–2); без них — набор данных как есть
    group_datasets = tables if tables else analytics_svc.get_group_datasets(db_conn, dataset_id)
    is_group = len(group_datasets) > 1
    group_manifests: dict = {}
    name_map: dict = {}
    date_expr = None
    no_relation = False

    if is_group:
        relationships = []
        try:
            rel_json = group_datasets[0].get("relationships")
            if rel_json:
                relationships = json.loads(rel_json) if isinstance(rel_json, str) else rel_json
        except Exception:
            pass
        blocks = []
        for gd in group_datasets:
            m = load_manifest(db_conn, gd.get("id")) if gd.get("id") else None
            if m:
                group_manifests[gd["id"]] = m
            blocks.append((gd["name"], _schema_lines(m, _values_ctx(db_conn, gd, ctx), db_conn, gd["table_name"])))
        names = {gd["name"] for gd in group_datasets}
        joins = [f'{r["from_table"]}.{r["from_col"]} = {r["to_table"]}.{r["to_col"]}' for r in relationships
                 if r.get("from_table") in names and r.get("to_table") in names]
        shared_note = ""
        no_relation = not joins
        if not joins:
            # Прямой связи нет: JOIN по общему артикулу размножает строки («100 % возвратов от продаж»).
            # Общую колонку подсказываем только для IN / NOT IN
            shared = set.intersection(*[set(_column_keys(gd)) for gd in group_datasets])
            keys = [c for c in sorted(shared) if any(w in c for w in ("артикул", "код", "id", "sku", "номер"))]
            if keys:
                shared_note = (f"Общая колонка {', '.join(keys)} — только для IN / NOT IN (например, «есть в одной таблице, "
                               f"но нет в другой»), НЕ для JOIN: строки размножатся")
        sys_prompt = get_tables_system_prompt(blocks, joins, shared_note)
        schema_str = "\n\n".join(f"{n}:\n{b}" for n, b in blocks)
        table_name = group_datasets[0]["name"]
        name_map = {gd["name"]: gd["table_name"] for gd in group_datasets}
        examples = few_shot_history
    else:
        one = group_datasets[0] if group_datasets else {"id": dataset_id}
        if one.get("id") and one.get("id") != dataset_id:
            manifest = load_manifest(db_conn, one["id"])
            dataset_id = one["id"]
        table_name = manifest["table_name"] if manifest else analytics_svc._get_dataset_meta(db_conn, dataset_id)
        # Определения и эталонные примеры — только для таблицы продаж (ctx); для листа остатков
        # «выручка = SUM(розничная стоимость)» ввела бы модель в заблуждение
        own_ctx = ctx if ctx and ctx.table == table_name else None
        date_expr = own_ctx.date_expr if own_ctx else None
        schema_str = _schema_lines(manifest, own_ctx or _values_ctx(db_conn, one, None, table_name), db_conn, table_name)
        col_names = [c["name"] for c in manifest["columns"]] if manifest and manifest.get("columns") else []
        roles = own_ctx.roles if own_ctx else {}
        sys_prompt = get_system_prompt(table_name, schema_str, col_names, date_expr=date_expr,
                                       definitions=build_definitions(roles) if roles else None)
        ref = select_examples(question, build_examples(table_name, roles, date_expr)) if roles else []
        examples = few_shot_history + [{"question": q, "sql": s} for q, s in ref]

    await status("Формирую SQL-запрос...")
    sql = await ollama_client.generate_sql(_trim_history(contextual_question), sys_prompt, table_name, schema_str, examples,
                                           is_multi_table=is_group, date_expr=None if is_group else date_expr)

    def post(s: str) -> str:
        if is_group:
            s = remap_table_names(s, name_map)
            s = fix_current_date(s, group_manifests)
        elif manifest:
            s = fix_current_date(s, {dataset_id: manifest})
            s = fix_filter_values(s, db_conn, table_name, manifest)
        return deterministic_sql_fixes(s, ctx)

    if sql and sql.strip().upper().startswith("CLARIFY:"):
        prep.answer = sql.split(":", 1)[1].strip()
        prep.data = []
        return prep

    sql = post(sql)
    await status("Выполняю запрос к базе данных...")
    last_error = None
    known_cols = _known_columns(db_conn, group_datasets, table_name if not is_group else None)
    for attempt in range(SQL_FIX_ATTEMPTS + 1):
        ok, err = full_validate(sql)
        if ok and is_group and no_relation and re.search(r"\bJOIN\b", sql, re.IGNORECASE):
            # Две таблицы без связи: JOIN по общему артикулу размножает строки («−80 % возвратов от продаж»)
            ok, err = False, ("Таблицы не связаны: JOIN размножает строки и искажает суммы. Перепиши без JOIN — "
                              "доли и проценты через два независимых подзапроса (SELECT SUM(...) FROM A) / (SELECT SUM(...) FROM B), "
                              "«есть в одной, нет в другой» — через NOT IN (SELECT ... FROM B)")
        if ok:
            try:
                # Показываем тот SQL, что выполнен: с добавленным LIMIT 1000 — иначе обрезка строк была не видна
                executed = sanitize_sql(sql)
                prep.data = await _run_sql(db_conn, executed)
                prep.sql = executed
                last_error = None
                break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                err = str(e)
                # Искажённое имя колонки («brand» вместо «бренд») чиним сами, без новой попытки модели
                repaired = sql
                for _ in range(5):
                    fixed_col = fix_unknown_column(repaired, err, known_cols)
                    if not fixed_col or not full_validate(fixed_col)[0]:
                        break
                    repaired = fixed_col
                    try:
                        executed = sanitize_sql(repaired)
                        prep.data = await _run_sql(db_conn, executed)
                        prep.sql, sql, last_error, err = executed, repaired, None, None
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception as e2:
                        err = str(e2)
                if err is None:
                    break
                # Частичный ремонт сохраняем: модели — исправленный SQL вместе с ЕГО ошибкой, а не исходный
                sql = repaired
        last_error = err
        prep.sql = sql
        if attempt == SQL_FIX_ATTEMPTS:
            break
        logger.info(f"SQL attempt {attempt + 1} failed: {err[:200]}")
        try:
            fixed = await ollama_client.fix_sql(_trim_history(contextual_question), sql, err, sys_prompt, is_group)
        except Exception as e:
            logger.error(f"SQL fix request failed: {e}")
            break
        if not fixed or fixed.strip() == sql.strip():
            break
        if fixed.strip().upper().startswith("CLARIFY:"):
            break
        sql = post(fixed)

    if last_error is not None:
        prep.error = last_error
        ok, _ = full_validate(prep.sql or "")
        prep.answer = ("Не могу выполнить запрос из-за ограничений безопасности." if not ok
                       else "Не получилось построить запрос к данным. Попробуйте переформулировать вопрос.")
        return prep

    prep.note = insights.DEFINITIONS_NOTE if ctx and ctx.table == table_name else ""
    if ctx and ctx.table == table_name and ctx.data_start and ctx.data_end:
        prep.note = f"Данные охватывают {ctx.data_start:%d.%m.%Y}–{ctx.data_end:%d.%m.%Y}. " + prep.note
    total = await _total_rows(db_conn, prep.sql, len(prep.data or []))
    if total:
        prep.note = f"Всего строк по запросу: {total}; ниже показаны первые {len(prep.data)}. " + prep.note
    if prep.data and len(prep.data) == 1 and isinstance(prep.data[0], dict) \
            and not re.search(r"измен|рост|сниж|упал|вырос|сравн", question.lower()):
        nums = [v for v in prep.data[0].values() if _is_num(v)]
        if nums and all(v < 0 for v in nums):
            # Возвраты в выгрузках 1С — отрицательные суммы: «−2,69 %» модель читала как «снизился на 2,7 %»
            prep.note = ("Знак минус — свойство данных (суммы возвратов/списаний записаны отрицательными), "
                         "а не снижение: называй значение по модулю и не говори о росте или падении. " + prep.note)
    # Строки — готовый ответ на вопрос: модель писала «нет информации», получив список артикулов
    prep.note = ("Строки ниже — результат запроса, составленного именно по этому вопросу: это и есть ответ, "
                 "перескажи или обобщи их. " + prep.note).strip()
    prep.chart_type = detect_chart_type(prep.intent, question, prep.data)
    single = prep.data[0] if prep.data and len(prep.data) == 1 and isinstance(prep.data[0], dict) else None
    if single and len(single) == 1 and _is_num(next(iter(single.values()))):
        # Одно число: модель дописывала «снизился на 2,7 %» к доле возвратов. Ответ собирает код
        prep.answer = single_value_answer(question, single, answer_currency(prep, db_conn))
        return prep
    if prep.data and isinstance(prep.data[0], dict) and not any(_is_num(v) for v in prep.data[0].values()):
        # Список без чисел (товары, документы): модель отвечала «таких нет», получив 100 строк
        prep.answer = list_answer(prep.data, total)
        return prep
    if prep.data and len(prep.data) >= 2 and isinstance(prep.data[0], dict):
        num, asc = _order_column(prep.sql or "", prep.data[0])
        if num is None:
            num = next((k for k, v in prep.data[0].items() if _is_num(v)), None)
            asc = detect_ascending(question)
        if num:
            prep.note = f"{ranking_note(prep.data, num, asc)} {prep.note}".strip()
    return prep


def _order_column(sql: str, row: dict) -> tuple[Optional[str], bool]:
    """Колонка внешнего ORDER BY и направление: рейтинг — по тому, по чему SQL сортировал.

    Раньше рейтинг строился по первой числовой колонке: «ORDER BY stoimost» давал «1-е место» по остатку.
    """
    m = None
    for m in re.finditer(r"\bORDER\s+BY\s+(\"?[\w]+\"?)(?:\s+(ASC|DESC))?", sql, re.IGNORECASE):
        pass
    if not m:
        return None, False
    col = m.group(1).strip('"')
    key = next((k for k in row if k.lower() == col.lower()), None)
    if key is None or not _is_num(row.get(key)):
        return None, False
    return key, (m.group(2) or "ASC").upper() == "ASC"


# ── Точка входа ───────────────────────────────────────────────────────────────

async def prepare(question: str, contextual_question: str, db_conn, dataset_id: Optional[str],
                  status: Status | None = None, history_rows: list[tuple] | None = None,
                  ollama_client=None, few_shot_history: list[dict] | None = None) -> Prepared:
    """Получить данные для ответа: готовый расчёт или SQL от модели."""
    prep = await _prepare(question, contextual_question, db_conn, dataset_id, status, history_rows,
                          ollama_client, few_shot_history)
    if "source_columns" not in prep.extra:
        prep.extra["source_columns"] = _known_columns(db_conn, analytics_svc.get_group_datasets(db_conn, dataset_id), None)
    return prep


async def _prepare(question: str, contextual_question: str, db_conn, dataset_id: Optional[str],
                   status: Status | None = None, history_rows: list[tuple] | None = None,
                   ollama_client=None, few_shot_history: list[dict] | None = None) -> Prepared:
    status = status or _noop
    if ollama_client is None:
        from app.services.llm import ollama_client as _client
        ollama_client = _client
    question = question.strip()

    await status("Анализирую запрос...")
    manifest = load_manifest(db_conn, dataset_id)
    group = analytics_svc.get_group_datasets(db_conn, dataset_id)
    calc_id, calc_manifest = dataset_id, manifest
    sql_tables = None
    if _DQ_RE.search(question.lower()):
        # «Найди несостыковки» — проверки качества по всем таблицам (листам) или по названной
        return _data_quality(db_conn, group, question)
    if len(group) <= 1:
        ctx = insights.build_ctx(db_conn, dataset_id, manifest)
    else:
        # Группа таблиц (листы книги 1С, таблицы .db): типовые вопросы о продажах — по главной
        # таблице с выручкой и датой; вопросы про другие листы (остатки, поступления) — в SQL
        ctx, calc_id, calc_manifest, sql_tables = _select_tables(db_conn, group, question)
        if ctx is not None:
            # Вопрос про продажи: если готового расчёта не найдётся, SQL — по главной таблице и её
            # справочникам (товары, магазины): «выручка по категориям», когда категория — в справочнике
            sql_tables = with_related(group, [g for g in group if g.get("id") == calc_id])

    await status("Определяю тип запроса...")
    plan = None
    if ctx:
        try:
            extra, unresolved = resolve_entities(db_conn, ctx, question)
            plan = router_mod.route(question, ctx.roles, ctx.values, ctx.data_start, ctx.data_end,
                                    prev_question=_prev_user_question(history_rows), extra_filters=extra)
            if plan and unresolved and plan.kind in _FILTERABLE:
                # «Выручка в Караганде», а такого значения в данных нет: готовый расчёт ответил бы итогом
                # по всем данным. SQL-путь честно вернёт «подходящих записей нет»
                logger.info(f"Unresolved words {unresolved} — plan '{plan.kind}' skipped")
                plan = None
        except Exception as e:
            logger.warning(f"Router failed, falling back to SQL: {e}")

    if plan and plan.kind == "missing" and len(group) > 1:
        plan = None  # в группе нужная колонка может быть на другом листе — пусть разбирается SQL
    if plan and plan.kind == "missing":
        concepts = ", ".join(f"«{c}»" for c in plan.kwargs["concepts"])
        cols = ", ".join(ctx.columns)
        return Prepared(intent="sql", data=[], answer=(
            f"В загруженных данных нет колонки для: {concepts}, поэтому точно ответить на этот вопрос нельзя. "
            f"Есть колонки: {cols}. Если нужные данные лежат в колонке с другим названием, укажите её в вопросе."))

    if plan:
        await status("Извлекаю данные...")
        try:
            # В отдельном потоке: сводка — десяток запросов по всей таблице, и всё это время
            # остальные клиенты WebSocket и кнопка «Стоп» ждали
            rows, note = await asyncio.to_thread(_run_plan, db_conn, ctx, plan, calc_id, calc_manifest)
        except Exception as e:
            logger.exception(f"Insight '{plan.kind}' failed, falling back to SQL: {e}")
            rows, note = [], ""
        if not rows and note and plan.kind in ("churned_customers", "returns_overview"):
            # Посчитано, но строк нет: «перестали покупать 0 клиентов», возвраты только по статусу.
            # Это ответ, а не повод отдавать вопрос модели писать SQL
            return Prepared(intent=plan.intent, data=[], note=note,
                            answer=note.replace("ОТВЕТ НА ВОПРОС: ", "", 1), extra={"plan": plan.kind})
        if rows:
            value_key = plan.value_key if plan.value_key and plan.value_key in rows[0] else None
            chart = plan.chart_type
            if chart and len(rows) < 2:
                chart = None
            if value_key and plan.kind in _RANKED:
                asc = plan.kwargs.get("ascending", False) or plan.kind == "change_by_dimension"
                # «Сравни каналы: выручка, средний чек, маржа» — рейтинг по каждому показателю, иначе
                # модель переносила порядок по среднему чеку на выручку
                metrics = [value_key] + [m for m in plan.show_metrics if m != value_key and m in rows[0]]
                ranks = [ranking_note(rows, m, asc if m == value_key else False) for m in metrics]
                ranking = " ".join(r for r in ranks if r)
                note = f"{ranking} {note}".strip()
            else:
                ranking = ""
            direct = direct_answer(question, plan, rows, value_key)
            if direct:
                note = f"{direct} {note}"
            if plan.kind == "timeline" and _GROWTH_RE.search(question.lower()):
                # Полный ряд пишет код: модель на «рост месяц к месяцу» перечисляла только месяцы с ростом
                return Prepared(intent=plan.intent, data=rows, note=note, chart_type=chart,
                                chart_value_key=value_key, answer=growth_answer(rows),
                                extra={"plan": plan.kind})
            return Prepared(intent=plan.intent, data=rows, note=note, chart_type=chart,
                            chart_value_key=value_key, extended=plan.extended,
                            extra={"plan": plan.kind, "direct": direct, "ranking": ranking},
                            prompt_rows=_select_columns(rows, plan.show_metrics, value_key),
                            summary_question=concrete_question(question, plan))

    return await _sql_path(question, contextual_question, db_conn, dataset_id, manifest, ctx, status,
                           ollama_client, few_shot_history or [], tables=sql_tables)  # список или функция


def build_summary_request(prep: Prepared, contextual_question: str, db_conn, extended: bool = False) -> tuple[str, str]:
    """Промпт и системное сообщение для текстового ответа по данным."""
    rows = prep.prompt_rows or (prep.data if isinstance(prep.data, list) else [prep.data])
    table = format_rows_for_prompt(rows)
    if len(table) > 8000:
        # 40 строк по 25 колонок — 33 тыс. символов: с правилами и историей это больше контекста модели
        table = table[:8000].rsplit("\n", 1)[0] + "\n… остальные строки не показаны"
    data_block = f"Пояснение к данным: {prep.note}\n\n{table}" if prep.note else table
    currency = get_local_currency(db_conn)
    template = SUMMARY_PROMPT_EXTENDED if (extended or prep.extended) else SUMMARY_PROMPT
    question = _trim_history(contextual_question)
    if prep.summary_question:
        # В контексте диалога вопрос идёт последним — заменяем только его
        question = question[: len(question) - len(question.split("ТЕКУЩИЙ ВОПРОС: ")[-1])] \
            + prep.summary_question if "ТЕКУЩИЙ ВОПРОС: " in question else prep.summary_question
    if currency != answer_currency(prep, db_conn):
        currency = answer_currency(prep, db_conn)
    prompt = template.format(currency_rule=currency_rule(currency), question=question, data=data_block)
    system_msg = summary_system_message(currency)
    try:
        instructions = db_conn.execute("SELECT category, content FROM assistant_instructions").fetchall()
        if instructions:
            system_msg += "\n\nПОЛЬЗОВАТЕЛЬСКИЕ ИНСТРУКЦИИ:\n" + "".join(
                f"- [{cat.upper()}]: {content}\n" for cat, content in instructions)
    except Exception:
        pass
    return prompt, system_msg


def _trim_history(contextual_question: str, per_message: int = 500) -> str:
    """Укоротить реплики истории в контексте: ответ-список или отчёт о несостыковках занимал тысячи
    символов и шёл в каждый следующий промпт. Текущий вопрос не трогаем."""
    if "ТЕКУЩИЙ ВОПРОС: " not in contextual_question:
        return contextual_question
    head, current = contextual_question.rsplit("ТЕКУЩИЙ ВОПРОС: ", 1)
    lines = []
    for ln in head.split("\n"):
        lines.append(ln if len(ln) <= per_message else ln[:per_message] + " …")
    return "\n".join(lines) + "ТЕКУЩИЙ ВОПРОС: " + current


def _source_text(prep: Prepared) -> dict:
    """Строки и источник данных для проверки валюты: имена исходных колонок («сумма_руб») в строках
    готового расчёта переименованы в revenue — без них рубли в данных не находились."""
    return {"rows": prep.data, "sql": prep.sql, "columns": prep.extra.get("source_columns")}


def answer_currency(prep: Prepared, db_conn) -> str:
    """Валюта ответа: из данных, если она в них указана (рубли), иначе — из настроек."""
    from app.domain.ai.response_builder import _RUB_IN_DATA_RE

    local = get_local_currency(db_conn)
    if "руб" not in local.lower():
        try:
            text = json.dumps(_source_text(prep), ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(_source_text(prep))
        if _RUB_IN_DATA_RE.search(text):
            return "руб."
    return local


def finalize_answer(raw: str, prep: Prepared, db_conn) -> str:
    """Постобработка ответа: служебные теги, разряды чисел, валюта."""
    rows = prep.data if isinstance(prep.data, list) else []
    answer = clean_answer(raw)
    answer = fix_magnitudes(answer, rows, _note_numbers(prep.note) + _note_humanized(prep.note))
    return normalize_currency(answer, _source_text(prep), get_local_currency(db_conn))


# Не годы из дат («01.01.2025–15.11.2026»): иначе 2025 считался «известной суммой» и «2,02 млн» → «2 025»
_NOTE_NUM_RE = re.compile(r"(?<![\d.])\d[\d ]{3,}(?:[.,]\d+)?(?![.\d]*\.\d{4})(?!–\d)")


_NOTE_HUMAN_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(млрд|млн|тыс)")
_UNITS = {"млрд": 1e9, "млн": 1e6, "тыс": 1e3}


def _note_humanized(note: str) -> list[float]:
    """Числа из пояснения вида «27,00 млн»: итоги групп и рейтинги, которых нет в строках данных.
    Без них страховка разрядов «исправляла» верный итог «27,00 млн» на значение ячейки «2,70 млн»."""
    out = []
    for m in _NOTE_HUMAN_RE.finditer(note or ""):
        try:
            out.append(float(m.group(1).replace(",", ".")) * _UNITS[m.group(2)])
        except ValueError:
            pass
    return out


def _note_numbers(note: str) -> list[float]:
    out = []
    for m in _NOTE_NUM_RE.finditer(note or ""):
        try:
            v = float(m.group().replace(" ", "").replace(",", "."))
        except ValueError:
            continue
        if 1900 <= v <= 2100 and v.is_integer():
            continue  # год, а не сумма
        out.append(v)
    return out






def template_answer(prep: Prepared) -> str:
    """Ответ без модели — по пояснению и данным. Крайний случай, когда модель дважды ушла в китайский."""
    note = prep.note or ""
    direct = prep.extra.get("direct") or ""
    if direct:
        lead = direct.split(":", 1)[1].strip()
    else:
        lead = note.split(". ")[0].rstrip(".") + "." if note else ""
    parts = [lead[:1].upper() + lead[1:]] if lead else []
    ranking = prep.extra.get("ranking") or ""
    if ranking:
        parts.append("Рейтинг: " + ranking.split("посчитан точно:", 1)[-1].strip())
    else:
        rows = prep.prompt_rows or (prep.data if isinstance(prep.data, list) else [])
        lines = format_rows_for_prompt(rows, max_rows=5).splitlines()
        parts.append("\n".join("- " + ln.split(". ", 1)[-1] if ln[:1].isdigit() else ln for ln in lines))
    return "\n\n".join(p for p in parts if p).strip()


async def regenerate_if_needed(answer: str, prompt: str, system_msg: str, ollama_client,
                               prep: Optional[Prepared] = None) -> str:
    """Иероглифы в ответе (qwen2.5 иногда переходит на китайский) — одна повторная генерация."""
    if not has_cjk(answer):
        return answer
    logger.warning("CJK characters in answer — regenerating")
    strict = system_msg + "\nОтвет пиши ТОЛЬКО кириллицей на русском языке. Ни одного китайского символа."
    try:
        again = await ollama_client.generate(prompt, system=strict, num_predict=SUMMARY_NUM_PREDICT)
    except Exception as e:
        logger.error(f"Regeneration failed: {e}")
        again = answer
    if has_cjk(again) and prep is not None:
        # Вырезать иероглифы нельзя — остаются обрывки («VIP- 1,33»). Отвечаем по данным без модели
        return template_answer(prep)
    return again
