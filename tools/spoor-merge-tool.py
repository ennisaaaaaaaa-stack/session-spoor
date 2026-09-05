#!/usr/bin/env python3
"""spoor 归流管道：ledger 并集 merge + 撞车桌归并 + 并集对账。

规则来自 drafts/spoor-hook-layer-skeleton-v1.1.md §1.5：
- id 并集：左(VPS主库)原序保留 + 右(WSL导出)独有行追加；撞号=拒收报人（重映射是人的账，不自动做）
- 桌归并：右独有桌整拷；两边都有 → 文件级归并：identical 跳过（计数报告）、
  journal/正文 md 按最新条目 ts 排段（左右各自时区参数显式吃，转 UTC 比）、
  STATUS.md/description.md 打 MERGE-CONFLICT 留人审、二进制冲突只报不并
- 对账基准 = 并集：merged 必须逐 id 逐字节等于来源行；桌文件 containment + 条目数守恒

铁律：所有输出只进 staging 目录/文件，绝不写活库——切流末行由人工执行。

用法（真实归流时的命令）：
  # 1. ledger 并集（先补号，见 renumber-ledger-ids.py）
  python3 spoor-merge-tool.py ledger --left /home/ubuntu/Stigmergy/ledger.jsonl \
      --right <wsl导出>/ledger.jsonl -o /tmp/spoor-merge-staging/ledger.jsonl
  # 2. 桌归并（右=WSL workbench 导出目录）
  python3 spoor-merge-tool.py desk --left-wb /home/ubuntu/Stigmergy/workbench \
      --right-wb <wsl导出>/workbench --left-tz +0800 --right-tz +0900 \
      -o /tmp/spoor-merge-staging/workbench
  # 3. 并集对账（rc=0 才算过，报告机读 json）
  python3 spoor-merge-tool.py reconcile --left ... --right ... --merged ... \
      --left-wb ... --right-wb ... --merged-wb ... --json report.json
"""
import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone

TS_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})[ T](\d{2}:\d{2})")
CONFLICT_HEADER = "<!-- MERGE-CONFLICT: 两边均有此文件，以下为 WSL 版本，留人审后取舍（归流 2026-09-05） -->"
SRC_HEADER = "<!-- MERGE-SRC: 后段来自 {src}，段内各自保序，跨侧未交织（归流 2026-09-05） -->"
META_FILES = {"INDEX.md", "ignore.json", "repos.json"}


def tz_from_offset(tz_offset: str) -> timezone:
    sign = 1 if tz_offset.startswith("+") else -1
    rest = tz_offset[1:]
    hh, mm = int(rest[:2]), int(rest[2:4] or 0)
    return timezone(sign * timedelta(hours=hh, minutes=mm))


def load_ledger(path: str):
    """返回 [(row, raw_line)]；无 id / json 坏行 / 库内重复 id 全部致命退出。"""
    rows = []
    seen = {}
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f.read().splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                sys.exit(f"FAIL {path} 第{i}行 json 解析失败: {e}")
            if not row.get("id"):
                sys.exit(f"FAIL {path} 第{i}行无 id —— 先跑 renumber-ledger-ids.py 补号再归流")
            if row["id"] in seen:
                sys.exit(f"FAIL {path} 库内重复 id（第{seen[row['id']]}与{i}行）: {row['id']}")
            seen[row["id"]] = i
            rows.append((row, line))
    return rows


def cmd_ledger(args):
    left = load_ledger(args.left)
    right = load_ledger(args.right)
    lids = {r["id"] for r, _ in left}
    rids = {r["id"] for r, _ in right}
    inter = sorted(lids & rids)
    if inter:
        print(f"FAIL 两库撞号 {len(inter)} 条（v1.1 §1.5.3：WSL 侧重映射并记映射账，不自动做）:",
              file=sys.stderr)
        for x in inter[:10]:
            print("  ", x, file=sys.stderr)
        sys.exit(2)
    out_lines = [ln for _, ln in left] + [ln for r, ln in right if r["id"] not in lids]
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines) + "\n")
    union = len(out_lines)
    print(f"左(VPS)={len(left)} 右(WSL)={len(right)} 撞号=0 并集={union}（追加 {union - len(left)} 条）")
    print(f"首={out_lines[0].split(chr(34))[3]}  尾={out_lines[-1].split(chr(34))[3]}")
    print(f"staging 已写: {args.out}（未碰活库；行序=左原序+右增量，展示按行内 ts 排）")


def latest_entry_ts_utc(text: str, tz: timezone):
    """取文本里最后一条 (date hh:mm) 当该侧最新条目时间，转 UTC；解析不出返回 None。"""
    hits = TS_RE.findall(text)
    if not hits:
        return None
    d, t = hits[-1]
    try:
        dt = datetime.fromisoformat(f"{d}T{t}:00").replace(tzinfo=tz)
    except ValueError:
        return None
    return dt.astimezone(timezone.utc)


