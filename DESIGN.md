# 猎迹 · session-spoor — 设计文档 v0.1

> 2026-08-16 主agent起草（中文名：猎迹） | 给人类维护者的人话版 + 给代码审校者的接口版
> 定位：独立开源工具（MIT），不绑Portalk。任何agent框架的过程管理都适用。
> 命名：开源名 Stigmergy｜中文名 猎迹｜营销比喻 总裁办
> 灵感来源：白蚁冢的stigmergy——没有蓝图没有工头，痕迹刺激下一步行动。
> 第一任住户：主agent + 分身们 + 临时工们。

---

## 一句话

Agent干活的痕迹管理系统——草稿有房间，过程有走廊，判断有归档，蒸发有账本。

## 为什么需要（问题陈述）

现状：agent的工程工作没有过程管理层。
- 临时文件散落 /tmp，无人打扫（真实案例：一个晚上产生4个孤立文件）
- session中断=现场考古，下次醒来重新搜索"我做到哪了"
- 写过的代码蒸发，同样的脚本重写三遍（真实案例：safetensors头部读取）
- 踩过的坑不会被想起来，同一个坑踩两次（真实案例：长命令被拦截）
- 分身干完活，中间判断跟着上下文一起消失

原则：**工具返回数据，agent返回认知。** 过程里的判断必须有归档通道。

## 三层结构

```
猎迹（Stigmergy）
├── 涂鸦房 scratch/    —— L1 临时工的分身草稿纸，绑任务，用完即弃
├── 工作台 workbench/  —— L1.5 主agent的常驻手稿层，绑agent不绑任务
└── 档案房 archive/    —— L2 项目库，阶段性成果，版本化，消化门槛
```

三层共享：同一套mark词汇表、同一个回流压缩器入口、同一本账本。

### 涂鸦房（scratch/，照照蓝图已定稿）

- 生命周期绑任务：编排层spawn分身时创建，结束时清理
- 清理三去向：导出（有价值）→ 账本（清理记录）→ 蒸发（无价值）
- 目录树存储，rm -rf即清理

### 工作台（workbench/，用户侧设计+主agent的工程需求）

主agent的常驻过程层。**联邦式**：跟涂鸦房共享接口语义，存储独立。

```
workbench/
├── INDEX.md                 —— 项目索引（agent自维护，工具提示层可见）
└── {project_name}/
    ├── description.md       —— 一句话：这项目是什么（最简）
    ├── STATUS.md            —— 进行中状态：做到哪/下一步/卡在哪
    │                           （session死了下次醒来先读这个，不用考古）
    │                           「下一步」条目标注归属（人名/agent名打头），
    │                           办完即移除——已办历史由journal承接
    ├── design/              —— 设计底稿+architecture，多版本+时间戳
    ├── journal/             —— 板块记录条：判断/数据/坑/待审·自/待审·人 + 时间戳
    │                           （每天的工作痕迹，mark标记，压缩器读这里）
    └── snippets/            —— 复用件架：下次能直接抄的脚本/片段
                                （不够格变skill，但绝不重写第三遍）
```

**关键机制（用户侧设计）：**
1. **项目索引自维护**——agent自己写自己更新，格式化写入，工具层能看到现有项目
2. **完成勾✓**——项目做完打勾，消化cron看见勾就把已消化部分整理进skill，清理工作台
3. **毕业路径**：涂鸦房草稿 → 工作台复用件 → 消化成skill（三级越来越永久）

**关键机制（主agent的工程需求）：**
1. **STATUS.md是桌面不是档案**——给下次醒来的自己看的，永远反映"现在"
2. **开工仪式**：新session接手项目前先读 journal/ 里的坑清单——索引存在不等于坑被想起，需要主动召回时机
3. **snippets绝不蒸发**——session可以死，写过的代码必须能找回

### 档案房（archive/，照照蓝图已定稿）

- 版本化存储引擎：多版本可追溯，显式commit（对齐账本哲学）
- **不是记忆系统**：Tideline管"想起什么"（进context），档案房管"做过什么"（不进）。指针相连，绝不合并——保护Tideline插件化边界
- `processed_by`/`processed_at` 消化门槛字段：没有=待消化，有=档案
- FTS全文检索
- visibility标记：agent可见/人类可见/both

