"""Миграция стандартных правил: старый набор сводится к новому, свои правила не трогаются."""
import unittest
import uuid

from app.core.config import settings
from app.db import default_instructions as d

# Стандартный набор из версии 1.1.0 (коммит 795f904, app/db/connection.py, init_db) — ровно то, что
# лежит в базах пользователей. Скопирован в тест, а не читается через `git show`: тест должен идти
# и в неглубоком клоне, и в архиве с исходниками, и без git
V1_1_0_DEFAULTS: list[tuple[str, str]] = [
    ("rules", "ОБЯЗАТЕЛЬНО пиши весь текст исключительно на русском языке."),
    ("rules", "Форматируй большие числа в человеко-понятный текстовый вид, а рядом в скобках указывай точное число. Например: 3.04 млрд (3041454711)."),
    ("rules", "Абсолютная точность: используй строго те значения, которые предоставлены в данных."),
    ("rules", "Категорически запрещено придумывать цифры, искажать метрики и галлюцинировать."),
    ("rules", "Запрещено строить догадки о причинах изменения метрик, если этих причин нет в выборке."),
    ("rules", "Не используй [Жирный шрифт] или markdown-форматирование текста звездами (**)"),
    ("rules", "При ответах на запросы о ключевых показателях (KPI) розничных сетей обязательно указывай: выручку, прибыль, маржинальность (%), средний чек, количество заказов/транзакций, количество проданных единиц. Если данные содержат информацию о магазинах/точках — добавляй разбивку по точкам продаж. Сравнивай показатели с предыдущим периодом если данные позволяют."),
    ("rules", "При работе с группами баз данных (многотабличные .db файлы): определи какие таблицы содержат факты продаж (даты, суммы, количества), а какие — справочники (названия, категории, адреса). Соединяй таблицы через связи из системного промпта. Для агрегирующих запросов ищи числовые колонки в таблицах фактов. Если запрос касается названий или категорий — JOIN со справочниками."),
    ("rules", "При анализе данных розничных сетей: выводи числа как есть, БЕЗ указания валюты, если она явно не указана в базе. Используй бизнес-терминологию: товарооборот, маржинальность, средний чек, конверсия, ROMI. При выявлении аномалий (резкие скачки/падения) — объясняй возможные причины и давай рекомендации. При сравнении периодов — указывай процент изменения."),
    ("rules", "ОЧЕНЬ ВАЖНО ДЛЯ ДАТ: В DuckDB если колонка даты — это UNIX timestamp (BIGINT, целое число), конвертируй её ТОЛЬКО через TO_TIMESTAMP(col). НИКОГДА не вычитай INTERVAL из BIGINT! Правильно: TO_TIMESTAMP(col) - INTERVAL '30 DAY'. Если же колонка даты — это строка (VARCHAR), конвертируй её ТОЛЬКО через TRY_CAST(col AS DATE). НИКОГДА не путай их!"),
    ("rules", "ОТСУТСТВИЕ КОНТЕКСТА: Если запрос бессмысленный или абстрактный (например, 'TOP N' без указания чего именно) и ты НЕ МОЖЕШЬ составить SQL, начни свой ответ строго со слова CLARIFY: и напиши уточняющий вопрос к пользователю."),
    ("recommendations", "Используй профессиональную лексику из сферы продаж, маркетинга и бизнес-аналитики."),
    ("recommendations", "Фокусируйся на конкретных точках роста: подсвечивай аномалии, лидеров и аутсайдеров (товары, категории, менеджеры, регионы)."),
    ("recommendations", "Делай ответ легко читаемым: выделяй ключевые KPI жирным шрифтом и используй маркированные списки."),
    ("recommendations", "При анализе продаж розничной сети учитывай: 1) Структуру данных — продажи (факты) и справочники (товары, магазины, клиенты). 2) Многотабличные данные требуют JOIN через связи. 3) Числовые суммы всегда округляй ROUND(). 4) Для динамики используй GROUP BY по дате с ORDER BY. 5) Для поиска товаров/категорий используй LOWER() LIKE для регистронезависимого поиска."),
    ("recommendations", "Популярные аналитические запросы для розничных сетей и как на них отвечать: 1) Топ товаров/категорий — GROUP BY + ORDER BY SUM(revenue) DESC LIMIT N. 2) Средний чек — AVG(revenue) WHERE revenue > 0. 3) Динамика продаж — GROUP BY DATE_TRUNC(month/day) с ORDER BY. 4) Продажи по регионам/магазинам — GROUP BY region/store. 5) ABC-анализ — ранжирование по выручке с накопительным процентом. 6) Возвраты — WHERE returns > 0 или CASE WHEN. 7) Влияние скидок — GROUP BY discount с AVG(revenue). 8) Конверсия — COUNT с CASE WHEN условиями."),
    ("context", "Ты — Senior Business & Sales Analyst (Старший бизнес-аналитик)."),
    ("context", "Твоя цель — помогать коммерческому директору и отделу маркетинга быстро и точно понимать текущую ситуацию по продажам."),
    ("context", "Ты смотришь на данные через призму выручки, прибыли и эффективности продаж."),
    ("context", "Если данных для ответа на вопрос пользователя нет в базе — прямо и вежливо скажи об этом, не пытайся угадать ответ."),
]


