import unittest
import uuid

from fastapi.testclient import TestClient

from app.core.dependencies import db_manager
from app.core.security import create_access_token
from app.main import app


class FeedbackOwnershipTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_manager.init_db()
        conn = db_manager.get_connection()
        cls.sid, cls.mid = str(uuid.uuid4()), str(uuid.uuid4())
        conn.execute("INSERT INTO chat_sessions (id, user_id, title) VALUES (?, 'admin', 't')", [cls.sid])
        conn.execute(
            "INSERT INTO chat_messages (id, session_id, role, content) VALUES (?, ?, 'assistant', 'a')",
            [cls.mid, cls.sid],
        )
        cls.client = TestClient(app)

    def _feedback(self, username, value):
        headers = {"Authorization": f"Bearer {create_access_token(subject=username, role='user')}"}
        return self.client.post("/api/v1/sessions/feedback", json={"message_id": self.mid, "feedback": value}, headers=headers)

    def _stored(self):
        return db_manager.get_connection().execute("SELECT feedback FROM chat_messages WHERE id = ?", [self.mid]).fetchone()[0]

    def test_other_user_cannot_rate(self):
        self.assertEqual(self._feedback("user", "negative").status_code, 200)
        self.assertIsNone(self._stored())

    def test_owner_can_rate(self):
        self.assertEqual(self._feedback("admin", "positive").status_code, 200)
        self.assertEqual(self._stored(), "positive")


if __name__ == "__main__":
    unittest.main()
