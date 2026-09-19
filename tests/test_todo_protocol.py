"""v0.9 待办协议测试（2026-09-19）。

覆盖共用层：parse_next_steps / todo_receipt_ids / 出账孤儿不变量。
自执行风格（与 session-spoor tests 同款，pytest 不认）。
server 层的 status.write 事件在 v0.9 迁移时真跑验证过（ledger 六桌事件）。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import spoor_common as sc

FAILS = []


def check(name, cond, detail=""):
    print(f"{'pass' if cond else 'FAIL'} / {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


DOC_FULL = """# STATUS · 更新于 2026-09-19 12:00

## 做到哪
x

## 下一步
> 排序确认：2026-09-19（甜心）·确认至第2条
- T12 [洄] 修六轨器乐误判 ｜协商9/19「先复测」｜判据：六轨复测全对
- T13 [甜心] Orbi口径三题 ｜派单9/18「材料提前」｜判据：口径表入库
- T14 [鸣鸣] 游戏面板 ｜分工表9/1｜判据：面板过契约对照

## 卡在哪
不卡。
"""


def t_parser():
    r = sc.parse_next_steps(DOC_FULL)
    check("section found", r["section_found"])
    check("confirm parsed", r["confirm"] == {"date": "2026-09-19", "by": "甜心", "count": 2}, str(r["confirm"]))
    check("3 items", len(r["items"]) == 3)
    t12 = r["items"][0]
    check("fields", (t12["id"], t12["owner"], t12["head"]) == ("T12", "洄", "修六轨器乐误判"))
    check("source", t12["source"] == "协商9/19「先复测」", t12["source"])
    check("criteria", t12["criteria"] == "六轨复测全对", t12["criteria"])
    check("sorted flag", [i["sorted"] for i in r["items"]] == [True, True, False])
    check("complete flag", all(i["complete"] for i in r["items"]))
    check("no malformed", r["malformed"] == [])

    # 老格式行内式 → 找到段但不炸
    r2 = sc.parse_next_steps("# S\n\n下一步：装 box，修误判。\n")
    check("legacy inline tolerated", r2["section_found"] and r2["items"] == [])

    # 老格式条目（无T号）→ malformed 等迁移
    r3 = sc.parse_next_steps("# S\n\n## 下一步\n- [洄] 三层门施工\n")
    check("legacy item malformed", len(r3["malformed"]) == 1)

    # ASCII 竖线也认；判据段含竖线归判据（join 回去时规范化为全角）
    r5 = sc.parse_next_steps("# S\n\n## 下一步\n- T2 [照照] b | s | 判据： a|b\n")
    it = r5["items"][0]
    check("ascii pipe", it["source"] == "s" and it["criteria"] == "a｜b", str(it))

    # 确认数超过条目数 → 全部 sorted
    r6 = sc.parse_next_steps("# S\n\n## 下一步\n> 排序确认：2026-09-19（甜心）·确认至第5条\n- T1 [洄] a ｜x｜判据：y\n")
    check("overcount confirm", r6["items"][0]["sorted"] is True)

    # 无段
    check("no section", sc.parse_next_steps("# S\n\n## 做到哪\nn\n")["section_found"] is False)

    # 不完整条目：缺出处 → complete=False（进不了待办，lint 层面）
    r7 = sc.parse_next_steps("# S\n\n## 下一步\n- T1 [洄] 只有标题\n")
    check("incomplete no source", r7["items"][0]["complete"] is False)

    # 平标签混排（鸣鸣审稿发现#2 的回归断言）：老式「下一步：」独占行 +
    # 平标签「卡在哪：」——段截止到平标签，不把后段吞进 malformed。
    r8 = sc.parse_next_steps(
        "# S\n\n下一步：\n- T1 [洄] a ｜x｜判据：y\n卡在哪：无\n")
    check("flat-label boundary items", len(r8["items"]) == 1
          and r8["items"][0]["id"] == "T1", str(r8["items"]))
    check("flat-label no malformed", r8["malformed"] == [], str(r8["malformed"]))


def t_receipts():
    check("ok receipt", sc.todo_receipt_ids("✅T12 判据达成") == {"T12"})
    check("return receipt", sc.todo_receipt_ids("↩T13 退回待审") == {"T13"})
    check("bare id no receipt", sc.todo_receipt_ids("T12 裸提不算") == set())
    check("multi", sc.todo_receipt_ids("先 ✅T1 后 ✅T2，还有↩T3") == {"T1", "T2", "T3"})


def t_orphan_invariant():
    """出账孤儿不变量：消失的 id 必须有收据。fixture 全链。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        desk = root / "workbench" / "proj"
        (desk / "journal").mkdir(parents=True)
        # v1 盘面：T1 T2 T3
        st1 = "# S\n\n## 下一步\n- T1 [洄] a ｜x｜判据：y\n- T2 [洄] b ｜x｜判据：y\n- T3 [洄] c ｜x｜判据：y\n"
        (desk / "STATUS.md").write_text(st1, encoding="utf-8")
        seen = set(i["id"] for i in sc.parse_next_steps(st1)["items"])
        # v2 盘面：T1 T2 出账（T1 有收据，T2 没有），T3 留
        st2 = "# S\n\n## 下一步\n- T3 [洄] c ｜x｜判据：y\n"
        disk2 = set(i["id"] for i in sc.parse_next_steps(st2)["items"])
        (desk / "journal" / "2026-09-19.md").write_text(
            "- **[判断]** 2026-09-19 ✅T1 判据达成\n", encoding="utf-8")
        rcpt = sc.todo_receipt_ids(
            "".join(p.read_text(encoding="utf-8") for p in (desk / "journal").glob("*.md")))
        orphans = (seen | disk2) - disk2 - rcpt
        check("orphan detected", orphans == {"T2"}, str(orphans))
        # 补上 T2 收据后孤儿消失
        (desk / "journal" / "2026-09-19.md").write_text(
            "- **[判断]** 2026-09-19 ✅T1 判据达成\n- **[判断]** 2026-09-19 ↩T2 退回待审\n", encoding="utf-8")
        rcpt2 = sc.todo_receipt_ids(
            "".join(p.read_text(encoding="utf-8") for p in (desk / "journal").glob("*.md")))
        check("orphan cured", ((seen | disk2) - disk2 - rcpt2) == set())


