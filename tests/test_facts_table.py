#!/usr/bin/env python3
"""facts 表 v1.0 回归测试——验收判据 J1/J2/J4/J5/J7 的机器面（spec v2 §四）。

J3/J6 是行为/经济判据（遥测+launch 账本，真跑看账），归zhaozhao事后验收，
本夹具钉的是它们的前提：表结构、读写口、TTL 语义、阴性对照。
house style：stdio transport 真机 MCP 路径，同 test_spoor_portable.py。

红得起来纪律（空钉教训）：每条断言真钉行为——TTL 边界用反证法
（把过滤方向掰反必红），阴性对照真清表。
"""
import asyncio, json, os, shutil, sqlite3, sys, tempfile, time as _t, traceback
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

VENV_PY = os.environ.get("SPOOR_TEST_PY", sys.executable)
HERE = Path(__file__).resolve().parent
def _find(name):
    for cand in (HERE / name, HERE.parent / name, HERE.parent / "session-spoor" / name):
        if cand.exists():
            return str(cand)
    raise FileNotFoundError(name)
WORKBENCH_SERVER = _find("workbench_server.py")
FACTS_STORE = _find("facts_store.py")
ROOT = tempfile.mkdtemp(prefix="spoor_facts_test_")

results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))
    print(f"{'PASS' if cond else 'FAIL'} {name} {str(detail)[:200]}")

async def call(session, tool, **kw):
    r = await session.call_tool(tool, kw)
    texts = [c.text for c in r.content if hasattr(c, "text")]
    return "\n".join(texts) if texts else f"__NO_TEXT__ isError={getattr(r, 'isError', None)}"


