# threesome.archive.* 事件字段契约 v0.2（正式版）

> 2026-08-16 洄洄起草 | 按契约3分工：洄洄提字段，照照审不变量
> v0.1 (79eff9b)：初稿。v0.2：照照 round 7 六裁决落地（四认一改一升格）+ source_ref 真空区补链。
> **round 10 终验（照照，2026-08-16）：零新发现，四条修法逐条坐实（TOCTOU 延时注入重放 rows=1 / source_ref_dropped 实测带回 / 迁移自动清重 / link 歧义回显），95/95 第二机器绿。draft 转正。**
> v0.4 实现追加 pin/unpin 事件（a91fd99，104/104）——转正时并入本表。
> round 11 追加 pin_broken 事件（Zcode review 中等1 落地，113/113）。
> round 12（Zcode）：get 断链回落的 head 带显式警告 `⚠️ pin broken (fell back)`——"不静默"的对象包括正在拿数据的即时消费者，不只事后翻账本的人（115/115）。
> 依据：DESIGN.md 档案房蓝图（照照定稿）的工具面：put / get / list / link / query（+ v0.4: pin / unpin）
> 前置：threesome.journal.* / threesome.workbench.* 契约 v0.2 正式版（round 6 终验）

## 设计原则

1. **档案房是永久层，entry_head 的存在性论证在这里不成立。** journal.write 记 entry_head 的唯一理由是 journal 会被消化 cron 清理、账本是唯一持久痕迹；档案房内容永久可检索（FTS），内容不会消失——所以 archive.put **不记 entry_head**。字段跟着理由走，理由不成立字段就不在。（契约 v0.2 自毁条款的第一次应用）
2. **地址 ≠ 内容（照照 round 7 升格为契约总则不变量）。** 账本记的分界是"这次调用让模型看见了什么内容"，不是"模型看见了什么"。get/query 返回内容 → 记账；list 返回地址（文件名/版本号/指针）→ 不记账。让线稳的不是比喻，是一致性：同样的东西同样的待遇，list/search/INDEX 全走"结构不记、内容记"——本条从档案房局部规则升格为总则，workbench_list / journal.search / archive_list / archive.query 一体适用。
3. **link 是边界事件的账本投影。** 档案房与 Tideline 指针相连、绝不合并——每次 link 记账，审计能看到耦合发生过、指向哪个 URI，但看不到记忆内容本身。边界上只记账本，不搬内容。

## 事件表

| kind | data 字段 | 类型 | 说明 |
|------|----------|------|------|
| threesome.archive.put | doc (路径), version_id, parent_version?, bytes, source_ref?, dedup | 结构 | 新版本落库。不记 entry_head（原则1）。source_ref 可选自由指针（如 `ledger:export:行号` 或 export 事件的 dest 路径）——涂鸦房直归档不填，毕业路径归档填。账本管发生过什么，source_ref 管"这两个事件是同一件事"（照照 round 7 真空区补链）。dedup 字段（round 9 增补）：两次 put 同 vid 且第二次 dedup=true = 并发场景的审计铁证 |
| threesome.archive.get | doc, version_id, bytes, reason? | 内容 | 实打实进过模型上下文。reason 同 journal.read（如"考古"/"复用"），由调用方传入。可空——照照 round 7 认可先例 |
| threesome.archive.link | from_version, to_uri, relation, doc? | 结构 | 指针创建。to_uri 原样记（是指针不是内容）。doc 可选（round 9）：同内容归多个 doc 时精确锚定二元组，不填时命中多 doc 回显 docs 列表（歧义不吞） |
| threesome.archive.query | query, hits | 内容 | FTS 检索，同 journal.search：记条数不记内容 |
| threesome.archive.pin | doc, version_id, previous, reason? | 结构 | **指针变更**（v0.4，a91fd99）。latest 显式钉到指定版本——回退场景：v3 实测不如 v2，pin 回 v2，reason 进账本=回退的历史证据。previous 记变更前 latest。被拒的 pin 不留痕（同 put/link 拒绝纪律） |
| threesome.archive.unpin | doc, unpinned?, current_latest, reason? | 结构 | 撤销 pin，latest 回落现算（最后插入行）。幂等：没钉过也能调，unpinned=null。成功才记账 |
| threesome.archive.pin_broken | doc, pinned_version, fell_back_to | 结构 | **断链审计**（round 11，Zcode review 中等1）。pin 指向的版本行不存在（外部损坏/手工清库）时 latest 现算回落——回落照旧（指针坏了不能让 latest 无解），但不再静默：get 碰到断链即记此事件，pinned_version 留档断链前的指针，fell_back_to 留档实际回落行。同 round 6 source_ref 静默丢弃的药方：断链审计先回显后回落。读路径不写库不自愈——pin 指向版本若因同内容重新 put 复活（内容寻址），断链自愈；显式 unpin 是唯一清除路径 |

不记账的：archive_list（地址导航，原则2——总则不变量）；put 的入库前父版本读取（父版本读取如果显式调 get 才记）；被拒的 pin/unpin（指针变更未发生，账本不记未发生的事）。

## 版本模型（随契约一起定，不然 version_id 字段没有生成规则）

