#!/usr/bin/env python3
"""
任务D原型：潮痕记忆库「机械增量表格」生成器
=============================================
架构定位（graphify-harvest-plan §D）：
  机械层（本脚本，零token）：每条新记忆 → 落哪个簇/时间戳/实体/同簇上条+间隔
  未来 LLM 层：只读这张小表格判断 narrative↔narrative 因果边，不重读全图

铁律遵守：
  - DB 只读：URI mode=ro，绝无写入
  - 独立原型，不触碰生产固化管线
  - 原始散文不进输出（LLM 只读表格结构信号；如需锚，因果层再按 id 回查）

输出：increment-table.json（本目录）
"""

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_URI = "file:/home/ubuntu/memory/mcp_memory.db?mode=ro"
OUT = Path(__file__).parent / "increment-table.json"
RECENT_N = 30
CLUSTER_NAME_MAX = 28  # 簇名截断长度（簇名本身是长句首段）


def parse_ts(s: str) -> datetime:
    """narratives.created_at 实测为 'YYYY-MM-DD HH:MM:SS'（本地时间），
    context 表有 ISO Z 格式——统一兼容。"""
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:  # ISO 带微秒/时区等变体：'2026-07-11T02:13:50.663186'
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except ValueError:
        pass
    raise ValueError(f"unparsed timestamp: {s!r}")


