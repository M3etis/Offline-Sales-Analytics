"""Типовые вопросы: разбор, маршрутизация, готовые расчёты, формат чисел."""
import unittest
from datetime import date

import duckdb
import pandas as pd

from app.domain.ai.data_format import fix_magnitudes, format_rows_for_prompt, humanize
from app.domain.ai.question_parser import detect_filters, detect_metric, parse_periods
from app.domain.ai.response_builder import clean_answer, count_header, inject_count_header
from app.domain.ai.router import route
from app.services import insights

START, END = date(2025, 11, 15), date(2026, 11, 15)
ROLES = {"date": "date", "revenue": "revenue", "profit": "profit", "quantity": "quantity", "order_id": "order_id",
         "discount": "discount", "product": "product", "category": "category", "region": "region",
         "manager": "manager", "channel": "channel", "brand": "brand", "warehouse": "warehouse",
         "status": "status", "customer": "customer_id", "returns": "returns"}
VALUES = {"region": ["Актау", "Алматы", "Астана", "Атырау", "Шымкент"],
          "channel": ["VIP-клиенты", "Бутик", "Онлайн-магазин"],
          "category": ["Аксессуары", "Брендовые вещи", "Парфюмерия", "Ювелирные изделия"],
          "status": ["cancelled", "completed", "returned"]}


def plan(q, prev=None):
    return route(q, ROLES, VALUES, START, END, prev_question=prev)


class PeriodParsingTest(unittest.TestCase):
    def labels(self, q):
        return [(p.start, p.end) for p in parse_periods(q, START, END)]

    def test_month_without_year_is_latest_in_data(self):
        self.assertEqual(self.labels("сколько продано за сентябрь"), [(date(2026, 9, 1), date(2026, 10, 1))])
        self.assertEqual(self.labels("выручка за декабрь"), [(date(2025, 12, 1), date(2026, 1, 1))])

    def test_two_months_in_order(self):
        self.assertEqual(self.labels("сравни октябрь и сентябрь"),
                         [(date(2026, 10, 1), date(2026, 11, 1)), (date(2026, 9, 1), date(2026, 10, 1))])

    def test_quarters(self):
        q3 = [(date(2026, 7, 1), date(2026, 10, 1))]
        self.assertEqual(self.labels("выручка за третий квартал 2026"), q3)
        self.assertEqual(self.labels("выручка за 3 квартал"), q3)
        self.assertEqual(self.labels("Q3 2026"), q3)
        self.assertEqual(self.labels("в последнем квартале"), q3)

    def test_relative(self):
        self.assertEqual(self.labels("выручка за прошлый месяц"), [(date(2026, 10, 1), date(2026, 11, 1))])
        self.assertEqual(self.labels("за последние 30 дней"), [(date(2026, 10, 17), date(2026, 11, 16))])
        self.assertEqual(self.labels("за последнюю неделю"), [(date(2026, 11, 9), date(2026, 11, 16))])

    def test_partial_year_is_marked(self):
        p = parse_periods("сравни 2025 и 2026", START, END)
        self.assertEqual(len(p), 2)
        self.assertFalse(p[0].complete)
        self.assertIn("неполный", p[0].label)


