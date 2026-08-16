#!/usr/bin/env python3
"""session-spoor v0.2 回归测试：stdio transport 全工具 + v0.2 新契约。"""
import asyncio, json, os, shutil, sqlite3, sys, tempfile, traceback
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

            # ---- round 13（Zcode review）：Windows/nt 语义的路径逃逸 ----
            # Path("/etc/x").is_absolute() 在 nt 语义下 False（有根无盘符），join 丢 base → 双向逃逸。
            # 纯 POSIX 跑这里两个向量都已在 absolute 拦截，但断言守卫向量齐全——CI 全绿≠Windows安全。
            e2r = await call(s, "scratchpad_write", space_id=sid, path="\\\\etc\\passwd", content="x")
            try:
                j2r = json.loads(e2r); ok2r = j2r.get("ok") is False and "must be relative" in j2r.get("error", "")
            except Exception: ok2r = False
            check("[v0.2][r13] rooted path (no drive) rejected on any platform semantics",
                  ok2r, e2r[:150])
            e2d = await call(s, "scratchpad_write", space_id=sid, path="C:/evil.md", content="x")
            try:
                j2d = json.loads(e2d); ok2d = j2d.get("ok") is False and "must be relative" in j2d.get("error", "")
            except Exception: ok2d = False
            check("[v0.2][r13] drive-absolute path (C:/evil.md) rejected on any platform",
                  ok2d, e2d[:150])
            check("[v0.2][r13] no rooted/drive file landed outside space",
                  not Path(ROOT, "etc/passwd").exists() and not Path("C:").exists()
                  and not Path(ROOT, "evil.md").exists(), "")

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

ARCHIVE_SERVER = _find("archive_server.py")


