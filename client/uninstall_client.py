"""Remove the optional local engine-client service and runtime cache.

The normal client is on-demand and does not keep a tunnel or change system
proxy settings. This command is therefore safe to run even when the client is
used only as an imported Python module.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Uninstall the Proxy Pool engine client")
    parser.add_argument("--purge", action="store_true", help="also remove local client cache files")
    args = parser.parse_args()
    service = "proxy-pool-client.service"
    result = subprocess.run(f"systemctl stop {service}", shell=True, text=True, capture_output=True)
    if result.returncode not in (0, 5, 127):
        print(f"[FAIL] stopping local client service: {(result.stderr or result.stdout).strip()}")
        return 1
    if args.purge:
        root = Path(__file__).resolve().parent
        for path in (root / ".proxy_client", root / "__pycache__", root / "proxy_client" / "__pycache__"):
            if path.exists():
                shutil.rmtree(path)
        print("[OK] local client cache removed")
    print("[OK] engine client uninstalled; normal system networking was not changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