class RoutingTest(unittest.TestCase):
    def test_named_months_are_compared_not_current_month(self):
        p = plan("Сравни октябрь и сентябрь")
        self.assertEqual(p.kind, "compare")
        self.assertEqual([x.start.month for x in p.kwargs["periods"]], [9, 10])

    def test_week_change_compares_weeks(self):
        p = plan("Как изменилась выручка за последнюю неделю?")
        self.assertEqual(p.kind, "compare")
        self.assertEqual(p.kwargs["periods"][1].days, 7)

    def test_rating_sorts_by_requested_metric(self):
        p = plan("Рейтинг менеджеров по количеству заказов")
        self.assertEqual((p.kind, p.kwargs["dims"], p.kwargs["metric"]), ("breakdown", ["manager"], "orders"))
        p = plan("Топ-5 категорий по прибыли")
        self.assertEqual((p.kwargs["metric"], p.kwargs["limit"]), ("profit", 5))
        self.assertEqual(plan("Топ-10 клиентов по сумме покупок").kwargs["metric"], "revenue")
        self.assertEqual(plan("Товары с самой высокой маржой").kwargs["metric"], "margin_pct")

    def test_filters_and_multi_value_dimension(self):
        p = plan("Какой товар самый популярный в Алматы?")
        self.assertEqual(p.kwargs["filters"], {"region": ["Алматы"]})
        self.assertEqual(p.kwargs["metric"], "quantity")
        p = plan("Сравни продажи менеджеров в Астане и Алматы")
        self.assertEqual(p.kwargs["dims"], ["region", "manager"])
        p = plan("Средняя скидка в бутиках и онлайн")
        self.assertEqual(p.kwargs["dims"], ["channel"])
        self.assertEqual(sorted(p.kwargs["filters"]["channel"]), ["Бутик", "Онлайн-магазин"])

    def test_special_questions(self):
        self.assertEqual(plan("Какие товары продаются в убыток?").kind, "loss_items")
        self.assertEqual(plan("Какая доля повторных клиентов?").kind, "customers_overview")
        self.assertEqual(plan("Какие клиенты перестали покупать?").kind, "churned_customers")
        self.assertEqual(plan("Прогноз выручки на следующий месяц").kind, "forecast")
        self.assertEqual(plan("ABC-анализ товаров").kind, "abc")
        self.assertEqual(plan("Какой процент возвратов?").kind, "returns_overview")
        self.assertEqual(plan("Как скидка влияет на прибыль?").kind, "discount_effect")
        self.assertTrue(plan("Какие рекомендации для увеличения продаж?").extended)
        p = plan("Какие категории падают в последнем квартале?")
        self.assertEqual((p.kind, p.kwargs["granularity"]), ("change_by_dimension", "quarter"))

    def test_counts(self):
        self.assertEqual(plan("Сколько уникальных клиентов?").kwargs["count_dims"], ["customer"])
        p = plan("Сколько отменённых заказов?")
        self.assertEqual((p.kind, p.kwargs["dims"]), ("breakdown", ["status"]))

    def test_follow_up_inherits_dimension(self):
        p = plan("а по прибыли?", prev="Какой регион приносит больше всего выручки?")
        self.assertEqual((p.kwargs["dims"], p.kwargs["metric"]), (["region"], "profit"))

    def test_pronoun_goes_to_sql(self):
        self.assertIsNone(plan("а в каких регионах он продаёт?", prev="Лучший менеджер по выручке"))

    def test_share_question_gets_pie_only_for_sums(self):
        self.assertEqual(plan("Доля выручки по каналам продаж").chart_type, "pie")
        self.assertEqual(plan("Средний чек по каналам").chart_type, "bar")


class FilterDetectionTest(unittest.TestCase):
    def test_dimension_words_are_not_values(self):
        # «клиентов» не должно находить канал «VIP-клиенты»
        self.assertEqual(detect_filters("Сколько клиентов купили", VALUES), {})

    def test_metric_order(self):
        self.assertEqual(detect_metric("средний чек по каналам"), "avg_check")
        self.assertEqual(detect_metric("рейтинг по количеству заказов"), "orders")


class NumberFormatTest(unittest.TestCase):
    def test_humanize(self):
        self.assertEqual(humanize(2819139036.07), "2,82 млрд")
        self.assertEqual(humanize(1409569.52), "1,41 млн")
        self.assertEqual(humanize(923812), "923,8 тыс.")
        self.assertEqual(humanize(2383), "2 383")

    def test_prompt_rows_are_preformatted(self):
        text = format_rows_for_prompt([{"channel": "Бутик", "avg_check": 1447371.55, "orders": 1198, "margin_pct": 69.8}])
        self.assertIn("средний чек: 1,45 млн", text)
        self.assertIn("заказов: 1 198", text)
        self.assertIn("маржинальность, %: 69,8 %", text)

    def test_fix_magnitudes(self):
        rows = [{"avg_check": 1409569.52, "revenue": 1637860401.97}]
        self.assertEqual(fix_magnitudes("Средний чек 14,09 млн тенге", rows), "Средний чек 1,41 млн тенге")
        self.assertEqual(fix_magnitudes("Выручка 16.38 млрд", rows), "Выручка 1,64 млрд")
        self.assertEqual(fix_magnitudes("Выручка 1,64 млрд", rows), "Выручка 1,64 млрд")


class AnswerCleanupTest(unittest.TestCase):
    def test_plural_header_and_single_row(self):
        self.assertEqual(count_header(1), "Найдено: 1 запись.\n")
        self.assertEqual(count_header(3), "Найдено: 3 записи.\n")
        self.assertEqual(count_header(11), "Найдено: 11 записей.\n")
        self.assertEqual(inject_count_header("Ответ", [{"x": 1}]), "Ответ")

    def test_service_tags_removed(self):
        self.assertEqual(clean_answer("Выручка 2,82 млрд тенге ([CHART_KEY:total_revenue])."), "Выручка 2,82 млрд тенге.")
        self.assertEqual(clean_answer("Клиентов 845.\n[CHART_KEY:count(DISTINCT customer_id)]"), "Клиентов 845.")
        self.assertEqual(clean_answer("Топ-10 готов\n\n[CHART"), "Топ-10 готов")
        self.assertEqual(clean_answer("CLARIFY: Уточните период"), "Уточните период")


