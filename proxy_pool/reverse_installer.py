"""One-shot reverse relay deployment for the middle VPS.

The command is intended to run on the middle VPS.  It installs a local GOST
relay and installs one outbound GOST client on every configured VPS.  SSH is
used only during installation; the resulting services reconnect themselves.
"""
from __future__ import annotations

import hashlib
import platform
import shlex
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

import paramiko

from .models import AppConfig, ProxyNodeConfig
from .runtime_sync import SyncProxyPool


def _run(command: str) -> None:
    result = subprocess.run(command, shell=True, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip() or f"exit {result.returncode}")


def _artifact(config: AppConfig, suffix: str) -> Path:
    directory = config.gost_binary_dir
    if directory is None:
        raise FileNotFoundError("gost.binary_dir is not configured")
    matches = sorted(directory.glob(f"*linux_{suffix}.tar*"))
    if not matches:
        raise FileNotFoundError(f"no GOST archive for {suffix} in {directory}")
    return matches[0]


def _extract(path: Path) -> Path:
    if path.suffix not in {".gz", ".tar", ".tgz"}:
        return path
    root = Path(tempfile.mkdtemp(prefix="proxy-pool-gost-"))
    with tarfile.open(path, "r:*") as archive:
        archive.extractall(root)
    found = list(root.rglob("gost"))
    if not found:
        raise FileNotFoundError(f"gost executable not found in {path}")
    return found[0]


def _local_suffix() -> str:
    machine = platform.machine().lower()
    if machine in {"aarch64", "arm64"}:
        return "arm64"
    if machine in {"x86_64", "amd64"}:
        return "amd64"
    raise RuntimeError(f"unsupported middle VPS architecture: {machine}")


def _install_local_relay(config: AppConfig) -> None:
    relay = config.reverse_tunnel
    binary = _extract(_artifact(config, _local_suffix()))
    remote_bin = "/usr/local/lib/proxy-pool/gost"
    auth = ""
    if relay.relay_username:
        auth = f"{relay.relay_username}:{relay.relay_password or ''}@"
    listen = f"relay://{auth}{relay.relay_host}:{relay.relay_port}?bind=true"
    unit = "\n".join([
        "[Unit]", "Description=Proxy Pool GOST Relay", "After=network-online.target",
        "[Service]", "Type=simple", f"ExecStart={remote_bin} -L {listen}",
        "Restart=always", "RestartSec=3", "[Install]", "WantedBy=multi-user.target", "",
    ])
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    tmp = f"/tmp/proxy-pool-gost-{digest[:12]}"
    _run(f"sudo mkdir -p /usr/local/lib/proxy-pool && sudo install -m 0755 {shlex.quote(str(binary))} {tmp} && sudo install -m 0755 {tmp} {remote_bin} && sudo rm -f {tmp}")
    service = "proxy-pool-gost-relay.service"
    encoded = shlex.quote(unit)
    _run(f"printf %s {encoded} | sudo tee /etc/systemd/system/{service} >/dev/null && sudo systemctl daemon-reload && sudo systemctl enable --now {service} && sudo systemctl restart {service} && sudo systemctl is-active --quiet {service}")
    print(f"[OK] relay active on {relay.relay_host}:{relay.relay_port}; sha256={digest}")


