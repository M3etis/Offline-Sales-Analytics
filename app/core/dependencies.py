from app.db.connection import DatabaseManager
import json
import logging
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from typing import Dict, Any, Optional
import jwt
from app.core.config import settings

db_manager = DatabaseManager()

def get_db():
    """FastAPI dependency: отдельный курсор DuckDB на запрос.

    Курсор — независимое соединение с той же базой, его безопасно использовать параллельно
    с другими. Раньше все запросы делили одно соединение под глобальной блокировкой: она
    держалась весь запрос (включая стриминг ответа LLM) и тормозила остальные ручки, а места,
    ходившие в соединение мимо блокировки (/health, WebSocket), могли прочитать чужой результат.
    """
    cursor = db_manager.get_connection().cursor()
    try:
        yield cursor
    finally:
        try:
            cursor.close()
        except Exception:
            pass


logger = logging.getLogger(__name__)

#: Все разделы интерфейса; администратор получает их всегда
ALL_PERMISSIONS = [
    "dashboard", "analyst", "sessions", "details", "settings_status",
    "settings_data", "settings_assistant", "settings_feedback", "settings_cleanup",
]


def resolve_user(username: str) -> Optional[Dict[str, Any]]:
    """Актуальные роль и права пользователя из базы; None — пользователя больше нет.

    Роль из токена не используется: иначе удалённый или пониженный в правах пользователь
    сохранял бы доступ до истечения токена (24 ч).
    """
    try:
        cur = db_manager.get_connection().cursor()
        try:
            row = cur.execute("SELECT role, permissions FROM users WHERE username = ?", [username]).fetchone()
        finally:
            cur.close()
    except Exception as e:
        logger.error(f"Failed to load user '{username}': {e}")
        return None
    if not row:
        return None
    role, permissions_raw = row
    try:
        permissions = json.loads(permissions_raw) if permissions_raw else []
    except (json.JSONDecodeError, TypeError):
        permissions = []
    if role == "admin":
        permissions = list(ALL_PERMISSIONS)
    return {"username": username, "role": role, "permissions": permissions}


def user_from_token(token: str) -> Optional[Dict[str, Any]]:
    """Проверяет подпись и срок токена и возвращает пользователя из базы; None — токен не годится."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except jwt.PyJWTError:
        return None
    username = payload.get("sub")
    if not username or not payload.get("role"):
        return None
    return resolve_user(username)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

def get_current_user(request: Request, token: str = Depends(oauth2_scheme)) -> Dict[str, Any]:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    user = user_from_token(token)
    if user is None:
        raise credentials_exception
    return user

def get_current_admin(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The user doesn't have enough privileges"
        )
    return current_user


def require_permission(permission: str):
    """Зависимость: доступ к разделу по тому же праву, что и пункт меню (nav.ts).

    Раньше права проверялись только в интерфейсе — отобранное право не мешало вызвать API напрямую.
    """
    def checker(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
        if current_user.get("role") == "admin" or permission in current_user.get("permissions", []):
            return current_user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The user doesn't have enough privileges",
        )
    return checker
