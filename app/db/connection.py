from __future__ import annotations
import duckdb
import logging
import os
import re
import threading
import uuid

from typing import Optional, Any

from app.core.config import settings

logger = logging.getLogger(__name__)

SALES_TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS sales (
    date DATE,
    order_id VARCHAR,
    product VARCHAR,
    category VARCHAR,
    region VARCHAR,
    manager VARCHAR,
    channel VARCHAR,
    quantity INTEGER DEFAULT 0,
    price DOUBLE DEFAULT 0,
    discount DOUBLE DEFAULT 0,
    revenue DOUBLE DEFAULT 0,
    cost DOUBLE DEFAULT 0,
    profit DOUBLE DEFAULT 0,
    customer_id VARCHAR,
    brand VARCHAR,
    warehouse VARCHAR,
    returns INTEGER DEFAULT 0,
    status VARCHAR DEFAULT 'completed',
    dataset_id VARCHAR DEFAULT 'default'
)
"""

DATASETS_TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    id VARCHAR PRIMARY KEY,
    name VARCHAR,
    rows INTEGER,
    columns TEXT,
    table_name VARCHAR,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

ASSISTANT_INSTRUCTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS assistant_instructions (
    id VARCHAR PRIMARY KEY,
    category VARCHAR,
    content TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

USERS_TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id VARCHAR PRIMARY KEY,
    username VARCHAR UNIQUE,
    password_hash VARCHAR,
    role VARCHAR,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

SYSTEM_SETTINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS system_settings (
    setting_key VARCHAR PRIMARY KEY,
    setting_value VARCHAR
)
"""

