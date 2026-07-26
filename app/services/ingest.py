from __future__ import annotations
import pandas as pd
import duckdb
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Column name patterns that indicate a date field
DATE_COLUMN_PATTERNS = [
    'date', 'time', 'timestamp', 'created', 'updated', 'deleted',
    'start', 'end', 'begin', 'finish', 'expire', 'due',
    'birth', 'death', 'published', 'scheduled',
]

def detect_column_type(col_name: str, dtype) -> str:
    """Detect column type from pandas dtype and column name.

    Uses both the actual dtype and column name heuristics to correctly
    identify date columns like 'delivery_date', 'purchase_date', etc.
    """
    type_str = str(dtype).lower()

    # Already datetime type
    if 'datetime' in type_str or 'timestamp' in type_str:
        return 'date'

    # Name-based detection for date columns (before numeric check!)
    col_lower = col_name.lower().strip()
    for pattern in DATE_COLUMN_PATTERNS:
        if pattern in col_lower:
            return 'date'

    # Numeric
    if 'int' in type_str or 'float' in type_str:
        return 'numeric'

    return 'string'


def _clean_date_series(s: pd.Series) -> pd.Series:
    """Clean a date column: strip timezone, normalize to YYYY-MM-DDTHH:MM:SS.

    Handles: datetime64[ns, tz], ISO strings with +HH:MM, Unix timestamps.
    """
    if s.dtype == 'object':
        # String column — strip timezone suffix and try to parse
        def strip_tz(val):
            if pd.isna(val):
                return val
            val_str = str(val).strip()
            # Remove timezone suffix like +00:00, +03:00, Z
            val_str = re.sub(r'[+-]\d{2}:\d{2}$', '', val_str)
            val_str = re.sub(r'Z$', '', val_str)
            # If it looks like a Unix timestamp (all digits), convert
            if re.match(r'^\d{10,13}$', val_str):
                ts = int(val_str)
                if ts > 1e12:  # milliseconds
                    ts = ts / 1000
                return pd.Timestamp(ts, unit='s').strftime('%Y-%m-%dT%H:%M:%S')
            return val_str
        s = s.apply(strip_tz)
        return pd.to_datetime(s, errors='coerce')

    # datetime64 with timezone — strip tz
    if hasattr(s.dt, 'tz') and s.dt.tz is not None:
        s = s.dt.tz_localize(None)

    return s


def _clean_dates_in_df(df: pd.DataFrame, columns_meta: list[dict]) -> pd.DataFrame:
    """Clean all date columns in a DataFrame."""
    for col, dtype in df.dtypes.items():
        meta_type = detect_column_type(str(col), dtype)
        if meta_type == 'date':
            try:
                df[col] = _clean_date_series(df[col])
                # Format as clean string without timezone
                df[col] = df[col].dt.strftime('%Y-%m-%dT%H:%M:%S')
            except Exception as e:
                logger.warning(f"Failed to clean date column '{col}': {e}")
    return df


