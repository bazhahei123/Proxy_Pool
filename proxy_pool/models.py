from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


ProxyKind = Literal["direct", "managed_ssh", "managed_gost", "reverse_gost_client"]
SelectorMode = Literal["fixed_count", "adaptive_403"]


@dataclass(slots=True)
class SSHConfig:
    host: str
    username: str
    port: int = 22
    password: str | None = None
    private_key: Path | None = None
    passphrase: str | None = None
    known_hosts: Path | None = None


@dataclass(slots=True)
class DirectProxyConfig:
    scheme: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None

    def url(self) -> str:
        from urllib.parse import quote

        auth = ""
        if self.username is not None and self.username != "":
            auth = quote(self.username, safe="")
            if self.password is not None:
                auth += ":" + quote(self.password, safe="")
            auth += "@"
        return f"{self.scheme}://{auth}{self.host}:{self.port}"


@dataclass(slots=True)
class GostNodeConfig:
    binary_path: Path | None = None
    binary_dir: Path | None = None
    version: str = "3.x"
    sha256: str | None = None
    remote_bind_host: str = "127.0.0.1"
    remote_port: int = 1080
    username: str | None = None
    password: str | None = None
    local_host: str = "127.0.0.1"
    local_port: int = 0
    install_dir: str = "/usr/local/lib/proxy-pool"
    # Public listener used after installation.  It is deliberately separate
    # from the old SSH-local-forward fields kept for backwards compatibility.
    public_host: str | None = None
    public_port: int | None = None
    tunnel_id: str | None = None
    hub_entry_port: int | None = None


@dataclass(slots=True)
class ReverseTunnelConfig:
    enabled: bool = False
    hub_host: str = ""
    relay_host: str = "0.0.0.0"
    relay_port: int = 443
    relay_username: str | None = None
    relay_password: str | None = None
    hub_entry_host: str = "127.0.0.1"
    entry_port_base: int = 11000
    entry_port_end: int = 11000


@dataclass(slots=True)
class ProxyNodeConfig:
    id: str
    kind: ProxyKind
    direct: DirectProxyConfig | None = None
    ssh: SSHConfig | None = None
    gost: GostNodeConfig | None = None
    enabled: bool = True


@dataclass(slots=True)
class SelectorConfig:
    mode: SelectorMode = "fixed_count"
    rotate_after: int = 5
    max_attempts_on_403: int = 3
    block_statuses: list[int] = field(default_factory=lambda: [403, 418, 429])
    block_header_matches: dict[str, str] = field(default_factory=dict)
    block_redirect_prefixes: list[str] = field(default_factory=list)
    max_attempts_on_transport: int = 2
    cooldown_seconds: float = 60.0


@dataclass(slots=True)
class RuntimeConfig:
    timeout_seconds: float = 20.0
    max_concurrency: int = 50
    verify_tls: bool = True


@dataclass(slots=True)
class DiagnosticsConfig:
    ip_check_url: str | None = None
    health_urls: list[str] = field(default_factory=list)
    allowed_ports: list[int] = field(default_factory=lambda: [80, 443])


@dataclass(slots=True)
class AppConfig:
    runtime: RuntimeConfig
    selector: SelectorConfig
    diagnostics: DiagnosticsConfig
    proxies: list[ProxyNodeConfig]
    gost_binary_path: Path | None = None
    gost_binary_dir: Path | None = None
    gost_version: str = "3.x"
    gost_sha256: str | None = None
    reverse_tunnel: ReverseTunnelConfig = field(default_factory=ReverseTunnelConfig)


@dataclass(slots=True)
class ProxyResult:
    response: Any
    proxy_id: str
    egress_ip: str | None
    attempts: int
    classification: str | None = None