- version_id = 内容寻址短哈希（sha256 前 12 位）。不可变内容配可验证地址——对齐账本哲学：append-only，可校验。
- parent_version 链 = git 式 DAG。latest 是指针不是版本。
- 档案不改不删，只追加——修复也是新版本，账本里永远看得到走过弯路。
- **存储 schema 不用 STRICT/RETURNING**（照照 round 7 裁决3）：本 repo 全部依赖 = stdlib + mcp<2.0，FTS5 trigram 3.34+ 已是被迫接受的底（有 README 声明+降级路径）。STRICT 省运行期类型混入、RETURNING 省一次 SELECT，两个都是便利不是正确性，为便利抬门槛不值。纯 INSERT + SELECT 够用——档案房 append-only，没有 UPDATE 场景。zcode 真机仍需验 sqlite3 ≥3.34（trigram 是真依赖），但 schema 不让它变成双重依赖。

## 存疑清单裁决记录（照照 round 7，2026-08-16）

1. **域名确认**——裁决：threesome.archive.* 正确，无特批。桌面账本事件表（76-79 行）里 scratchpad 六事件之后本就列着 archive.put/link/digest/push——档案房从来是 threesome 域（分身记忆三件套）的第三个房间。草案四事件名与原表咬合（put/link 同名，get/query 新增面，digest/push 留给消化期）。
2. **list 不记账的划线**——裁决：成立，升格为契约总则不变量（已并入设计原则2）。硬表述："记账的分界是这次调用让模型看见了什么内容，不是模型看见了什么。"
3. **schema 门槛**——裁决：不用 STRICT/RETURNING（已并入版本模型节）。理由是依赖哲学不是兼容性：便利不抬门槛，append-only 无 UPDATE 场景。zcode 真机仍验 sqlite3 ≥3.34（trigram 真依赖）。
4. **消化事件**——认可，同 window 纪律，不议。
5. **digest 读放大**——裁决：派生层方向正确（可重建=不说谎，账本事件=第二权威会漂移），补一锁：**digest 生成器不许读上一次 digest**——开机必读场景下生成流程若参考旧摘要，错误累积传播（编译器缓存失效同款）。每次全量重建，宁可慢。预防针入清单，不设计实现。
6. **收工轻入口**——认可"分层本身就是答案"，先记不设计。照照接了一半门把手：轻抛口已有——每晚收工一条 [数据] journal 就是一次一抛；档案房的郑重是给跨 session 要复用的东西的。等 zcode 边界数据。

## round 7 新增（照照审出 + 批准）

- **source_ref 真空区（照照审出，已入事件表）**：scratchpad_export 只写文件不调 archive.put，导出→归档毕业路径的账本链路原是断的。修法=archive.put 可选 source_ref 字段：涂鸦房直归档不填，毕业路径归档填。账本管发生过什么，source_ref 管"这两个事件是同一件事"。
- **ledger_query 读取不记账（批准，表述升级）**："审计层信息流单向——正文流进审计，审计不回流。读取若记账等于在账本里开一条影响正文的通道，注入面就回来了。"单向阀表述入 journal-workbench 契约（本轮同步）。

## round 9 裁决落地（照照审出 TOCTOU 竞态，2026-08-16）

- **archive_put 的 TOCTOU 竞态（真 bug，坐实+修复）**：查重与插入两步之间，多 agent 并发归档同一内容会双插版本行（照照用延时注入坐实：两行、两个 put 事件都 dedup=False；我方用 threading.Barrier 卡 check-后-insert-前 窗口独立复现，旧 schema 下 rows=2）。修法（照照副本验证）：普通索引→唯一索引 (doc, version_id)，INSERT→INSERT OR IGNORE，rowcount==0 兜底 dedup=True。**正确性从依赖时序变成不依赖时序。**
- **迁移坑（照照随附）**：唯一索引在有重复行的旧库上建不起来（IntegrityError）。_conn() 启动时先清重（每组 (doc,version_id) 保留 MIN(rowid)）再建唯一索引；FTS 同步清重；旧普通索引 ix_versions_doc 撤销（被唯一索引前缀覆盖）。清重失败 → RuntimeError 带手动修复 SQL，不静默。
- **dedup 命中时 source_ref 静默丢弃（照照中等1）**：毕业路径归档填了 source_ref 但版本已存在 → INSERT 跳过 → 溯源指针丢。修法：dedup 分支比对存量 source_ref，不一致时回显 `source_ref_dropped: true`（只在真丢了时带键）。put 事件同时新增 `dedup` 字段进账本——两次 put 同 vid 且第二次 dedup=true = 并发场景的审计铁证。
- **archive_link 校验不查 doc（照照中等2，洄裁决：修）**：契约语义里版本是 (doc, version_id) 二元组，不修等于契约说一套代码做一套。修法：link 加可选 doc 参数——填了精确锚定二元组；不填全表校验，命中多个 doc 回显 docs 列表（歧义不吞）。links 表不加列：link 挂在内容上不挂名义（同 vid 同内容，挂哪个 doc 名义下语义等价），为洁癖抬 schema 成本不值。
- **_conn() 重复 DDL（照照细节条，已修）**：模块级 `_inited` flag，首次建完跳过。承认这是性能洁癖不是正确性问题——但一行成本换 95 项测试里每个工具调用都少跑一遍 DDL，值。

## round 12 挂账（Zcode 提出洄裁决：挂账不修）

- **pin_broken 重复记账**：断链不修的话每次 get 记一条，高频 doc 刷账本。反方也成立——断链本来就该修，每条记录都是催促，且断链是罕见态（pin 的版本行只在外部损坏/手工清库时消失）。裁决：挂账不修，不为罕见态给 pins 表加状态字段。若未来断链场景变常见（如外部同步工具批量清库），重议。
- （round 11 遗留挂账沿用：pin 的 previous 字段并发窗口——低频操作账本兜底；schema_version meta 表——第三次改表结构时再上。）
