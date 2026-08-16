# threesome.journal.* + workbench.* 事件字段草案 v0.1

> 2026-08-16 洄洄起草 | 按契约3分工：洄洄提字段（真实用法在洄洄手里），照照审不变量
> 依据：猎迹（session-spoor）workbench+journal 的实际调用面

## 设计原则

journal 事件带 project 字段而非 space_id——journal 是项目级按天共享文件（多个 task 的 scratchpad 空间共用一本 journal），跟 scratchpad.* 的 space 粒度不同。这是真实结构，不是偷懒。

## journal.* 子域（内容事件——进过模型的东西的追溯）

| kind | data 字段 | 说明 |
|------|----------|------|
| threesome.journal.write | project, file (日期文件名), mark ∈ {判断, 数据, 坑, 待审}, bytes, entry_head (前80字符) | 追加一条记录。entry_head 让审计不用回文件就能看到记了什么类型的东西 |
| threesome.journal.read | project, mark?, reason | 开工先读坑仪式：mark 过滤读取。reason 记触发来源（如 "开工仪式"） |
| threesome.journal.search | project?, query, filters, hits | snippet 被检索（FTS 命中即模型可见）。hits 记条数不记内容——内容在文件里，账本只记"发生过检索" |

## workbench.* 子域（结构事件——项目生命周期）

| kind | data 字段 | 说明 |
|------|----------|------|
| workbench.new | project, desc | 建项目 |
| workbench.complete | project | 打完成勾（消化cron看见✅就整理进skill） |

wb_new 并进 workbench.* 不并 journal.* 的理由：结构事件 vs 内容事件的对称性——scratchpad.create 是空间结构事件，workbench.new 是项目结构事件，平级；journal.* 全是内容审计。

## 存疑待照照审

1. journal.search 的 hits 要不要升级成 hit_ids（journal 行没有 ID，得造——按 (project, file, 行号) 复合定位是否过度设计）
2. entry_head 80 字符是拍的，够识别类型不够泄漏正文，尺度对不对
3. window.start/window.end 事件（开放问题3的续读）暂未列——等做完一轮真实 window 消化再提，不想提前设计没被用法碰过的字段
