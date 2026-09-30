"""Explicit one-time deployment command for public GOST listeners."""

from __future__ import annotations

import argparse
import hashlib
import shlex
import tarfile
import tempfile
from pathlib import Path

import paramiko

from proxy_pool.config import load_config
from proxy_pool.runtime_sync import SyncProxyPool
from proxy_pool.reverse_installer import install_reverse

def _install_sync(config_path: str) -> None:
    """Install public GOST with SSH only as a bootstrap transport."""
    config = load_config(config_path)
    if not config.diagnostics.ip_check_url and not config.diagnostics.health_urls:
        raise RuntimeError(
            "configure diagnostics.ip_check_url or diagnostics.health_urls; "
            "installation must perform an end-to-end proxy health check"
        )
    for node in config.proxies:
        if node.kind != "managed_gost" or not node.ssh or not node.gost:
            continue
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(node.ssh.host, port=node.ssh.port, username=node.ssh.username,
                       password=node.ssh.password, key_filename=str(node.ssh.private_key) if node.ssh.private_key else None,
                       passphrase=node.ssh.passphrase, timeout=config.runtime.timeout_seconds)
        try:
            _, stdout, stderr = client.exec_command("uname -a")
            arch_text = stdout.read().decode(errors="replace").lower()
            suffix = "arm64" if "aarch64" in arch_text or "arm64" in arch_text else "amd64" if "x86_64" in arch_text or "amd64" in arch_text else None
            if suffix is None:
                raise RuntimeError(f"{node.id}: unsupported architecture: {arch_text.strip()}")
            artifact = _find_artifact(node.gost.binary_path, node.gost.binary_dir, suffix)
            binary = _extract_binary(artifact)
            digest = hashlib.sha256(binary.read_bytes()).hexdigest()
            sftp = client.open_sftp()
            remote_tmp = f"/tmp/proxy-pool-{node.id}-gost"
            sftp.put(str(binary), remote_tmp)
            sftp.close()
            listen_host = node.gost.remote_bind_host if node.gost.remote_bind_host != "127.0.0.1" else "0.0.0.0"
            auth = f"{node.gost.username}:{node.gost.password}@" if node.gost.username else ""
            remote_bin = f"{node.gost.install_dir}/gost"
            service = f"proxy-pool-gost-{node.id}.service"
            unit = "\n".join(["[Unit]", "Description=Proxy Pool GOST", "After=network-online.target", "[Service]",
                "Type=simple", f"ExecStart={remote_bin} -L socks5://{auth}{listen_host}:{node.gost.remote_port}",
                "Restart=always", "RestartSec=3", "[Install]", "WantedBy=multi-user.target", ""])
            command = " && ".join([
                f"sudo mkdir -p {shlex.quote(node.gost.install_dir)}",
                f"sudo install -m 0755 {shlex.quote(remote_tmp)} {shlex.quote(remote_bin)}",
                f"sudo rm -f {shlex.quote(remote_tmp)}",
                f"printf %s {shlex.quote(unit)} | sudo tee /etc/systemd/system/{shlex.quote(service)} >/dev/null",
                "sudo systemctl daemon-reload",
                f"sudo systemctl enable --now {shlex.quote(service)}",
                f"sudo systemctl is-active --quiet {shlex.quote(service)}",
            ])
            _, out, err = client.exec_command(command)
            if out.channel.recv_exit_status() != 0:
                raise RuntimeError(err.read().decode(errors="replace"))
            print(f"{node.id}: installed, systemd active, sha256={digest}")
        finally:
            client.close()
    pool = SyncProxyPool.connect(config_path)
    try:
        snapshot = pool.health_snapshot()
        failures: list[str] = []
        for node_id, result in snapshot.items():
            if not result.get("egress_ip"):
                failures.append(f"{node_id}: proxy request to ip_check_url failed")
            checks = result.get("checks", {})
            for url, status in checks.items():
                if not isinstance(status, int) or status < 200 or status >= 400:
                    failures.append(f"{node_id}: health check failed for {url}: {status}")
        print(snapshot)
        if failures:
            raise RuntimeError("GOST communication health check failed: " + "; ".join(failures))
        print("all GOST nodes passed end-to-end health checks")
    finally:
        pool.close()


def _find_artifact(binary_path, binary_dir, suffix):
    if binary_path and binary_path.is_file():
        return binary_path
    if not binary_dir:
        raise FileNotFoundError("GOST binary_dir is not configured")
    candidates = sorted(binary_dir.glob(f"*linux_{suffix}.tar*"))
    if not candidates:
        raise FileNotFoundError(f"no GOST archive for {suffix} in {binary_dir}")
    return candidates[0]


