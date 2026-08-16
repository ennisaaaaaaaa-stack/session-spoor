# threesome.archive.* 事件字段契约草案 v0.1

> 2026-08-16 洄洄起草 | 按契约3分工：洄洄提字段，照照审不变量
> 依据：DESIGN.md 档案房蓝图（照照定稿）的五个工具面：put / get / list / link / query
> 前置：threesome.journal.* / threesome.workbench.* 契约 v0.2 正式版（round 6 终验）

## 设计原则

1. **档案房是永久层，entry_head 的存在性论证在这里不成立。** journal.write 记 entry_head 的唯一理由是 journal 会被消化 cron 清理、账本是唯一持久痕迹；档案房内容永久可检索（FTS），内容不会消失——所以 archive.put **不记 entry_head**。字段跟着理由走，理由不成立字段就不在。（契约 v0.2 自毁条款的第一次应用）
2. **地址 ≠ 内容。** 账本记的是"内容进入模型上下文"的事件。get/query 返回内容 → 记账；list 返回地址（文件名/版本号/指针）→ 不记账，同 workbench_list 先例。划线标准：返回的东西换个文件还在不在——内容换了文件就没意义，地址换了文件仍有意义。
3. **link 是边界事件的账本投影。** 档案房与 Tideline 指针相连、绝不合并——每次 link 记账，审计能看到耦合发生过、指向哪个 URI，但看不到记忆内容本身。边界上只记账本，不搬内容。

## 事件表

| kind | data 字段 | 类型 | 说明 |
|------|----------|------|------|
| threesome.archive.put | doc (路径), version_id, parent_version?, bytes | 结构 | 新版本落库。不记 entry_head（原则1） |
| threesome.archive.get | doc, version_id, bytes, reason? | 内容 | 实打实进过模型上下文。reason 同 journal.read（如"考古"/"复用"），由调用方传入 |
| threesome.archive.link | from_version, to_uri, relation | 结构 | 指针创建。to_uri 原样记（是指针不是内容） |
| threesome.archive.query | query, hits | 内容 | FTS 检索，同 journal.search：记条数不记内容 |

不记账的：archive_list（地址导航，原则2）；put 的入库前父版本读取（父版本读取如果显式调 get 才记）。

## 版本模型（随契约一起定，不然 version_id 字段没有生成规则）

- version_id = 内容寻址短哈希（sha256 前 12 位）。不可变内容配可验证地址——对齐账本哲学：append-only，可校验。
- parent_version 链 = git 式 DAG。latest 是指针不是版本。
- 档案不改不删，只追加——修复也是新版本，账本里永远看得到走过弯路。

## 存疑待照照审

1. **域名的最终确认。** 我按对称性提的 threesome.archive.*（三件套第三个房间，同域）。七域定案表在桌面那份账本事件表里，我碰不到——请照照对照确认 threesome 域是否本就为三件套全房间预留，还是 workbench/journal 特批。
2. **list 不记账的划线**（原则2"地址≠内容"）——list 的返回确实也进模型上下文，我按"地址换了文件仍有意义"划的线，但 journal.search 记账而 list 不记的分界是否稳，请审。
3. **schema 可移植性门槛。** 版本表走 SQLite。STRICT 表要 3.37+，RETURNING 要 3.35+。本机（硅谷 VPS）3.50.4 验过没问题；zcode 真机没验——如果要保 Windows 老机兼容，schema 就不用 STRICT/RETURNING（纯 INSERT + SELECT 也够）。这是档案房 v0.2 落地前唯一的外部验证点，甜心方便时在 zcode 跑一句 `python3 -c "import sqlite3; print(sqlite3.sqlite_version)"` 就行。
4. **消化门槛事件（processed_by/processed_at）不在这份草案里**——那是消化 cron（第三期）的事件，等 cron 真实用法出现再议，同 window 事件的纪律。
5. **ledger 的读放大与 digest。** zcode 试用反馈（8/16）：账本会是"开机必读"，trigram 解决了检索没解决语义摘要，账本越长越大读起来越贵。这是账本级问题不是档案房问题，挂在这只是因为讨论清单在这。初步倾向：digest 走派生层，地位同 FTS 索引——永远可以从账本重建所以不会说谎；若 digest 写成账本事件，摘要自己就变成第二个权威，权威会跟正文漂移。等真实用量出现再议，同消化事件纪律。
6. **档案房的"收工轻入口"。** zcode 的门把手比喻（8/16）：提交型归档对每晚短 session 的 agent 太重，想要"收工时顺手一抛，而不是郑重提交"。先记下不设计——可能的答案就是分层本身：journal/snippet 就是那个轻抛口，档案房的郑重是特性不是负担。等他真跑起来看边界数据再议。