def ingest_file(file_path: str, db_conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Ingest a file into the database.

    Args:
        file_path: Path to the file to ingest.
        db_conn: DuckDB connection.

    Returns:
        Dict with rows_loaded, columns, warnings.
    """
    warnings = []
    ext = Path(file_path).suffix.lower()

    # Read file
    try:
        if ext == '.csv':
            df = pd.read_csv(file_path, sep=None, engine='python', encoding='utf-8')
        elif ext in ('.xlsx', '.xls'):
            df = pd.read_excel(file_path, engine='openpyxl')
        elif ext == '.db':
            return _ingest_db_file(file_path, db_conn)
        else:
            raise ValueError(f"Unsupported file format: {ext}. Use CSV, XLSX or DB.")
    except UnicodeDecodeError:
        df = pd.read_csv(file_path, sep=None, engine='python', encoding='cp1251')
        warnings.append("File was read with cp1251 encoding")

    import uuid
    import json
    import re

    # Clean column names
    clean_cols = []
    for col in df.columns:
        c = str(col).lower().strip()
        c = re.sub(r'[^a-zа-я0-9_]', '_', c)
        if c[0].isdigit():
            c = 'col_' + c
        clean_cols.append(c)
    df.columns = clean_cols

    if df.empty:
        raise ValueError("File is empty")

    dataset_id = str(uuid.uuid4())
    dataset_name = Path(file_path).name
    table_name = f"dataset_{dataset_id.replace('-', '_')}"

    # Extract column metadata for frontend
    columns_meta = []
    for col, dtype in df.dtypes.items():
        meta_type = detect_column_type(str(col), dtype)
        columns_meta.append({"key": col, "label": col, "type": meta_type})

    # Clean dates: strip timezones, normalize format
    df = _clean_dates_in_df(df, columns_meta)

    # Create and populate table
    db_conn.register('_ingest_df', df)
    db_conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM _ingest_df")
    db_conn.unregister('_ingest_df')

    rows_loaded = len(df)
    columns_json = json.dumps(columns_meta, ensure_ascii=False)

    db_conn.execute(
        "INSERT INTO datasets (id, name, rows, columns, table_name) VALUES (?, ?, ?, ?, ?)",
        (dataset_id, dataset_name, rows_loaded, columns_json, table_name)
    )

    # Pre-analyze dataset for fast runtime lookups
    from app.services.preanalysis import preanalyze_dataset
    preanalyze_dataset(db_conn, dataset_id, table_name)

    return {
        "rows_loaded": rows_loaded,
        "total_rows": rows_loaded,
        "dataset_id": dataset_id,
        "columns": clean_cols,
        "warnings": warnings
    }


def _ingest_db_file(file_path: str, db_conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Ingest a .db file (DuckDB or SQLite) into the database."""
    import uuid
    import json
    import re
    from pathlib import Path

    # Try DuckDB first, then SQLite
    tables = _try_duckdb_tables(file_path)
    if tables is None:
        tables = _try_sqlite_tables(file_path)
    if tables is None:
        raise ValueError("Не удалось прочитать файл .db. Поддерживаются DuckDB и SQLite.")

    # Detect relationships between tables
    relationships = _detect_relationships(tables, file_path)
    relationships_json = json.dumps(relationships, ensure_ascii=False)

    # Shared group ID for all tables from this .db file
    source_group = str(uuid.uuid4())
    source_file = Path(file_path).name

    total_rows = 0
    all_columns = []
    dataset_ids = []

    for table_name, df in tables.items():
        if df.empty:
            continue

        # Clean column names
        clean_cols = []
        for col in df.columns:
            c = str(col).lower().strip()
            c = re.sub(r'[^a-zа-я0-9_]', '_', c)
            if c[0].isdigit():
                c = 'col_' + c
            clean_cols.append(c)
        df.columns = clean_cols

        dataset_id = str(uuid.uuid4())
        target_table = f"dataset_{dataset_id.replace('-', '_')}"

        # Extract column metadata
        columns_meta = []
        for col, dtype in df.dtypes.items():
            meta_type = detect_column_type(str(col), dtype)
            columns_meta.append({"key": col, "label": col, "type": meta_type})

        # Clean dates: strip timezones, normalize format
        df = _clean_dates_in_df(df, columns_meta)

        db_conn.register('_ingest_df', df)
        db_conn.execute(f"CREATE TABLE {target_table} AS SELECT * FROM _ingest_df")
        db_conn.unregister('_ingest_df')

        rows = len(df)
        total_rows += rows
        columns_json = json.dumps(columns_meta, ensure_ascii=False)

        db_conn.execute(
            "INSERT INTO datasets (id, name, rows, columns, table_name, source_group, source_file, relationships) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (dataset_id, table_name, rows, columns_json, target_table, source_group, source_file, relationships_json)
        )
        
        # Pre-analyze dataset for fast runtime lookups
        from app.services.preanalysis import preanalyze_dataset
        preanalyze_dataset(db_conn, dataset_id, target_table)
        
        dataset_ids.append(dataset_id)
        all_columns.extend(clean_cols)

    if not dataset_ids:
        raise ValueError("Файл .db не содержит таблиц с данными.")

    return {
        "rows_loaded": total_rows,
        "total_rows": total_rows,
        "dataset_id": dataset_ids[0] if len(dataset_ids) == 1 else ",".join(dataset_ids),
        "columns": list(dict.fromkeys(all_columns)),
        "warnings": [f"Импортировано таблиц: {len(dataset_ids)}"]
    }


def _detect_relationships(tables: dict, file_path: str) -> list[dict]:
    """Detect relationships between tables in a .db file."""
    relationships = []

    # Try explicit foreign keys
    fk_relationships = _try_duckdb_foreign_keys(file_path)
    if fk_relationships is None:
        fk_relationships = _try_sqlite_foreign_keys(file_path)
    if fk_relationships:
        return fk_relationships

    # Heuristic: match *_id columns to table.id
    table_names = list(tables.keys())
    for t1_name, t1_df in tables.items():
        t1_cols = [c.lower() for c in t1_df.columns]
        for col in t1_cols:
            if col == 'id' or not col.endswith('_id'):
                continue
            ref_base = col[:-3]
            for t2_name in table_names:
                if t2_name == t1_name:
                    continue
                t2_lower = t2_name.lower()
                if ref_base in t2_lower or t2_lower.startswith(ref_base):
                    t2_cols = [c.lower() for c in tables[t2_name].columns]
                    if 'id' in t2_cols:
                        relationships.append({
                            "from_table": t1_name,
                            "from_col": col,
                            "to_table": t2_name,
                            "to_col": "id"
                        })
                        break

    return relationships


def _try_duckdb_foreign_keys(file_path: str):
    """Try to read foreign keys from a DuckDB file."""
    try:
        tmp_conn = duckdb.connect(file_path, read_only=True)
        tables = tmp_conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
        ).fetchall()
        relationships = []
        for (tname,) in tables:
            try:
                fks = tmp_conn.execute(f"PRAGMA foreign_keys('{tname}')").fetchall()
                for fk in fks:
                    if len(fk) >= 4:
                        relationships.append({
                            "from_table": tname,
                            "from_col": str(fk[0]),
                            "to_table": str(fk[2]),
                            "to_col": str(fk[3]) if len(fk) > 3 else "id"
                        })
            except Exception:
                pass
        tmp_conn.close()
        return relationships if relationships else None
    except Exception:
        return None


