# gateway重启窗口 · 四个payload清单（8/23新增第四件）

**什么时候做**：甜心开窗（她说重启gateway可以的时候）。
**怎么做**：重启gateway进程（PID家族1028/1873/1875）——workbench MCP server
和scratchpad watchdog会跟着重启，spoor新代码自动加载。

## 载荷清单

1. **Tideline prefetch v2**（8/22定稿，4bec90c已推）——开场预取改进。
2. **session_epilogue**（7a4417e已推，含今天的session gap挂载）——session收口自动叙事。
3. **工具调用钩子（遥测）**——memory_mirror同构adapter，tool.call事件落events账本。
4. **spoor v0.4.3 session gap检测**（8/23下午新增，322601d已推）——
   on_session_end时纯机械diff：messages路径签名 vs 账本journal.write，
   缺口落spoor.session.gap事件；下个session工具返回浮现，消费即记录。

## 为什么一次电闸带走全部

四个载荷都只差“进程重启加载新代码”这一步。分四次重启=四次打断；
一次带走=一次痛。epoch线（Stigmergy从APPROVALS.md epoch制改到账本制）
和v0.4.3都是Stigmergy侧改动，挂在gateway进程树上的MCP server重启即可。

## 前置条件

线上DB迁移（DROP旧表）已单独批过流程：先备份→甜心批DROP→迁移→
才轮到重启。**顺序不可倒**。

## 前后检查

重启前：git log确认Stigmergy @322601d、tideline-memory @7a4417e已推；
待推=0。
重启后：①随便调一个workbench工具，看返回尾部有没有_sessgap/_nudge
（新代码活了的标志）②账本里应有spoor.session.gap事件（如果上个
session触达了项目却没落账）③probe/smoke跑一遍（32/32+16/16）。
重启后首个session的开工仪式会自然消费今天那笔真gap。