CHAT_SESSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_sessions (
    id VARCHAR PRIMARY KEY,
    title VARCHAR,
    dataset_id VARCHAR DEFAULT 'default',
    user_id VARCHAR DEFAULT 'anonymous',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

CHAT_MESSAGES_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_messages (
    id VARCHAR PRIMARY KEY,
    session_id VARCHAR,
    role VARCHAR,
    content TEXT,
    data_summary TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

LLM_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_cache (
    query_hash VARCHAR PRIMARY KEY,
    response_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

SCHEMA_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_cache (
    table_name VARCHAR PRIMARY KEY,
    profile_json TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

DATASET_MANIFEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS dataset_manifest (
    dataset_id VARCHAR PRIMARY KEY,
    manifest_json TEXT,
    data_hash VARCHAR,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

COLUMN_DICTIONARY_SCHEMA = """
CREATE TABLE IF NOT EXISTS column_dictionary (
    id VARCHAR PRIMARY KEY,
    en_name VARCHAR NOT NULL,
    ru_name VARCHAR NOT NULL,
    category VARCHAR DEFAULT 'other',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

class DatabaseManager:
    """Manages DuckDB connection lifecycle."""
    
    def __init__(self):
        self._connection: Optional[duckdb.DuckDBPyConnection] = None
        self._lock = threading.Lock()
    
    def init_db(self) -> None:
        """Initialize database and create tables."""
        db_path = settings.db_full_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        
        conn = self.get_connection()
        conn.execute(SALES_TABLE_SCHEMA)
        conn.execute(DATASETS_TABLE_SCHEMA)
        
        try:
            cols = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'datasets'").fetchall()
            col_names = [c[0] for c in cols]
            if 'columns' not in col_names:
                conn.execute('ALTER TABLE datasets ADD COLUMN "columns" TEXT')
            if 'table_name' not in col_names:
                conn.execute("ALTER TABLE datasets ADD COLUMN table_name VARCHAR")
                conn.execute("UPDATE datasets SET table_name = 'sales' WHERE table_name IS NULL")
            if 'source_group' not in col_names:
                conn.execute("ALTER TABLE datasets ADD COLUMN source_group VARCHAR")
            if 'source_file' not in col_names:
                conn.execute("ALTER TABLE datasets ADD COLUMN source_file VARCHAR")
            if 'relationships' not in col_names:
                conn.execute("ALTER TABLE datasets ADD COLUMN relationships TEXT")
        except Exception as e:
            logger.error(f"Migration error: {e}")
        conn.execute(ASSISTANT_INSTRUCTIONS_SCHEMA)
        conn.execute(USERS_TABLE_SCHEMA)
        conn.execute(SYSTEM_SETTINGS_SCHEMA)
        conn.execute(CHAT_SESSIONS_SCHEMA)
        conn.execute(CHAT_MESSAGES_SCHEMA)
        conn.execute(LLM_CACHE_SCHEMA)
        conn.execute(SCHEMA_CACHE_SCHEMA)
        conn.execute(DATASET_MANIFEST_SCHEMA)
        conn.execute(COLUMN_DICTIONARY_SCHEMA)

        # Ensure dataset_manifest table exists (migration for older databases)
        try:
            conn.execute(DATASET_MANIFEST_SCHEMA)
        except Exception:
            pass

        # Performance indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_role_date ON chat_messages(role, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON chat_sessions(user_id, dataset_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_datasets_group ON datasets(source_group)")

        # Insert default voice if not exists
        voice_count = conn.execute("SELECT COUNT(*) FROM system_settings WHERE setting_key = 'tts_voice'").fetchone()[0]
        if voice_count == 0:
            conn.execute("INSERT INTO system_settings (setting_key, setting_value) VALUES (?, ?)", ('tts_voice', 'ru-RU-SvetlanaNeural'))
        # Add dataset_id to existing sales table if missing
        try:
            conn.execute("ALTER TABLE sales ADD COLUMN dataset_id VARCHAR DEFAULT 'default'")
        except Exception:
            pass

        try:
            conn.execute("ALTER TABLE sales ADD COLUMN quantity INTEGER DEFAULT 0")
        except Exception:
            pass
            
        # Add dataset_id to chat_sessions table if missing
        try:
            conn.execute("ALTER TABLE chat_sessions ADD COLUMN dataset_id VARCHAR DEFAULT 'default'")
        except Exception:
            pass # Column likely already exists

        # Add user_id to chat_sessions table if missing
        try:
            conn.execute("ALTER TABLE chat_sessions ADD COLUMN user_id VARCHAR DEFAULT 'anonymous'")
        except Exception:
            pass # Column likely already exists
        
        # Add permissions column to users table if missing
        try:
            conn.execute("ALTER TABLE users ADD COLUMN permissions VARCHAR DEFAULT '[]'")
            # Set default permissions for existing users
            conn.execute("""UPDATE users SET permissions = '["dashboard","analyst","sessions","details","settings_status","settings_data","settings_assistant","settings_feedback","settings_cleanup"]' WHERE permissions = '[]'""")
        except Exception:
            pass # Column likely already exists
        
        # Add full_name column to users table if missing
        try:
            conn.execute("ALTER TABLE users ADD COLUMN full_name VARCHAR DEFAULT ''")
        except Exception:
            pass # Column likely already exists

        # Add feedback column to chat_messages for learning system
        try:
            conn.execute("ALTER TABLE chat_messages ADD COLUMN feedback VARCHAR DEFAULT NULL")
        except Exception:
            pass

        # Add sql_query column to chat_messages for storing generated SQL
        try:
            conn.execute("ALTER TABLE chat_messages ADD COLUMN sql_query TEXT DEFAULT NULL")
        except Exception:
            pass

        # Add intent column to chat_messages
        try:
            conn.execute("ALTER TABLE chat_messages ADD COLUMN intent VARCHAR DEFAULT NULL")
        except Exception:
            pass
            
        # Ensure default users exist (admin/admin, user/user)
        from app.core.security import get_password_hash
        all_perms = '["dashboard","analyst","sessions","details","settings_status","settings_data","settings_cleanup","settings_assistant","settings_feedback"]'

        for default_user, default_pw, role, full_name in [
            ("admin", "admin", "admin", "Администратор"),
            ("user", "user", "user", "Пользователь"),
        ]:
            exists = conn.execute(
                "SELECT COUNT(*) FROM users WHERE username = ?", (default_user,)
            ).fetchone()[0]
            if exists == 0:
                pw_hash = get_password_hash(default_pw)
                conn.execute(
                    "INSERT INTO users (id, username, password_hash, role, permissions, full_name) VALUES (?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), default_user, pw_hash, role, all_perms, full_name)
                )
                logger.info(f"Default user created: {default_user}/{default_pw}")

        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        logger.info(f"Users in DB: {user_count}")

        # Insert default instructions if table is empty
        instr_count = conn.execute("SELECT COUNT(*) FROM assistant_instructions").fetchone()[0]
        if instr_count == 0:
            default_instructions = [
                # Строгие правила
                ("rules", "ОБЯЗАТЕЛЬНО пиши весь текст исключительно на русском языке."),
                ("rules", "Форматируй большие числа в человеко-понятный текстовый вид, а рядом в скобках указывай точное число. Например: 3.04 млрд (3041454711)."),
                ("rules", "Абсолютная точность: используй строго те значения, которые предоставлены в данных."),
                ("rules", "Категорически запрещено придумывать цифры, искажать метрики и галлюцинировать."),
                ("rules", "Запрещено строить догадки о причинах изменения метрик, если этих причин нет в выборке."),
                ("rules", "Не используй [Жирный шрифт] или markdown-форматирование текста звездами (**)"),
                ("rules", "При ответах на запросы о ключевых показателях (KPI) розничных сетей обязательно указывай: выручку, прибыль, маржинальность (%), средний чек, количество заказов/транзакций, количество проданных единиц. Если данные содержат информацию о магазинах/точках — добавляй разбивку по точкам продаж. Сравнивай показатели с предыдущим периодом если данные позволяют."),
                ("rules", "При работе с группами баз данных (многотабличные .db файлы): определи какие таблицы содержат факты продаж (даты, суммы, количества), а какие — справочники (названия, категории, адреса). Соединяй таблицы через связи из системного промпта. Для агрегирующих запросов ищи числовые колонки в таблицах фактов. Если запрос касается названий или категорий — JOIN со справочниками."),
                ("rules", "При анализе данных розничных сетей: выводи числа как есть, БЕЗ указания валюты, если она явно не указана в базе. Используй бизнес-терминологию: товарооборот, маржинальность, средний чек, конверсия, ROMI. При выявлении аномалий (резкие скачки/падения) — объясняй возможные причины и давай рекомендации. При сравнении периодов — указывай процент изменения."),
                ("rules", "ОЧЕНЬ ВАЖНО ДЛЯ ДАТ: В DuckDB если колонка даты — это UNIX timestamp (BIGINT, целое число), конвертируй её ТОЛЬКО через TO_TIMESTAMP(col). НИКОГДА не вычитай INTERVAL из BIGINT! Правильно: TO_TIMESTAMP(col) - INTERVAL '30 DAY'. Если же колонка даты — это строка (VARCHAR), конвертируй её ТОЛЬКО через TRY_CAST(col AS DATE). НИКОГДА не путай их!"),
                ("rules", "ОТСУТСТВИЕ КОНТЕКСТА: Если запрос бессмысленный или абстрактный (например, 'TOP N' без указания чего именно) и ты НЕ МОЖЕШЬ составить SQL, начни свой ответ строго со слова CLARIFY: и напиши уточняющий вопрос к пользователю."),
                ("recommendations", "Используй профессиональную лексику из сферы продаж, маркетинга и бизнес-аналитики."),
                ("recommendations", "Фокусируйся на конкретных точках роста: подсвечивай аномалии, лидеров и аутсайдеров (товары, категории, менеджеры, регионы)."),
                ("recommendations", "Делай ответ легко читаемым: выделяй ключевые KPI жирным шрифтом и используй маркированные списки."),
                ("recommendations", "При анализе продаж розничной сети учитывай: 1) Структуру данных — продажи (факты) и справочники (товары, магазины, клиенты). 2) Многотабличные данные требуют JOIN через связи. 3) Числовые суммы всегда округляй ROUND(). 4) Для динамики используй GROUP BY по дате с ORDER BY. 5) Для поиска товаров/категорий используй LOWER() LIKE для регистронезависимого поиска."),
                ("recommendations", "Популярные аналитические запросы для розничных сетей и как на них отвечать: 1) Топ товаров/категорий — GROUP BY + ORDER BY SUM(revenue) DESC LIMIT N. 2) Средний чек — AVG(revenue) WHERE revenue > 0. 3) Динамика продаж — GROUP BY DATE_TRUNC(month/day) с ORDER BY. 4) Продажи по регионам/магазинам — GROUP BY region/store. 5) ABC-анализ — ранжирование по выручке с накопительным процентом. 6) Возвраты — WHERE returns > 0 или CASE WHEN. 7) Влияние скидок — GROUP BY discount с AVG(revenue). 8) Конверсия — COUNT с CASE WHEN условиями."),
                # Контекст и Роль
                ("context", "Ты — Senior Business & Sales Analyst (Старший бизнес-аналитик)."),
                ("context", "Твоя цель — помогать коммерческому директору и отделу маркетинга быстро и точно понимать текущую ситуацию по продажам."),
                ("context", "Ты смотришь на данные через призму выручки, прибыли и эффективности продаж."),
                ("context", "Если данных для ответа на вопрос пользователя нет в базе — прямо и вежливо скажи об этом, не пытайся угадать ответ."),
            ]
            for cat, content in default_instructions:
                conn.execute(
                    "INSERT INTO assistant_instructions (id, category, content) VALUES (?, ?, ?)",
                    [str(uuid.uuid4()), cat, content]
                )
            logger.info("Default retail analytics instructions created")

        # Insert default column dictionary if empty
        dict_count = conn.execute("SELECT COUNT(*) FROM column_dictionary").fetchone()[0]
        if dict_count == 0:
            from app.services.column_dict import COLUMN_TRANSLATIONS
            for en_name, ru_name in COLUMN_TRANSLATIONS.items():
                # Determine category based on common patterns
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
                
                conn.execute(
                    "INSERT INTO column_dictionary (id, en_name, ru_name, category) VALUES (?, ?, ?, ?)",
                    [str(uuid.uuid4()), en_name, ru_name, category]
                )
            logger.info(f"Default column dictionary created with {len(COLUMN_TRANSLATIONS)} entries")

        logger.info(f"Database initialized at {db_path}")
    
    def get_connection(self) -> duckdb.DuckDBPyConnection:
        """Get or create DuckDB connection."""
        if self._connection is None:
            with self._lock:
                if self._connection is None:
                    db_path = settings.db_full_path
                    os.makedirs(os.path.dirname(db_path), exist_ok=True)
                    self._connection = self._connect_with_wal_recovery(db_path)
                    logger.info(f"Connected to DuckDB at {db_path}")
        return self._connection

    def _connect_with_wal_recovery(self, db_path: str) -> duckdb.DuckDBPyConnection:
        """Connect to DuckDB, recovering from WAL corruption if needed."""
        try:
            return duckdb.connect(db_path)
        except Exception as e:
            if "WAL" not in str(e):
                raise
            wal_path = db_path + ".wal"
            logger.warning(f"DuckDB WAL corruption detected, removing {wal_path}: {e}")
            try:
                os.remove(wal_path)
            except FileNotFoundError:
                pass
            return duckdb.connect(db_path)
    
    def close(self) -> None:
        """Close database connection."""
        if self._connection:
            try:
                self._connection.close()
            except Exception:
                pass
            self._connection = None
            logger.info("DuckDB connection closed")
    
    def execute_safe_query(
        self, 
        sql: str, 
        params: list | None = None,
        max_rows: int | None = None
    ) -> list[dict[str, Any]]:
        """Execute a query with row limit and return results as list of dicts."""
        conn = self.get_connection()
        limit = max_rows or settings.MAX_RESULT_ROWS
        
        # Add LIMIT if not present
        sql_upper = sql.strip().upper()
        if "LIMIT" not in sql_upper:
            sql = f"{sql.rstrip(';')} LIMIT {limit}"
        
        try:
            if params:
                result = conn.execute(sql, params)
            else:
                result = conn.execute(sql)
            
            columns = [desc[0] for desc in result.description]
            rows = result.fetchall()
            return [dict(zip(columns, row)) for row in rows]
        except Exception as e:
            logger.error(f"Query error: {e}\nSQL: {sql}")
            raise
    
    def get_table_info(self) -> dict:
        """Get global information across all datasets."""
        conn = self.get_connection()
        try:
            try:
                row = conn.execute("SELECT SUM(rows) FROM datasets").fetchone()
                total_rows = int(row[0]) if row and row[0] is not None else 0
            except duckdb.CatalogException:
                total_rows = 0
                
            if total_rows == 0:
                return {"rows": 0, "columns": [], "date_range": None}
                
            tables = conn.execute("SELECT table_name FROM datasets").fetchall()
            min_dates = []
            max_dates = []
            
            for (t_name,) in tables:
                if not re.match(r'^(sales|dataset_[a-f0-9_]+)$', t_name):
                    logger.warning(f"Skipping invalid table name: {t_name}")
                    continue
                has_date = conn.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_name = ? AND column_name = 'date'", (t_name,)).fetchone()[0] > 0
                if has_date:
                    d_range = conn.execute(f"SELECT MIN(date), MAX(date) FROM {t_name} WHERE date IS NOT NULL").fetchone()
                    if d_range and d_range[0]:
                        min_dates.append(d_range[0])
                        max_dates.append(d_range[1])
            
            date_range = None
            if min_dates and max_dates:
                date_range = {
                    "min": str(min(min_dates)),
                    "max": str(max(max_dates))
                }
                
            return {
                "rows": total_rows,
                "columns": [],
                "date_range": date_range
            }
        except Exception as e:
            logger.error(f"Error getting table info: {e}")
            return {"rows": 0, "columns": [], "date_range": None}
