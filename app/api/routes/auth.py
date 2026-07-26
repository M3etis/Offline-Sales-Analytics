from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from app.core.dependencies import get_db
from app.core.security import verify_password, create_access_token
import duckdb
import json
import time
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

# Simple in-memory rate limiter: {ip: [timestamps]}
_login_attempts: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX = 5  # max attempts per window


def _check_rate_limit(ip: str) -> bool:
    now = time.time()
    _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < RATE_LIMIT_WINDOW]
    if len(_login_attempts[ip]) >= RATE_LIMIT_MAX:
        return False
    _login_attempts[ip].append(now)
    return True


@router.post("/login")
async def login_for_access_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: duckdb.DuckDBPyConnection = Depends(get_db)
):
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком много попыток входа. Попробуйте через минуту.",
        )

    # Diagnostic: count total users and check if requested user exists
    try:
        total_users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        logger.info(f"Login attempt: username='{form_data.username}', total_users_in_db={total_users}")
    except Exception as e:
        logger.error(f"Failed to query users table: {e}")
        total_users = -1

    user = db.execute(
        "SELECT id, username, password_hash, role, permissions, full_name FROM users WHERE username = ?",
        (form_data.username,)
    ).fetchone()

    if not user:
        logger.warning(f"Login failed: user '{form_data.username}' not found in database (total users: {total_users})")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id, username, password_hash, role, permissions_raw, full_name = user
    if not verify_password(form_data.password, password_hash):
        logger.warning(f"Login failed: wrong password for user '{form_data.username}'")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Parse permissions
    try:
        permissions = json.loads(permissions_raw) if permissions_raw else []
    except (json.JSONDecodeError, TypeError):
        permissions = []
    
    # Admin always has all permissions
    if role == "admin":
        permissions = ["dashboard", "analyst", "sessions", "details", "settings_status", "settings_data", "settings_assistant", "settings_feedback", "settings_cleanup"]
        
    access_token = create_access_token(subject=username, role=role)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": role,
        "username": username,
        "full_name": full_name or username,
        "permissions": permissions
    }
