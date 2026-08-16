#!/usr/bin/env python3
"""
session-spoor MCP — 猎迹
涂鸦房(scratch) MCP server v0.2


Dying Will (dependency declaration — forces one ppid thought at write time):
  Who launched me: the gateway, as its stdio child process (Hermes config, mcp section).
  What happens if you kill me: the gateway's stdio channel breaks; MCP calls hang.
  Nobody revives me — only a gateway restart brings me back. Always check ppid before kill.
  Patch activation: code changes require a gateway restart (patch-on-disk != running process).

生命周期绑任务的临时工作空间：
- 编排层 spawn 分身时 scratchpad_create
- 分身执行中 write / mark 自由使用
- 结束时 status -> cleanup（导出/账本/蒸发三去向）

存储：目录树（{root}/scratch/{space_id}/）
账本：{root}/ledger.jsonl 追加写
"""

import json
import os
import shutil
import time
import uuid
from pathlib import Path, PureWindowsPath

# FastMCP
from mcp.server.fastmcp import FastMCP

import spoor_common

mcp = FastMCP("stigmergy-scratchpad")

ROOT = Path(os.environ.get("STIGMERGY_ROOT", str(Path.home() / "Stigmergy")))
SCRATCH = ROOT / "scratch"
LEDGER = ROOT / "ledger.jsonl"

MARKS = {"判断", "数据", "坑", "待审·自", "待审·人"}
MAX_SPACE_BYTES = 64 * 1024 * 1024  # 单空间64MB，超限报错不淘汰

SCRATCH.mkdir(parents=True, exist_ok=True)


# ---------- 内部 ----------

def _ledger(event: dict) -> None:
    spoor_common.append_ledger(event)


def _space_path(space_id: str) -> Path:
    # space_id 只允许 [A-Za-z0-9_\-]，防路径逃逸
    if not space_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in space_id):
        raise ValueError(f"invalid space_id: {space_id!r}")
    p = SCRATCH / space_id
    if not p.is_dir():
        raise FileNotFoundError(f"space not found: {space_id} (created? cleaned?)")
    return p


def _safe_rel(path: str) -> Path:
    rel = Path(path)
    # Windows 陷阱（r13/zcode）：Path("/etc/x").is_absolute() 在 nt 语义下 False（有根无盘符），
    # 之后 base / rel 的 join 语义丢弃整个 base → 读写双向逃逸出沙箱。
    # 双视角检查：本机 Path 只按本平台语义解析，PureWindowsPath 补上 Windows 视角——
    # POSIX 写入的 "C:/x" 在本机是字面量目录，同步到 Windows 读取端即成盘符逃逸向量。
    win = PureWindowsPath(path)
    if (rel.is_absolute() or rel.drive or rel.root or ".." in rel.parts
            or win.drive or win.root or ".." in win.parts):
        raise ValueError(f"path must be relative inside space: {path!r}")
    return rel


def _marks_path(space: Path, target: Path) -> Path:
    return space / ".marks" / (target.as_posix() + ".marks.json")


def _load_marks(space: Path, target: Path) -> dict:
    mp = _marks_path(space, target)
    if mp.exists():
        return json.loads(mp.read_text(encoding="utf-8"))
    return {"marks": [], "exported": False}


def _save_marks(space: Path, target: Path, data: dict) -> None:
    mp = _marks_path(space, target)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _space_size(space: Path) -> int:
    total = 0
    for f in space.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
    return total


def _bundle_md(space: Path, files: list[Path]) -> str:
    """Markdown bundle：mark过的在前（判断优先于数据），数据在后。"""
    out = [f"# Export from scratch space: {space.name}", ""]
    # 排序键：判断 < 待审·自 < 待审·人 < 坑 < 数据（认知在前，人审路由明确）
    RANK = {"判断": 0, "待审·自": 1, "待审·人": 2, "坑": 3, "数据": 4}
    ranked = []
    for f in files:
        m = _load_marks(space, f.relative_to(space))
        best = min((RANK[mk] for mk in m["marks"] if mk in RANK), default=99)
        ranked.append((best, f.relative_to(space).as_posix(), m, f))
    ranked.sort(key=lambda x: x[0])
    for _, relname, m, f in ranked:
        marks = "/".join(m["marks"]) if m["marks"] else "-"
        out.append(f"## {relname}  [marks: {marks}]")
        out.append("")
        try:
            out.append(f.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            out.append(f"(binary file, {f.stat().st_size} bytes)")
        out.append("")
    return "\n".join(out)



def _json_safe(fn):
    """错误契约统一：所有工具异常转JSON，编排层（程序）做解析不会炸。"""
    import functools
    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})
    return wrapper

