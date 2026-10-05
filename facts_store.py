"""
facts_store: session-spoor 已探明事实表（facts 表）v1.0。

病（为什么有这个器官，spec v2 §一）：
- 09-30 凌晨：三场 spoor-session 在同一张逐字没变的牌面上各跑一遍
  完整冷启动，烧 $0.42/场得出同一句「值得动 0」。
- 09-29 深夜：一场 todo-review 烧 $1.00、48 次 bash 做出处考古——
  结论只活在 transcript 里，下个空闲窗谁也不能复用。
- 10-05 僵尸环：env-event 每场 ~2.4k token 重新发现同一个已知事实。
病根：journal 天天打修因但没人考古（考古本身太贵）。这张表是便宜的
结论层：冷启动必读，要细节按 source 指针回挖。

边界（9/28 编排归属纪律，spec v2 §二）：
- 本模块 = 存储+读写口。一张表，两宅（山海/pianist）共用，不碰编排。
- pianist 侧消费：派活模板「上游已探明：X，别再探」一节，数据源 =
  fact_read。触发器挂派活侧，不进唤醒（tiexin 9/30 定调：记忆是工具
  不是义务，需要时才拉）。
- 与 faceHash 分工：faceHash 管「同牌面 12h 不重抽」（时间维）；
  facts 管「结论跨牌面存活」（内容维）。互补不是替代。冲突裁决钉死：
  TTL 过期 > faceHash 冷却（新鲜度优先于省钱：牌面没变但事实过期，
  照抽）——机制面=读口过滤只看自己的 TTL，过期即从活视图消失，逼
  重探；外部冷却窗无权把过期事实留在读口里。见 docs/facts-table.md。

存储：{root}/facts.db（SQLite WAL，gitignore——运行时数据，每部署
各自积累，与 workbench/journal 联邦式同哲学）。

UPSERT 语义：同 (scope, key) 再写 = 刷新而非插新行——重探同一主题
不该把表喂成新的考古对象。conclusion/source/reconfirmed_at/agent
更新，created_at 保留首次探明时间。

TTL 分型（spec §三，具体值洄拍）：
  fast = 6h    「现在时」事实：队列空、在跑进程、临时状态
  slow = 720h  「地质」事实：路径不存在、表结构、环境恒性
万物有 TTL：慢事实 30 天后也该重验一次——「新鲜度优先于省钱」
对慢事实同样成立。写者可用 ttl_h 覆盖默认（>0 生效）。
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

ROOT = Path(os.environ.get("STIGMERGY_ROOT", str(Path.home() / "Stigmergy")))

TTL_CLASS_H = {"fast": 6.0, "slow": 720.0}
FACTS_DB = "facts.db"

_NOW_FMT = "%Y-%m-%dT%H:%M:%S"   # 与账本 ts 同款，跨表可比较
_COLS = ["id", "scope", "key", "conclusion", "source",
         "created_at", "reconfirmed_at", "ttl_h", "agent"]

_SCOPE_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def _db_path(root: "Path | None" = None) -> Path:
    return (Path(root) if root else ROOT) / FACTS_DB


def _conn(root: "Path | None" = None) -> sqlite3.Connection:
    """连接并确保 schema。WAL+busy_timeout：跨进程并发写安全。"""
    p = _db_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p), timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=10000")
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,
            key TEXT NOT NULL,
            conclusion TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            reconfirmed_at TEXT NOT NULL,
            ttl_h REAL NOT NULL,
            agent TEXT,
            UNIQUE(scope, key)
        )
        """
    )
    c.commit()
    return c


def _now_iso() -> str:
    return time.strftime(_NOW_FMT, time.localtime())


def _age_h(ts: str, now: "float | None" = None) -> "float | None":
    try:
        t = time.mktime(time.strptime(ts, _NOW_FMT))
        return ((now if now is not None else time.time()) - t) / 3600.0
    except (ValueError, TypeError, OverflowError):
        return None


def validate_scope_write(scope: str, project_exists) -> "str | None":
    """写口 scope 校验。返回错误消息或 None（通过）。

    - 'global'：全局事实，直接过
    - 'face:<指纹>'：牌面指纹域，指纹的真值在琴师侧，本侧不做存在性校验
    - 其他裸名：必须是已存在的 workbench 项目——防手滑把事实写进
      孤儿域（拼错项目名 = 结论永远没人读到）
    project_exists: callable(name)->bool，由调用侧注入（MCP 层知道
    workbench 目录结构，本模块保持零依赖）。
    """
    s = (scope or "").strip()
    if not s:
        return "scope required: 'global' / 'face:<hash>' / <existing project>"
    if s == "global" or s.startswith("face:"):
        return None
    if not _SCOPE_SAFE.issuperset(s):
        return f"project scope: [a-zA-Z0-9_-] only (got {s!r})"
    if not project_exists(s):
        return f"project not found: {s} (workbench_new first, or use 'global'/'face:<hash>')"
    return None


