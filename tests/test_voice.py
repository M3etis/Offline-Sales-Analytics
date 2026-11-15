import asyncio
import unittest
from concurrent.futures.process import BrokenProcessPool
from unittest import mock

from app.services import voice


class _BrokenPool:
    def submit(self, *a, **k):
        raise BrokenProcessPool("worker died")

    def shutdown(self, *a, **k):
        pass


class WhisperPoolRecoveryTest(unittest.TestCase):
    def test_broken_pool_is_recreated(self):
        voice._whisper_executor = _BrokenPool()
        fresh = mock.MagicMock()

        def fake_pool(max_workers):
            return fresh

        async def fake_run(executor, fn, *args):
            if isinstance(executor, _BrokenPool):
                raise BrokenProcessPool("worker died")
            return {"text": "ok"}

        async def go():
            loop = asyncio.get_running_loop()
            with mock.patch.object(voice, "ProcessPoolExecutor", fake_pool), \
                 mock.patch.object(loop, "run_in_executor", fake_run):
                return await voice.voice_service.transcribe(b"x", ".webm")

        self.assertEqual(asyncio.run(go()), {"text": "ok"})
        self.assertIs(voice._whisper_executor, fresh)


if __name__ == "__main__":
    unittest.main()
