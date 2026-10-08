"""EricMingle: small ASGI boundary for personal MCP HTTP services.

Loopback is the default trust boundary. Set MCP_AUTH_TOKEN to also authenticate
local clients; non-loopback deployment requires it and explicit allowed hosts.
This is not an OAuth implementation or a public deployment configuration.
"""
import hmac
import ipaddress
import os


def protect_http(app, *, host, port, environ=None):
    env = os.environ if environ is None else environ
    token = env.get("MCP_AUTH_TOKEN", "")
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback and not token:
        raise ValueError("Non-loopback MCP binding requires MCP_AUTH_TOKEN")
    if token and len(token) < 24:
        raise ValueError("MCP_AUTH_TOKEN must have at least 24 characters")
    defaults = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
    allowed_hosts = defaults | {s.strip().lower() for s in env.get("MCP_ALLOWED_HOSTS", "").split(",") if s.strip()}
    allowed_origins = {f"http://{h}" for h in defaults} | {s.strip() for s in env.get("MCP_ALLOWED_ORIGINS", "").split(",") if s.strip()}
    max_body = 128 * 1024

    async def secured(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)

        async def reject(code):
            await send({"type": "http.response.start", "status": code, "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": b'{"error":"request_rejected"}'})

        pairs = scope.get("headers", [])
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in pairs}
        for name in (b"host", b"origin", b"authorization", b"content-length"):
            if sum(k.lower() == name for k, _ in pairs) > 1:
                return await reject(400)
        if headers.get("host", "").lower() not in allowed_hosts:
            return await reject(403)
        if headers.get("sec-fetch-site", "").lower() in {"cross-site", "same-site"}:
            return await reject(403)
        if "origin" in headers and headers["origin"] not in allowed_origins:
            return await reject(403)
        if token and not hmac.compare_digest(headers.get("authorization", "").encode(), ("Bearer " + token).encode()):
            return await reject(401)
        try:
            length = int(headers.get("content-length", "0"))
        except ValueError:
            return await reject(400)
        if length < 0:
            return await reject(400)
        if length > max_body:
            return await reject(413)
        if scope.get("method") == "POST":
            if headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
                return await reject(415)
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > max_body:
                    return await reject(413)
                if not message.get("more_body", False):
                    break
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            return await app(scope, replay, send)
        return await app(scope, receive, send)

    return secured
