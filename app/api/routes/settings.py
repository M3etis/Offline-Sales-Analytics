from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
import duckdb
from typing import List, Optional
from pydantic import BaseModel
import uuid

from app.core.dependencies import get_db, get_current_admin, require_permission
from app.core.config import settings as app_settings
from app.services.model_catalog import ACTIVE_MODEL_KEY
from app.services.currency import (
    LOCAL_CURRENCY_CHANGED_KEY,
    LOCAL_CURRENCY_DEFAULT_KEY,
    LOCAL_CURRENCY_KEY,
    MAX_CURRENCY_LEN,
    clean_currency,
    default_currency,
    get_local_currency,
    mark_currency_changed,
)
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])

# Изменение правил ассистента и словаря колонок — право «settings_assistant», как у страниц в меню
_assistant = Depends(require_permission("settings_assistant"))

#: Ключи system_settings, у которых есть своя ручка с проверкой прав
PROTECTED_CONFIG_KEYS = {ACTIVE_MODEL_KEY, LOCAL_CURRENCY_DEFAULT_KEY, LOCAL_CURRENCY_CHANGED_KEY}

class InstructionItem(BaseModel):
    id: Optional[str] = None
    category: str
    content: str

class InstructionsList(BaseModel):
    instructions: List[InstructionItem]

class ConfigItem(BaseModel):
    key: str
    value: str

class ConfigUpdate(BaseModel):
    key: str
    value: str

class ColumnDictItem(BaseModel):
    id: Optional[str] = None
    en_name: str
    ru_name: str
    category: str = "other"

class ColumnDictList(BaseModel):
    items: List[ColumnDictItem]

def _reset_voice_cache() -> None:
    """Голос TTS кешируется при первом синтезе — без сброса новый голос включался только после перезапуска."""
    from app.services.voice import voice_service
    voice_service.invalidate_voice_cache()


