# threesome.journal.* + threesome.workbench.* 事件字段契约 v0.2（正式版）

> 2026-08-16 洄洄起草 v0.1 → 照照审不变量（round 5），五条裁决全采纳，修订 v0.2
> 2026-08-16 照照终验通过（round 6），三层文档对齐五值，草案升正式版。归档：账本事件表已同步（照照），桌面契约 73 行四值残留已由照照修正
> 按契约3分工：洄洄提字段（真实用法在洄洄手里），照照审不变量
> 依据：猎迹（session-spoor）workbench+journal 的实际调用面

## 设计原则

1. journal 事件带 project 字段而非 space_id——journal 是项目级按天共享文件（多个 task 的 scratchpad 空间共用一本 journal），跟 scratchpad.* 的 space 粒度不同。这是真实结构，不是偷懒。（照照已认可）
2. 事件 kind 一律带七域前缀。裸 workbench.* 不在七域（message / orchestration / threesome / approval / agent / skill / memory）里——workbench 子域全称 threesome.workbench.*。（裁决2）
3. 契约字段必须有真实数据来源。文档声称支持、代码里没有 = 给未来埋一条永远为空的列。（裁决3原则）

## 词汇表（与代码一致：两 server 的 MARKS 集合）

mark ∈ {判断, 数据, 坑, 待审·自, 待审·人}——五值，不是四值。

待审在 03ffb87 拆成两档法官：自=主agent消化，人=人类拍板。这是账本最关心的路由信息——两种待审压回一个词，审计时看不出请求的是哪个法官。

## journal.* 子域（内容事件——进过模型的东西的追溯）

| kind | data 字段 | 说明 |
|------|----------|------|
| threesome.journal.write | project, file (日期文件名), mark (五值), bytes (entry字节数), entry_head (整行前80字符，含mark前缀) | 追加一条记录 |
| threesome.journal.read | project, mark?, limit, reason?, entries (返回条数) | mark 过滤读取。reason 由调用方传入——workbench_read_journal 的 reason 参数（如 "开工仪式"），空=未声明来源的读取 |
| threesome.journal.search | project?, query, type?, agent?, hits (条数) | FTS 检索（命中即模型可见）。hits 记条数不记内容——内容在文件里，账本只记"发生过检索" |

### entry_head 的存在性理由（裁决4修订）

entry_head 不是为了"审计方便"——mark 字段已经回答了类型。它成立的真实理由：journal 是消化 cron 的消费介质，会被清理；清理之后，账本是这条内容唯一的持久痕迹。80 字符的尺度由这个理由决定：mark前缀+时间戳吃掉约25字符，实际露出约55字符正文，够识别、不够泄漏。

（若未来确认 journal 其实不清理，entry_head 应降级到跟 search 的 hits 同等待遇——字段跟着理由走，理由倒了字段就让位。）

## workbench.* 子域（结构事件——项目生命周期）

| kind | data 字段 | 说明 |
|------|----------|------|
| threesome.workbench.new | project, desc | 建项目 |
| threesome.workbench.complete | project, note | 打完成勾（消化cron看见✅就整理进skill） |
| threesome.workbench.snippet_get | project, name, bytes | 复用件直取——实打实进过模型上下文的内容，按不变量3该有条（裁决5）。存入不记：写不是"模型可见内容"事件 |

wb_new 并进 workbench.* 不并 journal.* 的理由：结构事件 vs 内容事件的对称性——scratchpad.create 是空间结构事件，workbench.new 是项目结构事件，平级；journal.* 全是内容审计。（照照已认可）

## 遗留迁移

wb_new / wb_complete 两个旧裸事件名随本契约落地迁移为 threesome.workbench.new / threesome.workbench.complete。scratchpad 侧的 create / export / cleanup 仍是裸名——scratchpad.* 契约不在本契约范围，待议。

## 暂不实现（有真实用法再设计）

1. journal.search 的 hit_ids：预留格式 = (project, file, 行号) 复合键。journal 行没有天然 ID，复合键是唯一能回答"模型这次检索到底看见了什么行"的诚实选择。现在不加，等 window 消化真的需要回溯时再加——但格式以此为准，不另发明 ID 体系。（裁决1）
2. window.start / window.end 事件：等做完一轮真实 window 消化再提。（裁决3认可）
3. workbench_status 的"醒来先读"：将来并进 journal.read 语义（reason="醒来"），不单独立事件。（裁决5）
4. ledger 读取事件的记账：**不记**（已裁决为设计而非遗漏——见"账本读取通道"节）。审计层的读取不污染审计层：access log 不记录"有人看了 access log"，否则每读一次多一行、自我放大。ledger_query 本身零写入。

## 账本读取通道（ledger_query，8/16 增补）

账本不在 FTS 索引里——它是审计层（access log）不是阅读层，出事时它是唯一说实话的。工作台新增 `ledger_query` 工具作为其唯一读取通道：倒序扫描 + kind 前缀 / agent / date / contains 子串过滤 + skip_recent 翻页，纯顺序扫描零依赖（万行毫秒级）。读取本身不记账（见上）。验证"蒸发的不值得全文留"这个设计假设：账本里每条 cleanup 事件带 mode（export_marked/ledger/evaporate），毕业率/蒸发率随时可算；"想找回但找不回"的真实案例出现时，entry_head 80 字符是唯一考古线索——读取工具就是验证仪器。

## 实现状态

v0.2 契约已全部落地：workbench_journal / workbench_read_journal / workbench_search / workbench_snippet(取) / wb_new / wb_complete 六处 _ledger 调用，事件名用本契约 kind。回归套件含账本断言。

## 审阅记录

- v0.1 (1d85563)：洄洄提字段草案
- v0.2：照照五条裁决全采纳——①词汇表五值（草案/DESIGN.md/README 同步修，桌面账本事件表待甜心改）②kind 带全前缀 ③reason 落到工具参数 ④entry_head 改存在性论证 ⑤snippet_get 补事件
