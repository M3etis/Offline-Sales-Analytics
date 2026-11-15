import unittest

from fastapi.testclient import TestClient

from app.core.dependencies import db_manager
from app.core.security import create_access_token
from app.main import app

ADMIN = {"Authorization": f"Bearer {create_access_token(subject='admin', role='admin')}"}


class SaveInstructionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_manager.init_db()
        cls.client = TestClient(app)

    def _rules(self):
        return self.client.get("/api/v1/settings/instructions", headers=ADMIN).json()["instructions"]

    def test_failed_save_keeps_previous_rules(self):
        ok = {"instructions": [{"id": "r1", "category": "general", "content": "Валюта — тенге"}]}
        self.assertEqual(self.client.post("/api/v1/settings/instructions", json=ok, headers=ADMIN).status_code, 200)
        dup = {"instructions": [
            {"id": "same", "category": "general", "content": "a"},
            {"id": "same", "category": "general", "content": "b"},
        ]}
        self.assertEqual(self.client.post("/api/v1/settings/instructions", json=dup, headers=ADMIN).status_code, 500)
        self.assertEqual([r["content"] for r in self._rules()], ["Валюта — тенге"])


if __name__ == "__main__":
    unittest.main()


class TtsVoiceCacheTest(unittest.TestCase):
    def test_saving_voice_resets_cache(self):
        from app.services.voice import voice_service
        db_manager.init_db()
        client = TestClient(app)
        voice_service._cached_voice = "ru-RU-SvetlanaNeural"
        r = client.post("/api/v1/settings/config", json={"key": "tts_voice", "value": "ru-RU-DmitryNeural"}, headers=ADMIN)
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(voice_service._cached_voice)
        self.assertEqual(voice_service._get_voice(), "ru-RU-DmitryNeural")
