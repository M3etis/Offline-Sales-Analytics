"""Находки ревью ветки assistant-accuracy-any-dataset: каждая — тестом, чтобы не вернулась."""
import asyncio
import logging
import tempfile
import unittest
import warnings
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from app.db.date_columns import migrate_date_columns
from app.domain.ai.column_roles import infer_roles, sql_ident
from app.domain.ai.data_format import fix_magnitudes, format_value, humanize, kind_of
from app.domain.ai.question_parser import detect_filters, parse_periods
from app.domain.ai.router import route
from app.domain.ai.sql_utils import fix_filter_values, fix_unknown_column, remap_table_names
from app.services import analytics, ingest, insights, pipeline
from tests.test_insights import END, ROLES, START, VALUES

logging.disable(logging.CRITICAL)
warnings.simplefilter("ignore")


def plan(q, prev=None, extra=None, roles=ROLES, values=VALUES, s=START, e=END):
    return route(q, roles, values, s, e, prev_question=prev, extra_filters=extra)


class FakeLLM:
    def __init__(self):
        self.sql_calls = 0

    async def generate_sql(self, *a, **k):
        self.sql_calls += 1
        return "SELECT 1 AS x"

    async def fix_sql(self, *a, **k):
        return None


def _base_conn():
    conn = duckdb.connect()
    conn.execute("CREATE TABLE assistant_instructions (category VARCHAR, content VARCHAR)")
    conn.execute("CREATE TABLE system_settings (setting_key VARCHAR, setting_value VARCHAR, updated_at TIMESTAMP)")
    insights._VALUES_CACHE.clear()
    return conn


class RoutingFixesTest(unittest.TestCase):
    def test_short_question_is_not_followup(self):
        self.assertEqual(plan("Общая выручка?", prev="Какие товары убыточные?").kind, "totals")
        self.assertEqual(plan("Средний чек?", prev="Сколько было возвратов в Алматы?").kind, "totals")
        p = plan("а по прибыли?", prev="Какой регион приносит больше всего выручки?")
        self.assertEqual((p.kind, p.kwargs["dims"], p.kwargs["metric"]), ("breakdown", ["region"], "profit"))

    def test_count_of_dimension_needs_count_words(self):
        self.assertNotEqual(plan("Сколько заработал менеджер Омаров?").kwargs.get("count_dims"), ["manager"])
        self.assertIsNone(plan("Сколько продано товаров?").kwargs.get("count_dims"))
        self.assertEqual(plan("Сколько уникальных клиентов?").kwargs.get("count_dims"), ["customer"])

    def test_returns_and_retention(self):
        self.assertEqual(plan("Какая возвращаемость клиентов?").kind, "customers_overview")
        self.assertEqual(plan("Сколько клиентов не возвращаются?").kind, "churned_customers")
        self.assertEqual(plan("Какие товары чаще всего возвращают?").kind, "returns_overview")
        p = plan("Возвраты по регионам за октябрь")
        self.assertEqual((p.kwargs["dim"], p.kwargs["period"].start), ("region", date(2026, 10, 1)))
        self.assertEqual(plan("Процент возвратов в Алматы").kwargs["filters"], {"region": ["Алматы"]})

    def test_conditions_go_to_sql(self):
        for q in ("Сколько заказов на сумму больше 50 000?", "Сколько заказов без скидки?", "Какая максимальная скидка?",
                  "Динамика продаж по категориям"):
            with self.subTest(q=q):
                self.assertIsNone(plan(q))

    def test_this_month_is_a_period(self):
        self.assertEqual(plan("Выручка за этот месяц").kwargs["period"].start, date(2026, 11, 1))

    def test_stems_do_not_overmatch(self):
        self.assertEqual(plan("Просто покажи выручку").kind, "totals")
        p = plan("Топ-3 наименований по выручке")
        self.assertEqual((p.kind, p.kwargs["ascending"]), ("breakdown", False))
        self.assertEqual(plan("Какие категории попадают в топ-3 по выручке?").kind, "breakdown")

    def test_modifier_dimension_dropped(self):
        self.assertEqual(plan("Продажи товаров по регионам").kwargs["dims"], ["region"])
        self.assertEqual(plan("Выручка по категориям товаров").kwargs["dims"], ["category"])

    def test_previous_period_marked_incomplete(self):
        p = plan("Как изменилась выручка в декабре 2025?")
        self.assertFalse(p.kwargs["periods"][0].complete)

    def test_no_date_column_refuses_periods(self):
        p = plan("Выручка за 2021 год", roles={"revenue": "amount", "region": "city"}, values={}, s=None, e=None)
        self.assertEqual((p.kind, p.kwargs["concepts"]), ("missing", ["дата"]))

    def test_sku_counts_as_product(self):
        p = plan("Топ 10 товаров по выручке", roles={"date": "дата", "sku": "артикул", "revenue": "сумма"}, values={})
        self.assertEqual((p.kind, p.kwargs["dims"]), ("breakdown", ["product"]))


