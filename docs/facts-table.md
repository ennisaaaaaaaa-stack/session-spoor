# facts 表（已探明事实表）v1.0

> spec：档案房 `spoor-facts-table-spec`（zhaozhao拟边界与判据，洄施工，tiexin 10/5 裁定）。
> 实现：`facts_store.py`（纯逻辑，两宅共用）+ `workbench_server.py` 接线
> （`fact_write`/`fact_read`/冷启动搭车）。
> 验收判据 J1-J7 见 spec；本文档只钉**配置**与**裁决**。

## 这张表是什么

便宜的结论层。病根：journal 天天打修因但没人考古（考古本身太贵）——
三场 spoor-session 在同一张牌面上各烧 $0.42 得出同一句「值得动 0」，
env-event 每场 ~2.4k token 重新发现同一个已知事实烧了十天。
这张表让结论跨牌面存活：**冷启动必读，要细节按 source 指针回挖。**

## 配置（一处可查——J7 断言一）

两个时效机制，一张表对齐：

| 机制 | 管什么 | 值 | 定义处 |
|---|---|---|---|
| faceHash 冷却窗 `CONDUCTOR_SPOOR_FACE_COOLDOWN_H` | 同**牌面** 12h 不重抽（时间维） | 12h（琴师侧 env，本表不持有） | pianist/conductor 派活侧 |
| facts TTL `fast` | 「现在时」事实：队列空、在跑进程、临时状态 | 6h | `facts_store.TTL_CLASS_H` |
| facts TTL `slow` | 「地质」事实：路径不存在、表结构、环境恒性 | 720h（30天） | `facts_store.TTL_CLASS_H` |

TTL 值改动 = 改 `facts_store.py` 的 `TTL_CLASS_H` 常量 + 本文表格同步
（一处可查的双写义务在这——常量是机制，本文是人查的地图）。

## 冲突裁决（钉死——J7 断言二）

**TTL 过期 > faceHash 冷却（新鲜度优先于省钱）。**

牌面没变但事实过期 → 照抽照探，faceHash 冷却**无权**拦截重探。
机制面：读口过滤只看自己的 TTL，过期事实即从活视图消失；外部冷却
窗无法把过期事实留在读口里。省钱永远让位于新鲜度——一张过期的
「队列空」比重复探索更贵（假绿比浪费贵）。

## 接口

- 写口 `fact_write(scope, key, conclusion, source, ttl_class, ttl_h)`：
  同 `(scope,key)` 再写 = 刷新（UPSERT），`created_at` 保留首次探明时间。
  scope 三域：`global` / `face:<指纹>`（指纹真值在琴师侧）/ 项目名（须已开桌）。
- 读口 `fact_read(scope, include_expired, reason)`：默认只回未过期；
  过期名单在返回尾部出声（`[过期待重验 N 条]`），逼重探不藏账。
  `include_expired=True` 审计用，过期行带全字段。
- 冷启动注入：`workbench_status(project)` 读路径自动携带该项目域+全局域
  活事实（开工仪式搭车，不新建仪式——nudge 同哲学）。

## 与邻居的分界

- **faceHash**（pianist 侧）：时间维 vs 本表内容维，互补不是替代（见上裁决）。
- **journal**：journal 是过程叙事（人话修因），本表是结论索引（机器可判
  活/过期）。journal 会被消化 cron 清理，本表自管生命周期。
- **ledger**：`threesome.facts.write/read` 事件入账本（conclusion_head
  前 80 字符，同 journal entry_head 泄漏哲学），审计走 `ledger_query`。

## 存储

`{STIGMERGY_ROOT}/facts.db`——SQLite WAL，gitignore（运行时数据，每部署
各自积累）。schema 迁移义务：加列走幂等 ALTER（对齐 Tideline 旧库补列
惯例），本表 v1.0 无存量故直接建表。
