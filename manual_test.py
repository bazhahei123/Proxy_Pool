from __future__ import annotations

import sys

from proxy_pool import ProxyPool


def main() -> int:
    config = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    url = sys.argv[2] if len(sys.argv) > 2 else "https://api.ipify.org?format=json"
    pool = ProxyPool.connect(config)
    try:
        print("health:")
        print(pool.health_snapshot())
        result = pool.request("GET", url, headers={"User-Agent": "ProxyPool-Manual-Test/1.0"})
        print("status:", result.response.status_code)
        print("proxy_id:", result.proxy_id)
        print("egress_ip:", result.egress_ip)
        print("attempts:", result.attempts)
        print("classification:", result.classification)
        print("body:", result.response.text[:500])
        return 0
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
