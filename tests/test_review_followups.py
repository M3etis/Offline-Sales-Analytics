"""Доработки после ревью: связи «X_id → X.id», ремонт имён колонок, роли из словаря, подписи колонок."""
import unittest

import pandas as pd

from app.services.ingest import detect_shared_key_relationships


def _sales():
    return pd.DataFrame({"Дата": ["01.07.2026"] * 6, "Товар_id": ["1", "2", "3", "1", "2", "5"],
                         "Магазин_id": ["1", "2", "3", "1", "2", "3"], "Сумма": [10, 20, 30, 40, 50, 60]})


class NamedKeyRelationshipTest(unittest.TestCase):
    def test_entity_id_links_to_directory_id(self):
        tables = {"Продажи": _sales(),
                  "Товары": pd.DataFrame({"id": ["1", "2", "3", "4", "5"], "Название": list("абвгд"),
                                          "Категория": ["x", "x", "y", "y", "z"]}),
                  "Магазины": pd.DataFrame({"id": ["1", "2", "3"], "Город": ["А", "Б", "В"], "Адрес": ["1", "2", "3"]})}
        rels = detect_shared_key_relationships(tables)
        self.assertIn({"from_table": "Продажи", "from_col": "товар_id", "to_table": "Товары", "to_col": "id"}, rels)
        self.assertIn({"from_table": "Продажи", "from_col": "магазин_id", "to_table": "Магазины", "to_col": "id"}, rels)
        # «id» товаров и магазинов — мелкие целые с общим именем: это не связь
        self.assertFalse([r for r in rels if r["from_col"] == "id"])

    def test_code_of_entity_and_english_names(self):
        tables = {"Sales": pd.DataFrame({"product_code": ["P10", "P11", "P10"], "amount": [1, 2, 3]}),
                  "Products": pd.DataFrame({"code": ["P10", "P11", "P12"], "name": ["a", "b", "c"]})}
        self.assertEqual(detect_shared_key_relationships(tables),
                         [{"from_table": "Sales", "from_col": "product_code", "to_table": "Products", "to_col": "code"}])
        tables = {"Продажи": pd.DataFrame({"Код товара": ["1", "2", "2"], "Сумма": [1, 2, 3]}),
                  "Товары": pd.DataFrame({"Код": ["1", "2", "3"], "Наименование": ["a", "b", "c"]})}
        self.assertEqual(detect_shared_key_relationships(tables),
                         [{"from_table": "Продажи", "from_col": "код_товара", "to_table": "Товары", "to_col": "код"}])

    def test_no_link_when_values_missing_or_ambiguous(self):
        sales = pd.DataFrame({"товар_id": ["7", "8", "9"], "сумма": [1, 2, 3]})
        goods = pd.DataFrame({"id": ["1", "2", "3"], "название": ["a", "b", "c"]})
        # Значений ссылки нет в справочнике — совпадение имён случайно
        self.assertEqual(detect_shared_key_relationships({"Продажи": sales, "Товары": goods}), [])
        sales = pd.DataFrame({"товар_id": ["1", "2", "3"], "сумма": [1, 2, 3]})
        # Два одинаково подходящих справочника — не угадываем
        self.assertEqual(detect_shared_key_relationships({"Продажи": sales, "Товары": goods, "Товары 2025": goods.copy()}), [])

    def test_seller_id_does_not_link_to_sales_sheet(self):
        sales = pd.DataFrame({"id": ["1", "2", "3", "4"], "продавец_id": ["1", "2", "1", "2"], "сумма": [1, 2, 3, 4]})
        sellers = pd.DataFrame({"id": ["1", "2"], "фио": ["А", "Б"], "отдел": ["x", "y"]})
        rels = detect_shared_key_relationships({"Продажи": sales, "Продавцы": sellers})
        self.assertEqual(rels, [{"from_table": "Продажи", "from_col": "продавец_id", "to_table": "Продавцы", "to_col": "id"}])

    def test_text_and_number_keys_not_linked(self):
        sales = pd.DataFrame({"productId": [1, 2, 3], "amount": [1, 2, 3]})
        products = pd.DataFrame({"id": ["1", "2", "3"], "name": ["a", "b", "c"]})
        self.assertEqual(detect_shared_key_relationships({"Sales": sales, "Products": products}), [])


