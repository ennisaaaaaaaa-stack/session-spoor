#!/usr/bin/env python3
"""
session-spoor MCP — 猎迹
工作台(workbench) MCP server v0.2


Dying Will (dependency declaration — forces one ppid thought at write time):
  Who launched me: the gateway, as its stdio child process (Hermes config, mcp section).
  What happens if you kill me: the gateway's stdio channel breaks; MCP calls hang.
  Nobody revives me — only a gateway restart brings me back. Always check ppid before kill.
  Patch activation: code changes require a gateway restart (patch-on-disk != running process).

主agent的常驻过程层：项目索引/状态桌面/记录条/复用件架。
联邦式：与涂鸦房共享mark词汇表与接口语义，存储独立。

存储：{root}/workbench/{project}/
索引：{root}/workbench/INDEX.md
"""

import json
import os
import time
from pathlib import Path, PureWindowsPath

from mcp.server.fastmcp import FastMCP

import spoor_common
import spoor_search

mcp = FastMCP("stigmergy-workbench")

ROOT = Path(os.environ.get("STIGMERGY_ROOT", str(Path.home() / "Stigmergy")))
WB = ROOT / "workbench"
INDEX = WB / "INDEX.md"

MARKS = {"判断", "数据", "坑", "待审·自", "待审·人"}

WB.mkdir(parents=True, exist_ok=True)


# ---------- 内部 ----------


LEDGER = ROOT / "ledger.jsonl"

def _ledger(event: dict) -> None:
    spoor_common.append_ledger(event)

def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M")

def _proj(name: str) -> Path:
    if not name or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in name):
        raise ValueError(f"invalid project name: {name!r}")
    p = WB / name
    if not p.is_dir():
        raise FileNotFoundError(f"project not found: {name} (workbench_new first)")
    return p

def _safe_rel(path: str) -> Path:
    rel = Path(path)
    # Windows 陷阱（r13/zcode）：Path("/etc/x").is_absolute() 在 nt 语义下 False（有根无盘符），
    # 之后 base / rel 的 join 语义丢弃整个 base → 读写双向逃逸出沙箱。
    # 双视角检查：PureWindowsPath 补上 Windows 视角（POSIX 字面量 "C:/x" 同步到 Windows 即逃逸）。
    win = PureWindowsPath(path)
    if (rel.is_absolute() or rel.drive or rel.root or ".." in rel.parts
            or win.drive or win.root or ".." in win.parts):
        raise ValueError(f"path must be relative inside project: {path!r}")
    return rel

def _index_reload() -> list[dict]:
    """扫描所有项目的description和完成状态，重建索引。"""
    rows = []
    if WB.is_dir():
        for p in sorted(WB.iterdir()):
            if not p.is_dir():
                continue
            desc = ""
            d = p / "description.md"
            if d.exists():
                desc = d.read_text(encoding="utf-8").strip().splitlines()[0] if d.read_text(encoding="utf-8").strip() else ""
            done = (p / "DONE").exists()
            mtime = time.strftime("%m-%d %H:%M", time.localtime(p.stat().st_mtime))
            rows.append({"project": p.name, "desc": desc, "done": done, "touched": mtime})
    return rows

def _index_write() -> None:
    rows = _index_reload()
    lines = ["# 迹廊 · 项目索引", "", "| 项目 | 说明 | 状态 | 最近 |", "|---|---|---|---|"]
    for r in rows:
        status = "✅完成" if r["done"] else "🔨进行中"
        # GFM表格转义：管道字符必须转成 \|（|| 仍会破列——照照二轮验证）
        lines.append(f"| {r['project']} | {(r['desc'] or '-').replace('|', chr(92)+'|')} | {status} | {r['touched']} |")
    lines += ["", f"_自动维护 · {_now()}_"]
    INDEX.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------- MCP 工具 ----------

@mcp.tool()
def workbench_new(project: str, description: str = "") -> str:
    """建项目：目录骨架 + description + 索引登记。

    Args:
        project: 项目名（字母数字-_）
        description: 一句话说明这项目是什么
    """
    if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in project):
        return json.dumps({"ok": False, "error": "project name: [a-zA-Z0-9_-] only"})
    p = WB / project
    if p.exists():
        return json.dumps({"ok": False, "error": f"project exists: {project}"})
    (p / "design").mkdir(parents=True)
    (p / "journal").mkdir(parents=True)
    (p / "snippets").mkdir(parents=True)
    (p / "description.md").write_text(description + "\n", encoding="utf-8")
    (p / "STATUS.md").write_text("# STATUS\n\n刚创建，还没开工。\n", encoding="utf-8")
    _index_write()
    _ledger({"event": "threesome.workbench.new", "project": project, "desc": description})
    return json.dumps({"ok": True, "project": project, "dir": str(p)})

