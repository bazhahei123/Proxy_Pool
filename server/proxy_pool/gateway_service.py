from __future__ import annotations

import argparse
import asyncio

from .gateway import serve


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Proxy Pool HTTP gateway")
    parser.add_argument("config", nargs="?", default="server_config.yaml")
    args = parser.parse_args()
    asyncio.run(serve(args.config))


if __name__ == "__main__":
    main()