def _install_sync_reported(config_path: str) -> int:
    """Run installation with an explicit failure stage for every node."""
    config = load_config(config_path)
    if config.reverse_tunnel.enabled:
        return install_reverse(config, config_path)
    if not config.diagnostics.ip_check_url and not config.diagnostics.health_urls:
        print("[FAIL] health-check configuration: set diagnostics.ip_check_url or health_urls")
        return 2

    failures: list[str] = []
    for node in config.proxies:
        if not node.enabled or node.kind != "managed_gost":
            continue
        stage = "configuration"
        client = None
        try:
            if not node.ssh or not node.gost:
                raise RuntimeError("managed_gost requires ssh and gost sections")
            stage = "SSH connection"
            client = paramiko.SSHClient()
            client.load_system_host_keys()
            # Strict verification remains available when known_hosts is
            # configured. Without it, install is intentionally one-click:
            # Paramiko accepts the first host key (TOFU) so no manual SSH
            # connection is required before this command.
            if node.ssh.known_hosts and node.ssh.known_hosts.is_file():
                client.load_host_keys(str(node.ssh.known_hosts))
                client.set_missing_host_key_policy(paramiko.RejectPolicy())
            else:
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(node.ssh.host, port=node.ssh.port, username=node.ssh.username,
                           password=node.ssh.password,
                           key_filename=str(node.ssh.private_key) if node.ssh.private_key else None,
                           passphrase=node.ssh.passphrase, timeout=config.runtime.timeout_seconds)

            stage = "remote architecture detection"
            _, stdout, _ = client.exec_command("uname -a")
            arch = stdout.read().decode(errors="replace").lower()
            suffix = "arm64" if "aarch64" in arch or "arm64" in arch else "amd64" if "x86_64" in arch or "amd64" in arch else None
            if suffix is None:
                raise RuntimeError(f"unsupported architecture: {arch.strip()}")

            stage = "GOST artifact lookup"
            artifact = _find_artifact(node.gost.binary_path, node.gost.binary_dir, suffix)
            binary = _extract_binary(artifact)
            digest = hashlib.sha256(binary.read_bytes()).hexdigest()

            listen_host = node.gost.remote_bind_host if node.gost.remote_bind_host != "127.0.0.1" else "0.0.0.0"
            auth = f"{node.gost.username}:{node.gost.password}@" if node.gost.username else ""
            remote_bin = f"{node.gost.install_dir}/gost"
            service = f"proxy-pool-gost-{node.id}.service"
            _, digest_out, _ = client.exec_command(
                f"sha256sum {shlex.quote(remote_bin)} 2>/dev/null || true"
            )
            remote_digest = digest_out.read().decode(errors="replace").split()
            binary_current = bool(remote_digest and remote_digest[0].lower() == digest.lower())
            upload_commands: list[str] = [f"sudo mkdir -p {shlex.quote(node.gost.install_dir)}"]
            if not binary_current:
                stage = "GOST upload"
                remote_tmp = f"/tmp/proxy-pool-{node.id}-gost"
                sftp = client.open_sftp()
                sftp.put(str(binary), remote_tmp)
                sftp.close()
                upload_commands.extend([
                    f"sudo install -m 0755 {shlex.quote(remote_tmp)} {shlex.quote(remote_bin)}",
                    f"sudo rm -f {shlex.quote(remote_tmp)}",
                ])
            else:
                print(f"[SKIP] {node.id}: GOST binary already matches SHA256 {digest}")
            unit = "\n".join(["[Unit]", "Description=Proxy Pool GOST", "After=network-online.target",
                "[Service]", "Type=simple", f"ExecStart={remote_bin} -L socks5://{auth}{listen_host}:{node.gost.remote_port}",
                "Restart=always", "RestartSec=3", "[Install]", "WantedBy=multi-user.target", ""])
            command = " && ".join(upload_commands + [
                f"printf %s {shlex.quote(unit)} | sudo tee /etc/systemd/system/{shlex.quote(service)} >/dev/null",
                "sudo systemctl daemon-reload",
                f"sudo systemctl enable --now {shlex.quote(service)}",
                f"sudo systemctl is-active --quiet {shlex.quote(service)}",
            ])
            stage = "GOST systemd startup"
            _, out, err = client.exec_command(command)
            if out.channel.recv_exit_status() != 0:
                detail = err.read().decode(errors="replace").strip()
                raise RuntimeError(detail or "systemctl is-active returned failure")
            client.close()
            client = None
            print(f"[OK] {node.id}: remote GOST active; sha256={digest}")
        except Exception as exc:
            message = f"{node.id}: {stage}: {type(exc).__name__}: {exc}"
            failures.append(message)
            print(f"[FAIL] {message}")
        finally:
            if client is not None:
                client.close()

    if failures:
        print("\nInstallation failed. No runtime proxy pool was started:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    stage = "GOST end-to-end tunnel health"
    try:
        pool = SyncProxyPool.connect(config_path)
        try:
            snapshot = pool.health_snapshot()
            health_failures: list[str] = []
            for node_id, result in snapshot.items():
                if not result.get("egress_ip"):
                    health_failures.append(f"{node_id}: ip_check_url request failed")
                for url, status in result.get("checks", {}).items():
                    if not isinstance(status, int) or not 200 <= status < 400:
                        health_failures.append(f"{node_id}: {url} returned {status}")
            print(snapshot)
            if health_failures:
                print("[FAIL] " + stage + ":")
                for failure in health_failures:
                    print(f"  - {failure}")
                return 1
            print("[OK] all GOST nodes passed end-to-end tunnel health checks")
            return 0
        finally:
            pool.close()
    except Exception as exc:
        print(f"[FAIL] {stage}: {type(exc).__name__}: {exc}")
        return 1


def _extract_binary(path):
    if path.suffix != ".gz" and path.suffix != ".tar":
        return path
    root = Path(tempfile.mkdtemp(prefix="proxy-pool-gost-"))
    with tarfile.open(path, "r:*") as archive:
        archive.extractall(root)
    binaries = list(root.rglob("gost"))
    if not binaries:
        raise FileNotFoundError(f"no gost executable in {path}")
    return binaries[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy GOST to managed SSH nodes")
    parser.add_argument("config", nargs="?", default="server_config.yaml")
    args = parser.parse_args()
    raise SystemExit(_install_sync_reported(args.config))


if __name__ == "__main__":
    main()
