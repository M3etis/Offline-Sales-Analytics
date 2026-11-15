import asyncio
import datetime
import json
import os
import unittest
from decimal import Decimal
from unittest import mock

from fastapi.testclient import TestClient

from app.api.routes import ai as ai_routes
from app.core.config import settings
from app.core.dependencies import db_manager
from app.core.security import create_access_token
from app.main import app

ADMIN = {"Authorization": f"Bearer {create_access_token(subject='admin', role='admin')}"}


class AskStreamTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_manager.init_db()
        cls.client = TestClient(app)

    def test_done_event_with_dates_and_decimals(self):
        async def fake(*args, **kwargs):
            return {"answer": "ok", "intent": "sql", "processing_time": 0.1, "error": None,
                    "data": [{"month": datetime.date(2024, 3, 1), "revenue": Decimal("10.5")}]}
        with mock.patch.object(ai_routes, "process_question", fake):
            r = self.client.post("/api/v1/ai/ask-stream", json={"question": "по месяцам"}, headers=ADMIN)
        events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
        self.assertEqual(events[-1]["status"], "done")
        self.assertEqual(events[-1]["result"]["data"][0]["month"], "2024-03-01")
        log = os.path.join(settings.log_full_path, "queries.log")
        self.assertTrue(os.path.exists(log))

    def test_background_task_cancelled_when_stream_closes(self):
        started, cancelled = asyncio.Event(), {"v": False}

        async def slow(*args, **kwargs):
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled["v"] = True
                raise

        async def go():
            with mock.patch.object(ai_routes, "process_question", slow):
                req = ai_routes.AskRequest(question="q")
                resp = await ai_routes.ask_question_stream(req, db=None)
                gen = resp.body_iterator
                waiter = asyncio.ensure_future(gen.__anext__())
                await started.wait()
                waiter.cancel()
                try:
                    await waiter
                except (asyncio.CancelledError, StopAsyncIteration):
                    pass
                await gen.aclose()
                await asyncio.sleep(0.05)
                return cancelled["v"]  # до завершения asyncio.run, который сам отменил бы задачу
        self.assertTrue(asyncio.run(go()))


if __name__ == "__main__":
    unittest.main()
