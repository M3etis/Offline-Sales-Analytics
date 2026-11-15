import asyncio
import time
import unittest

import duckdb

from app.services.streaming import _run_sql


class RunSqlTest(unittest.TestCase):
    def test_returns_rows_as_dicts(self):
        conn = duckdb.connect()
        rows = asyncio.run(_run_sql(conn, "SELECT 1 AS a, 'x' AS b"))
        self.assertEqual(rows, [{"a": 1, "b": "x"}])

    def test_event_loop_stays_responsive_and_cancel_interrupts(self):
        conn = duckdb.connect()
        heavy = "SELECT COUNT(*) FROM range(300000000) a, range(1000) b"

        async def go():
            ticks = 0
            task = asyncio.create_task(_run_sql(conn, heavy))
            t0 = time.monotonic()
            while time.monotonic() - t0 < 0.3:
                await asyncio.sleep(0.01)
                ticks += 1
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return ticks, time.monotonic() - t0

        started = time.monotonic()
        ticks, _ = asyncio.run(go())  # asyncio.run ждёт и поток с запросом
        self.assertGreater(ticks, 10)                       # цикл событий не стоял
        self.assertLess(time.monotonic() - started, 5)      # запрос прерван, а не досчитан


if __name__ == "__main__":
    unittest.main()
