#!/usr/bin/env python3
"""detect-ledger-id-collision — A2 归流撞号检测（骨架 v1.1 配套）

用法：
  双库撞号检测：
    python3 detect-ledger-id-collision.py --left A.jsonl --right B.jsonl [--id-field id]
  单库体检（无 --right）：
    python3 detect-ledger-id-collision.py --left A.jsonl
    → 统计有 id / 无 id 事件（存量补发号的第一手事实）

退出码：0 干净 / 1 撞号 / 2 输入问题
输出：stdout 人读报告；--json out.json 同时落机读版（迁移脚本第二步吃它）
"""
import argparse
import json
import sys
from pathlib import Path


def load(path: Path):
    events, bad = [], 0
    with open(path, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
                print(f"WARN {path.name}:{ln} 非法 JSON 行，跳过", file=sys.stderr)
    return events, bad


def describe(ev: dict, id_field: str) -> str:
    for k in (id_field, "event", "type", "project", "room", "text"):
        if k in ev and ev[k]:
            s = str(ev[k])
            return s[:80]
    return json.dumps(ev, ensure_ascii=False)[:80]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--left", required=True, help="库 A 的 ledger.jsonl")
    ap.add_argument("--right", help="库 B 的 ledger.jsonl（缺省=单库体检）")
    ap.add_argument("--id-field", default="id")
    ap.add_argument("--json", help="机读结果输出路径")
    args = ap.parse_args()

    left, bad_l = load(Path(args.left))
    report = {"left": args.left, "right": args.right, "id_field": args.id_field}
    rc = 0

    l_ids = [ev.get(args.id_field) for ev in left]
    l_have = [i for i in l_ids if i is not None]
    print(f"[A] {args.left}: {len(left)} 事件，有 {args.id_field} 字段的 {len(l_have)}，无 {len(left)-len(l_have)}")
    report["left_total"], report["left_with_id"], report["left_without_id"] = len(left), len(l_have), len(left) - len(l_have)

    # 单库内部重复也要查（同一库自己撞自己，归流前必须干净）
    l_dup = {}
    seen = {}
    for ev, i in zip(left, l_ids):
        if i is not None:
            seen.setdefault(i, []).append(ev)
    l_dup = {i: evs for i, evs in seen.items() if len(evs) > 1}
    if l_dup:
        rc = 1
        print(f"[A] 内部重复 id {len(l_dup)} 个：")
        for i, evs in list(l_dup.items())[:20]:
            print(f"    {i} ×{len(evs)}  ({describe(evs[0], args.id_field)})")
    report["left_internal_dup"] = len(l_dup)

    if not args.right:
        print(f"[单库体检] {'干净' if rc == 0 else '有内部重复'}；无 id 存量 {len(left)-len(l_have)} 条需在归流前补发号")
        if args.json:
            Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=1))
        return rc

    right, bad_r = load(Path(args.right))
    r_ids = [ev.get(args.id_field) for ev in right]
    r_have = [i for i in r_ids if i is not None]
    print(f"[B] {args.right}: {len(right)} 事件，有 {args.id_field} 字段的 {len(r_have)}，无 {len(right)-len(r_have)}")
    report["right_total"], report["right_with_id"], report["right_without_id"] = len(right), len(r_have), len(right) - len(r_have)

    r_seen = {}
    for ev, i in zip(right, r_ids):
        if i is not None:
            r_seen.setdefault(i, []).append(ev)
    r_dup = {i: evs for i, evs in r_seen.items() if len(evs) > 1}
    if r_dup:
        rc = 1
        print(f"[B] 内部重复 id {len(r_dup)} 个")
    report["right_internal_dup"] = len(r_dup)

    collisions = sorted(set(seen) & set(r_seen))
    report["cross_collisions"] = [
        {"id": i, "left": describe(seen[i][0], args.id_field), "right": describe(r_seen[i][0], args.id_field)}
        for i in collisions
    ]
    if collisions:
        rc = 1
        print(f"[撞号] A∩B 共 {len(collisions)} 个 id 撞车，重映射清单：")
        for c in report["cross_collisions"][:50]:
            print(f"    {c['id']}\n      A: {c['left']}\n      B: {c['right']}")
    else:
        print("[撞号] 0 —— 按 id 维度两库可直并")

    both_no_id = (len(left) - len(l_have)) + (len(right) - len(r_have))
    if both_no_id:
        print(f"[补号] 两库合计 {both_no_id} 条存量无 id：归流第一步=按发号规则补发（带发号方标识），补完重跑本脚本")
    print(f"[结论] {'有问题，按上面清单处理' if rc else '干净，可进归流下一步'}")
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=1))
    return rc


if __name__ == "__main__":
    sys.exit(main())
