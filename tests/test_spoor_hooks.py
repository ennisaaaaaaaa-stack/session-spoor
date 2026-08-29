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
    # 幽灵桌守卫（照照 8/23 审）后：BARE_NAMES 目标桌须真实存在才命中——
    # 测试环境与生产同构，四张会被 BARE_NAMES 命中的桌目录要建出来。
    (root / "workbench" / "portalk").mkdir()
    (root / "workbench" / "memory-wash").mkdir()
    (root / "workbench" / "grimoire").mkdir()
    (root / "workbench" / "repos.json").write_text(json.dumps({
        "portalk": "/home/ubuntu/Portalk",
        "tideline": "/home/ubuntu/tideline-memory",
        "orbi": "/home/ubuntu/orbi-repo",
        # v0.6 对象形态：别名进数据层（BARE_NAMES 硬编码已拆雷）
        "grimoire": {"path": "/home/ubuntu/Agent-Grimoire"},
        "memory-wash": {"path": "/home/ubuntu/Stigmergy"},
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
check("hardcode: Grimoire→grimoire", sh.touched_projects(msgs("巡山 cd ~/Agent-Grimoire"), r) == {"grimoire"})
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
# 普适发现（v0.5）会探测真实 home 的 .git——密封测试传假 home，探测必空。
FAKE_HOME = Path(tempfile.mkdtemp(prefix="spoor_fake_home_"))
check("坏repos.json→零触达不炸", sh.session_gap_nudge(msgs("cd /home/ubuntu/Portalk"), r3, now=NOW, home=FAKE_HOME) is None)
check("messages=None→None", sh.session_gap_nudge(None, r3, now=NOW, home=FAKE_HOME) is None)
check("root不存在→None", sh.session_gap_nudge(msgs("cd /home/ubuntu/Portalk"), Path("/nonexistent/xx"), now=NOW, home=FAKE_HOME) is None)

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
g2 = sc.pending_sessgap(root=r4)
check("未消费→浮现", g2 is not None and "portalk" in g2)
out = sc.nudge_text("工具正文")
check("nudge_text搭车", "_nudge]" in out or "[nudge]" in out)
shown = [json.loads(l) for l in open(r4 / "ledger.jsonl", encoding="utf-8") if '"ch": "sessgap"' in l]
check("消费即记录", len(shown) == 1)
g3 = sc.pending_sessgap(root=r4)
check("消费后→静默", g3 is None)
os.environ["STIGMERGY_ROOT"] = str(Path.home() / "Stigmergy")
importlib.reload(sc)
# 照照审补充：root 参数化的正确性——不 reload 不改全局，直接传 root，
# 与上面 reload+改全局的旧路径产出必须一致（同账本两种读法）。
# r4 账本里 gap 已被 nudge_text 消费（ch=sessgap shown 在后）→ 两种读法都应静默。
g4 = sc.pending_sessgap(root=r4)
check("root参数=reload旧路径同结果(消费后→静默)", g4 is None)

print("== v0.5 普适发现 ==")
import shutil
# 密封环境：假 home 下造假 git 目录，探测与真实机器解耦
fh = Path(tempfile.mkdtemp(prefix="spoor_v05_home_"))
(fh / "new-proj").mkdir(); (fh / "new-proj" / ".git").mkdir()
(fh / "upstream-clone").mkdir(); (fh / "upstream-clone" / ".git").mkdir()
(fh / "plain-dir").mkdir()  # v0.5 时代无 .git 不算项目；v0.6 起算弱候选
(fh / "node_modules").mkdir()  # 系统目录：编译期卫生常量，永不做候选
(fh / "ocean-env").mkdir()  # venv 后缀：同上
r5 = mk_root()
(r5 / "workbench" / "repos.json").write_text(json.dumps({
    "portalk": "/home/ubuntu/Portalk",
    "grimoire": "/home/ubuntu/Agent-Grimoire",
}), encoding="utf-8")

d = sh.discover_projects("cd ~/new-proj 干活", r5, home=fh)
check("发现新git项目", d == {"new-proj": {"path": str(fh / "new-proj"), "kind": "git"}})

check("目录存在即弱候选", sh.discover_projects("在 ~/plain-dir 整理素材", r5, home=fh) == {"plain-dir": {"path": str(fh / "plain-dir"), "kind": "dir"}})
check("系统目录不候选", sh.discover_projects("npm 装到 ~/node_modules 了", r5, home=fh) == {})
check("venv后缀不候选", sh.discover_projects("装依赖 ~/ocean-env", r5, home=fh) == {})

# 豁免表：裁决=登记，登记后不再发现
(r5 / "workbench" / "ignore.json").write_text(json.dumps({"upstream-clone": "上游克隆，不开桌"}), encoding="utf-8")
d2 = sh.discover_projects("在 ~/upstream-clone 和 ~/new-proj 都有活动", r5, home=fh)
check("ignore.json豁免", d2 == {"new-proj": {"path": str(fh / "new-proj"), "kind": "git"}})

# 已登记项目（含路径 basename 自动别名）不算新
d3 = sh.discover_projects("巡山 ~/Agent-Grimoire", r5, home=fh)
check("已登记别名不算新", d3 == {})

# 前缀吞噬修复：Portalk-latest 不再误中 portalk 桌
check("前缀吞噬修复", sh.touched_projects(msgs("cd /home/ubuntu/Portalk-latest 看旧版"), r5) == set())

# ~/短写法经 basename 映射命中桌
check("~/短写法命中", sh.touched_projects(msgs("去 ~/Portalk/src 改代码"), r5) == {"portalk"})

# 发现进 nudge 且账本去重：第一次报、落账后第二次静默
n_d1 = sh.session_gap_nudge(msgs("cd ~/new-proj 干了一下午"), r5, now=NOW, home=fh)
check("发现→nudge", n_d1 is not None and "new-proj" in n_d1)
add_ledger(r5, [{"event": "spoor.session.gap", "ts": "2026-08-23T17:30:00", "new_projects": ["new-proj"]}])
n_d2 = sh.session_gap_nudge(msgs("cd ~/new-proj 又干了一下午"), r5, now=NOW, home=fh)
check("落账去重→静默", n_d2 is None)

# 真实机器冒烟：本机已知桌的裸名/路径不应被当成新项目
d_smoke = sh.discover_projects("cd ~/Stigmergy 和 ~/Agent-Grimoire 巡了一圈", Path.home() / "Stigmergy")
check("真实冒烟:已知桌不是新项目", d_smoke == {})

shutil.rmtree(fh)

print("== v0.6 归一化管道 ==")
# 反转查找方向：全路径不再走 find 裸查找，前缀嵌套天然安全
fh6 = Path(tempfile.mkdtemp(prefix="spoor_v06_home_"))
r6 = mk_root()
(r6 / "workbench" / "tooling").mkdir()  # 幽灵桌守卫：虚拟桌目录要真实存在
(r6 / "workbench" / "repos.json").write_text(json.dumps({
    "portalk": "/home/ubuntu/Portalk",
    "grimoire": {"path": "/home/ubuntu/Agent-Grimoire", "aliases": ["山海", "山海库", "shanhai"]},
    "tooling": {"aliases": ["工具层提案"]},  # 虚拟桌：无 path
}), encoding="utf-8")

check("全路径命中(token方向)", sh.touched_projects(msgs("cd /home/ubuntu/Portalk/src"), r6) == {"portalk"})
check("裸名别名命中", sh.touched_projects(msgs("巡山去 山海 看了一眼"), r6) == {"grimoire"})
check("CJK前缀防火墙", sh.touched_projects(msgs("山海经 是另一本书"), r6) == set())
check("CJK虚词边界", sh.touched_projects(msgs("山海的三层门设计好了"), r6) == {"grimoire"})
check("多别名同身份", sh.touched_projects(msgs("shanhai 和 山海库 是同一个"), r6) == {"grimoire"})
check("中文别名命中", sh.touched_projects(msgs("工具层提案 v0.3 过一遍"), r6) == {"tooling"})
check("虚拟桌命中", sh.touched_projects(msgs("把 工具层提案 的三道题列出来"), r6) == {"tooling"})
check("未知裸名不误伤", sh.touched_projects(msgs("随便聊聊 tideline-memory 的事"), r6) == set())

# home 动态化：假 home 下 ~/名字 也能命中（token 正则按 home 现编）
(fh6 / "Portalk").mkdir()
check("home动态化", sh.touched_projects(msgs("cd ~/Portalk 改代码"), r6, home=fh6) == {"portalk"})

# 宣言旗：有宣言无目录 → 打旗提示复核
n6 = sh.session_gap_nudge(msgs("我们开了个新坑，还没建目录"), r6, now=NOW, home=fh6)
check("宣言旗打旗", n6 is not None and "宣言" in n6)
# 宣言+已知桌触达（有目录信号）→ 不打宣言旗
n7 = sh.session_gap_nudge(msgs("开了个新坑 ~/Portalk 先改这个"), r6, now=NOW, home=fh6)
check("宣言+目录不打旗", n7 is not None and "宣言" not in n7)

shutil.rmtree(fh6)

print(f"\n{PASS} pass / {FAIL} fail")
sys.exit(1 if FAIL else 0)
