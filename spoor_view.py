#!/usr/bin/env python3
"""
spoor-view 桥 — 档案房只读 HTTP 窗口（给鸣鸣的前端 fetch 用）

设计边界（docs/frontend-bridge-spec.md）：
- 只读：GET only，零写操作。待办/待审在工作台文件里，agent 标，前端只展示。
- 单一事实源：直接挂 index.db（mode=ro）+ workbench/ 文件 + ledger.jsonl 尾部。
  故意不走 MCP archive_get：get 会往账本里灌浏览事件，把变更 feed 污染成浏览记录。
  桥自己永不写任何东西——浏览不产生历史。
- 记忆不出前端：不碰 portalk 记忆 MCP。

运行：~/Stigmergy/venv/bin/python ~/Stigmergy/spoor_view.py
地址：http://127.0.0.1:8765 （只绑 127.0.0.1）
"""

import json
import re
import sqlite3
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path("/home/ubuntu/Stigmergy")
DB = ROOT / "archive" / "index.db"
WB = ROOT / "workbench"
LEDGER = ROOT / "ledger.jsonl"
REPOS_FILE = ROOT / "workbench" / "repos.json"


def _db():
    # ro 模式：桥的存在不能改变档案房的物理状态
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def _latest(c, doc):
    """与 archive_server._latest_row 同算法：pin 优先，无 pin 取最后插入行。"""
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


def _doc_meta(c, row):
    doc, vid, parent, nbytes, source_ref, ts = row
    version_count = c.execute(
        "SELECT count(*) FROM versions WHERE doc=?", (doc,)
    ).fetchone()[0]
    pinned = c.execute(
        "SELECT 1 FROM pins WHERE doc=?", (doc,)
    ).fetchone()
    return {
        "doc": doc,
        "version_id": vid,
        "parent": parent,
        "bytes": nbytes,
        "source_ref": source_ref,
        "ts": ts,
        "versions": version_count,
        "pinned": bool(pinned),
    }


def archive_docs():
    c = _db()
    try:
        docs = [r[0] for r in c.execute(
            "SELECT DISTINCT doc FROM versions ORDER BY doc")]
        return [_doc_meta(c, _latest(c, d)) for d in docs]
    finally:
        c.close()


def archive_doc(doc):
    c = _db()
    try:
        latest = _latest(c, doc)
        if not latest:
            return None
        meta = _doc_meta(c, latest)
        chain = [
            {"version_id": r[0], "parent": r[1], "bytes": r[2], "ts": r[3]}
            for r in c.execute(
                "SELECT version_id, parent, bytes, ts FROM versions"
                " WHERE doc=? ORDER BY rowid", (doc,))
        ]
        out_links = [
            {"to": r[0], "relation": r[1], "ts": r[2]}
            for r in c.execute(
                "SELECT to_uri, relation, ts FROM links WHERE from_version=?",
                (meta["version_id"],))
        ]
        in_links = [
            {"from_version": r[0], "relation": r[1], "ts": r[2]}
            for r in c.execute(
                "SELECT from_version, relation, ts FROM links WHERE to_uri LIKE ?",
                (f"archive:{doc}@",))
        ]
        f = ROOT / "archive" / doc / f"{meta['version_id']}.md"
        content = f.read_text(encoding="utf-8") if f.exists() else ""
        return {
            **meta,
            "content": content,
            "versions_chain": chain,
            "out_links": out_links,
            "in_links": in_links,
        }
    finally:
        c.close()


def archive_graph():
    c = _db()
    try:
        docs = [r[0] for r in c.execute(
            "SELECT DISTINCT doc FROM versions ORDER BY doc")]
        nodes = []
        vid_to_doc = {}
        for d in docs:
            row = _latest(c, d)
            if not row:
                continue
            meta = _doc_meta(c, row)
            nodes.append(meta)
        for r in c.execute("SELECT doc, version_id FROM versions"):
            vid_to_doc[r[1]] = r[0]
        edges = []
        for r in c.execute(
                "SELECT from_version, to_uri, relation, ts FROM links"):
            fv, to_uri, rel, ts = r
            to_doc = None
            m = re.match(r"archive:([^@]+)@", to_uri)
            if m:
                to_doc = m.group(1)
            edges.append({
                "from_version": fv,
                "from_doc": vid_to_doc.get(fv, fv),
                "to": to_uri,
                "to_doc": to_doc,
                "external": to_doc is None,
                "relation": rel,
                "ts": ts,
            })
        return {"nodes": nodes, "edges": edges}
    finally:
        c.close()


# ---------- workbench（过程层） ----------

JOURNAL_LINE = re.compile(r"-\s*\*\*\[(.+?)\]\*\*\s*(.*)")
TS_PREFIX = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*(.*)")


def _parse_journal(path):
    entries = []
    if not path.exists():
        return entries
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = JOURNAL_LINE.match(line.strip())
        if not m:
            continue
        mark, rest = m.groups()
        tm = TS_PREFIX.match(rest.strip())
        if tm:
            ts, text = tm.groups()
            entries.append({"mark": mark.strip(), "ts": ts, "entry": text.strip()})
        else:
            entries.append({"mark": mark.strip(), "ts": "", "entry": rest.strip()})
    return entries


def _parse_status(path):
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    h = re.search(r"更新于\s*([0-9-]+ [0-9:]+)", text)
    updated = h.group(1) if h else ""
    pri = re.search(r"^优先级\s*[:：]\s*(.+)$", text, re.M)
    priority = pri.group(1).strip() if pri else ""
    sections = {}
    for key in ("做到哪", "下一步", "卡在哪"):
        m = re.search(
            rf"{key}[:：]?(.*?)(?=\n(?:下一步|卡在哪)[:：]?|\Z)", text, re.S)
        if m:
            sections[key] = m.group(1).strip()
    return {"updated": updated, "priority": priority, "sections": sections}


