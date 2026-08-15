# session-spoor · 猎迹

**Every session leaves a trail.**

Agent干活的痕迹管理系统——MCP工具集，三层结构：涂鸦房（临时工草稿，用完即弃）、工作台（主agent常驻手稿）、档案房（项目归档，版本化）。

## 为什么

Agent的session结束，过程就蒸发了。临时文件散在/tmp，进行中的状态没人记得，写过的脚本重写三遍，踩过的坑再踩一次。subagent干完活，中间判断跟着上下文一起消失。

工具返回数据，agent返回认知——但认知没有归档通道。session-spoor就是这条通道：**session死了，痕迹还在。**

## 三层

| 层 | 名字 | 生命周期 | 干什么 |
|---|---|---|---|
| L1 | scratch/ 涂鸦房 | 绑任务 | 分身的草稿纸，结束三去向：导出/账本/蒸发 |
| L1.5 | workbench/ 工作台 | 绑agent | 项目索引/状态桌面/记录条/复用件架 |
| L2 | archive/ 档案房 | 永久 | 版本化归档，FTS检索，消化门槛 |

共享一套mark词汇表（判断/数据/坑/待审），同一个回流压缩器入口，同一本账本——**插件可拔，账本不可少。**

## 安装

```bash
# 涂鸦房
STIGMERGY_ROOT=~/.spoor python3 scratchpad_server.py

# 工作台
STIGMERGY_ROOT=~/.spoor python3 workbench_server.py
```

任何MCP client直接挂载，不绑任何runtime。

## 名字

spoor /spʊər/ — 猎人追踪用的词：动物走过的路留下的足迹。
下一个session，循着spoor找到上一个session的自己。

---

MIT · 猎迹（中文）· 灵感来自stigmergy——白蚁没有蓝图，塔自己会站着。