def _try_sqlite_foreign_keys(file_path: str):
    """Try to read foreign keys from a SQLite file."""
    try:
        import sqlite3
        conn = sqlite3.connect(file_path)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = cursor.fetchall()
        relationships = []
        for (tname,) in tables:
            try:
                fk_cursor = conn.execute(f"PRAGMA foreign_key_list('{tname}')")
                for fk in fk_cursor.fetchall():
                    if len(fk) >= 4:
                        relationships.append({
                            "from_table": tname,
                            "from_col": str(fk[3]),
                            "to_table": str(fk[2]),
                            "to_col": str(fk[4]) if len(fk) > 4 and fk[4] else "id"
                        })
            except Exception:
                pass
        conn.close()
        return relationships if relationships else None
    except Exception:
        return None


def _try_duckdb_tables(file_path: str):
    """Try to read tables from a DuckDB file. Returns dict of {table_name: DataFrame} or None."""
    try:
        tmp_conn = duckdb.connect(file_path, read_only=True)
        tables = tmp_conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
        ).fetchall()
        result = {}
        for (tname,) in tables:
            df = tmp_conn.execute(f'SELECT * FROM "{tname}"').fetchdf()
            result[tname] = df
        tmp_conn.close()
        return result if result else None
    except Exception:
        return None


def _try_sqlite_tables(file_path: str):
    """Try to read tables from a SQLite file. Returns dict of {table_name: DataFrame} or None."""
    try:
        import sqlite3
        conn = sqlite3.connect(file_path)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = cursor.fetchall()
        result = {}
        for (tname,) in tables:
            df = pd.read_sql_query(f'SELECT * FROM "{tname}"', conn)
            result[tname] = df
        conn.close()
        return result if result else None
    except Exception:
        return None


def analyze_and_cache_schema(db_conn, table_name: str) -> dict:
    """Analyze table structure and cache the profile for fast agent lookups."""
    import json
    
    try:
        row_count = db_conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        cols = db_conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position",
            [table_name]
        ).fetchall()

        columns = []
        null_rates = {}
        distinct_counts = {}
        sample_values = {}

        for col_name, col_type in cols:
            columns.append({"name": col_name, "type": col_type})
            try:
                nc = db_conn.execute(f"SELECT COUNT(*) FROM {table_name} WHERE {col_name} IS NULL").fetchone()[0]
                null_rates[col_name] = round(nc / max(row_count, 1), 4)
            except Exception:
                null_rates[col_name] = 0.0
            try:
                dc = db_conn.execute(f"SELECT COUNT(DISTINCT {col_name}) FROM {table_name}").fetchone()[0]
                distinct_counts[col_name] = dc
            except Exception:
                distinct_counts[col_name] = 0
            try:
                samples = db_conn.execute(
                    f"SELECT DISTINCT {col_name} FROM {table_name} WHERE {col_name} IS NOT NULL LIMIT 5"
                ).fetchall()
                sample_values[col_name] = [s[0] for s in samples]
            except Exception:
                sample_values[col_name] = []

        profile = {
            "table_name": table_name,
            "row_count": row_count,
            "columns": columns,
            "null_rates": null_rates,
            "distinct_counts": distinct_counts,
            "sample_values": sample_values,
        }
        db_conn.execute(
            "INSERT OR REPLACE INTO schema_cache (table_name, profile_json, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            [table_name, json.dumps(profile, ensure_ascii=False, default=str)]
        )
        logger.info(f"Cached schema profile for {table_name} ({row_count} rows, {len(columns)} cols)")
        return profile
    except Exception as e:
        logger.error(f"Failed to analyze schema for {table_name}: {e}")
        return {"table_name": table_name, "error": str(e)}
