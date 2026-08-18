# 桥-前端契约 v0.1

> spoor_view.py（数据面）与 dashboard（渲染面）之间唯一的形状约定。
> 改契约 = 改这份文档 + 桥实现 + 前端实现，三者同步，review 走 main。
> 三条硬边界（继承自桥）：只读 · 单一事实源 · 记忆不出前端。

## 语义规则（404 ≠ 空）

| 状态码 | 含义 | 前端行为 |
|---|---|---|
| 404 | 这个口不存在 | 隐藏整个区块 |
| 200 + 空结构 | 口存在，暂无数据 | 显示空态文案（区块保留） |
| 403 | 没 token | 人类去带 token 的 URL 进门 |

动机：此前 /api/scratch、/api/heatmap 不存在时前端整区消失——「桥没开口」和「开了口但空着」在前端是同一个反应。拆开后涂鸦房/热力图在 VPS 上以空态出现而非消失。

## 项目元数据（新，v0.1 核心）

来源：每个项目桌 `workbench/<project>/STATUS.md` 的 YAML frontmatter。写入时顺手维护，桥聚合后随 /api/projects 下发，前端只渲染不猜测。

```yaml
---
lifecycle: 生长      # 毕业 | 里程碑 | 生长 | 胚胎
ecosystem: portalk   # portalk | 声海 | 猎迹 | varia | ""
relates_to:          # 横向关系（可选，边）
  - project: tideline
    relation: 记忆层底座
---
# STATUS
（正文照旧）
```

frontmatter 缺失或字段缺失 → 默认 `lifecycle: 生长`、`ecosystem: ""`、`relates_to: []`（向后兼容）。

**语义（甜心 2026-08-18 定）**：毕业=已归档；胚胎=未完成；生长=进行中；里程碑=已完成的部分。生态归属：Portalk生态={portalk, tideline, session spoor}；声海={ocean-listen, music box}。

**已裁决（甜心 2026-08-18）——主状态模型**：lifecycle 是项目的**主状态**（在跑=生长，收档=毕业），已完成的部分不抢格子，以 `milestones` 成就列表挂在项目页。例：Portalk 主线在跑 → lifecycle=生长；v0.x 交付、前端上线 → milestones 列表。里程碑是项目的成就，不是项目的状态。

**初稿值**（= 鸣鸣 lifecycle.json 现值 + 两个新增，均可改）：

| project | lifecycle | ecosystem | milestones |
|---|---|---|---|
| portalk | 生长 | portalk | v0.x 交付；档案房前端上线（2026-08-18） |
| session-spoor-review | 里程碑 | portalk | — |
| ocean-listen | 生长 | 声海 | — |
| spoor-dashboard | 生长 | 猎迹 | — |
| memory-wash | 生长 | 猎迹 | 档案房 9 doc 收官（2026-08-18） |
| tideline | 生长 | portalk | v2.4 闭环（2026-08-09）；Kimi3 入驻成为底座（2026-08-12） |
| music-box | 生长 | 声海 | — |

注：dashboard/lifecycle.json 退役——数据进 frontmatter 后前端不再单独 fetch 它（过渡期桥继续 serve，前端切换完成后删）。

## API 形状（冻结现状，v0.1 只加不改）

- `GET /api/overview` → `{server_time, projects[], todos[], pending_review[], recent_events[], archive_docs, push_queue[], needs_attention[]}`
- `GET /api/projects` → `[{project, description, status{updated, priority, sections{}}, git{}, todo, blocked, pending_review[], pits[], journal[], journal_total}]` + **v0.1 新增每项 `lifecycle, ecosystem, relates_to[]`（来自 frontmatter）**
- `GET /api/project/<name>` → 同上单项目
- `GET /api/graph` → `{nodes[{doc, version_id, parent, bytes, source_ref}], edges[]}`
- `GET /api/archive` → `{docs[]}`
- `GET /api/archive/<doc>` → `{content, ...}`
- `GET /api/scratch` → **待开口**。桥有数据源时返回 `{spaces[{space, files[{name, bytes, mtime}]}], events[]}`；开口前 404（前端隐藏）
- `GET /api/heatmap` → **待开口**。从 ledger ts 按天聚合 → `{days{"2026-08-18": 12, ...}}`；开口前 404

事件类型（overview.recent_events[].event）：`threesome.archive.put/link/get/query/verify`（ledger 事件）。前端 EV_ACTION 表里 journal.write/create/cleanup 等为本地桥方言，不在本契约内——生产前端遇到未知名按原样显示或「留下了痕迹」。

## 项目间关系怎么到前端（映射草案）

1. **生态归属（树）** → 侧边栏顶层分组：portalk生态 / 声海 / 猎迹 / 未归属
2. **生命周期（状态）** → 组内徽章或二级分组
3. **relates_to（横向边）** → 星图加项目节点（生态=分区色）+ 项目页「关联项目」区
4. **项目页 × 档案房 join** → /api/project/<name> 附带 `archive_docs[]`（按 doc 名前缀或 source_ref 匹配），ocean-listen 页不再空

## 变更流程

1. 改 CONTRACT.md（本文件）+ 桥 + 前端，一个 commit 或一组关联 commit
2. 前端侧改动走 incoming-frontend 分支（鸣鸣 channel），洄 review 后 merge
3. 契约任何字段级变更，commit message 里带 `contract:` 前缀
