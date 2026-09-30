"""Synchronous runtime for already-installed public GOST nodes.

This module deliberately performs no SSH, SCP, provisioning, or remote
service restart.  ``install.py`` is the only deployment entry point.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
from urllib.parse import quote, urlsplit

import httpx

from .config import load_config
from .errors import ProxyTransportError, RequestBodyNotReplayable
from .models import AppConfig, ProxyNodeConfig, ProxyResult
from .selector import ProxySelector


class SyncProxyPool:
    def __init__(self, config: AppConfig, clients: dict[str, httpx.Client], egress: dict[str, str | None]):
        self.config = config
        self.clients = clients
        self.egress = egress
        self.selector = ProxySelector([n for n in config.proxies if n.enabled], config.selector)
        self._by_id = {n.id: n for n in config.proxies}
        self._closed = False

    @classmethod
    def connect(cls, source: str | dict | AppConfig) -> "SyncProxyPool":
        config = load_config(source)
        clients: dict[str, httpx.Client] = {}
        egress: dict[str, str | None] = {}
        for node in config.proxies:
            if not node.enabled:
                continue
            proxy = _proxy_url(node)
            clients[node.id] = httpx.Client(proxy=proxy, timeout=config.runtime.timeout_seconds,
                                            verify=config.runtime.verify_tls, follow_redirects=False)
            egress[node.id] = None
            if config.diagnostics.ip_check_url:
                try:
                    response = clients[node.id].get(config.diagnostics.ip_check_url)
                    response.raise_for_status()
                    raw_egress = response.text.strip()
                    try:
                        parsed = json.loads(raw_egress)
                        egress[node.id] = str(parsed.get("ip", raw_egress)) if isinstance(parsed, dict) else raw_egress
                    except json.JSONDecodeError:
                        egress[node.id] = raw_egress
                except Exception:
                    pass
        if not clients:
            raise ProxyTransportError("no installed proxy nodes are configured")
        return cls(config, clients, egress)

    create = connect

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            for client in self.clients.values():
                client.close()

    def health_snapshot(self) -> dict[str, dict[str, object]]:
        result = {}
        for node_id, client in self.clients.items():
            checks: dict[str, object] = {}
            for url in self.config.diagnostics.health_urls:
                try:
                    checks[url] = client.get(url).status_code
                except Exception as exc:
                    checks[url] = str(exc)
            result[node_id] = {"egress_ip": self.egress.get(node_id), "checks": checks}
        return result

    def request(self, method: str, url: str, *, body_factory=None, **kwargs) -> ProxyResult:
        if self._closed:
            raise RuntimeError("proxy pool is closed")
        origin = _origin(url)
        attempted: set[str] = set()
        first = None
        last = None
        limit = self.config.selector.max_attempts_on_403
        for index in range(limit):
            node = self.selector.choose_random(origin, attempted) if index else self.selector.choose(origin, attempted)
            if node is None:
                break
            attempted.add(node.id)
            first = first or node.id
            if body_factory is not None:
                request_kwargs = dict(kwargs)
                for key in ("data", "json", "files"):
                    request_kwargs.pop(key, None)
                request_kwargs["content"] = body_factory()
            else:
                request_kwargs = dict(kwargs)
                content = request_kwargs.get("content")
                if hasattr(content, "read") or hasattr(content, "__aiter__"):
                    raise RequestBodyNotReplayable("provide body_factory for a streamed request")
            try:
                response = self.clients[node.id].request(method.upper(), url, **request_kwargs)
            except (httpx.HTTPError, OSError) as exc:
                if index + 1 >= limit:
                    raise ProxyTransportError(str(exc)) from exc
                continue
            last = (node, response)
            blocked = response.status_code in self.config.selector.block_statuses
            blocked = blocked or any(response.headers.get(k, "").lower() == v.lower()
                                     for k, v in self.config.selector.block_header_matches.items())
            if not blocked or index + 1 >= limit:
                classification = None if not blocked else (
                    "target_policy_or_request_signature" if len(attempted) >= 3
                    else "node_origin_block")
                return ProxyResult(response, node.id, self.egress.get(node.id), index + 1, classification)
        if last:
            node, response = last
            classification = "target_policy_or_request_signature" if len(attempted) >= 3 else "node_origin_block"
            return ProxyResult(response, node.id, self.egress.get(node.id), len(attempted), classification)
        raise ProxyTransportError("all proxy attempts failed")


def _proxy_url(node: ProxyNodeConfig) -> str:
    if node.kind == "direct" and node.direct:
        return node.direct.url()
    if not node.gost or (not node.ssh and node.kind != "reverse_gost_client"):
        raise ValueError(f"{node.id}: managed_gost requires ssh and gost configuration")
    if node.kind == "reverse_gost_client":
        if node.gost.hub_entry_port is None:
            raise ValueError(f"{node.id}: reverse tunnel entry port was not assigned; run install.py first")
        host = node.gost.public_host or "127.0.0.1"
        port = node.gost.hub_entry_port
    else:
        host = node.gost.public_host or node.ssh.host
        port = node.gost.public_port or node.gost.remote_port
    auth = ""
    if node.gost.username:
        auth = quote(node.gost.username, safe="")
        if node.gost.password is not None:
            auth += ":" + quote(node.gost.password, safe="")
        auth += "@"
    return f"socks5://{auth}{host}:{port}"


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"
