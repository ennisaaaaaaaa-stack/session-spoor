# 桥-前端契约 v0.2

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
ecosystem: portalk   # 自由值（本部署用 portalk/声海/猎迹/varia，空=未归属）
relates_to:          # 横向关系（可选，边）
  - project: tideline
    relation: 记忆层底座
milestones:          # 成就列表（可选）
  - v2.4 全链路闭环（2026-08-09）
docs:                # 显式档案归属（v0.2 新增，可选）——这个项目拥有哪些档案 doc
  - tideline-v24-milestone
---
# STATUS
（正文照旧）
```

frontmatter 缺失或字段缺失 → 默认 `lifecycle: 生长`、`ecosystem: ""`、`relates_to: []`、`milestones: []`、`docs: []`（向后兼容）。

**v0.2 新增（甜心 2026-08-18 拍板）——docs 显式归属**：档案 doc 与项目的连线不再靠命名习惯猜。声明了至少一个 doc 的项目以声明为准（星图边 w:2，权威，前缀让位）；未声明或声明为空 → 退回名字前缀启发式（w:1）——零配置部署开箱仍有连线。注意：桥对未声明桌统一下发 `docs: []`，「显式空」与「未声明」在数据形状上不可区分，两者同等走兜底。开源可一键：语义层不绑命名习惯。

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
- `GET /api/projects` → `[{project, description, status{updated, priority, sections{}}, git{}, todo, blocked, pending_review[], pits[], journal[], journal_total}]` + **v0.1 每项 `lifecycle, ecosystem, relates_to[]`（来自 frontmatter）** + **v0.2 新增 `milestones[], docs[]`**
- `GET /api/project/<name>` → 同上单项目
- `GET /api/graph` → `{nodes[{doc, version_id, parent, bytes, source_ref}], edges[]}`
- `GET /api/archive` → `{docs[]}`
- `GET /api/archive/<doc>` → `{content, ...}`
- `GET /api/scratch` → **待开口**。桥有数据源时返回 `{spaces[{space, files[{name, bytes, mtime}]}], events[]}`；开口前 404（前端隐藏）
- `GET /api/heatmap` → **待开口**。从 ledger ts 按天聚合 → `{days{"2026-08-18": 12, ...}}`；开口前 404

事件类型（overview.recent_events[].event）：`threesome.archive.put/link/get/query/verify`（ledger 事件）。前端 EV_ACTION 表里 journal.write/create/cleanup 等为本地桥方言，不在本契约内——生产前端遇到未知名按原样显示或「留下了痕迹」。

## 项目间关系怎么到前端（v0.2 定稿）

**甜心 2026-08-18 定轴：分层主轴 = 生命周期**（生长 → 里程碑 → 毕业 → 胚胎），生态降为组内徽章。「正常应该按生长/里程碑/毕业/胚胎做项目分层」——侧边栏现在的项目名平铺是错的层级。

1. **生命周期（树）** → 侧边栏顶层分组，契约四值按叙事序；未知值透传垫底（不猜数据）
2. **生态归属（徽章）** → 组内彩色徽章（颜色由名字哈希派生，零硬编码）+ 星图分区色
3. **relates_to（横向边）** → 星图加项目节点 + 项目页「关联项目」区
4. **项目页 × 档案房 join** → `docs:` 显式声明（w:2 权威）+ 名字前缀兜底（w:1，仅未声明者）；项目页新增「档案」区列出声明的 doc

## 变更流程

1. 改 CONTRACT.md（本文件）+ 桥 + 前端，一个 commit 或一组关联 commit
2. 前端侧改动走 incoming-frontend 分支（鸣鸣 channel），洄 review 后 merge
3. 契约任何字段级变更，commit message 里带 `contract:` 前缀
