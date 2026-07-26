from fastapi import APIRouter, Depends, HTTPException, status
import duckdb
import uuid
import json
from typing import List, Optional
from pydantic import BaseModel

from app.core.dependencies import get_db, get_current_user
from app.core.security import get_password_hash
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])

# --- Pydantic models ---

class UserOut(BaseModel):
    id: str
    username: str
    full_name: str
    role: str
    permissions: List[str]
    created_at: str

class UserCreate(BaseModel):
    username: str
    password: str
    full_name: str = ""
    role: str = "user"
    permissions: List[str] = ["dashboard", "analyst", "sessions", "details", "settings_status", "settings_data", "settings_assistant", "settings_feedback", "settings_cleanup"]

    def model_post_init(self, __context):
        if len(self.password) < 8:
            raise ValueError("Пароль должен содержать минимум 8 символов")
        if not any(c.isupper() for c in self.password):
            raise ValueError("Пароль должен содержать хотя бы одну заглавную букву")
        if not any(c.islower() for c in self.password):
            raise ValueError("Пароль должен содержать хотя бы одну строчную букву")
        if not any(c.isdigit() for c in self.password):
            raise ValueError("Пароль должен содержать хотя бы одну цифру")

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    role: Optional[str] = None
    permissions: Optional[List[str]] = None

class PasswordUpdate(BaseModel):
    password: str

    def model_post_init(self, __context):
        if len(self.password) < 8:
            raise ValueError("Пароль должен содержать минимум 8 символов")
        if not any(c.isupper() for c in self.password):
            raise ValueError("Пароль должен содержать хотя бы одну заглавную букву")
        if not any(c.islower() for c in self.password):
            raise ValueError("Пароль должен содержать хотя бы одну строчную букву")
        if not any(c.isdigit() for c in self.password):
            raise ValueError("Пароль должен содержать хотя бы одну цифру")

# --- Endpoints ---

@router.get("", response_model=List[UserOut])
def list_users(db_conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    """List all users (without password hashes)."""
    try:
        rows = db_conn.execute(
            "SELECT id, username, role, permissions, created_at, full_name FROM users ORDER BY created_at"
        ).fetchall()
        
        users = []
        for row in rows:
            perms = []
            if row[3]:
                try:
                    perms = json.loads(row[3])
                except (json.JSONDecodeError, TypeError):
                    perms = []
            
            users.append(UserOut(
                id=row[0],
                username=row[1],
                role=row[2],
                permissions=perms,
                created_at=str(row[4]) if row[4] else "",
                full_name=row[5] if row[5] else ""
            ))
        return users
    except Exception as e:
        logger.error(f"Failed to list users: {e}")
        raise HTTPException(status_code=500, detail="Failed to list users")


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(user: UserCreate, db_conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    """Create a new user."""
    # Check if username already exists
    existing = db_conn.execute(
        "SELECT id FROM users WHERE username = ?", [user.username]
    ).fetchone()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Пользователь '{user.username}' уже существует"
        )
    
    try:
        user_id = str(uuid.uuid4())
        password_hash = get_password_hash(user.password)
        permissions_json = json.dumps(user.permissions, ensure_ascii=False)
        
        db_conn.execute(
            "INSERT INTO users (id, username, password_hash, role, permissions, full_name) VALUES (?, ?, ?, ?, ?, ?)",
            [user_id, user.username, password_hash, user.role, permissions_json, user.full_name]
        )
        
        # Fetch the created user
        row = db_conn.execute(
            "SELECT id, username, role, permissions, created_at, full_name FROM users WHERE id = ?",
            [user_id]
        ).fetchone()
        
        perms = json.loads(row[3]) if row[3] else []
        
        return UserOut(
            id=row[0],
            username=row[1],
            role=row[2],
            permissions=perms,
            created_at=str(row[4]) if row[4] else "",
            full_name=row[5] if row[5] else ""
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create user: {e}")
        raise HTTPException(status_code=500, detail="Failed to create user")


@router.put("/{user_id}", response_model=UserOut)
def update_user(
    user_id: str,
    update: UserUpdate,
    db_conn: duckdb.DuckDBPyConnection = Depends(get_db)
):
    """Update user role and/or permissions."""
    # Check user exists
    existing = db_conn.execute(
        "SELECT id, username FROM users WHERE id = ?", [user_id]
    ).fetchone()
    
    if not existing:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    try:
        if update.role is not None:
            db_conn.execute(
                "UPDATE users SET role = ? WHERE id = ?",
                [update.role, user_id]
            )
        
        if update.permissions is not None:
            permissions_json = json.dumps(update.permissions, ensure_ascii=False)
            db_conn.execute(
                "UPDATE users SET permissions = ? WHERE id = ?",
                [permissions_json, user_id]
            )
            
        if update.full_name is not None:
            db_conn.execute(
                "UPDATE users SET full_name = ? WHERE id = ?",
                [update.full_name, user_id]
            )
        
        # Fetch updated user
        row = db_conn.execute(
            "SELECT id, username, role, permissions, created_at, full_name FROM users WHERE id = ?",
            [user_id]
        ).fetchone()
        
        perms = json.loads(row[3]) if row[3] else []
        
        return UserOut(
            id=row[0],
            username=row[1],
            role=row[2],
            permissions=perms,
            created_at=str(row[4]) if row[4] else "",
            full_name=row[5] if row[5] else ""
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update user: {e}")
        raise HTTPException(status_code=500, detail="Failed to update user")


@router.put("/{user_id}/password")
def update_password(
    user_id: str,
    update: PasswordUpdate,
    db_conn: duckdb.DuckDBPyConnection = Depends(get_db)
):
    """Change a user's password."""
    existing = db_conn.execute(
        "SELECT id FROM users WHERE id = ?", [user_id]
    ).fetchone()
    
    if not existing:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    try:
        password_hash = get_password_hash(update.password)
        db_conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            [password_hash, user_id]
        )
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Failed to update password: {e}")
        raise HTTPException(status_code=500, detail="Failed to update password")


@router.delete("/{user_id}")
def delete_user(
    user_id: str,
    current_user: dict = Depends(get_current_user),
    db_conn: duckdb.DuckDBPyConnection = Depends(get_db)
):
    """Delete a user. Admin cannot delete themselves."""
    # Find user to delete
    target = db_conn.execute(
        "SELECT id, username FROM users WHERE id = ?", [user_id]
    ).fetchone()
    
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    # Prevent self-deletion
    if target[1] == current_user.get("username"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя удалить собственную учётную запись"
        )
    
    try:
        db_conn.execute("DELETE FROM users WHERE id = ?", [user_id])
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Failed to delete user: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete user")
