import asyncio
import unittest

import httpx
from fastapi import HTTPException

from app.api.routes.data import _ensure_public_url, _stream_public


class EnsurePublicUrlTest(unittest.TestCase):
    def test_internal_addresses_rejected(self):
        for url in (
            "http://127.0.0.1/x.csv", "http://10.0.0.5/x.csv", "http://192.168.1.1/x.csv",
            "http://169.254.169.254/latest", "http://100.64.0.1/x.csv", "http://[::ffff:127.0.0.1]/x",
            "http://0.0.0.0/x", "ftp://93.184.216.34/x.csv",
        ):
            with self.assertRaises(HTTPException, msg=url):
                _ensure_public_url(url)

    def test_public_address_allowed(self):
        _ensure_public_url("http://93.184.216.34/data.csv")


class RedirectTest(unittest.TestCase):
    def _run(self, handler, url):
        async def go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                async with _stream_public(client, url) as response:
                    return response.status_code, str(response.url)
        return asyncio.run(go())

    def test_redirect_to_internal_is_blocked(self):
        def handler(request):
            return httpx.Response(302, headers={"location": "http://127.0.0.1:11434/api/tags"})
        with self.assertRaises(HTTPException):
            self._run(handler, "http://93.184.216.34/data.csv")

    def test_public_redirect_followed(self):
        def handler(request):
            if request.url.path == "/a.csv":
                return httpx.Response(301, headers={"location": "/b.csv"})
            return httpx.Response(200, content=b"x,y\n1,2\n")
        status, final = self._run(handler, "http://93.184.216.34/a.csv")
        self.assertEqual(status, 200)
        self.assertTrue(final.endswith("/b.csv"))

    def test_redirect_loop_stops(self):
        def handler(request):
            return httpx.Response(302, headers={"location": "/again"})
        with self.assertRaises(HTTPException):
            self._run(handler, "http://93.184.216.34/start")


if __name__ == "__main__":
    unittest.main()