def _sales_conn():
    conn = duckdb.connect()
    conn.execute("CREATE TABLE datasets (id VARCHAR, name VARCHAR, rows INTEGER, columns TEXT, table_name VARCHAR)")
    conn.execute("INSERT INTO datasets VALUES ('d1', 't', 0, '[]', 'dataset_aa')")
    # Две строки одного заказа o1: средний чек на заказ = 300, а не AVG по строкам = 150
    conn.execute("""
        CREATE TABLE dataset_aa AS SELECT * FROM (VALUES
            (TIMESTAMP '2026-09-05', 'o1', 'Алматы', 'Бутик',  100.0, 60.0, 1, 'c1'),
            (TIMESTAMP '2026-09-05', 'o1', 'Алматы', 'Бутик',  200.0, 50.0, 2, 'c1'),
            (TIMESTAMP '2026-10-10', 'o2', 'Астана', 'Онлайн', 400.0, 40.0, 1, 'c2'),
            (TIMESTAMP '2026-10-31', 'o3', 'Алматы', 'Онлайн', 100.0, 90.0, 5, 'c1')
        ) t(date, order_id, region, channel, revenue, profit, quantity, customer_id)
    """)
    return conn


class InsightsQueriesTest(unittest.TestCase):
    def setUp(self):
        self.conn = _sales_conn()
        self.ctx = insights.build_ctx(self.conn, "d1", None)

    def test_ctx(self):
        self.assertEqual(self.ctx.roles["customer"], "customer_id")
        self.assertEqual((self.ctx.data_start, self.ctx.data_end), (date(2026, 9, 5), date(2026, 10, 31)))

    def test_avg_check_is_per_order(self):
        rows, note = insights.totals(self.conn, self.ctx)
        self.assertEqual(rows[0]["orders"], 3)
        self.assertAlmostEqual(rows[0]["avg_check"], 266.67, places=2)
        self.assertEqual(rows[0]["customers"], 2)
        self.assertIn("Средний чек = выручка / число заказов", note)

    def test_breakdown_sorted_by_metric_with_filter(self):
        rows, _ = insights.breakdown(self.conn, self.ctx, ["region"], metric="profit")
        self.assertEqual([r["region"] for r in rows], ["Алматы", "Астана"])
        self.assertEqual(rows[0]["profit"], 200.0)
        rows, _ = insights.breakdown(self.conn, self.ctx, ["channel"], metric="quantity", filters={"region": ["Алматы"]})
        self.assertEqual(rows[0]["channel"], "Онлайн")

    def test_compare_named_months(self):
        periods = parse_periods("сравни сентябрь и октябрь", self.ctx.data_start, self.ctx.data_end)
        rows, note = insights.compare(self.conn, self.ctx, periods)
        self.assertEqual([r["revenue"] for r in rows], [300.0, 500.0])
        self.assertIn("выручка: +66,7 %", note)

    def test_customers_overview(self):
        rows, _ = insights.customers_overview(self.conn, self.ctx)
        self.assertEqual((rows[0]["customers"], rows[0]["repeat_customers"]), (2, 1))


class DateStorageTest(unittest.TestCase):
    def test_ingest_keeps_datetime(self):
        from app.services.ingest import _clean_dates_in_df
        df = pd.DataFrame({"date": ["2026-09-08", "2026-09-09"], "spend": ["a", "b"], "sale_date": ["05.11.2025", "15.11.2025"]})
        out = _clean_dates_in_df(df, [])
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(out["date"]))
        self.assertEqual(out["sale_date"].iloc[0].month, 11)  # день первым: 5 ноября, а не 11 мая
        self.assertEqual(list(out["spend"]), ["a", "b"])  # «spend» по имени похож на дату, но не трогаем

    def test_varchar_dates_migrated(self):
        from app.db.date_columns import migrate_date_columns
        conn = duckdb.connect()
        conn.execute("CREATE TABLE datasets (id VARCHAR, name VARCHAR, rows INTEGER, columns TEXT, table_name VARCHAR)")
        conn.execute("INSERT INTO datasets VALUES ('d1', 't', 0, '[]', 'dataset_aa')")
        conn.execute("CREATE TABLE dataset_aa AS SELECT * FROM (VALUES ('2026-09-08T00:00:00', 1), ('2026-09-09T00:00:00', 2)) v(date, x)")
        self.assertEqual(migrate_date_columns(conn), ["dataset_aa.date"])
        typ = conn.execute("SELECT data_type FROM information_schema.columns WHERE table_name='dataset_aa' AND column_name='date'").fetchone()[0]
        self.assertEqual(typ, "TIMESTAMP")
        self.assertEqual(migrate_date_columns(conn), [])


