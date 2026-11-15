import unittest

from app.utils.sql_safety import full_validate, validate_tables


class ValidateTablesTest(unittest.TestCase):
    def assertAllowed(self, sql):
        ok, err = full_validate(sql)
        self.assertTrue(ok, f"{sql!r} rejected: {err}")

    def assertBlocked(self, sql):
        ok, _ = full_validate(sql)
        self.assertFalse(ok, f"{sql!r} must be rejected")

    def test_regular_queries_pass(self):
        self.assertAllowed("SELECT region, SUM(revenue) FROM dataset_abc GROUP BY region")
        self.assertAllowed("SELECT * FROM sales s JOIN dataset_b b ON s.id = b.id")
        self.assertAllowed("WITH t AS (SELECT * FROM dataset_a) SELECT * FROM t")
        self.assertAllowed("WITH a AS (SELECT 1 x FROM dataset_a), b AS (SELECT * FROM a) SELECT * FROM b")
        self.assertAllowed("SELECT EXTRACT(YEAR FROM date) y FROM dataset_a")
        self.assertAllowed("SELECT * FROM main.dataset_a")
        self.assertAllowed("SELECT * FROM generate_series(1, 3)")
        self.assertAllowed("SELECT x FROM (SELECT 1 AS x FROM dataset_a) sub")

    def test_hidden_service_tables_blocked(self):
        self.assertBlocked('SELECT password_hash FROM "users"')
        self.assertBlocked("SELECT u.password_hash FROM dataset_a, users u")
        self.assertBlocked('SELECT * FROM dataset_a a JOIN "chat_messages" m ON true')
        self.assertBlocked("SELECT * FROM dataset_a WHERE 1 IN (SELECT 1 FROM system_settings)")
        self.assertBlocked("SELECT * FROM dataset_manifest")
        self.assertBlocked("SELECT * FROM information_schema.tables")

    def test_file_access_blocked(self):
        self.assertBlocked("SELECT * FROM dataset_a, read_text('/etc/hosts')")
        self.assertBlocked("SELECT * FROM glob('/Users/*')")
        self.assertBlocked("SELECT * FROM dataset_a, '/etc/passwd'")

    def test_cte_shadowing_does_not_leak(self):
        # CTE "users" видна только внутри подзапроса — внешний users остаётся настоящей таблицей
        self.assertBlocked("SELECT * FROM users WHERE 1 IN (WITH users AS (SELECT 1) SELECT 1 FROM users)")

    def test_unparsable_sql_falls_back_to_regex(self):
        ok, err = validate_tables("SELEC * FROM users")
        self.assertFalse(ok)
        ok, _ = validate_tables("SELEC * FROM dataset_a")
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()


class ValidateSqlLiteralsTest(unittest.TestCase):
    def test_keywords_inside_literals_are_allowed(self):
        for sql in (
            "SELECT * FROM dataset_a WHERE warehouse = 'S3'",
            "SELECT * FROM dataset_a WHERE product = 'Gift Set'",
            "SELECT * FROM dataset_a WHERE note = 'a;b'",
            "SELECT REPLACE(product, 'a', 'b') FROM dataset_a",
            'SELECT "set" FROM dataset_a',
        ):
            ok, err = full_validate(sql)
            self.assertTrue(ok, f"{sql!r} rejected: {err}")

    def test_real_commands_still_blocked(self):
        for sql in (
            "SELECT 1; DROP TABLE users",
            "CREATE OR REPLACE TABLE x AS SELECT 1",
            "SELECT * FROM dataset_a WHERE x = 'it''s'; DELETE FROM users",
            "WITH t AS (SELECT 1) INSERT INTO users SELECT * FROM t",
        ):
            ok, _ = full_validate(sql)
            self.assertFalse(ok, f"{sql!r} must be rejected")


class SanitizeLimitTest(unittest.TestCase):
    def test_inner_limit_does_not_count(self):
        from app.utils.sql_safety import sanitize_sql
        sql = sanitize_sql("WITH t AS (SELECT * FROM dataset_a LIMIT 1) SELECT * FROM dataset_a", max_rows=1000)
        self.assertTrue(sql.endswith("LIMIT 1000"), sql)
        sql = sanitize_sql("SELECT order_limit FROM dataset_a", max_rows=50)
        self.assertTrue(sql.endswith("LIMIT 50"), sql)

    def test_outer_limit_kept(self):
        from app.utils.sql_safety import sanitize_sql
        self.assertEqual(sanitize_sql("SELECT * FROM dataset_a ORDER BY x LIMIT 5;"), "SELECT * FROM dataset_a ORDER BY x LIMIT 5")
        self.assertEqual(sanitize_sql("SELECT 1 FROM dataset_a UNION ALL SELECT 2 FROM dataset_a LIMIT 3"),
                         "SELECT 1 FROM dataset_a UNION ALL SELECT 2 FROM dataset_a LIMIT 3")
