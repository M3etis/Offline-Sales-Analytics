"""Промпты и дефолтные инструкции не должны противоречить друг другу."""
import unittest

import duckdb

import app.shared.prompts.templates as t
from app.domain.ai.sql_utils import fix_current_date

def _sql_prompts() -> dict[str, str]:
    return {
        "system": t.get_system_prompt("sales", "a INT"),
        "multi_system": t.get_tables_system_prompt([("purchases", "- product_id INT"), ("products", "- product_id INT")],
                                                   ["purchases.product_id = products.product_id"]),
        "sql": t.get_sql_prompt("sales", "a, b", "вопрос"),
        "sql_multi": t.get_sql_prompt("sales", "a, b", "вопрос", None, True),
    }


class SqlPromptConsistencyTest(unittest.TestCase):
    def test_current_date_is_only_forbidden_never_shown(self):
        for name, prompt in _sql_prompts().items():
            for line in prompt.splitlines():
                if "CURRENT_DATE" in line:
                    with self.subTest(prompt=name, line=line):
                        self.assertIn("НИКОГДА", line)

    def test_same_limit_rule_everywhere(self):
        for name, prompt in _sql_prompts().items():
            with self.subTest(prompt=name):
                self.assertIn(t.LIMIT_RULE, prompt)
                self.assertNotIn("Добавь LIMIT 100", prompt)

    def test_system_prompt_asks_for_sql_only(self):
        self.assertNotIn("Отвечай кратко на русском языке", t.get_system_prompt("sales", "a INT"))

    def test_period_examples_run_on_duckdb(self):
        """Примеры периодов из промпта выполняются для BIGINT, VARCHAR и DATE дат."""
        conn = duckdb.connect()
        conn.execute("CREATE TABLE b AS SELECT * FROM (VALUES (1704067200), (1735600000)) v(c)")
        conn.execute("CREATE TABLE s AS SELECT * FROM (VALUES ('2023-06-01'), ('2024-12-30')) v(c)")
        conn.execute("CREATE TABLE d AS SELECT CAST(c AS DATE) AS c FROM s")
        for table, col_type in (("b", "BIGINT"), ("s", "VARCHAR"), ("d", "DATE")):
            expr = t.date_expression("c", col_type)
            examples = [line.split(": ", 1)[1] for line in t._date_period_rules(table, expr).splitlines()
                        if line.strip().startswith('- "за ')]
            self.assertGreaterEqual(len(examples), 5)
            for where in examples:
                with self.subTest(table=table, where=where):
                    conn.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()

    def test_period_rules_have_no_bare_alias(self):
        """Псевдоним «d» модель писала буквально («WHERE d >= …») — в правилах его быть не должно."""
        rules = t._date_period_rules("TBL", "date")
        self.assertNotRegex(rules, r"\bd\b")
        self.assertIn("MAX(date)", rules)


class SummaryMessageTest(unittest.TestCase):
    def test_chart_key_is_not_requested(self):
        # Колонку для графика выбирает код: тег посреди текста пользователь видел как «тенге ()»
        self.assertNotIn("[CHART_KEY:", t.summary_system_message())
        self.assertNotIn("[CHART_KEY:", t.SUMMARY_PROMPT)
        self.assertNotIn("[CHART_KEY:", t.SUMMARY_PROMPT_EXTENDED)

    def test_numbers_are_not_reformatted_by_model(self):
        self.assertNotIn("3041454711", t.SUMMARY_PROMPT)
        self.assertIn("УЖЕ отформатированы", t.SUMMARY_PROMPT)

    def test_llm_and_streaming_share_the_message(self):
        import app.services.llm as llm
        import app.services.streaming as streaming
        self.assertIs(llm.summary_system_message, t.summary_system_message)
        self.assertIs(streaming.summary_system_message, t.summary_system_message)