class ParsingFixesTest(unittest.TestCase):
    def periods(self, q):
        return [(p.start, p.end) for p in parse_periods(q, START, END)]

    def test_months_are_whole_words(self):
        self.assertEqual(self.periods("выручка менеджера мартынова"), [])
        self.assertEqual(self.periods("продажи маек"), [])
        self.assertEqual(self.periods("выручка в мае"), [(date(2026, 5, 1), date(2026, 6, 1))])

    def test_quarters(self):
        self.assertEqual(self.periods("сравни 1 и 3 квартал"),
                         [(date(2026, 1, 1), date(2026, 4, 1)), (date(2026, 7, 1), date(2026, 10, 1))])
        self.assertEqual(self.periods("за последние 3 квартала"), [(date(2026, 1, 1), date(2026, 10, 1))])

    def test_years_dates_ranges(self):
        self.assertEqual(self.periods("заказов на сумму более 2000"), [])
        self.assertEqual(self.periods("продажи за 15.11.2026"), [(date(2026, 11, 15), date(2026, 11, 16))])
        self.assertEqual(self.periods("выручка с 01.01.2026 по 31.03.2026"), [(date(2026, 1, 1), date(2026, 4, 1))])
        self.assertEqual(self.periods("выручка с января по март"), [(date(2026, 1, 1), date(2026, 4, 1))])
        self.assertEqual(self.periods("продажи за 2024-2025 годы"), [(date(2024, 1, 1), date(2026, 1, 1))])

    def test_values_matched_by_best_words(self):
        v = {"store": ["ТРЦ Мега Алматы", "ТРЦ Мега Астана", "ТРЦ Достык Плаза Алматы"],
             "mgr": ["Иванов Иван", "Иванова Анна", "Петров Иван"], "seg": ["Средний", "Премиум"],
             "city": ["Уфа", "Пермь", "Тула"]}
        self.assertEqual(detect_filters("выручка в мега алматы", v), {"store": ["ТРЦ Мега Алматы"]})
        self.assertEqual(detect_filters("сколько продал иванов иван", v), {"mgr": ["Иванов Иван"]})
        self.assertEqual(detect_filters("какой средний чек", v), {})
        for q, city in (("в уфе", "Уфа"), ("в перми", "Пермь"), ("в туле", "Тула")):
            self.assertEqual(detect_filters(q, v), {"city": [city]})


class CalculationFixesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = _base_conn()
        cls.conn.execute("CREATE TABLE sales AS SELECT * FROM read_csv_auto('data/demo/demo_luxury.csv')")
        cls.conn.execute("ALTER TABLE sales ALTER date TYPE TIMESTAMP")
        cls.ctx = insights.build_ctx(cls.conn, None, None)

    def run_q(self, q):
        p = route(q, self.ctx.roles, self.ctx.values, self.ctx.data_start, self.ctx.data_end)
        return getattr(insights, p.kind)(self.conn, self.ctx, **p.kwargs)

    def test_timeline_marks_month_cut_by_period(self):
        rows, _ = self.run_q("Динамика выручки по месяцам за последние 3 месяца")
        first = rows[0]
        self.assertIn("неполный", first["period"])
        self.assertIsNone(rows[1]["revenue_change_pct"])

    def test_compare_with_partial_previous_month_warns(self):
        _, note = self.run_q("Как изменилась выручка в декабре 2025?")
        self.assertIn("ВНИМАНИЕ", note)
        self.assertIn("среднедневную", note)

    def test_group_totals_are_full(self):
        _, note = insights.breakdown(self.conn, self.ctx, ["region", "product"], limit=5)
        ref = self.conn.execute("SELECT region, SUM(revenue) FROM sales GROUP BY 1 ORDER BY 2 DESC LIMIT 1").fetchone()
        self.assertIn(f"{ref[0]}: итого {humanize(ref[1])}", note)

    def test_abc_shares_from_revenue(self):
        _, note = insights.abc(self.conn, self.ctx, "product")
        self.assertIn("A — позиции, дающие первые 80 % выручки", note)
        self.assertIn("C — остальные 5 % (5 шт., 3.0 %)", note)

    def test_vanished_group_is_full_drop(self):
        conn = _base_conn()
        conn.execute("CREATE TABLE sales AS SELECT * FROM read_csv_auto('data/demo/demo_luxury.csv')")
        conn.execute("ALTER TABLE sales ALTER date TYPE TIMESTAMP")
        conn.execute("DELETE FROM sales WHERE region = 'Актау' AND date >= '2026-10-01' AND date < '2026-11-01'")
        ctx = insights.build_ctx(conn, None, None)
        rows, note = insights.change_by_dimension(conn, ctx, "region")
        aktau = next(r for r in rows if r["region"] == "Актау")
        self.assertEqual(aktau["change_pct"], -100.0)
        self.assertIn("Актау", note.split("Снижение у:")[1])

    def test_returns_respect_period(self):
        _, note = self.run_q("Возвраты по регионам за октябрь")
        self.assertIn("Возвращено единиц: 2 из 176", note)

    def test_churn_not_computed_inside_period(self):
        rows, _ = self.run_q("Повторные клиенты за 1 квартал 2026")
        self.assertNotIn("churned_customers", rows[0])

    def test_numeric_customer_dimension(self):
        conn = _base_conn()
        conn.execute("CREATE TABLE sales (date TIMESTAMP, customer_id INTEGER, revenue DOUBLE)")
        conn.execute("INSERT INTO sales VALUES ('2026-01-05', 1, 10), ('2026-01-06', 2, 20), ('2026-01-07', NULL, 5)")
        ctx = insights.build_ctx(conn, None, None)
        rows, _ = insights.breakdown(conn, ctx, ["customer"])
        self.assertEqual(len(rows), 3)


class PipelineFixesTest(unittest.TestCase):
    def test_unknown_city_is_looked_up_not_dropped(self):
        conn = _base_conn()
        cities = [f"Город{i}" for i in range(79)] + ["Караганда"]
        conn.execute("CREATE TABLE sales (дата TIMESTAMP, город VARCHAR, выручка DOUBLE)")
        conn.executemany("INSERT INTO sales VALUES ('2026-01-05', ?, ?)", [(c, 100.0 if c == "Караганда" else 1.0) for c in cities])
        prep = asyncio.run(pipeline.prepare("Какая выручка в Караганде?", "Какая выручка в Караганде?", conn, None,
                                            ollama_client=FakeLLM()))
        self.assertEqual(prep.data[0]["revenue"], 100.0)
        llm = FakeLLM()
        asyncio.run(pipeline.prepare("Какая выручка в Урюпинске?", "Какая выручка в Урюпинске?", conn, None, ollama_client=llm))
        self.assertEqual(llm.sql_calls, 1)  # такого города нет — не итог по всем, а SQL

    def test_single_value_keeps_sign(self):
        text = pipeline.single_value_answer("Какая прибыль у товаров со скидкой больше 20%?", {"profit": -1250000.0}, "тенге")
        self.assertIn("-1,25 млн", text)
        self.assertNotIn("знаком минус", text)
        self.assertEqual(pipeline.single_value_answer("Сколько строк в таблице?", {"n": 25000}, "тенге"),
                         "Строк в таблице: 25 000.")

    def test_to_char_format_translated(self):
        self.assertEqual(pipeline.deterministic_sql_fixes("SELECT TO_CHAR(дата, 'YYYY-MM') AS m FROM t"),
                         "SELECT strftime(дата, '%Y-%m') AS m FROM t")

    def test_sales_sheet_chosen_by_meaning(self):
        conn = _base_conn()
        conn.execute("CREATE TABLE datasets (id VARCHAR, name VARCHAR, table_name VARCHAR, columns VARCHAR, "
                     "relationships VARCHAR, source_group VARCHAR)")
        conn.execute("CREATE TABLE dataset_manifest (dataset_id VARCHAR, manifest_json VARCHAR)")
        conn.execute("CREATE TABLE dataset_aa (дата TIMESTAMP, товар VARCHAR, клиент VARCHAR, сумма DOUBLE)")
        conn.execute("CREATE TABLE dataset_bb (дата TIMESTAMP, поставщик VARCHAR, сумма DOUBLE)")
        conn.executemany("INSERT INTO dataset_aa VALUES ('2026-01-05', 't', 'c', ?)", [(1000.0,)] * 3)
        conn.executemany("INSERT INTO dataset_bb VALUES ('2026-01-05', 'p', ?)", [(50000.0,)] * 9)
        conn.execute("INSERT INTO datasets VALUES ('s', 'Продажи', 'dataset_aa', '[]', '[]', 'g'), "
                     "('p', 'Поступления', 'dataset_bb', '[]', '[]', 'g')")
        prep = asyncio.run(pipeline.prepare("Какая общая выручка?", "Какая общая выручка?", conn, "p", ollama_client=FakeLLM()))
        self.assertEqual(prep.data[0]["revenue"], 3000.0)

    def test_rubles_in_source_columns_kept(self):
        conn = _base_conn()
        conn.execute("CREATE TABLE sales (дата TIMESTAMP, город VARCHAR, сумма_руб DOUBLE)")
        conn.execute("INSERT INTO sales VALUES ('2026-01-05', 'Москва', 1500000), ('2026-02-05', 'Казань', 3000000)")
        prep = asyncio.run(pipeline.prepare("Выручка по городам", "Выручка по городам", conn, None, ollama_client=FakeLLM()))
        self.assertIn("рублей", pipeline.finalize_answer("Москва — 1,50 млн рублей.", prep, conn))


