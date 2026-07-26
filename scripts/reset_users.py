#!/usr/bin/env python3
"""Сброс дефолтных пользователей (admin/admin, user/user).

Использование: python scripts/reset_users.py
"""
import sys
import os
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.core.security import get_password_hash
from app.db.connection import DatabaseManager

import duckdb
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    db_path = settings.db_full_path
    logger.info(f"Database path: {db_path}")

    if not os.path.exists(db_path):
        logger.error(f"Database file not found: {db_path}")
        logger.info("Запустите приложение хотя бы раз, чтобы создать БД.")
        sys.exit(1)

    conn = duckdb.connect(db_path)

    # Check users table
    try:
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        logger.info(f"Current users: {user_count}")
    except Exception as e:
        logger.error(f"Users table error: {e}")
        conn.close()
        sys.exit(1)

    if user_count > 0:
        # Show existing users
        rows = conn.execute("SELECT username, role FROM users").fetchall()
        for row in rows:
            logger.info(f"  - {row[0]} (role: {row[1]})")

        answer = input("\nВ БД уже есть пользователи. Пересоздать admin/admin и user/user? (y/N): ")
        if answer.lower() != 'y':
            logger.info("Отменено.")
            conn.close()
            return

        # Delete existing admin/user if they exist
        conn.execute("DELETE FROM users WHERE username IN ('admin', 'user')")
        logger.info("Удалены существующие admin/user")

    # Create default users
    all_perms = '["dashboard","analyst","sessions","details","settings_status","settings_data","settings_cleanup","settings_assistant","settings_feedback"]'
    admin_hash = get_password_hash("admin")
    user_hash = get_password_hash("user")

    conn.execute(
        "INSERT INTO users (id, username, password_hash, role, permissions, full_name) VALUES (?, ?, ?, ?, ?, ?), (?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), "admin", admin_hash, "admin", all_perms, "Администратор",
         str(uuid.uuid4()), "user", user_hash, "user", all_perms, "Пользователь")
    )

    # Verify
    verify = conn.execute("SELECT username, role FROM users WHERE username IN ('admin', 'user')").fetchall()
    for row in verify:
        logger.info(f"Создан: {row[0]} (role: {row[1]})")

    conn.close()
    logger.info("Готово! Попробуйте залогиниться: admin/admin")


if __name__ == "__main__":
    main()
