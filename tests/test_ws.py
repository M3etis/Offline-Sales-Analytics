import json
import unittest
import uuid

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.dependencies import db_manager
from app.core.security import create_access_token
from app.main import app


class WebSocketTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_manager.init_db()
        cls.client = TestClient(app)
        cls.token = create_access_token(subject="user", role="user")

    def test_bad_token_closes_with_4001(self):
        with self.client.websocket_connect("/ws/ai?token=garbage") as ws:
            with self.assertRaises(WebSocketDisconnect) as ctx:
                ws.receive_text()
        self.assertEqual(ctx.exception.code, 4001)

    def test_malformed_messages_keep_socket_open(self):
        with self.client.websocket_connect(f"/ws/ai?token={self.token}") as ws:
            for raw in ("[]", "1", '"x"', "{bad", json.dumps({"type": "ask", "question": 5})):
                ws.send_text(raw)
                self.assertEqual(json.loads(ws.receive_text())["type"], "error")
            ws.send_text(json.dumps({"type": "cancel"}))
            self.assertEqual(json.loads(ws.receive_text())["type"], "cancelled")

    def test_foreign_session_rejected(self):
        sid = str(uuid.uuid4())
        db_manager.get_connection().execute("INSERT INTO chat_sessions (id, title, user_id) VALUES (?, 't', 'admin')", [sid])
        with self.client.websocket_connect(f"/ws/ai?token={self.token}") as ws:
            ws.send_text(json.dumps({"type": "ask", "question": "покажи историю", "session_id": sid}))
            msg = json.loads(ws.receive_text())
        self.assertEqual(msg, {"type": "error", "message": "Session not found"})


if __name__ == "__main__":
    unittest.main()