DOC_PLAIN = """# STATUS · 更新于 2026-09-20 03:00

## 做到哪
x

## 下一步
- T1 [洄] a ｜x｜判据：y
"""


def t_view_tristate():
    """桥端三态（鸣鸣 v0.9 审稿发现#1 的回归断言）。

    同桌 fixture 分两态钉死，别只钉一头：
    - _sc 正常 + STATUS 缺席      → todo_parsed 键整体不在
    - _sc 正常 + STATUS 在场无段  → section_found=false、键在
    第三态（_sc=None，残缺部署）用 monkeypatch 钉：键也不在。
    """
    import spoor_view as sv

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        desk = root / "proj"
        desk.mkdir()
        # _sc 正常 + STATUS 缺席 → 键整体不在
        p1 = sv._project(desk)
        check("absent: no key", "todo_parsed" not in p1, str(p1.get("todo_parsed")))

        # _sc 正常 + STATUS 在场无下一步段 → 键在、section_found=false
        (desk / "STATUS.md").write_text("# S\n\n## 做到哪\nn\n", encoding="utf-8")
        p2 = sv._project(desk)
        tp = p2.get("todo_parsed")
        check("no-section: key present", "todo_parsed" in p2)
        check("no-section: section_found=false",
              isinstance(tp, dict) and tp.get("section_found") is False, str(tp))

        # 有段 → 键在、解析出条目
        (desk / "STATUS.md").write_text(DOC_PLAIN, encoding="utf-8")
        p3 = sv._project(desk)
        tp3 = p3.get("todo_parsed")
        check("parsed: items out", isinstance(tp3, dict)
              and [i["id"] for i in tp3["items"]] == ["T1"], str(tp3))

    # _sc=None（残缺部署）→ 键不在（monkeypatch，测完还原）
    saved = sv._sc
    try:
        sv._sc = None
        with tempfile.TemporaryDirectory() as td:
            desk = Path(td) / "proj2"
            desk.mkdir()
            (desk / "STATUS.md").write_text(DOC_PLAIN, encoding="utf-8")
            p4 = sv._project(desk)
            check("degraded: no key", "todo_parsed" not in p4,
                  str(p4.get("todo_parsed")))
    finally:
        sv._sc = saved


def main():
    t_parser()
    t_receipts()
    t_orphan_invariant()
    t_view_tristate()
    n = len(FAILS)
    print(f"\n{'ALL PASS' if not n else f'{n} FAIL'}")
    sys.exit(1 if n else 0)


if __name__ == "__main__":
    main()
