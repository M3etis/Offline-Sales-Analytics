#!/usr/bin/env python3
"""Test 20 common marketing queries against the assistant."""

import sys
import json
import asyncio
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, PROJECT_ROOT)

from app.services.llm import ollama_client, get_system_prompt, _extract_sql, _adjust_limit, _inject_count_header
from app.services.analytics import _get_dataset_meta, _map_columns
from app.services.preanalysis import load_manifest
from app.core.dependencies import db_manager

QUERIES = [
    # 1. Базовые KPI
    ("Какая общая выручка и прибыль?", "kpi"),
    # 2. Топ товаров
    ("Топ 10 самых продаваемых товаров по выручке", "top"),
    # 3. Продажи по категориям
    ("Продажи по категориям с разбивкой по выручке", "summary"),
    # 4. Средний чек
    ("Какой средний чек по всем продажам?", "kpi"),
    # 5. Динамика продаж
    ("Покажи динамику продаж по месяцам", "chart"),
    # 6. Топ менеджеров
    ("Кто лучший менеджер по выручке?", "top"),
    # 7. Продажи по регионам
    ("Продажи по регионам — кто впереди?", "summary"),
    # 8. Pareto 80/20
    ("Какие товары дают 80% выручки? (принцип Парето)", "sql"),
    # 9. ABC-классификация
    ("Сделай ABC-анализ товаров по выручке", "sql"),
    # 10. RFM-сегментация
    ("Сегментируй менеджеров по RFM: давность, частота, сумма", "sql"),
    # 11. Влияние скидок
    ("Как скидки влияют на средний чек?", "sql"),
    # 12. Скользящее среднее
    ("Покажи скользящее среднее продаж за 7 дней", "chart"),
    # 13. Падение продаж
    ("Какие товары показали падение продаж в этом месяце?", "drop"),
    # 14. Самый продаваемый товар лучшего менеджера
    ("Какой самый продаваемый товар у самого успешного менеджера?", "sql"),
    # 15. Топ категория в лучшем регионе
    ("Какая категория товаров лидирует в регионе с наибольшими продажами?", "sql"),
    # 16. Эффективность менеджеров
    ("Сравни эффективность менеджеров: выручка на сделку и средний чек", "sql"),
    # 17. Региональный топ-3
    ("Покажи топ-3 категории в каждом регионе", "sql"),
    # 18. Товары из конкретной страны
    ("Покажи все товары из Франции", "sql"),
    # 19. Динамика среднего чека
    ("Как меняется средний чек по месяцам? Есть ли тренд?", "chart"),
    # 20. Конверсия возвратов
    ("Какой процент возвратов по категориям?", "sql"),
]

async def test_query(question, intent, db_conn, table_name, schema_str, col_names, manifest):
    """Test a single query."""
    result = {"question": question, "intent": intent}
    
    try:
        # Generate SQL
        system_prompt = get_system_prompt(table_name, schema_str, col_names)
        sql = await ollama_client.generate_sql(question, system_prompt, table_name, schema_str)
        sql = _adjust_limit(question, sql)
        result["sql"] = sql
        
        # Execute SQL
        try:
            raw_data = db_conn.execute(sql).fetchall()
            cols = [desc[0] for desc in db_conn.description] if db_conn.description else []
            data = [dict(zip(cols, row)) for row in raw_data]
            result["row_count"] = len(data)
            result["columns"] = cols
            result["sample"] = data[:3] if data else []
            result["status"] = "OK"
        except Exception as e:
            result["error"] = str(e)
            result["status"] = "SQL_ERROR"
            
    except Exception as e:
        result["error"] = str(e)
        result["status"] = "LLM_ERROR"
    
    return result

async def main():
    """Run all tests."""
    print("=" * 80)
    print("TESTING 20 MARKETING QUERIES")
    print("=" * 80)
    
    # Use a copy of the database to avoid lock conflicts
    import duckdb
    db_path = str(Path(PROJECT_ROOT) / 'data' / 'processed' / 'sales.duckdb')
    db_conn = duckdb.connect(db_path, read_only=True)
    
    # Use the main sales table
    table_name = "sales"
    
    # Get columns from information_schema
    cols_info = db_conn.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'sales' ORDER BY ordinal_position").fetchall()
    col_names = [c[0] for c in cols_info]
    schema_str = "\n".join([f"- {c[0]} {c[1]}" for c in cols_info])
    
    manifest = None  # Not needed for direct table access
    
    print(f"\nDataset: {table_name}")
    print(f"Columns: {', '.join(col_names)}")
    print()
    
    # Run tests
    results = []
    for i, (question, intent) in enumerate(QUERIES, 1):
        print(f"[{i:2d}/20] {question[:60]}...", end=" ", flush=True)
        result = await test_query(question, intent, db_conn, table_name, schema_str, col_names, manifest)
        results.append(result)
        
        status = result["status"]
        if status == "OK":
            rows = result["row_count"]
            print(f"✓ {rows} rows")
        else:
            print(f"✗ {status}: {result.get('error', '')[:50]}")
    
    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    
    ok_count = sum(1 for r in results if r["status"] == "OK")
    sql_errors = sum(1 for r in results if r["status"] == "SQL_ERROR")
    llm_errors = sum(1 for r in results if r["status"] == "LLM_ERROR")
    
    print(f"\nTotal: {len(results)}")
    print(f"OK: {ok_count}")
    print(f"SQL Errors: {sql_errors}")
    print(f"LLM Errors: {llm_errors}")
    
    # Print failed queries
    if sql_errors > 0 or llm_errors > 0:
        print("\n--- FAILED QUERIES ---")
        for r in results:
            if r["status"] != "OK":
                print(f"\nQ: {r['question']}")
                print(f"Status: {r['status']}")
                print(f"SQL: {r.get('sql', 'N/A')[:100]}")
                print(f"Error: {r.get('error', 'N/A')[:100]}")
    
    # Print successful queries with details
    print("\n--- SUCCESSFUL QUERIES ---")
    for r in results:
        if r["status"] == "OK":
            print(f"\nQ: {r['question']}")
            print(f"SQL: {r['sql'][:120]}...")
            print(f"Rows: {r['row_count']}, Columns: {r['columns']}")
            if r['sample']:
                print(f"Sample: {json.dumps(r['sample'][0], ensure_ascii=False, default=str)[:120]}")
    
    db_conn.close()

if __name__ == "__main__":
    asyncio.run(main())
