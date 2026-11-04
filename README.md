# 🌐 Blitz Tailscale Exit Node & SOCKS5h Gateway (`blitz-tailscale-node`)

基于 **Blitz.cloud**（德国 Hetzner 数据中心节点）部署的 **Tailscale 出口节点（Exit Node）与内网 SOCKS5h / HTTP 代理网关**。

无需公网暴露任何代理端口，全链路运行在你的私有 **Tailnet** 虚拟内网内；支持两种并行访问方式：
1. **SOCKS5h 代理模式**：免切全局路由，Tailnet 内部设备直接指定 `socks5h://100.x.y.z:1080`，支持远端 DNS 解析；
2. **Tailscale 全局 Exit Node 模式**：通过官方 Exit Node 协议，将手机或电脑全局流量通过德国 Hetzner 高速出口转发。

---

## ⚡ 网络性能基准实测数据 (Ground Truth)

在 Blitz 容器内实测的真实网络延迟与吞吐量基准：

| 测试项 | 测速目标 / 协议 | 实测结果 |
| :--- | :--- | :--- |
| **出口 IPv4 地址** | 德国 Hetzner Falkenstein 数据中心 | `88.198.90.181` (`AS24940`) |
| **Cloudflare Anycast 延迟** | `1.1.1.1:443` | **10.6 ms** |
| **Google DNS 延迟** | `8.8.8.8:443` | **10.4 ms** |
| **GitHub 访问延迟** | `github.com:443` | **56.6 ms** |
| **公网下载带宽 (Download)** | Kernel.org 100MB 块传输 | **236.8 Mbps** (28.2 MB/s, TTFB: 79ms) |
| **公网上传带宽 (Upload)** | Cloudflare Speedtest 25MB 上传 | **348.7 Mbps** (43.6 MB/s, 0.60s 完成) |
| **协议支持** | Raw TCP / Raw UDP / STUN 打洞 | 全部放行，支持 WireGuard P2P 直连打洞 |

---

## 🚀 部署与使用流程

### 1. 接入 Tailnet (连接网络)
1. 访问部署好的 Web 控制台地址：`https://ts-exit.blitz48.blitz.cloud`；
2. 前往 [Tailscale 控制台生成 AuthKey](https://login.tailscale.com/admin/settings/keys)；
3. 在页面输入框粘贴 `tskey-auth-...` 并点击「立即加入 Tailnet」；
   *(或在 Blitz 环境变量中配置 `TS_AUTHKEY`，应用启动时会自动完成注册)*；
4. 连接成功后，页面将实时显示分配的 Tailscale IPv4（如 `100.x.y.z`）与 MagicDNS 域名。

---

### 2. 方式一：使用 SOCKS5h 代理 (按需分流，免改全局路由)

Tailscale 守护进程已直接在容器内监听 `0.0.0.0:1080`（SOCKS5）与 `0.0.0.0:8080`（HTTP 代理）。同属一个 Tailnet 的任意设备可直接访问：

#### 命令行 (cURL)
```bash
# 测试通过 Tailscale SOCKS5h 代理查询公网出口 IP（支持远程域名解析）
curl -x socks5h://<TAILSCALE_IP>:1080 https://ipinfo.io/json
```

#### 浏览器 (SwitchyOmega / 代理插件)
- **协议**：`SOCKS5`
- **代理服务器**：`<TAILSCALE_IP>`（例如 `100.88.99.100`）
- **端口**：`1080`
- 勾选由代理服务器解析域名（SOCKS5h）。

---

### 3. 方式二：启用 Tailscale 全局 Exit Node 出口

1. 进入 [Tailscale 机器管理后台 (Machines)](https://login.tailscale.com/admin/machines)；
2. 找到名为 `blitz-de-exit` 的设备；
3. 点击右侧菜单 `...` ➔ 选择 **Edit route settings**；
4. 勾选并批准 **Use as exit node**；
5. 在你的电脑或手机 Tailscale 客户端中：
   - **CLI 命令行**：
     ```bash
     tailscale set --exit-node=blitz-de-exit
     ```
   - **GUI 客户端**：在菜单中点击 `Exit Nodes` ➔ 勾选 `blitz-de-exit` 即可。

---

## 💓 容器双层防休眠保活机制 (Dual-Layer Keep-Alive)

Blitz 平台默认在 15 分钟无公网 HTTP 请求时自动进入休眠状态。本节点容器集成了与凭据管理器同等级别的双层保活架构：
1. **进程外独立脚本守护**：`/app/scripts/keepalive.py` 在容器后台常驻，每 **180 秒（3 分钟）** 向自身的公网健康检查接口（`https://ts-exit.blitz48.blitz.cloud/api/health`）发起一次携带毫秒级时间戳的 HTTPS 探测，若网络偶尔抖动失败，自动在 **15 秒内重试**；
2. **进程内异步协程兜底**：FastAPI 后台协程 `_keepalive_loop` 同样以 180 秒为周期进行公网回环保活；
3. **状态透明可查**：可通过访问 Web 控制台首页或直接请求 `GET /api/status` 实时查看累计保活探测次数与最近一次成功响应时间。

---

## 📂 项目结构

```text
blitz-tailscale-node/
├── app/
│   ├── main.py            # FastAPI 控制台后端 + Tailscale 状态管理 + 协程保活
│   └── static/
│       └── index.html     # Web 控制台界面 (展示 Tailscale IP、连接表单、SOCKS5 使用指引)
├── scripts/
│   └── keepalive.py       # 容器内独立运行的 Python 防休眠保活守护脚本 (180s 周期)
├── entrypoint.sh          # 容器启动入口 (拉起 tailscaled userspace、keepalive.py 与 Web 服务)
├── Dockerfile             # 包含官方 Tailscale 静态二进制的构建文件 (持久化 /app/data)
├── requirements.txt       # Python 依赖
└── README.md              # 完整使用说明文档
```
