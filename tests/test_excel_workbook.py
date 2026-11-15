"""Книги Excel из учётных систем: несколько листов, служебные строки, «Итого», числа текстом."""
import os
import tempfile
import unittest

import duckdb
import pandas as pd

from app.services.ingest import clean_table, detect_shared_key_relationships, find_header_row, read_excel_tables


def _write_book(path):
    title = [["ТОО «Ромашка»", None, None, None], ["Отчёт: Продажи", None, None, None],
             ["Период: 01.07.2026 - 31.07.2026", None, None, None], [None, None, None, None]]
    sales = title + [["Дата", "Артикул", "Количество", "Сумма, KZT"],
                     ["01.07.2026", "A-1", 1, "1 000,00"], ["02.07.2026", "A-2", 2, "2 500,50"],
                     ["03.07.2026", "A-1", 1, "—"], ["Итого", None, 4, "3 500,50"]]
    goods = title + [["Артикул", "Наименование", "Категория", "БИН"],
                     ["A-1", "Куртка", "Одежда", "090340098765"], ["A-2", "Туфли", "Обувь", "180440012345"]]
    info = [["ВЫГРУЗКА ИЗ 1С", None], [None, None], ["Организация", "ТОО «Ромашка»"], ["Период", "июль 2026"]]
    with pd.ExcelWriter(path) as w:
        pd.DataFrame(info).to_excel(w, sheet_name="Справка", header=False, index=False)
        pd.DataFrame(sales).to_excel(w, sheet_name="Продажи", header=False, index=False)
        pd.DataFrame(goods).to_excel(w, sheet_name="Номенклатура", header=False, index=False)


class WorkbookReadTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        _write_book(self.path)

    def tearDown(self):
        os.remove(self.path)

    def test_all_table_sheets_with_detected_header(self):
        tables = read_excel_tables(self.path)
        self.assertEqual(sorted(tables), ["Номенклатура", "Продажи"])  # «Справка» — не таблица
        self.assertEqual(list(tables["Продажи"].columns), ["Дата", "Артикул", "Количество", "Сумма, KZT"])

    def test_totals_dropped_and_text_numbers_parsed(self):
        sales = read_excel_tables(self.path)["Продажи"]
        self.assertEqual(len(sales), 3)  # строка «Итого» удвоила бы суммы
        self.assertAlmostEqual(sales["Сумма, KZT"].sum(), 3500.5)
        self.assertTrue(pd.isna(sales["Сумма, KZT"].iloc[2]))  # «—» — пусто

    def test_identifiers_keep_leading_zeros(self):
        goods = read_excel_tables(self.path)["Номенклатура"]
        self.assertEqual(goods["БИН"].iloc[0], "090340098765")

    def test_relationship_by_shared_key(self):
        rels = detect_shared_key_relationships(read_excel_tables(self.path))
        self.assertEqual(rels, [{"from_table": "Продажи", "from_col": "артикул", "to_table": "Номенклатура", "to_col": "артикул"}])

    def test_ingest_as_group(self):
        from app.services.ingest import ingest_file
        conn = duckdb.connect()
        conn.execute("CREATE TABLE datasets (id VARCHAR, name VARCHAR, rows INTEGER, columns TEXT, table_name VARCHAR, "
                     "source_group VARCHAR, source_file VARCHAR, relationships TEXT, uploaded_at TIMESTAMP DEFAULT now())")
        res = ingest_file(self.path, conn)
        self.assertEqual(res["warnings"], ["Импортировано таблиц: 2"])
        groups = conn.execute("SELECT COUNT(DISTINCT source_group), COUNT(*) FROM datasets").fetchone()
        self.assertEqual(groups, (1, 2))
        t = conn.execute("SELECT table_name FROM datasets WHERE name = 'Продажи'").fetchone()[0]
        typ = conn.execute(f"SELECT data_type FROM information_schema.columns WHERE table_name = '{t}' AND column_name = 'дата'").fetchone()[0]
        self.assertIn("TIMESTAMP", typ)


class CleanTableTest(unittest.TestCase):
    def test_header_detection_skips_title_rows(self):
        raw = pd.DataFrame([["Отчёт", None, None], [None, None, None], ["Дата", "Товар", "Сумма"], ["2026-01-01", "А", 5]])
        self.assertEqual(find_header_row(raw), 2)

    def test_csv_like_table_cleanup(self):
        df = pd.DataFrame({"Товар": ["А", "Б", "Всего"], "Сумма": ["1 200", "300,5", "1 500,5"]})
        out = clean_table(df)
        self.assertEqual(list(out["Товар"]), ["А", "Б"])
        self.assertEqual(list(out["Сумма"]), [1200.0, 300.5])


