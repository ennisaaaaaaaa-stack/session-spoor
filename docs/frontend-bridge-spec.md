# 档案房前端 · 接口说明（给鸣鸣）

> 写于 2026-08-17，洄。前端定位：**人类的只读窗口**——看见项目、进度、关系、坑；不看见 agent 的记忆（那是另一个系统，永远不出前端）。

## 一、硬边界（先读这个）

1. **只读**。前端没有任何写操作。待办/待审不是在前端里改，是agent在工作台里标，前端只负责展示。
2. **单一事实源**。数据全部来自档案服务器+工作台文件，前端不做第二份数据库、不做缓存副本当真相。刷新即真。
3. **记忆不出前端**。档案房(archive)和工作台(workbench)可以看；portalk记忆MCP（洄的私人记忆）永远不接。比喻：这扇窗开在写字间，不开在书房。

## 二、数据源（三层）

### 1. 档案服务器（结论层，版本化）

MCP stdio server：`~/Stigmergy/archive_server.py`（启动：`~/Stigmergy/venv/bin/python`，env `STIGMERGY_ROOT=/home/ubuntu/Stigmergy`）。
前端**不要**直接跑MCP——洄提供一个只读HTTP桥（见第四节），桥内部调这些工具：

- `archive_list()` → 所有doc：`{doc名, latest version_id, 版本数, 最后时间}`
- `archive_get(doc, reason)` → 返回纯文本：第一行header `(archive {doc} @ {vid} · {N}B · parent {p} · {ts} · source_ref {ref})`，空行后是markdown正文。**header里source_ref自带括号，解析按首空行切，别按第一个右括号切**（已踩过的坑）。
- doc生命周期标签（约定写在doc名或正文头部）：`毕业档案 / 里程碑 / 目录册 / 胚胎`

### 2. 账本 ledger.jsonl（变更流）

`~/Stigmergy/ledger.jsonl`，append-only JSONL，每行一个事件（put/link/get/query，带version_id和时间戳）。
**这是首页"自上次以来发生了什么"feed的原料**——天然的时间线，不用发明。

### 3. 工作台 workbench/（过程层，活的）

`~/Stigmergy/workbench/{project}/`，每个项目一张桌：

- `STATUS.md` — 当前状态（人类待办从这里读）
- `journal/日期.md` — 工作记录，**带mark词表**：`判断 / 数据 / 坑 / 待审·自 / 待审·人`
- `INDEX.md` — 项目索引

mark词表直接支撑前端两个核心队列：
- **待审·人** = 等甜心看的东西（首页"待审"栏的唯一直接来源）
- **坑** = agent踩过的坑和注意事项（深层视图）

## 三、视图层级（甜心钦定四层，前端IA以此为准）

1. **首页 = 人问的问题**：「有什么需要我？」→ 待办(STATUS) + 待审(待审·人) + 自上次访问的变更(ledger feed)
2. **项目页**：「发生什么了？」→ 每项目：架构概述+当前进度(STATUS) + 该项目的档案doc(点开看全文)
3. **关系网**：「怎么连的？」→ archive_link的边就是图。节点=doc，边=relation。五份档案时先做平铺+筛选，不用急着上图可视化
4. **深层 = agent的话**：「你们学到了什么？」→ journal里mark=坑的条目聚合，按项目分组

设计原则（回应"进去感觉自己是AI"的病根）：信息架构跟着**读者的问题**走，不跟数据的形状走。agent视角的前端从"有哪些doc"开始；人视角的前端从"有什么需要我"开始。

## 四、只读HTTP桥（洄晚上搭，鸣鸣只管fetch）

计划形态（今晚定稿）：

```
GET /api/overview      → 首页数据包：待办+待审队列+最近ledger事件
GET /api/projects      → 项目列表：workbench桌 + 档案doc join
GET /api/project/{name} → 单项目：STATUS全文+journal(mark分组)+关联doc
GET /api/archive       → doc列表(生命周期标签)
GET /api/archive/{doc} → doc正文(带version_id和parent链)
GET /api/graph         → 节点+边（archive_link）
```

全部JSON，零鉴权（只绑127.0.0.1），零写操作。鸣鸣的审美自由度在视觉层随便发挥，数据形状锁死。

## 五、给前端的三个提醒

1. 字节数对不上别慌：档案正文按UTF-8字节算，JSON里是字符串，长度按字符数会差一截。
2. `待审·人`队列的质量取决于agent标记得勤不勤——这是纪律问题不是技术问题，洄这边保证习惯。
3. 胚胎期项目的doc会很少甚至一行——空态设计要做，"还没长出来"也是一种真实状态。
