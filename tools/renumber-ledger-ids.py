#!/usr/bin/env python3
"""存量账本补发号：给无 id 的 ledger.jsonl 每行补 id = <origin>-<UTC时间戳>-<序号>.

规则来自 drafts/spoor-hook-layer-skeleton-v1.1.md §1：
- id = <origin>-<UTC时间戳>-<序号>，origin ∈ vps/wsl
- 行内 ts 是本地时间无时区后缀，必须显式吃 --tz-offset 转.utc
- 补号是幂等的：已有 id 的行不动

用法:
  # dry-run（不写盘，只报告）
  python3 renumber-ledger-ids.py --ledger /home/ubuntu/Stigmergy/ledger.jsonl --origin vps --tz-offset +0800
  # 真跑（自动备份到同目录 ledger.jsonl.bak-<ts>，原子替换）
  python3 renumber-ledger-ids.py --ledger ... --origin vps --tz-offset +0800 --apply
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone


def parse_local_ts(ts: str, tz_offset: str):
    """本地无后缀 ts -> aware datetime。tz_offset 形如 +0800 / -0530。"""
    sign = 1 if tz_offset.startswith("+") else -1
    rest = tz_offset[1:]
    hh, mm = int(rest[:2]), int(rest[2:4] or 0)
    tz = timezone(sign * timedelta(hours=hh, minutes=mm))
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is not None:
        raise ValueError(f"ts 已带时区后缀（补号前提是不带）: {ts}")
    return dt.replace(tzinfo=tz)


def make_id(origin: str, dt_utc: datetime, seq: int) -> str:
    stamp = dt_utc.strftime("%Y%m%dT%H%M%SZ")
    return f"{origin}-{stamp}-{seq:03d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--origin", required=True, choices=["vps", "wsl"])
    ap.add_argument("--tz-offset", required=True, help="行内 ts 的本地时区，如 +0800 / +0900")
    ap.add_argument("--apply", action="store_true", help="真跑写盘（默认 dry-run）")
    args = ap.parse_args()

    with open(args.ledger, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    out, seen_ids = [], {}
    n_new, n_kept, n_blank = 0, 0, 0
    for i, line in enumerate(lines, 1):
        if not line.strip():
            n_blank += 1
            out.append(line)
            continue
        row = json.loads(line)
        if row.get("id"):
            n_kept += 1
            out.append(json.dumps(row, ensure_ascii=False))
            continue
        if "ts" not in row:
            print(f"FAIL 第{i}行无 ts 也无 id，拒绝补号: {line[:80]}", file=sys.stderr)
            sys.exit(1)
        dt_utc = parse_local_ts(row["ts"], args.tz_offset).astimezone(timezone.utc)
        seq = len(seen_ids) + 1
        new_id = make_id(args.origin, dt_utc, seq)
        # 全局序号递增，同 ts 天然被序号打散；撞号自检（防重复行）
        if new_id in seen_ids:
            print(f"FAIL 生成的 id 重复（第{i}行）: {new_id}", file=sys.stderr)
            sys.exit(1)
        seen_ids[new_id] = i
        row_new = {"id": new_id}
        row_new.update(row)
        n_new += 1
        out.append(json.dumps(row_new, ensure_ascii=False))

    print(f"行数={len(lines)} 补号={n_new} 已有id保留={n_kept} 空行={n_blank}")
    sample = [l for l in out if '"id"' in l][:2]
    for s in sample:
        print("样例:", s[:120])

    if not args.apply:
        print("dry-run 完毕，未写盘。加 --apply 真跑。")
        return

    backup = f"{args.ledger}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(args.ledger, backup)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(args.ledger), prefix=".renumber-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        os.replace(tmp, args.ledger)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    print(f"已写盘（原子替换），备份: {backup}")


if __name__ == "__main__":
    main()
