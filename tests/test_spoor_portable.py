#!/usr/bin/env python3
"""session-spoor v0.2 回归测试：stdio transport 全工具 + v0.2 新契约。"""
import asyncio, json, os, shutil, sys, tempfile, traceback
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

VENV_PY = os.environ.get("SPOOR_TEST_PY", sys.executable)
HERE = __import__("pathlib").Path(__file__).resolve().parent
def _find(name):
    for cand in (HERE / name, HERE.parent / name, HERE.parent / "session-spoor" / name):
        if cand.exists(): return str(cand)
    raise FileNotFoundError(name)
SCRATCH_SERVER = _find("scratchpad_server.py")
WORKBENCH_SERVER = _find("workbench_server.py")
ROOT = tempfile.mkdtemp(prefix="spoor_test_")

results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))
    print(f"{'PASS' if cond else 'FAIL'} {name} {str(detail)[:200]}")

async def call(session, tool, **kw):
    r = await session.call_tool(tool, kw)
    texts = [c.text for c in r.content if hasattr(c, "text")]
    return "\n".join(texts) if texts else f"__NO_TEXT__ isError={getattr(r, 'isError', None)}"

async def scratch_suite():
    params = StdioServerParameters(
        command=VENV_PY, args=[SCRATCH_SERVER],
        env={"STIGMERGY_ROOT": ROOT, "PATH": "/usr/bin:/bin"},
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            lt = await s.list_tools()
            check("scratch tools listed", len(lt.tools) == 8, [t.name for t in lt.tools])

            out = json.loads(await call(s, "scratchpad_create", task_id="review_测试1", label="t"))
            check("create sanitizes non-ascii task_id", out["space_id"].startswith("review"), out["space_id"])
            sid = out["space_id"]

            w1 = json.loads(await call(s, "scratchpad_write", space_id=sid, path="notes/plan.md", content="plan v1\n"))
            check("write ok", w1.get("ok"), w1)
            await call(s, "scratchpad_write", space_id=sid, path="notes/plan.md", content="line2\n", mode="append")
            rd = await call(s, "scratchpad_read", space_id=sid, path="notes/plan.md")
            check("append works", "line2" in rd, rd)
            rd2 = await call(s, "scratchpad_read", space_id=sid, path="notes/plan.md", offset=1, limit=1)
            check("read offset works", rd2.startswith("2|"), rd2)

            # ---- v0.2 错误契约：所有异常必须转JSON，不再出现裸文本 Error executing ----
            e1 = await call(s, "scratchpad_write", space_id=sid, path="../evil.md", content="x")
            ok1 = False; d1 = e1
            try:
                j1 = json.loads(e1); ok1 = (j1.get("ok") is False) and ("must be relative" in j1.get("error", ""))
            except Exception: pass
            check("[v0.2] escape error is JSON {ok:false}", ok1, e1[:150])
            check("no file escaped", not Path(ROOT, "evil.md").exists(), "")

            e2 = await call(s, "scratchpad_write", space_id=sid, path="/etc/passwd", content="x")
            try:
                j2 = json.loads(e2); ok2 = j2.get("ok") is False
            except Exception: ok2 = False
            check("[v0.2] absolute path error is JSON", ok2, e2[:150])

            e3 = await call(s, "scratchpad_read", space_id=f"{sid}/../..", path="ledger.jsonl")
            try:
                j3 = json.loads(e3); ok3 = j3.get("ok") is False and "invalid" in j3.get("error", "")
            except Exception: ok3 = False
            check("[v0.2] bad space_id error is JSON", ok3, e3[:150])

            # mark
            m1 = json.loads(await call(s, "scratchpad_mark", space_id=sid, path="notes/plan.md", mark="判断"))
            check("mark ok", m1.get("ok") and "判断" in m1["marks"], m1)
            await call(s, "scratchpad_write", space_id=sid, path="data.bin", content="\x00\x01binary")
            m2 = json.loads(await call(s, "scratchpad_mark", space_id=sid, path="data.bin", mark="坑"))
            check("mark binary file ok", m2.get("ok"), m2)
            m3 = json.loads(await call(s, "scratchpad_mark", space_id=sid, path="notes/plan.md", mark="banana"))
            check("bad mark rejected", not m3.get("ok", True), m3)

            ls = json.loads(await call(s, "scratchpad_list", space_id=sid))
            names = [e["file"] for e in ls]
            check("list shows files with marks", "notes/plan.md" in names and "data.bin" in names, names)

            st = json.loads(await call(s, "scratchpad_status", space_id=sid))
            check("status shape", st["by_mark"].get("判断") == 1 and st["marked_files"] == 2, st)

            ex = json.loads(await call(s, "scratchpad_export", space_id=sid, selection="marked", dest="exports/review-bundle.md"))
            check("export marked", ex.get("ok") and ex.get("exported") == 2, ex)
            bundle = Path(ROOT, "exports/review-bundle.md").read_text(encoding="utf-8")
            check("bundle 判断 before 坑", bundle.find("plan.md") < bundle.find("data.bin"), bundle[:80])
            ex2 = json.loads(await call(s, "scratchpad_export", space_id=sid, selection="marked", dest="exports/review-bundle2.md"))
            check("re-export dedupe", ex2.get("exported") == 0, ex2)

            # ---- v0.2 dest安全边界 ----
            ed1 = await call(s, "scratchpad_export", space_id=sid, selection="marked", dest="/tmp/spoor_escape_target.md")
            try:
                jd1 = json.loads(ed1); okd1 = jd1.get("ok") is False and "must stay under" in jd1.get("error", "")
            except Exception: okd1 = False
            check("[v0.2] absolute dest outside ROOT rejected", okd1, ed1[:150])
            ed2 = await call(s, "scratchpad_export", space_id=sid, selection="marked", dest="exports/../../tmp/spoor_escape2.md")
            try:
                jd2 = json.loads(ed2); okd2 = jd2.get("ok") is False
            except Exception: okd2 = False
            check("[v0.2] dotdot dest rejected", okd2, ed2[:150])
            check("[v0.2] no dest escape on disk",
                  not Path("/tmp/spoor_escape_target.md").exists() and not Path("/tmp/spoor_escape2.md").exists(), "")

            # export selection 传目录名 → 应JSON报错不裸抛
            exd = await call(s, "scratchpad_export", space_id=sid, selection='["notes"]', dest="exports/dir-bundle.md")
            try:
                jx = json.loads(exd); okx = jx.get("ok") is False
            except Exception: okx = False
            check("[v0.2] dir selection error is JSON", okx, exd[:150])

            # 真二进制落盘 → export binary分支
            out3 = json.loads(await call(s, "scratchpad_create", task_id="binfile"))
            sid3 = out3["space_id"]
            await call(s, "scratchpad_write", space_id=sid3, path="real.bin", content="placeholder")
            Path(ROOT, "scratch", sid3, "real.bin").write_bytes(b"\xff\xfe\x00bad bytes")
            await call(s, "scratchpad_mark", space_id=sid3, path="real.bin", mark="数据")
            ex3 = json.loads(await call(s, "scratchpad_export", space_id=sid3, selection='["real.bin"]', dest="exports/bin-bundle.md"))
            bin_bundle = Path(ROOT, "exports/bin-bundle.md").read_text(encoding="utf-8")
            check("binary placeholder branch works", "(binary file" in bin_bundle, bin_bundle[:150])
            cl3 = json.loads(await call(s, "scratchpad_cleanup", space_id=sid3, mode="discard"))
            check("cleanup bin space", cl3.get("ok"), cl3)

            # 纯中文task_id → safe_id回退"task"，账本保留原名
            outc = json.loads(await call(s, "scratchpad_create", task_id="中文任务"))
            check("[v0.2] all-non-ascii task_id falls back", outc["space_id"].startswith("task-"), outc["space_id"])

            out2 = json.loads(await call(s, "scratchpad_create", task_id="tmp2"))
            sid2 = out2["space_id"]
            await call(s, "scratchpad_write", space_id=sid2, path="a.md", content="a")
            cl = json.loads(await call(s, "scratchpad_cleanup", space_id=sid2, mode="discard"))
            check("cleanup discard", cl.get("ok"), cl)
            cl2 = json.loads(await call(s, "scratchpad_cleanup", space_id=sid2, mode="discard"))
            check("cleanup idempotent", cl2.get("ok") and "no-op" in cl2.get("note", ""), cl2)

async def workbench_suite():
    params = StdioServerParameters(
        command=VENV_PY, args=[WORKBENCH_SERVER],
        env={"STIGMERGY_ROOT": ROOT, "PATH": "/usr/bin:/bin"},
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            lt = await s.list_tools()
            check("workbench tools listed", len(lt.tools) == 10, str([t.name for t in lt.tools]))

            n1 = json.loads(await call(s, "workbench_new", project="stigmergy", description="agent过程管理系统"))
            check("new ok", n1.get("ok"), n1)
            n2 = json.loads(await call(s, "workbench_new", project="stigmergy", description="dup"))
            check("dup blocked", not n2.get("ok", True), n2)
            n3 = json.loads(await call(s, "workbench_new", project="bad name!", description="x"))
            check("bad name blocked", not n3.get("ok", True), n3)

            sw = json.loads(await call(s, "workbench_status", project="stigmergy", text="做到哪: 代码写完\n下一步: review\n卡在: 无"))
            check("status write", sw.get("ok"), sw)
            sr = await call(s, "workbench_status", project="stigmergy")
            check("status read", "做到哪" in sr, sr[:100])

            j1 = json.loads(await call(s, "workbench_journal", project="stigmergy", entry="FTS trigger必须先DROP", mark="坑"))
            check("journal 坑", j1.get("ok"), j1)
            await call(s, "workbench_journal", project="stigmergy", entry="压缩threshold定0.625", mark="判断")
            await call(s, "workbench_journal", project="stigmergy", entry="早期数据", mark="数据")

            rj = await call(s, "workbench_read_journal", project="stigmergy", mark="坑")
            check("filter 坑 exact", "FTS trigger" in rj and "早期数据" not in rj, rj)
            # v0.2契约: read_journal 带 reason 参数（裁决3——字段必须有真实数据来源）
            rjr = await call(s, "workbench_read_journal", project="stigmergy", mark="坑", reason="开工仪式")
            check("[v0.2] read_journal accepts reason", "FTS trigger" in rjr, rjr[:100])
            # 正文含"[坑]"字样但mark=判断 → 不应被捞
            await call(s, "workbench_journal", project="stigmergy", entry="文档里看到[坑]这个词但不是坑", mark="判断")
            rjf = await call(s, "workbench_read_journal", project="stigmergy", mark="坑")
            check("filter no false-positive", "这个词但不是坑" not in rjf, rjf[-200:])

            # 多天journal limit语义：旧文件5条+今天1条，limit=2应返回最新2条
            jr = Path(ROOT, "workbench", "stigmergy", "journal")
            (jr / "2026-01-01.md").write_text(
                "# 2026-01-01\n" + "\n".join(f"- **[坑]** 2026-01-01 0{i}:00 旧坑{i}" for i in range(5)) + "\n",
                encoding="utf-8")
            rjm = await call(s, "workbench_read_journal", project="stigmergy", mark="坑", limit=2)
            check("limit takes newest", "FTS trigger" in rjm and "旧坑0" not in rjm, rjm[:200])

            # ---- v0.3.1 FTS降级：短query/空query + 过滤维度 ≠ 空手而归 ----
            # trigram最小粒度3：query="坑"(1字)原本永远no hits——开工先读坑仪式直接失效
            sr1 = await call(s, "workbench_search", query="坑", type="journal:坑")
            check("[v0.3.1] single-char query + type filter degrades to filtered list",
                  "FTS trigger" in sr1 and "旧坑" in sr1 and "(no hits)" not in sr1, sr1[:200])
            sr2 = await call(s, "workbench_search", query="", type="journal:坑")
            check("[v0.3.1] empty query + type filter lists all 坑",
                  "FTS trigger" in sr2 and "旧坑" in sr2, sr2[:200])
            sr3 = await call(s, "workbench_search", query="", type="", project="", agent="")
            check("[v0.3.1] no query no filter → clean empty, no crash",
                  sr3 == "(no hits)", sr3[:100])
            # path显示走Path语义（Windows \分隔符下split('workbench/')失效）
            check("[v0.3.1] search result path is relative (Path semantics)",
                  "stigmergy/journal/" in sr1, sr1[:120])

            sn = json.loads(await call(s, "workbench_snippet", project="stigmergy", name="utils/fix.py", content="print('hi')"))
            check("snippet save nested", sn.get("ok"), sn)
            got = await call(s, "workbench_snippet", project="stigmergy", name="utils/fix.py")
            check("snippet get", "print" in got, got)

            # ---- v0.2 契约断言：六类账本事件带全前缀 ----
            # （此时点 complete 还没发生，它在 suite 末尾——末尾 main 里另有断言）
            led = [json.loads(l) for l in Path(ROOT, "ledger.jsonl").read_text(encoding="utf-8").strip().splitlines()]
            kinds = {e["event"] for e in led}
            check("[v0.2] ledger has prefixed wb events",
                  {"threesome.workbench.new", "threesome.journal.write",
                   "threesome.journal.read", "threesome.journal.search"} <= kinds, sorted(kinds))
            check("[v0.2] no bare legacy event names",
                  not [k for k in kinds if k in ("wb_new", "wb_complete")], sorted(kinds))
            jw = next(e for e in led if e["event"] == "threesome.journal.write")
            check("[v0.2] journal.write has entry_head<=80 & five-value mark",
                  0 < len(jw["entry_head"]) <= 80 and jw["mark"] in ("判断", "数据", "坑", "待审·自", "待审·人"), jw)
            jr = next(e for e in led if e["event"] == "threesome.journal.read" and e.get("reason"))
            check("[v0.2] journal.read reason routed from tool arg", jr.get("reason") == "开工仪式", jr)
            sg = next((e for e in led if e["event"] == "threesome.workbench.snippet_get"), None)
            check("[v0.2] snippet_get ledgered (裁决5)", sg and sg["name"] == "utils/fix.py", sg)
            js = next(e for e in led if e["event"] == "threesome.journal.search")
            check("[v0.2] journal.search hits=条数不记内容", isinstance(js.get("hits"), int) and "fragment" not in js, js)

            # ---- ledger_query：读取工具（审计层唯一读通道）----
            ledpath = Path(ROOT, "ledger.jsonl")
            q0 = await call(s, "ledger_query", limit=3)
            check("[ledger_query] head shows totals", q0.startswith("(ledger") and "hits" in q0, q0[:80])
            check("[ledger_query] 倒序返回原始JSONL行", all(l.startswith("{") for l in q0.splitlines()[1:]), q0[:120])
            qk = await call(s, "ledger_query", kind="cleanup", limit=5)
            check("[ledger_query] kind前缀过滤命中cleanup", all('"event": "cleanup"' in l for l in qk.splitlines()[1:]), qk[:200])
            qn = await call(s, "ledger_query", contains="不存在的字符串xyz", limit=5)
            check("[ledger_query] contains无命中→no match", "no match" in qn, qn)
            # 反自我放大：查询动作本身零写入（查询前后账本行数不变）
            n_before = len(ledpath.read_text(encoding="utf-8").strip().splitlines())
            q1 = await call(s, "ledger_query", kind="threesome.", limit=2)
            q2 = await call(s, "ledger_query", contains="journal", limit=2)
            n_after = len(ledpath.read_text(encoding="utf-8").strip().splitlines())
            check("[ledger_query] 读账本不记账(反自我放大)", n_after == n_before, f"{n_before}→{n_after}")
            # 翻页：skip_recent 跳过最新命中后仍能取到更早的
            qp1 = await call(s, "ledger_query", limit=2)
            qp2 = await call(s, "ledger_query", limit=2, skip_recent=2)
            check("[ledger_query] skip_recent翻页无重叠", qp1.splitlines()[1:] != qp2.splitlines()[1:], qp1[:60] + " vs " + qp2[:60])
            # 照照 round 7 边界测试收编：脏行跳过不炸（账本里混进非JSON行，查询降级继续）
            Path(ROOT, "dirty-ledger-probe").write_text("x", encoding="utf-8")  # 占位标记本沙箱
            lp = Path(ROOT, "ledger.jsonl")
            with open(lp, "a", encoding="utf-8") as f:
                f.write("{{{ this is NOT json\n")  # 脏行（未闭合花括号）
            qd = await call(s, "ledger_query", limit=5)
            check("[ledger_query][r7] 脏行跳过不炸", qd.startswith("(ledger"), qd[:60])

            # ---- 空账本：全新ROOT第二个服务端实例，ledger.jsonl不存在 ----
            import tempfile as _tf
            ROOT_EMPTY = _tf.mkdtemp(prefix="spoor_empty_")
            params_e = StdioServerParameters(command=VENV_PY, args=[WORKBENCH_SERVER],
                                             env={"STIGMERGY_ROOT": ROOT_EMPTY, "PATH": "/usr/bin:/bin", "HOME": ROOT_EMPTY})
            async with stdio_client(params_e) as (r2, w2):
                async with ClientSession(r2, w2) as s2:
                    await s2.initialize()
                    qe = await call(s2, "ledger_query", limit=5)
                    check("[ledger_query][r7] 空账本不炸", qe.startswith("(ledger 0 lines") and "no match" in qe, qe[:60])
            shutil.rmtree(ROOT_EMPTY, ignore_errors=True)
            Path(ROOT, "dirty-ledger-probe").unlink(missing_ok=True)

            lst = json.loads(await call(s, "workbench_list"))
            check("list shows project", any(r["project"] == "stigmergy" for r in lst), lst)

            await call(s, "workbench_new", project="tabletest", description="desc with | pipe")
            idx3 = Path(ROOT, "workbench/INDEX.md").read_text(encoding="utf-8")
            # GFM表格转义正确形式是 \| —— || 不是合法转义，表格仍会破列
            check("INDEX.md pipe escaped as \\|", "desc with \\| pipe" in idx3, idx3[:400])

            cm = json.loads(await call(s, "workbench_complete", project="stigmergy", note="v0.1"))
            check("complete ok", cm.get("ok"), cm)
            idx2 = Path(ROOT, "workbench/INDEX.md").read_text(encoding="utf-8")
            check("INDEX.md after complete", "✅完成" in idx2, idx2[:150])

async def windows_sim_suite():
    """Windows模拟（照照round-4方法）：sys.modules['fcntl']=None 拦截fcntl，
    两个server必须仍能启动握手——v0.3顶层 import fcntl 在Windows上import即死。"""
    shim = Path(ROOT, "_win_sim_launch.py")
    shim.write_text(
        "import sys, os, runpy\n"
        "sys.modules['fcntl'] = None  # simulate Windows: import fcntl -> ImportError\n"
        "sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[1])))  # run_path不加脚本目录\n"
        "runpy.run_path(sys.argv[1], run_name='__main__')\n",
        encoding="utf-8")
    params = StdioServerParameters(
        command=VENV_PY, args=[str(shim), WORKBENCH_SERVER],
        env={"STIGMERGY_ROOT": ROOT, "PATH": "/usr/bin:/bin"},
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            lt = await s.list_tools()
            check("[v0.3.1][win-sim] workbench server starts without fcntl",
                  len(lt.tools) == 10, str([t.name for t in lt.tools]))
            # journal写入走 _with_lock 退化路径（fcntl=None → 裸写）必须成功
            wj = json.loads(await call(s, "workbench_journal", project="stigmergy",
                                       entry="Windows模拟下锁层退化裸写成功", mark="数据"))
            check("[v0.3.1][win-sim] journal write via degraded lock path", wj.get("ok"), wj)
            ws = await call(s, "workbench_search", query="", type="journal:数据")
            # 断言验"过滤查询返回行"：snippet窗口24字会被时间戳前缀占满，不能断言正文可见
            check("[v0.3.1][win-sim] FTS filter-only query works",
                  "[journal:数据]" in ws and "stigmergy/journal/" in ws and "(no hits)" not in ws, ws[:150])


async def main():
    for name, suite in [("scratch", scratch_suite), ("workbench", workbench_suite),
                        ("win-sim", windows_sim_suite)]:
        try:
            await suite()
        except Exception as e:
            check(f"{name} server suite completed", False, f"{type(e).__name__}: {e}".replace(chr(10), ' | ')[:300])
            traceback.print_exc(file=sys.stderr)

    # ledger：v0.2 契约——事件名带七域前缀（wb_new/wb_complete 已迁移）
    # 注意：套件中途故意注入过脏行（[r7] 脏行跳过测试），parse 时跳过非 JSON 行
    try:
        ledger = Path(ROOT, "ledger.jsonl").read_text(encoding="utf-8").strip().splitlines()
        parsed = []
        for l in ledger:
            try:
                parsed.append(json.loads(l))
            except json.JSONDecodeError:
                pass  # 套件注入的脏行，账本消费者（ledger_query）本来就跳过
        events = [e["event"] for e in parsed]
        check("ledger has wb events",
              {"threesome.workbench.new", "threesome.workbench.complete"} <= set(events), str(events))
        check("ledger keeps original task_id",
              any(e.get("task") == "中文任务" for e in parsed), "")
    except Exception as e:
        check("ledger readable", False, f"{type(e).__name__}: {e}")

    npass = sum(1 for _, c, _ in results if c)
    print(f"\n=== {npass}/{len(results)} PASS ===")
    shutil.rmtree(ROOT, ignore_errors=True)
    sys.exit(0 if npass == len(results) else 1)

asyncio.run(main())
