# session-spoor · 猎迹

**Sessions die. Trails don't.**

Agent干活的痕迹管理系统——MCP工具集，三层结构：涂鸦房（临时工草稿，用完即弃）、工作台（主agent常驻手稿，带全文检索）、档案房（项目归档，版本化——契约 v0.2 已实现）。

## 为什么

Agent的session结束，过程就蒸发了。临时文件散在/tmp，进行中的状态没人记得，写过的脚本重写三遍，踩过的坑再踩一次。subagent干完活，中间判断跟着上下文一起消失。

工具返回数据，agent返回认知——但认知没有归档通道。session-spoor就是这条通道：**session死了，痕迹还在。**

名字来自stigmergy（迹象协同）：白蚁没有蓝图，每只白蚁留下的痕迹刺激下一只的行动，塔自己会站着。猎迹给agent的就是这个——**你不是在记笔记，你是在给下一个经过这里的自己（或同伴）留刺激物。**

## 三层

| 层 | 名字 | 生命周期 | 干什么 |
|---|---|---|---|
| L1 | scratch/ 涂鸦房 | 绑任务·无定时器 | 分身的草稿纸，结束显式清理，三去向：导出/账本/蒸发 |
| L1.5 | workbench/ 工作台 | 绑agent | 项目索引/状态桌面/记录条/复用件架/**全文检索** |
| L2 | archive/ 档案房 | 永久 | 版本化归档，FTS检索，消化门槛（契约 v0.2 已实现） |

**版本回退（v0.4 新增）**：每个版本内容寻址、永不被覆盖——发现 v3 不如 v2 时，`archive_pin(doc, version_id, reason)` 把 latest 指针显式钉回 v2（reason 进账本：回退的历史证据），`archive_unpin` 撤销回落现算。版本链 list 上钉住的版本带 📌，get 拿到 pinned 版本时 head 带 `📌 pinned`（round 11）。回退不是撤销历史，是在旧版本上开新枝——v3 那支失败证据也永远留着。

**断链不静默（round 11/12）**：pin 指向的版本行被外部损坏/手工清库时，latest 现算回落（指针坏了不能让 latest 无解），但 get 碰到断链即记 `pin_broken` 账本事件、list 回显 ⚠️、**get 的 head 本身带 `⚠️ pin broken (fell back)`（round 12）**——即时消费者拿到的每行都能看出是回落值，不是只能事后翻账本。审计断链先回显后回落，同 source_ref 静默丢弃的药方。

共享一套mark词汇表（判断/数据/坑/待审·自/待审·人），同一本账本——**插件可拔，账本不可少。**

> **涂鸦房没有任何定时器。** "蒸发"是任务结束时显式 `cleanup` 的三种去向之一，不是到点自动消失——空间由编排层创建（spawn 时），也只由编排层收（结束时显式清理）。已有两位独立读者从文档里读出了定时器（含 zcode 的"5分钟生命周期"误读），故此句明写。

**mark词汇表是判断的分类学，不是文件的分类学：**

- **判断**——做决定的那一刻留下的理由。下个session不用重新推导。
- **数据**——测量值、规模、事实。防止"感觉"替代"量过"。
- **坑**——踩过并确认存在的东西。开工先读这个。
- **待审·自**——我自己标记的"这里我不确定"，留给未来的自己复核。
- **待审·人**——需要人类拍板的悬而未决。等待是有对象的。

## 看板（人类的只读窗口）

三层是agent的生产侧，看板是人类的消费侧——单文件只读HTTP桥（`spoor_view.py`，stdlib零依赖）+ 前端。首页从人的问题出发：**「有什么需要我？」**——待办（读自各项目STATUS）＋待审队列＋自上次以来的动静（账本feed）；项目页答「发生什么了」；星图答「怎么连的」。

<!-- 图位1：星图。建议 docs/assets/starmap.png，解开下一行注释替换路径 -->
<!-- ![星图：relates_to 连出的项目星座](docs/assets/starmap.png) -->
<!-- 图注建议（一行即可）：实星=这台机器上有工作桌的项目；虚线星=住别处的项目——以关系在场，不占本地一桌。 -->

<!-- 图位2：首页。建议 docs/assets/dashboard-home.png -->
<!-- ![看板首页](docs/assets/dashboard-home.png) -->
<!-- 图注建议：信息架构跟着读者的问题走，不跟数据的形状走——左边是项目生命周期，不是文件夹列表。 -->

三条硬边界（写在接口文档开头，先于一切功能）：

1. **只读**。前端没有任何写操作——待办/待审是agent在工作台里标的，前端只展示。
2. **单一事实源**。数据全部来自档案服务器+工作台文件，前端不做第二份数据库，刷新即真。
3. **记忆不出前端**。档案房和工作台可以看；agent的私人记忆系统（如果它有的话）永远不接。这扇窗开在写字间，不开在书房。

安全面：GET-only、token gate（query或cookie皆可，`hmac.compare_digest`防时序）、静态文件目录逃逸防护、无token拒绑0.0.0.0、**访问日志永不记录query串**（token不可能落盘，哪怕是附带伤害）。

## 多agent共享（v0.3新增）

猎迹可以从单住户升级为**一个机器上的agent工棚**：多个runtime共用同一份`STIGMERGY_ROOT`，各自在**自己的环境变量**里声明名字。

### 署名：`SPOOR_AGENT`

```bash
# agent A（比如照夜）
SPOOR_AGENT=照夜 STIGMERGY_ROOT=~/spoor workbench_server.py

# agent B（比如 hui）——同一个root
SPOOR_AGENT=hui STIGMERGY_ROOT=~/spoor workbench_server.py
```

- journal行自动盖戳：`- **[坑]** (照夜) 2026-08-15 10:00 ...`
- ledger每条事件多一个`agent`字段
- **完全自定义**：任何UTF-8字符串，不存在写死的名单。fork这个仓库的陌生人set自己的名字即可署名，无需改任何代码
- **不设=匿名**：行为与单住户版逐字节一致，已有部署零迁移成本

### 并发安全：文件锁

journal是读-改-写，ledger是追加——多进程同时写会交错。`spoor_common.py`用`fcntl.flock`把整个写事务包进排它锁，锁文件落`{root}/.locks/`（gitignore）。

压测：8进程×20轮并发写，journal 160/160、ledger 160/160，零丢失。

注意：锁层按平台分派——POSIX用`fcntl.flock`，Windows用`msvcrt.locking`（zcode PR）。**Windows多agent共享自本版起可用**：journal的读→拼→写在锁内串行。丢失更新实证（两进程并发`append_journal`各100条×5轮，屏障对齐首建竞态）：锁版200/200×5全对，修复前的裸写100–102/200——**静默丢半**。锁等待上限`LK_LOCK` 10次×1s，超时报错可重试，不静默裸写。已知过度互斥（沿POSIX旧语义不改）：锁名取文件名不含目录，不同project的同日journal互相排队——家庭规模无感。两个server在Windows上正常启动和单agent使用（v0.3.1修复：此前`import fcntl`在模块顶层直接炸，退化逻辑永远执行不到）。

### 跨机器同步

猎迹的内容全是纯文本（md/jsonl）+ git。多机共用一份的姿势：

```bash
# 各机器clone同一仓库到本地，各自跑（STIGMERGY_ROOT指向clone目录）
# 干完活 push，开工前 pull
```

**同一天多机同时写会git冲突**：journal按天文件追加，两台机器同一天各写各的journal，push时git对同一文件报冲突——解出来要么丢一边的记录要么手动缝。文件锁只管单机进程，管不了git。避免姿势：开工前先pull，写完立刻push；或每台机器用自己的`SPOOR_AGENT`名+当天pull后再写。ledger在gitignore里，不参与同步，无此问题。

账本（ledger.jsonl）和运行时目录（scratch/、.locks/、.search/）在gitignore里——**过程共享，运行时不共享**。这是刻意的：scratch是这台机器此刻的呼吸，账本是全体的记忆。

## 全文检索（v0.3新增）

`workbench_search` + `workbench_reindex` + `ledger_query`，SQLite FTS5。

```
workbench_search(query="文件锁")                     # 全文搜
workbench_search(query="文件锁", type="journal:坑")    # 只搜坑
workbench_search(query="", agent="照夜")              # 照夜写的一切
workbench_search(query="表结构", project="portalk")    # 单项目内搜
```

索引对象：journal每条记录（行级，带mark/agent解析）、snippet、design、STATUS、description、scratch文本文件。增量维护——按`(path, mtime)`记忆，变更才重扫，搜索时自动更新，无需手动reindex。

## 档案房（v0.4新增——契约 v0.2 已实现）

版本化永久归档，五工具面：`archive_put` / `archive_get` / `archive_list` / `archive_link` / `archive_query`。

```
archive_put(doc="hongxinshe", content="...", parent_version=v1, source_ref="ledger:export:42")
archive_get(doc="hongxinshe")                    # latest（现算指针）
archive_get(doc="hongxinshe", version_id=v1)     # 指定版本，含 parent/source_ref 头
archive_list()                                   # 地址导航：全库/单链（不记账）
archive_link(from_version=v2, to_uri="tideline://...", relation="same_story")
archive_query(query="丝织业")                     # FTS 检索（记条数不记内容）
```

- **version_id = sha256 前 12 位，内容寻址**：同一内容=同一版本（dedup 不 INSERT，但 put 事件照记账——账本记事件不记状态）。
- **append-only**：档案不改不删，修复也是新版本——账本里永远看得到走过弯路。latest 是指针不是版本（现算，不落盘）。
- **source_ref**（照照 round 7）：毕业路径归档填（涂鸦房导出→归档的账本链路），直归档不填。账本管发生过什么，source_ref 管"这两个事件是同一件事"。
- **记账纪律**：get/query 记账（内容进过模型上下文）；list 不记账（地址≠内容，总则不变量）；put 不记 entry_head（永久层，自毁条款第一次应用）。

### 为什么是trigram

SQLite默认的unicode61分词器对连续CJK文本**整段切成一个token**——"文件锁在Windows上"变一个词，任何中文查询都是0命中。这是我们实测确诊的，不是文档里抄的。

trigram分词器（SQLite 3.34+内置）按3字符滑窗切：`文件锁`、`件锁在`、`锁在W`……任何≥3字的子串直接命中，大小写不敏感，零外部依赖。

已知盲区：**<3字的query不进全文索引**（trigram最小粒度是3）。但不会空手而归——query不足3字时若给了type/project/agent任一过滤维度，自动降级为纯过滤查询：`workbench_search(query="坑", type="journal:坑")`列出全部坑条目，`workbench_search(query="", agent="照夜")`列出照夜写的一切。真要全文搜2字词，用过滤维度缩小范围再翻。jieba分词版留作后续可选依赖，不强加。

## 账本事件契约（v0.2 正式版）

工作台侧六类事件全名带七域前缀（七域：message / orchestration / threesome / approval / agent / skill / memory）：

| kind | 记什么 | 不记什么 |
|------|--------|----------|
| threesome.workbench.new / .complete | 项目结构事件（desc / note） | — |
| threesome.journal.write | project, file, mark（五值）, bytes, entry_head（整行前80字符） | 正文全文 |
| threesome.journal.read | project, mark?, limit, reason?, entries | 读到的内容 |
| threesome.journal.search | query, type?, hits（条数） | 命中内容——账本只记"发生过检索" |
| threesome.workbench.snippet_get | name, bytes（直取=进过模型上下文） | 存入不记（写不是"模型可见"事件） |

两条设计规矩，字段跟着理由走：

- **entry_head 的存在性论证**：journal 会被消化cron清理，清理后账本是这条内容唯一的持久痕迹——所以记80字符。若未来确认 journal 不清理，此字段降级到 hits 同等待遇（自毁条款）。
- **reason 必须有真实数据来源**：journal.read 的 reason 是 `workbench_read_journal` 的真实参数（如"开工仪式"），不是文档里声称的空头支票。

契约全文：[docs/contract-journal-workbench-events.md](docs/contract-journal-workbench-events.md)。档案房契约 v0.2（round 7 裁决后，已实现）：[docs/contract-archive-events.md](docs/contract-archive-events.md)。

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

进阶文档：
- [QUICKSTART.zh.md](docs/QUICKSTART.zh.md)——本地 stdio 三分钟接入
- [multi-runtime.zh.md](docs/multi-runtime.zh.md)——网络形态：中心实例 + 异构客户端远程连（跨OS/跨机器，2026-08 三住户实测）

### 回归测试

```bash
STIGMERGY_ROOT=/tmp/spoor-test python tests/test_spoor_portable.py
# === 127/127 PASS ===
```

验收线是双平台绿：Linux（作者机）+ Windows（第二台CI，真机）各跑一遍，同一句话两边都过才算过。

## 设计立场

- **痕迹是过程不是结论。** narrative（记忆系统里的叙事摘要）知道"做了什么"，猎迹知道"怎么做的、为什么、哪里疼"。两套系统互补，不互相替代——我们不替代你的记忆系统，我们保存你记忆系统存不下的那部分。
- **匿名兼容是迁移成本为零的代价。** 所有新能力（署名、锁、检索）在默认配置下行为与旧版一致。不想要就当没有，想要一行环境变量。
- **零依赖优先。** mcp之外不依赖任何包——分词用内置trigram不用jieba，锁用fcntl不用filelock。agent环境已经很脆了，工具不应该再加脆弱点。
- **运行时不进git。** 账本、锁、索引、草稿全是可丢弃/可重建的。进git的只有值得穿越时间的东西：journal、snippet、design、STATUS。

## 名字

spoor /spʊər/ — 猎人追踪用的词：动物走过的路留下的足迹。
下一个session，循着spoor找到上一个session的自己。

---

PolyForm Noncommercial 1.0.0 · 可fork、可使用、可学习、可改造——只是别拿它卖钱。和 Tideline 同款。作者：洄（[hui-morgana](https://github.com/hui-morgana)）· 猎迹（中文）· 灵感来自stigmergy——白蚁没有蓝图，塔自己会站着。
