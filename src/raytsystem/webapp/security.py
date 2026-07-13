from __future__ import annotations

import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SESSION_COOKIE = "raytsystem_session"
CSRF_HEADER = "x-csrf-token"
IDEMPOTENCY_HEADER = "idempotency-key"


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    csrf_token: str
    issued_at: float
    expires_at: float


class SessionStore:
    def __init__(self, *, ttl_seconds: int = 8 * 60 * 60, max_sessions: int = 128) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self._sessions: dict[str, SessionRecord] = {}
        self._lock = threading.Lock()

    def issue(self) -> SessionRecord:
        now = time.time()
        record = SessionRecord(
            session_id=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(32),
            issued_at=now,
            expires_at=now + self.ttl_seconds,
        )
        with self._lock:
            self._cleanup(now)
            if len(self._sessions) >= self.max_sessions:
                oldest = min(self._sessions.values(), key=lambda item: item.issued_at)
                self._sessions.pop(oldest.session_id, None)
            self._sessions[record.session_id] = record
        return record

    def get(self, token: str | None) -> SessionRecord | None:
        if token is None:
            return None
        now = time.time()
        with self._lock:
            self._cleanup(now)
            return self._sessions.get(token)

    def csrf_matches(self, record: SessionRecord, candidate: str | None) -> bool:
        return candidate is not None and hmac.compare_digest(record.csrf_token, candidate)

    def _cleanup(self, now: float) -> None:
        expired = [token for token, item in self._sessions.items() if item.expires_at <= now]
        for token in expired:
            self._sessions.pop(token, None)


