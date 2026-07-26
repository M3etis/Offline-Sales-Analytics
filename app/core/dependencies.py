from app.db.connection import DatabaseManager

db_manager = DatabaseManager()

def get_db():
    """FastAPI dependency that yields a DuckDB connection (thread-safe).

    DuckDB connections are NOT safe for concurrent use from multiple threads.
    We acquire a request-level lock so only one request uses the connection at a time.
    """
    db_manager._lock.acquire()
    try:
        conn = db_manager.get_connection()
        yield conn
    finally:
        db_manager._lock.release()

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from typing import Dict, Any
import jwt
from app.core.config import settings

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
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username: str = payload.get("sub")
        role: str = payload.get("role")
        if username is None or role is None:
            raise credentials_exception
        return {"username": username, "role": role}
    except jwt.PyJWTError:
        raise credentials_exception

def get_current_admin(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The user doesn't have enough privileges"
        )
    return current_user