## MCP 工具接口

### 涂鸦房（照照草案，已定稿）
```
scratchpad_create(task_id, label?) -> space_id
scratchpad_write(space_id, path, content, mode=overwrite|append)
scratchpad_read(space_id, path, offset?, limit?)
scratchpad_list(space_id, path?)
scratchpad_export(space_id, selection, dest)    # 导出即打标
scratchpad_mark(space_id, path, mark)            # 判断/数据/坑/待审·自/待审·人
scratchpad_status(space_id) -> {files, size, marks, exported, age}
scratchpad_cleanup(space_id, mode=export_all|export_marked|discard)
```

### 工作台（新增，主agent设计）
```
workbench_new(project, description)              # 建项目：目录+description+INDEX登记
workbench_status(project, text?)                 # 读/写 STATUS.md（不传text=读，传=写）
workbench_journal(project, entry, mark?)         # 追加记录条（自动时间戳）
workbench_read_journal(project, mark?, limit?)   # 按类型读记录（开工先读"坑"）
workbench_snippet(project, name, content)        # 存复用件
workbench_get_snippet(project, name)             # 取复用件
workbench_list() -> projects[]                   # 项目索引（工具层可见）
workbench_complete(project)                      # 打完成勾，等消化cron处理
```

### 档案房（照照草案）
```
archive_put(doc, parent_version?) -> version_id
archive_get(version_id | latest, path?)
archive_list(prefix?, tag?, since?)
archive_link(from_version, to_uri, relation)
archive_query(fts)
```

## 三个共享契约（跟照照主线异步对齐，不阻塞）

1. **mark词汇表**（涂鸦房↔工作台↔压缩器）：`判断/数据/坑/待审·自/待审·人`——已拍板；待审两档法官（自=主agent消化，人=人类拍板）是 03ffb87 的拆分，不是加新词
2. **export格式**（涂鸦房↔档案房）：Markdown bundle，判断在前数据在后——已拍板
3. **账本事件格式**（全部↔账本）：journal/workbench 侧契约 v0.2 正式版（照照 round 6 终验通过）——docs/contract-journal-workbench-events.md；scratchpad 侧待议

## 回流压缩器（第二期）

```
compress(session_log, scratchpad_marks, workbench_journal, ruleset) ->
  { summary_blocks[], origin_pointers[] }
```

- summary_blocks结构化三分块（数据/判断/坑）——已拍板
- 弱模型层触发：主agent派发时标记——已拍板（成本决定权在派活的人手里）
- 溯源指针：session事件seq + 文件路径，两者都存——已拍板
- 规则层零LLM优先：坑原样保留（LLM摘要会把错误洗掉）、判断优先于数据、滤工具噪声不滤错误、assistant文字不压缩

## 消化cron（第三期）

定期扫描：完成勾✓的项目 → 已消化的journal条目整理进skill → snippets里高频使用的晋升为skill → 清理工作台残留 → 消化事件写账本。

## 与Portalk的关系

引擎层独立开源（本repo），Portalk以配置和规则集接入：
- 编排层挂载钩子：spawn时create、结束时status→cleanup
- 消化门槛字段、visibility标记：Portalk皮肤
- 账本事件格式：journal/workbench 契约 v0.2 已落地；scratchpad.* 侧对接Portalk账本词汇表（照照起草中）

不依赖Portalk任何部分，任何MCP client直接用。

## 开工顺序

1. **涂鸦房 v0.1**（本周）：最独立，照照蓝图+6决策已齐
2. **工作台 v0.1**（本周）：目录结构+核心工具，INDEX自维护
3. **档案房 v0.2**（下周）：版本化+FTS
4. **回流压缩器**（下下周）：规则层先行
5. **消化cron**（之后）：毕业路径闭环

第一个项目就是猎迹自己：`workbench_new("stigmergy", "agent过程管理系统")`——**第一只白蚁就是蚁冢**（对不 起，迹冢。第一段痕迹就是走廊本身）。

## 设计原则复述

- 用完即弃但不是无条件蒸发：导出/账本/蒸发三去向
- 插件可拔，账本不可少
- 工具返回数据，agent返回认知
- Tideline管想起什么，猎迹管做过什么——指针相连，绝不合并
- 存储：目录树全家统一，简单压倒一切