class FixCurrentDateTest(unittest.TestCase):
    def setUp(self):
        self.conn = duckdb.connect()
        self.conn.execute(
            "CREATE TABLE dataset_x AS SELECT * FROM (VALUES "
            "(DATE '2023-03-01', '2023-03-01', 100), (DATE '2024-11-20', '2024-11-20', 200)) v(order_date, order_date_str, amount)"
        )

    def run_fixed(self, col: str, col_type: str, sql: str):
        manifests = {"uuid-1": {"table_name": "dataset_x", "profiles": {col: {"type": col_type}, "amount": {"type": "INTEGER"}}}}
        fixed = fix_current_date(sql, manifests)
        self.assertNotIn("CURRENT_DATE", fixed.upper())
        return self.conn.execute(fixed).fetchone()[0]

    def test_date_column(self):
        sql = "SELECT SUM(amount) FROM dataset_x WHERE EXTRACT(YEAR FROM order_date) = EXTRACT(YEAR FROM CURRENT_DATE) - 1"
        self.assertEqual(self.run_fixed("order_date", "DATE", sql), 100)

    def test_varchar_date_column(self):
        sql = ("SELECT SUM(amount) FROM dataset_x "
               "WHERE TRY_CAST(order_date_str AS DATE) >= CURRENT_DATE - INTERVAL 30 DAY")
        self.assertEqual(self.run_fixed("order_date_str", "VARCHAR", sql), 200)

    def test_now_is_replaced_too(self):
        sql = "SELECT SUM(amount) FROM dataset_x WHERE order_date >= NOW() - INTERVAL 30 DAY"
        manifests = {"u": {"table_name": "dataset_x", "profiles": {"order_date": {"type": "DATE"}}}}
        self.assertNotIn("NOW()", fix_current_date(sql, manifests).upper())

    def test_no_date_column_leaves_sql(self):
        sql = "SELECT CURRENT_DATE"
        self.assertEqual(fix_current_date(sql, {"u": {"table_name": "t", "profiles": {"name": {"type": "VARCHAR"}}}}), sql)

    def test_current_date_with_parentheses(self):
        sql = "SELECT SUM(amount) FROM dataset_x WHERE order_date >= CURRENT_DATE() - INTERVAL 30 DAY"
        self.assertEqual(self.run_fixed("order_date", "DATE", sql), 200)

    def test_identifiers_containing_current_date_are_untouched(self):
        manifests = {"u": {"table_name": "dataset_x", "profiles": {"order_date": {"type": "DATE"}}}}
        fixed = fix_current_date("SELECT current_date_flag, t.current_date FROM t WHERE d < CURRENT_DATE", manifests)
        self.assertIn("SELECT current_date_flag, t.current_date FROM t", fixed)
        self.assertNotIn("< CURRENT_DATE", fixed)

    def test_date_arithmetic_on_unix_timestamps(self):
        # 2024-01-01 и 2024-12-31: CURRENT_DATE - 1 должно остаться арифметикой над DATE
        self.conn.execute("CREATE TABLE dataset_b AS SELECT * FROM (VALUES (1704067200, 1), (1735603200, 2)) v(ts_date, amount)")
        manifests = {"u": {"table_name": "dataset_b", "profiles": {"ts_date": {"type": "BIGINT"}}}}
        sql = "SELECT SUM(amount) FROM dataset_b WHERE CAST(TO_TIMESTAMP(ts_date) AS DATE) > CURRENT_DATE - 1"
        self.assertEqual(self.conn.execute(fix_current_date(sql, manifests)).fetchone()[0], 2)

    def test_integer_duration_does_not_beat_text_date(self):
        self.conn.execute(
            "CREATE TABLE dataset_y AS SELECT * FROM (VALUES (45, '2023-03-01'), (30, '2024-11-20')) v(delivery_time, date)"
        )
        manifests = {"u": {"table_name": "dataset_y", "profiles": {"delivery_time": {"type": "BIGINT"}, "date": {"type": "VARCHAR"}}}}
        fixed = fix_current_date("SELECT COUNT(*) FROM dataset_y WHERE TRY_CAST(date AS DATE) >= CURRENT_DATE - INTERVAL 30 DAY", manifests)
        self.assertNotIn("delivery_time", fixed)
        self.assertEqual(self.conn.execute(fixed).fetchone()[0], 1)

    def test_group_takes_date_from_tables_in_query(self):
        manifests = {
            "c": {"table_name": "dataset_customers", "profiles": {"birth_date": {"type": "DATE"}}},
            "x": {"table_name": "dataset_x", "profiles": {"order_date": {"type": "DATE"}}},
        }
        fixed = fix_current_date("SELECT SUM(amount) FROM dataset_x WHERE order_date >= CURRENT_DATE - INTERVAL 30 DAY", manifests)
        self.assertNotIn("dataset_customers", fixed)
        self.assertEqual(self.conn.execute(fixed).fetchone()[0], 200)

    def test_column_mapping_date_is_preferred(self):
        manifests = {"u": {"table_name": "dataset_x", "column_mapping": {"date": "order_date_str"},
                           "profiles": {"order_date": {"type": "DATE"}, "order_date_str": {"type": "VARCHAR"}}}}
        self.assertIn('"order_date_str"', fix_current_date("SELECT CURRENT_DATE", manifests))

    def test_now_keeps_time_of_day(self):
        # 48 почасовых строк в VARCHAR: «последние 24 часа» — это 24 строки, а не двое суток
        self.conn.execute(
            "CREATE TABLE dataset_h AS SELECT strftime(TIMESTAMP '2024-01-01' + INTERVAL (i) HOUR, '%Y-%m-%d %H:%M:%S') AS ts_time "
            "FROM range(48) r(i)"
        )
        manifests = {"u": {"table_name": "dataset_h", "profiles": {"ts_time": {"type": "VARCHAR"}}}}
        fixed = fix_current_date("SELECT COUNT(*) FROM dataset_h WHERE TRY_CAST(ts_time AS TIMESTAMP) > NOW() - INTERVAL 24 HOUR", manifests)
        self.assertEqual(self.conn.execute(fixed).fetchone()[0], 24)