class SqlRepairFixesTest(unittest.TestCase):
    err = staticmethod(lambda c: f'Binder Error: Referenced column "{c}" not found in FROM clause!')

    def test_no_unrelated_substitution(self):
        self.assertIsNone(fix_unknown_column("SELECT SUM(profit) FROM t", self.err("profit"), ["п_п", "товар"]))
        self.assertIsNone(fix_unknown_column("SELECT SUM(amount) FROM t", self.err("amount"), ["count", "товар"]))
        self.assertEqual(fix_unknown_column("SELECT brand FROM t", self.err("brand"), ["бренд"]), "SELECT бренд FROM t")

    def test_literals_untouched(self):
        fixed = fix_unknown_column("SELECT магазин FROM t WHERE магазин = 'Магазин №1'", self.err("магазин"),
                                   ["магазин_название"])
        self.assertEqual(fixed, "SELECT магазин_название FROM t WHERE магазин_название = 'Магазин №1'")

    def test_remap_quoted_and_comma(self):
        nm = {"Продажи": "dataset_a", "Возвраты": "dataset_b", "Остатки на складе": "dataset_c"}
        self.assertEqual(remap_table_names("SELECT * FROM Продажи t1, Возвраты t2", nm),
                         "SELECT * FROM dataset_a t1, dataset_b t2")
        self.assertEqual(remap_table_names('SELECT a FROM "Остатки на складе" t1', nm), "SELECT a FROM dataset_c t1")

    def test_filter_values(self):
        conn = duckdb.connect()
        conn.execute("CREATE TABLE t (артикул VARCHAR, город VARCHAR)")
        conn.execute("INSERT INTO t VALUES ('A-1', 'Алматы'), ('A-2', 'Астана')")
        self.assertEqual(fix_filter_values("SELECT * FROM t WHERE артикул = 'A-11'", conn, "t", {"profiles": {}}),
                         "SELECT * FROM t WHERE артикул = 'A-11'")
        self.assertEqual(fix_filter_values("SELECT * FROM t WHERE город = 'алматы'", conn, "t", {"profiles": {}}),
                         "SELECT * FROM t WHERE город = 'Алматы'")


class FormatFixesTest(unittest.TestCase):
    def test_correct_numbers_not_rewritten(self):
        data = [{"r": "a", "revenue": 16_500_000.0}, {"r": "b", "revenue": 15_000_000.0}, {"r": "c", "revenue": 1_409_569.0}]
        self.assertEqual(fix_magnitudes("Разница — 1,5 млн.", data), "Разница — 1,5 млн.")
        self.assertEqual(fix_magnitudes("Чек 14,09 млн.", data), "Чек 1,41 млн.")

    def test_ids_and_rounding(self):
        self.assertEqual(format_value(100245, kind_of("customer_id", [100245])), "100245")
        self.assertEqual(format_value(1.42, kind_of("avg_quantity", [1.42])), "1,42")
        self.assertEqual(humanize(999_960), "1,00 млн")
        self.assertEqual(format_value(float("inf"), "money"), "нет данных")