# ---------- MCP 工具 ----------

@mcp.tool()
def scratchpad_create(task_id: str, label: str = "") -> str:
    """创建临时工作空间。调用方：编排层 spawn 分身时。

    Args:
        task_id: 任务标识（用于space_id前缀和账本溯源）
        label: 可选人类可读标签
    Returns:
        space_id（JSON字符串，含 space_id/scratch_dir）
    """
    safe_id = "".join(c for c in task_id if c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" ) or "task"
    space_id = f"{safe_id}-{uuid.uuid4().hex[:8]}"
    sp = SCRATCH / space_id
    sp.mkdir(parents=True)
    _ledger({"event": "create", "space": space_id, "task": task_id, "safe_id": safe_id, "label": label})
    return json.dumps({"space_id": space_id, "scratch_dir": str(sp)}, ensure_ascii=False)


@mcp.tool()
def scratchpad_write(space_id: str, path: str, content: str, mode: str = "overwrite") -> str:
    """写/追加文件。path 是空间内相对路径。

    Args:
        mode: overwrite | append
    """
    space = _space_path(space_id)
    rel = _safe_rel(path)
    if _space_size(space) + len(content.encode()) > MAX_SPACE_BYTES:
        return json.dumps({"ok": False, "error": "space quota exceeded (64MB). clean up before writing more."})
    target = space / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if mode == "append" and target.exists():
        with open(target, "a", encoding="utf-8") as f:
            f.write(content)
    else:
        target.write_text(content, encoding="utf-8")
    return json.dumps({"ok": True, "path": rel.as_posix(), "bytes": len(content.encode())})


@mcp.tool()
def scratchpad_read(space_id: str, path: str, offset: int = 0, limit: int = 2000) -> str:
    """读文件。分身读回自己的痕迹。"""
    space = _space_path(space_id)
    rel = _safe_rel(path)
    target = space / rel
    if not target.is_file():
        return json.dumps({"ok": False, "error": f"not found: {path}"})
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    picked = lines[offset : offset + limit]
    return "\n".join(f"{offset + i + 1}|{ln}" for i, ln in enumerate(picked))


@mcp.tool()
def scratchpad_list(space_id: str, path: str = "") -> str:
    """列空间内文件树（含每文件marks）。"""
    space = _space_path(space_id)
    base = space / _safe_rel(path) if path else space
    entries = []
    for f in sorted(base.rglob("*")):
        if ".marks" in f.parts:
            continue
        if f.is_file():
            m = _load_marks(space, f.relative_to(space))
            entries.append({
                "file": f.relative_to(space).as_posix(),
                "bytes": f.stat().st_size,
                "marks": m["marks"],
                "exported": m["exported"],
            })
    return json.dumps(entries, ensure_ascii=False, indent=1)


@mcp.tool()
def scratchpad_mark(space_id: str, path: str, mark: str) -> str:
    """给文件打标记。词汇表：判断 / 数据 / 坑 / 待审·自 / 待审·人（两个法官要分开：自=主agent消化，人=人类拍板）
    分身执行中给自己留的判断用 mark 标，回流压缩器优先读 mark 过的。"""
    if mark not in MARKS:
        return json.dumps({"ok": False, "error": f"mark must be one of {sorted(MARKS)}"})
    space = _space_path(space_id)
    rel = _safe_rel(path)
    target = space / rel
    if not target.exists():
        return json.dumps({"ok": False, "error": f"not found: {path}"})
    m = _load_marks(space, rel)
    if mark not in m["marks"]:
        m["marks"].append(mark)
        _save_marks(space, rel, m)
    return json.dumps({"ok": True, "path": rel.as_posix(), "marks": m["marks"]})


@mcp.tool()
def scratchpad_export(space_id: str, selection: str, dest: str) -> str:
    """导出。selection: 文件列表(JSON数组) 或 'marked'。
    dest: 目标md文件路径（相对 STIGMERGY_ROOT 或绝对路径）。
    导出即打标——导出过的文件标记 exported，不重复导出。"""
    space = _space_path(space_id)
    if selection == "marked":
        files = [f for f in sorted(space.rglob("*"))
                 if f.is_file() and ".marks" not in f.parts and _load_marks(space, f.relative_to(space))["marks"]]
    else:
        names = json.loads(selection)
        files = [space / _safe_rel(n) for n in names]
        for f in files:
            if not f.exists():
                return json.dumps({"ok": False, "error": f"not found: {f}"})
    # dest校验先行——即使nothing new也不允许探测任意路径
    # (照照二轮验证: 相对路径拼接后必须再resolve校验，否则 exports/../../.. 可逃出ROOT)
    dest_p = Path(dest)
    if not dest_p.is_absolute():
        dest_p = ROOT / dest_p
    try:
        dest_p = dest_p.resolve()
        dest_p.relative_to(ROOT.resolve())
    except ValueError:
        return json.dumps({"ok": False, "error": f"dest must stay under {ROOT}"})
    # 过滤已导出的
    fresh = []
    for f in files:
        m = _load_marks(space, f.relative_to(space))
        if not m["exported"]:
            fresh.append(f)
    if not fresh:
        return json.dumps({"ok": True, "exported": 0, "note": "nothing new (all already exported)"})
    dest_p.parent.mkdir(parents=True, exist_ok=True)
    bundle = _bundle_md(space, fresh)
    dest_p.write_text(bundle, encoding="utf-8")
    for f in fresh:
        m = _load_marks(space, f.relative_to(space))
        m["exported"] = True
        _save_marks(space, f.relative_to(space), m)
    _ledger({"event": "export", "space": space_id, "files": [str(f) for f in fresh],
             "dest": str(dest_p), "bytes": len(bundle.encode())})
    return json.dumps({"ok": True, "exported": len(fresh), "dest": str(dest_p)})


@mcp.tool()
def scratchpad_status(space_id: str) -> str:
    """状态查询。编排层在分身结束时调，决定导出什么。"""
    space = _space_path(space_id)
    files = [f for f in sorted(space.rglob("*"))
             if f.is_file() and ".marks" not in f.parts]
    by_mark = {}
    marked_count = 0
    for f in files:
        m = _load_marks(space, f.relative_to(space))
        if m["marks"]:
            marked_count += 1
        for mk in m["marks"]:
            by_mark[mk] = by_mark.get(mk, 0) + 1
    age_h = (time.time() - space.stat().st_mtime) / 3600
    return json.dumps({
        "space": space_id,
        "files": len(files),
        "size_bytes": _space_size(space),
        "marked_files": marked_count,
        "by_mark": by_mark,
        "exported_any": any(_load_marks(space, f.relative_to(space))["exported"] for f in files) if files else False,
        "age_hours": round(age_h, 1),
    }, ensure_ascii=False)


@mcp.tool()
def scratchpad_cleanup(space_id: str, mode: str = "export_marked", dest: str = "") -> str:
    """清理。执行：先按 mode 导出 → 写清理事件进账本 → 删除空间。
    幂等：已清理的空间再次 cleanup 是 no-op。

    Args:
        mode: export_all | export_marked | discard
        dest: 导出目标（mode 非 discard 时必填）
    """
    try:
        space = _space_path(space_id)
    except FileNotFoundError:
        return json.dumps({"ok": True, "note": "already cleaned (no-op)"})
    exported = 0
    if mode in ("export_all", "export_marked"):
        if not dest:
            return json.dumps({"ok": False, "error": "dest required for export modes"})
        sel = "marked" if mode == "export_marked" else json.dumps(
            [f.relative_to(space).as_posix() for f in sorted(space.rglob("*"))
             if f.is_file() and ".marks" not in f.parts])
        r = json.loads(scratchpad_export(space_id, sel, dest))
        exported = r.get("exported", 0)
    stats = json.loads(scratchpad_status(space_id))
    shutil.rmtree(space)
    _ledger({"event": "cleanup", "space": space_id, "mode": mode,
             "exported": exported, "final_stats": stats})
    return json.dumps({"ok": True, "space": space_id, "mode": mode, "exported": exported})


# 统一错误契约：包装所有已注册工具
# (照照验证: FastMCP 1.28.1 无 mcp.tools 属性——原写法启动即AttributeError。
#  正确路径是 ToolManager._tools，但更稳的是替换 .fn，两处均已实测可行。
#  这里用装饰器内层套法替代——见各工具定义处 @_json_safe)
for _t in getattr(getattr(mcp, "_tool_manager", None), "_tools", {}).values():
    _t.fn = _json_safe(_t.fn)

if __name__ == "__main__":
    mcp.run()
