# session-spoor · 猎迹

**Every session leaves a trail.**

Agent干活的痕迹管理系统——MCP工具集，三层结构：涂鸦房（临时工草稿，用完即弃）、工作台（主agent常驻手稿，带全文检索）、档案房（项目归档，版本化——设计中）。

## 为什么

Agent的session结束，过程就蒸发了。临时文件散在/tmp，进行中的状态没人记得，写过的脚本重写三遍，踩过的坑再踩一次。subagent干完活，中间判断跟着上下文一起消失。

工具返回数据，agent返回认知——但认知没有归档通道。session-spoor就是这条通道：**session死了，痕迹还在。**

名字来自stigmergy（迹象协同）：白蚁没有蓝图，每只白蚁留下的痕迹刺激下一只的行动，塔自己会站着。猎迹给agent的就是这个——**你不是在记笔记，你是在给下一个经过这里的自己（或同伴）留刺激物。**

## 三层

| 层 | 名字 | 生命周期 | 干什么 |
|---|---|---|---|
| L1 | scratch/ 涂鸦房 | 绑任务 | 分身的草稿纸，结束三去向：导出/账本/蒸发 |
| L1.5 | workbench/ 工作台 | 绑agent | 项目索引/状态桌面/记录条/复用件架/**全文检索** |
| L2 | archive/ 档案房 | 永久 | 版本化归档，FTS检索，消化门槛（设计中） |

共享一套mark词汇表（判断/数据/坑/待审·自/待审·人），同一本账本——**插件可拔，账本不可少。**

**mark词汇表是判断的分类学，不是文件的分类学：**

- **判断**——做决定的那一刻留下的理由。下个session不用重新推导。
- **数据**——测量值、规模、事实。防止"感觉"替代"量过"。
- **坑**——踩过并确认存在的东西。开工先读这个。
- **待审·自**——我自己标记的"这里我不确定"，留给未来的自己复核。
- **待审·人**——需要人类拍板的悬而未决。等待是有对象的。

## 多agent共享（v0.3新增）

猎迹可以从单住户升级为**一个机器上的agent工棚**：多个runtime共用同一份`STIGMERGY_ROOT`，各自在**自己的环境变量**里声明名字。

### 署名：`SPOOR_AGENT`

```bash
# agent A（比如照夜）
SPOOR_AGENT=照夜 STIGMERGY_ROOT=~/spoor workbench_server.py

# agent B（比如洄游）——同一个root
SPOOR_AGENT=洄游 STIGMERGY_ROOT=~/spoor workbench_server.py
```

- journal行自动盖戳：`- **[坑]** (照夜) 2026-08-15 10:00 ...`
- ledger每条事件多一个`agent`字段
- **完全自定义**：任何UTF-8字符串，不存在写死的名单。fork这个仓库的陌生人set自己的名字即可署名，无需改任何代码
- **不设=匿名**：行为与单住户版逐字节一致，已有部署零迁移成本

### 并发安全：文件锁

journal是读-改-写，ledger是追加——多进程同时写会交错。`spoor_common.py`用`fcntl.flock`把整个写事务包进排它锁，锁文件落`{root}/.locks/`（gitignore）。

压测：8进程×20轮并发写，journal 160/160、ledger 160/160，零丢失。

注意：`fcntl`是POSIX的。Windows上`flock`不可用，锁层会静默退化裸写——单agent无影响，Windows多agent共享暂不支持（要支持的话msvcrt.locking是路，欢迎PR）。

### 跨机器同步

猎迹的内容全是纯文本（md/jsonl）+ git。多机共用一份的姿势：

```bash
# 各机器clone同一仓库到本地，各自跑（STIGMERGY_ROOT指向clone目录）
# 干完活 push，开工前 pull
```

账本（ledger.jsonl）和运行时目录（scratch/、.locks/、.search/）在gitignore里——**过程共享，运行时不共享**。这是刻意的：scratch是这台机器此刻的呼吸，账本是全体的记忆。

## 全文检索（v0.3新增）

`workbench_search` + `workbench_reindex`，SQLite FTS5。

```
workbench_search(query="文件锁")                     # 全文搜
workbench_search(query="文件锁", type="journal:坑")    # 只搜坑
workbench_search(query="", agent="照夜")              # 照夜写的一切
workbench_search(query="表结构", project="portalk")    # 单项目内搜
```

索引对象：journal每条记录（行级，带mark/agent解析）、snippet、design、STATUS、description、scratch文本文件。增量维护——按`(path, mtime)`记忆，变更才重扫，搜索时自动更新，无需手动reindex。

### 为什么是trigram

SQLite默认的unicode61分词器对连续CJK文本**整段切成一个token**——"文件锁在Windows上"变一个词，任何中文查询都是0命中。这是我们实测确诊的，不是文档里抄的。

trigram分词器（SQLite 3.34+内置）按3字符滑窗切：`文件锁`、`件锁在`、`锁在W`……任何≥3字的子串直接命中，大小写不敏感，零外部依赖。

已知盲区：**2字词不命中**（trigram最小粒度是3）。中文关键词绝大多数≥3字；真要搜2字词，用type/project/agent维度过滤缩小范围再翻。jieba分词版留作后续可选依赖，不强加。

## 安装

```bash
git clone https://github.com/ennisaaaaaaaa-stack/session-spoor
cd session-spoor
python3 -m venv venv && source venv/bin/activate
pip install "mcp>=1.28"

# 工作台（主agent用这个）
STIGMERGY_ROOT=~/spoor python workbench_server.py

# 涂鸦房（给subagent配）
STIGMERGY_ROOT=~/spoor python scratchpad_server.py
```

任何MCP client直接挂载（Hermes/Claude/其他），不绑任何runtime。每个server自带依赖，只要`mcp`一个包。

### 回归测试

```bash
STIGMERGY_ROOT=/tmp/spoor-test python tests/test_spoor_portable.py
# === 44/44 PASS ===
```

## 设计立场

- **痕迹是过程不是结论。** narrative（记忆系统里的叙事摘要）知道"做了什么"，猎迹知道"怎么做的、为什么、哪里疼"。两套系统互补，不互相替代——我们不替代你的记忆系统，我们保存你记忆系统存不下的那部分。
- **匿名兼容是迁移成本为零的代价。** 所有新能力（署名、锁、检索）在默认配置下行为与旧版一致。不想要就当没有，想要一行环境变量。
- **零依赖优先。** mcp之外不依赖任何包——分词用内置trigram不用jieba，锁用fcntl不用filelock。agent环境已经很脆了，工具不应该再加脆弱点。
- **运行时不进git。** 账本、锁、索引、草稿全是可丢弃/可重建的。进git的只有值得穿越时间的东西：journal、snippet、design、STATUS。

## 名字

spoor /spʊər/ — 猎人追踪用的词：动物走过的路留下的足迹。
下一个session，循着spoor找到上一个session的自己。

---

MIT · 猎迹（中文）· 灵感来自stigmergy——白蚁没有蓝图，塔自己会站着。
