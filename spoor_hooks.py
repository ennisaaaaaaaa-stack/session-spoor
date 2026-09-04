"""
spoor_hooks: session-end 缺口检测（纯逻辑，零 runtime 依赖）。

母命题见 docs/spoor-hooks-proposal.zh.md 第 2 行（会话结束→尾段落盘）。
本案要解决的缺口：agent 干了一下午活但从未调用 spoor 工具——
MCP 返回层的 nudge（v0.4.1/0.4.2）只挂在工具返回上，工具不调，
传感器就死。把检测搬到 on_session_end：壳层在 /new、/reset、CLI 退出、
gateway 过期四种时刻喊一声，本模块做纯机械 diff——

    messages 里出现的项目引用  −  账本里 journal.write 的项目集合
    =  触达了但没落账的桌

三条纪律继承自提案，一字不改：
1. 钩子只提醒，裁判是 agent——本模块不写 journal，只算缺口。
2. 搭现有动作的便车——提醒在下个 session 开工仪式（读状态/读账）
   的工具返回尾部浮现，不弹通知不占频道。
3. 失败静默——任何异常返回 None，没有资格弄坏 on_session_end 主路径。

v0.6 归一化管道（2026-08-29 晚，甜心架构问答后施工）：
- 查找方向反转：正则只负责从文本「咬」名字 token（边界在咬的
  那一口就定死），表负责认身份。v0.5 之前「拿路径去 find 文本」
  的裸查找清零——前缀吞噬类 bug（Portalk 误吞 Portalk-latest）
  从机制上绝种，不是修补。
- 引用变体升一等公民：全路径 / ~/短写 / 裸名 / 别名（含中文）
  全部折叠到同一身份索引（by_ref），对表只对身份。
- repos.json schema 放宽：值可为字符串（路径，向后兼容）或对象
  {path, aliases[], virtual}。裸名映射从代码（BARE_NAMES 硬编码，
  v0.4-0.5 时代）搬进数据——"会变的都是数据，数据不住代码里"。
- 虚拟桌：无路径、只有名字锚点的桌（架构设计/纯文档项目），
  经 aliases 命中，git 状态自然为空。
- 发现层三信号（甜心 8/29 需求：非代码任务/架构设计也要能进桌）：
  ① .git 存在 → 强候选；② 目录存在 → 弱候选（非代码项目）；
  系统目录/venv 后缀为编译期卫生常量（任何机器都成立的默认值），
  与部署态裁决表 ignore.json 是两种东西。
  ③ 宣言短语旗（"新项目/开坑/立项"等）——无路径时提示 agent 复核。
- home 硬编码拆除：token 正则按运行时 home 现编（re.escape + 缓存），
  开源部署第一天不再假设用户叫 ubuntu。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

# 账本倒序扫描窗口（性能地板，与 spoor_common.NUDGE_SCAN_LINES 同哲学）
SESSION_SCAN_LINES = 2000

# token 正则缓存：按 home 现编（re.escape），同 home 只编一次。
_TOKEN_CACHE: dict = {}

# 系统目录（编译期卫生常量）：只放任何机器都成立的普适噪音——
# OS 目录/包管理缓存/临时区/venv 后缀。部署者自己的目录不在这，
# 经一次裁决进 ignore.json（那是部署态数据，这是机制内置默认）。
_SYSTEM_DIRS = {
    "Downloads", "downloads", "node_modules", "node-modules",
    "go", "snap", "tmp", "temp", "backups",
}
_SYSTEM_SUFFIXES = ("-env", "-venv", "_env")

# 宣言短语（发现信号③）：有宣言但没咬到任何路径 token 时打旗。
_DECL_PATTERNS = re.compile(r"新项目|开了?个(?:新)?坑|立项|新仓库|新repo")


def _token_re(home: Path) -> "re.Pattern":
    key = str(home)
    rx = _TOKEN_CACHE.get(key)
    if rx is None:
        rx = re.compile(r"(?:" + re.escape(key) + r"|~)/([\w.-]+)")
        _TOKEN_CACHE[key] = rx
    return rx


# CJK 虚词边界集：中文没有词边界，「山海的三层门」的「的」是语法胶水
# 不是名字部件——虚词当边界，其余 CJK 当名字部件（「山海经」的「经」
# 挡住「山海」误中）。词表歧义（山海库 vs 山海经）归别名数据层管，
# 不在边界语法里管——语法管形状，词汇管身份。
_CJK_BOUNDARY = set("的了是在和与把被这就也都还而及对从到向以于为吗呢吧啊")


def _is_head_break(ch: str) -> bool:
    """左侧边界：只有 ASCII 名字字符会构成「更长名字」的前缀。"""
    return not (ch.isascii() and (ch.isalnum() or ch in "._-"))


def _is_tail_break(ch: str) -> bool:
    """右侧边界：ASCII 名字字符挡（Portalk-latest）；CJK 只被虚词放行。"""
    if ch.isascii():
        return not (ch.isalnum() or ch in "._-")
    return ch in _CJK_BOUNDARY


def _load_projects(root: Path) -> dict:
    """repos.json → 归一化 {桌: {"path": str|None, "aliases": set}}。

    值兼容两种形态：字符串=路径（v0.5 及以前）；对象={path?, aliases?}。
    path 的 basename 自动进 aliases（全路径与 ~/短写天然命中）。
    缺失/损坏返回空 dict（静默纪律）。
    """
    try:
        with open(root / "workbench" / "repos.json", encoding="utf-8") as f:
            raw = json.load(f)
        out: dict = {}
        for desk, v in raw.items():
            if isinstance(v, dict):
                path = str(v["path"]) if v.get("path") else None
                aliases = {str(a) for a in v.get("aliases", []) or []}
            else:
                path, aliases = str(v), set()
            if path:
                aliases.add(Path(path).name)
            aliases.discard(desk)
            out[desk] = {"path": path, "aliases": aliases}
        return out
    except Exception:
        return {}


def _load_ignore(root: Path) -> dict:
    """ignore.json → 豁免名单 {目录名: 一句话理由}。看见了但不开桌的
    上游克隆/快照副本登记在这里（agent 裁决后写入，钩子只读）。
    缺失/损坏返回空 dict（静默）。"""
    try:
        with open(root / "workbench" / "ignore.json", encoding="utf-8") as f:
            return {k: str(v) for k, v in json.load(f).items()}
    except Exception:
        return {}


def _word_hit(word: str, text: str) -> bool:
    """裸词带双侧边界（unicode 语义）：xStigmergy 不算命中 Stigmergy，
    山海经 不算命中 山海；山海的三层门 算命中 山海（虚词当边界）。"""
    start = 0
    while True:
        i = text.find(word, start)
        if i < 0:
            return False
        j = i + len(word)
        head_ok = i == 0 or _is_head_break(text[i - 1])
        tail_ok = j >= len(text) or _is_tail_break(text[j])
        if head_ok and tail_ok:
            return True
        start = i + 1


def _by_ref(projects: dict) -> dict:
    """身份索引：名字/别名 → 桌（先到先得，同名歧义以表序为准）。

    桌名自身也是合法引用形态（v0.6 回归修复 2026-09-05）：裸名
    「ocean-listen」在 BARE_NAMES 时代可命中，拆表进数据层时被
    aliases.discard(desk) 连带丢弃——目录名恰等于桌名的桌连全路径
    引用都失效。修复=桌名以最低优先级进索引（setdefault 放在
    aliases 之后，同名歧义时显式别名优先）。
    """
    out: dict = {}
    for desk, info in projects.items():
        for a in info["aliases"]:
            out.setdefault(a, desk)
        out.setdefault(desk, desk)
    return out


def discover_projects(text: str, root: Path, home: Path | None = None) -> dict:
    """普适发现：文本里出现的 home 下一级目录，不在 repos.json /
    ignore.json / 系统目录的 → {目录名: {"path": 绝对路径, "kind": "git"|"dir"}}。

    kind=git（强候选：代码项目）/ dir（弱候选：目录存在但非 git——
    非代码任务/资产/文档项目）。钩子不做开桌动作——只提醒，
    裁判是 agent（祖训第 1 条）。
    """
    home = home or Path.home()
    if not text:
        return {}
    try:
        ignore = _load_ignore(root)
        known: set = set(ignore)
        for desk, info in _load_projects(root).items():
            known.add(desk)
            known |= info["aliases"]
        out: dict = {}
        for m in _token_re(home).finditer(text):
            name = m.group(1)
            if name in known or name in out:
                continue
            if name.startswith("."):
                continue  # 隐藏目录（.cache/.config/…）一律不算候选
            if name in _SYSTEM_DIRS or name.endswith(_SYSTEM_SUFFIXES):
                continue
            p = home / name
            try:
                if (p / ".git").exists():
                    out[name] = {"path": str(p), "kind": "git"}
                elif p.is_dir():
                    out[name] = {"path": str(p), "kind": "dir"}
            except OSError:
                continue
        return out
    except Exception:
        return {}


def _recently_surfaced_new(root: Path) -> set:
    """近窗口 gap 事件里已提醒过、仍未裁决的新项目名集合。
    裁决=写进 repos.json 或 ignore.json——表一更新，发现层自然不再
    产出该名字，这里也就无所谓重复。账本就是传感器，不另立状态文件。"""
    out: set = set()
    try:
        with open(root / "ledger.jsonl", encoding="utf-8", errors="replace") as f:
            lines = [l for l in f if l.strip()][-SESSION_SCAN_LINES:]
        for raw in lines:
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if obj.get("event") == "spoor.session.gap":
                for n in obj.get("new_projects", []) or []:
                    out.add(str(n))
    except Exception:
        pass
    return out


def _messages_text(messages: list) -> str:
    """user+assistant 消息的文本拼接（content 兼容 str/list-of-dict）。"""
    parts: list = []
    for msg in messages or []:
        try:
            if msg.get("role") not in ("user", "assistant"):
                continue
            c = msg.get("content", "")
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, list):
                for p in c:
                    if isinstance(p, dict) and p.get("type") == "text":
                        parts.append(str(p.get("text", "")))
                    elif isinstance(p, str):
                        parts.append(p)
        except Exception:
            continue
    return "\n".join(parts)


def touched_projects(messages: list, root: Path, home: Path | None = None) -> set:
    """messages 文本里出现引用的桌集合。

    v0.6 归一化：token 咬出的名字（全路径/~/短写同源）与裸词（别名，
    含中文）都折叠到身份索引 by_ref 再对桌——原文千变万化，身份只有
    一个。裸词命中过幽灵桌守卫（照照 8/23 审）：目标桌在本机
    workbench/ 不存在时跳过——跨机器部署时提醒一张本地写不了的桌
    是错误语义，不是降级。
    """
    text = _messages_text(messages)
    if not text:
        return set()
    home = home or Path.home()
    out: set = set()
    projects = _load_projects(root)
    by_ref = _by_ref(projects)
    for m in _token_re(home).finditer(text):
        desk = by_ref.get(m.group(1))
        if desk:
            out.add(desk)
    for name, desk in by_ref.items():
        if _word_hit(name, text) and (root / "workbench" / desk).is_dir():
            out.add(desk)
    return out


def discover_from_messages(messages: list, root: Path, home: Path | None = None) -> dict:
    """discover_projects 的 messages 入口（record_session_gap 落审计用）。"""
    return discover_projects(_messages_text(messages), root, home)


def journaled_projects(root: Path) -> dict:
    """账本里每桌最近一次 threesome.journal.write 的 {桌: ts}。"""
    ledger = root / "ledger.jsonl"
    out: dict = {}
    try:
        if not ledger.exists():
            return out
        with open(ledger, encoding="utf-8", errors="replace") as f:
            lines = [l for l in f if l.strip()][-SESSION_SCAN_LINES:]
        for raw in reversed(lines):
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if obj.get("event") == "threesome.journal.write" and obj.get("project"):
                p = str(obj["project"])
                if p not in out:   # 倒序，第一条即最新
                    out[p] = str(obj.get("ts", ""))
        return out
    except Exception:
        return out


def session_gap_nudge(
    messages: list, root: Path, now: float | None = None, home: Path | None = None
) -> "str | None":
    """session 末尾缺口检测。返回提醒文本或 None。

    三类提醒：
    1. 本次 session 触达、且 4h 内无 journal.write 的桌（4h 窗与
       NUDGE_AFTER_H 同源：弧线内不催，收口超期才提醒）。
    2. 普适发现：未登记的新目录——git 强候选/目录弱候选，每个只提醒
       一次（账本 new_projects 去重），裁决（登记两张表之一）后熄灭。
    3. 宣言旗：说了"新项目/开坑"但没咬到任何新目录——提示 agent
       复核（可能是无目录的概念型项目，可登记虚拟桌）。
    """
    try:
        text = _messages_text(messages)
        if not text:
            return None
        now = now if now is not None else time.time()
        home = home or Path.home()
        parts: list = []
        journaled = journaled_projects(root)
        gap: list = []
        for p in sorted(touched_projects(messages, root, home)):
            ts = journaled.get(p)
            if ts is None:
                gap.append(f"{p}(从未写)")
                continue
            try:
                age_h = (now - time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))) / 3600.0
            except (ValueError, TypeError, OverflowError):
                age_h = None
            if age_h is None or age_h >= 4.0:
                gap.append(f"{p}({age_h:.0f}h前)" if age_h is not None else f"{p}(时间戳异常)")
        if gap:
            parts.append(
                f"[nudge] 上个 session 触达 {('、'.join(gap))} 但未落账。"
                "journal 是下个 session 的交接凭据——开工前 workbench_journal 补一条"
                "（mark: 坑/判断/数据，一句话即可），不涉及项目可不写。"
            )
        surfaced = _recently_surfaced_new(root)
        discovered = {k: v for k, v in discover_projects(text, root, home).items()
                      if k not in surfaced}
        if discovered:
            names = "、".join(
                f"{k}({'git' if v['kind'] == 'git' else '目录'})"
                for k, v in sorted(discovered.items())
            )
            parts.append(
                f"[nudge] 发现未登记的目录：{names}。"
                "真项目→workbench_new 开桌+repos.json 登记；"
                "上游克隆/副本→workbench/ignore.json 登记豁免。"
                "裁决一次即静默。"
            )
        if not discovered and not _token_re(home).search(text) and _DECL_PATTERNS.search(text):
            parts.append(
                "[nudge] 本 session 有新项目宣言但未识别到目录——若有：真项目开桌"
                "+repos.json 登记；纯设计/文档类可登记虚拟桌（无 path，只填 aliases）；"
                "无则忽略本条。"
            )
        if not parts:
            return None
        return "\n".join(parts)
    except Exception:
        return None