@mcp.tool()
def workbench_status(project: str, text: str = "") -> str:
    """读/写 STATUS.md —— 进行中状态桌面。
    不传text=读当前状态（下次session醒来先读这个）；
    传text=覆盖写入（保持最新，写"做到哪/下一步/卡在哪"）。"""
    p = _proj(project)
    f = p / "STATUS.md"
    if not text:
        return f.read_text(encoding="utf-8") if f.exists() else "(no status yet)"
    f.write_text(f"# STATUS · 更新于 {_now()}\n\n{text}\n", encoding="utf-8")
    _index_write()
    return json.dumps({"ok": True, "written": len(text)})

@mcp.tool()
def workbench_journal(project: str, entry: str, mark: str = "判断") -> str:
    """追加一条工作记录（自动时间戳）。

    Args:
        mark: 判断 / 数据 / 坑 / 待审·自 / 待审·人
    """
    if mark not in MARKS:
        return json.dumps({"ok": False, "error": f"mark must be one of {sorted(MARKS)}"})
    p = _proj(project)
    day = time.strftime("%Y-%m-%d")
    jf = p / "journal" / f"{day}.md"
    line = f"- **[{mark}]** {spoor_common.stamped(_now())} {entry}"
    spoor_common.append_journal(jf, line)
    _index_write()
    # 契约 v0.2: entry_head 前80字符（含mark前缀）。journal会被消化cron清理，
    # 清理后账本是这条内容唯一的持久痕迹——够识别、不够泄漏。
    _ledger({"event": "threesome.journal.write", "project": project, "file": jf.name,
             "mark": mark, "bytes": len(entry.encode("utf-8")), "entry_head": line[:80]})
    return json.dumps({"ok": True, "journal": str(jf.name), "mark": mark, "agent": spoor_common.agent_name() or None})

@mcp.tool()
def workbench_read_journal(project: str, mark: str = "", limit: int = 30, reason: str = "") -> str:
    """读记录条。可按mark过滤。
    开工仪式：新session接手项目先读 mark=坑 的。

    Args:
        reason: 触发来源（如 "开工仪式"/"醒来"），进账本——审计要看是谁在什么场合读的。可空。
    """
    p = _proj(project)
    entries = []
    # 按文件名（日期）升序遍历，entries 全局按时间排——[-limit:] 取最新
    # (照照 review: 旧版 reverse=True + [-limit:] 方向打架，limit 截掉的是最新记录)
    for jf in sorted((p / "journal").glob("*.md")):
        for ln in jf.read_text(encoding="utf-8").splitlines():
            if ln.startswith("- **["):
                entries.append((jf.stem, ln))
    if mark:
        if mark not in MARKS:
            return json.dumps({"ok": False, "error": f"mark must be one of {sorted(MARKS)} or empty"})
        # 精确匹配行首固定格式，不做子串匹配
        # (照照 review: 旧版 in 匹配，正文里出现"[坑]"字样的判断条会被误捞)
        prefix = f"- **[{mark}]**"
        entries = [(d, ln) for d, ln in entries if ln.startswith(prefix)]
    entries = entries[-limit:]
    _ledger({"event": "threesome.journal.read", "project": project, "mark": mark or None,
             "limit": limit, "reason": reason or None, "entries": len(entries)})
    return "\n".join(f"{d} | {ln}" for d, ln in entries) if entries else "(empty)"

@mcp.tool()
def workbench_snippet(project: str, name: str, content: str = "") -> str:
    """存/取复用件。content空=取，非空=存。
    复用件=下次能直接抄的脚本/片段（不够格变skill但绝不重写第三遍）。"""
    p = _proj(project)
    sp = p / "snippets" / _safe_rel(name)
    if content:
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(content, encoding="utf-8")
        # 存入不记账：写不是"模型可见内容"事件（契约 v0.2 裁决5）
        return json.dumps({"ok": True, "saved": name, "bytes": len(content.encode())})
    if not sp.exists():
        return json.dumps({"ok": False, "error": f"snippet not found: {name}"})
    got = sp.read_text(encoding="utf-8")
    # 直取记账：实打实进过模型上下文的内容（契约 v0.2 裁决5）
    _ledger({"event": "threesome.workbench.snippet_get", "project": project,
             "name": name, "bytes": len(got.encode("utf-8"))})
    return got

@mcp.tool()
def workbench_list() -> str:
    """项目索引（agent视角，JSON）。人类视角看 INDEX.md。"""
    rows = _index_reload()
    _index_write()
    return json.dumps(rows, ensure_ascii=False, indent=1)

@mcp.tool()
def workbench_complete(project: str, note: str = "") -> str:
    """打完成勾。消化cron看见✅就把已消化部分整理进skill，清理工作台。"""
    p = _proj(project)
    (p / "DONE").write_text(f"completed {_now()} {note}".strip() + "\n", encoding="utf-8")
    _index_write()
    _ledger({"event": "threesome.workbench.complete", "project": project, "note": note})
    return json.dumps({"ok": True, "project": project, "note": "done — awaiting digestion cron"})