def fact_write(scope: str, key: str, conclusion: str, source: str,
               ttl_class: str = "fast", ttl_h: float = 0.0,
               root: "Path | None" = None) -> dict:
    """写口：落一条已探明事实。同 (scope,key) 再写=刷新（UPSERT）。

    Args:
        scope: 'global' / 'face:<指纹>' / 项目名
        key: 事实主题键（短标识串，如 env-event-queue-empty）
        conclusion: 一句话人话结论
        source: 出处指针（journal:<project>/<date> / ledger:<id> / commit:<sha>）
        ttl_class: fast(6h) / slow(720h)
        ttl_h: >0 覆盖分型默认
    """
    import spoor_common
    if ttl_class not in TTL_CLASS_H:
        return {"ok": False, "error": f"ttl_class must be one of {sorted(TTL_CLASS_H)}"}
    if not key or not key.strip():
        return {"ok": False, "error": "key required"}
    if not conclusion or not conclusion.strip():
        return {"ok": False, "error": "conclusion required (一句话人话)"}
    if not source or not source.strip():
        return {"ok": False, "error": "source required (出处指针：journal:.../ledger:.../commit:...)"}
    if ttl_h < 0:
        return {"ok": False, "error": "ttl_h must be > 0"}
    eff_ttl = ttl_h if ttl_h > 0 else TTL_CLASS_H[ttl_class]

    scope = scope.strip()
    key = key.strip()
    wb = (Path(root) if root else ROOT) / "workbench"
    err = validate_scope_write(scope, lambda n: (wb / n).is_dir())
    if err:
        return {"ok": False, "error": err}

    now = _now_iso()
    agent = spoor_common.agent_name()
    c = _conn(root)
    try:
        prev = c.execute(
            "SELECT id, created_at FROM facts WHERE scope=? AND key=?", (scope, key)
        ).fetchone()
        if prev:
            c.execute(
                """UPDATE facts SET conclusion=?, source=?, reconfirmed_at=?,
                       ttl_h=?, agent=? WHERE id=?""",
                (conclusion.strip(), source.strip(), now, eff_ttl, agent or None, prev[0]),
            )
            row = c.execute("SELECT * FROM facts WHERE id=?", (prev[0],)).fetchone()
            is_new = False
        else:
            cur = c.execute(
                """INSERT INTO facts (scope, key, conclusion, source, created_at,
                     reconfirmed_at, ttl_h, agent) VALUES (?,?,?,?,?,?,?,?)""",
                (scope, key, conclusion.strip(), source.strip(), now, now, eff_ttl, agent or None),
            )
            row = c.execute("SELECT * FROM facts WHERE id=?", (cur.lastrowid,)).fetchone()
            is_new = True
        c.commit()
    finally:
        c.close()
    d = dict(zip(_COLS, row))
    spoor_common.append_ledger({
        "event": "threesome.facts.write", "scope": scope, "key": key,
        "fresh": is_new, "ttl_h": eff_ttl,
        "conclusion_head": conclusion.strip()[:80],   # 同 journal entry_head 泄漏哲学
    }, root=root)
    d["ok"] = True
    d["fresh"] = is_new
    return d


def fact_read(scope: str = "", include_expired: bool = False,
              root: "Path | None" = None, reason: str = "") -> dict:
    """读口：冷启动 / 派活侧拉取已探明事实。

    scope='' 或 'global' → 只回 global 域；
    scope=<项目名或face:*> → global 域 + 该域（冷启动全景=global恒含）。
    默认只回未过期（过期即从活视图消失=逼重探，J4/J7 裁决的机制面）；
    过期名单无论如何都在 expired[] 里出声——读口持续报待重验清单。
    include_expired=True 时过期行也进 rows（带 expired=True 标记，
    审计/回挖用；冷启动注入用默认视图）。

    Returns: {ok, rows:[{...}], expired:[{key,scope,age_h,ttl_h}], text}
    text 是可直接注入 prompt 的紧凑文本。
    """
    import spoor_common
    now = time.time()
    s = (scope or "").strip()
    scopes = ["global"] + ([s] if (s and s != "global") else [])
    c = _conn(root)
    try:
        q = "SELECT * FROM facts WHERE scope IN (%s)" % ",".join("?" * len(scopes))
        raw = c.execute(q, scopes).fetchall()
    finally:
        c.close()
    alive, expired_rows = [], []
    for r in raw:
        d = dict(zip(_COLS, r))
        age = _age_h(d["reconfirmed_at"], now)
        d["age_h"] = round(age, 2) if age is not None else None
        d["left_h"] = round(d["ttl_h"] - age, 2) if age is not None else None
        if age is not None and age < d["ttl_h"]:
            d["expired"] = False
            alive.append(d)
        else:
            d["expired"] = True
            expired_rows.append(d)
    expired = [{"key": d["key"], "scope": d["scope"],
                "age_h": d["age_h"], "ttl_h": d["ttl_h"]} for d in expired_rows]
    rows = alive + expired_rows if include_expired else alive
    lines = []
    for d in sorted(alive, key=lambda x: (x["scope"], x["key"])):
        lines.append(f"[{d['scope']}|{d['key']}] {d['conclusion']} :: {d['source']} :: 剩{d['left_h']}h")
    if expired:
        ex = "、".join(f"{e['key']}({e['scope']},超{round((e['age_h'] or 0) - e['ttl_h'], 1)}h)" for e in expired)
        lines.append(f"[过期待重验 {len(expired)} 条] {ex} —— 重探后 fact_write 刷新，别猜")
    text = f"[facts {len(alive)} 活/{len(expired)} 过期]" \
           + ("\n" + "\n".join(lines) if lines else "")
    spoor_common.append_ledger({
        "event": "threesome.facts.read", "scope": s or "global",
        "returned": len(rows), "expired_filtered": len(expired),
        "reason": reason or None,
    }, root=root)
    return {"ok": True, "rows": rows, "expired": expired, "text": text}
