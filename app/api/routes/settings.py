from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
import duckdb
from typing import List, Optional
from pydantic import BaseModel
import uuid

from app.core.dependencies import get_db, get_current_admin
from app.core.config import settings as app_settings
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])

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

@router.post("/instructions")
def save_instructions(payload: InstructionsList, db_conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    """Save assistant instructions (replaces existing)."""
    try:
        db_conn.execute("DELETE FROM assistant_instructions")
        
        for item in payload.instructions:
            item_id = item.id or str(uuid.uuid4())
            db_conn.execute(
                "INSERT INTO assistant_instructions (id, category, content) VALUES (?, ?, ?)",
                [item_id, item.category, item.content]
            )
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Failed to save instructions: {e}")
        raise HTTPException(status_code=500, detail="Failed to save instructions")

@router.get("/config")
def get_config(db_conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    """Get all system config settings."""
    try:
        result = db_conn.execute("SELECT setting_key, setting_value FROM system_settings").fetchall()
        config = {row[0]: row[1] for row in result}
        return config
    except Exception as e:
        logger.error(f"Failed to fetch config: {e}")
        return {}

@router.post("/config")
def save_config(payload: ConfigUpdate, db_conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    """Save a system config setting."""
    try:
        db_conn.execute(
            "INSERT INTO system_settings (setting_key, setting_value) VALUES (?, ?) ON CONFLICT(setting_key) DO UPDATE SET setting_value = ?",
            [payload.key, payload.value, payload.value]
        )
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Failed to save config: {e}")
        raise HTTPException(status_code=500, detail="Failed to save config")


@router.post("/tts-voice")
def save_tts_voice(payload: ConfigUpdate, db_conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    """Save TTS voice setting."""
    if payload.key != "tts_voice":
        raise HTTPException(status_code=400, detail="Invalid setting key")
    
    try:
        db_conn.execute(
            "INSERT INTO system_settings (setting_key, setting_value) VALUES (?, ?) ON CONFLICT (setting_key) DO UPDATE SET setting_value = ?",
            (payload.key, payload.value, payload.value)
        )
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

@router.get("/users")
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


@router.post("/column-dictionary")
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


@router.put("/column-dictionary/{item_id}")
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


@router.delete("/column-dictionary/{item_id}")
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


@router.post("/column-dictionary/restore-defaults")
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
