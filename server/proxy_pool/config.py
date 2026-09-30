from __future__ import annotations

import os
import re
import socket
import json
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError
from .models import (
    AppConfig,
    DiagnosticsConfig,
    DirectProxyConfig,
    GostNodeConfig,
    ProxyNodeConfig,
    RuntimeConfig,
    ReverseTunnelConfig,
    GatewayConfig,
    SSHConfig,
    SelectorConfig,
)

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


def _port_available(host: str, port: int) -> bool:
    """Return whether the hub can bind the requested local entry port."""
    family = socket.AF_INET6 if ":" in host and host != "0.0.0.0" else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _entry_state_path(source: str | Path | dict[str, Any] | AppConfig, base: Path) -> Path | None:
    if isinstance(source, (str, Path)):
        return base / "state" / "entry_ports.json"
    return None


def _read_entry_state(path: Path | None) -> dict[str, int]:
    if path is None or not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {str(key): int(value) for key, value in raw.items()}
    except (OSError, ValueError, TypeError):
        return {}


def _write_entry_state(path: Path | None, mapping: dict[str, int]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(mapping, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _expand(value: Any, allow_unset: bool = False) -> Any:
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            if name in os.environ:
                return os.environ[name]
            if default is not None:
                return default
            if allow_unset:
                return match.group(0)
            raise ConfigError(f"environment variable {name!r} is not set")

        return _ENV.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand(v, allow_unset) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v, allow_unset) for v in value]
    return value


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a mapping")
    return value


def _required(mapping: dict[str, Any], key: str, path: str) -> Any:
    if key not in mapping or mapping[key] in (None, ""):
        raise ConfigError(f"missing {path}.{key}")
    return mapping[key]


