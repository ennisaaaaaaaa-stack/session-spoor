# spoor-hooks：把"钩子共性逻辑"从壳里剥出来

> 状态：提案（2026-08-21）。目标读者：任何想让 agent 在"该记一笔"的时刻被
> 提醒的 runtime 维护者。本文是 session-spoor v0.4.2 跨项目 nudge 的母命题：
> 那个 nudge 是这套逻辑在 MCP 工具返回层的一个壳。

## 问题

"什么时候该往记忆/账本里写什么"是所有 agent runtime 的共性问题。
每个 runtime 都有自己的钩子名（Hermes 的 `on_pre_compress`、别家的
`before_context_truncation`……），但底下那张"时机→动作"表是同一张。

现在的状态：每个 runtime 把这张表重新实现一遍。逻辑漂移、修一处漏一处。

## 共性表（一份逻辑）

| # | 时机 | 该发生什么 | 数据源 | 壳需要提供的回调 |
|---|---|---|---|---|
| 1 | 上下文即将被压缩/截断 | 救援将被丢弃的内容（落盘，带出处） | 待压缩消息列表 | `on_pre_compress(messages)` |
| 2 | 会话结束/切换 | 整段对话尾落盘（兜底网） | 完整消息历史 | `on_session_end(messages)` |
| 3 | 记忆工具落笔 | 镜像决策记录（谁/何时/改了什么/旧值） | 工具调用结果+参数 | `on_memory_write(action,target,content,meta)` |
| 4 | 工具返回（高频） | 提醒搭车：该写没写的（journal断流/跨项目触达）提醒 agent，**不代写** | 账本/状态 | 返回值尾部注入 `[nudge]` |
| 5 | 每轮结束 | 双向落库（用户侧+agent侧，带embedding） | 当轮消息 | `sync_turn(user, assistant)` |

**三条设计纪律（跨壳不变的部分）：**

1. **钩子只提醒，裁判是 agent**——钩子无权写内容，只提供通道和提醒。
   判断（写不写、写到哪个项目）永远在 agent（或人）。
2. **搭现有动作的便车，不新建仪式**——提醒骑在 agent 本来就会读的
   返回值尾部，不弹通知不占频道。防免疫：只在超期/触发态出现。
3. **失败静默，绝不弄坏主路径**——提醒层/镜像层异常一律吞掉，
   它没有资格弄坏真实写入和工具返回。

## 壳的职责（每个 runtime 只写这十行）

壳只做一件事：**runtime 的那个时刻到了，喊一声。**

```python
# 伪代码：任意 runtime 的适配层
from spoor_hooks import on_pre_compress_core, on_session_end_core, ...

# runtime 压缩前
def runtime_before_truncation(messages):
    return on_pre_compress_core(messages)   # 返回救援提醒文本（可空）

# runtime 会话结束
def runtime_session_close(messages):
    on_session_end_core(messages)

# runtime 的记忆工具写完
def runtime_after_memory_tool(result, args):
    on_memory_write_core(result, args)
```

## 已有的三个壳（实证）

| 壳 | 位置 | 状态 |
|---|---|---|
| Hermes provider | tideline-memory `tideline_provider.py`（#1/#2/#5）+ `memory_mirror.py`（#3） | 生产运行中 |
| MCP 工具返回层 | session-spoor `spoor_common.py` nudge 管道（#4，v0.4.2 起含跨项目） | 生产运行中 |
| 独立脚本 | `memory_mirror.py` 可脱离 Hermes 单独 import | 测试通过 |

## 下一步

- [ ] 从 tideline_provider + spoor_common 提炼纯逻辑包 `spoor_hooks/`
      （零 Hermes / 零 MCP 依赖，纯函数 + 存储接口）
- [ ] 写两个最小壳作验证：Hermes 壳（现有代码重构）+ 一个非 Hermes 壳（鸣鸣的 Kimi CLI / zcode 的 Windows 环境）
- [ ] 双壳行为一致性测试：同一份输入，两个壳落出同样的行