@mcp.tool()
def workbench_search(query: str, type: str = "", project: str = "", agent: str = "", limit: int = 20) -> str:
    """全文检索猎迹内容（FTS5）。搜索 journal/snippet/design/status/scratch。

    Args:
        query: 检索词。支持词、\"短语引号\"、OR/AND/NEAR。中文按字切分，连续短语用引号包住。
        type: 过滤——journal:坑 / journal:判断 / snippet / design / status / description / scratch
        project: 只搜该项目
        agent: 只搜该住户（具名模式盖过戳的行）
        limit: 最多返回几条
    """
    try:
        rows = spoor_search.search(query, type=type, project=project, agent=agent, limit=limit)
    except Exception as e:
        import sqlite3
        if isinstance(e, sqlite3.OperationalError):
            return json.dumps({"ok": False, "error": f"bad query syntax: {e}", "hint": 'phrase → "文件锁"  OR → 词1 OR 词2'})
        raise
    # 契约 v0.2: hits 记条数不记内容——内容在文件里，账本只记"发生过检索"
    _ledger({"event": "threesome.journal.search", "query": query, "type": type or None,
             "project": project or None, "agent": agent or None, "hits": len(rows)})
    if not rows:
        return "(no hits)"
    def _rel(p: str) -> str:
        # Windows 分隔符是 \，字符串 split('workbench/') 会失效——用 Path 语义
        try:
            return Path(p).relative_to(WB).as_posix()
        except ValueError:
            return p
    return "\n".join(
        f"[{r['type']}{'|' + r['agent'] if r['agent'] else ''}] {_rel(r['path'])}\n  {r['fragment']}"
        for r in rows
    )


@mcp.tool()
def ledger_query(kind: str = "", contains: str = "", agent: str = "", date: str = "", limit: int = 20, skip_recent: int = 0) -> str:
    """读账本（审计层）。倒序+过滤，纯顺序扫描，零依赖。

    账本不在 FTS 索引里（它是 access log 不是阅读层），这个工具是它唯一的读取通道。
    读取本身不记账——审计层的读取不污染审计层，access log 不记录"有人看了 access log"，
    否则每读一次多一行、自我放大。此决策入契约待审（照照 round 7）。

    Args:
        kind: 事件过滤。前缀匹配（"cleanup" 匹配所有 cleanup；"threesome." 匹配全契约域；空=不过滤）
        contains: 任意文本子串，对整条 JSON 行匹配（如 "export_marked"、"桡骨"）
        agent: 只看某住户的事件
        date: "2026-08-16" 当天 / "2026-08" 当月
        limit: 最多返回几条（从最新往回数）
        skip_recent: 跳过最新 N 条——翻页用
    """
    lines = []
    ledger_path = ROOT / "ledger.jsonl"
    if ledger_path.exists():
        with open(ledger_path, encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
    total = len(lines)

    def _match(obj: dict, raw: str) -> bool:
        if kind and not str(obj.get("event", obj.get("kind", ""))).startswith(kind):
            return False
        if agent and obj.get("agent") != agent:
            return False
        if date:
            ts = str(obj.get("ts", ""))
            if not ts.startswith(date):
                return False
        if contains and contains not in raw:
            return False
        return True

    hits = []
    skipped = 0
    for raw in reversed(lines):          # 倒序：最近的永远最常被查
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue                      # 脏行不炸，跳过并计数
        if not _match(obj, raw):
            continue
        if skip_recent and skipped < skip_recent:
            skipped += 1
            continue
        hits.append(raw.rstrip("\n"))
        if len(hits) >= limit:
            break

    head = f"(ledger {total} lines, {len(hits)} hits"
    if skip_recent:
        head += f", skipped {skip_recent}"
    head += ")"
    if not hits:
        return head + " — no match"
    return head + "\n" + "\n".join(hits)


@mcp.tool()
def workbench_reindex() -> str:
    """全量重建检索索引。schema 变更或怀疑索引脏时用。日常搜索自动增量，无需手动调。"""
    r = spoor_search.update_index(force=True)
    return json.dumps(r, ensure_ascii=False)


# 统一错误契约：包装所有已注册工具
def _json_safe(fn):
    import functools
    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})
    return wrapper

# (照照验证: FastMCP 1.28.1 无 mcp.tools 属性——原写法启动即AttributeError。
#  正确路径是 ToolManager._tools，替换 .fn 已实测可行：schema 正常、异常转JSON。)
_wrapped = 0
for _t in getattr(getattr(mcp, "_tool_manager", None), "_tools", {}).values():
    _t.fn = _json_safe(_t.fn)
    _wrapped += 1
# r14（zcode review）：包装零命中即启动失败——静默失效面必须在部署时暴露
if _wrapped == 0:
    raise RuntimeError(
        "错误契约包装零命中：FastMCP 内部结构已变更（_tool_manager._tools 不可用），"
        "工具异常将以非 JSON 形态抛出。请升级 session-spoor 或检查 mcp 版本兼容性。"
    )

if __name__ == "__main__":
    mcp.run()
