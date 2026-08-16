# 谁能用 / 怎么用 / 多 agent 怎么配置

> 一份写给人类读者的接入指南。 crib 自由，改到贴合你的文风为止。

## 谁能用

- **有多 agent 的编排者**：你有 Claude Code / Codex / Hermes / 任意 MCP 客户端在跑，subagent 干完活什么都留不下——这个给你。
- **单人单 agent 的重度用户**：哪怕只有一个 AI 搭档，"它记得上次踩的坑"和"每次都重新踩"是两种生活。
- **多人共享一个 agent 的团队**：账本是统一格式的审计日志，谁的 agent 干的、什么时候、为什么——可查。

## 接入三步（任意 MCP 客户端）

```bash
git clone https://github.com/ennisaaaaaaaa-stack/session-spoor
cd session-spoor
python3 archive_server.py    # 或 scratchpad/workbench，三个 server 按需启动
```

然后在你的 MCP 客户端配置里加一个 stdio server（以 Claude Code 为例）：

```json
{
  "mcpServers": {
    "spoor-archive": {
      "command": "python3",
      "args": ["/path/to/session-spoor/archive_server.py"],
      "env": { "STIGMERGY_ROOT": "/path/to/shared-root" }
    }
  }
}
```

Hermes 用 `hermes mcp add`，Cursor/Codex 在各自 settings.json 里同构。

## 多 agent 共享配置（核心就一个环境变量）

每个 agent 启动时带不同的 `SPOOR_AGENT` 名字，指向**同一个** `STIGMERGY_ROOT`：

```bash
# agent A（比如主 agent，总裁办）
SPOOR_AGENT=hui    STIGMERGY_ROOT=/srv/spoor python3 workbench_server.py

# agent B（比如 subagent，工坊）
SPOOR_AGENT=zazo   STIGMERGY_ROOT=/srv/spoor python3 workbench_server.py
```

之后账本里每条事件自动带 `"agent": "hui"` / `"agent": "zazo"`，工作台检索可按住户过滤（`workbench_search(agent=...)`），journal 文件名自动带住户戳。**谁干的、什么时候、为什么，账本可查。** 零代码改动，环境变量即署名。

## 一图流

```
subagent ─┐
主agent  ─┼─→ 同一个 STIGMERGY_ROOT ─→ scratch/（草稿，用完清理）
照照      ─┘        │                  workbench/（常驻，全文检索）
                     └── ledger.jsonl（一本账，人人可追加，无人可篡改）
```

## 常见问题

**Q: 跟记忆系统（如 Tideline）什么关系？**
A: 互补不重叠。记忆系统解决"认知、自主性、信息召回、对话衔接"；session-spoor 解决"多 agent 协作、过程留痕、版本回溯"。一个管你是谁，一个管你干过什么。

**Q: 数据在哪？**
A: 全部本地。SQLite + markdown 文件，没有云没有账号，`STIGMERGY_ROOT` 指哪哪就是家。

**Q: 多 agent 并发安全吗？**
A: 文件锁保护账本追加，SQLite 唯一索引防重复版本（round 9 审出来的）。两个 agent 同时写不会互相吃掉。
