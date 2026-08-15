"""
FTS5 全文检索层：猎迹的内容可被找到。

设计：
- 索引库 {root}/.search/index.db（SQLite FTS5），gitignore（可随时重建）
- 索引对象：workbench journal 行 / snippet 文件 / scratch 文件 / description / STATUS
- 每条文档带 type/project/path/agent 四个元数据列，搜索可按维度过滤
- 增量：按 (path, mtime) 记忆——mtime 没变的不重新解析
- 中文：trigram 分词器（SQLite 3.34+ 内置）——3字及以上子串直接命中，
  大小写不敏感。2字词是盲区（trigram 最小粒度3），中文关键词大多≥3字，
  2字词用 type/project 维度过滤辅助。零外部依赖，jieba 版留作后续。

对外工具：
- workbench_search(query, type="", project="", agent="", limit=20)
  → 命中行列表（文件路径 + 高亮片段 + 元数据）
- workbench_reindex() → 全量重建（schema 变更/怀疑索引脏时用）
"""
import json
import os
import re
import sqlite3
import time
from pathlib import Path

ROOT = Path(os.environ.get("STIGMERGY_ROOT", str(Path.home() / "Stigmergy")))
DB = ROOT / ".search" / "index.db"

_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(
    body, path UNINDEXED, type UNINDEXED, project UNINDEXED, agent UNINDEXED,
    tokenize='trigram'
);
CREATE TABLE IF NOT EXISTS file_state (
    path TEXT PRIMARY KEY, mtime REAL
);
"""


def _conn() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.executescript(_SCHEMA)
    return c


# ---------- 解析：从文件提取带元数据的文档 ----------

_JOURNAL_LINE = re.compile(r"^- \*\*\[(.+?)\]\*\*\s+(\((.+?)\)\s+)?(\S+ .+)$")


def _parse_journal(path: Path, project: str) -> list[dict]:
    """journal 行级索引：每条记录一个文档（mark/agent 从行内解析）。"""
    docs = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        m = _JOURNAL_LINE.match(ln)
        if not m:
            continue
        mark, agent = m.group(1), (m.group(3) or "")
        docs.append({"body": ln, "path": str(path), "type": f"journal:{mark}", "project": project, "agent": agent})
    return docs


def _plain_file(path: Path, type_: str, project: str) -> list[dict]:
    body = path.read_text(encoding="utf-8", errors="replace")
    return [{"body": body, "path": str(path), "type": type_, "project": project, "agent": ""}]


def collect_docs() -> list[dict]:
    """扫描全部可索引内容。workbench 是主对象；scratch 存在也收。"""
    docs: list[dict] = []
    wb = ROOT / "workbench"
    if wb.is_dir():
        for p_dir in sorted(wb.iterdir()):
            if not p_dir.is_dir():
                continue
            proj = p_dir.name
            jdir = p_dir / "journal"
            if jdir.is_dir():
                for jf in sorted(jdir.glob("*.md")):
                    docs += _parse_journal(jf, proj)
            for sub, type_ in [("snippets", "snippet"), ("design", "design")]:
                sdir = p_dir / sub
                if sdir.is_dir():
                    for sf in sorted(sdir.rglob("*")):
                        if sf.is_file():
                            docs += _plain_file(sf, type_, proj)
            for fname, type_ in [("description.md", "description"), ("STATUS.md", "status")]:
                fp = p_dir / fname
                if fp.exists():
                    docs += _plain_file(fp, type_, proj)
    sc = ROOT / "scratch"
    if sc.is_dir():
        for sf in sorted(sc.rglob("*")):
            if sf.is_file() and sf.suffix in (".md", ".txt", ".json", ".py", ".yaml", ".yml"):
                docs += _plain_file(sf, "scratch", "")
    return docs


# ---------- 增量维护 ----------

def _file_key(docs_by_file: dict, path: str) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


def update_index(force: bool = False) -> dict:
    """增量重建：新/变更文件重解析，删除文件清条目。force=True 全量重来。"""
    c = _conn()
    try:
        if force:
            c.executescript("DROP TABLE IF EXISTS docs; DROP TABLE IF EXISTS file_state;")
            c.executescript(_SCHEMA)
        known = {r[0]: r[1] for r in c.execute("SELECT path, mtime FROM file_state")}
        grouped: dict[str, list[dict]] = {}
        for d in collect_docs():
            grouped.setdefault(d["path"], []).append(d)
        files = grouped.keys()
        rescanned = 0
        for f in files:
            mt = os.stat(f).st_mtime if os.path.exists(f) else 0.0
            if force or known.get(f) != mt:
                rescanned += 1
                c.execute("DELETE FROM docs WHERE path=?", (f,))
                for d in grouped[f]:
                    c.execute(
                        "INSERT INTO docs(body, path, type, project, agent) VALUES (?,?,?,?,?)",
                        (d["body"], d["path"], d["type"], d["project"], d["agent"]),
                    )
                c.execute(
                    "INSERT OR REPLACE INTO file_state(path, mtime) VALUES (?,?)", (f, mt)
                )
        gone = set(known) - set(files)
        for f in gone:
            c.execute("DELETE FROM docs WHERE path=?", (f,))
            c.execute("DELETE FROM file_state WHERE path=?", (f,))
        c.commit()
        n = c.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        return {"ok": True, "docs": n, "rescanned": rescanned}
    finally:
        c.close()


def search(query: str, type: str = "", project: str = "", agent: str = "", limit: int = 20) -> list[dict]:
    """FTS5 MATCH 查询。query 语法：词 / 短语引号 / OR / AND / NEAR。
    过滤维度：type（如 journal:坑）、project、agent（具名住户）。

    trigram 最小粒度 3 字：query 为空或 <3 字时无法全文匹配。
    此时若存在过滤维度（type/project/agent），降级为纯过滤查询——
    “列出所有坑条目”（query="坑", type="journal:坑"）是自然用法，
    不该空手而归。无任何过滤维度时如实返回空。
    """
    update_index()
    c = _conn()
    try:
        where: list[str] = []
        args: list = []
        q = query.strip()
        if len(q) >= 3:
            where.append("docs MATCH ?")
            args.append(q)
        if type:
            where.append("type=?")
            args.append(type)
        if project:
            where.append("project=?")
            args.append(project)
        if agent:
            where.append("agent=?")
            args.append(agent)
        if not where:
            return []
        sql = (
            "SELECT path, type, project, agent, "
            # 窗口48：journal行前缀(mark+时间戳)约吃掉20 token，24的窗口正文永远不可见
            "snippet(docs, 0, '→', '←', '…', 48) AS frag FROM docs"
        )
        sql += " WHERE " + " AND ".join(where)
        # rank 只在 MATCH 语境下有意义；纯过滤查询按 path 稳定排序
        sql += " ORDER BY rank" if q and len(q) >= 3 else " ORDER BY path"
        sql += " LIMIT ?"
        args.append(limit)
        rows = c.execute(sql, args).fetchall()
        return [{"path": r[0], "type": r[1], "project": r[2], "agent": r[3], "fragment": r[4]} for r in rows]
    finally:
        c.close()
