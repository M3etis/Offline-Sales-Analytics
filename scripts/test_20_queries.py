#!/usr/bin/env python3
"""Test 20 marketing queries through the app's WebSocket API."""

import json
import asyncio
import websockets
import httpx

BASE = "http://localhost:8000"
WS_URL = "ws://localhost:8000/ws/ai"

QUERIES = [
    "Какая общая выручка и прибыль?",
    "Топ 10 самых продаваемых товаров по выручке",
    "Продажи по категориям с разбивкой",
    "Какой средний чек по всем продажам?",
    "Покажи динамику продаж по месяцам",
    "Кто лучший менеджер по выручке?",
    "Продажи по регионам — кто впереди?",
    "Какие товары дают 80% выручки? Принцип Парето",
    "Сделай ABC-анализ товаров по выручке",
    "Сегментируй менеджеров по RFM: давность, частота, сумма",
    "Как скидки влияют на средний чек?",
    "Покажи скользящее среднее продаж за 7 дней",
    "Какие товары показали падение продаж в этом месяце?",
    "Какой самый продаваемый товар у самого успешного менеджера?",
    "Какая категория лидирует в регионе с наибольшими продажами?",
    "Сравни эффективность менеджеров: выручка на сделку и средний чек",
    "Покажи топ-3 категории в каждом регионе",
    "Покажи все товары из Франции",
    "Как меняется средний чек по месяцам? Есть ли тренд?",
    "Какой процент возвратов по категориям?",
]


async def get_token():
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{BASE}/api/v1/auth/login", data={"username": "admin", "password": "admin"})
        return resp.json()["access_token"]


async def ask_question(token, question):
    uri = f"{WS_URL}?token={token}"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"question": question, "extended": False}))

        answer_parts = []
        data = None
        sql = None
        error = None

        while True:
            msg = json.loads(await ws.recv())
            t = msg.get("type")

            if t == "token":
                answer_parts.append(msg.get("text", ""))
            elif t == "result":
                r = msg.get("data", {})
                data = r.get("data")
                sql = r.get("sql")
                answer = r.get("answer", "".join(answer_parts))
                error = r.get("error")
                return {
                    "answer": answer,
                    "sql": sql,
                    "data": data,
                    "error": error,
                    "rows": len(data) if isinstance(data, list) else 0,
                }
            elif t == "error":
                return {"answer": "", "sql": None, "data": None, "error": msg.get("message"), "rows": 0}


async def main():
    print("=" * 90)
    print("  ТЕСТ 20 МАРКЕТИНГОВЫХ ЗАПРОСОВ")
    print("=" * 90)

    token = await get_token()
    print(f"Token obtained\n")

    results = []
    for i, q in enumerate(QUERIES, 1):
        print(f"[{i:2d}/20] {q}")
        try:
            r = await ask_question(token, q)
            results.append(r)

            rows = r["rows"]
            status = "OK" if not r["error"] else "ERR"
            print(f"       → {status} | {rows} строк | SQL: {(r['sql'] or '')[:80]}")

            if r["answer"]:
                # Show first 120 chars of answer
                ans_short = r["answer"].replace("\n", " ")[:120]
                print(f"       Ответ: {ans_short}")

            if r["error"]:
                print(f"       Ошибка: {r['error'][:100]}")
            print()
        except Exception as e:
            print(f"       → EXCEPTION: {e}\n")
            results.append({"error": str(e), "rows": 0})

    # Summary
    print("=" * 90)
    print("  ИТОГО")
    print("=" * 90)
    ok = sum(1 for r in results if not r.get("error"))
    err = sum(1 for r in results if r.get("error"))
    with_data = sum(1 for r in results if r.get("rows", 0) > 0)
    print(f"  Всего: {len(results)} | Успешно: {ok} | С данными: {with_data} | Ошибки: {err}")

    if err:
        print("\n  ОШИБКИ:")
        for i, r in enumerate(results, 1):
            if r.get("error"):
                print(f"    [{i}] {QUERIES[i-1][:50]} → {r['error'][:80]}")


if __name__ == "__main__":
    asyncio.run(main())