if __name__ == "__main__":
    unittest.main()


class RealDuckDBErrorTest(unittest.TestCase):
    """Ремонт колонок опирается на текст ошибки DuckDB: берём его у установленной версии, а не вписываем."""

    COLS = ["дата_текст", "бренд", "amount"]

    def setUp(self):
        import duckdb

        self.con = duckdb.connect()
        self.con.execute('CREATE TABLE t ("дата_текст" VARCHAR, "бренд" VARCHAR, amount DOUBLE)')

    def tearDown(self):
        self.con.close()

    def error_of(self, sql):
        try:
            self.con.execute(sql)
        except Exception as e:
            return str(e)
        self.fail(f"запрос выполнился: {sql}")

    def test_patterns_match_installed_duckdb(self):
        from app.domain.ai.sql_utils import _MISSING_COL_RES

        for sql, name in (("SELECT brand FROM t", "brand"), ("SELECT x.brand FROM t x", "brand"),
                          ("SELECT SUM(amount) FROM t GROUP BY дата__", "дата__"),
                          ('SELECT * FROM t WHERE "Бренд2" = 1', "Бренд2")):
            err = self.error_of(sql)
            found = next((m.group(1) for rx in _MISSING_COL_RES for m in [rx.search(err)] if m), None)
            self.assertEqual(found, name, err)

    def test_repair_runs_on_real_error(self):
        from app.domain.ai.sql_utils import fix_unknown_column

        sql = "SELECT дата__, SUM(amount) AS s FROM t GROUP BY дата__ ORDER BY s"
        fixed = fix_unknown_column(sql, self.error_of(sql), self.COLS)
        self.assertEqual(fixed, "SELECT дата_текст, SUM(amount) AS s FROM t GROUP BY дата_текст ORDER BY s")
        self.con.execute(fixed)

    def test_unknown_column_without_known_wording(self):
        """Формулировка ошибки сменилась — колонку находит разбор запроса, псевдонимы не путаются с колонками."""
        from app.domain.ai.sql_utils import fix_unknown_column, unknown_column

        sql = "SELECT дата__, SUM(amount) AS s FROM t GROUP BY 1 ORDER BY s"
        self.assertEqual(unknown_column(sql, "Binder Error: no such thing: дата__", self.COLS), "дата__")
        self.assertEqual(fix_unknown_column(sql, "Binder Error: something new", self.COLS),
                         "SELECT дата_текст, SUM(amount) AS s FROM t GROUP BY 1 ORDER BY s")
        # Две неизвестные колонки и ни одной в кавычках — не угадываем
        self.assertIsNone(unknown_column("SELECT foo, bar FROM t", "Binder Error: ?", self.COLS))
        # Не ошибка привязки — не трогаем
        self.assertIsNone(unknown_column("SELECT foo FROM t", "Conversion Error: foo", self.COLS))


