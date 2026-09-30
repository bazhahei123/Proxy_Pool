# Python GOST 代理池

这是同步 Python 代理池。第一阶段由中间 VPS 运行 GOST Relay，远端节点主动回连；SSH/SCP 只在 `install.py` 部署阶段使用，安装完成后由 systemd 维持隧道。

## 安装

在中间 VPS 的终端中执行：

```cmd
cd /path/to/proxy/server
python3 -m pip install -r requirements.txt
export SSH_PASSWORD='服务器SSH密码'
export GOST_PROXY_PASSWORD='Relay密码'
python3 install.py server_config.yaml
```

在反向模式下，安装程序会先在中间 VPS 启动 Relay，再自动识别每台远端 VPS 的 amd64/arm64 架构、上传 GOST、创建反向客户端 systemd 服务，最后通过自动分配的入口端口进行出口 IP 健康检查。

停止全部隧道但保留文件，便于之后重新启动：

```bash
python3 uninstall.py stop server_config.yaml
```

删除远端通过 SCP 上传的 GOST 文件：

```bash
python3 uninstall.py clean server_config.yaml
```

删除远端 GOST 文件以及中间 VPS 本地安装目录：

```bash
python3 uninstall.py purge server_config.yaml
```

## 配置

服务端配置使用 `server/server_config.yaml`。将 `reverse_tunnel.enabled` 设为 `true`，填写中间 VPS 的 `hub_host`，节点使用 `kind: reverse_gost_client`。`entry_port_base: "11000-11010"` 会按节点顺序自动分配端口，不需要填写 `hub_entry_port`。

云安全组只需放行中间 VPS 的 Relay 入站端口；远端 VPS 不需要暴露 1080。

启用 `gateway.enabled` 后，`server/install.py` 还会启动中间 VPS 的统一 HTTP Gateway。引擎侧客户端使用 `client/client_config.yaml` 连接它；客户端支持 `rules`、`fixed_count` 和 `random` 三种模式。

## 健康检查

```yaml
diagnostics:
  ip_check_url: "https://ip.3322.net"
  health_urls:
    - "https://你的授权测试站点/health"
```

`ip.3322.net` 用来确认代理后的公网出口 IP；无法访问时可以换成自有接口。健康 URL 应返回 200-399，403 页面应作为业务目标而不是安装健康地址。

## 同步调用

```python
from proxy_pool import ProxyPool

pool = ProxyPool.connect("server/server_config.yaml")
try:
    result = pool.request(method="GET", url="https://example.com")
    print(result.response.status_code, result.proxy_id, result.egress_ip, result.attempts)
finally:
    pool.close()
```

运行时只连接已安装的 GOST，不重复 SSH、SCP 或部署。403、418、429 会在不同节点间有限重试。

## 403 测试

```cmd
python test_403_server.py --host 0.0.0.0 --port 18080 --status 403
```
# 第一阶段：中间 VPS 反向隧道

第一阶段的 `install.py` 应在中间 VPS 上运行。将 `reverse_tunnel.enabled` 设为 `true` 后，脚本会在中间 VPS 启动 GOST Relay，再通过 SSH 将 GOST 客户端安装到每个 `reverse_gost_client` 节点。SSH 只用于安装；systemd 服务会在后台持续回连 Relay。

`entry_port_base` 可以写成 `11000` 或 `11000-11010`。启用节点按配置顺序自动获得入口端口，不需要在节点下填写 `hub_entry_port`。分配时会从当前游标开始逐个探测；端口被占用就继续尝试下一个，单个节点最多尝试 5 个端口。某个节点成功后，下一个节点从后一个端口继续。入口只绑定中间 VPS 的 `entry_host`（默认 `127.0.0.1`），不会把 SOCKS 服务直接暴露到公网。

云厂商安全组只需放行中间 VPS 的 Relay 入站端口（例如 TCP 443），远端 VPS 只需能主动出站连接该端口。
