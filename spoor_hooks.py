"""
spoor_hooks: session-end 缺口检测（纯逻辑，零 runtime 依赖）。

母命题见 docs/spoor-hooks-proposal.zh.md 第 2 行（会话结束→尾段落盘）。
本案要解决的缺口：agent 干了一下午活但从未调用 spoor 工具——
MCP 返回层的 nudge（v0.4.1/0.4.2）只挂在工具返回上，工具不调，
传感器就死。把检测搬到 on_session_end：壳层在 /new、/reset、CLI 退出、
gateway 过期四种时刻喊一声，本模块做纯机械 diff——

    messages 里出现的项目路径签名  −  账本里 journal.write 的项目集合
    =  触达了但没落账的桌

三条纪律继承自提案，一字不改：
1. 钩子只提醒，裁判是 agent——本模块不写 journal，只算缺口。
2. 搭现有动作的便车——提醒在下个 session 开工仪式（读状态/读账）
   的工具返回尾部浮现，不弹通知不占频道。
3. 失败静默——任何异常返回 None，没有资格弄坏 on_session_end 主路径。

设计注（2026-08-23，与甜心现场讨论）：
- 路径签名从 workbench/repos.json 读——那是路径→桌的唯一事实源，
  不在本模块里硬编码项目清单。
- 只扫 user+assistant 消息文本（tool 结果是机器噪音，不扫）。
- 时间锚：不依赖消息时间戳（壳层不一定提供），以检测时刻为"现在"，
  账本倒序扫最近 SESSION_SCAN_LINES 行算每桌最后一次 journal.write。

v0.5 普适发现（2026-08-29，甜心需求：任何新增项目随时会被同步到桌）：
- 发现层与触达层共用同一把 token 提取器（~/名 与 /home/ubuntu/名）。
- 文本里出现、目录有 .git、不在 repos.json 也不在 ignore.json 的
  → 新项目候选，nudge 一次等裁决：真项目开桌+登记，上游克隆/副本
  登记豁免。裁决的痕迹就在两张表里，表更新后提醒自然熄灭；
  未裁决期间由日终兜底层（~/.hermes/scripts/spoor_backstop.py）低频再报。
- 附带修复：路径签名带边界，/home/ubuntu/Portalk 不再误吞
  /home/ubuntu/Portalk-latest（前缀吞噬 bug）；~/短写法经 basename
  映射也能命中桌。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

# 账本倒序扫描窗口（性能地板，与 spoor_common.NUDGE_SCAN_LINES 同哲学）
SESSION_SCAN_LINES = 2000

# home 一级名的字符集与提取器（触达层/发现层共用）。
_NAME_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)
_PATH_TOKEN = re.compile(r"(?:/home/ubuntu|~)/([A-Za-z0-9][A-Za-z0-9._-]*)")

# 裸名签名表：不在 repos.json 的桌 + 对话里的裸短写（无路径前缀直呼其名）。
# Agent-Grimoire=山海库（grimoire 桌）；Stigmergy 本体开发=memory-wash 桌
# （自举：自己的出生自己记）。
BARE_NAMES = {"Agent-Grimoire": "grimoire", "Stigmergy": "memory-wash"}


def _load_projects(root: Path) -> dict:
    """repos.json → {桌名: 绝对路径}。缺失/损坏返回空 dict（静默）。"""
    try:
        with open(root / "workbench" / "repos.json", encoding="utf-8") as f:
            return {k: str(v) for k, v in json.load(f).items()}
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


def _path_hit(path: str, text: str) -> bool:
    """完整路径签名带右边界：/home/ubuntu/Portalk 不吞 Portalk-latest。"""
    start = 0
    while True:
        i = text.find(path, start)
        if i < 0:
            return False
        j = i + len(path)
        if j >= len(text) or text[j] not in _NAME_CHARS:
            return True
        start = j


def _word_hit(word: str, text: str) -> bool:
    """裸词带双侧边界：xStigmergy 不算命中 Stigmergy。"""
    start = 0
    while True:
        i = text.find(word, start)
        if i < 0:
            return False
        j = i + len(word)
        head_ok = i == 0 or text[i - 1] not in _NAME_CHARS
        tail_ok = j >= len(text) or text[j] not in _NAME_CHARS
        if head_ok and tail_ok:
            return True
        start = i + 1


def discover_projects(text: str, root: Path, home: Path | None = None) -> dict:
    """普适发现：文本里出现的 home 下一级目录，有 .git 但不在 repos.json
    也不在豁免表的 → {目录名: 绝对路径}（新项目候选）。

    钩子不做开桌动作——只提醒，裁判是 agent（祖训第 1 条）。
    """
    home = home or Path.home()
    if not text:
        return {}
    try:
        ignore = _load_ignore(root)
        known_paths = {str(Path(v).resolve()) for v in _load_projects(root).values() if v}
        known_names = set(BARE_NAMES)
        out: dict = {}
        for m in _PATH_TOKEN.finditer(text):
            name = m.group(1)
            if name in ignore or name in known_names:
                continue
            p = home / name
            try:
                if str(p.resolve()) not in known_paths and (p / ".git").exists():
                    out[name] = str(p)
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


def touched_projects(messages: list, root: Path) -> set:
    """messages 文本里出现路径签名的桌集合。

    签名三路：①完整路径（带右边界，不吞前缀同名目录）；
    ②~/短写法（token 提取 → repos.json 路径 basename 映射，v0.5 起
    与发现层共用同一把提取器）；③裸名（BARE_NAMES，双侧边界）。
    """
    text = _messages_text(messages)
    if not text:
        return set()
    out: set = set()
    projects = _load_projects(root)
    for name, path in projects.items():
        if path and _path_hit(path, text):
            out.add(name)
    by_basename = {Path(p).name: d for d, p in projects.items() if p}
    for m in _PATH_TOKEN.finditer(text):
        desk = by_basename.get(m.group(1))
        if desk:
            out.add(desk)
    # 幽灵桌守卫（照照 8/23 审）：目标桌在本机 workbench/ 不存在时跳过——
    # 跨机器部署时提醒一张本地写不了的桌是错误语义，不是降级。
    for bare, desk in BARE_NAMES.items():
        if _word_hit(bare, text) and (root / "workbench" / desk).is_dir():
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

    两类提醒：
    1. 本次 session 触达、且 4h 内无 journal.write 的桌（4h 窗与
       NUDGE_AFTER_H 同源：弧线内不催，收口超期才提醒）。
    2. 普适发现：未开桌的 git 项目——每个只提醒一次（账本去重），
       裁决（登记两张表之一）后自然熄灭。
    """
    try:
        text = _messages_text(messages)
        if not text:
            return None
        now = now if now is not None else time.time()
        parts: list = []
        journaled = journaled_projects(root)
        gap: list = []
        for p in sorted(touched_projects(messages, root)):
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
            names = "、".join(sorted(discovered))
            parts.append(
                f"[nudge] 发现未开桌的 git 项目：{names}。"
                "真项目→workbench_new 开桌+repos.json 登记；"
                "上游克隆/副本→workbench/ignore.json 登记豁免。裁决一次即静默。"
            )
        if not parts:
            return None
        return "\n".join(parts)
    except Exception:
        return None
