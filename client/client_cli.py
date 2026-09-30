from __future__ import annotations

import argparse
import json
import sys

from proxy_client import ProxyClient


def main() -> int:
    parser = argparse.ArgumentParser(description="Proxy Pool engine client")
    parser.add_argument("command", choices=("health", "request"))
    parser.add_argument("--config", default="client_config.yaml")
    parser.add_argument("--method", default="GET")
    parser.add_argument("url", nargs="?")
    args = parser.parse_args()
    client = ProxyClient.from_config(args.config)
    try:
        if args.command == "health":
            print(json.dumps({"gateway": "ok", "proxy_id": client.proxy_id}, ensure_ascii=False))
            return 0
        if not args.url:
            parser.error("request requires a URL")
        result = client.request(args.method, args.url)
        print(f"status={result.response.status_code} proxy_id={result.proxy_id} attempts={result.attempts}")
        sys.stdout.buffer.write(result.response.content)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