async def archive_suite():
    """档案房 v0.2 契约（照照 round 7 裁决后）：五工具面 + 记账纪律。"""
    params = StdioServerParameters(
        command=VENV_PY, args=[ARCHIVE_SERVER],
        env={"STIGMERGY_ROOT": ROOT, "PATH": "/usr/bin:/bin"},
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            lt = await s.list_tools()
            check("archive tools listed", len(lt.tools) == 7,
                  str([t.name for t in lt.tools]))

            # ---- put：内容寻址 + DAG + source_ref ----
            p1 = json.loads(await call(s, "archive_put", doc="hongxinshe",
                                       content="# 世界观 v1\n\n架空明末背景。\n"))
            check("[arch] put v1 ok", p1.get("ok") and len(p1["version_id"]) == 12, p1)
            v1 = p1["version_id"]
            p2 = json.loads(await call(s, "archive_put", doc="hongxinshe",
                                       content="# 世界观 v2\n\n架空明末背景，江南丝织业。\n",
                                       parent_version=v1, source_ref="ledger:export:42"))
            check("[arch] put v2 with parent+source_ref ok", p2.get("ok") and not p2.get("dedup"), p2)
            v2 = p2["version_id"]
            pd = json.loads(await call(s, "archive_put", doc="hongxinshe",
                                       content="# 世界观 v1\n\n架空明末背景。\n"))
            check("[arch] dedup same content same vid", pd.get("ok") and pd.get("dedup") and pd["version_id"] == v1, pd)
            pe = json.loads(await call(s, "archive_put", doc="hongxinshe",
                                       content="# 世界观 v3\n", parent_version="deadbeef0000"))
            check("[arch] bad parent rejected", pe.get("ok") is False and "parent" in pe["error"], pe)
            pbad = json.loads(await call(s, "archive_put", doc="不良 名字", content="x"))
            check("[arch] invalid doc name rejected", pbad.get("ok") is False, pbad)

            # ---- get：latest 指针 + 指定版本 + reason 进账本 ----
            g1 = await call(s, "archive_get", doc="hongxinshe")
            check("[arch] get latest resolves v2", "江南丝织业" in g1 and v2 in g1, g1[:100])
            g2 = await call(s, "archive_get", doc="hongxinshe", version_id=v1)
            check("[arch] get explicit v1", "架空明末背景" in g2 and v1 in g2, g2[:100])
            gn = await call(s, "archive_get", doc="no-such-doc")
            check("[arch] get missing doc → JSON error", json.loads(gn).get("ok") is False, gn[:80])

            # ---- list：地址导航，不记账 ----
            l1 = await call(s, "archive_list")          # 全库
            l2 = await call(s, "archive_list", doc="hongxinshe")  # 单链
            check("[arch] list all docs", "hongxinshe" in l1 and "1 docs" in l1, l1[:120])
            check("[arch] list doc chain newest first", v2 in l2 and v1 in l2 and l2.find(v2) < l2.find(v1), l2[:160])

            # ---- link：Tideline 指针 ----
            lk = json.loads(await call(s, "archive_link", from_version=v2,
                                       to_uri="tideline://memory/abc123", relation="same_story"))
            check("[arch] link ok", lk.get("ok"), lk)
            lkb = json.loads(await call(s, "archive_link", from_version="deadbeef0000",
                                        to_uri="x://y", relation="r"))
            check("[arch] link bad from_version rejected", lkb.get("ok") is False, lkb)

            # ---- query：FTS 检索 + 记账条数 ----
            q1 = await call(s, "archive_query", query="丝织业")
            check("[arch] FTS hits content", "hongxinshe" in q1 and v2 in q1, q1[:120])
            q2 = await call(s, "archive_query", query="不存在的词组xyzq")
            check("[arch] FTS no-hit path", "no hits" in q2, q2)
            q3 = await call(sub := s, "archive_query", query="ab")  # <3字
            check("[arch] query <3 chars → trigram notice", "trigram" in q3, q3)

            # ---- 账本纪律断言（契约核心）----
            led_lines = Path(ROOT, "ledger.jsonl").read_text(encoding="utf-8").strip().splitlines()
            parsed = []
            for l in led_lines:
                try:
                    parsed.append(json.loads(l))
                except json.JSONDecodeError:
                    pass
            arch_evts = [e for e in parsed if str(e.get("event", "")).startswith("threesome.archive.")]
            kinds = [e["event"] for e in arch_evts]
            check("[arch] ledger has all 4 archive event kinds",
                  {"threesome.archive.put", "threesome.archive.get",
                   "threesome.archive.link", "threesome.archive.query"} <= set(kinds), str(kinds))
            put_evts = [e for e in arch_evts if e["event"] == "threesome.archive.put"]
            check("[arch] put 不记 entry_head（自毁条款第一次应用）",
                  all("entry_head" not in e for e in put_evts), str(put_evts[:1]))
            check("[arch] put 记 source_ref 且毕业路径才有",
                  any(e.get("source_ref") == "ledger:export:42" for e in put_evts)
                  and sum(1 for e in put_evts if e.get("source_ref")) == 1,
                  str([e.get("source_ref") for e in put_evts]))
            get_evts = [e for e in arch_evts if e["event"] == "threesome.archive.get"]
            check("[arch] get 记 bytes+reason", all("bytes" in e for e in get_evts) and any(e.get("reason") is None for e in get_evts), str(get_evts[:1]))
            # list 不记账：l1/l2 之前记一次"base count"，之后不再新增 archive.list 事件
            n_list_before = len([e for e in arch_evts if e["event"] == "threesome.archive.list"])
            check("[arch] list 从不记账（总则不变量）", n_list_before == 0, str(n_list_before))

            # ---- 反自我放大：list/query 后账本 archive 事件数只增 query ----
            await call(s, "archive_list")
            await call(s, "archive_list", doc="hongxinshe")
            arch_after = []
            for l in Path(ROOT, "ledger.jsonl").read_text(encoding="utf-8").strip().splitlines():
                try:
                    e = json.loads(l)
                except json.JSONDecodeError:
                    continue  # 套件注入的脏行（两处parse同守）
                if str(e.get("event", "")).startswith("threesome.archive."):
                    arch_after.append(e)
            check("[arch] list 调用后 archive 事件零增加",
                  len(arch_after) - len(arch_evts) == 0, f"{len(arch_evts)}→{len(arch_after)}")

            # ---- 追加性验证：坏版本被拒后 DB 无痕 ----
            # versions=2（v1+v2；dedup 不 INSERT 第二行是设计：内容寻址，同一内容=同一版本）
            c = sqlite3.connect(Path(ROOT, "archive", "index.db"))
            n = c.execute("SELECT COUNT(*) FROM versions WHERE doc='hongxinshe'").fetchone()[0]
            link_rows = c.execute("SELECT COUNT(*) FROM links").fetchone()[0]
            c.close()
            check("[arch] rejected put/link leaves no DB trace",
                  n == 2 and link_rows == 1, f"versions={n} links={link_rows}")

            # ---- round 9（照照）：TOCTOU 修复回归 ----
            # dedup put 带不同 source_ref -> 回显 source_ref_dropped，不静默丢
            pd2 = json.loads(await call(s, "archive_put", doc="hongxinshe",
                                        content="# 世界观 v1\n\n架空明末背景。\n", source_ref="ledger:export:77"))
            check("[arch][r9] dedup put 回显 source_ref_dropped",
                  pd2.get("dedup") is True and pd2.get("source_ref_dropped") is True, pd2)
            # put 事件 dedup 字段进账本（并发竞态的审计铁证）
            led_r9 = []
            for l in Path(ROOT, "ledger.jsonl").read_text(encoding="utf-8").strip().splitlines():
                try:
                    e = json.loads(l)
                except json.JSONDecodeError:
                    continue
                if e.get("event") == "threesome.archive.put" and e.get("dedup") is True:
                    led_r9.append(e)
            check("[arch][r9] 账本 put 事件带 dedup=true",
                  any(e.get("source_ref") == "ledger:export:77" and e.get("dedup") is True for e in led_r9)
                  and any(e.get("dedup") is True for e in led_r9),
                  f"count={len(led_r9)}")
            # 唯一索引存在 + 旧普通索引已撤
            cu = sqlite3.connect(Path(ROOT, "archive", "index.db"))
            ux = cu.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='ux_versions_doc_vid'").fetchone()
            old_ix = cu.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='ix_versions_doc'").fetchone()
            # link doc 校验：错 doc 拒；同内容双 doc 歧义回显
            lk_bad2 = json.loads(await call(s, "archive_link", from_version=v2,
                                            to_uri="tideline://y", relation="r", doc="nosuchdoc"))
            p_al = json.loads(await call(s, "archive_put", doc="alpha", content="shared-r9-check\n"))
            p_be = json.loads(await call(s, "archive_put", doc="beta", content="shared-r9-check\n"))
            lk_amb2 = json.loads(await call(s, "archive_link", from_version=p_al["version_id"],
                                            to_uri="tideline://z", relation="r"))
            lk_ok2 = json.loads(await call(s, "archive_link", from_version=p_al["version_id"],
                                           to_uri="tideline://w", relation="r", doc="alpha"))
            cu.close()
            check("[arch][r9] 唯一索引在位/旧索引已撤", bool(ux) and not old_ix, f"ux={ux} old={old_ix}")
            check("[arch][r9] link 错 doc 被拒", lk_bad2.get("ok") is False, lk_bad2)
            check("[arch][r9] 同内容双 doc，link 歧义回显 docs",
                  lk_amb2.get("ok") is True and lk_amb2.get("docs") == ["alpha", "beta"], lk_amb2)
            check("[arch][r9] link 带 doc 精确锚定通过", lk_ok2.get("ok") is True and "docs" not in lk_ok2, lk_ok2)

            # ---- pin/unpin：latest 指针显式管理（回退场景 v0.4）----
            # 场景：真回退——先长出 v3（latest=v3），实测 v3 不如 v2 → pin 回 v2
            p3 = json.loads(await call(s, "archive_put", doc="hongxinshe",
                                       content="# 世界观 v3\n\n丝织业主线推翻重写。\n", parent_version=v2))
            check("[arch][v0.4] 前置 v3 ok", p3.get("ok") is True, p3)
            v3 = p3["version_id"]
            pin1 = json.loads(await call(s, "archive_pin", doc="hongxinshe", version_id=v2,
                                         reason="回退：v3系实测不稳"))
            check("[arch][v0.4] pin v2 ok 且 previous=v3", pin1.get("ok") is True
                  and pin1.get("previous") == v3, pin1)
            g_after = await call(s, "archive_get", doc="hongxinshe")  # 不带 version_id = latest
            check("[arch][v0.4] get latest 解析回 pinned v2",
                  g_after.startswith(f"(archive hongxinshe @ {v2}"), g_after[:120])
            # 版本链导航能看到 pin 状态（按行首匹配——parent 字段里也有 vid，防止抓错行）
            def _line_of(vid):
                return next((ln for ln in l_pin.splitlines()
                             if ln.strip().lstrip("📌 ").startswith(vid)), "")
            l_pin = await call(s, "archive_list", doc="hongxinshe")
            v2_line = _line_of(v2)
            v3_line = _line_of(v3)
            check("[arch][v0.4] list 链上 v2 行带 📌，v3 行不带",
                  "📌" in v2_line and "📌" not in v3_line, l_pin[:200])
            # pin 不存在版本 → 拒
            pin_bad = json.loads(await call(s, "archive_pin", doc="hongxinshe",
                                            version_id="deadbeef0000", reason="x"))
            check("[arch][v0.4] pin 不存在版本被拒", pin_bad.get("ok") is False, pin_bad)
            # pin 不存在 doc → 拒
            pin_bad2 = json.loads(await call(s, "archive_pin", doc="no-such-doc",
                                             version_id=v1, reason="x"))
            check("[arch][v0.4] pin 不存在 doc 被拒", pin_bad2.get("ok") is False, pin_bad2)
            # unpin → 回落现算（最后插入行 = v3）
            un1 = json.loads(await call(s, "archive_unpin", doc="hongxinshe"))
            check("[arch][v0.4] unpin ok 回落现算", un1.get("ok") is True, un1)
            g_fallback = await call(s, "archive_get", doc="hongxinshe")
            check("[arch][v0.4] unpin 后 latest 现算回 v3（最后插入行）",
                  g_fallback.startswith(f"(archive hongxinshe @ {v3}"), g_fallback[:120])
            # 账本纪律：成功的 pin/unpin 各记一笔（带 reason），被拒的不留痕
            led_v04 = []
            for l in Path(ROOT, "ledger.jsonl").read_text(encoding="utf-8").strip().splitlines():
                try:
                    e = json.loads(l)
                except json.JSONDecodeError:
                    continue
                ev = str(e.get("event", ""))
                if ev in ("threesome.archive.pin", "threesome.archive.unpin"):
                    led_v04.append(e)
            check("[arch][v0.4] pin/unpin 各一笔带 reason，被拒零痕迹",
                  len(led_v04) == 2
                  and led_v04[0].get("event") == "threesome.archive.pin"
                  and led_v04[0].get("version_id") == v2
                  and led_v04[0].get("previous") == v3
                  and led_v04[0].get("reason") == "回退：v3系实测不稳"
                  and led_v04[1].get("event") == "threesome.archive.unpin",
                  str(led_v04))

            # ---- round 11（Zcode review）：断链不静默 + pinned 对 get 透明 ----
            # 重建 pin 现场用于 r11 场景
            pin2 = json.loads(await call(s, "archive_pin", doc="hongxinshe", version_id=v2,
                                         reason="r11前置：重新钉回v2"))
            check("[arch][r11] 前置 pin v2 ok", pin2.get("ok") is True, pin2)
            # 修2：get head 带 pinned 标记
            g_pin = await call(s, "archive_get", doc="hongxinshe")
            check("[arch][r11] get latest=pinned v2 时 head 带 📌 pinned",
                  g_pin.startswith(f"(archive hongxinshe @ {v2}")
                  and "📌 pinned" in g_pin.splitlines()[0], g_pin[:120])
            # 显式取 v3（非 pin 版本）不带 pinned 标记——标记只在指针解析路径上出现
            g_v3 = await call(s, "archive_get", doc="hongxinshe", version_id=v3)
            check("[arch][r11] 显式 version_id 取非pin版本无 pinned 标记",
                  "📌 pinned" not in g_v3.splitlines()[0], g_v3[:120])
            # 修1：手工把 pin 的版本行删掉制造断链 → get 回落现算 + 记 pin_broken
            cu = sqlite3.connect(Path(ROOT, "archive", "index.db"))
            cu.execute("DELETE FROM versions WHERE doc='hongxinshe' AND version_id=?", (v2,))
            cu.commit(); cu.close()
            g_broken = await call(s, "archive_get", doc="hongxinshe")
            check("[arch][r11] 断链后 get 回落现算（最后插入行=v3）",
                  g_broken.startswith(f"(archive hongxinshe @ {v3}"), g_broken[:120])
            # round 12（Zcode）：即时消费者可见——断链回落的 head 带显式警告
            check("[arch][r12] 断链回落的 get head 带 ⚠️ pin broken (fell back)",
                  "⚠️ pin broken (fell back)" in g_broken.splitlines()[0], g_broken[:150])
            # 非断链路径不受污染：显式取版本不带警告（broken 只在指针解析路径出现）
            check("[arch][r12] 显式 version_id 取版本不带 pin broken 警告",
                  "⚠️ pin broken" not in g_v3.splitlines()[0], g_v3[:100])
            led_r11 = []
            for l in Path(ROOT, "ledger.jsonl").read_text(encoding="utf-8").strip().splitlines():
                try:
                    e = json.loads(l)
                except json.JSONDecodeError:
                    continue
                if e.get("event") == "threesome.archive.pin_broken":
                    led_r11.append(e)
            check("[arch][r11] 断链被 get 碰到时记 pin_broken 账本事件（不静默）",
                  len(led_r11) == 1
                  and led_r11[0].get("pinned_version") == v2
                  and led_r11[0].get("fell_back_to") == v3,
                  str(led_r11))
            # list 单链模式断链回显 ⚠️
            l_broken = await call(s, "archive_list", doc="hongxinshe")
            check("[arch][r11] list 单链断链回显 ⚠️ pin broken",
                  "⚠️ pin broken" in l_broken, l_broken[:200])
            # 全库概览认 pin：另建 doc pin 住，概览 latest 显示 pinned 版本
            p4 = json.loads(await call(s, "archive_put", doc="r11doc",
                                       content="# r11 概览认pin\n\nv1\n"))
            v4 = p4["version_id"]
            p5 = json.loads(await call(s, "archive_put", doc="r11doc",
                                       content="# r11 概览认pin\n\nv2\n",
                                       parent_version=v4))
            v5 = p5["version_id"]
            pin3 = json.loads(await call(s, "archive_pin", doc="r11doc", version_id=v4,
                                         reason="r11：概览latest认pin"))
            check("[arch][r11] 前置 r11doc pin v4 ok", pin3.get("ok") is True, pin3)
            l_all = await call(s, "archive_list")
            r11_line = next((ln for ln in l_all.splitlines() if ln.strip().startswith("r11doc")), "")
            check("[arch][r11] 全库概览 latest 认 pin（显示 v4 + 📌）",
                  v4 in r11_line and "📌" in r11_line and v5 not in r11_line, r11_line)
            # 全库概览断链回显 ⚠️
            cu = sqlite3.connect(Path(ROOT, "archive", "index.db"))
            cu.execute("DELETE FROM versions WHERE doc='r11doc' AND version_id=?", (v4,))
            cu.commit(); cu.close()
            l_all2 = await call(s, "archive_list")
            r11_line2 = next((ln for ln in l_all2.splitlines() if ln.strip().startswith("r11doc")), "")
            check("[arch][r11] 全库概览断链回显 ⚠️ pin broken",
                  "⚠️ pin broken" in r11_line2 and v5 in r11_line2, r11_line2)

            # ---- round 13（Zcode review，Windows 真机）：unpin 守卫挡死断链 pin 的清除 ----
            # 契约写"显式 unpin 是唯一清除路径"，但版本行整批被清、pin 残留的外部损坏态
            # （r11 同族），unpin 曾被 no-versions 守卫拒绝 → pins 行永留、每次 get 刷 pin_broken。
            # 场景A：r11doc 删光全部版本行（整批清+pin残留）→ unpin 必须成功清 pin
            cu = sqlite3.connect(Path(ROOT, "archive", "index.db"))
            cu.execute("DELETE FROM versions WHERE doc='r11doc'")
            cu.commit(); cu.close()
            n_ver = cu_n = None
            cu2 = sqlite3.connect(Path(ROOT, "archive", "index.db"))
            n_ver = cu2.execute("SELECT COUNT(*) FROM versions WHERE doc='r11doc'").fetchone()[0]
            n_pin = cu2.execute("SELECT COUNT(*) FROM pins WHERE doc='r11doc'").fetchone()[0]
            cu2.close()
            check("[arch][r13] 前置：r11doc 版本清光且 pin 残留（外部损坏态）",
                  n_ver == 0 and n_pin == 1, f"versions={n_ver} pins={n_pin}")
            un_broken = json.loads(await call(s, "archive_unpin", doc="r11doc", reason="r13清残留pin"))
            check("[arch][r13] 断链 pin 的 unpin 不再被 no-versions 守卫拒绝",
                  un_broken.get("ok") is True and un_broken.get("unpinned") == v4, un_broken)
            cu3 = sqlite3.connect(Path(ROOT, "archive", "index.db"))
            n_pin2 = cu3.execute("SELECT COUNT(*) FROM pins WHERE doc='r11doc'").fetchone()[0]
            cu3.close()
            check("[arch][r13] pins 行真被清除（后续 get 不再刷 pin_broken）", n_pin2 == 0, n_pin2)
            # 场景B：版本行还在的 doc（hongxinshe 断链态，v1/v3 尚存）→ unpin 也清（旧代码此场景本就能过，回归保护）
            un_hx = json.loads(await call(s, "archive_unpin", doc="hongxinshe", reason="r13回归保护"))
            check("[arch][r13] 部分存版的断链 pin unpin 同样 ok",
                  un_hx.get("ok") is True and un_hx.get("unpinned") == v2, un_hx)
            # 守卫仍然活着：全新 doc（无版本无pin）→ no versions 拒绝
            un_fresh = json.loads(await call(s, "archive_unpin", doc="never-existed"))
            check("[arch][r13] 无版本无 pin 的 doc 仍被 no versions 拒绝",
                  un_fresh.get("ok") is False and "no versions" in un_fresh.get("error", ""), un_fresh)


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
                        ("archive", archive_suite), ("win-sim", windows_sim_suite)]:
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
