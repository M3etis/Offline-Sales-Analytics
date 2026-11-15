import unittest

import duckdb

from app.services import analytics


def _conn_with(table_sql: str):
    conn = duckdb.connect()
    conn.execute("CREATE TABLE datasets (id VARCHAR, name VARCHAR, rows INTEGER, columns TEXT, table_name VARCHAR)")
    conn.execute("INSERT INTO datasets VALUES ('d1', 't', 0, '[]', 'dataset_aa')")
    conn.execute(table_sql)
    return conn


class ComparePeriodsTest(unittest.TestCase):
    def setUp(self):
        self.conn = _conn_with("""
            CREATE TABLE dataset_aa AS SELECT * FROM (VALUES
                (DATE '2024-02-10', 'o1', 100.0, 10.0, 1),
                (DATE '2024-03-05', 'o2', 300.0, 30.0, 2),
                (DATE '2024-03-20', 'o3', 200.0, 20.0, 1)
            ) t(date_of_sale, order_id, revenue, profit, quantity)
        """)

    def test_periods_anchor_to_latest_data_date(self):
        p = analytics.month_periods(self.conn, dataset_id="d1")
        self.assertEqual(p, {"current_start": "2024-03-01", "current_end": "2024-03-20",
                             "prev_start": "2024-02-01", "prev_end": "2024-02-29"})

    def test_compare_uses_mapped_date_column(self):
        res = analytics.compare_periods(self.conn, **analytics.month_periods(self.conn, dataset_id="d1"), dataset_id="d1")
        self.assertEqual(res["current"]["revenue"], 500.0)
        self.assertEqual(res["previous"]["revenue"], 100.0)


if __name__ == "__main__":
    unittest.main()


class RegionDeclineTest(unittest.TestCase):
    """Падения по регионам: только полные месяцы, по строке на регион, месяц в описании."""

    def setUp(self):
        # Данные с 15.01 по 15.05: январь и май неполные
        self.conn = _conn_with("""
            CREATE TABLE dataset_aa AS SELECT * FROM (VALUES
                (DATE '2024-01-15', 'Алматы', 1000.0), (DATE '2024-01-15', 'Актау', 1000.0),
                (DATE '2024-02-10', 'Алматы', 1000.0), (DATE '2024-02-10', 'Актау', 1000.0),
                (DATE '2024-03-10', 'Алматы',  500.0), (DATE '2024-03-10', 'Актау', 1000.0),
                (DATE '2024-04-10', 'Алматы',  300.0), (DATE '2024-04-10', 'Актау',  700.0),
                (DATE '2024-05-15', 'Алматы',   10.0), (DATE '2024-05-15', 'Актау',   10.0)
            ) t(date, region, revenue)
        """)

    def _declines(self):
        return [s for s in analytics.find_weak_spots(self.conn, dataset_id="d1") if s["type"] == "region_decline"]

    def test_partial_months_ignored_and_one_row_per_region(self):
        rows = self._declines()
        self.assertEqual([(r["entity"], r["value"]) for r in rows], [("Алматы", -50.0), ("Актау", -30.0)])

    def test_description_names_months(self):
        rows = {r["entity"]: r["description"] for r in self._declines()}
        self.assertEqual(rows["Алматы"], "Регион 'Алматы': падение выручки на 50.0% (март 2024 к февралю 2024)")

    def test_gap_month_not_compared(self):
        self.conn.execute("DELETE FROM dataset_aa WHERE region = 'Актау' AND date = DATE '2024-03-10'")
        self.assertNotIn("Актау", [r["entity"] for r in self._declines()])


class NonStandardColumnsTest(unittest.TestCase):
    def test_anomalies_with_department_column(self):
        conn = _conn_with("""
            CREATE TABLE dataset_aa AS SELECT * FROM (VALUES
                (DATE '2024-01-01', 'Обувь', 100.0), (DATE '2024-01-02', 'Сумки', 1000.0),
                (DATE '2024-01-03', 'Часы', 1100.0)
            ) t(date, department, revenue)
        """)
        cmap = analytics._map_columns(conn, "dataset_aa")
        self.assertEqual(cmap["category"], "department")
        rows = analytics.find_anomalies(conn, dataset_id="d1")
        self.assertIn("Обувь", [r["entity"] for r in rows])

    def test_kpis_with_text_discount(self):
        conn = _conn_with("""
            CREATE TABLE dataset_aa AS SELECT * FROM (VALUES
                (DATE '2024-01-01', 'o1', 100.0, '5,5'), (DATE '2024-01-02', 'o2', 300.0, '10')
            ) t(date, order_id, revenue, discount)
        """)
        k = analytics.get_kpis(conn, dataset_id="d1")
        self.assertEqual(k["total_revenue"], 400.0)
        self.assertAlmostEqual(k["avg_discount"], 7.75, places=2)
