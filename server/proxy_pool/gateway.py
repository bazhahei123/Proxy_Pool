"""HTTP forward proxy and session control gateway for the hub VPS."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
import time
import random
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .config import load_config
from .models import AppConfig, ProxyNodeConfig
from .selector import ProxySelector

LOGGER = logging.getLogger("proxy_pool.gateway")


@dataclass
class Session:
    session_id: str
    node: ProxyNodeConfig
    expires_at: float
    mode: str = "rules"


class Gateway:
    def __init__(self, config: AppConfig):
        self.config = config
        self.selector = ProxySelector([n for n in config.proxies if n.enabled], config.selector)
        self.sessions: dict[str, Session] = {}

    def _auth_ok(self, headers: dict[str, str], api: bool = False) -> bool:
        key = "authorization" if api else "proxy-authorization"
        value = headers.get(key, "")
        configured_password = self.config.gateway.password
        if isinstance(configured_password, str) and configured_password.startswith("${"):
            import os
            configured_password = os.environ.get(configured_password[2:-1], "")
        if not self.config.gateway.username:
            return True
        if not value.lower().startswith("basic "):
            return False
        try:
            decoded = base64.b64decode(value[6:]).decode()
            user, password = decoded.split(":", 1)
        except Exception:
            return False
        if not api and "|" in password:
            password = password.split("|", 1)[0]
        return secrets.compare_digest(user, self.config.gateway.username) and secrets.compare_digest(password, configured_password or "")

    def _session_from_proxy_auth(self, headers: dict[str, str]) -> str | None:
        value = headers.get("proxy-authorization", "")
        if not value.lower().startswith("basic "):
            return None
        try:
            decoded = base64.b64decode(value[6:]).decode()
            _, password = decoded.split(":", 1)
            _, session_id = password.split("|", 1)
            return session_id or None
        except Exception:
            return None

    def _session(self, session_id: str | None) -> Session | None:
        if not session_id:
            return None
        session = self.sessions.get(session_id)
        if session and session.expires_at > time.time():
            return session
        if session:
            self.sessions.pop(session_id, None)
        return None

    def _new_session(self, exclude: set[str] | None = None, mode: str = "rules") -> Session:
        exclude = exclude or set()
        if mode == "random":
            eligible = [n for n in self.selector.nodes if n.id not in exclude]
            node = random.choice(eligible) if eligible else None
        else:
            node = self.selector.choose("gateway://session", exclude)
        if node is None:
            raise RuntimeError("no eligible proxy node")
        sid = secrets.token_urlsafe(24)
        session = Session(sid, node, time.time() + self.config.gateway.session_ttl_seconds, mode)
        self.sessions[sid] = session
        return session

    def _rotate(self, session: Session) -> Session:
        return self._new_session({session.node.id}, session.mode)

    def _proxy_url(self, node: ProxyNodeConfig) -> str:
        assert node.gost and node.gost.hub_entry_port
        return f"socks5://127.0.0.1:{node.gost.hub_entry_port}"

    async def _json(self, writer: asyncio.StreamWriter, status: int, value: dict) -> None:
        body = json.dumps(value, separators=(",", ":")).encode()
        reason = {200: "OK", 201: "Created", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 409: "Conflict", 500: "Internal Server Error"}.get(status, "Error")
        writer.write(f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
        await writer.drain()

    async def _handle_api(self, method: str, target: str, headers: dict[str, str], body: bytes, writer: asyncio.StreamWriter) -> bool:
        if not self._auth_ok(headers, api=True):
            await self._json(writer, 401, {"error": "authentication required"})
            return True
        if method == "POST" and target == "/v1/sessions":
            try:
                payload = json.loads(body or b"{}")
                mode = str(payload.get("mode", "rules"))
                if mode not in {"rules", "fixed_count", "random"}:
                    await self._json(writer, 400, {"error": "unsupported mode"})
                    return True
                session = self._new_session(mode=mode)
            except RuntimeError as exc:
                await self._json(writer, 409, {"error": str(exc)})
                return True
            await self._json(writer, 201, {"session_id": session.session_id, "proxy_id": session.node.id, "expires_at": session.expires_at})
            return True
        parts = target.split("/")
        if len(parts) == 5 and parts[:3] == ["", "v1", "sessions"] and parts[4] == "rotate" and method == "POST":
            session = self._session(parts[3])
            if not session:
                await self._json(writer, 404, {"error": "session not found or expired"})
                return True
            try:
                new_session = self._rotate(session)
            except RuntimeError as exc:
                await self._json(writer, 409, {"error": str(exc)})
                return True
            session.expires_at = 0
            await self._json(writer, 200, {"session_id": new_session.session_id, "proxy_id": new_session.node.id, "expires_at": new_session.expires_at})
            return True
        if method == "GET" and target == "/v1/health":
            await self._json(writer, 200, {"sessions": len(self.sessions), "nodes": [n.id for n in self.selector.nodes]})
            return True
        await self._json(writer, 404, {"error": "not found"})
        return True

    async def _socks_connect(self, node: ProxyNodeConfig, host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        assert node.gost and node.gost.hub_entry_port
        reader, writer = await asyncio.open_connection("127.0.0.1", node.gost.hub_entry_port)
        writer.write(b"\x05\x01\x00"); await writer.drain()
        if await reader.readexactly(2) != b"\x05\x00":
            writer.close(); raise RuntimeError("upstream SOCKS5 authentication failed")
        encoded = host.encode()
        writer.write(b"\x05\x01\x00\x03" + bytes([len(encoded)]) + encoded + port.to_bytes(2, "big")); await writer.drain()
        head = await reader.readexactly(4)
        if head[1] != 0:
            writer.close(); raise RuntimeError(f"upstream SOCKS5 connect failed: {head[1]}")
        length = (await reader.readexactly(1))[0] if head[3] == 3 else (4 if head[3] == 1 else 16)
        await reader.readexactly(length + 2)
        return reader, writer

    async def _pipe(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, target_reader: asyncio.StreamReader, target_writer: asyncio.StreamWriter) -> None:
        async def copy(src, dst):
            try:
                while data := await src.read(65536):
                    dst.write(data); await dst.drain()
            except (ConnectionError, asyncio.IncompleteReadError):
                pass
            finally:
                try: dst.close()
                except Exception: pass
        await asyncio.gather(copy(reader, target_writer), copy(target_reader, writer))

    async def _proxy(self, reader: asyncio.StreamReader, method: str, target: str, headers: dict[str, str], body: bytes, session: Session, writer: asyncio.StreamWriter) -> None:
        if method == "CONNECT":
            host, _, port_text = target.rpartition(":")
            port = int(port_text or 443)
            try:
                upstream_reader, upstream_writer = await self._socks_connect(session.node, host, port)
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n"); await writer.drain()
                await self._pipe(reader=reader, writer=writer, target_reader=upstream_reader, target_writer=upstream_writer)
            except Exception as exc:
                LOGGER.error("CONNECT upstream failed: %s: %s", type(exc).__name__, exc)
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n"); await writer.drain()
            return
        url = target if target.startswith(("http://", "https://")) else "http://" + target
        outgoing = {k: v for k, v in headers.items() if k.lower() not in {"proxy-authorization", "proxy-connection", "x-proxy-session", "connection"}}
        try:
            async with httpx.AsyncClient(proxy=self._proxy_url(session.node), timeout=self.config.runtime.timeout_seconds, verify=self.config.runtime.verify_tls, follow_redirects=False) as client:
                response = await client.request(method, url, headers=outgoing, content=body)
            hop = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"}
            response_headers = [(k, v) for k, v in response.headers.items() if k.lower() not in hop]
            payload = response.content
            writer.write(f"HTTP/1.1 {response.status_code} {response.reason_phrase}\r\n".encode())
            for key, value in response_headers: writer.write(f"{key}: {value}\r\n".encode())
            writer.write(f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode() + payload); await writer.drain()
        except Exception as exc:
            LOGGER.error("upstream proxy failed: %s: %s", type(exc).__name__, exc)
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n"); await writer.drain()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await reader.readuntil(b"\r\n\r\n")
            lines = raw.decode("latin1").split("\r\n")
            method, target, _ = lines[0].split(" ", 2)
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    key, value = line.split(":", 1); headers[key.lower()] = value.strip()
            length = int(headers.get("content-length", "0"))
            body = await reader.readexactly(length) if length else b""
            if target.startswith("/v1/"):
                await self._handle_api(method, target, headers, body, writer)
            elif not self._auth_ok(headers):
                writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic\r\nContent-Length: 0\r\n\r\n"); await writer.drain()
            else:
                session = self._session(headers.get("x-proxy-session") or self._session_from_proxy_auth(headers))
                if not session:
                    writer.write(b"HTTP/1.1 409 Session Required\r\nContent-Length: 0\r\n\r\n"); await writer.drain()
                else:
                    await self._proxy(reader, method, target, headers, body, session, writer)
        except Exception:
            pass
        finally:
            writer.close()


async def serve(source: str) -> None:
    log_dir = Path(source).resolve().parent / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / "gateway.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOGGER.setLevel(logging.INFO)
    LOGGER.addHandler(handler)
    LOGGER.info("Gateway starting; config=%s", Path(source).resolve())
    config = load_config(source, allow_unset_env=True, probe_ports=False)
    if not config.gateway.enabled:
        raise RuntimeError("gateway.enabled is false")
    gateway = Gateway(config)
    LOGGER.info("Gateway listening on %s:%s", config.gateway.listen_host, config.gateway.listen_port)
    server = await asyncio.start_server(gateway.handle, config.gateway.listen_host, config.gateway.listen_port)
    async with server:
        await server.serve_forever()
