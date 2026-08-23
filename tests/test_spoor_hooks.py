"""test_spoor_hooks.py — session-end 缺口检测的纯逻辑单测。

隔离手法：tmp root + 伪造 repos.json/ledger.jsonl，不碰真实账本。
覆盖：触达检测（路径签名/多桌/零触达）、缺口计算（从未写/4h窗/刚写过）、
静默失败（坏json/缺文件）、hardcode映射（Grimoire→portalk, Stigmergy→memory-wash）。
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import spoor_hooks as sh

PASS = 0
FAIL = 0

def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name}")

def mk_root():
    root = Path(tempfile.mkdtemp(prefix="spoor_hooks_test_"))
    (root / "workbench").mkdir()
    (root / "workbench" / "repos.json").write_text(json.dumps({
        "portalk": "/home/ubuntu/Portalk",
        "tideline": "/home/ubuntu/tideline-memory",
        "orbi": "/home/ubuntu/orbi-repo",
    }), encoding="utf-8")
    return root

def add_ledger(root, events):
    with open(root / "ledger.jsonl", "a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

def msgs(*texts):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": t} for i, t in enumerate(texts)]

NOW = time.mktime(time.strptime("2026-08-23T18:00:00", "%Y-%m-%dT%H:%M:%S"))

print("== 触达检测 ==")
r = mk_root()
check("路径签名命中", sh.touched_projects(msgs("我看下 cd /home/ubuntu/Portalk/src"), r) == {"portalk"})
check("多桌", sh.touched_projects(msgs("cd /home/ubuntu/Portalk/src 和 /home/ubuntu/tideline-memory 都要动"), r) == {"portalk", "tideline"})
check("零触达", sh.touched_projects(msgs("今天聊了点别的"), r) == set())
check("tool结果不扫", sh.touched_projects([{"role": "tool", "content": "/home/ubuntu/Portalk"}], r) == set())
check("hardcode: Grimoire→portalk", sh.touched_projects(msgs("巡山 cd ~/Agent-Grimoire"), r) == {"portalk"})
check("hardcode: Stigmergy→memory-wash", sh.touched_projects(msgs("cd /home/ubuntu/Stigmergy 改spoor_common"), r) == {"memory-wash"})
check("multimodal list content", sh.touched_projects([{"role": "user", "content": [{"type": "text", "text": "去 /home/ubuntu/orbi-repo"}]}], r) == {"orbi"})

print("== 缺口计算 ==")
r2 = mk_root()
add_ledger(r2, [
    {"event": "threesome.journal.write", "project": "portalk", "ts": "2026-08-23T12:07:00"},
    {"event": "threesome.journal.read", "project": "orbi", "ts": "2026-08-23T15:00:00"},
])
n = sh.session_gap_nudge(msgs("cd /home/ubuntu/Portalk 干活"), r2, now=NOW)
check("5.9h前未写→提醒", n is not None and "portalk(6h前)" in n)
n2 = sh.session_gap_nudge(msgs("cd /home/ubuntu/Portalk 干活"), r2, now=NOW)
# 刚写过账本里的 12:07 是 6h 前——但换个刚写的场景:
add_ledger(r2, [{"event": "threesome.journal.write", "project": "portalk", "ts": "2026-08-23T17:00:00"}])
n3 = sh.session_gap_nudge(msgs("cd /home/ubuntu/Portalk 干活"), r2, now=NOW)
check("1h前刚写→静默", n3 is None)
n4 = sh.session_gap_nudge(msgs("/home/ubuntu/orbi-repo 全调研"), r2, now=NOW)
check("从未写→提醒", n4 is not None and "orbi(从未写)" in n4)
n5 = sh.session_gap_nudge(msgs("纯聊天没碰项目"), r2, now=NOW)
check("零触达→None", n5 is None)

print("== 静默失败 ==")
r3 = mk_root()
(r3 / "workbench" / "repos.json").write_text("{broken json", encoding="utf-8")
check("坏repos.json→零触达不炸", sh.session_gap_nudge(msgs("cd /home/ubuntu/Portalk"), r3, now=NOW) is None)
check("messages=None→None", sh.session_gap_nudge(None, r3, now=NOW) is None)
check("root不存在→None", sh.session_gap_nudge(msgs("cd /home/ubuntu/Portalk"), Path("/nonexistent/xx"), now=NOW) is None)

print("== 管线集成（真实 spoor_common） ==")
import spoor_common as sc
import os
r4 = mk_root()
os.environ["STIGMERGY_ROOT"] = str(r4)
import importlib
importlib.reload(sc)
sc.LEDGER = r4 / "ledger.jsonl"
add_ledger(r4, [
    {"event": "threesome.journal.write", "project": "portalk", "ts": "2026-08-23T12:07:00"},
])
m = msgs("cd /home/ubuntu/Portalk/src 干了一下午", "改了 spoor_hooks 的逻辑")
t = sc.record_session_gap(m, root=r4)
check("record落账本", t is not None and "portalk" in t)
gap_line = [json.loads(l) for l in open(r4 / "ledger.jsonl", encoding="utf-8") if "spoor.session.gap" in l]
check("gap事件在账本", len(gap_line) == 1 and gap_line[0]["projects"] == ["portalk"])
g2 = sc.pending_sessgap()
check("未消费→浮现", g2 is not None and "portalk" in g2)
out = sc.nudge_text("工具正文")
check("nudge_text搭车", "_nudge]" in out or "[nudge]" in out)
shown = [json.loads(l) for l in open(r4 / "ledger.jsonl", encoding="utf-8") if '"ch": "sessgap"' in l]
check("消费即记录", len(shown) == 1)
g3 = sc.pending_sessgap()
check("消费后→静默", g3 is None)
os.environ["STIGMERGY_ROOT"] = str(Path.home() / "Stigmergy")
importlib.reload(sc)

print(f"\n{PASS} pass / {FAIL} fail")
sys.exit(1 if FAIL else 0)