if __name__ == "__main__":
    unittest.main()


class PipelineHelpersTest(unittest.TestCase):
    ROWS = [{"manager": "Ким А.", "revenue": 700.6e6}, {"manager": "Омаров Т.", "revenue": 713.4e6},
            {"manager": "Сатпаева А.", "revenue": 699.7e6}, {"manager": "Ахметова Д.", "revenue": 705.3e6}]

    def test_ranking_note_sorts_itself(self):
        from app.services.pipeline import ranking_note
        note = ranking_note(self.ROWS, "revenue")
        self.assertIn("1-е место — Омаров Т. (713,40 млн)", note)
        self.assertIn("последнее место — Сатпаева А.", note)

    def test_direct_answer_for_superlative(self):
        from app.services.pipeline import direct_answer
        p = plan("Лучший менеджер по выручке")
        self.assertIn("Омаров Т. — 713,40 млн", direct_answer("Лучший менеджер по выручке", p, self.ROWS, "revenue"))

    def test_relative_period_made_concrete(self):
        from app.services.pipeline import concrete_question
        self.assertEqual(concrete_question("Выручка за прошлый месяц", plan("Выручка за прошлый месяц")),
                         "Выручка за октябрь 2026")
        q = "Сколько заказов было за последние 30 дней?"
        self.assertEqual(concrete_question(q, plan(q)), "Сколько заказов было за период 17.10.2026–15.11.2026?")

    def test_template_answer_is_readable(self):
        from app.services.pipeline import Prepared, template_answer
        prep = Prepared(data=self.ROWS, note="ОТВЕТ НА ВОПРОС: … Прочее.", extra={
            "direct": "ОТВЕТ НА ВОПРОС: Омаров Т. — 713,40 млн (показатель «выручка»).",
            "ranking": "Рейтинг по показателю «выручка», посчитан точно: 1-е место — Омаров Т. (713,40 млн)."})
        text = template_answer(prep)
        self.assertTrue(text.startswith("Омаров Т. — 713,40 млн"))
        self.assertNotIn("ОТВЕТ НА ВОПРОС", text)
        self.assertNotIn("посчитан точно", text)


class ColumnRolesTest(unittest.TestCase):
    """Роли колонок распознаются в любом наборе данных, а не только с английскими именами из демо."""

    def roles(self, cols, stats=None):
        from app.domain.ai.column_roles import infer_roles
        return infer_roles(cols, stats)

    def test_russian_headers(self):
        r = self.roles([("дата_продажи", "TIMESTAMP"), ("номер_заказа", "VARCHAR"), ("товар", "VARCHAR"),
                        ("категория", "VARCHAR"), ("город", "VARCHAR"), ("менеджер", "VARCHAR"), ("канал", "VARCHAR"),
                        ("количество", "BIGINT"), ("цена", "BIGINT"), ("сумма", "BIGINT"),
                        ("себестоимость", "DOUBLE"), ("прибыль", "DOUBLE"), ("клиент", "VARCHAR")])
        self.assertEqual((r["date"], r["revenue"], r["region"], r["manager"], r["order_id"], r["customer"]),
                         ("дата_продажи", "сумма", "город", "менеджер", "номер_заказа", "клиент"))

    def test_alternative_english(self):
        r = self.roles([("order_date", "TIMESTAMP_NS"), ("invoice_no", "VARCHAR"), ("item_name", "VARCHAR"),
                        ("product_category", "VARCHAR"), ("store_city", "VARCHAR"), ("salesperson", "VARCHAR"),
                        ("sales_channel", "VARCHAR"), ("qty", "BIGINT"), ("unit_price", "BIGINT"),
                        ("total_amount", "BIGINT"), ("cogs", "DOUBLE"), ("gross_profit", "DOUBLE"), ("client_id", "VARCHAR")])
        self.assertEqual(r["revenue"], "total_amount")
        self.assertEqual(r["region"], "store_city")  # последнее слово главное: город, а не магазин
        self.assertEqual(r["channel"], "sales_channel")  # текстовая — канал, а не выручка
        self.assertEqual((r["manager"], r["order_id"], r["category"]), ("salesperson", "invoice_no", "product_category"))

    def test_demo_schema_unchanged(self):
        r = self.roles([(c, t) for c, t in [("date", "TIMESTAMP"), ("order_id", "VARCHAR"), ("revenue", "DOUBLE"),
                                            ("price", "DOUBLE"), ("profit", "DOUBLE"), ("returns", "BIGINT"),
                                            ("warehouse", "VARCHAR"), ("customer_id", "VARCHAR")]])
        self.assertEqual((r["revenue"], r["returns"], r["warehouse"], r["customer"]),
                         ("revenue", "returns", "warehouse", "customer_id"))

    def test_revenue_from_price_and_quantity(self):
        r = self.roles([("дата", "DATE"), ("товар", "VARCHAR"), ("цена", "DOUBLE"), ("кол_во", "BIGINT")])
        self.assertEqual(r["revenue"], '("цена" * "кол_во")')

    def test_text_numbers_allowed(self):
        self.assertEqual(self.roles([("revenue", "DOUBLE"), ("discount", "VARCHAR")])["discount"], "discount")

    def test_low_uniqueness_is_not_order_id(self):
        r = self.roles([("order_status", "VARCHAR"), ("order_id", "VARCHAR"), ("revenue", "DOUBLE")],
                       {"order_status": {"distinct": 3, "rows": 1000}, "order_id": {"distinct": 1000, "rows": 1000}})
        self.assertEqual(r["order_id"], "order_id")
        self.assertEqual(r["status"], "order_status")


