# Engine Client

Install dependencies and validate the Gateway connection:

```bash
python3 -m pip install -r requirements.txt
export GATEWAY_PASSWORD='your-gateway-password'
python3 install_client.py client_config.yaml
```

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
python3 client_cli.py request --config client_config.yaml https://example.com
```

Remove the optional local client runtime:

```bash
python3 uninstall_client.py
```

Add `--purge` to remove local client cache files. The client is on-demand and
does not install a permanent system proxy.