@router.get("/instructions", response_model=InstructionsList)
def get_instructions(db_conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    """Get all assistant instructions."""
    try:
        result = db_conn.execute("SELECT id, category, content FROM assistant_instructions ORDER BY created_at ASC").fetchall()
        instructions = [
            {"id": row[0], "category": row[1], "content": row[2]}
            for row in result
        ]
        return {"instructions": instructions}
    except Exception as e:
        logger.error(f"Failed to fetch instructions: {e}")
        return {"instructions": []}

@router.get("/instructions/defaults", response_model=InstructionsList)
def get_default_instructions():
    """Стандартный набор правил — единый для сидинга базы и кнопки «Стандартные правила»."""
    from app.db.default_instructions import DEFAULT_INSTRUCTIONS
    return {"instructions": [{"category": cat, "content": content} for cat, content in DEFAULT_INSTRUCTIONS]}


@router.post("/instructions", dependencies=[_assistant])
def save_instructions(payload: InstructionsList, db_conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    """Save assistant instructions (replaces existing)."""
    # Одной транзакцией: раньше ошибка на середине (например, повтор id) оставляла
    # таблицу правил пустой — DELETE уже выполнился
    began = False
    try:
        db_conn.execute("BEGIN")
        began = True
        db_conn.execute("DELETE FROM assistant_instructions")

        for item in payload.instructions:
            item_id = item.id or str(uuid.uuid4())
            db_conn.execute(
                "INSERT INTO assistant_instructions (id, category, content) VALUES (?, ?, ?)",
                [item_id, item.category, item.content]
            )
        db_conn.execute("COMMIT")
        return {"status": "success"}
    except Exception as e:
        if began:
            try:
                db_conn.execute("ROLLBACK")
            except Exception:
                pass
        logger.error(f"Failed to save instructions: {e}")
        raise HTTPException(status_code=500, detail="Failed to save instructions")

@router.get("/column-labels")
def get_column_labels(db_conn: duckdb.DuckDBPyConnection = Depends(get_db)) -> dict[str, str]:
    """Подписи колонок для таблиц и графиков ответа: {ключ в нижнем регистре: подпись}."""
    from app.domain.ai.data_format import column_labels
    from app.services.column_dict import user_aliases

    return column_labels(user_aliases(db_conn))


@router.get("/config")
def get_config(db_conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    """Get all system config settings."""
    try:
        result = db_conn.execute("SELECT setting_key, setting_value FROM system_settings").fetchall()
        config = {row[0]: row[1] for row in result}
    except Exception as e:
        logger.error(f"Failed to fetch config: {e}")
        config = {}
    # Валюта по умолчанию из .env — интерфейс показывает её, пока своя не задана
    config[LOCAL_CURRENCY_DEFAULT_KEY] = default_currency()
    return config

@router.post("/config", dependencies=[_assistant])
def save_config(payload: ConfigUpdate, db_conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    """Save a system config setting."""
    if payload.key in PROTECTED_CONFIG_KEYS:
        # Активная модель меняется только через POST /ai/models/active (только админ)
        raise HTTPException(status_code=403, detail="Этот параметр нельзя менять здесь")
    if payload.key == LOCAL_CURRENCY_KEY:
        return _save_local_currency(payload.value, db_conn)
    try:
        db_conn.execute(
            "INSERT INTO system_settings (setting_key, setting_value) VALUES (?, ?) ON CONFLICT(setting_key) DO UPDATE SET setting_value = ?",
            [payload.key, payload.value, payload.value]
        )
        if payload.key == "tts_voice":
            _reset_voice_cache()
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Failed to save config: {e}")
        raise HTTPException(status_code=500, detail="Failed to save config")


def _save_local_currency(value: str, db_conn: duckdb.DuckDBPyConnection) -> dict:
    """Сохранить валюту ответов. Пустое значение — вернуться к валюте из .env."""
    currency = clean_currency(value)
    if len(currency) > MAX_CURRENCY_LEN:
        raise HTTPException(status_code=400, detail=f"Валюта — не длиннее {MAX_CURRENCY_LEN} символов")
    previous = get_local_currency(db_conn)
    try:
        if currency:
            db_conn.execute(
                "INSERT INTO system_settings (setting_key, setting_value) VALUES (?, ?) ON CONFLICT(setting_key) DO UPDATE SET setting_value = ?",
                [LOCAL_CURRENCY_KEY, currency, currency],
            )
        else:
            db_conn.execute("DELETE FROM system_settings WHERE setting_key = ?", [LOCAL_CURRENCY_KEY])
        # Кэш ответов сверяет дату ответа с этой отметкой: ответы до смены — в прежней валюте.
        # Только при реальной смене: «Сохранить» на странице всегда шлёт валюту, и кэш сбрасывался бы зря
        if get_local_currency(db_conn) != previous:
            mark_currency_changed(db_conn)
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Failed to save local currency: {e}")
        raise HTTPException(status_code=500, detail="Failed to save config")


@router.post("/tts-voice", dependencies=[_assistant])
def save_tts_voice(payload: ConfigUpdate, db_conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    """Save TTS voice setting."""
    if payload.key != "tts_voice":
        raise HTTPException(status_code=400, detail="Invalid setting key")
    
    try:
        db_conn.execute(
            "INSERT INTO system_settings (setting_key, setting_value) VALUES (?, ?) ON CONFLICT (setting_key) DO UPDATE SET setting_value = ?",
            (payload.key, payload.value, payload.value)
        )
        _reset_voice_cache()
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Failed to save TTS voice: {e}")
        raise HTTPException(status_code=500, detail="Failed to save settings")

@router.delete("/clear-sessions")
def clear_all_sessions(user_id: str = None, current_user: dict = Depends(get_current_admin), db_conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    """Delete chat sessions and messages, optionally for a specific user."""
    try:
        if user_id:
            session_ids = db_conn.execute(
                "SELECT id FROM chat_sessions WHERE user_id = ?", [user_id]
            ).fetchall()
            ids = [row[0] for row in session_ids]
            if ids:
                placeholders = ', '.join(['?'] * len(ids))
                db_conn.execute(f"DELETE FROM chat_messages WHERE session_id IN ({placeholders})", ids)
            db_conn.execute("DELETE FROM chat_sessions WHERE user_id = ?", [user_id])
            message = f"Sessions and messages for user '{user_id}' have been deleted."
        else:
            db_conn.execute("DELETE FROM chat_messages")
            db_conn.execute("DELETE FROM chat_sessions")
            message = "All sessions and messages have been deleted."
        return {"status": "success", "message": message}
    except Exception as e:
        logger.error(f"Failed to clear sessions: {e}")
        raise HTTPException(status_code=500, detail="Failed to clear sessions")

@router.delete("/clear-cache")
def clear_llm_cache(user_id: str = None, current_user: dict = Depends(get_current_admin), db_conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    """Delete LLM cache entries, optionally for a specific user."""
    try:
        if user_id:
            db_conn.execute(
                """DELETE FROM llm_cache WHERE query_hash IN (
                    SELECT query_hash FROM llm_cache
                    WHERE response_json LIKE ?
                )""",
                [f'%{user_id}%']
            )
            # Also clear chat_messages data_summary for this user's sessions
            db_conn.execute(
                """UPDATE chat_messages SET data_summary = NULL
                   WHERE session_id IN (SELECT id FROM chat_sessions WHERE user_id = ?)""",
                [user_id]
            )
            message = f"LLM cache for user '{user_id}' has been cleared."
        else:
            db_conn.execute("DELETE FROM llm_cache")
            message = "All LLM cache has been cleared."
        return {"status": "success", "message": message}
    except Exception as e:
        logger.error(f"Failed to clear cache: {e}")
        raise HTTPException(status_code=500, detail="Failed to clear cache")

@router.get("/users", dependencies=[Depends(get_current_admin)])
def get_users_list(db_conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    """Get list of all users for admin operations."""
    try:
        result = db_conn.execute("SELECT id, username, full_name, role FROM users ORDER BY username").fetchall()
        users = [
            {"id": row[0], "username": row[1], "full_name": row[2] or row[1], "role": row[3]}
            for row in result
        ]
        return users
    except Exception as e:
        logger.error(f"Failed to fetch users: {e}")
        return []

# Column Dictionary endpoints
@router.get("/column-dictionary")
def get_column_dictionary(
    category: str = None,
    search: str = None,
    db_conn: duckdb.DuckDBPyConnection = Depends(get_db)
):
    """Get column dictionary entries, optionally filtered by category or search term."""
    try:
        query = "SELECT id, en_name, ru_name, category FROM column_dictionary"
        params = []
        conditions = []
        
        if category:
            conditions.append("category = ?")
            params.append(category)
        
        if search:
            conditions.append("(LOWER(en_name) LIKE ? OR LOWER(ru_name) LIKE ?)")
            params.extend([f"%{search.lower()}%", f"%{search.lower()}%"])
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        query += " ORDER BY category, en_name"
        
        result = db_conn.execute(query, params).fetchall()
        items = [
            {"id": row[0], "en_name": row[1], "ru_name": row[2], "category": row[3]}
            for row in result
        ]
        return {"items": items, "total": len(items)}
    except Exception as e:
        logger.error(f"Failed to fetch column dictionary: {e}")
        return {"items": [], "total": 0}


@router.post("/column-dictionary", dependencies=[_assistant])
def add_column_dict_item(
    item: ColumnDictItem,
    db_conn: duckdb.DuckDBPyConnection = Depends(get_db)
):
    """Add a new column dictionary entry."""
    try:
        item_id = item.id or str(uuid.uuid4())
        db_conn.execute(
            "INSERT INTO column_dictionary (id, en_name, ru_name, category) VALUES (?, ?, ?, ?)",
            [item_id, item.en_name.lower().strip(), item.ru_name.strip(), item.category]
        )
        return {"status": "success", "id": item_id}
    except Exception as e:
        logger.error(f"Failed to add column dict item: {e}")
        raise HTTPException(status_code=500, detail="Failed to add entry")


@router.put("/column-dictionary/{item_id}", dependencies=[_assistant])
def update_column_dict_item(
    item_id: str,
    item: ColumnDictItem,
    db_conn: duckdb.DuckDBPyConnection = Depends(get_db)
):
    """Update an existing column dictionary entry."""
    try:
        db_conn.execute(
            "UPDATE column_dictionary SET en_name = ?, ru_name = ?, category = ? WHERE id = ?",
            [item.en_name.lower().strip(), item.ru_name.strip(), item.category, item_id]
        )
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Failed to update column dict item: {e}")
        raise HTTPException(status_code=500, detail="Failed to update entry")


@router.delete("/column-dictionary/{item_id}", dependencies=[_assistant])
def delete_column_dict_item(
    item_id: str,
    db_conn: duckdb.DuckDBPyConnection = Depends(get_db)
):
    """Delete a column dictionary entry."""
    try:
        db_conn.execute("DELETE FROM column_dictionary WHERE id = ?", [item_id])
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Failed to delete column dict item: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete entry")


@router.post("/column-dictionary/restore-defaults", dependencies=[_assistant])
def restore_column_dict_defaults(
    db_conn: duckdb.DuckDBPyConnection = Depends(get_db)
):
    """Restore column dictionary to defaults from column_dict.py."""
    try:
        from app.services.column_dict import COLUMN_TRANSLATIONS
        
        # Clear existing entries
        db_conn.execute("DELETE FROM column_dictionary")
        
        # Insert defaults
        for en_name, ru_name in COLUMN_TRANSLATIONS.items():
            category = "other"
            if any(kw in en_name for kw in ["date", "time", "created", "updated", "birth", "hire"]):
                category = "dates"
            elif any(kw in en_name for kw in ["price", "cost", "revenue", "profit", "margin", "amount", "total", "budget", "spend", "fee", "tax", "discount", "balance", "credit", "debit"]):
                category = "finance"
            elif any(kw in en_name for kw in ["quantity", "qty", "count", "stock", "inventory", "volume", "weight", "size"]):
                category = "quantity"
            elif any(kw in en_name for kw in ["id", "code", "number", "barcode", "sku"]):
                category = "identifiers"
            elif any(kw in en_name for kw in ["name", "title", "description", "address", "city", "country", "email", "phone", "contact"]):
                category = "text"
            elif any(kw in en_name for kw in ["category", "type", "status", "segment", "group", "channel", "source", "class"]):
                category = "classification"
            elif any(kw in en_name for kw in ["sales", "lead", "deal", "pipeline", "funnel", "opportunity"]):
                category = "sales"
            elif any(kw in en_name for kw in ["impression", "click", "ctr", "cpc", "cpm", "traffic", "conversion", "bounce"]):
                category = "marketing"
            elif any(kw in en_name for kw in ["cash", "ebitda", "equity", "asset", "liability", "debt", "depreciation", "dividend"]):
                category = "finance"
            elif any(kw in en_name for kw in ["delivery", "shipping", "warehouse", "return", "transit", "fulfillment"]):
                category = "logistics"
            elif any(kw in en_name for kw in ["customer", "client", "loyalty", "ltv", "churn", "retention", "rfm", "satisfaction"]):
                category = "customers"
            
            db_conn.execute(
                "INSERT INTO column_dictionary (id, en_name, ru_name, category) VALUES (?, ?, ?, ?)",
                [str(uuid.uuid4()), en_name, ru_name, category]
            )
        
        count = len(COLUMN_TRANSLATIONS)
        logger.info(f"Column dictionary restored to defaults: {count} entries")
        return {"status": "success", "count": count}
    except Exception as e:
        logger.error(f"Failed to restore column dictionary defaults: {e}")
        raise HTTPException(status_code=500, detail="Failed to restore defaults")


@router.get("/column-dictionary/categories")
def get_column_dict_categories(
    db_conn: duckdb.DuckDBPyConnection = Depends(get_db)
):
    """Get all unique categories in the column dictionary."""
    try:
        result = db_conn.execute(
            "SELECT DISTINCT category FROM column_dictionary ORDER BY category"
        ).fetchall()
        categories = [row[0] for row in result]
        return {"categories": categories}
    except Exception as e:
        logger.error(f"Failed to fetch categories: {e}")
        return {"categories": []}
