from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

import httpx

from .config import load_client_config
from .errors import ClientInstallError, ClientRequestError


@dataclass
class ClientResult:
    response: httpx.Response
    proxy_id: str
    attempts: int


class ProxyClient:
    def __init__(self, config: dict):
        self.config = config
        self.gateway = config["gateway"]
        self.selector = config["selector"]
        self.session_id: str | None = None
        self.proxy_id: str | None = None
        self.uses = 0
        self._client: httpx.Client | None = None

    @classmethod
    def from_config(cls, source):
        client = cls(load_client_config(source))
        client.health()
        return client

    def _auth_headers(self) -> dict[str, str]:
        user = self.gateway.get("username")
        password = self.gateway.get("password") or ""
        if user is None:
            return {}
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def _create_session(self) -> None:
        response = httpx.post(
            self.gateway["url"] + "/v1/sessions",
            headers=self._auth_headers(),
            json={"mode": self.selector["mode"]},
            timeout=self.gateway["timeout_seconds"],
            verify=self.gateway["verify_tls"],
        )
        if response.status_code >= 400:
            raise ClientInstallError(f"session creation failed: HTTP {response.status_code} {response.text[:200]}")
        payload = response.json()
        self.session_id = payload["session_id"]
        self.proxy_id = payload["proxy_id"]
        self.uses = 0
        self._rebuild_client()

    def _rebuild_client(self) -> None:
        if self._client:
            self._client.close()
        proxy_auth = self._auth_headers().get("Authorization")
        proxy_headers = {"X-Proxy-Session": self.session_id or ""}
        if proxy_auth:
            proxy_headers["Proxy-Authorization"] = proxy_auth
        self._client = httpx.Client(
            proxy=self.gateway["url"],
            proxy_headers=proxy_headers,
            timeout=self.gateway["timeout_seconds"],
            verify=self.gateway["verify_tls"],
            follow_redirects=False,
        )

    def _rotate(self) -> None:
        if not self.session_id:
            self._create_session(); return
        response = httpx.post(
            self.gateway["url"] + f"/v1/sessions/{self.session_id}/rotate",
            headers=self._auth_headers(),
            json={"reason": "client_rotation", "exclude_proxy_id": self.proxy_id},
            timeout=self.gateway["timeout_seconds"],
            verify=self.gateway["verify_tls"],
        )
        if response.status_code >= 400:
            raise ClientRequestError(f"session rotation failed: HTTP {response.status_code}")
        payload = response.json()
        self.session_id = payload["session_id"]
        self.proxy_id = payload["proxy_id"]
        self.uses = 0
        self._rebuild_client()

    def health(self) -> dict:
        response = httpx.get(self.gateway["url"] + "/v1/health", headers=self._auth_headers(), timeout=self.gateway["timeout_seconds"], verify=self.gateway["verify_tls"])
        if response.status_code >= 400:
            raise ClientInstallError(f"gateway health failed: HTTP {response.status_code}")
        self._create_session()
        return response.json()

    def request(self, method: str, url: str, *, body_factory=None, **kwargs: Any) -> ClientResult:
        if self._client is None:
            self._create_session()
        attempts = 0
        max_attempts = self.selector["max_attempts_on_403"]
        last = None
        for index in range(max_attempts):
            if self.selector["mode"] == "random" and self.uses > 0:
                self._rotate()
            if self.selector["mode"] == "fixed_count" and self.uses >= self.selector["rotate_after"]:
                self._rotate()
            request_kwargs = dict(kwargs)
            if body_factory is not None:
                request_kwargs.pop("data", None); request_kwargs.pop("json", None); request_kwargs.pop("files", None)
                request_kwargs["content"] = body_factory()
            elif hasattr(request_kwargs.get("content"), "read"):
                raise ClientRequestError("streaming bodies require body_factory")
            try:
                assert self._client is not None
                response = self._client.request(method.upper(), url, **request_kwargs)
            except httpx.HTTPError as exc:
                if index + 1 >= max_attempts: raise ClientRequestError(str(exc)) from exc
                self._rotate(); continue
            attempts += 1; self.uses += 1; last = response
            if response.status_code not in self.selector["block_statuses"] or index + 1 >= max_attempts:
                return ClientResult(response, self.proxy_id or "", attempts)
            self._rotate()
        if last is None:
            raise ClientRequestError("proxy request failed")
        return ClientResult(last, self.proxy_id or "", attempts)

    def close(self) -> None:
        if self._client:
            self._client.close(); self._client = None
