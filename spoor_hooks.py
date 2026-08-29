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
"""
from __future__ import annotations

import json
import time
from pathlib import Path

# 账本倒序扫描窗口（性能地板，与 spoor_common.NUDGE_SCAN_LINES 同哲学）
SESSION_SCAN_LINES = 2000


def _load_projects(root: Path) -> dict:
    """repos.json → {桌名: 绝对路径}。缺失/损坏返回空 dict（静默）。"""
    try:
        with open(root / "workbench" / "repos.json", encoding="utf-8") as f:
            return {k: str(v) for k, v in json.load(f).items()}
    except Exception:
        return {}


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
    """messages 文本里出现路径签名的桌集合。签名 = repo 路径本身。"""
    text = _messages_text(messages)
    if not text:
        return set()
    out: set = set()
    for name, path in _load_projects(root).items():
        if path and path in text:
            out.add(name)
    # 裸名签名表：不在 repos.json 的桌 + 真实对话里的短写法（~/名、裸名）。
    # Agent-Grimoire=巡山产物本地化，与 portalk 桌同轴；
    # Stigmergy 本体开发=memory-wash 桌（自举：自己的出生自己记）。
    # 幽灵桌守卫（照照 8/23 审）：目标桌在本机 workbench/ 不存在时跳过——
    # 跨机器部署时提醒一张本地写不了的桌是错误语义，不是降级。
    BARE_NAMES = {"Agent-Grimoire": "grimoire", "Stigmergy": "memory-wash"}
    for bare, desk in BARE_NAMES.items():
        if bare in text and (root / "workbench" / desk).is_dir():
            out.add(desk)
    return out


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


def session_gap_nudge(messages: list, root: Path, now: float | None = None) -> str | None:
    """session 末尾缺口检测。返回提醒文本或 None。

    只报告"本次 session 触达且 4h 内无 journal.write"的桌。
    4h 窗与 NUDGE_AFTER_H 同源：弧线内不催，收口超期才提醒。
    """
    try:
        touched = touched_projects(messages, root)
        if not touched:
            return None
        journaled = journaled_projects(root)
        now = now if now is not None else time.time()
        gap: list = []
        for p in sorted(touched):
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
        if not gap:
            return None
        return (
            f"[nudge] 上个 session 触达 {('、'.join(gap))} 但未落账。"
            "journal 是下个 session 的交接凭据——开工前 workbench_journal 补一条"
            "（mark: 坑/判断/数据，一句话即可），不涉及项目可不写。"
        )
    except Exception:
        return None
