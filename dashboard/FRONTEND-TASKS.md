# 前端任务包 v1 · 契约 v0.1 数据面已就绪

> 洄 → 鸣鸣，2026-08-18。数据面 @ be31f1e 已推。所有 API 形状冻结在根目录 `CONTRACT.md`——要改形状，先改契约。

## 新到货（生产 API 已在流）

`/api/projects` 与 `/api/project/<name>` 每项新增四键：

- `lifecycle`：毕业 | 里程碑 | 生长 | 胚胎 —— **主状态**（裁决：在跑=生长，里程碑是成就不是状态）
- `ecosystem`：portalk | 声海 | 猎迹 | varia | ""（显示名映射留前端，如 portalk→Portalk生态）
- `relates_to`：`[{project, relation}]` —— 项目间关系边
- `milestones`：`["…"]` —— 成就列表（日期写进文案）

当前真值：portalk=生长（里程碑：v0.x交付、前端上线8-18）；tideline=生长（v2.4闭环8-9、Kimi3入驻8-12）；ocean-listen=生长；memory-wash=生长（9doc收官8-18）。

## 渲染任务（按优先级）

1. **侧边栏/首页**：lifecycle 徽章 + ecosystem 分组，全部从 API 字段读——**删掉从文档名猜分类的启发式**（生产档案全英文名，猜不中，全堆未分类那层目录就是它）
2. **档案房**：内部不再按生命周期套娃分类（侧边栏已表达，信息不重复）。档案内部如需结构，等桥侧给 metadata——挂账中，别在前端猜
3. **项目页**：milestones 渲染成成就列表；relates_to 渲染成边（跟星图联动，可以下一波）
4. **404 ≠ 空**（契约语义表）：404=隐藏整个区块；200+空结构=空态文案。`/api/scratch`、`/api/heatmap` 洄侧马上开——空态现在就能按契约形状先做
5. **lifecycle.json 退役**：侧边栏切到 API 字段后，删掉对它的 fetch（文件先留，不物理删）

## 本地开发

本地桥不用改——用契约形状的 mock 数据即可。集成验证在合并时由洄在 VPS 侧做；token 的事洄和甜心管，你的代码不碰它。

## 推送

照旧 `incoming-frontend` 分支（`dashboard/README.md` 四步）。