class UserDictionaryRolesTest(unittest.TestCase):
    """Роль колонки можно задать словарём колонок: эвристика по имени его учитывает."""

    def setUp(self):
        import duckdb

        self.con = duckdb.connect()
        self.con.execute("CREATE TABLE column_dictionary (id VARCHAR, en_name VARCHAR, ru_name VARCHAR, category VARCHAR)")
        self.con.execute("CREATE TABLE t (kassa_itog DOUBLE, summa_vozvrata DOUBLE, gorod VARCHAR, dt TIMESTAMP)")

    def tearDown(self):
        self.con.close()

    def add(self, en, ru):
        self.con.execute("INSERT INTO column_dictionary VALUES (?, ?, ?, 'other')", [en, en, ru])

    def test_builtin_translations_are_not_aliases(self):
        from app.services.column_dict import COLUMN_TRANSLATIONS, user_aliases

        en, ru = next(iter(COLUMN_TRANSLATIONS.items()))
        self.add(en, ru)
        self.assertEqual(user_aliases(self.con), {})

    def test_alias_sets_role_and_overrides_heuristic(self):
        from app.domain.ai.column_roles import infer_roles
        from app.services.column_dict import user_aliases

        cols = [("kassa_itog", "DOUBLE"), ("summa_vozvrata", "DOUBLE"), ("gorod", "VARCHAR"), ("dt", "TIMESTAMP")]
        # Транслит эвристика не понимает: выручкой стала «сумма возврата»
        self.assertEqual(infer_roles(cols).get("revenue"), "summa_vozvrata")
        self.add("Kassa itog", "Выручка")
        self.add("gorod", "город")
        roles = infer_roles(cols, None, user_aliases(self.con))
        self.assertEqual(roles.get("revenue"), "kassa_itog")
        self.assertEqual(roles.get("region"), "gorod")
        # Встроенная эвристика выбрала бы «Сумму», пользователь сказал — выручка «Итог кассы»
        cols2 = [("сумма", "DOUBLE"), ("итог_кассы", "DOUBLE")]
        self.assertEqual(infer_roles(cols2)["revenue"], "сумма")
        self.add("итог кассы", "выручка")
        self.assertEqual(infer_roles(cols2, None, user_aliases(self.con))["revenue"], "итог_кассы")

    def test_dashboard_mapping_recomputed_after_dictionary_edit(self):
        from app.services.analytics import _map_columns

        manifest = {"table_name": "t", "row_count": 10,
                    "columns": [{"name": "kassa_itog", "type": "DOUBLE"}, {"name": "dt", "type": "TIMESTAMP"}],
                    "profiles": {}, "column_mapping": {"revenue": "NULL", "date": "dt"}}
        self.assertEqual(_map_columns(self.con, "t", manifest)["revenue"], "NULL")
        self.add("kassa_itog", "выручка")
        self.assertEqual(_map_columns(self.con, "t", manifest)["revenue"], "kassa_itog")

    def test_cache_signature_depends_on_dictionary(self):
        from app.services import llm

        self.con.execute("CREATE TABLE assistant_instructions (category VARCHAR, content VARCHAR)")
        before = llm.cache_signature(self.con, False)
        self.add("kassa_itog", "выручка")
        self.assertNotEqual(llm.cache_signature(self.con, False), before)


class ColumnLabelsTest(unittest.TestCase):
    """Подписи колонок — с бэкенда; запасной словарь фронтенда не расходится с ними."""

    def test_frontend_fallback_matches_backend(self):
        import re
        from pathlib import Path

        from app.domain.ai.data_format import column_labels

        src = Path(__file__).resolve().parents[1].joinpath("frontend/src/components/ChatMessage.tsx").read_text("utf-8")
        block = src.split("const KEY_TRANSLATIONS", 1)[1].split("};", 1)[0]
        front = dict(re.findall(r"^\s*(\w+): '([^']*)',", block, re.MULTILINE))
        self.assertGreater(len(front), 50)
        back = column_labels()
        for key, text in front.items():
            self.assertIn(key, back, f"{key} нет в data_format.column_labels")
            self.assertEqual(back[key].lower(), text.lower(), key)

    def test_user_alias_labels_do_not_override_builtin(self):
        from app.domain.ai.data_format import column_labels

        labels = column_labels({"kassa_itog": "выручка кассы", "revenue": "что-то своё"})
        self.assertEqual(labels["kassa_itog"], "выручка кассы")
        self.assertEqual(labels["revenue"], "выручка")
