from fastapi import APIRouter, Depends, HTTPException
import duckdb
from typing import List, Optional
from pydantic import BaseModel
import uuid
import datetime
import hashlib
import json

from app.core.dependencies import get_db, get_current_user
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])

class ChatSession(BaseModel):
    id: str
    title: str
    dataset_id: str = 'default'
    user_id: str = 'anonymous'
    created_at: str
    updated_at: str
    status: str = 'ok'  # 'ok', 'no_response', 'error'

class ChatMessageItem(BaseModel):
    id: str
    role: str
    content: str
    data_summary: Optional[str] = None
    feedback: Optional[str] = None
    created_at: str

class SessionDetails(BaseModel):
    session: ChatSession
    messages: List[ChatMessageItem]

class SessionCreateRequest(BaseModel):
    title: Optional[str] = "Новая сессия"
    dataset_id: Optional[str] = "default"

class SessionTitleUpdate(BaseModel):
    title: str

class PaginatedSessions(BaseModel):
    sessions: List[ChatSession]
    total: int
    page: int
    page_size: int
    total_pages: int

@router.get("", response_model=PaginatedSessions)
def get_sessions(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    dataset_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    current_user: dict = Depends(get_current_user),
    db_conn: duckdb.DuckDBPyConnection = Depends(get_db)
):
    """Get chat sessions for current user with pagination, ordered by updated_at descending."""
    try:
        username = current_user.get("username", "anonymous")
        base_query = "FROM chat_sessions WHERE user_id = ?"
        params = [username]
        conditions = []

        if from_date:
            conditions.append("CAST(created_at AS DATE) >= CAST(? AS DATE)")
            params.append(from_date)
        if to_date:
            conditions.append("CAST(created_at AS DATE) <= CAST(? AS DATE)")
            params.append(to_date)
        if dataset_id:
            conditions.append("dataset_id = ?")
            params.append(dataset_id)

        if conditions:
            base_query += " AND " + " AND ".join(conditions)

        count_row = db_conn.execute(f"SELECT COUNT(*) {base_query}", params).fetchone()
        total = count_row[0] if count_row else 0
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)

        offset = (page - 1) * page_size
        # Subquery to determine session status
        status_query = """
            CASE
                WHEN NOT EXISTS (SELECT 1 FROM chat_messages m WHERE m.session_id = s.id AND m.role = 'assistant') THEN 'no_response'
                WHEN EXISTS (
                    SELECT 1 FROM chat_messages m2
                    WHERE m2.session_id = s.id AND m2.role = 'assistant'
                    AND (m2.content LIKE '%Произошла ошибка%' OR m2.content LIKE '%непредвиденная ошибка%'
                         OR m2.content LIKE '%Не могу выполнить%' OR m2.content LIKE '%ограничениями безопасности%'
                         OR m2.content LIKE '%По вашему запросу нет данных%')
                    AND m2.id = (SELECT m3.id FROM chat_messages m3 WHERE m3.session_id = s.id AND m3.role = 'assistant' ORDER BY m3.created_at DESC LIMIT 1)
                ) THEN 'error'
                ELSE 'ok'
            END as status
        """
        query = f"SELECT id, title, user_id, created_at, updated_at, {status_query} {base_query.replace('FROM chat_sessions', 'FROM chat_sessions s')} ORDER BY updated_at DESC LIMIT {page_size} OFFSET {offset}"

        result = db_conn.execute(query, params).fetchall()

        sessions = [
            ChatSession(
                id=row[0],
                title=row[1],
                user_id=row[2] or 'anonymous',
                created_at=str(row[3]),
                updated_at=str(row[4]),
                status=row[5] if len(row) > 5 else 'ok'
            )
            for row in result
        ]
        return PaginatedSessions(
            sessions=sessions,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages
        )
    except Exception as e:
        logger.error(f"Failed to fetch sessions: {e}")
        return PaginatedSessions(sessions=[], total=0, page=1, page_size=page_size, total_pages=1)

