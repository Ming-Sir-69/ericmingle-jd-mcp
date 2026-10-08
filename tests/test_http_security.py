import asyncio
import importlib.util
from pathlib import Path

import httpx
import unittest


def security():
    path = Path(__file__).parents[1] / "src" / "http_security.py"
    assert path.exists(), "HTTP boundary protection is not implemented"
    spec = importlib.util.spec_from_file_location("http_security_test_target", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.protect_http


async def ok_app(scope, receive, send):
    if scope["type"] != "http":
        return
    while True:
        msg = await receive()
        if not msg.get("more_body"):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def request(headers=None, url="http://127.0.0.1:8840/mcp", env=None):
    async def run():
        app = security()(ok_app, host="127.0.0.1", port=8840, environ=env or {})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
            return await client.post(url, headers=headers or {}, json={"jsonrpc": "2.0"})
    return asyncio.run(run())


class HttpSecurityTests(unittest.TestCase):
    def test_native_mcp_client_is_allowed(self):
        self.assertEqual(request().status_code, 200)

    def test_untrusted_web_requests_never_reach_mcp(self):
        for headers, url in [
            ({"Origin": "https://attacker.example"}, "http://127.0.0.1:8840/mcp"),
            ({"Sec-Fetch-Site": "cross-site"}, "http://127.0.0.1:8840/mcp"),
            ({}, "http://attacker.example/mcp"),
            ({"Origin": "null"}, "http://127.0.0.1:8840/mcp"),
            ({"Origin": "http://127.0.0.1:9999"}, "http://127.0.0.1:8840/mcp"),
        ]:
            with self.subTest(headers=headers, url=url):
                self.assertEqual(request(headers, url).status_code, 403)

    def test_bearer_is_enforced_when_configured(self):
        env = {"MCP_AUTH_TOKEN": "test-token-only-never-a-real-secret"}
        self.assertEqual(request(env=env).status_code, 401)
        self.assertEqual(request({"Authorization": "Bearer wrong"}, env=env).status_code, 401)
        self.assertEqual(request({"Authorization": "Bearer test-token-only-never-a-real-secret"}, env=env).status_code, 200)

    def test_non_loopback_binding_requires_authentication(self):
        with self.assertRaisesRegex(ValueError, "MCP_AUTH_TOKEN"):
            security()(ok_app, host="0.0.0.0", port=8840, environ={})

    def test_oversized_request_is_rejected(self):
        self.assertEqual(request({"Content-Length": "99999999"}).status_code, 413)
