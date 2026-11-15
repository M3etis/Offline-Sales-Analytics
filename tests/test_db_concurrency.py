import threading
import unittest

from app.core.dependencies import db_manager, get_db


class GetDbTest(unittest.TestCase):
    def test_parallel_requests_get_independent_cursors(self):
        """Две «ручки» одновременно: каждая читает свой результат, никто не ждёт глобальную блокировку."""
        db_manager.init_db()
        gen_a, gen_b = get_db(), get_db()
        a, b = next(gen_a), next(gen_b)
        try:
            self.assertIsNot(a, b)
            ra = a.execute("SELECT 42")
            b.execute("SELECT 1")
            self.assertEqual(ra.fetchall(), [(42,)])
        finally:
            for g in (gen_a, gen_b):
                with self.assertRaises(StopIteration):
                    next(g)

    def test_threads_do_not_mix_results(self):
        db_manager.init_db()
        errors = []

        def worker(n):
            gen = get_db()
            cur = next(gen)
            try:
                for _ in range(50):
                    if cur.execute("SELECT ?", [n]).fetchone()[0] != n:
                        errors.append(n)
            finally:
                gen.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
