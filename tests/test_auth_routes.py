import unittest

from fastapi.testclient import TestClient

from app.main import app


class VoiceAuthTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_transcribe_requires_login(self):
        r = self.client.post("/api/v1/voice/transcribe", files={"file": ("a.webm", b"x", "audio/webm")})
        self.assertEqual(r.status_code, 401)

    def test_synthesize_requires_login(self):
        r = self.client.post("/api/v1/voice/synthesize", json={"text": "привет"})
        self.assertEqual(r.status_code, 401)

    def test_synthesize_stream_requires_token(self):
        r = self.client.get("/api/v1/voice/synthesize_stream", params={"text": "привет"})
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()


class TokenTrustTest(unittest.TestCase):
    """Роль берётся из базы, а не из токена; удалённый пользователь теряет доступ сразу."""

    @classmethod
    def setUpClass(cls):
        from app.core.dependencies import db_manager
        db_manager.init_db()
        cls.client = TestClient(app)

    def _auth(self, username, role):
        from app.core.security import create_access_token
        return {"Authorization": f"Bearer {create_access_token(subject=username, role=role)}"}

    def test_forged_admin_role_is_ignored(self):
        r = self.client.get("/api/v1/users", headers=self._auth("user", "admin"))
        self.assertEqual(r.status_code, 403)

    def test_real_admin_passes(self):
        r = self.client.get("/api/v1/users", headers=self._auth("admin", "admin"))
        self.assertEqual(r.status_code, 200)

    def test_unknown_user_rejected(self):
        r = self.client.get("/api/v1/sessions", headers=self._auth("ghost", "admin"))
        self.assertEqual(r.status_code, 401)


class PermissionEnforcementTest(unittest.TestCase):
    """Права разделов проверяются на сервере так же, как в меню."""

    @classmethod
    def setUpClass(cls):
        import uuid
        from app.core.dependencies import db_manager
        db_manager.init_db()
        conn = db_manager.get_connection()
        conn.execute("DELETE FROM users WHERE username = 'viewer'")
        conn.execute(
            "INSERT INTO users (id, username, password_hash, role, permissions) VALUES (?, 'viewer', 'x', 'user', '[\"dashboard\"]')",
            [str(uuid.uuid4())],
        )
        cls.client = TestClient(app)

    def _auth(self, username):
        from app.core.security import create_access_token
        return {"Authorization": f"Bearer {create_access_token(subject=username, role='user')}"}

    def test_without_permission_cannot_change_rules(self):
        r = self.client.post("/api/v1/settings/instructions", json={"instructions": []}, headers=self._auth("viewer"))
        self.assertEqual(r.status_code, 403)
        r = self.client.get("/api/v1/sessions/feedback-messages", headers=self._auth("viewer"))
        self.assertEqual(r.status_code, 403)

    def test_user_list_is_admin_only(self):
        r = self.client.get("/api/v1/settings/users", headers=self._auth("viewer"))
        self.assertEqual(r.status_code, 403)

    def test_reading_rules_still_allowed(self):
        r = self.client.get("/api/v1/settings/instructions", headers=self._auth("viewer"))
        self.assertEqual(r.status_code, 200)
