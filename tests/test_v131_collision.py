"""test_v131_collision.py — v1.3.1 接力失明兜底回归（照照审稿火力①②）。

复现三案 + 顺手修的第四案，全部同秒注入（mock _now_stamp，不等表）：
  1. A3 混形状尾行致静默回 001 → 应扫库拿 max+1（照照 attempt 0 一发命中的洞）
  2. >8KB 大行垫底窗口失明 → 同上兜底，不回 001
  3. 序号 999 → 1000（原 \\d{3} 接力断裂，v1.3.1 放宽 \\d{3,}）
  4. 快路径不回归：尾行本秒 v1.3 号照常接力；异形状尾段恰好三位数不误吞
隔离：tmp root（显式 root 永远走本地分支，不打网），origin 文件按场景写。
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import spoor_common as sc

ROOT = Path(tempfile.mkdtemp(prefix="spoor_v131_"))
LEDGER = ROOT / "ledger.jsonl"
STAMP = "20260915T150000Z"  # 固定秒：所有场景同秒
sc._now_stamp = lambda: STAMP  # 注入，不等表

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS {name}")
    else:
        FAIL += 1
        print(f"FAIL {name} :: {detail}")


def set_origin(name):
    (ROOT / "origin").write_text(name, encoding="utf-8")


def seed(rows):
    with open(LEDGER, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def reset(origin):
    shutil.rmtree(ROOT)
    ROOT.mkdir()
    (ROOT / "origin").write_text(origin, encoding="utf-8")


def last_id():
    lines = [l for l in open(LEDGER, encoding="utf-8").read().splitlines() if l.strip()]
    return json.loads(lines[-1]).get("id")


# ── 案1：A3 混形状尾行（照照复现：origin-unix秒-pid-seq，未垫零）──────
reset("wsl")
seed([
    {"id": f"wsl-{STAMP}-001", "kind": "v13"},
    {"id": "wsl-1768550400-4321-7", "kind": "a3"},  # A3 契约形状
])
sc.append_ledger({"kind": "hook"}, root=ROOT)
check("[火力②] A3尾行不再静默回001", last_id() == f"wsl-{STAMP}-002", f"got {last_id()}")

# ── 案1b：A3 尾段恰好三位数（-007），不被误吞也不接力 ──────────────────
reset("vps")
seed([
    {"id": "vps-1768550400-4321-007", "kind": "a3"},  # 尾段三位数陷阱
])
sc.append_ledger({"kind": "hook"}, root=ROOT)
check("[火力②b] A3三位尾段不被误吞，本秒001起步", last_id() == f"vps-{STAMP}-001", f"got {last_id()}")

# ── 案2：>8KB 大行垫底，窗口失明 ─────────────────────────────────────
reset("vps")
seed([
    {"id": f"vps-{STAMP}-001", "kind": "v13"},
    {"kind": "blob", "payload": "x" * 12000},  # 大行无 id 垫底
])
sc.append_ledger({"kind": "hook"}, root=ROOT)
check("[火力①] 大行垫底窗口失明仍拿002", last_id() == f"vps-{STAMP}-002", f"got {last_id()}")

# ── 案2b：>8KB 大行带 A3 形状 id 垫底（双失明叠加）────────────────────
reset("vps")
seed([
    {"id": f"vps-{STAMP}-041", "kind": "v13"},
    {"id": "vps-1768550400-99-3", "kind": "a3", "payload": "y" * 12000},
])
sc.append_ledger({"kind": "hook"}, root=ROOT)
check("[火力①②] 大行+A3双叠加仍拿042", last_id() == f"vps-{STAMP}-042", f"got {last_id()}")

# ── 案3：999 → 1000（原 \\d{3} 断裂点）───────────────────────────────
reset("vps")
seed([
    {"id": f"vps-{STAMP}-998", "kind": "v13"},
    {"id": f"vps-{STAMP}-999", "kind": "v13"},
])
sc.append_ledger({"kind": "hook"}, root=ROOT)
check("[顺手] 999→1000 四位不断裂", last_id() == f"vps-{STAMP}-1000", f"got {last_id()}")
sc.append_ledger({"kind": "hook2"}, root=ROOT)
check("[顺手] 1000→1001 四位接力继续", last_id() == f"vps-{STAMP}-1001", f"got {last_id()}")

# ── 案4：快路径与既有语义不回归 ──────────────────────────────────────
reset("vps")
seed([{"id": f"vps-{STAMP}-005", "kind": "v13"}])
sc.append_ledger({"kind": "hook"}, root=ROOT)
check("[回归] 快路径接力 005→006", last_id() == f"vps-{STAMP}-006", f"got {last_id()}")

# 案4a：库内无本秒号时，异秒尾行 → 001 起步（旧语义保持）
reset("vps")
seed([{"id": "vps-20260101T000000Z-042", "kind": "old"}])  # 秒不同，库内无本秒号
sc.append_ledger({"kind": "hook"}, root=ROOT)
check("[回归] 异秒尾行+本秒无号→001起步", last_id() == f"vps-{STAMP}-001", f"got {last_id()}")

# 案4b：本秒号被异秒尾行埋在下面（旧版会重发001实撞，v1.3.1 扫描兜底）
reset("vps")
seed([
    {"id": f"vps-{STAMP}-001", "kind": "v13"},
    {"id": f"vps-{STAMP}-002", "kind": "v13"},
    {"id": "vps-20260101T000000Z-042", "kind": "old"},  # 异秒号垫底，本秒号被埋
])
sc.append_ledger({"kind": "hook"}, root=ROOT)
check("[火力②c] 本秒号被异秒尾行掩埋→max+1不撞", last_id() == f"vps-{STAMP}-003", f"got {last_id()}")

sc.append_ledger({"kind": "hook", "id": "explicit-keep-1"}, root=ROOT)
check("[回归] 调用方自带id仍尊重", last_id() == "explicit-keep-1", f"got {last_id()}")

# 空库起步
reset("vps")
sc.append_ledger({"kind": "hook"}, root=ROOT)
check("[回归] 空库起步001", last_id() == f"vps-{STAMP}-001", f"got {last_id()}")

shutil.rmtree(ROOT)
print(f"\n{PASS} pass, {FAIL} fail")
sys.exit(1 if FAIL else 0)
