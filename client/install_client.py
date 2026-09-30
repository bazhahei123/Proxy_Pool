from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from proxy_client import ProxyClient
from proxy_client.config import default_config_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and install the Proxy Pool engine client")
    parser.add_argument("config", nargs="?", default="client_config.yaml")
    args = parser.parse_args()
    try:
        client = ProxyClient.from_config(args.config)
        try:
            print(f"[OK] gateway reachable; initial proxy={client.proxy_id}")
        finally:
            client.close()
        target = default_config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(args.config).resolve(), target)
        if os.name != "nt":
            target.chmod(0o600)
        print(f"[OK] default client configuration saved to {target}")
        print("[OK] client configuration and session test passed")
        return 0
    except Exception as exc:
        print(f"[FAIL] client installation: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
