#!/usr/bin/env python3
"""
Stigmergy MCP — 迹廊
工作台(workbench) MCP server v0.1

主agent的常驻过程层：项目索引/状态桌面/记录条/复用件架。
联邦式：与涂鸦房共享mark词汇表与接口语义，存储独立。

存储：{root}/workbench/{project}/
索引：{root}/workbench/INDEX.md
"""

import json
import os
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("stigmergy-workbench")

ROOT = Path(os.environ.get("STIGMERGY_ROOT", str(Path.home() / "Stigmergy")))
WB = ROOT / "workbench"
INDEX = WB / "INDEX.md"

MARKS = {"判断", "数据", "坑", "待审·自", "待审·人"}

WB.mkdir(parents=True, exist_ok=True)


# ---------- 内部 ----------


LEDGER = ROOT / "ledger.jsonl"

def _ledger(event: dict) -> None:
    import time as _t
    event["ts"] = _t.strftime("%Y-%m-%dT%H:%M:%S", _t.localtime())
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

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
    if rel.is_absolute() or ".." in rel.parts:
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
        lines.append(f"| {r['project']} | {(r['desc'] or '-').replace(chr(124), chr(124)*2)} | {status} | {r['touched']} |")
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
    _ledger({"event": "wb_new", "project": project, "desc": description})
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
    line = f"- **[{mark}]** {_now()} {entry}"
    if jf.exists():
        content = jf.read_text(encoding="utf-8")
    else:
        content = f"# {day}\n"
    content += line + "\n"
    jf.write_text(content, encoding="utf-8")
    _index_write()
    return json.dumps({"ok": True, "journal": str(jf.name), "mark": mark})

@mcp.tool()
def workbench_read_journal(project: str, mark: str = "", limit: int = 30) -> str:
    """读记录条。可按mark过滤。
    开工仪式：新session接手项目先读 mark=坑 的。"""
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
        return json.dumps({"ok": True, "saved": name, "bytes": len(content.encode())})
    if not sp.exists():
        return json.dumps({"ok": False, "error": f"snippet not found: {name}"})
    return sp.read_text(encoding="utf-8")

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
    _ledger({"event": "wb_complete", "project": project, "note": note})
    return json.dumps({"ok": True, "project": project, "note": "done — awaiting digestion cron"})


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

for _name in list(mcp.tools.keys()):
    mcp.tools[_name] = _json_safe(mcp.tools[_name])

if __name__ == "__main__":
    mcp.run()