def merge_md_file(left_path: str, right_path: str, rel: str, left_tz: timezone,
                  right_tz: timezone, actions: list):
    """两边都有且内容不同 → 归并成一份；返回归并后文本。"""
    lb = open(left_path, "r", encoding="utf-8").read()
    rb = open(right_path, "r", encoding="utf-8").read()
    base = os.path.basename(rel)
    if base in ("STATUS.md", "description.md"):
        # 现态/定位类文件：语义冲突，左前右后 + 冲突标记，留人审
        actions.append(f"CONFLICT(留人审): {rel}")
        return lb.rstrip("\n") + "\n\n" + CONFLICT_HEADER + "\n" + rb.lstrip("\n")
    # journal/正文类：按两侧最新条目 ts 排段（显式吃时区），老的在前
    lt = latest_entry_ts_utc(lb, left_tz)
    rt = latest_entry_ts_utc(rb, right_tz)
    if lt is not None and rt is not None and rt < lt:
        head, head_src, tail = rb, "wsl", lb
        actions.append(f"MERGE(段序 wsl→vps): {rel}")
    else:
        head, head_src, tail = lb, "vps", rb
        if lt is None or rt is None:
            actions.append(f"MERGE(段序缺ts默认 vps→wsl): {rel}")
        else:
            actions.append(f"MERGE(段序 vps→wsl): {rel}")
    return head.rstrip("\n") + "\n\n" + SRC_HEADER.format(src=head_src) + "\n" + tail.lstrip("\n")


def cmd_desk(args):
    left_tz = tz_from_offset(args.left_tz)
    right_tz = tz_from_offset(args.right_tz)
    lw, rw, out = args.left_wb, args.right_wb, args.out
    if os.path.exists(out):
        sys.exit(f"FAIL 输出目录已存在，拒绝覆盖: {out}")
    os.makedirs(out)
    actions = []

    # 左侧全量先落 staging（staging=完整合并态，对账和切流都吃它）
    for entry in os.listdir(lw):
        src = os.path.join(lw, entry)
        dst = os.path.join(out, entry)
        (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, dst)

    # 元文件：INDEX 由自动维护重生成，右侧版本存档留审；ignore/repos 左版为准，右侧差异存档
    for meta in sorted(META_FILES):
        rmeta = os.path.join(rw, meta)
        if os.path.exists(rmeta):
            lmeta = os.path.join(lw, meta)
            same = os.path.exists(lmeta) and open(lmeta, "rb").read() == open(rmeta, "rb").read()
            if not same:
                shutil.copy2(rmeta, os.path.join(out, f"{meta}.wsl-incoming"))
                actions.append(f"META-ARCHIVE(留审): {meta} 两侧不同，右版存为 {meta}.wsl-incoming")
            else:
                actions.append(f"SAME: {meta}")

    # 桌级归并
    for desk in sorted(os.listdir(rw)):
        rdesk = os.path.join(rw, desk)
        if not os.path.isdir(rdesk) or desk in META_FILES:
            continue
        ldesk = os.path.join(lw, desk)
        if not os.path.isdir(ldesk):
            shutil.copytree(rdesk, os.path.join(out, desk))
            actions.append(f"COPY-NEW-DESK: {desk}/（WSL独有，整拷）")
            continue
        for root, _dirs, files in os.walk(rdesk):
            for fn in sorted(files):
                rpath = os.path.join(root, fn)
                rel = os.path.relpath(rpath, rdesk)
                lpath = os.path.join(ldesk, rel)
                tpath = os.path.join(out, desk, rel)
                if not os.path.exists(lpath):
                    os.makedirs(os.path.dirname(tpath), exist_ok=True)
                    shutil.copy2(rpath, tpath)
                    actions.append(f"FILE-ADD: {desk}/{rel}")
                    continue
                if open(lpath, "rb").read() == open(rpath, "rb").read():
                    actions.append(f"SAME: {desk}/{rel}")
                    continue
                if fn.endswith((".md", ".txt")):
                    merged = merge_md_file(lpath, rpath, f"{desk}/{rel}", left_tz, right_tz, actions)
                    os.makedirs(os.path.dirname(tpath), exist_ok=True)
                    with open(tpath, "w", encoding="utf-8") as f:
                        f.write(merged)
                else:
                    actions.append(f"CONFLICT-BINARY(只报不并): {desk}/{rel}")

    for a in actions:
        print(a)
    n = len(actions)
    print(f"\n桌归并完毕：{n} 条动作，staging 完整工作台已写: {out}（未碰活库）")


