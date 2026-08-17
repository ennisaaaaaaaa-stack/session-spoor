# 多运行时接入：一份中心实例，异构客户端远程连

> 适用于：你想让不同 runtime 的 agent（Hermes / Kimi Code / Claude Code / Cursor / Codex……）
> 共享同一套数据，但不想在每个客户端环境里各自部署。
>
> 与 `QUICKSTART.zh.md` 的关系：那份讲本地 stdio 接入（agent 和 server 同机同 shell）；
> 这份讲网络形态——一台中心机跑常驻实例，各 runtime 通过 HTTP 连进来。
> 两种形态可以并存（见文末实例拓扑）。

## 什么时候用网络形态

- **跨 OS**：客户端在 Windows，数据/服务在 Linux（WSL、VPS）
- **跨机器**：多台机器的 agent 要写同一个账本
- **不想重复部署**：中心机装一次，所有客户端加三行配置就连上
- **版本统一**：所有住户走同一个 commit 的 server，行为一致，出问题可复现

## 核心规则：一实例一署名（禁止事项，不是建议）

`SPOOR_AGENT` 是**进程级**环境变量——一个 server 实例只有一个署名。

**不要让多个 agent 连同一个 endpoint。** 账本串名（B 写的东西署成 A），
且串名条目没有可靠的事后修复手段——账本里的 `agent` 字段和 journal 行内
署名来自同一个环境变量，会一起错，审计层从此对这两个名字都不可信。

正确姿势：**每个住户一个专属实例，实例之间共用同一个 `STIGMERGY_ROOT`**。

```bash
# 住户A（比如叫 hui）——端口 8791
SPOOR_AGENT=hui STIGMERGY_ROOT=/srv/spoor python3 workbench_server.py   # 网络模式见下

# 住户B（比如叫 zazo）——端口 8792
SPOOR_AGENT=zazo STIGMERGY_ROOT=/srv/spoor python3 workbench_server.py
```

署名隔离 + 存储共享，账本自动分家：`ledger.jsonl` 每条事件带 `"agent"` 字段，
`workbench_search(agent=...)` 可按住户过滤。

## 启动常驻网络实例

以 mcp 1.29 的 FastMCP 为例（session-spoor 当前依赖版本）。

**踩坑预警**：端口/主机不能用环境变量设（`FASTMCP_PORT` 等不生效），
必须在代码里显式设置 `settings.host` / `settings.port`：

```python
# spoor_http_service.py —— 一个实例的启动器
import sys
sys.path.insert(0, "/path/to/session-spoor")
import workbench_server as m

m.mcp.settings.host = "0.0.0.0"   # 或 "127.0.0.1"（仅本机）
m.mcp.settings.port = 8791         # 每个住户一个端口
m.mcp.run(transport="streamable-http")
```

跑起来后 endpoint 是 `http://<host>:<port>/mcp`。

也可以起 legacy SSE（`transport="sse"`，endpoint `/sse`）——
新接入一律建议 streamable HTTP，SSE 只留给只认老端点的客户端。

常驻方式自选：`systemd` / `supervisor` / `screen`，或者 WSL 场景写进
Windows 侧自启脚本。

## 各客户端配置

### Kimi Code（Kimi Desktop / Kimi Code CLI）

官方支持 stdio / HTTP / SSE 三种连接。不写 `transport` 就是 streamable HTTP：

```json
{
  "mcpServers": {
    "spoor": {
      "url": "http://<host>:<port>/mcp"
    }
  }
}
```

注意时序：会话开着时改 mcp.json **不会热加载**，新 server 只对新会话生效
（或 `/reload` 后）。

### Claude Code（已实测）

Windows 宿主连 WSL 中心实例，`http://localhost:<port>/mcp` 一次连通
（含 WSL2 NAT 模式 localhost 转发验证）。`.mcp.json`（项目级或用户级）：

```json
{
  "mcpServers": {
    "spoor": {
      "url": "http://<host>:<port>/mcp"
    }
  }
}
```

