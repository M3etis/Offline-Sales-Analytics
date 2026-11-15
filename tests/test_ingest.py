import unittest

from app.services.ingest import clean_column_names


class CleanColumnNamesTest(unittest.TestCase):
    def test_regular_names_unchanged_behaviour(self):
        self.assertEqual(clean_column_names(["Date", "Order ID", "Выручка"]), ["date", "order_id", "выручка"])
        self.assertEqual(clean_column_names(["2024"]), ["col_2024"])

    def test_blank_header_does_not_crash(self):
        self.assertEqual(clean_column_names(["   ", "a"]), ["col_1", "a"])

    def test_duplicates_get_suffix(self):
        self.assertEqual(clean_column_names(["Revenue", "revenue ", "REVENUE"]), ["revenue", "revenue_2", "revenue_3"])

    def test_yo_is_kept(self):
        self.assertEqual(clean_column_names(["Объём"]), ["объём"])


if __name__ == "__main__":
    unittest.main()


class IngestFileTest(unittest.TestCase):
    def test_csv_with_blank_and_duplicate_headers_loads(self):
        import os
        import tempfile

        import duckdb

        from app.services.ingest import ingest_file

        fd, path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("date,Revenue,revenue , \n2024-01-01,10,11,x\n2024-01-02,20,21,y\n")
        conn = duckdb.connect()
        from app.db.connection import DATASETS_TABLE_SCHEMA
        conn.execute(DATASETS_TABLE_SCHEMA)
        try:
            result = ingest_file(path, conn)
        finally:
            os.remove(path)
        self.assertEqual(result["columns"], ["date", "revenue", "revenue_2", "col_4"])
        self.assertEqual(result["rows_loaded"], 2)


class MultiTableIngestTest(unittest.TestCase):
    def test_failure_on_second_table_leaves_nothing_behind(self):
        import os
        import sqlite3
        import tempfile
        from unittest import mock

        import duckdb

        from app.db.connection import DatabaseManager  # noqa: F401 — схемы в модуле
        from app.services import ingest

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(path)
        lite = sqlite3.connect(path)
        lite.execute("CREATE TABLE a (id INTEGER, v TEXT)")
        lite.execute("INSERT INTO a VALUES (1, 'x')")
        lite.execute("CREATE TABLE b (id INTEGER, w TEXT)")
        lite.execute("INSERT INTO b VALUES (1, 'y')")
        lite.commit()
        lite.close()

        conn = duckdb.connect()
        conn.execute("""CREATE TABLE datasets (id VARCHAR PRIMARY KEY, name VARCHAR, rows INTEGER, columns TEXT,
            table_name VARCHAR, uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, source_group VARCHAR,
            source_file VARCHAR, relationships TEXT)""")
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("boom")

        try:
            with mock.patch("app.services.preanalysis.preanalyze_dataset", flaky):
                with self.assertRaises(RuntimeError):
                    ingest.ingest_file(path, conn)
        finally:
            os.remove(path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0], 0)
        names = [r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables").fetchall()]
        self.assertEqual([n for n in names if n.startswith("dataset_")], [])
