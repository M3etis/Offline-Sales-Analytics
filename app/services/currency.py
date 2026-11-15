"""Локальная валюта: в ней подписываются суммы, если в данных валюта не указана.

Значение из настроек приложения (system_settings) перекрывает LOCAL_CURRENCY из .env.
"""
from __future__ import annotations

import logging
from datetime import datetime

from app.core.config import settings

logger = logging.getLogger(__name__)

#: Ключ system_settings с валютой, заданной в интерфейсе
LOCAL_CURRENCY_KEY = "local_currency"
#: Ключ ответа GET /settings/config с валютой по умолчанию (из .env) — только для чтения
LOCAL_CURRENCY_DEFAULT_KEY = "local_currency_default"
#: Когда валюту в последний раз меняли: ответы из кэша старше этого момента подписаны прежней валютой
LOCAL_CURRENCY_CHANGED_KEY = "local_currency_changed_at"
#: Валюта попадает в промпт — длинный текст туда не пускаем
MAX_CURRENCY_LEN = 32


def default_currency() -> str:
    return (settings.LOCAL_CURRENCY or "").strip() or "тенге"


def clean_currency(value: str) -> str:
    """Нормализовать ввод: убрать переводы строк и лишние пробелы."""
    return " ".join((value or "").split())


def get_local_currency(db_conn=None) -> str:
    """Валюта из настроек приложения, иначе из .env."""
    if db_conn is not None:
        try:
            row = db_conn.execute(
                "SELECT setting_value FROM system_settings WHERE setting_key = ?",
                [LOCAL_CURRENCY_KEY],
            ).fetchone()
            if row and clean_currency(row[0]):
                return clean_currency(row[0])
        except Exception as e:
            logger.warning(f"Failed to read local currency: {e}")
    return default_currency()


def currency_changed_at(db_conn) -> datetime | None:
    """Момент последней смены валюты в настройках; None — не меняли."""
    try:
        row = db_conn.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key = ?",
            [LOCAL_CURRENCY_CHANGED_KEY],
        ).fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None
    except Exception as e:
        logger.warning(f"Failed to read currency change time: {e}")
        return None


def mark_currency_changed(db_conn, when: datetime | None = None) -> None:
    """Запомнить смену валюты — кэш ответов перестанет отдавать ответы в прежней валюте."""
    value = (when or datetime.now()).isoformat()
    db_conn.execute(
        "INSERT INTO system_settings (setting_key, setting_value) VALUES (?, ?) "
        "ON CONFLICT (setting_key) DO UPDATE SET setting_value = ?",
        [LOCAL_CURRENCY_CHANGED_KEY, value, value],
    )
