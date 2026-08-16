#!/usr/bin/env python3
"""
session-spoor MCP — 猎迹
档案房(archive) MCP server v0.2 —— 契约 contract-archive-events-draft.md v0.2


Dying Will (dependency declaration — forces one ppid thought at write time):
  Who launched me: the gateway, as its stdio child process (Hermes config, mcp section).
  What happens if you kill me: the gateway's stdio channel breaks; MCP calls hang.
  Nobody revives me — only a gateway restart brings me back. Always check ppid before kill.
  Patch activation: code changes require a gateway restart (patch-on-disk != running process).

主agent的永久层：版本化归档。档案不改不删，只追加——修复也是新版本，
账本里永远看得到走过弯路。

契约要点（照照 round 7 裁决后 v0.2）：
- put 不记 entry_head（永久层，存在性论证不成立——自毁条款第一次应用）
- get/query 记账（内容进过模型上下文）；list 不记账（地址导航——总则不变量：
  "记账的分界是这次调用让模型看见了什么内容"）
- source_ref 可选自由指针：涂鸦房直归档不填，毕业路径归档填
- 版本模型：version_id = sha256(content) 前12位，内容寻址；parent DAG；
  latest 是指针不是版本（每次现算，不落盘）
- 存储不用 STRICT/RETURNING（便利不抬门槛，append-only 无 UPDATE 场景）

存储：
  内容   {root}/archive/{doc}/{version_id}.md   （不可变，内容寻址）
  版本表 {root}/archive/index.db                （SQLite: versions / links / FTS）
"""

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

import spoor_common

mcp = FastMCP("stigmergy-archive")

ROOT = Path(os.environ.get("STIGMERGY_ROOT", str(Path.home() / "Stigmergy")))
AR = ROOT / "archive"
DB = AR / "index.db"

