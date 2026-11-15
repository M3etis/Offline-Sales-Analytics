"""Какую модель скрипт запуска (start.sh / start.ps1) должен проверить и при необходимости скачать.

Тот же выбор, что делает бэкенд при старте (app/services/model_manager.restore_active_model):
модель, выбранная в интерфейсе (system_settings.ollama_model), если она скачана в Ollama,
иначе OLLAMA_MODEL из окружения или .env. Печатает одно имя модели.
Иначе скрипт каждый раз качал бы модель из .env (~9 ГБ), даже когда в интерфейсе выбрана другая.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.services.model_catalog import ACTIVE_MODEL_KEY, normalize_name  # noqa: E402


def saved_model() -> str | None:
    db = Path(settings.db_full_path)
    if not db.exists():
        return None
    try:
        import duckdb

        with duckdb.connect(str(db), read_only=True) as conn:
            row = conn.execute(
                "SELECT setting_value FROM system_settings WHERE setting_key = ?", [ACTIVE_MODEL_KEY]
            ).fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None  # БД занята запущенным бэкендом или ещё без таблицы настроек


def installed_models() -> set[str]:
    with urllib.request.urlopen(f"{settings.OLLAMA_URL}/api/tags", timeout=5) as response:
        models = json.load(response).get("models", [])
    return {normalize_name(m.get("name", "")) for m in models}


def main() -> None:
    saved = saved_model()
    if saved:
        try:
            if normalize_name(saved) in installed_models():
                print(saved)
                return
        except Exception:
            pass  # Ollama не отвечает — как и бэкенд, выбор не применяем
    print(settings.OLLAMA_MODEL)


if __name__ == "__main__":
    main()