class MigrationTest(unittest.TestCase):
    def setUp(self):
        from app.core.dependencies import db_manager

        self.db = db_manager
        self.db.init_db()
        self.conn = self.db.get_connection()
        self.conn.execute("DELETE FROM assistant_instructions")
        self.conn.execute("DELETE FROM system_settings WHERE setting_key = ?", [d.VERSION_KEY])

    def _insert(self, rows):
        for cat, content in rows:
            self.conn.execute(
                "INSERT INTO assistant_instructions (id, category, content) VALUES (?, ?, ?)",
                [str(uuid.uuid4()), cat, content],
            )

    def _contents(self):
        return sorted(r[0] for r in self.conn.execute("SELECT content FROM assistant_instructions").fetchall())

    def test_old_set_becomes_new_set_and_custom_rule_survives(self):
        self._insert(V1_1_0_DEFAULTS + [("rules", "Валюта — тенге")])
        self.db.init_db()
        expected = sorted([c for _, c in d.DEFAULT_INSTRUCTIONS] + ["Валюта — тенге"])
        self.assertEqual(self._contents(), expected)

    def test_interim_currency_rule_is_removed(self):
        self._insert([("rules", d._interim_currency_rule(settings.LOCAL_CURRENCY)), ("rules", "своё")])
        self.db.init_db()
        self.assertEqual(self._contents(), ["своё"])

    def test_deletion_runs_once(self):
        self._insert([("rules", "своё")])
        self.db.init_db()
        # После миграции пользователь сам вернул старое правило — перезапуск его не удаляет
        self._insert([("context", "Ты смотришь на данные через призму выручки, прибыли и эффективности продаж.")])
        self.db.init_db()
        self.assertEqual(len(self._contents()), 2)

    def test_old_wording_added_after_migration_is_kept_as_is(self):
        self._insert([("rules", "своё")])
        self.db.init_db()
        # Пользователь сам вернул прежнюю редакцию правила — перезапуск не переписывает её в дубль новой
        self._insert([("recommendations", d.READABILITY_RULE_OLD), ("recommendations", d.READABILITY_RULE)])
        self.db.init_db()
        self.assertEqual(self._contents(), sorted(["своё", d.READABILITY_RULE_OLD, d.READABILITY_RULE]))

    def test_defaults_endpoint_matches_seed(self):
        from fastapi.testclient import TestClient

        from app.core.security import create_access_token
        from app.main import app

        headers = {"Authorization": f"Bearer {create_access_token(subject='admin', role='admin')}"}
        got = TestClient(app).get("/api/v1/settings/instructions/defaults", headers=headers).json()["instructions"]
        self.assertEqual([(i["category"], i["content"]) for i in got], d.DEFAULT_INSTRUCTIONS)


if __name__ == "__main__":
    unittest.main()