class RolesAndDashboardFixesTest(unittest.TestCase):
    def test_roles(self):
        t, d, v = "TIMESTAMP", "DOUBLE", "VARCHAR"
        r = infer_roles([("order_date", t), ("country", v), ("category", v), ("segment", v), ("sales", d)])
        self.assertNotEqual(r.get("quantity"), "country")
        self.assertEqual(r["category"], "category")
        r = infer_roles([("дата", t), ("номер_чека", v), ("сумма", d)], {"номер_чека": {"distinct": 200, "rows": 1000}})
        self.assertEqual(r["order_id"], "номер_чека")
        r = infer_roles([("date", t), ("quantity", d), ("unit_cost", d), ("total", d)])
        self.assertEqual(r["profit"], '("total" - "unit_cost" * "quantity")')
        self.assertNotIn("revenue", infer_roles([("sale_id", "BIGINT"), ("sale_date", t), ("product", v)]))

    def test_reserved_word_columns_on_dashboard(self):
        self.assertEqual(sql_ident("order"), '"order"')
        conn = duckdb.connect()
        conn.execute('CREATE TABLE sales ("date" TIMESTAMP, "order" VARCHAR, "group" VARCHAR, total DOUBLE)')
        conn.execute("INSERT INTO sales VALUES ('2026-01-05', 'o1', 'A', 100), ('2026-02-05', 'o2', 'B', 200)")
        self.assertEqual(analytics.get_kpis(conn)["total_revenue"], 300.0)
        self.assertEqual(len(analytics.sales_by_category(conn)), 2)


class IngestFixesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_duplicate_headers(self):
        rows = [["Товар", "Количество", "Сумма", "Количество", "Сумма"]] + [[f"Т{i}", i, i * 10.5, i, i * 9.0] for i in range(1, 6)]
        pd.DataFrame(rows).to_excel(self.dir / "dup.xlsx", header=False, index=False)
        df = next(iter(ingest.read_excel_tables(str(self.dir / "dup.xlsx")).values()))
        self.assertEqual(list(df.columns), ["Товар", "Количество", "Сумма", "Количество.1", "Сумма.1"])

    def test_total_rows_only_real_totals(self):
        df = pd.DataFrame({"Товар": ["Total Quartz 9000", "Всего понемногу", "Итого", "Итого по складу:", "Шарф"],
                           "Сумма": [1, 2, 3, 4, 5]})
        self.assertEqual(ingest.clean_table(df)["Товар"].tolist(), ["Total Quartz 9000", "Всего понемногу", "Шарф"])

    def test_dates(self):
        mixed = pd.DataFrame({"Дата": ["2026-07-15 00:00:00"] * 9 + ["15.07.2026"], "x": range(10)})
        self.assertEqual(int(ingest._clean_dates_in_df(mixed, [])["Дата"].isna().sum()), 0)
        monthly = pd.DataFrame({"Дата": ["01/02/2025", "01/03/2025"], "x": [1, 2]})
        self.assertEqual(ingest._clean_dates_in_df(monthly, [])["Дата"].dt.month.tolist(), [2, 3])
        times = pd.DataFrame({"Время": ["10:30", "11:45"], "x": [1, 2]})
        self.assertEqual(ingest._clean_dates_in_df(times, [])["Время"].tolist(), ["10:30", "11:45"])

    def test_csv_semicolon_with_comma_in_header(self):
        (self.dir / "s.csv").write_text("Дата;Скидка, %;Сумма\n2026-01-01;5;100\n", encoding="utf-8")
        self.assertEqual(len(ingest.read_table_file(str(self.dir / "s.csv"))[0].columns), 3)

    def test_header_below_parameter_block(self):
        raw = pd.DataFrame([["Период:", "июль", "Склад:", "Основной", None],
                            ["Дата", "Товар", "Количество", "Цена", "Сумма"], ["01.07.2026", "A", 1, 10, 10],
                            ["02.07.2026", "B", 2, 10, 20]])
        self.assertEqual(ingest.find_header_row(raw), 1)

    def test_date_migration_is_strict(self):
        conn = duckdb.connect()
        conn.execute("CREATE TABLE datasets (id VARCHAR, table_name VARCHAR)")
        conn.execute("INSERT INTO datasets VALUES ('d', 'dataset_x')")
        conn.execute("CREATE TABLE dataset_x (короткий VARCHAR, срок VARCHAR)")
        conn.executemany("INSERT INTO dataset_x VALUES (?, ?)",
                         [("05.01.24", "по факту" if i == 0 else "01.02.2026") for i in range(50)])
        self.assertEqual(migrate_date_columns(conn), [])


if __name__ == "__main__":
    unittest.main()
