"""
spoor_http_client: 写口客户端 + 本地 spool（A3，2026-09-06 照照）。

背景（客厅 #335/#339 契约）：
- spoor 写口：POST /append?token=...，事件 body {id, ts, type, actor, room, payload}
- 按 id 幂等去重：同 id 同 body 重放 → 幂等成功（可识别）；同 id 不同 body → 409 冲突留账
- 一实例一 token；服务端日志剥 query 不落 token
- spool 语义（太阳哥 #339 三钉）：
  1) 本地队列先落盘再发送（crash 前事件已在盘上）
  2) 成功确认后才删（未确认的永在队列里）
  3) 进程中途死只会重复不会丢（at-least-once，服务端幂等兜底）

给三家共用的一份实现：
  - gateway 钩子（照照，A3 本票）：session gap 等事件从本地 stdio 落账改道 HTTP 写口
  - 堂屋 wrapper（鸣鸣落码，太阳哥验收）：result JSON 后 append 打写口
  - 远程 MCP 实例（鸣鸣/ZCode 的 8791/8792 → VPS）：同款客户端迁移

客户端判别纪律：幂等成功/冲突不猜状态码，按响应体 {"status": ...} 判——
body 语义标记比 200 vs 204 的数字游戏稳（照照 #343 提议，服务端可 200 全包）。

打标按记录不按 id（首版教训）：spool 里可能同时躺着两条同 id 不同 body 的
记录（正是 #339 钉 2 的冲突场景），按 id 打 sent 会把未裁决的冲突行连带
标走——必须精确到记录（id + 事件全文比对）。

零依赖（urllib），Python 3.10+，纯逻辑与传输分离——spool 可单独测试。
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("spoor.spoor_http_client")

# ── 纪律常量 ─────────────────────────────────────────────
# 发送尝试之间的基础退避（秒）。连续失败翻倍，封顶；成功即归零。
SPOOL_BACKOFF_S = 5.0
SPOOL_MAX_BACKOFF_S = 3600.0
# 一次 flush 冲多少条（防单次占住钩子线程过久）
SPOOL_BATCH = 20
# HTTP 超时（秒）——写口在 VPS，隧道/公网都可能抖
HTTP_TIMEOUT_S = 10.0
# 冲突事件的停发标记：同 id 不同 body 是账务事故，重试只会扩大事故
CONFLICT_MARK = "!conflict"


def _canon(ev: dict) -> str:
    """事件的规范形（身份比对用）：排序键序列化。"""
    return json.dumps(ev, ensure_ascii=False, sort_keys=True)


class Spool:
    """本地 spool：JSONL 追加文件 + crash-safe 三段协议。

    文件格式：一行一个事件 JSON。三种行态：
      {"id":..., "event":{...}}                       待发
      {"id":..., "event":{...}, "sent": true}          已确认（下轮清理）
      {"id":..., "event":{...}, "!conflict": true}     冲突停发（人裁决）

    三段协议（先落盘→发送→确认后删）：
      enqueue: 追加写盘，fsync —— crash 时事件还在盘上
      flush:   逐条 POST；ok/duplicate → 标 sent；conflict → 标停发留人裁决
      gc:      sent 行物理删除（重写文件）。crash 在 gc 中途 → 文件里仍有该行，
               下轮重发 → 服务端 duplicate → 幂等兜底，不会双记。
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ── enqueue：先落盘 ──
    def enqueue(self, event: dict) -> None:
        rec = {"id": event.get("id"), "event": event}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

    # ── 内部：重写文件，对每行套 predicate ──
    def _rewrite(self, mutate) -> None:
        """mutate(rec) → rec|None（None=丢弃该行）。锁外调用（由 sender 串行化）。"""
        tmp = self.path.with_suffix(".tmp")
        try:
            with open(self.path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return
        with open(tmp, "w", encoding="utf-8") as f:
            for l in lines:
                try:
                    rec = json.loads(l)
                except json.JSONDecodeError:
                    f.write(l)  # 脏行原样保留，人来看
                    continue
                out = mutate(rec)
                if out is not None:
                    f.write(json.dumps(out, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    # ── 状态迁移：按记录精确打标（id + 事件全文）──
    def mark_sent(self, ev: dict) -> None:
        target = _canon(ev)

        def m(rec):
            if rec.get("event") is not None and _canon(rec["event"]) == target:
                rec["sent"] = True
            return rec

        self._rewrite(m)

    def mark_conflict(self, ev: dict) -> None:
        target = _canon(ev)

        def m(rec):
            if rec.get("event") is not None and _canon(rec["event"]) == target:
                rec[CONFLICT_MARK] = True
            return rec

        self._rewrite(m)

    # ── gc：删已确认行（冲突行保留待人裁决）──
    def gc(self) -> None:
        self._rewrite(lambda rec: None if rec.get("sent") else rec)

    def pending(self) -> list:
        """待发行（无 sent / !conflict 标）。"""
        try:
            with open(self.path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return []
        out = []
        for l in lines:
            try:
                rec = json.loads(l)
            except json.JSONDecodeError:
                continue
            if not rec.get("sent") and not rec.get(CONFLICT_MARK):
                out.append(rec)
        return out

    def conflicts(self) -> list:
        """冲突停发行（人裁决用）。"""
        try:
            with open(self.path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return []
        out = []
        for l in lines:
            try:
                rec = json.loads(l)
            except json.JSONDecodeError:
                continue
            if rec.get(CONFLICT_MARK):
                out.append(rec)
        return out


class SpoorWriteClient:
    """写口 HTTP 客户端：spool 冲队列 + POST /append。

    用法（gateway 钩子 / wrapper / 远程实例同款）：
        client = SpoorWriteClient(url, token, spool_path)
        client.emit(type="spoor.session.gap", actor="照照",
                    room="chatroom", payload={...})   # 只入队+尝试冲
        client.drain()                                 # 事后冲队列（可定时）

    emit 不抛异常（钩子纪律：失败静默，事件在 spool 里等着）。

    退避只在失败后生效：全确认 → 时钟归零（成功不该拦住下一轮）；
    有失败 → 退避翻倍封顶，窗口内 drain 直接返回。
    """

    def __init__(self, url: str, token: str, spool_path: Path):
        self.url = url.rstrip("/")
        self.token = token
        self.spool = Spool(spool_path)
        self._backoff = SPOOL_BACKOFF_S
        self._last_attempt = 0.0
        # id 回显不符计数（太阳哥 #345）：报警是挂载方的活，客户端只记账
        self.mismatches = 0

    # ── 事件 id：origin-UTC时间戳-pid-进程内序号（洄 #335 契约）──
    # 进程内单调：同秒两条也不撞；跨进程靠 pid 区分；跨机靠 origin。
    _eid_seq = 0

    @classmethod
    def event_id(cls, origin: str) -> str:
        cls._eid_seq += 1
        return f"{origin}-{int(time.time())}-{os.getpid()}-{cls._eid_seq}"

    def emit(self, type: str, actor: str, room: str, payload: dict,
             origin: str = "", eid: str = "") -> str | None:
        """构造事件 → 落 spool → 尝试冲。返回事件 id（失败也返回，事件已落盘）。"""
        eid = eid or self.event_id(origin or actor)
        ev = {
            "id": eid,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "type": type,
            "actor": actor,
            "room": room,
            "payload": payload,
        }
        try:
            self.spool.enqueue(ev)
        except Exception:
            return eid  # 盘都落不进：钩子环境已病重，静默（钩子纪律）
        self.drain()
        return eid

    # ── 冲队列 ──
    def drain(self) -> int:
        """冲 pending 事件。返回本次确认数（ok/duplicate 都算确认）。

        删队列的守门（太阳哥 #345）：status=ok/duplicate 且响应体回显的
        id == 队首事件 id 才 mark_sent——代理串包/服务端回错事件时响应
        讲的是别的事，删了就是丢账。缺 id、错 id、未知 status 一律留队，
        mismatch 计数进 self.mismatches（报警是挂载方的活，这里只记账）。
        """
        pend = self.spool.pending()
        if not pend:
            return 0
        # 退避窗口未到：不硬打（服务可能刚炸，硬打只会堆 timeout）
        if self._last_attempt and time.time() - self._last_attempt < self._backoff:
            return 0
        confirmed = 0
        try:
            for rec in pend[:SPOOL_BATCH]:
                ev = rec.get("event", {})
                eid = ev.get("id")
                verdict, rid = self._post(ev)
                if rid != eid:
                    # 响应不讲本事件的事（串包/回错/缺 id）：不删不隔离，留队
                    if verdict != "error":
                        self.mismatches += 1
                        log.warning("spool id 回显不符：sent=%s resp=%s verdict=%s",
                                    eid, rid, verdict)
                    continue
                if verdict in ("ok", "duplicate"):
                    self.spool.mark_sent(ev)
                    confirmed += 1
                elif verdict == "conflict":
                    self.spool.mark_conflict(ev)
            if self.spool.pending():
                self._backoff = min(self._backoff * 2, SPOOL_MAX_BACKOFF_S)
                self._last_attempt = time.time()
            else:
                # 全确认：退避归零+时钟归零——成功不拦下一轮
                self._backoff = SPOOL_BACKOFF_S
                self._last_attempt = 0.0
                self.spool.gc()
        except Exception:
            # 发送中途炸：剩余事件仍在盘上，本轮计入失败时钟
            self._backoff = min(self._backoff * 2, SPOOL_MAX_BACKOFF_S)
            self._last_attempt = time.time()
        return confirmed

    # ── 单条 POST ──
    def _post(self, ev: dict) -> tuple:
        """POST /append → (verdict, rid)。

        verdict ∈ 'ok' | 'duplicate' | 'conflict' | 'error'；rid = 响应体
        回显的事件 id（可能缺）。

        判别纪律（洄 #344 + 太阳哥 #345）：body 的 status 枚举是机器判别
        唯一权威——监控看状态码，spool 认字。409/422 的 body 里若有权威
        status=conflict 也认；body 读不出权威语义时按码不猜，一律 error
        回队（重试无害：服务端幂等去重兜底）。
        """
        data = json.dumps(ev, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self.url}/append?{urllib.parse.urlencode({'token': self.token})}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
                return self._read_verdict(r)
        except urllib.error.HTTPError as e:
            try:
                return self._read_verdict(e)
            except Exception:
                return "error", ""
        except Exception:
            return "error", ""

    @staticmethod
    def _read_verdict(r) -> tuple:
        """从响应（200 或 HTTPError）读权威 verdict。读不出 → error。"""
        try:
            body = json.loads(r.read().decode("utf-8"))
            s = body.get("status", "")
            rid = body.get("id", "")
        except Exception:
            return "error", ""
        if s in ("ok", "duplicate", "conflict"):
            return s, str(rid)
        return "error", ""
