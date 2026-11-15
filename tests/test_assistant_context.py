import unittest

import duckdb

from app.domain.ai.intent_classifier import classify_intent_fast
from app.services.llm import recent_history_rows


class RecentHistoryTest(unittest.TestCase):
    def test_returns_latest_messages_in_order(self):
        conn = duckdb.connect()
        conn.execute("CREATE TABLE chat_messages (session_id VARCHAR, role VARCHAR, content VARCHAR, created_at TIMESTAMP)")
        for i in range(15):
            conn.execute(
                "INSERT INTO chat_messages VALUES ('s', 'user', ?, TIMESTAMP '2024-01-01 00:00:00' + to_seconds(?))",
                [f"m{i}", i],
            )
        rows = recent_history_rows(conn, "s")
        self.assertEqual([c for _, c in rows], [f"m{i}" for i in range(5, 15)])


class IntentFromQuestionTest(unittest.TestCase):
    def test_previous_question_does_not_change_intent(self):
        """Тип запроса берётся по самому вопросу — история «топ» не превращает вопрос в «top»."""
        question = "выручка по городам"
        polluted = "ПРЕДЫДУЩИЙ КОНТЕКСТ ДИАЛОГА:\nПользователь: топ 5 товаров\n\nТЕКУЩИЙ ВОПРОС: " + question
        self.assertNotEqual(classify_intent_fast(question), "top")
        self.assertEqual(classify_intent_fast(polluted), "top")  # так было раньше


if __name__ == "__main__":
    unittest.main()


class SqlRewriteTest(unittest.TestCase):
    def test_remap_handles_parenthesis_comma_end(self):
        from app.services.llm import _remap_table_names
        m = {"purchases": "dataset_p", "users_list": "dataset_u"}
        # Без своего псевдонима исходное имя остаётся псевдонимом: «purchases.col» продолжает работать
        self.assertEqual(
            _remap_table_names("WITH a AS (SELECT * FROM purchases) SELECT * FROM a JOIN users_list u ON true", m),
            'WITH a AS (SELECT * FROM dataset_p AS "purchases") SELECT * FROM a JOIN dataset_u u ON true',
        )
        self.assertEqual(_remap_table_names("SELECT * FROM purchases", m), 'SELECT * FROM dataset_p AS "purchases"')
        self.assertEqual(_remap_table_names("SELECT * FROM purchases;", m), 'SELECT * FROM dataset_p AS "purchases";')
        self.assertEqual(_remap_table_names("SELECT * FROM purchases_old", m), "SELECT * FROM purchases_old")

    def test_remap_keeps_table_name_qualifiers_working(self):
        import duckdb
        from app.services.llm import _remap_table_names
        conn = duckdb.connect()
        conn.execute("CREATE TABLE dataset_o AS SELECT 'A-1' AS артикул, 5 AS остаток")
        sql = _remap_table_names("SELECT Остатки.артикул FROM Остатки WHERE Остатки.остаток > 0", {"Остатки": "dataset_o"})
        self.assertEqual(conn.execute(sql).fetchall(), [("A-1",)])

    def test_current_date_uses_table_name_not_dataset_id(self):
        from app.services.llm import _fix_current_date
        manifests = {"3f2a1c00-11aa-4bbb-8ccc-123456789abc": {
            "table_name": "dataset_3f2a1c00_11aa_4bbb_8ccc_123456789abc",
            "profiles": {"order_date": {"type": "BIGINT"}},
        }}
        sql = _fix_current_date("SELECT * FROM t WHERE d > CURRENT_DATE - INTERVAL 7 DAY", manifests)
        self.assertIn("FROM dataset_3f2a1c00_11aa_4bbb_8ccc_123456789abc)", sql)
        self.assertNotIn("3f2a1c00-11aa", sql)
        duckdb.connect().execute("SELECT json_serialize_sql(?)", [sql])  # синтаксически валиден


class FixFilterValuesTest(unittest.TestCase):
    def setUp(self):
        self.conn = duckdb.connect()
        self.conn.execute("CREATE TABLE dataset_m AS SELECT * FROM (VALUES ('YellowGold'), ('RoseGold'), ('WhiteGold'), ('Silver')) t(metal)")
        self.manifest = {"profiles": {"metal": {"sample_values": []}}}

    def fix(self, sql):
        from app.services.llm import _fix_filter_values
        return _fix_filter_values(sql, self.conn, "dataset_m", self.manifest)

    def test_like_is_left_alone(self):
        sql = "SELECT * FROM dataset_m WHERE LOWER(metal) LIKE '%gold%'"
        self.assertEqual(self.fix(sql), sql)

    def test_equality_fixed_deterministically_and_in_original_case(self):
        out = {self.fix("SELECT * FROM dataset_m WHERE metal = 'silvr'") for _ in range(5)}
        self.assertEqual(out, {"SELECT * FROM dataset_m WHERE metal = 'Silver'"})

    def test_longer_translation_wins(self):
        out = self.fix("SELECT * FROM dataset_m WHERE LOWER(metal) = 'белое золото'")
        self.assertEqual(out, "SELECT * FROM dataset_m WHERE LOWER(metal) = 'whitegold'")

    def test_exact_value_untouched(self):
        sql = "SELECT * FROM dataset_m WHERE LOWER(metal) = 'rosegold'"
        self.assertEqual(self.fix(sql), sql)