class GenericRoutingTest(unittest.TestCase):
    MIN_ROLES = {"date": "date", "revenue": "sales_amount", "product": "product"}

    def test_missing_dimension_or_metric_is_reported(self):
        # Без нужной колонки SQL-путь выдумывал («297 клиентов» без колонки клиента) — отвечаем честно
        def missing(q):
            p = route(q, self.MIN_ROLES, {}, START, END)
            self.assertEqual(p.kind, "missing", q)
            return p.kwargs["concepts"]
        self.assertEqual(missing("Топ-5 категорий по прибыли"), ["категория", "прибыль"])
        self.assertEqual(missing("Сколько уникальных клиентов?"), ["клиент"])
        self.assertEqual(missing("Сколько штук продано за сентябрь?"), ["количество"])
        self.assertEqual(missing("Какой процент возвратов?"), ["возвраты"])
        self.assertEqual(route("Топ-10 товаров по выручке", self.MIN_ROLES, {}, START, END).kind, "breakdown")

    def test_column_named_in_question(self):
        values = {"тип_оплаты": ["Карта", "Наличные"]}
        p = route("Выручка по типу оплаты", self.MIN_ROLES, values, START, END)
        self.assertEqual(p.kwargs["dims"], ["тип_оплаты"])

    def test_partial_column_name_does_not_match(self):
        from app.domain.ai.question_parser import column_mentions
        self.assertEqual(column_mentions("Продажи по складам", ["канал_продаж"]), [])
        self.assertEqual(column_mentions("Доля выручки по каналам продаж", ["канал_продаж"]), ["канал_продаж"])


class DateByValuesTest(unittest.TestCase):
    def test_dates_found_by_values(self):
        from app.services.ingest import _clean_dates_in_df
        df = pd.DataFrame({"период": ["01.03.2026", "15.03.2026"], "год": ["2025", "2026"], "x": [1, 2]})
        out = _clean_dates_in_df(df, [])
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(out["период"]))
        self.assertEqual(out["период"].iloc[1].day, 15)
        self.assertEqual(list(out["год"]), ["2025", "2026"])  # год — не дата

    def test_migration_converts_ddmmyyyy(self):
        from app.db.date_columns import migrate_date_columns
        conn = duckdb.connect()
        conn.execute("CREATE TABLE datasets (id VARCHAR, name VARCHAR, rows INTEGER, columns TEXT, table_name VARCHAR)")
        conn.execute("INSERT INTO datasets VALUES ('d1', 't', 0, '[]', 'dataset_bb')")
        conn.execute("CREATE TABLE dataset_bb AS SELECT * FROM (VALUES ('15.11.2025', '2025'), ('01.12.2025', '2026')) v(дата_продажи, год)")
        self.assertEqual(migrate_date_columns(conn), ["dataset_bb.дата_продажи"])
        self.assertEqual(str(conn.execute("SELECT MAX(дата_продажи) FROM dataset_bb").fetchone()[0]), "2025-12-01 00:00:00")