AR.mkdir(parents=True, exist_ok=True)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS versions(
    doc TEXT, version_id TEXT, parent TEXT, bytes INTEGER, source_ref TEXT, ts TEXT
);
CREATE INDEX IF NOT EXISTS ix_versions_doc ON versions(doc);
CREATE TABLE IF NOT EXISTS links(
    from_version TEXT, to_uri TEXT, relation TEXT, ts TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
    body, doc UNINDEXED, version_id UNINDEXED, tokenize='trigram'
);
"""


# ---------- 内部 ----------


def _ledger(event: dict) -> None:
    spoor_common.append_ledger(event)


def _conn() -> sqlite3.Connection:
    AR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.executescript(_SCHEMA)
    return c


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


def _valid_name(name: str) -> bool:
    return bool(name) and all(
        c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in name
    )


def _vid(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]


def _latest_row(c: sqlite3.Connection, doc: str):
    """latest 是指针不是版本：现算（最后插入的一行），不落盘。"""
    return c.execute(
        "SELECT doc, version_id, parent, bytes, source_ref, ts FROM versions"
        " WHERE doc=? ORDER BY rowid DESC LIMIT 1", (doc,)
    ).fetchone()


# ---------- MCP 工具 ----------


@mcp.tool()
def archive_put(doc: str, content: str, parent_version: str = "", source_ref: str = "") -> str:
    """归档一个版本（内容寻址，append-only）。

    Args:
        doc: 文档名（字母数字-_，如 "hongxinshe-worldbuilding"）
        content: 版本全文
        parent_version: 父版本号（DAG 链）。空 = 根版本
        source_ref: 可选自由指针——毕业路径归档填（如 ledger:export:行号 或 export 的 dest 路径），
                    涂鸦房直归档不填。账本管发生过什么，source_ref 管"这两个事件是同一件事"
    """
    if not _valid_name(doc):
        return json.dumps({"ok": False, "error": f"invalid doc name: {doc!r} ([a-zA-Z0-9_-])"})
    vid = _vid(content)
    b = len(content.encode("utf-8"))
    c = _conn()
    try:
        existed = c.execute(
            "SELECT 1 FROM versions WHERE doc=? AND version_id=?", (doc, vid)
        ).fetchone()
        dedup = bool(existed)
        if not dedup:
            if parent_version and not c.execute(
                "SELECT 1 FROM versions WHERE doc=? AND version_id=?", (doc, parent_version)
            ).fetchone():
                return json.dumps({"ok": False, "error": f"parent_version not found: {parent_version}"})
            # 内容文件：不可变，只在首次出现时写
            fdir = AR / doc
            fdir.mkdir(parents=True, exist_ok=True)
            (fdir / f"{vid}.md").write_text(content, encoding="utf-8")
            c.execute(
                "INSERT INTO versions(doc, version_id, parent, bytes, source_ref, ts) VALUES (?,?,?,?,?,?)",
                (doc, vid, parent_version or "", b, source_ref or "", _now()),
            )
            c.execute(
                "INSERT INTO fts(body, doc, version_id) VALUES (?,?,?)", (content, doc, vid)
            )
            c.commit()
    finally:
        c.close()
    # 契约 v0.2：put 不记 entry_head（原则1，自毁条款第一次应用）。
    # dedup 也记账——账本记事件不记状态，put 发生过就是发生过。
    _ledger({"event": "threesome.archive.put", "doc": doc, "version_id": vid,
             "parent_version": parent_version or None, "bytes": b, "source_ref": source_ref or None})
    return json.dumps({"ok": True, "doc": doc, "version_id": vid,
                       "dedup": dedup, "bytes": b}, ensure_ascii=False)


@mcp.tool()
def archive_get(doc: str, version_id: str = "", reason: str = "") -> str:
    """取版本全文。version_id 空 = latest（现算指针）。reason 进账本（如"考古"/"复用"），可空。"""
    if not _valid_name(doc):
        return json.dumps({"ok": False, "error": f"invalid doc name: {doc!r}"})
    c = _conn()
    try:
        if version_id:
            row = c.execute(
                "SELECT version_id, parent, bytes, source_ref, ts FROM versions"
                " WHERE doc=? AND version_id=?", (doc, version_id)
            ).fetchone()
        else:
            r2 = _latest_row(c, doc)
            row = r2[1:] if r2 else None
        if not row:
            return json.dumps({"ok": False,
                               "error": f"version not found: {doc}@{version_id or 'latest'}"})
        vid, parent, b, source_ref, ts = row
    finally:
        c.close()
    fp = AR / doc / f"{vid}.md"
    if not fp.exists():
        return json.dumps({"ok": False, "error": f"content file missing: {doc}@{vid}"})
    content = fp.read_text(encoding="utf-8")
    _ledger({"event": "threesome.archive.get", "doc": doc, "version_id": vid,
             "bytes": b, "reason": reason or None})
    head = f"(archive {doc} @ {vid} · {b}B · parent {parent or '—'} · {ts}"
    if source_ref:
        head += f" · source_ref {source_ref}"
    return head + ")\n\n" + content


@mcp.tool()
def archive_list(doc: str = "") -> str:
    """地址导航。doc 空 = 列所有文档（含版本数/latest）；doc 给定 = 列该文档版本链。

    不记账——地址不是内容（契约总则不变量：list/search/INDEX 全走"结构不记、内容记"）。
    """
    c = _conn()
    try:
        if not doc:
            rows = c.execute(
                "SELECT doc, version_id, bytes, ts FROM versions ORDER BY rowid"
            ).fetchall()
            if not rows:
                return "(archive empty)"
            latest: dict = {}
            count: dict = {}
            for d, vid, b, ts in rows:
                latest[d] = f"{vid} · {ts}"
                count[d] = count.get(d, 0) + 1
            out = [f"(archive: {len(count)} docs)"]
            for d in sorted(count):
                out.append(f"  {d} — {count[d]} versions · latest {latest[d]}")
            return "\n".join(out)
        if not _valid_name(doc):
            return json.dumps({"ok": False, "error": f"invalid doc name: {doc!r}"})
        rows = c.execute(
            "SELECT version_id, parent, bytes, source_ref, ts FROM versions"
            " WHERE doc=? ORDER BY rowid DESC", (doc,)
        ).fetchall()
        if not rows:
            return f"(no versions: {doc})"
        out = [f"(doc {doc}: {len(rows)} versions, newest first)"]
        for vid, parent, b, source_ref, ts in rows:
            line = f"  {vid} · {b}B · {ts} · parent {parent or '—'}"
            if source_ref:
                line += f" · src {source_ref}"
            out.append(line)
        return "\n".join(out)
    finally:
        c.close()


@mcp.tool()
def archive_link(from_version: str, to_uri: str, relation: str) -> str:
    """建指针：档案版本 → 外部 URI（如 Tideline 记忆）。边界上只记账本，不搬内容。"""
    if not to_uri or not relation:
        return json.dumps({"ok": False, "error": "to_uri and relation are required"})
    c = _conn()
    try:
        if not c.execute("SELECT 1 FROM versions WHERE version_id=?", (from_version,)).fetchone():
            return json.dumps({"ok": False, "error": f"from_version not found: {from_version}"})
        c.execute("INSERT INTO links(from_version, to_uri, relation, ts) VALUES (?,?,?,?)",
                  (from_version, to_uri, relation, _now()))
        c.commit()
    finally:
        c.close()
    _ledger({"event": "threesome.archive.link", "from_version": from_version,
             "to_uri": to_uri, "relation": relation})
    return json.dumps({"ok": True, "from_version": from_version, "to_uri": to_uri}, ensure_ascii=False)


@mcp.tool()
def archive_query(query: str, limit: int = 20) -> str:
    """FTS5 检索档案全文（trigram，≥3字）。记条数不记内容（同 journal.search 先例）。"""
    q = query.strip()
    if len(q) < 3:
        return "(trigram needs >=3 chars)"
    c = _conn()
    try:
        rows = c.execute(
            "SELECT doc, version_id, snippet(fts, 0, '→', '←', '…', 48)"
            " FROM fts WHERE fts MATCH ? ORDER BY rank LIMIT ?", (q, limit)
        ).fetchall()
    finally:
        c.close()
    _ledger({"event": "threesome.archive.query", "query": query, "hits": len(rows)})
    if not rows:
        return "(no hits)"
    return "\n".join(f"[{d}] {v}\n  {frag}" for d, v, frag in rows)


# 统一错误契约：包装所有已注册工具（与 workbench_server 同款，照照验证路径）
def _json_safe(fn):
    import functools
    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})
    return wrapper

for _t in getattr(getattr(mcp, "_tool_manager", None), "_tools", {}).values():
    _t.fn = _json_safe(_t.fn)

if __name__ == "__main__":
    mcp.run()