def load_config(source: str | Path | dict[str, Any] | AppConfig, *, allow_unset_env: bool = False, probe_ports: bool = False) -> AppConfig:
    if isinstance(source, AppConfig):
        return source
    base = Path.cwd()
    if isinstance(source, (str, Path)):
        path = Path(source)
        base = path.resolve().parent
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except OSError as exc:
            raise ConfigError(f"cannot read config {path}: {exc}") from exc
    else:
        raw = source
    raw = _expand(raw, allow_unset_env)
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")

    runtime_raw = _mapping(raw.get("runtime"), "runtime")
    runtime = RuntimeConfig(
        timeout_seconds=float(runtime_raw.get("timeout_seconds", 20)),
        max_concurrency=int(runtime_raw.get("max_concurrency", 50)),
        verify_tls=bool(runtime_raw.get("verify_tls", True)),
    )
    if runtime.timeout_seconds <= 0 or runtime.max_concurrency <= 0:
        raise ConfigError("runtime timeout and max_concurrency must be positive")

    selector_raw = _mapping(raw.get("selector"), "selector")
    mode = selector_raw.get("mode", "fixed_count")
    if mode not in {"fixed_count", "adaptive_403", "random"}:
        raise ConfigError("selector.mode must be fixed_count, adaptive_403, or random")
    selector = SelectorConfig(
        mode=mode,
        rotate_after=int(selector_raw.get("rotate_after", 5)),
        max_attempts_on_403=int(selector_raw.get("max_attempts_on_block", selector_raw.get("max_attempts_on_403", 3))),
        block_statuses=[int(s) for s in selector_raw.get("block_statuses", [403, 418, 429])],
        block_header_matches={str(k): str(v) for k, v in _mapping(selector_raw.get("block_header_matches"), "selector.block_header_matches").items()},
        block_redirect_prefixes=[str(prefix) for prefix in selector_raw.get("block_redirect_prefixes", [])],
        max_attempts_on_transport=int(selector_raw.get("max_attempts_on_transport", 2)),
        cooldown_seconds=float(selector_raw.get("cooldown_seconds", 60)),
    )
    if selector.rotate_after <= 0 or selector.max_attempts_on_403 <= 0:
        raise ConfigError("selector counts must be positive")
    if any(status < 400 or status > 599 for status in selector.block_statuses):
        raise ConfigError("selector.block_statuses must contain HTTP 4xx/5xx codes")
    if any(not name.strip() or not value.strip() for name, value in selector.block_header_matches.items()):
        raise ConfigError("selector.block_header_matches requires non-empty header names and values")
    if any(not prefix.startswith(("http://", "https://")) for prefix in selector.block_redirect_prefixes):
        raise ConfigError("selector.block_redirect_prefixes requires absolute http(s) URL prefixes")

    diagnostics_raw = _mapping(raw.get("diagnostics"), "diagnostics")
    diagnostics = DiagnosticsConfig(
        ip_check_url=diagnostics_raw.get("ip_check_url"),
        health_urls=list(diagnostics_raw.get("health_urls", [])),
        allowed_ports=[int(p) for p in diagnostics_raw.get("allowed_ports", [80, 443])],
    )

    gost_raw = _mapping(raw.get("gost"), "gost")
    reverse_raw = _mapping(raw.get("reverse_tunnel"), "reverse_tunnel")
    gateway_raw = _mapping(raw.get("gateway"), "gateway")
    gateway = GatewayConfig(
        enabled=bool(gateway_raw.get("enabled", False)),
        listen_host=str(gateway_raw.get("listen_host", "127.0.0.1")),
        listen_port=int(gateway_raw.get("listen_port", 8080)),
        username=gateway_raw.get("username"),
        password=gateway_raw.get("password"),
        session_ttl_seconds=int(gateway_raw.get("session_ttl_seconds", 300)),
    )
    if gateway.enabled and (gateway.listen_port <= 0 or gateway.session_ttl_seconds <= 0):
        raise ConfigError("gateway listen_port and session_ttl_seconds must be positive")
    reverse_tunnel = ReverseTunnelConfig(
        enabled=bool(reverse_raw.get("enabled", False)),
        hub_host=str(reverse_raw.get("hub_host", "")),
        relay_host=str(reverse_raw.get("relay_host", "0.0.0.0")),
        relay_port=int(reverse_raw.get("relay_port", 443)),
        relay_username=reverse_raw.get("relay_username"),
        relay_password=reverse_raw.get("relay_password"),
        hub_entry_host=str(reverse_raw.get("entry_host", reverse_raw.get("hub_entry_host", "127.0.0.1"))),
        entry_port_base=int(str(reverse_raw.get("entry_port_base", 11000)).split("-", 1)[0]),
        entry_port_end=int(str(reverse_raw.get("entry_port_base", 11000)).split("-", 1)[-1]),
    )
    if reverse_tunnel.enabled and not reverse_tunnel.hub_host:
        raise ConfigError("reverse_tunnel.hub_host is required when reverse_tunnel.enabled is true")
    if reverse_tunnel.entry_port_base <= 0 or reverse_tunnel.entry_port_end < reverse_tunnel.entry_port_base:
        raise ConfigError("reverse_tunnel.entry_port_base must be a port or ascending port range")
    global_binary = gost_raw.get("binary_path")
    gost_binary = (base / global_binary).resolve() if global_binary else None
    global_binary_dir = gost_raw.get("binary_dir")
    gost_binary_dir = (base / global_binary_dir).resolve() if global_binary_dir else None

    nodes_raw = raw.get("proxies")
    if not isinstance(nodes_raw, list) or not nodes_raw:
        raise ConfigError("proxies must be a non-empty list")
    nodes: list[ProxyNodeConfig] = []
    ids: set[str] = set()
    for index, node_raw in enumerate(nodes_raw):
        path = f"proxies[{index}]"
        node = _mapping(node_raw, path)
        node_id = str(_required(node, "id", path))
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", node_id):
            raise ConfigError(f"{path}.id contains unsupported characters")
        if node_id in ids:
            raise ConfigError(f"duplicate proxy id {node_id!r}")
        ids.add(node_id)
        kind = node.get("kind")
        if kind not in {"direct", "managed_ssh", "managed_gost", "reverse_gost_client"}:
            raise ConfigError(f"{path}.kind must be direct, managed_ssh, managed_gost, or reverse_gost_client")

        if kind == "direct":
            p = _mapping(node.get("proxy"), f"{path}.proxy")
            scheme = str(_required(p, "scheme", f"{path}.proxy")).lower()
            if scheme not in {"http", "https", "socks5"}:
                raise ConfigError(f"unsupported proxy scheme {scheme!r}")
            direct = DirectProxyConfig(
                scheme=scheme,
                host=str(_required(p, "host", f"{path}.proxy")),
                port=int(_required(p, "port", f"{path}.proxy")),
                username=p.get("username"),
                password=p.get("password"),
            )
            nodes.append(ProxyNodeConfig(node_id, kind, direct=direct, enabled=bool(node.get("enabled", True))))
            continue

        ssh_raw = _mapping(node.get("ssh"), f"{path}.ssh")
        ssh = SSHConfig(
            host=str(_required(ssh_raw, "host", f"{path}.ssh")),
            port=int(ssh_raw.get("port", 22)),
            username=str(_required(ssh_raw, "username", f"{path}.ssh")),
            password=ssh_raw.get("password"),
            private_key=(base / ssh_raw["private_key"]).resolve() if ssh_raw.get("private_key") else None,
            passphrase=ssh_raw.get("passphrase"),
            known_hosts=(base / ssh_raw["known_hosts"]).resolve() if ssh_raw.get("known_hosts") else None,
        )
        node_gost_raw = _mapping(node.get("gost"), f"{path}.gost")
        gost = GostNodeConfig(
            binary_path=(base / node_gost_raw["binary_path"]).resolve() if node_gost_raw.get("binary_path") else gost_binary,
            binary_dir=(base / node_gost_raw["binary_dir"]).resolve() if node_gost_raw.get("binary_dir") else gost_binary_dir,
            version=str(node_gost_raw.get("version", gost_raw.get("version", "3.x"))),
            sha256=node_gost_raw.get("sha256", gost_raw.get("sha256")),
            remote_bind_host=str(node_gost_raw.get("remote_bind_host", "127.0.0.1")),
            remote_port=int(node_gost_raw.get("remote_port", 1080)),
            username=node_gost_raw.get("username"),
            password=node_gost_raw.get("password"),
            local_host=str(node_gost_raw.get("local_host", "127.0.0.1")),
            local_port=int(node_gost_raw.get("local_port", 0)),
            install_dir=str(node_gost_raw.get("install_dir", "/usr/local/lib/proxy-pool")),
            public_host=node_gost_raw.get("public_host"),
            public_port=(int(node_gost_raw["public_port"]) if node_gost_raw.get("public_port") is not None else None),
            tunnel_id=node_gost_raw.get("tunnel_id", node_id),
            hub_entry_port=(int(node_gost_raw["hub_entry_port"]) if node_gost_raw.get("hub_entry_port") is not None else None),
        )
        if not gost.binary_path and not gost.binary_dir:
            raise ConfigError(f"{path}.gost.binary_path or binary_dir is required")
        nodes.append(ProxyNodeConfig(node_id, kind, ssh=ssh, gost=gost, enabled=bool(node.get("enabled", True))))

    if reverse_tunnel.enabled:
        reverse_nodes = [n for n in nodes if n.enabled and n.kind == "reverse_gost_client"]
        available = list(range(reverse_tunnel.entry_port_base, reverse_tunnel.entry_port_end + 1))
        if len(reverse_nodes) > len(available):
            raise ConfigError(
                f"reverse_tunnel entry port range has {len(available)} ports but "
                f"{len(reverse_nodes)} reverse nodes are enabled"
            )
        used: set[int] = set()
        next_port = reverse_tunnel.entry_port_base
        state_path = _entry_state_path(source, base)
        saved_ports = _read_entry_state(state_path)
        if saved_ports:
            next_port = max(next_port, max(saved_ports.values()) + 1)
        for node in reverse_nodes:
            assert node.gost is not None
            if node.gost.hub_entry_port is not None:
                port = node.gost.hub_entry_port
                next_port = max(next_port, port + 1)
            elif node.id in saved_ports and saved_ports[node.id] in available and saved_ports[node.id] not in used:
                port = saved_ports[node.id]
                next_port = max(next_port, port + 1)
            elif probe_ports:
                attempts: list[int] = []
                port = None
                candidate = next_port
                for _ in range(5):
                    if candidate > reverse_tunnel.entry_port_end:
                        break
                    attempts.append(candidate)
                    if candidate not in used and _port_available(reverse_tunnel.hub_entry_host, candidate):
                        port = candidate
                        next_port = candidate + 1
                        break
                    candidate += 1
                if port is None:
                    attempted = ", ".join(str(value) for value in attempts) or str(next_port)
                    raise ConfigError(
                        f"no available reverse entry port for {node.id}; "
                        f"tried up to 5 ports: {attempted}"
                    )
            else:
                port = available[next_port - reverse_tunnel.entry_port_base] if next_port <= reverse_tunnel.entry_port_end else None
                if port is None:
                    raise ConfigError(f"reverse entry port range exhausted before {node.id}")
                next_port += 1
            if port in used or port not in available:
                raise ConfigError(f"duplicate or out-of-range reverse entry port {port} for {node.id}")
            node.gost.hub_entry_port = port
            node.gost.public_host = reverse_tunnel.hub_entry_host
            used.add(port)
        _write_entry_state(state_path, {node.id: node.gost.hub_entry_port for node in reverse_nodes if node.gost and node.gost.hub_entry_port is not None})
    return AppConfig(runtime, selector, diagnostics, nodes, gost_binary, gost_binary_dir, str(gost_raw.get("version", "3.x")), gost_raw.get("sha256"), reverse_tunnel, gateway)
