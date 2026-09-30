from __future__ import annotations

import argparse
import json

from . import ProxyPool


def _run(args: argparse.Namespace) -> None:
    pool = ProxyPool.connect(args.config)
    try:
        result = pool.request(args.method, args.url)
        print(json.dumps({
            "status": result.response.status_code,
            "proxy_id": result.proxy_id,
            "egress_ip": result.egress_ip,
            "attempts": result.attempts,
            "classification": result.classification,
        }, ensure_ascii=False))
    finally:
        pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Proxy pool smoke-test CLI")
    parser.add_argument("config")
    parser.add_argument("url")
    parser.add_argument("--method", default="GET")
    args = parser.parse_args()
    _run(args)


if __name__ == "__main__":
    main()
