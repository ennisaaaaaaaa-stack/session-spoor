# gateway重启窗口 · payload清单（9/9新增第六、七件）

**什么时候做**：甜心开窗（她说重启gateway可以的时候）。
**怎么做**：重启gateway进程（PID家族1028/1873/1875）——workbench MCP server
和scratchpad watchdog会跟着重启，spoor新代码自动加载。

## 载荷清单（staged，未生效）

1. **Tideline prefetch v2**（8/22定稿，4bec90c已推）——开场预取改进。
2. **session_epilogue**（7a4417e已推，含session gap挂载）——session收口自动叙事。
3. **工具调用钩子（遥测）**——memory_mirror同构adapter，tool.call事件落events账本。
4. **spoor v0.4.3 session gap检测**（8/23下午新增，322601d已推）——
   on_session_end时纯机械diff：messages路径签名 vs 账本journal.write，
   缺口落spoor.session.gap事件；下个session工具返回浮现，消费即记录。
5. **spoor v0.4.4+ 署名修复**（8/23晚新增，f6c01ba已推）——照照审出的
   部署缺口：gap事件匿名（SPOOR_AGENT env只到MCP进程，gateway没有）。
   本机更糟：全进程零配置，账本自第一天起全匿名。修复=agent_name()
   env优先→`$STIGMERGY_ROOT/agent.name`文件兜底，一个机制盖全进程，
   systemd unit不打洞。本机agent.name已写"洄"（gitignored，forker写自己的）。
6. **Tideline provider r64**（9/9 00:00收尾车新增，05e63ec已推）——
   注入侧轨迹渲染：命中带append的记忆渲染成🧭链（相对时间+拆锚
   脚手架+住母记忆块内），cap 6段。生产文件
   `~/.hermes/plugins/portalk/__init__.py` r63→r64，repo镜像已同步。
   依赖：生产MCP server还是v2.6，trajectories表不存在时静默跳过
   （过渡期防御），v2.8部署车过了才见真轨迹。
7. **DREAM prompt ⑥轨迹升格段**（9/9新增）——
   `~/Portalk/mcp-servers/memory-mcp/prompts/dream_solidify.md` staged，
   list→判断→write流程+无演进保持mech。防御：前置工具检查，
   memory_traj_promote不在工具列表→整节跳过不报错（今晚02:30固化
   就会读它，生产v2.6无traj工具，安全跳过）。
8. **spoor v1.3 出生发号**（9/15新增，1376350已推）——append_ledger
   本地分支锁内发号+origin文件机制。**代码已生效**：MCP server按连接
   spawn（stdio，9/6切流时确认的零感知特性），新连接自动加载新代码；
   唯一例外=挂在gateway进程树上的钩子宿主进程（on_session_end等），
   它们要等gateway重启才加载v1.3。已在本session用 append_ledger
   直调验证出生号（vps-20260915T134810Z-001/002）；活库已renumber
   --apply（438/438带号）。审稿房已开（堂屋「spoor v1.3 审稿房」，
   照照重点审A3服务端发号缺口）。

## 为什么一次电闸带走全部

五个载荷都只差“进程重启加载新代码”这一步。分五次重启=五次打断；
一次带走=一次痛。epoch线（Stigmergy从APPROVALS.md epoch制改到账本制）
和v0.4.3/v0.4.4都是Stigmergy侧改动，挂在gateway进程树上的MCP server重启即可。

## 前置条件

线上DB迁移（DROP旧表）已单独批过流程：先备份→甜心批DROP→迁移→
才轮到重启。**顺序不可倒**。

## 前后检查

重启前：git log确认Stigmergy @f6c01ba、tideline-memory @3448c95已推；
待推=0。
重启后：①随便调一个workbench工具，看返回尾部有没有_sessgap/_nudge
（新代码活了的标志）②新事件应带agent署名字段（署名修复活了的标志）③
probe/smoke跑一遍（32/32+16/16）。重启后首个session的开工仪式会自然
消费今天那笔真gap。
