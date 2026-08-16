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
CREATE TABLE IF NOT EXISTS links(
    from_version TEXT, to_uri TEXT, relation TEXT, ts TEXT
);
CREATE TABLE IF NOT EXISTS pins(
    doc TEXT PRIMARY KEY, version_id TEXT, reason TEXT, ts TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
    body, doc UNINDEXED, version_id UNINDEXED, tokenize='trigram'
);
"""
# round 9（照照裁决）：普通索引换唯一索引 (doc, version_id)——查重与插入两步之间的
# TOCTOU 窗口在多 agent 并发下会放进重复版本行。INSERT OR IGNORE + rowcount==0 → dedup。
# 迁移坑（照照随附）：唯一索引在已有重复行的旧库上建不起来（IntegrityError）——
# 先清重再建索引。清重保留每组 (doc, version_id) 的最早一行（MIN(rowid)），与
# "latest 现算指针取最后插入行"无冲突（重复行内容相同，谁存活都指同一内容）。
_MIGRATE_DEDUPE = (
    "DELETE FROM versions WHERE rowid NOT IN (SELECT MIN(rowid) FROM versions GROUP BY doc, version_id)",
    "DELETE FROM fts WHERE rowid NOT IN (SELECT MIN(rowid) FROM fts GROUP BY doc, version_id)",
    "DROP INDEX IF EXISTS ix_versions_doc",  # 被唯一索引的前缀覆盖，撤冗余
)
_UNIQUE_IX = "CREATE UNIQUE INDEX IF NOT EXISTS ux_versions_doc_vid ON versions(doc, version_id)"

_inited = False


# ---------- 内部 ----------


def _ledger(event: dict) -> None:
    spoor_common.append_ledger(event)


def _conn() -> sqlite3.Connection:
    global _inited
    AR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    if not _inited:
        c.executescript(_SCHEMA)
        for stmt in _MIGRATE_DEDUPE:
            c.execute(stmt)
        c.commit()
        try:
            c.execute(_UNIQUE_IX)
            c.commit()
        except sqlite3.IntegrityError as e:
            c.close()
            raise RuntimeError(
                f"archive index.db 存在未清干净的重复行，唯一索引建不起来。"
                f"手动清重后重启: DELETE FROM versions WHERE rowid NOT IN "
                f"(SELECT MIN(rowid) FROM versions GROUP BY doc, version_id); :: {e}"
            ) from e
        _inited = True
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
    """latest 是指针不是版本：显式 pin 优先；无 pin 现算（最后插入的一行）。不落盘指的是
    不为现算结果单写一行——pin 是用户显式声明的指针，落 pins 表。"""
    pin = c.execute("SELECT version_id FROM pins WHERE doc=?", (doc,)).fetchone()
    if pin:
        row = c.execute(
            "SELECT doc, version_id, parent, bytes, source_ref, ts FROM versions"
            " WHERE doc=? AND version_id=?", (doc, pin[0])
        ).fetchone()
        if row:
            return row
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
        if parent_version and not c.execute(
            "SELECT 1 FROM versions WHERE doc=? AND version_id=?", (doc, parent_version)
        ).fetchone():
            return json.dumps({"ok": False, "error": f"parent_version not found: {parent_version}"})
        # round 9（照照裁决）：查重→插入两步之间的 TOCTOU 窗口在多 agent 并发下放进重复版本行。
        # 修法=唯一索引 + INSERT OR IGNORE + rowcount==0 兜底 dedup——正确性不再依赖时序。
        # 文件先写（幂等：内容寻址，同内容同文件名，重复写无害），崩溃窗口只留孤儿文件不留缺文件行。
        fdir = AR / doc
        fdir.mkdir(parents=True, exist_ok=True)
        (fdir / f"{vid}.md").write_text(content, encoding="utf-8")
        cur = c.execute(
            "INSERT OR IGNORE INTO versions(doc, version_id, parent, bytes, source_ref, ts) VALUES (?,?,?,?,?,?)",
            (doc, vid, parent_version or "", b, source_ref or "", _now()),
        )
        dedup = (cur.rowcount == 0)
        if not dedup:
            c.execute(
                "INSERT INTO fts(body, doc, version_id) VALUES (?,?,?)", (content, doc, vid)
            )
        c.commit()
        # round 9（照照中等1）：dedup 命中时请求的 source_ref 不落库——静默丢弃是审计断链。
        # 回显 source_ref_dropped=true（只在真丢了时带这个键），溯源指针的去向可追溯。
        source_ref_dropped = False
        if dedup and source_ref:
            stored = c.execute(
                "SELECT source_ref FROM versions WHERE doc=? AND version_id=?", (doc, vid)
            ).fetchone()
            source_ref_dropped = not (stored and stored[0] == source_ref)
    finally:
        c.close()
    # 契约 v0.2：put 不记 entry_head（原则1，自毁条款第一次应用）。
    # dedup 也记账——账本记事件不记状态，put 发生过就是发生过。
    # dedup 字段进事件（round 9 增补）：两次 put 同 vid 且第二次 dedup=true = 并发竞态的审计铁证。
    resp = {"ok": True, "doc": doc, "version_id": vid,
            "dedup": dedup, "bytes": b}
    if source_ref_dropped:
        resp["source_ref_dropped"] = True
    _ledger({"event": "threesome.archive.put", "doc": doc, "version_id": vid,
             "parent_version": parent_version or None, "bytes": b, "source_ref": source_ref or None,
             "dedup": dedup})
    return json.dumps(resp, ensure_ascii=False)


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
        pin = c.execute("SELECT version_id FROM pins WHERE doc=?", (doc,)).fetchone()
        pinned_vid = pin[0] if pin else None
        out = [f"(doc {doc}: {len(rows)} versions, newest first)"]
        for vid, parent, b, source_ref, ts in rows:
            mark = "📌 " if vid == pinned_vid else ""
            line = f"  {mark}{vid} · {b}B · {ts} · parent {parent or '—'}"
            if source_ref:
                line += f" · src {source_ref}"
            out.append(line)
        return "\n".join(out)
    finally:
        c.close()


@mcp.tool()
def archive_link(from_version: str, to_uri: str, relation: str, doc: str = "") -> str:
    """建指针：档案版本 → 外部 URI（如 Tideline 记忆）。边界上只记账本，不搬内容。

    Args:
        from_version: 源版本号（内容哈希）
        to_uri: 目标 URI，原样记录（是指针不是内容）
        relation: 关系名（如 "same_story"）
        doc: 可选。同内容归入多个 doc 时（内容寻址的合法场景）用它精确锚定
             (doc, version_id) 二元组；不填则全表校验，命中多个 doc 时回显 docs 列表。
             link 本身挂在内容上不挂名义——同 vid 同内容，挂哪个 doc 名义下语义等价。
             （round 9 照照中等2：校验带 doc，语义对齐契约的二元组）
    """
    if not to_uri or not relation:
        return json.dumps({"ok": False, "error": "to_uri and relation are required"})
    c = _conn()
    try:
        if doc:
            if not c.execute(
                "SELECT 1 FROM versions WHERE doc=? AND version_id=?", (doc, from_version)
            ).fetchone():
                return json.dumps({"ok": False,
                                   "error": f"version {from_version} not found under doc {doc}"})
            docs = [doc]
        else:
            rows = c.execute(
                "SELECT DISTINCT doc FROM versions WHERE version_id=?", (from_version,)
            ).fetchall()
            if not rows:
                return json.dumps({"ok": False, "error": f"from_version not found: {from_version}"})
            docs = [r[0] for r in rows]
        c.execute("INSERT INTO links(from_version, to_uri, relation, ts) VALUES (?,?,?,?)",
                  (from_version, to_uri, relation, _now()))
        c.commit()
    finally:
        c.close()
    _ledger({"event": "threesome.archive.link", "from_version": from_version,
             "to_uri": to_uri, "relation": relation,
             "doc": doc or (docs[0] if len(docs) == 1 else None)})
    resp = {"ok": True, "from_version": from_version, "to_uri": to_uri}
    if len(docs) > 1:
        resp["docs"] = docs  # 歧义回显：同内容挂在多个 doc 名义下，审计时说清楚
    return json.dumps(resp, ensure_ascii=False)


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

@mcp.tool()
def archive_pin(doc: str, version_id: str, reason: str = "") -> str:
    """回退/锚定：把 doc 的 latest 指针显式钉到指定版本。

    场景：v3 实测不如 v2 → pin 回 v2。之后 get(不带 version_id) 解析到 v2，
    list 链上该版本带 📌。put 不受影响（仍可继续长新枝，但不动 pin——
    想让新版本成为 latest 就 unpin 或 pin 到新版本）。
    unpin = 撤销 pin，latest 回落现算（最后插入行）。

    Args:
        doc: 文档名
        version_id: 要钉住的版本号（必须已存在于该 doc 下）
        reason: 为什么钉（如"回退：v3系实测不稳"）——进账本，回退的历史证据
    """
    if not _valid_name(doc):
        return json.dumps({"ok": False, "error": f"invalid doc name: {doc!r}"})
    c = _conn()
    try:
        if not c.execute(
            "SELECT 1 FROM versions WHERE doc=? AND version_id=?", (doc, version_id)
        ).fetchone():
            return json.dumps({"ok": False,
                               "error": f"version not found: {doc}@{version_id}"})
        prev = _latest_row(c, doc)
        previous = prev[1] if prev else None
        c.execute(
            "INSERT INTO pins(doc, version_id, reason, ts) VALUES(?,?,?,?)"
            " ON CONFLICT(doc) DO UPDATE SET version_id=excluded.version_id,"
            " reason=excluded.reason, ts=excluded.ts",
            (doc, version_id, reason or "", _now()),
        )
        c.commit()
        _ledger({"event": "threesome.archive.pin", "doc": doc, "version_id": version_id,
                 "previous": previous, "reason": reason or None})
        return json.dumps({"ok": True, "doc": doc, "version_id": version_id,
                           "previous": previous, "reason": reason or None})
    finally:
        c.close()


@mcp.tool()
def archive_unpin(doc: str, reason: str = "") -> str:
    """撤销 pin：latest 回落现算（最后插入行）。没钉过也能调——幂等，返回当前现算值。

    Args:
        doc: 文档名
        reason: 为什么撤销（进账本）
    """
    if not _valid_name(doc):
        return json.dumps({"ok": False, "error": f"invalid doc name: {doc!r}"})
    c = _conn()
    try:
        if not c.execute("SELECT 1 FROM versions WHERE doc=?", (doc,)).fetchone():
            return json.dumps({"ok": False, "error": f"no versions: {doc}"})
        had_pin = c.execute("SELECT version_id FROM pins WHERE doc=?", (doc,)).fetchone()
        if had_pin:
            c.execute("DELETE FROM pins WHERE doc=?", (doc,))
            c.commit()
        prev = _latest_row(c, doc)
        current = prev[1] if prev else None
        _ledger({"event": "threesome.archive.unpin", "doc": doc,
                 "unpinned": had_pin[0] if had_pin else None,
                 "current_latest": current, "reason": reason or None})
        return json.dumps({"ok": True, "doc": doc,
                           "unpinned": had_pin[0] if had_pin else None,
                           "current_latest": current})
    finally:
        c.close()


for _t in getattr(getattr(mcp, "_tool_manager", None), "_tools", {}).values():
    _t.fn = _json_safe(_t.fn)

if __name__ == "__main__":
    mcp.run()