class SecurityMiddleware:
    """Enforce loopback-browser trust boundaries before requests reach FastAPI."""

    unsafe_methods = frozenset({"POST", "PUT", "PATCH", "DELETE"})
    max_body_bytes = 64 * 1024
    # A 5 MiB Markdown source can grow materially when JSON escapes line/control characters.
    # The service rechecks decoded UTF-8 bytes against the tighter per-document limit.
    document_max_body_bytes = 12 * 1024 * 1024

    def __init__(
        self,
        app: ASGIApp,
        *,
        sessions: SessionStore,
        allowed_hosts: frozenset[str],
        allowed_origins: frozenset[str],
    ) -> None:
        self.app = app
        self.sessions = sessions
        self.allowed_hosts = frozenset(host.lower() for host in allowed_hosts)
        self.allowed_origins = allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = self._headers(scope)
        host = headers.get("host", "").lower()
        if host not in self.allowed_hosts:
            await self._reject(send, 421, "host_rejected", "Open the exact loopback URL.", True)
            return
        path = str(scope.get("path", ""))
        method = str(scope.get("method", "GET")).upper()
        is_api = path.startswith("/api/")
        csp_nonce = secrets.token_urlsafe(24)
        scope.setdefault("state", {})["csp_nonce"] = csp_nonce
        session = self.sessions.get(self._cookie(headers.get("cookie")))
        if is_api and session is None:
            await self._reject(send, 401, "session_required", "Reopen the local interface.", True)
            return

        replay_receive = receive
        if method in self.unsafe_methods:
            if not is_api:
                await self._reject(send, 405, "method_rejected", "Method is not available.", False)
                return
            origin = headers.get("origin")
            if origin not in self.allowed_origins:
                await self._reject(
                    send,
                    403,
                    "origin_rejected",
                    "Reopen the exact loopback URL.",
                    True,
                )
                return
            if session is None or not self.sessions.csrf_matches(
                session,
                headers.get(CSRF_HEADER),
            ):
                await self._reject(send, 403, "csrf_rejected", "Refresh the local session.", True)
                return
            idempotency_key = headers.get(IDEMPOTENCY_HEADER, "")
            if (
                len(idempotency_key.encode("utf-8")) < 8
                or len(idempotency_key.encode("utf-8")) > 512
                or any(ord(character) < 32 for character in idempotency_key)
            ):
                await self._reject(
                    send,
                    400,
                    "idempotency_required",
                    "A valid idempotency key is required.",
                    True,
                )
                return
            content_type = headers.get("content-type", "").split(";", maxsplit=1)[0].strip()
            if content_type.lower() != "application/json":
                await self._reject(
                    send,
                    415,
                    "json_required",
                    "Only JSON requests are accepted.",
                    True,
                )
                return
            body_limit = self._body_limit(path)
            content_length = headers.get("content-length")
            if content_length is not None:
                try:
                    declared_length = int(content_length)
                except ValueError:
                    await self._reject(
                        send,
                        400,
                        "body_length_invalid",
                        "Request length is invalid.",
                        True,
                    )
                    return
                if declared_length < 0 or declared_length > body_limit:
                    await self._reject(
                        send,
                        413,
                        "body_too_large",
                        "Request body is too large.",
                        True,
                    )
                    return
            body = await self._bounded_body(receive, limit=body_limit)
            if body is None:
                await self._reject(
                    send,
                    413,
                    "body_too_large",
                    "Request body is too large.",
                    True,
                )
                return
            replay_receive = self._replay(body)

        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                self._apply_headers(message, is_api=is_api, csp_nonce=csp_nonce)
            await send(message)

        await self.app(scope, replay_receive, secure_send)

    def _body_limit(self, path: str) -> int:
        if path == "/api/v1/documents" or path.startswith("/api/v1/documents/"):
            return self.document_max_body_bytes
        return self.max_body_bytes

    async def _bounded_body(self, receive: Receive, *, limit: int) -> bytes | None:
        chunks: list[bytes] = []
        consumed = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return b""
            if message["type"] != "http.request":
                continue
            chunk = bytes(message.get("body", b""))
            consumed += len(chunk)
            if consumed > limit:
                return None
            chunks.append(chunk)
            if not message.get("more_body", False):
                return b"".join(chunks)

    @staticmethod
    def _replay(body: bytes) -> Receive:
        delivered = False

        async def receive() -> Message:
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        return receive

    async def _reject(
        self,
        send: Send,
        status: int,
        code: str,
        message: str,
        is_api: bool,
    ) -> None:
        body = json.dumps(
            {"error": {"code": code, "message": message}},
            separators=(",", ":"),
        ).encode("utf-8")
        response: Message = {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
        self._apply_headers(response, is_api=is_api, csp_nonce=secrets.token_urlsafe(24))
        await send(response)
        await send({"type": "http.response.body", "body": body})

    def _apply_headers(self, message: Message, *, is_api: bool, csp_nonce: str) -> None:
        headers = MutableHeaders(raw=message.setdefault("headers", []))
        headers["Content-Security-Policy"] = self._csp(csp_nonce)
        headers["X-Content-Type-Options"] = "nosniff"
        headers["X-Frame-Options"] = "DENY"
        headers["Referrer-Policy"] = "no-referrer"
        headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=()"
        )
        headers["Cross-Origin-Opener-Policy"] = "same-origin"
        headers["Cross-Origin-Resource-Policy"] = "same-origin"
        if is_api:
            headers["Cache-Control"] = "no-store"

    @staticmethod
    def _csp(nonce: str) -> str:
        return (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self'; "
            f"style-src-elem 'self' 'nonce-{nonce}'; "
            "style-src-attr 'unsafe-inline'; "
            "font-src 'self'; "
            "img-src 'self'; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "base-uri 'none'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "frame-src 'none'; "
            # graphology-layout-forceatlas2@0.10.1 creates its first-party worker from a
            # bundle-owned Blob URL. Keep every other execution source self-only.
            "worker-src 'self' blob:"
        )

    @staticmethod
    def _headers(scope: Scope) -> dict[str, str]:
        return {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }

    @staticmethod
    def _cookie(raw_cookie: str | None) -> str | None:
        if raw_cookie is None:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(raw_cookie)
        except Exception:
            return None
        morsel = cookie.get(SESSION_COOKIE)
        return None if morsel is None else morsel.value