class DataQualityTest(unittest.TestCase):
    def test_finds_typical_problems(self):
        from app.services import insights
        conn = duckdb.connect()
        conn.execute("""CREATE TABLE t_in AS SELECT * FROM (VALUES
            ('ПТ-1', 'ТОО Альфа', 'A-1', 10, 100), ('ПТ-2', 'ТОО Альфа', 'A-1', 10, 100),
            ('ПТ-3', 'ТОО "Бета"', 'A-2', 5, 50), ('ПТ-4', 'ТОО Бета', 'A-3', -1, 60)) v(номер_документа, контрагент, артикул, количество, сумма)""")
        # Отрицательное значение — редкость: 1 из 25 строк
        conn.execute("INSERT INTO t_in SELECT 'ПТ-' || (i + 10), 'ТОО Гамма', 'A-' || i, i, i * 10 FROM range(1, 22) r(i)")
        rows, _ = insights.data_quality(conn, [{"name": "Поступления", "table_name": "t_in"}])
        checks = {r["check"] for r in rows}
        self.assertIn("одинаковые записи под разными номерами (возможно, документ проведён дважды)", checks)
        self.assertIn("одно значение в разных написаниях в колонке «контрагент»", checks)
        self.assertIn("отрицательные значения в колонке «количество»", checks)


class GrowthAnswerTest(unittest.TestCase):
    def test_all_periods_listed_with_extremes(self):
        from app.services.pipeline import growth_answer
        rows = [{"period": "январь 2026", "revenue": 100.0e6, "revenue_change_pct": None, "compared_to": None},
                {"period": "февраль 2026", "revenue": 80.0e6, "revenue_change_pct": -20.0, "compared_to": "январь 2026"},
                {"period": "март 2026", "revenue": 120.0e6, "revenue_change_pct": 50.0, "compared_to": "февраль 2026"}]
        text = growth_answer(rows)
        self.assertIn("февраль 2026: 80,00 млн (−20,0 %", text)
        self.assertIn("Сильнее всего выручка выросла: март 2026 (+50,0 %)", text)
        self.assertIn("Сильнее всего снизилась: февраль 2026 (−20,0 %)", text)


if __name__ == "__main__":
    unittest.main()


class ColumnRepairTest(unittest.TestCase):
    def test_distorted_column_replaced(self):
        from app.domain.ai.sql_utils import fix_unknown_column
        cols = ["артикул", "бренд", "дата_текст", "сумма_возврата_kzt"]
        self.assertEqual(fix_unknown_column("SELECT brand FROM t GROUP BY brand",
                                            'Referenced column "brand" not found in FROM clause!', cols),
                         "SELECT бренд FROM t GROUP BY бренд")
        self.assertEqual(fix_unknown_column("SELECT r.дата FROM t r", 'Table "r" does not have a column named "дата"', cols),
                         "SELECT r.дата_текст FROM t r")
        self.assertIsNone(fix_unknown_column("SELECT zzz FROM t", 'Referenced column "zzz" not found', cols))

    def test_clean_names_without_extra_underscores(self):
        from app.services.ingest import clean_column_names
        self.assertEqual(clean_column_names(["Наценка, %", "Дата (текст)", "Сумма со скидкой, KZT"]),
                         ["наценка_pct", "дата_текст", "сумма_со_скидкой_kzt"])


class RankingNegativeTest(unittest.TestCase):
    def test_returns_ranked_by_absolute_value(self):
        from app.services.pipeline import ranking_note
        note = ranking_note([{"канал": "ТП-08", "сумма": -5.8e6}, {"канал": "ОНЛ", "сумма": -35.6e6},
                             {"канал": "ТП-01", "сумма": -9.2e6}], "сумма")
        self.assertIn("1-е место — ОНЛ", note)
        self.assertIn("по модулю", note)


class ShortDataChangeTest(unittest.TestCase):
    def test_one_month_compares_weeks(self):
        from app.services import insights
        conn = duckdb.connect()
        conn.execute("CREATE TABLE datasets (id VARCHAR, name VARCHAR, rows INTEGER, columns TEXT, table_name VARCHAR)")
        conn.execute("INSERT INTO datasets VALUES ('d1', 't', 0, '[]', 'dataset_ab')")
        conn.execute("""CREATE TABLE dataset_ab AS SELECT TIMESTAMP '2026-07-01' + INTERVAL (i) DAY AS дата,
                        CASE WHEN i % 2 = 0 THEN 'Точка А' ELSE 'Точка Б' END AS торговая_точка, 100.0 + i AS сумма
                        FROM range(0, 31) r(i)""")
        ctx = insights.build_ctx(conn, "d1", None)
        rows, note = insights.change_by_dimension(conn, ctx, "warehouse")
        self.assertEqual(len(rows), 2)
        self.assertIn("неделя 20.07–26.07.2026", note)
