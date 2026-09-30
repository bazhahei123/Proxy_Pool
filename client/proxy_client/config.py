from __future__ import annotations

import os
import re
from pathlib import Path
import yaml

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


def default_config_path() -> Path:
    return Path.home() / ".proxy-pool" / "client_config.yaml"


def resolve_config_path(source: str | Path | None) -> Path:
    if source:
        return Path(source)
    installed = default_config_path()
    if installed.is_file():
        return installed
    return Path("client_config.yaml")


def _expand(value):
    if isinstance(value, str):
        def replace(match):
            name, default = match.group(1), match.group(2)
            if name in os.environ:
                return os.environ[name]
            if default is not None:
                return default
            raise ValueError(f"environment variable {name} is not set")
        return _ENV.sub(replace, value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def load_client_config(source: str | Path | dict) -> dict:
    if isinstance(source, (str, Path)):
        raw = yaml.safe_load(Path(source).read_text(encoding="utf-8")) or {}
    else:
        raw = source
    raw = _expand(raw)
    gateway = raw.get("gateway", {})
    selector = raw.get("selector", {})
    if not gateway.get("url"):
        raise ValueError("gateway.url is required")
    mode = selector.get("mode", "rules")
    if mode not in {"rules", "fixed_count", "random"}:
        raise ValueError("selector.mode must be rules, fixed_count, or random")
    return {
        "gateway": {
            "url": str(gateway["url"]).rstrip("/"),
            "username": gateway.get("username"),
            "password": gateway.get("password"),
            "verify_tls": bool(gateway.get("verify_tls", True)),
            "timeout_seconds": float(gateway.get("timeout_seconds", 30)),
        },
        "selector": {
            "mode": mode,
            "rotate_after": int(selector.get("rotate_after", 5)),
            "max_attempts_on_403": int(selector.get("max_attempts_on_403", 3)),
            "block_statuses": [int(x) for x in selector.get("block_statuses", [403, 418, 429])],
        },
    }