async def facts_suite():
    params = StdioServerParameters(
        command=VENV_PY, args=[WORKBENCH_SERVER],
        env={"STIGMERGY_ROOT": ROOT, "PATH": "/usr/bin:/bin"},
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            lt = await s.list_tools()
            names = [t.name for t in lt.tools]
            check("workbench tools listed incl facts", len(lt.tools) == 12,
                  f"{len(lt.tools)} tools: {','.join(names)}")
            check("fact tools registered", "fact_write" in names and "fact_read" in names, ",".join(names))

            # 场景项目（J1 用）
            n = json.loads(await call(s, "workbench_new", project="pianist-ops", description="琴师运维"))
            check("project created", n.get("ok"), n)

            # ---------- J1 写入在场 ----------
            w1 = json.loads(await call(s, "fact_write", scope="pianist-ops",
                                       key="env-event-queue-empty",
                                       conclusion="env-event 队列空（10/5 起十分钟级验证过）",
                                       source="journal:pianist-ops/2026-10-05",
                                       ttl_class="fast"))
            check("[J1] fact_write ok", w1.get("ok"), w1)
            check("[J1] write reports fresh", w1.get("fresh") is True, w1)
            # 字段齐全：直接开 facts.db 断言行存在（不是只信工具返回）
            db = sqlite3.connect(os.path.join(ROOT, "facts.db"))
            row = db.execute("SELECT scope,key,conclusion,source,created_at,reconfirmed_at,ttl_h,agent FROM facts WHERE key='env-event-queue-empty'").fetchone()
            db.close()
            check("[J1] row in facts.db with all fields",
                  row is not None and row[0] == "pianist-ops" and "队列空" in row[2]
                  and row[3].startswith("journal:") and row[4] and row[5] and row[6] == 6.0,
                  str(row))
            first_created = row[4] if row else None
            # 账本事件
            ledger = Path(ROOT, "ledger.jsonl").read_text(encoding="utf-8")
            check("[J1] ledger event threesome.facts.write",
                  '"threesome.facts.write"' in ledger and "env-event-queue-empty" in ledger, "")

            # ---------- 写口校验 ----------
            e1 = json.loads(await call(s, "fact_write", scope="ghost-proj",
                                       key="k", conclusion="c", source="commit:abc"))
            check("[v] orphan project scope rejected", not e1.get("ok", True), e1)
            e2 = json.loads(await call(s, "fact_write", scope="global",
                                       key="k2", conclusion="c", source="ledger:vps-x", ttl_class="banana"))
            check("[v] bad ttl_class rejected", not e2.get("ok", True), e2)
            e3 = json.loads(await call(s, "fact_write", scope="face:abc123",
                                       key="deck-a-fact", conclusion="牌面事实", source="commit:dead"))
            check("[v] face scope accepted (指纹真值在琴师侧)", e3.get("ok"), e3)

            # ---------- UPSERT 刷新 ----------
            # 先睡过秒沿：_NOW_FMT 秒级精度，同秒内首写/刷新无法分辨
            # created_at 漂移（REPLACE 语义在快跑里隐身穿绿）。
            await asyncio.sleep(1.2)
            w2 = json.loads(await call(s, "fact_write", scope="pianist-ops",
                                       key="env-event-queue-empty",
                                       conclusion="env-event 队列空（复验仍空）",
                                       source="ledger:vps-20261005-001",
                                       ttl_class="fast"))
            check("[v] upsert refresh not new", w2.get("ok") and w2.get("fresh") is False, w2)
            db = sqlite3.connect(os.path.join(ROOT, "facts.db"))
            cnt = db.execute("SELECT COUNT(*) FROM facts WHERE key='env-event-queue-empty'").fetchone()[0]
            db.close()
            check("[v] upsert keeps single row", cnt == 1, str(cnt))
            db = sqlite3.connect(os.path.join(ROOT, "facts.db"))
            up = db.execute("SELECT created_at, conclusion FROM facts WHERE key='env-event-queue-empty'").fetchone()
            db.close()
            # 反证面：UPSERT 若是 REPLACE/删插语义，created_at 漂移 → 红
            check("[v] upsert preserves created_at (首探时间不漂移)",
                  up is not None and up[0] == first_created and "复验仍空" in up[1], str(up))

            # ---------- J2 冷启动可读 ----------
            # 直接调读口
            r1 = json.loads(await call(s, "fact_read", scope="pianist-ops"))
            check("[J2] fact_read returns the row",
                  any(x.get("key") == "env-event-queue-empty" for x in r1.get("rows", [])), r1)
            # 冷启动搭车：workbench_status(project) 不传 text = 开工仪式读路径
            st = await call(s, "workbench_status", project="pianist-ops")
            check("[J2] cold-start STATUS carries facts",
                  "env-event-queue-empty" in st and "队列空" in st, st[:200])

            # ---------- J4 TTL ----------
            # ttl_h=0.0001（0.36s）——极短 TTL，等 0.6s 后必过期
            w3 = json.loads(await call(s, "fact_write", scope="global",
                                       key="fleeting", conclusion="瞬态", source="ledger:t1", ttl_h=0.0001))
            check("[J4] short ttl write ok", w3.get("ok") and w3["ttl_h"] == 0.0001, w3)
            await asyncio.sleep(0.8)
            r2 = json.loads(await call(s, "fact_read"))
            keys_alive = [x["key"] for x in r2.get("rows", [])]
            check("[J4] expired fact leaves default view", "fleeting" not in keys_alive, keys_alive)
            check("[J4] expiry announced", any(e["key"] == "fleeting" for e in r2.get("expired", [])),
                  str(r2.get("expired")))
            # 反证法：过滤方向掰反（age < ttl 判活掰成 age > ttl 判活）必红
            # ——直接改 facts_store 源码做不了（subprocess 已加载），这里用 SQL 直查断言双保险：
            db = sqlite3.connect(os.path.join(ROOT, "facts.db"))
            fl = db.execute("SELECT reconfirmed_at, ttl_h FROM facts WHERE key='fleeting'").fetchone()
            db.close()
            from datetime import datetime
            dt = datetime.strptime(fl[0], "%Y-%m-%dT%H:%M:%S")
            age_h = (_t.time() - _t.mktime(dt.timetuple())) / 3600
            check("[J4] ttl boundary math honest (age>ttl → expired)",
                  age_h > fl[1], f"age={age_h:.6f}h ttl={fl[1]}h")

            # ---------- [v] bad-ts → 过期（fail-safe 方向钉） ----------
            # reconfirmed_at 被写坏（非本工具形状）时行不得永生：
            # _age_h 解析失败返回 None → 不进活视图 → 落 expired 出声逼重探。
            # 反证面：_age_h except 分支改回抛异常 → 读口整套炸 → 红；
            # 改成返回 0 → 坏行被判活 → 红。
            w3b = json.loads(await call(s, "fact_write", scope="global",
                                        key="ts-corrupted", conclusion="ts将被外部写坏",
                                        source="ledger:t2", ttl_class="slow"))
            db = sqlite3.connect(os.path.join(ROOT, "facts.db"))
            db.execute("UPDATE facts SET reconfirmed_at='garbage-not-a-date' WHERE key='ts-corrupted'")
            db.commit()
            db.close()
            r2b = json.loads(await call(s, "fact_read"))
            alive_b = [x["key"] for x in r2b.get("rows", [])]
            check("[v] bad-ts never alive (解析失败≠永生)",
                  w3b.get("ok") and "ts-corrupted" not in alive_b, str(alive_b))
            check("[v] bad-ts surfaces in expired (逼重探)",
                  any(e["key"] == "ts-corrupted" for e in r2b.get("expired", [])),
                  str(r2b.get("expired")))

            # ---------- J5 阴性对照 ----------
            # 清空表 → 同场景恢复「一无所知」：fact_read 空、STATUS 不带 facts
            db = sqlite3.connect(os.path.join(ROOT, "facts.db"))
            db.execute("DELETE FROM facts")
            db.commit()
            db.close()
            r3 = json.loads(await call(s, "fact_read", scope="pianist-ops"))
            check("[J5] emptied table reads empty", not r3.get("rows"), str(r3.get("rows")))
            st2 = await call(s, "workbench_status", project="pianist-ops")
            check("[J5] STATUS cold-start degrades to no-facts",
                  "env-event-queue-empty" not in st2 and "[facts" not in st2, st2[:150])
            # 清空后重探并落表 → 行为恢复（写口仍可用）
            w4 = json.loads(await call(s, "fact_write", scope="pianist-ops",
                                       key="env-event-queue-empty", conclusion="重探仍空",
                                       source="ledger:vps-20261005-002", ttl_class="fast"))
            r4 = json.loads(await call(s, "fact_read", scope="pianist-ops"))
            check("[J5] re-probe after empty writes back",
                  w4.get("ok") and any(x["key"] == "env-event-queue-empty" for x in r4.get("rows", [])), w4)

            # ---------- J7 并档 ----------
            doc = Path(_find("docs/facts-table.md"))
            txt = doc.read_text(encoding="utf-8")
            check("[J7] cooldown+TTL in one doc",
                  "CONDUCTOR_SPOOR_FACE_COOLDOWN_H" in txt and "TTL_CLASS_H" in txt, str(doc))
            check("[J7] arbitration pinned",
                  "TTL 过期 > faceHash 冷却" in txt and "新鲜度优先于省钱" in txt, "")

            # ---------- ledger read 事件 ----------
            ledger = Path(ROOT, "ledger.jsonl").read_text(encoding="utf-8")
            check("[J1] ledger event threesome.facts.read", '"threesome.facts.read"' in ledger, "")

            npass = sum(1 for _, c, _ in results if c)
            print(f"\n=== facts suite: {npass}/{len(results)} PASS ===")


async def main():
    try:
        await facts_suite()
    except Exception as e:
        check("facts suite completed", False, f"{type(e).__name__}: {e}".replace(chr(10), " | ")[:300])
        traceback.print_exc(file=sys.stderr)
    npass = sum(1 for _, c, _ in results if c)
    nfail = len(results) - npass
    print(f"\n=== TOTAL {npass}/{len(results)} PASS ===")
    if os.environ.get("SPOOR_KEEP_TMP"):
        print(f"(tmp kept: {ROOT})")
    else:
        shutil.rmtree(ROOT, ignore_errors=True)
    sys.exit(0 if nfail == 0 else 1)

asyncio.run(main())