def _remote_install(config: AppConfig, node: ProxyNodeConfig) -> None:
    assert node.ssh and node.gost
    relay = config.reverse_tunnel
    assert node.gost.hub_entry_port is not None
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    if node.ssh.known_hosts and node.ssh.known_hosts.is_file():
        client.load_host_keys(str(node.ssh.known_hosts))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node.ssh.host, port=node.ssh.port, username=node.ssh.username,
                   password=node.ssh.password,
                   key_filename=str(node.ssh.private_key) if node.ssh.private_key else None,
                   passphrase=node.ssh.passphrase, timeout=config.runtime.timeout_seconds)
    try:
        _, out, _ = client.exec_command("uname -a")
        text = out.read().decode(errors="replace").lower()
        suffix = "arm64" if "aarch64" in text or "arm64" in text else "amd64" if "x86_64" in text or "amd64" in text else None
        if suffix is None:
            raise RuntimeError(f"unsupported architecture: {text.strip()}")
        binary = _extract(_artifact(config, suffix))
        remote_bin = f"{node.gost.install_dir}/gost"
        tmp = f"/tmp/proxy-pool-{node.id}-gost"
        sftp = client.open_sftp(); sftp.put(str(binary), tmp); sftp.close()
        user = relay.relay_username or ""
        password = relay.relay_password or ""
        # The relay must receive an explicit bind host.  An empty host makes
        # GOST 3.3 build the invalid address 0.0.0.0::PORT on the hub.
        forward = (
            f"rtcp://{relay.hub_entry_host}:{node.gost.hub_entry_port}/"
            f"127.0.0.1:{node.gost.remote_port}"
        )
        relay_url = f"relay://{user}:{password}@{relay.hub_host}:{relay.relay_port}"
        service = f"proxy-pool-gost-{node.id}.service"
        reverse_service = f"proxy-pool-gost-{node.id}-reverse.service"
        local_unit = "\n".join([
            "[Unit]", "Description=Proxy Pool Reverse GOST Client", "After=network-online.target",
            "[Service]", "Type=simple",
            # This listener must be direct. It is the egress path on the
            # remote VPS and must not inherit the Relay forwarding chain.
            f"ExecStart={remote_bin} -L socks5://127.0.0.1:{node.gost.remote_port}",
            "Restart=always", "RestartSec=3", "[Install]", "WantedBy=multi-user.target", "",
        ])
        reverse_unit = "\n".join([
            "[Unit]", "Description=Proxy Pool Reverse GOST Relay Client", "After=network-online.target",
            "[Service]", "Type=simple",
            f"ExecStart={remote_bin} -L {forward} -F {relay_url}",
            "Restart=always", "RestartSec=3", "[Install]", "WantedBy=multi-user.target", "",
        ])
        command = " && ".join([
            f"sudo mkdir -p {shlex.quote(node.gost.install_dir)}",
            f"sudo install -m 0755 {shlex.quote(tmp)} {shlex.quote(remote_bin)}",
            f"sudo rm -f {shlex.quote(tmp)}",
            f"printf %s {shlex.quote(local_unit)} | sudo tee /etc/systemd/system/{service} >/dev/null",
            f"printf %s {shlex.quote(reverse_unit)} | sudo tee /etc/systemd/system/{reverse_service} >/dev/null",
            "sudo systemctl daemon-reload",
            f"sudo systemctl enable --now {service}",
            f"sudo systemctl restart {service}",
            f"sudo systemctl enable --now {reverse_service}",
            f"sudo systemctl restart {reverse_service}",
            f"sudo systemctl is-active --quiet {service}",
        ])
        _, stdout, stderr = client.exec_command(command)
        if stdout.channel.recv_exit_status() != 0:
            raise RuntimeError(stderr.read().decode(errors="replace").strip() or "systemd startup failed")
        print(f"[OK] {node.id}: reverse client active, entry 127.0.0.1:{node.gost.hub_entry_port}")
    finally:
        client.close()


def install_reverse(config: AppConfig) -> int:
    _install_local_relay(config)
    failures: list[str] = []
    for node in config.proxies:
        if not node.enabled or node.kind != "reverse_gost_client":
            continue
        try:
            _remote_install(config, node)
        except Exception as exc:
            failures.append(f"{node.id}: {type(exc).__name__}: {exc}")
            print(f"[FAIL] {node.id}: {exc}")
    if failures:
        print("\nReverse installation failed:")
        for item in failures:
            print(f"  - {item}")
        return 1
    try:
        # Reverse clients need a short amount of time to authenticate with
        # the Relay and create their remote entry listeners.
        snapshot = {}
        bad: list[str] = []
        for attempt in range(1, 16):
            pool = None
            try:
                pool = SyncProxyPool.connect(config_to_source(config))
                snapshot = pool.health_snapshot()
                bad = [node_id for node_id, value in snapshot.items() if not value.get("egress_ip")]
            finally:
                if pool is not None:
                    pool.close()
            if not bad:
                break
            print(f"[WAIT] reverse tunnel health attempt {attempt}/15; pending: {', '.join(bad)}")
            time.sleep(2)
        print(snapshot)
        if bad:
            print("[FAIL] reverse tunnel health: " + ", ".join(bad))
            print("Check: sudo journalctl -u proxy-pool-gost-relay.service -n 50 --no-pager")
            for node in config.proxies:
                if node.enabled and node.kind == "reverse_gost_client":
                    print(f"Check remote {node.id}: sudo journalctl -u proxy-pool-gost-{node.id}.service -n 50 --no-pager")
            return 1
    except Exception as exc:
        print(f"[FAIL] reverse tunnel health: {type(exc).__name__}: {exc}")
        return 1
    print("[OK] all reverse tunnels passed end-to-end health checks")
    return 0


def config_to_source(config: AppConfig) -> dict:
    """Runtime health only needs the already parsed object."""
    return config  # type: ignore[return-value]