class DefaultInstructionsTest(unittest.TestCase):
    def test_defaults_do_not_ask_for_bold(self):
        from app.db import default_instructions as d
        self.assertNotIn("жирным шрифтом", d.READABILITY_RULE)

    def test_defaults_do_not_repeat_summary_prompts_or_sql_hints(self):
        """Правила доходят только до шага ответа: SQL-подсказки и дубли промптов там лишние."""
        from app.db import default_instructions as d
        text = " ".join(content for _, content in d.DEFAULT_INSTRUCTIONS)
        for needle in ("CLARIFY", "TO_TIMESTAMP", "JOIN", "GROUP BY", "LOWER(", "ROUND(", "русском", "млрд", "валют"):
            with self.subTest(needle=needle):
                self.assertNotIn(needle, text)

    def test_old_defaults_are_migrated(self):
        from app.core.dependencies import db_manager
        from app.db import default_instructions as d

        db_manager.init_db()
        conn = db_manager.get_connection()
        conn.execute("DELETE FROM assistant_instructions")
        # База версии 1.1.0: ключа версии набора ещё нет
        conn.execute("DELETE FROM system_settings WHERE setting_key = ?", [d.VERSION_KEY])
        conn.execute("INSERT INTO assistant_instructions (id, category, content) VALUES ('1', 'rules', ?)", [d.CAUSES_RULE_OLD])
        conn.execute("INSERT INTO assistant_instructions (id, category, content) VALUES ('2', 'rules', 'своё правило')")
        db_manager.init_db()
        rows = {r[0] for r in conn.execute("SELECT content FROM assistant_instructions").fetchall()}
        self.assertEqual(rows, {d.CAUSES_RULE, "своё правило"})

if __name__ == "__main__":
    unittest.main()
