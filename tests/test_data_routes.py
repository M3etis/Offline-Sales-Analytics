import unittest

from fastapi.testclient import TestClient

from app.core.dependencies import db_manager
from app.core.security import create_access_token
from app.main import app

ADMIN = {"Authorization": f"Bearer {create_access_token(subject='admin', role='admin')}"}


class DeleteDatasetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_manager.init_db()
        cls.client = TestClient(app)

    def test_invalid_id_is_400_not_500(self):
        r = self.client.delete("/api/v1/data/datasets/../../x", headers=ADMIN)
        self.assertIn(r.status_code, (400, 404))
        r = self.client.delete("/api/v1/data/datasets/ZZZ", headers=ADMIN)
        self.assertEqual(r.status_code, 400)

    def test_legacy_default_dataset_can_be_deleted(self):
        conn = db_manager.get_connection()
        conn.execute("CREATE TABLE IF NOT EXISTS sales (x INTEGER)")
        conn.execute("DELETE FROM datasets WHERE id = 'default'")
        conn.execute("INSERT INTO datasets (id, name, rows, columns, table_name) VALUES ('default', 'old', 0, '[]', 'sales')")
        r = self.client.delete("/api/v1/data/datasets/default", headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        left = conn.execute("SELECT COUNT(*) FROM datasets WHERE id = 'default'").fetchone()[0]
        self.assertEqual(left, 0)
        tables = [t[0] for t in conn.execute("SELECT table_name FROM information_schema.tables").fetchall()]
        self.assertNotIn("sales", tables)


if __name__ == "__main__":
    unittest.main()


class DemoDataTest(unittest.TestCase):
    def test_demo_upload_does_not_touch_repo_demo_file(self):
        import os
        from app.core.config import settings
        repo_file = os.path.join(settings.project_root, "data", "demo", "demo_sales.csv")
        before = os.path.getmtime(repo_file) if os.path.exists(repo_file) else None
        db_manager.init_db()
        r = TestClient(app).post("/api/v1/data/demo", headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertGreater(r.json()["rows_loaded"], 0)
        after = os.path.getmtime(repo_file) if os.path.exists(repo_file) else None
        self.assertEqual(before, after)
        raw = os.path.join(settings.data_full_path, "raw")
        self.assertEqual([f for f in os.listdir(raw) if f.startswith("demo_")], [])


class SyncHandlersStillWorkTest(unittest.TestCase):
    """Обработчики переведены с async def на def (пул потоков) — поведение то же."""

    def test_upload_csv_and_preview(self):
        db_manager.init_db()
        client = TestClient(app)
        csv = "date,region,revenue\n2024-01-01,Алматы,10\n2024-01-02,Астана,20\n".encode("utf-8")
        r = client.post("/api/v1/data/preview", files={"file": ("s.csv", csv, "text/csv")}, headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        r = client.post("/api/v1/data/upload", files={"file": ("s.csv", csv, "text/csv")}, headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["rows_loaded"], 2)

    def test_login(self):
        db_manager.init_db()
        r = TestClient(app).post("/api/v1/auth/login", data={"username": "admin", "password": "admin"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("access_token", r.json())
