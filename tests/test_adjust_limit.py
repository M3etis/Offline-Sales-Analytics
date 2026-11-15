import unittest

import app.domain.ai.response_builder as rb
import app.domain.ai.sql_utils as su
import app.services.llm as llm
import app.shared.prompts.templates as templates
from app.domain.ai.sql_utils import adjust_limit

TOP_MANAGER_SQL = (
    "WITH top_manager AS (SELECT manager, SUM(revenue) AS total_revenue FROM t "
    "GROUP BY manager ORDER BY total_revenue DESC LIMIT 1)\n"
    "SELECT product, SUM(quantity) AS q FROM t "
    "WHERE manager = (SELECT manager FROM top_manager) "
    "GROUP BY product ORDER BY q DESC LIMIT 1"
)


class AdjustLimitTest(unittest.TestCase):
    def test_most_sold_is_not_a_request_for_all(self):
        # «больше всего» — это максимум, а не «покажи всё»
        self.assertEqual(adjust_limit("какой товар Омаров Т. продал больше всего?", TOP_MANAGER_SQL), TOP_MANAGER_SQL)

    def test_history_does_not_trigger_all(self):
        question = (
            "ПРЕДЫДУЩИЙ КОНТЕКСТ ДИАЛОГА:\n"
            "Пользователь: покажи всех менеджеров за все время\n\n"
            "ТЕКУЩИЙ ВОПРОС: топ 10 товаров"
        )
        self.assertEqual(adjust_limit(question, "SELECT * FROM t LIMIT 10"), "SELECT * FROM t LIMIT 10")

    def test_all_time_is_not_a_request_for_all(self):
        self.assertEqual(adjust_limit("выручка за всё время по месяцам", "SELECT 1 LIMIT 100"), "SELECT 1 LIMIT 100")

    def test_whole_year_is_not_a_request_for_all(self):
        sql = "SELECT product, SUM(q) FROM t GROUP BY product ORDER BY 2 DESC LIMIT 10"
        for q in ("топ 10 товаров за весь год", "топ 10 товаров за весь 2024 год", "топ 10 за весь квартал"):
            with self.subTest(q=q):
                self.assertEqual(adjust_limit(q, sql), sql)

    def test_number_named_by_user_is_kept(self):
        sql = "SELECT product, SUM(q) FROM t GROUP BY product ORDER BY 2 DESC LIMIT 5"
        for q in ("топ 5 товаров за весь май", "топ-5 товаров во всех магазинах", "топ 5 товаров, все категории",
                  "топ 5 товаров за весь прошлый год"):
            with self.subTest(q=q):
                self.assertEqual(adjust_limit(q, sql), sql)

    def test_number_only_in_history_does_not_block_all(self):
        question = "ПРЕДЫДУЩИЙ КОНТЕКСТ ДИАЛОГА:\nПользователь: топ 100\n\nТЕКУЩИЙ ВОПРОС: покажи все товары"
        self.assertEqual(adjust_limit(question, "SELECT * FROM t LIMIT 100"), "SELECT * FROM t LIMIT 500")

    def test_all_raises_only_outer_limit(self):
        sql = "WITH a AS (SELECT x FROM t ORDER BY x LIMIT 1) SELECT * FROM t WHERE x = (SELECT x FROM a) LIMIT 100;"
        self.assertEqual(adjust_limit("покажи все заказы", sql), sql.replace("LIMIT 100", "LIMIT 500"))

    def test_all_keeps_single_value_and_cte_limits(self):
        self.assertEqual(adjust_limit("покажи все товары", TOP_MANAGER_SQL), TOP_MANAGER_SQL)

    def test_all_without_outer_limit_is_untouched(self):
        sql = "SELECT * FROM t WHERE x IN (SELECT x FROM t LIMIT 5)"
        self.assertEqual(adjust_limit("покажи все строки", sql), sql)

    def test_all_raises_plain_limit(self):
        self.assertEqual(
            adjust_limit("полный список товаров", "SELECT * FROM t LIMIT 100 OFFSET 0"),
            "SELECT * FROM t LIMIT 500 OFFSET 0",
        )

    def test_all_raises_limit_before_trailing_comment(self):
        sql = "SELECT * FROM t LIMIT 100; -- все заказы\n/* пояснение */"
        self.assertEqual(adjust_limit("покажи все заказы", sql), sql.replace("LIMIT 100", "LIMIT 500"))

    def test_limit_inside_trailing_comment_is_ignored(self):
        sql = "SELECT * FROM t LIMIT 100 -- LIMIT 5"
        self.assertEqual(adjust_limit("покажи все заказы", sql), "SELECT * FROM t LIMIT 500 -- LIMIT 5")

    def test_dashes_inside_string_literal_are_not_a_comment(self):
        sql = "SELECT * FROM t WHERE note = 'a -- b' LIMIT 100"
        self.assertEqual(adjust_limit("покажи все заказы", sql), sql.replace("LIMIT 100", "LIMIT 500"))

    def test_pathological_tails_are_linear(self):
        """Строка дефисов или цепочка /**/ после LIMIT раньше давала экспоненциальный перебор."""
        import time
        tails = [
            "\n" + "-" * 5000 + "\nЭтот запрос выводит все товары",
            " " + "/**/ " * 5000 + "x",
            "\n" + "-- " * 5000 + "\nx",
        ]
        for tail in tails:
            sql = "SELECT * FROM t LIMIT 10" + tail
            start = time.perf_counter()
            self.assertEqual(adjust_limit("покажи все товары", sql), sql)
            self.assertLess(time.perf_counter() - start, 1.0)


class DomainReexportTest(unittest.TestCase):
    """llm.py не должен держать локальные копии: они молча перекрывают импорт из domain."""

    def test_llm_uses_domain_helpers(self):
        pairs = [
            (llm._adjust_limit, su.adjust_limit),
            (llm._extract_sql, su.extract_sql),
            (llm._fix_cte_errors, su.fix_cte_errors),
            (llm._fix_filter_values, su.fix_filter_values),
            (llm._fix_current_date, su.fix_current_date),
            (llm._remap_table_names, su.remap_table_names),
            (llm._inject_count_header, rb.inject_count_header),
            (llm._extract_chart_key, rb.extract_chart_key),
            (llm._detect_chart_type, rb.detect_chart_type),
        ]
        for local, domain in pairs:
            with self.subTest(name=domain.__name__):
                self.assertIs(local, domain)

    def test_llm_uses_prompt_templates(self):
        for name in ("SUMMARY_PROMPT", "SUMMARY_PROMPT_EXTENDED", "get_system_prompt", "get_sql_prompt"):
            with self.subTest(name=name):
                self.assertIs(getattr(llm, name), getattr(templates, name))


if __name__ == "__main__":
    unittest.main()
