import asyncio
import json
import unittest
import uuid
from unittest import mock

import httpx

from app.core.dependencies import db_manager
from app.services import llm


class _ChunkedStream(httpx.AsyncByteStream):
    """Отдаёт NDJSON кусками, разрезая JSON-строки посередине — как это бывает в сети."""

    def __init__(self, payload: bytes, size: int):
        self.payload, self.size = payload, size

    async def __aiter__(self):
        for i in range(0, len(self.payload), self.size):
            yield self.payload[i:i + self.size]


class GenerateStreamTest(unittest.TestCase):
    def test_tokens_split_across_chunks_are_kept(self):
        words = ["Выручка ", "выросла ", "на ", "12%", " [CHART_KEY:bar]"]
        payload = "".join(json.dumps({"response": w, "done": False}, ensure_ascii=False) + "\n" for w in words)
        payload += json.dumps({"response": "", "done": True}) + "\n"
        transport = httpx.MockTransport(lambda req: httpx.Response(200, stream=_ChunkedStream(payload.encode(), 7)))

        async def go():
            client = llm.OllamaClient()
            with mock.patch.object(client, "_get_client", return_value=httpx.AsyncClient(transport=transport)):
                return [t async for t in client.generate_stream("q")]
        self.assertEqual("".join(asyncio.run(go())), "".join(words))


class ProcessQuestionHistoryTest(unittest.TestCase):
    def test_question_saved_once_and_error_answer_saved(self):
        db_manager.init_db()
        conn = db_manager.get_connection()
        sid = str(uuid.uuid4())
        conn.execute("INSERT INTO chat_sessions (id, title, user_id) VALUES (?, 't', 'admin')", [sid])

        async def bad_sql(*a, **k):
            return "DROP TABLE users"

        async def no_fix(*a, **k):
            return "DROP TABLE users"

        # Вопрос без типового расчёта — идёт в SQL; модель упорно возвращает запрещённый SQL
        with mock.patch.object(llm.ollama_client, "generate_sql", bad_sql), \
             mock.patch.object(llm.ollama_client, "fix_sql", no_fix):
            res = asyncio.run(llm.process_question(f"уникальный вопрос {sid}", conn, session_id=sid))
        self.assertTrue(res.get("error"))
        roles = [r[0] for r in conn.execute("SELECT role FROM chat_messages WHERE session_id = ? ORDER BY created_at", [sid]).fetchall()]
        self.assertEqual(roles, ["user", "assistant"])


if __name__ == "__main__":
    unittest.main()