def _load_repos():
    # 桌名 → 仓库路径映射（声明式配置，每请求重读，改完不用重启）
    try:
        return json.loads(REPOS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _git(repo, *args):
    try:
        r = subprocess.run(
            ["git", "-c", f"safe.directory={repo}", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=5)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return -1, "", repr(e)


def git_state(repo):
    # 收据轴：本地 git 状态。只读，不 fetch——ahead 相对本地 remote-tracking。
    st = {"repo": str(repo), "branch": "", "dirty": 0, "ahead": None, "last_commit": ""}
    rc, out, _ = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if rc == 0:
        st["branch"] = out
    rc, out, _ = _git(repo, "status", "--porcelain")
    if rc == 0:
        st["dirty"] = len([l for l in out.splitlines() if l.strip()])
    rc, out, _ = _git(repo, "rev-list", "--count", "@{u}..HEAD")
    if rc == 0:
        try:
            st["ahead"] = int(out)
        except ValueError:
            pass
    rc, out, _ = _git(repo, "log", "-1", "--format=%ad %h %s", "--date=short")
    if rc == 0:
        st["last_commit"] = out
    return st


def _project(dirpath):
    status = _parse_status(dirpath / "STATUS.md")
    journal = []
    for jf in sorted(dirpath.glob("journal/*.md")):
        journal.extend(_parse_journal(jf))
    journal.sort(key=lambda e: e["ts"], reverse=True)
    dp = dirpath / "description.md"
    desc = dp.read_text(encoding="utf-8", errors="replace").strip() if dp.exists() else ""
    repo = _load_repos().get(dirpath.name)
    git = git_state(repo) if repo and Path(repo).is_dir() else None
    return {
        "project": dirpath.name,
        "description": desc,
        "status": status,
        "priority": (status["priority"] if status else ""),
        "git": git,
        "todo": (status["sections"].get("下一步", "") if status else ""),
        "blocked": (status["sections"].get("卡在哪", "") if status else ""),
        "pending_review": [e for e in journal if "待审" in e["mark"]],
        "pits": [e for e in journal if "坑" in e["mark"]],
        "journal": journal[:30],
        "journal_total": len(journal),
    }


def workbench_projects():
    projects = [_project(d) for d in sorted(WB.iterdir()) if d.is_dir()]
    projects.sort(
        key=lambda p: p["status"]["updated"] if p["status"] else "",
        reverse=True,
    )
    return projects


def workbench_project(name):
    d = WB / name
    if not d.is_dir():
        return None
    return _project(d)


# ---------- ledger（变更流） ----------

def ledger_tail(n=50):
    events = []
    if not LEDGER.exists():
        return events
    with LEDGER.open(encoding="utf-8", errors="replace") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                events.append(json.loads(ln))
            except json.JSONDecodeError:
                pass
    events.sort(key=lambda e: e.get("ts", ""), reverse=True)
    return events[:n]


# ---------- overview 聚合 ----------

def _needs_push(g):
    return bool(g) and (g["dirty"] > 0 or (g["ahead"] or 0) > 0 or g["ahead"] is None)


def _push_detail(g):
    bits = []
    if g["dirty"]:
        bits.append(f"{g['dirty']} 个未提交文件")
    if g["ahead"] is None:
        bits.append("无上游（从未推送或未设 upstream）")
    elif g["ahead"] > 0:
        bits.append(f"{g['ahead']} 个 commit 未推送")
    return "；".join(bits)


def overview():
    projects = workbench_projects()
    return {
        "server_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "projects": [
            {k: v for k, v in p.items() if k != "journal"} for p in projects
        ],
        "todos": [
            {"project": p["project"], "todo": p["todo"]}
            for p in projects if p["todo"]
        ],
        "pending_review": [
            {"project": p["project"], **e}
            for p in projects for e in p["pending_review"]
        ],
        "recent_events": ledger_tail(50),
        "push_queue": [
            {"project": p["project"], "git": p["git"]}
            for p in projects if _needs_push(p.get("git"))
        ],
        "needs_attention": [
            {"type": "review", "project": p["project"], "detail": e["entry"], "ts": e["ts"]}
            for p in projects for e in p["pending_review"]
        ] + [
            {"type": "push", "project": p["project"], "detail": _push_detail(p["git"]), "ts": ""}
            for p in projects if _needs_push(p.get("git"))
        ],
        "archive_docs": archive_docs(),
    }


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "spoor-view/0.1"

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path == "/api/overview":
                data = overview()
            elif path == "/api/projects":
                data = workbench_projects()
            elif path == "/api/graph":
                data = archive_graph()
            elif path == "/api/archive":
                data = archive_docs()
            elif path.startswith("/api/archive/"):
                doc = path[len("/api/archive/"):]
                data = archive_doc(urllib_parse_unquote(doc))
                if data is None:
                    self._json(404, {"error": f"doc not found: {doc}"})
                    return
            else:
                m = re.match(r"^/api/project/([^/]+)$", path)
                if m:
                    data = workbench_project(m.group(1))
                    if data is None:
                        self._json(404, {"error": f"project not found"})
                        return
                else:
                    self._json(404, {"error": "unknown route"})
                    return
            self._json(200, data)
        except Exception as e:
            self._json(500, {"error": repr(e)})

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[spoor-view] {self.address_string()} {fmt % args}", flush=True)


def urllib_parse_unquote(s):
    from urllib.parse import unquote
    return unquote(s)


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("[spoor-view] listening on http://127.0.0.1:8765", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