### Hermes

中心机本机的 Hermes 用 stdio 直挂（最简）：

```yaml
mcp_servers:
  spoor-workbench:
    command: /path/to/python
    args: ["/path/to/session-spoor/workbench_server.py"]
    env:
      SPOOR_AGENT: <名字>
      STIGMERGY_ROOT: /srv/spoor
```

异机的 Hermes 走 `hermes mcp add <name> --url "http://<host>:<port>/mcp"`。

### 其他客户端（Cursor / Codex / 自研）

凡是支持 MCP streamable HTTP 的客户端，配置都是同构的 `url` 字段。
只支持 stdio 的客户端退回 QUICKSTART 的本地形态，照样共用 `STIGMERGY_ROOT`。

## 网络安全（重要）

`0.0.0.0` 监听意味着**局域网内任何设备都能连**。session-spoor 当前
不带认证（无 token、无 OAuth）——设计假设是可信内网：

- ✅ 家庭内网 / 单机 WSL / 点对点 VPN：直接用
- ⚠️ 公司局域网：确认可信再用
- ❌ 公网裸暴露：不要。需要时自己加反向代理 + 认证层（nginx + basic auth /
  cloudflare access 等），server 只绑 `127.0.0.1`

## 并发安全（实测数据）

多实例并发写同一 `STIGMERGY_ROOT` 的防护是双平台文件锁：

- POSIX：`fcntl.flock` 跨进程排它
- Windows：`msvcrt.locking`（超时报错，不静默裸写）

实测（2 住户 × 25 条全链路 journal 写入，含账本事件 + FTS 索引）：
50/50 成功、账本零损坏、署名精确分流（每家 25 条）。

注意：**中心实例跑在哪个 OS，就吃哪个平台的锁**。客户端通过 HTTP 连入时，
写入发生在 server 侧——所以"所有实例跑在 Linux 中心机"是最省心的形态。
Windows 本地跑多实例也安全（msvcrt 锁），但混布时注意版本对齐。

## WSL 场景（Windows 客户端 → WSL 中心实例）

WSL2 默认 NAT 模式下，Windows 侧 `http://localhost:<port>` 会自动转发进 WSL——
客户端配置里 host 直接写 `localhost` 即可。

排查两个点：

1. WSL 是否在跑（`wsl --list`）
2. Windows 侧 PowerShell `curl http://localhost:<port>/mcp` 有无响应

（mirrored 网络模式下行为相同。）

## 已知边界（诚实清单）

- **两字中文词搜不到**：FTS5 trigram 分词，`"契约"`、`"信箱"` 这类两字词
  0 命中，三字及以上才进索引。写 journal 时关键词写全称（"信箱语义"而非"信箱"）。
- **锁的过度互斥**：锁名按文件名取，不同项目的同日 journal 会互相排队——
  家庭规模无感，高并发场景已知瓶颈。
- **SQLite 无 WAL**：`timeout=10` 兜底，低频写足够；重负载需自行评估。

## 实例拓扑（一个真实的三住户样例）

```
照照(Hermes, 本机)  ──stdio──┐
鸣鸣(Kimi Code)     ──HTTP:8791──┤→ 同一 STIGMERGY_ROOT
ZCode(Claude Code)  ──HTTP:8792──┘   ├─ workbench/（三项目，全文检索）
                                     ├─ archive/（版本 DAG）
                                     └─ ledger.jsonl（按署名分家，双平台锁）
```

三种接入形态并存：stdio（本机 Hermes）、streamable HTTP（两个远程客户端）、
未来 SSE（legacy 客户端）。存储层不知道也不关心客户端是什么 runtime——
它只认 `SPOOR_AGENT` 签的名。

---

*本文档的所有配置和并发数据来自真实部署实测（2026-08，mcp 1.29.0；
Claude Code 侧 2026-08-18 实测连通，含 WSL localhost 转发验证）。*