def gap_human(sec: float) -> str:
    sec = int(sec)
    if sec < 0:
        return "FUTURE?"
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m{sec % 60:02d}s"
    if sec < 86400:
        return f"{sec // 3600}h{(sec % 3600) // 60:02d}m"
    return f"{sec // 86400}d{(sec % 86400) // 3600}h"


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description='build increment table from memory DB (read-only)')
    ap.add_argument('--db', type=str,
                    default='file:/home/ubuntu/memory/mcp_memory.db?mode=ro',
                    help='sqlite URI (mode=ro strongly recommended)')
    ap.add_argument('--out', type=Path, default=Path(__file__).parent / 'increment-table.json')
    args = ap.parse_args()
    global DB_URI
    DB_URI = args.db
    # 只读连接——铁律
    con = sqlite3.connect(DB_URI, uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # ---- 1. 最近 N 条记忆（按 created_at，不按 id——实测 id 序≠时间序）----
    rows = cur.execute(
        """SELECT id, created_at, ntype, tags, related_entities,
                  entities_role, importance, emotional, recurrence, unresolved, weight
           FROM narratives ORDER BY created_at DESC, id DESC LIMIT ?""",
        (RECENT_N,),
    ).fetchall()
    recent = [dict(r) for r in rows]
    recent_ids = {r["id"] for r in recent}

    # ---- 2. 全库簇成员表 + 簇名（内存小表：2655 行级别，安全）----
    members = cur.execute(
        """SELECT m.narrative_id, m.cluster_id, m.distance
           FROM emb_cluster_members m ORDER BY m.cluster_id, m.narrative_id"""
    ).fetchall()
    cluster_names = {r["id"]: r["name"] for r in cur.execute(
        "SELECT id, name FROM emb_clusters").fetchall()}

    # 按 (cluster_id) 组织成员，携带时间戳供回溯
    nar_ts = {r["id"]: parse_ts(r["created_at"]) for r in recent}
    # 补充：簇内回溯需要查全库同簇条目的时间——拉一次轻量索引 (id, created_at)
    all_ts = {r["id"]: parse_ts(r[1]) for r in cur.execute(
        "SELECT id, created_at FROM narratives").fetchall()}

    by_cluster: dict[int, list[tuple[int, float]]] = {}  # cid -> [(nid, dist)]
    for m in members:
        by_cluster.setdefault(m["cluster_id"], []).append((m["narrative_id"], m["distance"]))
    for cid in by_cluster:
        by_cluster[cid].sort(key=lambda t: all_ts.get(t[0], datetime.min))
    # 聚类管线已照到的最大 id：超过它的没簇=批处理未及(pending)，不超过的没簇=算过是孤儿(outlier)
    MAX_CLUSTERED_ID = max((n for lst in by_cluster.values() for n, _d in lst), default=0)

    # ---- 3. graph_edges 补充实体（narrative_id 外键直连）----
    edge_entities: dict[int, set[str]] = {}
    for r in cur.execute(
            "SELECT narrative_id, entity_a, entity_b FROM graph_edges"):
        if r["narrative_id"] is None:
            continue
        edge_entities.setdefault(r["narrative_id"], set()).update(
            {r["entity_a"], r["entity_b"]})

    # ---- 4. 组装行 ----
    out_rows = []
    for r in sorted(recent, key=lambda x: (parse_ts(x["created_at"]), -x["id"]), reverse=True):
        nid = r["id"]
        ts = nar_ts[nid]
        # 实体：related_entities JSON 为主，graph_edges 补
        ents = []
        try:
            ents = [e for e in json.loads(r["related_entities"] or "[]") if e]
        except (json.JSONDecodeError, TypeError):
            pass
        ents = sorted(set(ents) | edge_entities.get(nid, set()))

        # 簇归属 + 同簇上一条 + 间隔
        clusters = []
        cid_list = sorted(
            ((cid, d) for cids in [by_cluster] for cid, lst in cids.items()
             for n, d in lst if n == nid),
            key=lambda t: t[1])  # distance 升序（最近质心优先）
        for cid, dist in cid_list[:3]:  # 软聚类 top-3 语义
            lst = by_cluster[cid]
            prev = None
            for n, _d in lst:
                if n == nid:
                    break
                prev = n  # 列表已按时间升序，最后命中的即同簇上一条
            entry = {
                "cluster_id": cid,
                "cluster_name": (cluster_names.get(cid, "?") or "?")[:CLUSTER_NAME_MAX],
                "distance": round(dist, 4),
            }
            if prev is not None:
                gap = (ts - all_ts[prev]).total_seconds()
                entry["prev_in_cluster"] = {
                    "id": prev,
                    "gap": gap_human(gap),
                    "gap_seconds": int(gap),
                }
            else:
                entry["prev_in_cluster"] = None  # 簇内首条
            clusters.append(entry)

        out_rows.append({
            "id": nid,
            "created_at": r["created_at"],
            "ntype": r["ntype"],
            "tags": json.loads(r["tags"] or "[]"),
            "entities": ents,
            "role_excerpt": (r["entities_role"] or "")[:60] or None,
            "signals": {k: r[k] for k in
                        ("importance", "emotional", "recurrence", "unresolved", "weight")},
            "clusters": clusters,
            # Three states explicitly separated (review ruling): consumers
            # must distinguish "not yet lit" from "lit and orphaned"
            # ok=算过有簇 | pending=批处理未及（未进聚类管线） | outlier=算过确实无簇
            "cluster_status": (status := ("ok" if clusters
                               else ("pending" if nid > MAX_CLUSTERED_ID
                                     else "outlier"))),
            # 降级时换基准必须标注不静默（同裁定）：全库上一条=不管簇的纯时间序回溯
            "fallback_prev": (None if status == "ok" else {
                "note": "cluster_status!=ok，prev 降级为全库上一条（换基准标注）",
                "prev_all": (lambda p: {"id": p, "gap": gap_human((ts - all_ts[p]).total_seconds())}
                             if p else None)(
                    max((n for n in nar_ts if n != nid and all_ts.get(n, datetime.min) <= ts),
                        key=lambda n: all_ts.get(n, datetime.min), default=None)),
            }),
        })

    unclustered = sum(1 for r in out_rows if not r["clusters"])
    doc = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "source_db": f"{DB_URI} (read-only prototype)",
            "recent_n": RECENT_N,
            "notes": [
                "id序≠时间序，行序=created_at降序",
                "软聚类：每条记忆可属多个簇（实测top-3，distance=到质心距离，越小越近）",
                "cluster_status three states (review): ok=clustered / pending=batch-not-reached / outlier=clustered-but-no-cluster — one UNCLUSTERED no longer conflates two semantics",
                "cluster_status!=ok 时 prev 降级为全库上一条，fallback_prev 带标注不静默换基准",
                "prev_in_cluster=同簇中created_at更早的最近一条；null=簇内首条",
                "raw散文不进表格——因果层如需锚按id回查narratives",
            ],
            "stats": {
                "rows": len(out_rows),
                "ok": sum(1 for r in out_rows if r["cluster_status"] == "ok"),
                "pending": sum(1 for r in out_rows if r["cluster_status"] == "pending"),
                "outlier": sum(1 for r in out_rows if r["cluster_status"] == "outlier"),
            },
        },
        "rows": out_rows,
    }
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1))

    # 控制台摘要（cron 日志用）
    print(f"rows={len(out_rows)} clustered={len(out_rows)-unclustered} unclustered={unclustered}")
    for r in out_rows[:5]:
        cl = ",".join(f"c{c['cluster_id']}(d{c['distance']})" for c in r["clusters"]) or "-"
        prev = next((f"{c['cluster_id']}->{c['prev_in_cluster']['id']}"
                     for c in r["clusters"] if c["prev_in_cluster"]), "-")
        print(f"  id={r['id']} {r['created_at'][:16]} ents={r['entities']} cl=[{cl}] prev={prev}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
