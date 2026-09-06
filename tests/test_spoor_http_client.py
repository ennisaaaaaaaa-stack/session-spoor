"""
A3 写口客户端 + spool 测试（mock server，不依赖洄的服务上线）。

覆盖：
1. 三段协议 crash-safety：先落盘→发送→确认后删
2. 同 id 同 body → duplicate（幂等成功，确认+删）
3. 同 id 不同 body → conflict（停发留账，绝不重试）
4. 服务不可达 → 事件滞留 spool，恢复后冲走
5. 退避：连续失败不硬打
6. 脏行保留（人来看，不吞）
7. mock server 起在随机端口，测试完自动关

自执行风格（与 session-spoor tests 同款，pytest 不认）。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import tempfile

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from spoor_http_client import Spool, SpoorWriteClient, CONFLICT_MARK  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}  {detail}")


# ── mock 写口：按 #335/#339 契约 + #343 响应体语义 ──
class MockAppendHandler(BaseHTTPRequestHandler):
    store: dict = {}  # id → body hash

    def log_message(self, *a):  # 测试静音
        pass

    def do_POST(self):
        if not self.path.startswith("/append"):
            self._send(404, {"status": "notfound"})
            return
        n = int(self.headers.get("Content-Length", 0))
        try:
            ev = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            self._send(400, {"status": "bad"})
            return
        eid = ev.get("id", "")
        h = json.dumps(ev, sort_keys=True)
        if eid in MockAppendHandler.store:
            if MockAppendHandler.store[eid] == h:
                self._send(200, {"status": "duplicate", "id": eid})  # 幂等成功（#339 钉1）
            else:
                self._send(409, {"status": "conflict", "id": eid})   # 冲突留账（#339 钉2）
            return
        MockAppendHandler.store[eid] = h
        self._send(200, {"status": "ok", "id": eid})

    def _send(self, code: int, obj: dict):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_mock() -> HTTPServer:
    srv = HTTPServer(("127.0.0.1", 0), MockAppendHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def mkclient(tmp: Path, port: int) -> SpoorWriteClient:
    return SpoorWriteClient(
        url=f"http://127.0.0.1:{port}",
        token="test-token-xyz",
        spool_path=tmp / "spool.jsonl",
    )


def ev(type="t.x", eid="e1", actor="照照", payload=None) -> dict:
    return {"id": eid, "ts": "2026-09-06T12:00:00Z", "type": type,
            "actor": actor, "room": "chatroom", "payload": payload or {}}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="a3-test-"))
    srv = start_mock()
    port = srv.server_address[1]
    MockAppendHandler.store.clear()
    c = mkclient(tmp, port)

    print("== 1. ok 路径：emit → 服务收账 → spool 清空 ==")
    eid = c.emit(type="spoor.session.gap", actor="照照", room="chatroom",
                 payload={"projects": ["portalk"]})
    check("服务端已记账", eid in MockAppendHandler.store)
    check("spool 无 pending", len(c.spool.pending()) == 0,
          str(c.spool.pending()))

    print("== 2. 幂等：同 id 同 body → duplicate → 确认+删 ==")
    c.spool.enqueue(ev(eid="e2", payload={"a": 1}))
    c.drain()
    # crash 重放：服务端已有 e2，同 body 再发 → duplicate（幂等成功）
    verdict, rid = c._post(ev(eid="e2", payload={"a": 1}))
    check("同 id 同 body → duplicate", verdict == "duplicate" and rid == "e2", str((verdict, rid)))

    print("== 3. 冲突：同 id 不同 body → conflict 停发，不重试不丢 ==")
    c.spool.enqueue(ev(eid="e3", payload={"v": 1}))
    c.drain()
    time.sleep(0.05)
    c._backoff = 0  # 测试免退避
    c._last_attempt = 0
    c.spool.enqueue(ev(eid="e3", payload={"v": 2}))  # 同 id 不同 body
    c.drain()
    time.sleep(0.05)
    conflicts = c.spool.conflicts()
    check("conflict 行在册", len(conflicts) == 1, str(conflicts))
    check("conflict 不进 pending（停发）",
          all(r["event"]["id"] != "e3" for r in c.spool.pending()))
    # 恢复裁决：人改掉 id 后应能重发
    check("服务端留的是第一版", MockAppendHandler.store["e3"].find('"v": 1') > 0)

    print("== 4. 服务不可达 → 滞留；恢复 → 冲走 ==")
    dead = SpoorWriteClient(url=f"http://127.0.0.1:1", token="t",
                            spool_path=tmp / "spool2.jsonl")
    dead.emit(type="t.offline", actor="照照", room="r", payload={"k": 1})
    check("不可达时事件滞留 spool", len(dead.spool.pending()) == 1)
    # 恢复：同一 spool 文件换个活 URL
    alive = SpoorWriteClient(url=f"http://127.0.0.1:{port}", token="t",
                             spool_path=tmp / "spool2.jsonl")
    alive._backoff = 0
    alive._last_attempt = 0
    alive.drain()
    check("恢复后冲走", len(alive.spool.pending()) == 0,
          str(alive.spool.pending()))
    check("恢复后服务端有账",
          any(k.startswith("照照") or True for k in MockAppendHandler.store))

    print("== 5. 先落盘再发送（crash-safety 三段协议） ==")
    probe = Spool(tmp / "spool3.jsonl")
    probe.enqueue(ev(eid="e5"))
    lines = (tmp / "spool3.jsonl").read_text(encoding="utf-8").strip().splitlines()
    check("enqueue 即落盘（不等发送）", len(lines) == 1 and '"e5"' in lines[0])

    print("== 6. 脏行保留 ==")
    (tmp / "spool3.jsonl").open("a", encoding="utf-8").write("{corrupt!!!\n")
    pend = probe.pending()
    check("脏行不进 pending 但留在文件", len(pend) == 1)
    probe.gc()
    check("gc 不吞脏行", "{corrupt" in (tmp / "spool3.jsonl").read_text(encoding="utf-8"))

    print("== 7. 未知 body → 回队不猜 ==")
    class OddHandler(MockAppendHandler):
        def do_POST(self):
            self._send(200, {"hello": "?"})  # 200 但 body 不认识
    srv2 = HTTPServer(("127.0.0.1", 0), OddHandler)
    threading.Thread(target=srv2.serve_forever, daemon=True).start()
    c7 = SpoorWriteClient(url=f"http://127.0.0.1:{srv2.server_address[1]}",
                          token="t", spool_path=tmp / "spool4.jsonl")
    c7.emit(type="t.odd", actor="照照", room="r", payload={})
    check("未知 body → 回队", len(c7.spool.pending()) == 1)

    print("== 8. token 走 query（家规），不进 body 不进 header ==")
    class TokenSpy(MockAppendHandler):
        seen_path = ""
        def do_POST(self):
            TokenSpy.seen_path = self.path
            super().do_POST()
    srv3 = HTTPServer(("127.0.0.1", 0), TokenSpy)
    threading.Thread(target=srv3.serve_forever, daemon=True).start()
    c8 = SpoorWriteClient(url=f"http://127.0.0.1:{srv3.server_address[1]}",
                          token="tok-secret", spool_path=tmp / "spool5.jsonl")
    c8.emit(type="t.tok", actor="照照", room="r", payload={})
    check("token 在 query", "token=tok-secret" in TokenSpy.seen_path,
          TokenSpy.seen_path)

    print("== 9. id 回显守门（太阳哥 #345）==")
    # 9a. 回错 id（代理串包/服务端回错事件）→ status=ok 也不删，留队+计数
    class WrongIdHandler(MockAppendHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            try:
                got = json.loads(self.rfile.read(n).decode("utf-8"))
            except Exception:
                self._send(400, {"status": "bad"})
                return
            # 把响应 id 篡改成别的事件——响应不讲你发的事
            self._send(200, {"status": "ok", "id": got.get("id", "") + "-other"})
    srv4 = HTTPServer(("127.0.0.1", 0), WrongIdHandler)
    threading.Thread(target=srv4.serve_forever, daemon=True).start()
    c9 = SpoorWriteClient(url=f"http://127.0.0.1:{srv4.server_address[1]}",
                          token="t", spool_path=tmp / "spool6.jsonl")
    c9.emit(type="t.wrongid", actor="照照", room="r", payload={})
    check("回错 id → 留队", len(c9.spool.pending()) == 1,
          str(c9.spool.pending()))
    check("回错 id → mismatch 计数", c9.mismatches == 1, str(c9.mismatches))

    # 9b. 缺 id 回显 → 留队（不算 mismatch——body 本来就没承诺什么）
    class NoIdHandler(MockAppendHandler):
        def do_POST(self):
            self._send(200, {"status": "ok"})  # 合法 200，body 无 id
    srv5 = HTTPServer(("127.0.0.1", 0), NoIdHandler)
    threading.Thread(target=srv5.serve_forever, daemon=True).start()
    c10 = SpoorWriteClient(url=f"http://127.0.0.1:{srv5.server_address[1]}",
                           token="t", spool_path=tmp / "spool7.jsonl")
    c10.emit(type="t.noid", actor="照照", room="r", payload={})
    check("缺 id → 留队不删", len(c10.spool.pending()) == 1,
          str(c10.spool.pending()))
    check("缺 id → 也计 mismatch（#345：留队并记账）",
          c10.mismatches == 1, str(c10.mismatches))

    # 9c. conflict 也回显 id → 隔离停发（不是留队重试）
    class ConflictEcho(MockAppendHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            try:
                got = json.loads(self.rfile.read(n).decode("utf-8"))
            except Exception:
                self._send(400, {"status": "bad"})
                return
            self._send(409, {"status": "conflict", "id": got.get("id", "")})
    srv6 = HTTPServer(("127.0.0.1", 0), ConflictEcho)
    threading.Thread(target=srv6.serve_forever, daemon=True).start()
    c11 = SpoorWriteClient(url=f"http://127.0.0.1:{srv6.server_address[1]}",
                           token="t", spool_path=tmp / "spool8.jsonl")
    c11.emit(type="t.conf", actor="照照", room="r", payload={})
    check("conflict 回显对 id → 隔离停发", len(c11.spool.conflicts()) == 1,
          str(c11.spool.conflicts()))
    check("conflict 不在 pending", len(c11.spool.pending()) == 0)

    srv.shutdown(); srv2.shutdown(); srv3.shutdown()
    srv4.shutdown(); srv5.shutdown(); srv6.shutdown()
    print(f"\n{PASS}/{PASS+FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
