from __future__ import annotations

import argparse
import os
import sys

from proxy_client import ProxyClient


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
        print("[OK] client configuration and session test passed")
        return 0
    except Exception as exc:
        print(f"[FAIL] client installation: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
