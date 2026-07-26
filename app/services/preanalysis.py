from __future__ import annotations
import json
import hashlib
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def preanalyze_dataset(db_conn, dataset_id: str, table_name: str) -> dict:
    """Полный преданализ датасета. Вызывается ОДИН РАЗ при загрузке."""
    
    row_count = db_conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    
    columns = db_conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = ? ORDER BY ordinal_position", [table_name]
    ).fetchall()
    
    profiles = {}
    for col_name, col_type in columns:
        try:
            nc = db_conn.execute(
                f"SELECT COUNT(*) FROM {table_name} WHERE {col_name} IS NULL"
            ).fetchone()[0]
            dc = db_conn.execute(
                f"SELECT COUNT(DISTINCT {col_name}) FROM {table_name}"
            ).fetchone()[0]
            samples = db_conn.execute(
                f"SELECT DISTINCT {col_name} FROM {table_name} "
                f"WHERE {col_name} IS NOT NULL LIMIT 10"
            ).fetchall()
            
            stats = {}
            if any(t in col_type.upper() for t in ['INT', 'FLOAT', 'DOUBLE', 'DECIMAL']):
                row = db_conn.execute(
                    f"SELECT MIN({col_name}), MAX({col_name}), AVG({col_name}) "
                    f"FROM {table_name}"
                ).fetchone()
                stats = {
                    "min": row[0],
                    "max": row[1],
                    "avg": round(row[2], 2) if row[2] else None
                }
            
            if 'DATE' in col_type.upper() or 'TIMESTAMP' in col_type.upper():
                date_range = db_conn.execute(
                    f"SELECT MIN({col_name}), MAX({col_name}) "
                    f"FROM {table_name} WHERE {col_name} IS NOT NULL"
                ).fetchone()
                stats = {
                    "min": str(date_range[0]) if date_range[0] else None,
                    "max": str(date_range[1]) if date_range[1] else None
                }
            
            profiles[col_name] = {
                "type": col_type,
                "null_rate": round(nc / max(row_count, 1), 4),
                "distinct_count": dc,
                "sample_values": [str(s[0]) for s in samples],
                "stats": stats
            }
        except Exception as e:
            logger.warning(f"Profile failed for {col_name}: {e}")
            profiles[col_name] = {
                "type": col_type,
                "null_rate": 0,
                "distinct_count": 0,
                "sample_values": [],
                "stats": {}
            }
    
    col_names_lower = [c[0].lower() for c in columns]
    column_mapping = _build_column_mapping(col_names_lower)
    
    sample = db_conn.execute(f"SELECT * FROM {table_name} LIMIT 5").fetchall()
    sample_cols = [d[0] for d in db_conn.description]
    sample_rows = [dict(zip(sample_cols, r)) for r in sample]
    for row in sample_rows:
        for k, v in row.items():
            if hasattr(v, 'isoformat'):
                row[k] = str(v)
            elif not isinstance(v, (str, int, float, bool)):
                row[k] = str(v)
    
    hash_input = f"{table_name}:{row_count}:{len(columns)}:"
    hash_input += json.dumps(profiles, default=str, sort_keys=True)[:500]
    data_hash = hashlib.md5(hash_input.encode()).hexdigest()
    
    manifest = {
        "dataset_id": dataset_id,
        "table_name": table_name,
        "row_count": row_count,
        "columns": [{"name": c[0], "type": c[1]} for c in columns],
        "profiles": profiles,
        "column_mapping": column_mapping,
        "sample_rows": sample_rows,
        "data_hash": data_hash,
        "analyzed_at": datetime.now().isoformat(),
        "version": 1
    }

    # Ensure table exists (safety for older databases)
    db_conn.execute("""
        CREATE TABLE IF NOT EXISTS dataset_manifest (
            dataset_id VARCHAR PRIMARY KEY,
            manifest_json TEXT,
            data_hash VARCHAR,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    db_conn.execute(
        "INSERT OR REPLACE INTO dataset_manifest "
        "(dataset_id, manifest_json, data_hash, updated_at) "
        "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
        [dataset_id, json.dumps(manifest, ensure_ascii=False, default=str), data_hash]
    )
    logger.info(
        f"Preanalysis complete for {table_name}: {row_count} rows, "
        f"{len(columns)} cols, hash={data_hash[:8]}"
    )
    return manifest


def _build_column_mapping(col_names: list[str]) -> dict:
    """Аналог _map_columns() из analytics.py, но по списку имён без SQL."""
    def find(*candidates):
        for c in candidates:
            if c in col_names:
                return c
        return 'NULL'
    
    return {
        'date': find('date', 'date_of_sale', 'date_of_sold', 'created_at',
                      'timestamp', 'purchase_date'),
        'revenue': find('revenue', 'sales_amount', 'retail_price', 'price',
                        'total', 'amount'),
        'profit': find('profit', 'markup', 'margin'),
        'quantity': find('quantity', 'quantity_sold', 'qty', 'count', 'product_count'),
        'order_id': find('order_id', 'id', 'purchase_id', 'transaction_id', 'sku_code'),
        'discount': find('discount', 'sale'),
        'product': find('product', 'name', 'sku_code', 'item'),
        'category': find('category', 'department', 'type'),
        'region': find('region', 'city', 'country', 'point_of_sale'),
        'manager': find('manager', 'user', 'employee')
    }


def load_manifest(db_conn, dataset_id: str) -> Optional[dict]:
    """Загрузить манифест. Возвращает None если нет."""
    if not dataset_id:
        return None
    try:
        row = db_conn.execute(
            "SELECT manifest_json FROM dataset_manifest WHERE dataset_id = ?",
            [dataset_id]
        ).fetchone()
        if row and row[0]:
            return json.loads(row[0])
    except Exception:
        pass
    return None


def is_manifest_valid(db_conn, dataset_id: str) -> bool:
    """Проверяет, существует ли манифест и совпадает ли row_count."""
    manifest = load_manifest(db_conn, dataset_id)
    if not manifest:
        return False
    try:
        table_name = manifest["table_name"]
        current_count = db_conn.execute(
            f"SELECT COUNT(*) FROM {table_name}"
        ).fetchone()[0]
        return current_count == manifest["row_count"]
    except Exception:
        return False


def needs_rebuild(db_conn, dataset_id: str) -> bool:
    """Проверяет, нужно ли перестроить манифест."""
    manifest = load_manifest(db_conn, dataset_id)
    if not manifest:
        return True
    
    try:
        db_conn.execute(f"SELECT 1 FROM {manifest['table_name']} LIMIT 1")
    except Exception:
        return True
    
    current_count = db_conn.execute(
        f"SELECT COUNT(*) FROM {manifest['table_name']}"
    ).fetchone()[0]
    if current_count != manifest["row_count"]:
        return True
    
    current_cols = db_conn.execute(
        "SELECT COUNT(*) FROM information_schema.columns WHERE table_name = ?",
        [manifest["table_name"]]
    ).fetchone()[0]
    if current_cols != len(manifest["columns"]):
        return True
    
    return False
