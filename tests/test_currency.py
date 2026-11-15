"""Валюта в ответах: без валюты в данных — локальная (тенге), а не рубли."""
import unittest

from app.domain.ai.response_builder import normalize_currency
from app.shared.prompts.templates import SUMMARY_PROMPT, SUMMARY_PROMPT_EXTENDED, currency_rule

DATA = [{"store": "Склад-Аст", "profit": 28959.21}]


class NormalizeCurrencyTests(unittest.TestCase):
    def test_rubles_replaced_with_local_currency(self):
        text = "Филиал показывает минимальную прибыль в размере 28959.21 рубля."
        self.assertEqual(
            normalize_currency(text, DATA, "тенге"),
            "Филиал показывает минимальную прибыль в размере 28959.21 тенге.",
        )

    def test_all_forms_and_abbreviations(self):
        text = "1 рубль, 2 рубля, 5 рублей, 3 млн руб. за месяц, итого 7 тыс. руб. Символ: 10 ₽"
        self.assertEqual(
            normalize_currency(text, DATA, "тенге"),
            "1 тенге, 2 тенге, 5 тенге, 3 млн тенге за месяц, итого 7 тыс. тенге. Символ: 10 тенге",
        )

    def test_kept_when_rubles_present_in_data(self):
        text = "Выручка 100 рублей."
        for data in ([{"currency": "RUB", "x": 1}], [{"сумма_руб": 100}], [{"price": "100 ₽"}]):
            with self.subTest(data=data):
                self.assertEqual(normalize_currency(text, data, "тенге"), text)

    def test_kept_when_local_currency_is_ruble(self):
        text = "Выручка 100 рублей."
        self.assertEqual(normalize_currency(text, DATA, "рубли"), text)

    def test_currency_with_backslashes_is_inserted_literally(self):
        for cur in ("тг\\", "KZT\\1", "\\u20b8", "\\g<0>x"):
            with self.subTest(cur=cur):
                self.assertEqual(normalize_currency("Выручка 300 рублей.", DATA, cur), f"Выручка 300 {cur}.")
                self.assertEqual(normalize_currency("Рост на 5%", DATA, cur), "Рост на 5%")

    def test_words_containing_rub_in_data_do_not_disable_replacement(self):
        for data in ([{"product": "Рубашка поло"}], [{"item": "Кольцо с рубином"}], [{"рубрика": "x"}], [{"city": "Рубцовск"}]):
            with self.subTest(data=data):
                self.assertEqual(normalize_currency("Выручка 100 рублей.", data, "тенге"), "Выручка 100 тенге.")

    def test_ruble_columns_in_data_keep_rubles(self):
        for data in ([{"price_rub": 100}], [{"revenue_RUR": 1}], [{"Цена, руб.": 1}], [{"сумма_рублей": 1}]):
            with self.subTest(data=data):
                self.assertEqual(normalize_currency("Выручка 100 рублей.", data, "тенге"), "Выручка 100 рублей.")

    def test_unrelated_words_untouched(self):
        text = "Рубрика «Рубеж»: 100 тенге."
        self.assertEqual(normalize_currency(text, DATA, "тенге"), text)


class CurrencyRuleTests(unittest.TestCase):
    def test_default_wording_is_unchanged(self):
        self.assertIn("в тенге. НИКОГДА не подставляй другие валюты (рубли, доллары, евро), если их нет в данных",
                      currency_rule("тенге"))

    def test_configured_currency_is_not_forbidden(self):
        for cur, kept in (("евро", "(рубли, доллары)"), ("доллары", "(рубли, евро)"), ("рубли", "(доллары, евро)"), ("USD", "(рубли, евро)")):
            with self.subTest(cur=cur):
                rule = currency_rule(cur)
                self.assertIn(f"не подставляй другие валюты {kept}", rule)


class CurrencyPromptTests(unittest.TestCase):
    def test_summary_prompts_default_to_local_currency(self):
        # Валюта по умолчанию берётся из LOCAL_CURRENCY (.env или окружение) — не «тенге» намертво,
        # иначе тест падал у разработчика с LOCAL_CURRENCY=сом в .env
        from app.services.currency import default_currency
        for tpl in (SUMMARY_PROMPT, SUMMARY_PROMPT_EXTENDED):
            prompt = tpl.format(currency_rule=currency_rule(), data="[]", question="q")
            self.assertIn(f"не указана — в {default_currency()}.", prompt)
            self.assertNotIn("БЕЗ указания валюты", prompt)


class CurrencySettingTests(unittest.TestCase):
    """Валюта из настроек приложения перекрывает LOCAL_CURRENCY из .env."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        from app.core.dependencies import db_manager
        from app.core.security import create_access_token
        from app.main import app

        db_manager.init_db()
        cls.conn = db_manager.get_connection()
        cls.client = TestClient(app)
        cls.headers = {"Authorization": f"Bearer {create_access_token(subject='admin', role='admin')}"}

    def tearDown(self):
        self.conn.execute("DELETE FROM system_settings WHERE setting_key = 'local_currency'")

    def _save(self, value):
        return self.client.post(
            "/api/v1/settings/config", json={"key": "local_currency", "value": value}, headers=self.headers
        )

    def test_saved_currency_used_in_prompt_and_normalizer(self):
        from app.services.currency import get_local_currency

        self.assertEqual(self._save("  сом \n").status_code, 200)
        self.assertEqual(get_local_currency(self.conn), "сом")
        self.assertIn("в сом.", currency_rule(get_local_currency(self.conn)))
        self.assertEqual(normalize_currency("100 рублей", DATA, get_local_currency(self.conn)), "100 сом")

    def test_empty_value_resets_to_default(self):
        from app.services.currency import default_currency, get_local_currency

        self._save("евро")
        self.assertEqual(self._save("").status_code, 200)
        self.assertEqual(get_local_currency(self.conn), default_currency())

    def test_config_exposes_default_and_rejects_long_value(self):
        config = self.client.get("/api/v1/settings/config", headers=self.headers).json()
        self.assertTrue(config["local_currency_default"])
        self.assertEqual(self._save("x" * 33).status_code, 400)
        r = self.client.post(
            "/api/v1/settings/config", json={"key": "local_currency_default", "value": "евро"}, headers=self.headers
        )
        self.assertEqual(r.status_code, 403)

    def test_default_rules_do_not_hardcode_currency(self):
        from app.db.default_instructions import DEFAULT_INSTRUCTIONS

        for _, rule in DEFAULT_INSTRUCTIONS:
            self.assertNotIn("тенге", rule)
            self.assertNotIn("БЕЗ указания валюты", rule)


if __name__ == "__main__":
    unittest.main()