def cmd_reconcile(args):
    problems = []
    rep = {"ledger": {}, "desks": {}}

    # ---- ledger：并集 + 逐 id 逐字节 ----
    left = load_ledger(args.left)
    right = load_ledger(args.right)
    merged = load_ledger(args.merged)
    src = {}
    for r, ln in left + right:
        src[r["id"]] = ln
    mids = [r["id"] for r, _ in merged]
    if len(mids) != len(set(mids)):
        problems.append("merged 库内存在重复 id")
    expect = set(src)
    got = set(mids)
    if expect - got:
        problems.append(f"merged 缺失 {len(expect - got)} 条（如 {sorted(expect - got)[:3]}）")
    if got - expect:
        problems.append(f"merged 多出 {len(got - expect)} 条（如 {sorted(got - expect)[:3]}）")
    byte_bad = [r["id"] for r, ln in merged if r["id"] in src and ln != src[r["id"]]]
    if byte_bad:
        problems.append(f"merged 有 {len(byte_bad)} 条与来源逐字节不符（如 {byte_bad[:3]}）")
    rep["ledger"] = {"left": len(left), "right": len(right), "union_expect": len(expect),
                     "merged": len(merged), "byte_equal_fail": len(byte_bad)}

    # ---- desks：文件 containment + journal 条目数守恒 ----
    def wb_files(root):
        out = {}
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                p = os.path.join(dirpath, fn)
                out[os.path.relpath(p, root)] = p
        return out

    lw = wb_files(args.left_wb)
    rw = wb_files(args.right_wb)
    mw = wb_files(args.merged_wb)
    entry_re = re.compile(r"^\s*-\s+\*\*\[", re.M)

    def count_entries(text):
        return len(entry_re.findall(text))

    all_rel = sorted(set(lw) | set(rw))
    for rel in all_rel:
        in_l, in_r, in_m = rel in lw, rel in rw, rel in mw
        desk = rel.split(os.sep)[0]
        d = rep["desks"].setdefault(desk, {"checked": 0, "entry_conserved": []})
        d["checked"] += 1
        if not in_m:
            problems.append(f"merged 工作台缺文件: {rel}")
            continue
        mb = open(mw[rel], "rb").read()
        if in_l and in_r:
            lbytes = open(lw[rel], "rb").read()
            rbytes = open(rw[rel], "rb").read()
            if lbytes == rbytes:
                if mb != lbytes:
                    problems.append(f"两侧同文但 merged 被改动: {rel}")
            elif os.path.basename(rel) in META_FILES:
                # 元文件：desk 侧约定=左版原样保留 + 右版存档为 {rel}.wsl-incoming
                if mb != lbytes:
                    problems.append(f"meta 左版被改动: {rel}")
                inc = rel + ".wsl-incoming"
                if inc not in mw:
                    problems.append(f"meta 右版存档缺失: {inc}")
                elif open(mw[inc], "rb").read() != rbytes:
                    problems.append(f"meta 右版存档与源不符: {inc}")
            else:
                # 归并文件：两侧正文各自完整在场（去 BOM/尾空白比对）+ 条目数守恒
                if rel.endswith((".md", ".txt")):
                    mt = mb.decode("utf-8", "replace")
                    lt = lbytes.decode("utf-8", "replace").strip()
                    rt = rbytes.decode("utf-8", "replace").strip()
                    if lt not in mt or rt not in mt:
                        problems.append(f"归并文件缺一侧正文: {rel}")
                    parts = rel.split(os.sep)
                    if len(parts) >= 2 and "journal" in parts[:-1]:
                        cl, cr, cm = count_entries(lt), count_entries(rt), count_entries(mt)
                        d["entry_conserved"].append({rel: [cl, cr, cm]})
                        if cm != cl + cr:
                            problems.append(f"journal 条目不守恒: {rel}（{cl}+{cr}≠{cm}）")
                else:
                    d.setdefault("binary_conflict_reported", []).append(rel)
        else:
            srcp = (lw if in_l else rw)[rel]
            if mb != open(srcp, "rb").read():
                problems.append(f"独有文件未被逐字节保留: {rel}")

    print(json.dumps(rep, ensure_ascii=False, indent=1))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"ok": not problems, "problems": problems, "report": rep},
                      f, ensure_ascii=False, indent=1)
        print(f"机读报告: {args.json}")
    if problems:
        print(f"\nFAIL 对账未过，{len(problems)} 处问题:")
        for p in problems:
            print("  -", p)
        sys.exit(1)
    print("\n对账通过：并集逐 id 逐字节相符，桌文件 containment + 条目守恒全绿。rc=0")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("ledger")
    p1.add_argument("--left", required=True, help="VPS 主库 ledger.jsonl（补号后）")
    p1.add_argument("--right", required=True, help="WSL 导出 ledger.jsonl（补号后）")
    p1.add_argument("-o", "--out", required=True, help="staging 输出文件（不碰活库）")

    p2 = sub.add_parser("desk")
    p2.add_argument("--left-wb", required=True)
    p2.add_argument("--right-wb", required=True)
    p2.add_argument("--left-tz", required=True, help="左座行内 ts 时区，如 +0800")
    p2.add_argument("--right-tz", required=True, help="右座行内 ts 时区，如 +0900")
    p2.add_argument("-o", "--out", required=True, help="staging 完整工作台目录（不碰活库）")

    p3 = sub.add_parser("reconcile")
    for k in ("--left", "--right", "--merged", "--left-wb", "--right-wb", "--merged-wb"):
        p3.add_argument(k, required=True)
    p3.add_argument("--json", help="机读报告输出路径")

    args = ap.parse_args()
    {"ledger": cmd_ledger, "desk": cmd_desk, "reconcile": cmd_reconcile}[args.cmd](args)


if __name__ == "__main__":
    main()
