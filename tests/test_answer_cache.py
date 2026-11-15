import asyncio
import json
import unittest
import uuid
from unittest import mock

from app.core.dependencies import db_manager
from app.services import llm, streaming


class AnswerCacheTest(unittest.TestCase):
    """Кеш ответов: тот же вопрос, тот же набор, без контекста диалога и без дизлайка."""

    @classmethod
    def setUpClass(cls):
        db_manager.init_db()

    def setUp(self):
        self.conn = db_manager.get_connection()
        self.conn.execute("DELETE FROM chat_messages")
        self.conn.execute("DELETE FROM chat_sessions")
        self.conn.execute("DELETE FROM system_settings WHERE setting_key IN ('local_currency', 'local_currency_changed_at')")
        self.question = f"выручка по регионам {uuid.uuid4().hex[:6]}"

    def tearDown(self):
        self.conn.execute("DELETE FROM system_settings WHERE setting_key IN ('local_currency', 'local_currency_changed_at')")

    def _cached_answer(self, dataset_id, feedback=None, content="ответ", signature=None, contextual=None):
        sid, mid = str(uuid.uuid4()), str(uuid.uuid4())
        self.conn.execute("INSERT INTO chat_sessions (id, title, dataset_id, user_id) VALUES (?, 't', ?, 'admin')", [sid, dataset_id])
        # Подпись условий (модель, правила, подробность) — как у вопроса без «подробно» в _ask
        summary = {"question": self.question, "data": [{"x": 1}], "sql": "SELECT 1",
                   "cache_signature": signature or llm.cache_signature(self.conn, False)}
        if contextual is not None:
            summary["contextual"] = contextual
        summary = json.dumps(summary, ensure_ascii=False)
        self.conn.execute(
            "INSERT INTO chat_messages (id, session_id, role, content, data_summary, feedback, created_at) "
            "VALUES (?, ?, 'assistant', ?, ?, ?, CURRENT_TIMESTAMP + INTERVAL 1 HOUR)",
            [mid, sid, content, summary, feedback],
        )

    def _cached_result(self, dataset_id):
        sent = []

        async def ws_send(msg):
            sent.append(msg)

        async def go():
            with mock.patch.object(streaming.pipeline, "prepare", side_effect=RuntimeError("stop")):
                await streaming.process_question_streaming(
                    ws_send=ws_send, cancel_event=asyncio.Event(), clarification_queue=asyncio.Queue(),
                    question=self.question, db_conn=self.conn, dataset_id=dataset_id, session_id=None,
                )
        asyncio.run(go())
        return next((m["data"] for m in sent if m.get("type") == "result" and (m.get("data") or {}).get("is_cached")), None)

    def test_cached_answer_currency_is_normalized(self):
        from app.services.currency import LOCAL_CURRENCY_KEY
        # Валюту задаём явно, а не берём из .env: при LOCAL_CURRENCY=рубли «рублей» в ответе верны
        # и нормализатор их не трогает. Ключ стирают setUp/tearDown; время смены не пишем — иначе кэш
        # счёл бы ответ устаревшим
        self.conn.execute(
            "INSERT INTO system_settings (setting_key, setting_value) VALUES (?, ?)", [LOCAL_CURRENCY_KEY, "тенге"]
        )
        self._cached_answer("d1", content="Выручка 300 рублей.")
        result = self._cached_result("d1")
        self.assertIsNotNone(result)
        self.assertTrue(result["answer"].endswith("Выручка 300 тенге."), result["answer"])

    def test_currency_change_invalidates_cache(self):
        from datetime import datetime, timedelta
        from app.services.currency import mark_currency_changed
        self._cached_answer("d1")  # ответ «из будущего»: +1 час
        mark_currency_changed(self.conn, datetime.now() + timedelta(hours=2))
        self.assertFalse(self._ask("d1"))

    def test_saving_same_currency_keeps_cache(self):
        from fastapi.testclient import TestClient
        from app.core.security import create_access_token
        from app.main import app
        from app.services.currency import currency_changed_at, default_currency
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {create_access_token(subject='admin', role='admin')}"}
        save = lambda v: client.post("/api/v1/settings/config", json={"key": "local_currency", "value": v}, headers=headers)  # noqa: E731
        # Значение, которое точно не совпадает с валютой по умолчанию из .env
        cur = "сом" if default_currency() != "сом" else "сум"
        self.assertEqual(save(cur).status_code, 200)
        first = currency_changed_at(self.conn)
        self.assertIsNotNone(first)
        self.assertEqual(save(f" {cur} ").status_code, 200)
        self.assertEqual(currency_changed_at(self.conn), first)

    def _ask(self, dataset_id, session_id=None):
        sent = []

        async def ws_send(msg):
            sent.append(msg)

        async def go():
            # Дальше кеша не идём: без кеша конвейер упадёт на подготовке данных, а не пойдёт в LLM
            with mock.patch.object(streaming.pipeline, "prepare", side_effect=RuntimeError("stop")):
                await streaming.process_question_streaming(
                    ws_send=ws_send, cancel_event=asyncio.Event(), clarification_queue=asyncio.Queue(),
                    question=self.question, db_conn=self.conn, dataset_id=dataset_id, session_id=session_id,
                )
        asyncio.run(go())
        return any(m.get("type") == "result" and (m.get("data") or {}).get("is_cached") for m in sent)

    def test_hit_for_same_dataset(self):
        self._cached_answer("d1")
        self.assertTrue(self._ask("d1"))

    def test_no_hit_for_other_dataset(self):
        self._cached_answer("d1")
        self.assertFalse(self._ask("d2"))

    def test_other_signature_not_reused(self):
        # Ответ, сохранённый при другой модели, других правилах или прежней логике ответов
        self._cached_answer("d1", signature="old")
        self.assertFalse(self._ask("d1"))

    def test_contextual_answer_not_reused(self):
        # Ответ на вопрос с контекстом диалога: тот же текст вопроса в другой сессии значит другое
        self._cached_answer("d1", contextual=True)
        self.assertFalse(self._ask("d1"))

    def test_disliked_answer_not_reused(self):
        self._cached_answer("d1", feedback="negative")
        self.assertFalse(self._ask("d1"))

    def test_no_hit_inside_dialog(self):
        self._cached_answer("d1")
        sid = str(uuid.uuid4())
        self.conn.execute("INSERT INTO chat_sessions (id, title, dataset_id, user_id) VALUES (?, 't', 'd1', 'admin')", [sid])
        self.conn.execute("INSERT INTO chat_messages (id, session_id, role, content) VALUES (?, ?, 'user', 'прошлый вопрос')", [str(uuid.uuid4()), sid])
        self.assertFalse(self._ask("d1", session_id=sid))


if __name__ == "__main__":
    unittest.main()
