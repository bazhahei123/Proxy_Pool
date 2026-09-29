"""Stop and remove all Proxy Pool GOST services.

Run this command on the hub VPS with the same config and environment variables
used by install.py. SSH is used only to stop the remote systemd services.
"""
from __future__ import annotations

import argparse
import shlex
import subprocess

import paramiko

from proxy_pool.config import load_config


def _service_name(node_id: str) -> str:
    return f"proxy-pool-gost-{node_id}.service"


def _stop_local_relay(operation: str) -> None:
    service = "proxy-pool-gost-relay.service"
    result = subprocess.run(
        f"sudo systemctl stop {service}",
        shell=True,
        text=True,
        capture_output=True,
    )
    if result.returncode not in (0, 5):
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or f"systemctl returned {result.returncode}")
    if operation == "purge":
        subprocess.run(f"sudo rm -f /etc/systemd/system/{service} && sudo systemctl daemon-reload", shell=True, check=True)
        subprocess.run("sudo rm -rf -- /usr/local/lib/proxy-pool", shell=True, check=True)
    print(f"[OK] local Relay stopped ({operation})")


def _stop_remote(node, operation: str, timeout: float) -> None:
    if not node.ssh:
        raise RuntimeError("node has no ssh configuration")
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    if node.ssh.known_hosts and node.ssh.known_hosts.is_file():
        client.load_host_keys(str(node.ssh.known_hosts))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        # Match install.py: first-time installation does not require a manual
        # SSH connection just to populate known_hosts.
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        node.ssh.host,
        port=node.ssh.port,
        username=node.ssh.username,
        password=node.ssh.password,
        key_filename=str(node.ssh.private_key) if node.ssh.private_key else None,
        passphrase=node.ssh.passphrase,
        timeout=timeout,
    )
    try:
        service = _service_name(node.id)
        reverse_service = f"proxy-pool-gost-{node.id}-reverse.service"
        commands = [
            f"sudo systemctl stop {shlex.quote(service)} || true",
            f"sudo systemctl stop {shlex.quote(reverse_service)} || true",
        ]
        if operation in {"clean", "purge"} and node.gost:
            commands.append(f"sudo rm -f -- {shlex.quote(node.gost.install_dir + '/gost')}")
        _, stdout, stderr = client.exec_command(" && ".join(commands))
        if stdout.channel.recv_exit_status() != 0:
            raise RuntimeError(stderr.read().decode(errors="replace").strip() or "remote cleanup failed")
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Stop or clean Proxy Pool GOST tunnels")
    parser.add_argument("operation", choices=("stop", "clean", "purge"))
    parser.add_argument("config", nargs="?", default="config.yaml")
    args = parser.parse_args()
    config = load_config(args.config)
    failures: list[str] = []

    if config.reverse_tunnel.enabled:
        try:
            _stop_local_relay(args.operation)
        except Exception as exc:
            failures.append(f"hub Relay: {type(exc).__name__}: {exc}")
            print(f"[FAIL] hub Relay: {exc}")

    for node in config.proxies:
        if not node.enabled or node.kind not in {"reverse_gost_client", "managed_gost"}:
            continue
        try:
            _stop_remote(node, args.operation, config.runtime.timeout_seconds)
            suffix = {
                "stop": "tunnel stopped; files kept",
                "clean": "tunnel stopped; remote GOST binary removed",
                "purge": "tunnel stopped; remote GOST files removed",
            }[args.operation]
            print(f"[OK] {node.id}: {suffix}")
        except Exception as exc:
            failures.append(f"{node.id}: {type(exc).__name__}: {exc}")
            print(f"[FAIL] {node.id}: {exc}")

    if failures:
        print("\nUninstall completed with failures:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("All configured Proxy Pool tunnels have been stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
