from __future__ import annotations

import argparse
import json
import sys

from proxy_client import ProxyClient
from proxy_client.config import resolve_config_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Proxy Pool engine client")
    parser.add_argument("command", choices=("health", "request"))
    parser.add_argument("--config", help="configuration path; defaults to the file saved by install_client.py")
    parser.add_argument("--method", default="GET")
    parser.add_argument("--mode", choices=("rules", "fixed_count", "random"), help="override selector.mode for this command")
    parser.add_argument("url", nargs="*")
    args = parser.parse_args()
    client = ProxyClient.from_config(resolve_config_path(args.config), mode=args.mode)
    try:
        if args.command == "health":
            print(json.dumps({"gateway": "ok", "proxy_id": client.proxy_id}, ensure_ascii=False))
            return 0
        if not args.url:
            parser.error("request requires at least one URL")
        for target in args.url:
            result = client.request(args.method, target)
            print(f"target={target} status={result.response.status_code} proxy_id={result.proxy_id} attempts={result.attempts}")
            sys.stdout.buffer.write(result.response.content)
            sys.stdout.buffer.write(b"\n")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
