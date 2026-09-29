# Python GOST Proxy Pool

This is a synchronous Python proxy pool. In phase 1 the hub VPS runs a GOST Relay and remote nodes connect outbound to it. SSH/SCP is used only by `install.py`; systemd keeps the reverse clients connected afterwards.

## Install

Run these commands on the hub VPS:

```cmd
cd /path/to/proxy
python -m pip install -r requirements.txt
export SSH_PASSWORD='server SSH password'
export GOST_PROXY_PASSWORD='Relay password'
python install.py config.yaml
```

In reverse mode the installer starts the Relay on the hub, detects amd64 or arm64 on every remote VPS, uploads GOST, creates reverse client systemd services, and checks each tunnel through its automatically assigned entry port.

Stop all tunnels while keeping files for a later restart:

```bash
python3 uninstall.py stop config.yaml
```

Remove the GOST binary copied to remote nodes through SCP:

```bash
python3 uninstall.py clean config.yaml
```

Remove the remote GOST binary and the local installation directory on the hub:

```bash
python3 uninstall.py purge config.yaml
```

## Configuration

Use the current `config.yaml` as the configuration reference. Set `reverse_tunnel.enabled: true`, fill in the hub `hub_host`, and use `kind: reverse_gost_client` for remote nodes. With `entry_port_base: "11000-11010"`, ports are assigned in node order and `hub_entry_port` is not needed.

Open only the Relay inbound port in the hub security group. Remote VPS nodes do not need to expose port 1080.

## Health checks

```yaml
diagnostics:
  ip_check_url: "https://ip.3322.net"
  health_urls:
    - "https://your-authorized-host/health"
```

The IP URL confirms the public egress address through the proxy. Replace it with an endpoint you control if this service is unavailable. Health URLs should return 200-399; use a 403 page as a business target, not as an installation health URL.

## Synchronous usage

```python
from proxy_pool import ProxyPool

pool = ProxyPool.connect("config.yaml")
try:
    result = pool.request(method="GET", url="https://example.com")
    print(result.response.status_code, result.proxy_id, result.egress_ip, result.attempts)
finally:
    pool.close()
```

Runtime only attaches to installed GOST endpoints. It does not repeat SSH, SCP, or provisioning. Configured 403, 418, and 429 responses are retried on different nodes with a bounded attempt count.

## 403 test

```cmd
python test_403_server.py --host 0.0.0.0 --port 18080 --status 403
```
# Phase 1: reverse tunnels on a hub VPS

Run `install.py` on the hub VPS. With `reverse_tunnel.enabled: true`, it starts a GOST Relay on the hub and installs a GOST client on every `reverse_gost_client` node through SSH. SSH is used only for installation; systemd keeps each client connected to the Relay afterwards.

`entry_port_base` accepts either one port (`11000`) or a range (`11000-11010`). Enabled nodes receive ports in configuration order, so `hub_entry_port` is not required in individual nodes. Entries bind to `entry_host` (default `127.0.0.1`) on the hub and are not exposed as public SOCKS listeners.

Open only the Relay inbound port (for example TCP 443) in the hub provider security group. Remote VPS nodes only need outbound access to that port.