@router.post("", response_model=ChatSession)
def create_session(request: SessionCreateRequest, current_user: dict = Depends(get_current_user), db_conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    """Create a new chat session for current user."""
    session_id = str(uuid.uuid4())
    title = request.title or "Новая сессия"
    username = current_user.get("username", "anonymous")
    now = datetime.datetime.now().isoformat()
    try:
        db_conn.execute(
            "INSERT INTO chat_sessions (id, title, dataset_id, user_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            [session_id, title, request.dataset_id, username, now, now]
        )
        return ChatSession(id=session_id, title=title, dataset_id=request.dataset_id, user_id=username, created_at=now, updated_at=now)
    except Exception as e:
        logger.error(f"Failed to create session: {e}")
        raise HTTPException(status_code=500, detail="Failed to create session")

@router.delete("/clear-my")
def clear_my_sessions(current_user: dict = Depends(get_current_user), db_conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    """Delete all sessions and messages for the current user."""
    username = current_user.get("username", "anonymous")
    try:
        session_ids = db_conn.execute(
            "SELECT id FROM chat_sessions WHERE user_id = ?", [username]
        ).fetchall()
        ids = [row[0] for row in session_ids]

        db_conn.execute("BEGIN")
        if ids:
            placeholders = ', '.join(['?'] * len(ids))
            db_conn.execute(f"DELETE FROM chat_messages WHERE session_id IN ({placeholders})", ids)

            hashes_to_delete = []
            for sid in ids:
                messages = db_conn.execute(
                    "SELECT content FROM chat_messages WHERE session_id = ? AND role = 'user'", [sid]
                ).fetchall()
                session_row = db_conn.execute("SELECT dataset_id FROM chat_sessions WHERE id = ?", [sid]).fetchone()
                dk = str(session_row[0]) if session_row and session_row[0] else "global"
                for msg_row in messages:
                    content = msg_row[0].strip().lower()
                    h_false = hashlib.md5((content + str(False) + dk).encode()).hexdigest()
                    h_true = hashlib.md5((content + str(True) + dk).encode()).hexdigest()
                    hashes_to_delete.extend([h_false, h_true])

            if hashes_to_delete:
                placeholders = ', '.join(['?'] * len(hashes_to_delete))
                db_conn.execute(f"DELETE FROM llm_cache WHERE query_hash IN ({placeholders})", hashes_to_delete)

        db_conn.execute("DELETE FROM chat_sessions WHERE user_id = ?", [username])
        db_conn.execute("COMMIT")
        return {"status": "success", "message": f"All sessions for user '{username}' deleted."}
    except Exception as e:
        try:
            db_conn.execute("ROLLBACK")
        except Exception:
            pass
        logger.error(f"Failed to clear sessions for user {username}: {e}")
        raise HTTPException(status_code=500, detail="Failed to clear sessions")


class FeedbackRequest(BaseModel):
    message_id: str
    feedback: Optional[str] = None  # "positive", "negative", or null to clear


@router.post("/feedback")
async def set_feedback(request: FeedbackRequest, db_conn=Depends(get_db), current_user: dict = Depends(get_current_user)):
    """Set feedback (thumbs up/down) on an assistant message."""
    try:
        if request.feedback not in ("positive", "negative", None):
            raise HTTPException(status_code=400, detail="Feedback must be 'positive', 'negative', or null")

        db_conn.execute(
            "UPDATE chat_messages SET feedback = ? WHERE id = ? AND role = 'assistant'",
            [request.feedback, request.message_id]
        )
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to set feedback: {e}")
        raise HTTPException(status_code=500, detail="Failed to set feedback")


@router.get("/feedback-stats")
async def get_feedback_stats(dataset_id: Optional[str] = None, db_conn=Depends(get_db), current_user: dict = Depends(get_current_user)):
    """Get feedback statistics for learning purposes."""
    try:
        where_clause = ""
        params = []
        if dataset_id:
            where_clause = "AND cs.dataset_id = ?"
            params.append(dataset_id)

        stats = db_conn.execute(f"""
            SELECT
                cm.feedback,
                COUNT(*) as count
            FROM chat_messages cm
            JOIN chat_sessions cs ON cm.session_id = cs.id
            WHERE cm.role = 'assistant' AND cm.feedback IS NOT NULL {where_clause}
            GROUP BY cm.feedback
        """, params).fetchall()

        result = {"positive": 0, "negative": 0, "total": 0}
        for row in stats:
            result[row[0]] = row[1]
            result["total"] += row[1]

        return result
    except Exception as e:
        logger.error(f"Failed to get feedback stats: {e}")
        return {"positive": 0, "negative": 0, "total": 0}


@router.get("/feedback-messages")
async def get_feedback_messages(
    dataset_id: Optional[str] = None,
    feedback: Optional[str] = None,
    limit: int = 50,
    db_conn=Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Get messages that have been rated, with their questions."""
    try:
        where_conditions = ["cm.role = 'assistant'", "cm.feedback IS NOT NULL"]
        params = []

        if dataset_id:
            where_conditions.append("cs.dataset_id = ?")
            params.append(dataset_id)
        if feedback:
            where_conditions.append("cm.feedback = ?")
            params.append(feedback)

        where_clause = " AND ".join(where_conditions)

        messages = db_conn.execute(f"""
            SELECT
                cm.id,
                cm.content,
                cm.feedback,
                cm.created_at,
                cs.id as session_id,
                cs.title as session_title,
                cs.dataset_id,
                user_msg.content as question
            FROM chat_messages cm
            JOIN chat_sessions cs ON cm.session_id = cs.id
            LEFT JOIN chat_messages user_msg
                ON user_msg.session_id = cm.session_id
                AND user_msg.role = 'user'
                AND user_msg.created_at = (
                    SELECT MAX(um2.created_at)
                    FROM chat_messages um2
                    WHERE um2.session_id = cm.session_id
                    AND um2.role = 'user'
                    AND um2.created_at < cm.created_at
                )
            WHERE {where_clause}
            ORDER BY cm.created_at DESC
            LIMIT ?
        """, params + [limit]).fetchall()

        result = []
        for row in messages:
            result.append({
                "id": row[0],
                "answer": row[1][:200] + "..." if len(row[1]) > 200 else row[1],
                "feedback": row[2],
                "created_at": str(row[3]),
                "session_id": row[4],
                "session_title": row[5],
                "dataset_id": row[6],
                "question": row[7] if row[7] else "—",
            })

        return result
    except Exception as e:
        logger.error(f"Failed to get feedback messages: {e}")
        return []


@router.get("/{session_id}", response_model=SessionDetails)
def get_session(session_id: str, current_user: dict = Depends(get_current_user), db_conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    """Get session details and all messages for current user."""
    try:
        username = current_user.get("username", "anonymous")
        session_row = db_conn.execute(
            "SELECT id, title, user_id, created_at, updated_at FROM chat_sessions WHERE id = ? AND user_id = ?",
            [session_id, username]
        ).fetchone()

        if not session_row:
            raise HTTPException(status_code=404, detail="Session not found")

        messages_result = db_conn.execute(
            "SELECT id, role, content, data_summary, feedback, created_at FROM chat_messages WHERE session_id = ? ORDER BY created_at ASC",
            [session_id]
        ).fetchall()

        messages = [
            ChatMessageItem(
                id=row[0],
                role=row[1],
                content=row[2],
                data_summary=row[3],
                feedback=row[4],
                created_at=str(row[5])
            )
            for row in messages_result
        ]

        session = ChatSession(
            id=session_row[0],
            title=session_row[1],
            user_id=session_row[2] or 'anonymous',
            created_at=str(session_row[3]),
            updated_at=str(session_row[4])
        )

        return SessionDetails(session=session, messages=messages)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch session {session_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch session")

@router.put("/{session_id}/title", response_model=ChatSession)
def update_session_title(session_id: str, request: SessionTitleUpdate, current_user: dict = Depends(get_current_user), db_conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    """Update session title for current user."""
    try:
        username = current_user.get("username", "anonymous")
        now = datetime.datetime.now().isoformat()
        db_conn.execute(
            "UPDATE chat_sessions SET title = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            [request.title, now, session_id, username]
        )

        session_row = db_conn.execute(
            "SELECT id, title, user_id, created_at, updated_at FROM chat_sessions WHERE id = ? AND user_id = ?",
            [session_id, username]
        ).fetchone()

        if not session_row:
            raise HTTPException(status_code=404, detail="Session not found")

        return ChatSession(
            id=session_row[0],
            title=session_row[1],
            user_id=session_row[2] or 'anonymous',
            created_at=str(session_row[3]),
            updated_at=str(session_row[4])
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update session title: {e}")
        raise HTTPException(status_code=500, detail="Failed to update session title")

@router.delete("/{session_id}")
def delete_session(session_id: str, current_user: dict = Depends(get_current_user), db_conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    """Delete a session, its messages, and clear its cached queries for current user."""
    try:
        username = current_user.get("username", "anonymous")
        # Verify ownership
        session_row = db_conn.execute("SELECT dataset_id, user_id FROM chat_sessions WHERE id = ? AND user_id = ?", [session_id, username]).fetchone()
        if not session_row:
            raise HTTPException(status_code=404, detail="Session not found")

        dataset_id = session_row[0]
        dataset_key = str(dataset_id) if dataset_id else "global"

        messages = db_conn.execute("SELECT content FROM chat_messages WHERE session_id = ? AND role = 'user'", [session_id]).fetchall()

        all_dataset_keys = [dataset_key]
        try:
            ds_rows = db_conn.execute("SELECT id FROM datasets").fetchall()
            for row in ds_rows:
                k = str(row[0]) if row[0] else "global"
                if k not in all_dataset_keys:
                    all_dataset_keys.append(k)
        except Exception:
            pass

        hashes_to_delete = []
        for msg_row in messages:
            content = msg_row[0].strip().lower()
            for dk in all_dataset_keys:
                h_false = hashlib.md5((content + str(False) + dk).encode()).hexdigest()
                h_true = hashlib.md5((content + str(True) + dk).encode()).hexdigest()
                hashes_to_delete.extend([h_false, h_true])

        db_conn.execute("BEGIN")
        if hashes_to_delete:
            placeholders = ', '.join(['?'] * len(hashes_to_delete))
            db_conn.execute(f"DELETE FROM llm_cache WHERE query_hash IN ({placeholders})", hashes_to_delete)

        db_conn.execute("DELETE FROM chat_messages WHERE session_id = ?", [session_id])
        db_conn.execute("DELETE FROM chat_sessions WHERE id = ?", [session_id])
        db_conn.execute("COMMIT")
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        try:
            db_conn.execute("ROLLBACK")
        except Exception:
            pass
        logger.error(f"Failed to delete session: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete session")
