from __future__ import annotations
import duckdb
import logging
import re
from typing import Any, Optional
from datetime import date, timedelta

logger = logging.getLogger(__name__)


def _has_column(conn: duckdb.DuckDBPyConnection, column: str) -> bool:
    """Check if a column exists in the sales table."""
    try:
        cols = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'sales'"
        ).fetchall()
        return column in [c[0] for c in cols]
    except Exception:
        return False



def _get_dataset_meta(conn, dataset_id: Optional[str]) -> str:
    """Get the actual table name for a dataset."""
    if not dataset_id or dataset_id == 'default':
        return 'sales'
    try:
        res = conn.execute("SELECT table_name FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        if res and res[0]:
            table_name = res[0]
            # Validate table name to prevent SQL injection
            if not re.match(r'^(sales|dataset_[a-f0-9_]+)$', table_name):
                logger.error(f"Invalid table name format: {table_name}")
                return 'sales'
            return table_name
    except Exception as e:
        logger.error(f"Error getting dataset metadata: {e}")
        pass
    return 'sales'


def get_group_datasets(conn, dataset_id: Optional[str]) -> list[dict]:
    """Get all datasets in the same group as the given dataset_id."""
    if not dataset_id or dataset_id == 'default':
        return [{"id": "default", "name": "sales", "table_name": "sales", "columns": None, "relationships": None}]
    try:
        row = conn.execute(
            "SELECT source_group FROM datasets WHERE id = ?", (dataset_id,)
        ).fetchone()
        if not row or not row[0]:
            ds = conn.execute(
                "SELECT id, name, table_name, columns, relationships FROM datasets WHERE id = ?",
                (dataset_id,)
            ).fetchone()
            if ds:
                return [{"id": ds[0], "name": ds[1], "table_name": ds[2], "columns": ds[3], "relationships": ds[4]}]
            return []

        source_group = row[0]
        rows = conn.execute(
            "SELECT id, name, table_name, columns, relationships FROM datasets WHERE source_group = ?",
            (source_group,)
        ).fetchall()
        return [
            {"id": r[0], "name": r[1], "table_name": r[2], "columns": r[3], "relationships": r[4]}
            for r in rows
        ]
    except Exception as e:
        logger.error(f"Error getting group datasets: {e}")
        return []




def _safe_date(col_name: str) -> str:
    if col_name == 'NULL':
        return 'NULL'
    return f"COALESCE(TRY_CAST({col_name} AS TIMESTAMP), try_strptime(CAST({col_name} AS VARCHAR), '%d.%m.%Y'), try_strptime(CAST({col_name} AS VARCHAR), '%m/%d/%Y'))"


def _group_expr(col_name: str, fallback: str) -> str:
    """Return a GROUP BY expression that matches the SELECT's COALESCE fallback."""
    if col_name == 'NULL':
        return f"COALESCE(NULL, '{fallback}')"
    return col_name


def _map_columns(conn, table_name: str, manifest: dict = None) -> dict:
    """Dynamically map available columns to standard sales columns.
    
    Uses pre-computed column_mapping from manifest when available (fast path),
    otherwise falls back to querying information_schema (slow path).
    """
    if manifest and "column_mapping" in manifest:
        return manifest["column_mapping"]
    
    try:
        cols_query = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = ?", [table_name]).fetchall()
        cols = [c[0].lower() for c in cols_query]
    except Exception:
        cols = []
        
    return {
        'date': next((c for c in ['date', 'date_of_sale', 'date_of_sold', 'created_at', 'timestamp', 'purchase_date'] if c in cols), 'NULL'),
        'revenue': next((c for c in ['revenue', 'sales_amount', 'retail_price', 'price', 'total', 'amount'] if c in cols), 'NULL'),
        'profit': next((c for c in ['profit', 'markup', 'margin'] if c in cols), 'NULL'),
        'quantity': next((c for c in ['quantity', 'quantity_sold', 'qty', 'count', 'product_count'] if c in cols), 'NULL'),
        'order_id': next((c for c in ['order_id', 'id', 'purchase_id', 'transaction_id', 'sku_code'] if c in cols), 'NULL'),
        'discount': next((c for c in ['discount', 'sale'] if c in cols), 'NULL'),
        'product': next((c for c in ['product', 'name', 'sku_code', 'item'] if c in cols), 'NULL'),
        'category': next((c for c in ['category', 'department', 'type'] if c in cols), 'NULL'),
        'region': next((c for c in ['region', 'city', 'country', 'point_of_sale'] if c in cols), 'NULL'),
        'manager': next((c for c in ['manager', 'user', 'employee'] if c in cols), 'NULL')
    }


def _build_filters(start_date: Optional[str], end_date: Optional[str], dataset_id: Optional[str] = None, date_col: str = 'date') -> tuple[str, list]:
    """Build a SQL WHERE clause for filters. Returns (clause, params)."""
    conditions = []
    params = []
    safe_date = _safe_date(date_col)
    if start_date:
        conditions.append(f"{safe_date} >= TRY_CAST(? AS TIMESTAMP)")
        params.append(start_date)
    if end_date:
        conditions.append(f"{safe_date} <= TRY_CAST(? AS TIMESTAMP)")
        params.append(end_date)
    clause = " AND ".join(conditions) if conditions else "1=1"
    return clause, params


def _safe_query(conn: duckdb.DuckDBPyConnection, sql: str) -> list[dict]:
    """Execute a query and return results as list of dicts."""
    try:
        result = conn.execute(sql)
        if result.description:
            columns = [desc[0] for desc in result.description]
            rows = result.fetchall()
            return [dict(zip(columns, row)) for row in rows]
        return []
    except Exception as e:
        logger.error(f"Analytics query error: {e}\nSQL: {sql}")
        return []


def _safe_query_params(conn: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> list[dict]:
    """Execute a parameterized query and return results as list of dicts."""
    try:
        if params:
            result = conn.execute(sql, params)
        else:
            result = conn.execute(sql)
        if result.description:
            columns = [desc[0] for desc in result.description]
            rows = result.fetchall()
            return [dict(zip(columns, row)) for row in rows]
        return []
    except Exception as e:
        logger.error(f"Analytics query error: {e}\nSQL: {sql}")
        return []


def get_kpis(conn: duckdb.DuckDBPyConnection, start_date: Optional[str] = None, end_date: Optional[str] = None, dataset_id: Optional[str] = None, manifest: dict = None) -> dict[str, Any]:
    table_name = _get_dataset_meta(conn, dataset_id)
    cmap = _map_columns(conn, table_name, manifest)

    """Calculate key performance indicators."""
    date_filter, date_params = _build_filters(start_date, end_date, dataset_id, cmap['date'] if 'cmap' in locals() else 'date')

    # Detect if profit column is actually a markup multiplier
    # If markup column exists and profit is mapped to it, calculate profit from sales_amount - price * quantity_sold
    profit_expr = f"COALESCE(SUM(TRY_CAST(REPLACE(CAST({cmap['profit']} AS VARCHAR), ',', '.') AS DOUBLE)), 0)"
    try:
        cols = [c[0].lower() for c in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = ?", [table_name]).fetchall()]
        if cmap['profit'] == 'markup' and 'sales_amount' in cols and 'price' in cols:
            qty_col = 'quantity_sold' if 'quantity_sold' in cols else 'quantity'
            profit_expr = f"COALESCE(SUM(TRY_CAST(REPLACE(CAST(sales_amount AS VARCHAR), ',', '.') AS DOUBLE) - TRY_CAST(REPLACE(CAST(price AS VARCHAR), ',', '.') AS DOUBLE) * TRY_CAST(REPLACE(CAST({qty_col} AS VARCHAR), ',', '.') AS DOUBLE)), 0)"
    except Exception:
        pass

    sql = f"""
    SELECT
        COALESCE(SUM(TRY_CAST(REPLACE(CAST({cmap['revenue']} AS VARCHAR), ',', '.') AS DOUBLE)), 0) as total_revenue,
        {profit_expr} as total_profit,
        COUNT(DISTINCT {cmap['order_id']}) as order_count,
        COALESCE(AVG(TRY_CAST(REPLACE(CAST({cmap['revenue']} AS VARCHAR), ',', '.') AS DOUBLE)), 0) as avg_check,
        COALESCE(AVG({cmap['discount']}), 0) as avg_discount,
        COALESCE(SUM(TRY_CAST(REPLACE(CAST({cmap['quantity']} AS VARCHAR), ',', '.') AS DOUBLE)), 0) as total_quantity
    FROM {table_name}
    WHERE {date_filter}
    """

    result = _safe_query_params(conn, sql, date_params if date_params else None)
    kpis = result[0] if result else {
        "total_revenue": 0, "total_profit": 0, "order_count": 0,
        "avg_check": 0, "avg_discount": 0, "total_quantity": 0
    }
    
    # Calculate previous period for comparison
    if start_date and end_date:
        from datetime import datetime
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')
        period_days = (end - start).days
        prev_start = (start - timedelta(days=period_days + 1)).strftime('%Y-%m-%d')
        prev_end = (start - timedelta(days=1)).strftime('%Y-%m-%d')
        
        prev_filter, prev_params = _build_filters(prev_start, prev_end, dataset_id)
        prev_sql = f"""
        SELECT
            COALESCE(SUM(TRY_CAST(REPLACE(CAST({cmap['revenue']} AS VARCHAR), ',', '.') AS DOUBLE)), 0) as total_revenue,
            {profit_expr} as total_profit,
            COUNT(DISTINCT {cmap['order_id']}) as order_count
        FROM {table_name} WHERE {prev_filter}
        """
        prev = _safe_query_params(conn, prev_sql, prev_params if prev_params else None)
        if prev and prev[0]['total_revenue'] > 0:
            prev_data = prev[0]
            kpis['revenue_change'] = round(((kpis['total_revenue'] - prev_data['total_revenue']) / prev_data['total_revenue']) * 100, 1)
            kpis['profit_change'] = round(((kpis['total_profit'] - prev_data['total_profit']) / prev_data['total_profit']) * 100, 1) if prev_data['total_profit'] else 0
            kpis['orders_change'] = round(((kpis['order_count'] - prev_data['order_count']) / prev_data['order_count']) * 100, 1) if prev_data['order_count'] else 0
        else:
            kpis['revenue_change'] = None
            kpis['profit_change'] = None
            kpis['orders_change'] = None
    else:
        kpis['revenue_change'] = None
        kpis['profit_change'] = None
        kpis['orders_change'] = None
    
    # Round numeric values
    for key in ['total_revenue', 'total_profit', 'avg_check', 'avg_discount']:
        if key in kpis and kpis[key] is not None:
            kpis[key] = round(float(kpis[key]), 2)
    
    return kpis


def sales_by_category(conn: duckdb.DuckDBPyConnection, start_date: Optional[str] = None, end_date: Optional[str] = None, dataset_id: Optional[str] = None, manifest: dict = None) -> list[dict[str, Any]]:
    table_name = _get_dataset_meta(conn, dataset_id)
    cmap = _map_columns(conn, table_name, manifest)
    """Group sales metrics by product category."""
    date_filter, date_params = _build_filters(start_date, end_date, dataset_id, cmap['date'] if 'cmap' in locals() else 'date')
    sql = f"""
    SELECT
        CAST(COALESCE({cmap['category']}, 'Не указана') AS VARCHAR) as category,
        ROUND(SUM(TRY_CAST(REPLACE(CAST({cmap['revenue']} AS VARCHAR), ',', '.') AS DOUBLE)), 2) as revenue,
        ROUND(SUM(TRY_CAST(REPLACE(CAST({cmap['profit']} AS VARCHAR), ',', '.') AS DOUBLE)), 2) as profit,
        COUNT(DISTINCT {cmap['order_id']}) as orders,
        SUM(TRY_CAST(REPLACE(CAST({cmap['quantity']} AS VARCHAR), ',', '.') AS DOUBLE)) as quantity,
        ROUND(SUM(TRY_CAST(REPLACE(CAST({cmap['revenue']} AS VARCHAR), ',', '.') AS DOUBLE)) * 100.0 / NULLIF((SELECT SUM(TRY_CAST(REPLACE(CAST({cmap['revenue']} AS VARCHAR), ',', '.') AS DOUBLE)) FROM {table_name} WHERE {date_filter}), 0), 1) as share
    FROM {table_name}
    WHERE {date_filter}
    GROUP BY {_group_expr(cmap['category'], 'Не указана')}
    ORDER BY revenue DESC
    """
    return _safe_query_params(conn, sql, date_params if date_params else None)


def sales_by_region(conn: duckdb.DuckDBPyConnection, start_date: Optional[str] = None, end_date: Optional[str] = None, dataset_id: Optional[str] = None, manifest: dict = None) -> list[dict[str, Any]]:
    table_name = _get_dataset_meta(conn, dataset_id)
    cmap = _map_columns(conn, table_name, manifest)
    """Group sales metrics by region."""
    date_filter, date_params = _build_filters(start_date, end_date, dataset_id, cmap['date'] if 'cmap' in locals() else 'date')
    sql = f"""
    SELECT
        CAST(COALESCE({cmap['region']}, 'Не указан') AS VARCHAR) as region,
        ROUND(SUM(TRY_CAST(REPLACE(CAST({cmap['revenue']} AS VARCHAR), ',', '.') AS DOUBLE)), 2) as revenue,
        ROUND(SUM(TRY_CAST(REPLACE(CAST({cmap['profit']} AS VARCHAR), ',', '.') AS DOUBLE)), 2) as profit,
        COUNT(DISTINCT {cmap['order_id']}) as orders,
        SUM(TRY_CAST(REPLACE(CAST({cmap['quantity']} AS VARCHAR), ',', '.') AS DOUBLE)) as quantity
    FROM {table_name}
    WHERE {date_filter}
    GROUP BY {_group_expr(cmap['region'], 'Не указан')}
    ORDER BY revenue DESC
    """
    return _safe_query_params(conn, sql, date_params if date_params else None)


def sales_by_manager(conn: duckdb.DuckDBPyConnection, start_date: Optional[str] = None, end_date: Optional[str] = None, dataset_id: Optional[str] = None, manifest: dict = None) -> list[dict[str, Any]]:
    table_name = _get_dataset_meta(conn, dataset_id)
    cmap = _map_columns(conn, table_name, manifest)
    """Group sales metrics by manager."""
    date_filter, date_params = _build_filters(start_date, end_date, dataset_id, cmap['date'] if 'cmap' in locals() else 'date')
    sql = f"""
    SELECT
        CAST(COALESCE({cmap['manager']}, 'Не указан') AS VARCHAR) as manager,
        ROUND(SUM(TRY_CAST(REPLACE(CAST({cmap['revenue']} AS VARCHAR), ',', '.') AS DOUBLE)), 2) as revenue,
        ROUND(SUM(TRY_CAST(REPLACE(CAST({cmap['profit']} AS VARCHAR), ',', '.') AS DOUBLE)), 2) as profit,
        COUNT(DISTINCT {cmap['order_id']}) as orders,
        SUM(TRY_CAST(REPLACE(CAST({cmap['quantity']} AS VARCHAR), ',', '.') AS DOUBLE)) as quantity
    FROM {table_name}
    WHERE {date_filter}
    GROUP BY {_group_expr(cmap['manager'], 'Не указан')}
    ORDER BY revenue DESC
    """
    return _safe_query_params(conn, sql, date_params if date_params else None)


def top_products(
    conn: duckdb.DuckDBPyConnection, 
    start_date: Optional[str] = None, 
    end_date: Optional[str] = None,
    dataset_id: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 10,
    sort_by: str = 'revenue',
    manifest: dict = None
) -> list[dict]:
    """Get top N products."""
    table_name = _get_dataset_meta(conn, dataset_id)
    cmap = _map_columns(conn, table_name, manifest)
    date_filter, date_params = _build_filters(start_date, end_date, dataset_id, cmap['date'] if 'cmap' in locals() else 'date')
    order_col = 'revenue' if sort_by == 'revenue' else 'profit'
    params = list(date_params) if date_params else []
    category_filter = ""
    if category:
        category_filter = f" AND CAST({cmap['category']} AS VARCHAR) = ?"
        params.append(category)
    sql = f"""
    SELECT
        CAST(COALESCE({cmap['product']}, 'Не указан') AS VARCHAR) as product,
        CAST(COALESCE({cmap['category']}, 'Не указана') AS VARCHAR) as category,
        ROUND(SUM(TRY_CAST(REPLACE(CAST({cmap['revenue']} AS VARCHAR), ',', '.') AS DOUBLE)), 2) as revenue,
        ROUND(SUM(TRY_CAST(REPLACE(CAST({cmap['profit']} AS VARCHAR), ',', '.') AS DOUBLE)), 2) as profit,
        SUM(TRY_CAST(REPLACE(CAST({cmap['quantity']} AS VARCHAR), ',', '.') AS DOUBLE)) as quantity,
        COUNT(DISTINCT {cmap['order_id']}) as orders
    FROM {table_name}
    WHERE {date_filter}{category_filter}
    GROUP BY {_group_expr(cmap['product'], 'Не указан')}, {_group_expr(cmap['category'], 'Не указана')}
    ORDER BY {order_col} DESC
    LIMIT {limit}
    """
    return _safe_query_params(conn, sql, params if params else None)


def sales_timeline(conn: duckdb.DuckDBPyConnection, start_date: Optional[str] = None, end_date: Optional[str] = None, dataset_id: Optional[str] = None, granularity: str = 'month', manifest: dict = None) -> list[dict[str, Any]]:
    table_name = _get_dataset_meta(conn, dataset_id)
    cmap = _map_columns(conn, table_name, manifest)
    """Get sales timeline aggregated by specified granularity."""
    date_filter, date_params = _build_filters(start_date, end_date, dataset_id, cmap['date'] if 'cmap' in locals() else 'date')
    
    if granularity == 'day':
        trunc = f"DATE_TRUNC('day', {_safe_date(cmap['date'])})"
        fmt = trunc
    elif granularity == 'week':
        trunc = f"DATE_TRUNC('week', {_safe_date(cmap['date'])})"
        fmt = trunc
    else:  # month
        trunc = f"DATE_TRUNC('month', {_safe_date(cmap['date'])})"
        fmt = trunc
    
    sql = f"""
    SELECT 
        CAST({fmt} AS VARCHAR) as period,
        ROUND(SUM(TRY_CAST(REPLACE(CAST({cmap['revenue']} AS VARCHAR), ',', '.') AS DOUBLE)), 2) as revenue,
        ROUND(SUM(TRY_CAST(REPLACE(CAST({cmap['profit']} AS VARCHAR), ',', '.') AS DOUBLE)), 2) as profit,
        COUNT(DISTINCT {cmap['order_id']}) as orders,
        SUM(TRY_CAST(REPLACE(CAST({cmap['quantity']} AS VARCHAR), ',', '.') AS DOUBLE)) as quantity
    FROM {table_name}
    WHERE {date_filter} AND {_safe_date(cmap['date'])} IS NOT NULL
    GROUP BY {trunc}
    ORDER BY {trunc}
    """
    return _safe_query_params(conn, sql, date_params if date_params else None)


def compare_periods(
    conn: duckdb.DuckDBPyConnection,
    current_start: str,
    current_end: str,
    prev_start: str,
    prev_end: str,
    dataset_id: Optional[str] = None,
    manifest: dict = None
) -> dict[str, Any]:
    """Compare two periods."""
    table_name = _get_dataset_meta(conn, dataset_id)
    cmap = _map_columns(conn, table_name, manifest)
    current_filter, current_params = _build_filters(current_start, current_end, dataset_id)
    prev_filter, prev_params = _build_filters(prev_start, prev_end, dataset_id)

    def get_period_stats(date_filter: str, date_params: list) -> dict:
        sql = f"""
        SELECT
            COALESCE(SUM(TRY_CAST(REPLACE(CAST({cmap['revenue']} AS VARCHAR), ',', '.') AS DOUBLE)), 0) as revenue,
            COALESCE(SUM(TRY_CAST(REPLACE(CAST({cmap['profit']} AS VARCHAR), ',', '.') AS DOUBLE)), 0) as profit,
            COUNT(DISTINCT {cmap['order_id']}) as orders,
            COALESCE(AVG(TRY_CAST(REPLACE(CAST({cmap['revenue']} AS VARCHAR), ',', '.') AS DOUBLE)), 0) as avg_check,
            COALESCE(SUM(TRY_CAST(REPLACE(CAST({cmap['quantity']} AS VARCHAR), ',', '.') AS DOUBLE)), 0) as quantity
        FROM {table_name} WHERE {date_filter}
        """
        result = _safe_query_params(conn, sql, date_params if date_params else None)
        return result[0] if result else {"revenue": 0, "profit": 0, "orders": 0, "avg_check": 0, "quantity": 0}

    current = get_period_stats(current_filter, current_params)
    previous = get_period_stats(prev_filter, prev_params)
    
    def calc_change(curr: float, prev: float) -> Optional[float]:
        if prev and prev != 0:
            return round(((curr - prev) / prev) * 100, 1)
        return None
    
    return {
        "current": {k: round(float(v), 2) for k, v in current.items()},
        "previous": {k: round(float(v), 2) for k, v in previous.items()},
        "changes": {
            "revenue": calc_change(current['revenue'], previous['revenue']),
            "profit": calc_change(current['profit'], previous['profit']),
            "orders": calc_change(current['orders'], previous['orders']),
            "avg_check": calc_change(current['avg_check'], previous['avg_check']),
        }
    }


def find_anomalies(
    conn: duckdb.DuckDBPyConnection,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    dataset_id: Optional[str] = None,
    threshold: float = 0.2
) -> list[dict]:
    """Find anomalies (deviations from average)."""
    table_name = _get_dataset_meta(conn, dataset_id)
    cmap = _map_columns(conn, table_name)
    date_filter, date_params = _build_filters(start_date, end_date, dataset_id, cmap['date'] if 'cmap' in locals() else 'date')

    # Find categories with {cmap['revenue']} significantly below average
    sql = f"""
    WITH category_stats AS (
        SELECT
            category,
            SUM(TRY_CAST(REPLACE(CAST({cmap['revenue']} AS VARCHAR), ',', '.') AS DOUBLE)) as revenue,
            AVG(SUM(TRY_CAST(REPLACE(CAST({cmap['revenue']} AS VARCHAR), ',', '.') AS DOUBLE))) OVER () as avg_revenue
        FROM {table_name}
        WHERE {date_filter}
        GROUP BY {_group_expr(cmap['category'], 'Не указана')}
    )
    SELECT
        category,
        ROUND(revenue, 2) as revenue,
        ROUND(avg_revenue, 2) as avg_revenue,
        ROUND((revenue - avg_revenue) / NULLIF(avg_revenue, 0), 3) as deviation
    FROM category_stats
    WHERE ABS((revenue - avg_revenue) / NULLIF(avg_revenue, 0)) > {threshold}
    ORDER BY deviation
    """
    anomalies = _safe_query_params(conn, sql, date_params if date_params else None)
    
    result = []
    for a in anomalies:
        deviation = float(a.get('deviation', 0))
        result.append({
            "type": "category_anomaly",
            "entity": a['category'],
            "metric": "revenue",
            "value": a['revenue'],
            "average": a['avg_revenue'],
            "deviation_pct": round(deviation * 100, 1),
            "severity": "high" if abs(deviation) > 0.5 else "medium",
            "description": f"Категория '{a['category']}': выручка {'ниже' if deviation < 0 else 'выше'} средней на {abs(round(deviation * 100, 1))}%"
        })
    
    return result


def category_share(
    conn: duckdb.DuckDBPyConnection,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    dataset_id: Optional[str] = None
) -> list[dict]:
    """Calculate category share in total revenue."""
    return sales_by_category(conn, start_date, end_date, dataset_id)


def find_weak_spots(
    conn: duckdb.DuckDBPyConnection,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    dataset_id: Optional[str] = None,
    manifest: dict = None
) -> list[dict]:
    table_name = _get_dataset_meta(conn, dataset_id)
    cmap = _map_columns(conn, table_name, manifest)
    """Find weak spots using rule-based analysis."""
    weak_spots = []
    date_filter, date_params = _build_filters(start_date, end_date, dataset_id, cmap['date'] if cmap['date'] != 'NULL' else 'date')

    # 1. Categories with low profit margin (only if category and profit cols exist)
    if cmap['category'] != 'NULL' and cmap['profit'] != 'NULL' and cmap['revenue'] != 'NULL':
        sql = f"""
        SELECT 
            CAST(COALESCE({cmap['category']}, 'Не указана') AS VARCHAR) as category,
            ROUND(SUM(TRY_CAST(REPLACE(CAST({cmap['revenue']} AS VARCHAR), ',', '.') AS DOUBLE)), 2) as revenue,
            ROUND(SUM(TRY_CAST(REPLACE(CAST({cmap['profit']} AS VARCHAR), ',', '.') AS DOUBLE)), 2) as profit,
            ROUND(SUM(TRY_CAST(REPLACE(CAST({cmap['profit']} AS VARCHAR), ',', '.') AS DOUBLE)) * 100.0 / NULLIF(SUM(TRY_CAST(REPLACE(CAST({cmap['revenue']} AS VARCHAR), ',', '.') AS DOUBLE)), 0), 1) as margin_pct
        FROM {table_name}
        WHERE {date_filter}
        GROUP BY {_group_expr(cmap['category'], 'Не указана')}
        HAVING SUM(TRY_CAST(REPLACE(CAST({cmap['revenue']} AS VARCHAR), ',', '.') AS DOUBLE)) > 0
            AND (SUM(TRY_CAST(REPLACE(CAST({cmap['profit']} AS VARCHAR), ',', '.') AS DOUBLE)) * 100.0 / NULLIF(SUM(TRY_CAST(REPLACE(CAST({cmap['revenue']} AS VARCHAR), ',', '.') AS DOUBLE)), 0)) < 10
        ORDER BY margin_pct
        """
        low_margin = _safe_query_params(conn, sql, date_params if date_params else None)
        for item in low_margin:
            weak_spots.append({
                "type": "low_margin",
                "entity": item.get('category', '—'),
                "description": f"Категория '{item.get('category', '—')}' имеет низкую маржу: {item.get('margin_pct', 0)}%",
                "severity": "high" if float(item.get('margin_pct', 0) or 0) < 5 else "medium",
                "metric": "margin",
                "value": item.get('margin_pct', 0)
            })

    # 2. Products with high discount and low profit
    if cmap['product'] != 'NULL' and cmap['discount'] != 'NULL' and cmap['profit'] != 'NULL':
        sql = f"""
        SELECT 
            CAST(COALESCE({cmap['product']}, 'Не указан') AS VARCHAR) as product,
            ROUND(AVG(TRY_CAST(REPLACE(CAST({cmap['discount']} AS VARCHAR), ',', '.') AS DOUBLE)), 1) as avg_discount,
            ROUND(SUM(TRY_CAST(REPLACE(CAST({cmap['profit']} AS VARCHAR), ',', '.') AS DOUBLE)), 2) as total_profit,
            COUNT(*) as sales_count
        FROM {table_name}
        WHERE {date_filter}
        GROUP BY {_group_expr(cmap['product'], 'Не указан')}
        HAVING AVG(TRY_CAST(REPLACE(CAST({cmap['discount']} AS VARCHAR), ',', '.') AS DOUBLE)) > 15
            AND SUM(TRY_CAST(REPLACE(CAST({cmap['profit']} AS VARCHAR), ',', '.') AS DOUBLE)) < 0
        ORDER BY total_profit
        LIMIT 10
        """
        high_discount = _safe_query_params(conn, sql, date_params if date_params else None)
        for item in high_discount:
            weak_spots.append({
                "type": "high_discount_low_profit",
                "entity": item.get('product', '—'),
                "description": f"Товар '{item.get('product', '—')}': средняя скидка {item.get('avg_discount', 0)}%, убыток {item.get('total_profit', 0)}",
                "severity": "high",
                "metric": "profit",
                "value": item.get('total_profit', 0)
            })

    # 3. Regions with declining performance
    if cmap['region'] != 'NULL' and cmap['revenue'] != 'NULL' and cmap['date'] != 'NULL':
        sql = f"""
        WITH monthly AS (
            SELECT 
                CAST(COALESCE({cmap['region']}, 'Не указан') AS VARCHAR) as region,
                DATE_TRUNC('month', {_safe_date(cmap['date'])}) as month,
                SUM(TRY_CAST(REPLACE(CAST({cmap['revenue']} AS VARCHAR), ',', '.') AS DOUBLE)) as revenue
            FROM {table_name}
            WHERE {date_filter} AND {_safe_date(cmap['date'])} IS NOT NULL
            GROUP BY {_group_expr(cmap['region'], 'Не указан')}, DATE_TRUNC('month', {_safe_date(cmap['date'])})
        ),
        with_lag AS (
            SELECT *,
                LAG(revenue) OVER (PARTITION BY region ORDER BY month) as prev_revenue
            FROM monthly
        )
        SELECT 
            region,
            ROUND(revenue, 2) as current_revenue,
            ROUND(prev_revenue, 2) as prev_revenue,
            ROUND((revenue - prev_revenue) * 100.0 / NULLIF(prev_revenue, 0), 1) as change_pct
        FROM with_lag
        WHERE prev_revenue IS NOT NULL 
            AND (revenue - prev_revenue) * 100.0 / NULLIF(prev_revenue, 0) < -20
        ORDER BY change_pct
        LIMIT 5
        """
        declining_regions = _safe_query_params(conn, sql, date_params if date_params else None)
        for item in declining_regions:
            weak_spots.append({
                "type": "region_decline",
                "entity": item.get('region', '—'),
                "description": f"Регион '{item.get('region', '—')}': падение выручки на {abs(float(item.get('change_pct', 0) or 0))}%",
                "severity": "high" if abs(float(item.get('change_pct', 0) or 0)) > 30 else "medium",
                "metric": "revenue_change",
                "value": item.get('change_pct', 0)
            })

    # 4. Managers with below-average performance
    if cmap['manager'] != 'NULL' and cmap['revenue'] != 'NULL':
        sql = f"""
        WITH manager_stats AS (
            SELECT 
                CAST(COALESCE({cmap['manager']}, 'Не указан') AS VARCHAR) as manager,
                SUM(TRY_CAST(REPLACE(CAST({cmap['revenue']} AS VARCHAR), ',', '.') AS DOUBLE)) as revenue,
                AVG(SUM(TRY_CAST(REPLACE(CAST({cmap['revenue']} AS VARCHAR), ',', '.') AS DOUBLE))) OVER () as avg_revenue
            FROM {table_name}
            WHERE {date_filter}
            GROUP BY {_group_expr(cmap['manager'], 'Не указан')}
        )
        SELECT 
            manager,
            ROUND(revenue, 2) as revenue,
            ROUND(avg_revenue, 2) as avg_revenue,
            ROUND((revenue - avg_revenue) * 100.0 / NULLIF(avg_revenue, 0), 1) as diff_pct
        FROM manager_stats
        WHERE revenue < avg_revenue * 0.5
        ORDER BY revenue
        """
        weak_managers = _safe_query_params(conn, sql, date_params if date_params else None)
        for item in weak_managers:
            weak_spots.append({
                "type": "weak_manager",
                "entity": item.get('manager', '—'),
                "description": f"Менеджер '{item.get('manager', '—')}': выручка на {abs(float(item.get('diff_pct', 0) or 0))}% ниже средней",
                "severity": "medium",
                "metric": "revenue",
                "value": item.get('revenue', 0)
            })

    # 5. Top products by revenue as a fallback insight when classic columns missing
    if not weak_spots and cmap['revenue'] != 'NULL' and cmap['product'] != 'NULL':
        sql = f"""
        SELECT 
            CAST(COALESCE({cmap['product']}, 'Не указан') AS VARCHAR) as product,
            ROUND(SUM(TRY_CAST(REPLACE(CAST({cmap['revenue']} AS VARCHAR), ',', '.') AS DOUBLE)), 2) as revenue,
            COUNT(*) as sales_count
        FROM {table_name}
        WHERE {date_filter}
        GROUP BY {_group_expr(cmap['product'], 'Не указан')}
        ORDER BY revenue ASC
        LIMIT 5
        """
        bottom_products = _safe_query_params(conn, sql, date_params if date_params else None)
        for item in bottom_products:
            weak_spots.append({
                "type": "low_revenue_product",
                "entity": item.get('product', '—'),
                "description": f"Товар с наименьшей выручкой: '{item.get('product', '—')}' — {item.get('revenue', 0)}",
                "severity": "medium",
                "metric": "revenue",
                "value": item.get('revenue', 0)
            })

    return weak_spots


def get_table_data(
    conn: duckdb.DuckDBPyConnection,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    category: Optional[str] = None,
    region: Optional[str] = None,
    manager: Optional[str] = None,
    sort_by: str = 'date',
    sort_order: str = 'desc',
    page: int = 1,
    page_size: int = 50,
    dataset_id: Optional[str] = None,
    search: Optional[str] = None
) -> dict:
    """Get paginated table data with filters."""
    table_name = _get_dataset_meta(conn, dataset_id)
    cmap = _map_columns(conn, table_name)
    conditions = []

    date_filter, date_params = _build_filters(start_date, end_date, dataset_id, cmap['date'] if 'cmap' in locals() else 'date')
    params = list(date_params) if date_params else []
    if date_filter != "1=1":
        conditions.append(date_filter)
    if category:
        conditions.append("category = ?")
        params.append(category)
    if region:
        conditions.append("region = ?")
        params.append(region)
    if manager:
        conditions.append("manager = ?")
        params.append(manager)

    # Global search across all text columns
    if search:
        try:
            cols_query = conn.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ?", [table_name]).fetchall()
            text_cols = [c[0] for c in cols_query if c[1].lower() in ('varchar', 'text', 'character varying')]
            if text_cols:
                search_conditions = [f"CAST({col} AS VARCHAR) ILIKE ?" for col in text_cols]
                conditions.append(f"({' OR '.join(search_conditions)})")
                search_term = f"%{search}%"
                params.extend([search_term] * len(text_cols))
        except Exception:
            pass

    where = " AND ".join(conditions) if conditions else "1=1"
    offset = (page - 1) * page_size
    
    # Validate sort column dynamically based on actual columns in the table
    try:
        cols_query = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = ?", [table_name]).fetchall()
        actual_cols = [c[0] for c in cols_query]
    except Exception:
        actual_cols = ['date']
        
    if sort_by not in actual_cols:
        # Default to first column if date is not available
        sort_by = actual_cols[0] if actual_cols else 'date'

    sort_dir = 'DESC' if sort_order.lower() == 'desc' else 'ASC'
    
    # Check if order_id exists to use it as secondary sort
    secondary_sort = ", order_id DESC" if "order_id" in actual_cols else ""
    
    count_sql = f"SELECT COUNT(*) as cnt FROM {table_name} WHERE {where}"
    count_result = _safe_query_params(conn, count_sql, params if params else None)
    total = count_result[0]['cnt'] if count_result else 0

    data_sql = f"""
    SELECT * FROM {table_name}
    WHERE {where}
    ORDER BY {sort_by} {sort_dir}{secondary_sort}
    LIMIT {page_size} OFFSET {offset}
    """
    rows = _safe_query_params(conn, data_sql, params if params else None)
    
    # Convert date objects to strings
    for row in rows:
        for key, val in row.items():
            if hasattr(val, 'isoformat'):
                row[key] = val.isoformat()
    
    return {
        "data": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size
    }
