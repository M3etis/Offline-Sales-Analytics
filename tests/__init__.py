"""Тесты запускаются на временной базе и каталогах, не трогая data/ и logs/ проекта.

Запуск: venv/bin/python -m unittest discover -s tests -t .
"""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="osa-tests-")
os.environ.setdefault("DB_PATH", os.path.join(_TMP, "test.duckdb"))
os.environ.setdefault("DATA_DIR", os.path.join(_TMP, "data"))
os.environ.setdefault("LOG_DIR", os.path.join(_TMP, "logs"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef0123456789abcdef")
