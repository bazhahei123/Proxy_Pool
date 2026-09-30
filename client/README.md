# Engine Client

Install dependencies and validate the Gateway connection:

```bash
python3 -m pip install -r requirements.txt
export GATEWAY_PASSWORD='your-gateway-password'
python3 install_client.py client_config.yaml
```

Installation validates the Gateway and saves the configuration as
`~/.proxy-pool/client_config.yaml` (with restrictive permissions on Linux).
After that, the CLI can use the installed configuration without `--config`.

The client supports three selection modes:

- `rules`: keep the node until a configured block response such as 403, then rotate and retry;
- `fixed_count`: rotate after `rotate_after` logical requests;
- `random`: choose a different random node for every logical request.

Use the API from the engine:

```python
from proxy_client import ProxyClient

client = ProxyClient.from_config("client_config.yaml")
try:
    result = client.request("GET", "https://example.com")
    print(result.response.status_code, result.proxy_id, result.attempts)
finally:
    client.close()
```

Or run one request without changing the machine's normal network settings:

```bash
python3 client_cli.py request https://example.com
```

The mode can be overridden for a command. `rules` rotates on configured block
statuses and retries at most `max_attempts_on_403` nodes; `fixed_count` ignores
those statuses and rotates only after `rotate_after` requests; `random` ignores
those statuses and chooses a node for each request:

```bash
python3 client_cli.py request --mode rules https://authorized.example/deny
python3 client_cli.py request --mode fixed_count https://one.example https://two.example https://three.example
python3 client_cli.py request --mode random https://one.example https://two.example https://three.example
```

Multiple URLs are processed in one client session. This is required for
`fixed_count` rotation to have meaning in directory or wordlist workflows.

Remove the optional local client runtime:

```bash
python3 uninstall_client.py
```

Add `--purge` to remove local client cache files. The client is on-demand and
does not install a permanent system proxy.
