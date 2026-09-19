"""
共享层：多agent并发的公共设施。

SPOOR_AGENT: 环境变量，设了就给 journal/ledger 条目盖名字戳。
  - 不设 = 匿名（单住户兼容，现有数据零影响）
  - 设了  = 每条 journal 行变成 "- **[判断]** (名字) 时间 正文"
            ledger 每条事件多一个 "agent": "名字" 字段

SPOOR_LOCK_*: 跨进程文件锁。fcntl.flock 在同一文件描述符上
  排队等待，两个进程同时写 journal/ledger 不会交错损坏。
  锁文件放 {root}/.locks/，gitignore 掉（运行时产物）。
  Windows 用 msvcrt.locking（zcode PR）——journal 是读→拼→写，
  两个实例并发时先写的整行静默消失（丢失更新），锁是必需品不是装饰。

无锁退化：fcntl 和 msvcrt 都不可用的平台才裸写（win-sim 模拟的
  POSIX）——单住户无并发才安全。真实平台都有锁：POSIX=flock，
  Windows=msvcrt。
"""
import json
import os
import re
import time
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = None
try:
    import msvcrt
except ImportError:  # POSIX: no msvcrt — fcntl 分支兜着，或裸写退化
    msvcrt = None

ROOT = Path(os.environ.get("STIGMERGY_ROOT", str(Path.home() / "Stigmergy")))
LEDGER = ROOT / "ledger.jsonl"
LOCKDIR = ROOT / ".locks"


# ---- round 14（zcode review）：SQLite 硬地板的可执行诊断 ----
# trigram 分词器需要 SQLite ≥ 3.34。老 Python 捆的老 sqlite 上首次建表
# 就是 OperationalError——响亮但不是人话。这里把版本检查提前到 _conn，
# 给出能直接行动的诊断。（zcode 裁决：报错要像人说话）
SQLITE_FLOOR = (3, 34)


def check_sqlite_floor() -> None:
    import sqlite3
    if sqlite3.sqlite_version_info < SQLITE_FLOOR:
        raise RuntimeError(
            f"sqlite3 过老（{sqlite3.sqlite_version} < 3.34）：FTS5 trigram 分词器不可用，"
            f"索引/档案库无法初始化。请升级 Python（或其捆绑的 SQLite）后重试，"
            f"数据文件本身无需迁移。"
        )


def agent_name() -> str:
    """当前住户名。空 = 匿名（单住户模式）。

    读取顺序（照照 8/23 审的部署缺口，v0.4.4）：env 注入优先 →
    $STIGMERGY_ROOT/agent.name 文件（每台机器写自己的名字，
    gateway/watchdog/MCP/cron/临时脚本全进程生效，systemd unit
    不用打 env 洞）→ 都没有 = 匿名。
    开源场景——fork 仓库的陌生人任选其一即可署名，无需改代码。
    """
    n = os.environ.get("SPOOR_AGENT", "").strip()
    if n:
        return n
    try:
        return (ROOT / "agent.name").read_text(encoding="utf-8").strip()[:64]
    except (OSError, ValueError):
        return ""


def stamped(now: str) -> str:
    """journal 行内的时间段：匿名 → 时间；具名 → (名字) 时间。"""
    n = agent_name()
    return f"({n}) {now}" if n else now


def _msvcrt_lock(path: Path, write_fn):
    """Windows 跨进程排它锁（zcode PR）。语义对齐 flock 分支：
    同一 {root}/.locks/{name}.lock，全程持锁执行 write_fn。

    三个 Windows 特有坑（winlock_probe 压测坐实：3进程×100轮零丢失零重复）：
    - locking 锁的是"当前位置起 N 字节"不是整个文件——必须 seek(0) 锁第 1 字节
    - 空文件没有字节可锁——首次创建补一个占位字节（双进程同时补无害：append 幂等）
    - LK_LOCK 等待上限 10 次重试 × 1 秒——超时抛 RuntimeError，**不静默裸写**：
      journal 是读→拼→写，裸写=丢失更新，静默吞掉比报错更坏（r14 零命中守卫同族药方）
    """
    LOCKDIR.mkdir(parents=True, exist_ok=True)
    lockfile = LOCKDIR / (path.name + ".lock")
    with open(lockfile, "a+b") as lf:
        lf.seek(0, os.SEEK_END)
        if lf.tell() == 0:
            lf.write(b"\0")
            lf.flush()
        try:
            lf.seek(0)
            msvcrt.locking(lf.fileno(), msvcrt.LK_LOCK, 1)
        except OSError as e:
            raise RuntimeError(
                f"文件锁竞争超时（{lockfile}，LK_LOCK 10s）：多 agent 并发写同一目标，"
                f"本条写入未执行，请重试。原始错误: {e}"
            ) from e
        try:
            return write_fn(path)
        finally:
            try:
                lf.seek(0)
                msvcrt.locking(lf.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass  # 句柄关闭时 OS 回收区域锁；写入已完成，不为解锁噪音炸返回值


def _with_lock(path: Path, write_fn):
    """在排它锁保护下执行 write_fn(path)。

    POSIX：flock（行为与旧版逐字节一致，锁打开失败才裸写降级）。
    Windows：msvcrt.locking（真锁，超时报错不裸写——见 _msvcrt_lock）。
    两者皆无（win-sim 模拟 fcntl=None 的 POSIX）：裸写，仅单住户安全。
    已知过度互斥（沿 POSIX 旧语义不改）：锁名取 path.name 不含目录，
    不同 project 的同日 journal（2026-08-17.md）互相排队——家庭规模无感。
    """
    if fcntl is not None:
        try:
            LOCKDIR.mkdir(parents=True, exist_ok=True)
            lockfile = LOCKDIR / (path.name + ".lock")
            with open(lockfile, "a+") as lf:
                fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
                try:
                    return write_fn(path)
                finally:
                    fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
        except OSError:
            return write_fn(path)
    if msvcrt is not None:
        return _msvcrt_lock(path, write_fn)
    return write_fn(path)


# ── v1.3 出生发号（2026-09-15 洄，甜心开窗批准）──────────────────
# 动机：v0.7 归流暴露的「增长型无号」——补号是事后救火，autofill/钩子每天
# 继续写无号新行，合并工具门闸拒收。根治=发号进写入路径：id 生而带。
# 族谱：与 renumber-ledger-ids.py 同格式 <origin>-<UTC秒>-<序号>，
# merge/detect 工具零改动即认。
# v1.3.1（2026-09-15 洄，照照审稿火力①②）：接力失明即兜底——尾行形状
# 不认（A3 混形状库）/ 窗口被大行截断读不到号 / 尾行无号，都不再静默
# 从 001 起，而是全库扫「本源本秒」已用最大序号 +1。正则放宽到 \d{3,}
# （原 \d{3} 在序号到 1000 时接力断裂，同根撞号）。
_ORIGIN_DEFAULT = "local"


def _origin(root: "Path | None" = None) -> str:
    """本机发号方标识。$STIGMERGY_ROOT/origin 文件（每台机器写自己的，
    gitignored，同 agent.name 惯例）；缺省 local——未配置机器仍出生带号，
    但跨机归流前应显式写（本家两值：vps / wsl，与 merge 工具口径一致）。"""
    base = Path(root) if root else ROOT
    try:
        v = (base / "origin").read_text(encoding="utf-8").strip().lower()[:16]
        if v:
            return v
    except (OSError, ValueError):
        pass
    return _ORIGIN_DEFAULT


def _now_stamp() -> str:
    """当前 UTC 秒戳。独立小函数：测试注入固定秒用（同秒场景不必等表）。"""
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _last_ledger_id(ledger: Path) -> "str | None":
    """锁内读账本尾部窗口（末 8KB）里最后一条带 id 的行。

    v1.3.1 勘误（照照批评收下）：原注释「性能地板：账本无上限」口径作废——
    8KB 窗口只是接力**快路径**的优化，不是唯一性的边界条件。行长无上界
    约束时窗口是可能失明的（一整条 >8KB 大行垫底 → 窗口内没有完整行），
    失明时唯一性由 _mint_event_id 的全库扫描兜底，窗口本身不承诺安全。"""
    try:
        with open(ledger, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 8192))
            chunk = f.read().decode("utf-8", "replace")
        for line in reversed(chunk.splitlines()):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = row.get("id")
            if rid:
                return str(rid)
        return None
    except OSError:
        return None


def _scan_max_seq(ledger: Path, origin: str, stamp: str) -> int:
    """全库扫描「本源本秒」已用的最大序号（0 = 本秒没写过）。锁内调用，
    无并发顾虑；代价一次全文件顺序读，只在快路径失明时发生——常态尾部
    接力零额外 IO。混形状库（A3 的 origin-unix秒-pid-seq / 历史无号行 /
    补号存量）按 origin-stamp 精确前缀过滤，异形状天然跳过。"""
    pat = re.compile(rf"^{re.escape(origin)}-{re.escape(stamp)}-(\d{{3,}})$")
    best = 0
    try:
        with open(ledger, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rid = row.get("id")
                if rid:
                    m = pat.match(str(rid))
                    if m:
                        best = max(best, int(m.group(1)))
    except OSError:
        pass
    return best


def _mint_event_id(ledger: Path, origin: str) -> str:
    """v1.3 发号（锁内调用）：id = <origin>-<UTC秒>-<序号>。

    快路径：尾行是「本源本秒」v1.3 号 → 序号接力 +1（group(1) 与
    origin-stamp 全等比对，A3 形状即使尾段恰好三位数也不会被误吞）。
    兜底路径（v1.3.1）：尾行无号 / 形状不认 / 窗口失明 → 全库扫本秒
    已用最大序号 +1，绝不静默回 001。秒不同 → 本秒无号 → 001 起步；
    历史补号 id 的秒是过去时刻，与新写不撞。"""
    stamp = _now_stamp()
    last = _last_ledger_id(ledger)
    seq = None
    if last:
        m = re.match(r"^(.*)-(\d{3,})$", last)
        if m and m.group(1) == f"{origin}-{stamp}":
            seq = int(m.group(2)) + 1
    if seq is None:
        seq = _scan_max_seq(ledger, origin, stamp) + 1
    return f"{origin}-{stamp}-{seq:03d}"


def append_ledger(event: dict, root: "Path | None" = None) -> None:
    """带锁的 ledger 追加。具名住户自动盖 agent 字段。root 可覆盖（测试隔离）。

    v0.7（A3，2026-09-06 照照）：写口改道。SPOOR_WRITE_URL 设了 → 事件走
    HTTP 写口（本地 spool 兜底，spoor_http_client 同仓）；没设 → 原本地
    追加，行为零变化。切流姿势：URL 配上即切，不切即回——config-gated，
    双写期用它对账，对平后撤掉本地路径。

    v1.3（2026-09-15 洄）：出生发号。本地分支在锁内发号（setdefault——
    调用方自带 id 则尊重，切流事件先例）；HTTP 分支不在客户端发号：权威
    账本在服务端，id 应在服务端落账那一刻出生（A3 服务端若不走本函数
    落账，需在其落账点补发号——挂账给照照审）。

    显式 root（测试隔离）永远走本地——测试不打网。生产钩子不传 root。
    """
    event["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    n = agent_name()
    if n:
        event["agent"] = n

    if root is None:
        url = os.environ.get("SPOOR_WRITE_URL", "").strip()
        if url:
            _emit_to_write_api(event, url)
            return

    ledger = (Path(root) / "ledger.jsonl") if root else LEDGER

    def _do(p: Path) -> None:
        # v1.3：锁内发号——读尾接力与追加在同一临界区，跨进程同秒不撞
        # v1.3.1：接力失明（尾行无号/形状不认/大行垫底）→ 全库同秒扫描兜底
        event.setdefault("id", _mint_event_id(p, _origin(root)))
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    _with_lock(ledger, _do)


# ── v0.7 写口改道（A3）───────────────────────────────────
# 单进程单客户端：gateway 是钩子的唯一宿主，进程内缓存即可，不落盘不
# 竞争。token 走 env（SPOOR_WRITE_TOKEN），不进代码不进 repo。
# spool 文件在 STIGMERGY_ROOT 下（与账本同家）：写口在 VPS，断网/服务
# 升级期间的事件在本地排队，恢复后 drain 冲走。
_WRITE_CLIENT = None


def _get_write_client(url: str):
    global _WRITE_CLIENT
    if _WRITE_CLIENT is None or _WRITE_CLIENT.url != url.rstrip("/"):
        import spoor_http_client
        _WRITE_CLIENT = spoor_http_client.SpoorWriteClient(
            url=url,
            token=os.environ.get("SPOOR_WRITE_TOKEN", ""),
            spool_path=ROOT / "spool" / "ledger-events.jsonl",
        )
    return _WRITE_CLIENT


def _emit_to_write_api(event: dict, url: str) -> None:
    """事件经 spool → POST /append。失败静默（钩子纪律），事件已在盘上。"""
    try:
        c = _get_write_client(url)
        c.emit(
            type=str(event.get("event", "")),
            actor=event.get("agent", ""),
            room=os.environ.get("SPOOR_WRITE_ROOM", "gateway"),
            payload=event,
            origin=os.environ.get("SPOOR_WRITE_ORIGIN", "") or "wsl-gateway",
        )
    except Exception:
        pass  # 盘都落不进：静默（on_session_end 纪律，无资格炸主路径）


def append_journal(jf: Path, line: str) -> None:
    """带锁的 journal 追加：读→拼→写，全程持锁。

    line 已由调用方拼好（含 mark 与时间戳）。jf 父目录需存在。
    """

    def _do(p: Path) -> None:
        content = p.read_text(encoding="utf-8") if p.exists() else f"# {time.strftime('%Y-%m-%d')}\n"
        p.write_text(content + line + "\n", encoding="utf-8")

    _with_lock(jf, _do)


# ---- v0.9 待办协议：共用「下一步」解析器（server/桥/backstop 三处同源）----
# 协议全文 docs/todo-protocol-v09.md。条目格式：
#   - T12 [洄] 标题 ｜出处｜判据：验收判据
# 确认线：> 排序确认：YYYY-MM-DD（甜心）·确认至第N条  → 前N条=已排序
# 出账收据（journal 里）：✅T12 …（判据达成）/ ↩T13 …（退回待审）

_TODO_CONFIRM_RE = re.compile(
    r"^>\s*排序确认[：:]\s*(\d{4}-\d{2}-\d{2})\s*[（(]([^）)]+)[）)]\s*[·•]?\s*确认至第\s*(\d+)\s*条")
_TODO_ITEM_RE = re.compile(r"^-\s*(T\d+)\s*\[([^\]\n]+)\]\s*(.+)$")
_TODO_SEP_RE = re.compile(r"[｜|]")
_TODO_RECEIPT_RE = re.compile(r"[✅↩]\s*(T\d+)")


def _nextstep_section(text: str) -> "str | None":
    """从 STATUS 正文里挖「下一步」段。两种形态：
    标题式（## 下一步 独占一行，段落到下一个标题为止）；
    行内式（下一步：内容 单行，老格式）。找不到返回 None。"""
    m = re.search(r"(?m)^#{0,3}\s*下一步\s*[：:]?\s*$", text)
    if m:
        rest = text[m.end():]
        m2 = re.search(r"(?m)^#{1,6}\s+\S", rest)
        return rest[:m2.start()] if m2 else rest
    m = re.search(r"(?m)^下一步\s*[：:]\s*(.+)$", text)
    if m:
        return m.group(1)
    return None


def parse_next_steps(text: str) -> dict:
    """解析「下一步」段。返回：
    {section_found, confirm: {date,by,count}|None,
     items: [{id,owner,head,source,criteria,sorted,complete}],
     malformed: [不合规行]}
    sorted=在确认线内；complete=出处+判据齐（四件套的机器面）。"""
    sec = _nextstep_section(text)
    if sec is None:
        return {"section_found": False, "confirm": None, "items": [], "malformed": []}
    confirm = None
    items = []
    malformed = []
    for ln in sec.splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith(">"):
            m = _TODO_CONFIRM_RE.match(s)
            if m:
                confirm = {"date": m.group(1), "by": m.group(2).strip(),
                           "count": int(m.group(3))}
            continue  # 其他引用行（含旧确认线被新线替换的过渡态）忽略
        m = _TODO_ITEM_RE.match(s)
        if m:
            tid, owner, rest = m.group(1), m.group(2).strip(), m.group(3).strip()
            parts = [p.strip() for p in _TODO_SEP_RE.split(rest)]
            head = parts[0]
            source = parts[1] if len(parts) > 1 else ""
            crit = "｜".join(parts[2:]) if len(parts) > 2 else ""
            if re.match(r"^判据\s*[：:]", crit):
                crit = re.split(r"[：:]", crit, 1)[1].strip()
            items.append({"id": tid, "owner": owner, "head": head,
                          "source": source, "criteria": crit})
            continue
        malformed.append(s)
    n = confirm["count"] if confirm else 0
    for i, it in enumerate(items):
        it["sorted"] = i < n
        it["complete"] = bool(it["source"] and it["criteria"])
    return {"section_found": True, "confirm": confirm, "items": items,
            "malformed": malformed}


def todo_receipt_ids(journal_text: str) -> set:
    """从 journal 文本里收出账收据的 id 集（✅/↩ 标记）。"""
    return set(m.group(1) for m in _TODO_RECEIPT_RE.finditer(journal_text))


# ---- v0.4.1 nudge：journal 久未写的搭车提醒 ----
# 动机：写入纪律在熟悉的 runtime（常驻 skill+SOUL）里靠自觉成立，
# 陌生 runtime（kimi code 等）没这层文化，journal 静默断流。
# 设计原则（用户侧维护观裁决）：搭现有动作的便车，不新建仪式——
# 提醒不弹通知、不占频道，只出现在 agent 本来就会读的工具返回尾部。
# 防免疫：只在超期时出现（每次都挂横幅，三天它就成了家具）；
# 防唠叨：2h 冷却；可审计：提醒闪过本身进账本（spoor.nudge.shown，
# 基础设施域前缀——threesome. 是三人协作域，spoor. 是设施自身的呼吸）。
# 失败纪律：提醒层的任何异常一律静默——它没有资格弄坏主功能的返回。
NUDGE_AFTER_H = 4.0      # journal.write 距今超过此小时数才提醒
NUDGE_COOLDOWN_H = 2.0   # 上次提醒距今不足此小时数则静默
NUDGE_SCAN_LINES = 500   # 账本只倒序扫这么多行（性能地板，老账不翻）


def _nudge_text(age_h) -> str:
    head = "workbench journal 从未写过" if age_h is None else f"workbench journal 已 {age_h:.0f}h 未写"
    return (f"[nudge] {head}。收工前 workbench_journal 留一条"
            f"（mark: 坑/判断/数据，一句话即可）——journal 是下个 session 的交接凭据。"
            f"顺手对一眼 STATUS 的「下一步」：新长出来的活落条目（v0.9 格式），"
            f"办完的先落 ✅/↩ 收据再删行。")


# ---- v0.4.2 跨项目 nudge：钩子只提醒，裁判是 agent ----
# 动机（2026-08-20 用户侧裁决）：一个 session 跨项目触达（如微信 session 里
# 调了 workbench_journal）时，账本元数据已全量自动记录（谁/何时/碰了哪个
# 项目——append_ledger 本来就写），但"这段跨项目内容要不要写进项目
# journal"的判断权不归代码。钩子的职责边界：提供通道+提醒，不写内容。
# 设计同 v0.4.1：搭返回的便车、不新建仪式；同一 nudge 冷却共享；
# spoor.nudge.shown 带域标签（ch 字段）以便审计两种提醒各自频率。
XPROJ_MIN_PROJECTS = 2   # 触达 ≥ 此数目的不同项目才构成"跨项目"

XP_NUDGE_TEXT = (
    "[nudge] 本 session 跨项目触达（{projects}）。跨 session 的内容要记进哪个"
    "项目 journal，由 agent 裁决后主动 workbench_journal 写入；不涉及项目可不写。"
)


def _cross_project_nudge(lines: list) -> "str | None":
    """扫最近账本行，聚合本次触达过的不同项目集合，≥2 则返回提醒文本。"""
    try:
        projects: "dict[str, str]" = {}
        for raw in reversed(lines):
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ev = str(obj.get("event", ""))
            proj = obj.get("project")
            if not proj:
                continue
            projects.setdefault(str(proj), obj.get("ts", ""))
            # 只看最近窗口：从最新一条往回，跨项目检测是"当下状态"不是历史学
            if len(projects) >= XPROJ_MIN_PROJECTS:
                break
        if len(projects) >= XPROJ_MIN_PROJECTS:
            return XP_NUDGE_TEXT.format(projects="、".join(sorted(projects)))
        return None
    except Exception:
        return None


def pending_xnudge() -> "str | None":
    """跨项目提醒入口（与 pending_nudge 同纪律：账本即传感器、静默失败）。"""
    try:
        lines: list = []
        if LEDGER.exists():
            with open(LEDGER, encoding="utf-8", errors="replace") as f:
                lines = [l for l in f if l.strip()][-NUDGE_SCAN_LINES:]
        return _cross_project_nudge(lines)
    except Exception:
        return None


# ---- v0.4.3 session gap：末尾检测进环境，出口仍是工具返回 ----
# 动机（2026-08-23 现行犯案）：一下午全用 terminal/git 干活、零 spoor 工具
# 调用——返回层 nudge 根本没机会弹（拉式传感器死了）。把检测搬到
# on_session_end（壳层在 /new /reset CLI退出 gateway过期 时喊一声），
# 纯机械 diff messages 路径签名 vs 账本 journal.write，缺口落账本
# spoor.session.gap 事件；下个 session 的工具返回最前面优先浮现。
# 纯逻辑在 spoor_hooks.py（零依赖，提案 docs/spoor-hooks-proposal.zh.md #2）。
# 消费即记录：spoor.nudge.shown ch=sessgap，每条 gap 只浮现一次——
# agent 裁决"不涉及可不写"后不再骚扰。失败静默纪律不变。


def _load_spoor_hooks():
    """按 __file__ 同目录加载 spoor_hooks——本模块常被插件按路径加载，
    sys.path 里没有本目录，普通 import 会静默失败（cwd bug 表亲，
    2026-08-23 现场抓的）。"""
    import importlib.util, sys
    if "spoor_hooks" in sys.modules:
        return sys.modules["spoor_hooks"]
    _p = Path(__file__).resolve().parent / "spoor_hooks.py"
    _spec = importlib.util.spec_from_file_location("spoor_hooks", _p)
    mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(mod)
    sys.modules["spoor_hooks"] = mod
    return mod


def record_session_gap(messages: list, root=None) -> "str | None":
    """session 末尾调用：算缺口、落账本。返回提醒文本或 None。

    v0.5：gap 事件带 new_projects 字段（普适发现的新项目名）——
    未裁决项目的提醒去重靠账本，不另立状态文件。
    """
    try:
        spoor_hooks = _load_spoor_hooks()
        r = Path(root) if root else ROOT
        text = spoor_hooks.session_gap_nudge(messages, r)
        if text:
            projects = sorted(spoor_hooks.touched_projects(messages, r))
            new_projects = sorted(spoor_hooks.discover_from_messages(messages, r))
            append_ledger({"event": "spoor.session.gap", "text": text,
                           "projects": projects,
                           "new_projects": new_projects}, root=r)
        return text
    except Exception:
        return None


def pending_sessgap(lines: "list | None" = None, root=None) -> "str | None":
    """最新 spoor.session.gap 未被消费（晚于最近一次 ch=sessgap 的 shown）则返回其文本。

    root 参数与 append_ledger 同款隔离约定（照照 8/23 审）：生产不传走
    模块全局 LEDGER，测试传临时目录——不再靠 reload+手改全局。
    """
    try:
        if lines is None:
            ledger = (Path(root) / "ledger.jsonl") if root else LEDGER
            if not ledger.exists():
                return None
            with open(ledger, encoding="utf-8", errors="replace") as f:
                lines = [l for l in f if l.strip()][-NUDGE_SCAN_LINES:]
        gap_ts = gap_text = None
        shown_ts = None
        for raw in reversed(lines):
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ev = str(obj.get("event", ""))
            if ev == "spoor.session.gap" and gap_ts is None:
                gap_ts, gap_text = str(obj.get("ts", "")), str(obj.get("text", ""))
            elif ev == "spoor.nudge.shown" and obj.get("ch") == "sessgap" and shown_ts is None:
                shown_ts = str(obj.get("ts", ""))
            if gap_ts is not None and shown_ts is not None:
                break
        if gap_ts and gap_ts > (shown_ts or ""):
            return gap_text or None
        return None
    except Exception:
        return None


def pending_nudge() -> "str | None":
    """journal 久未写时返回提醒文本，否则 None。

    账本就是传感器：不需要新状态文件，journal 写没写、提醒闪没闪
    ledger.jsonl 自己全知道。逻辑：
    - 最新一条 threesome.journal.write 距今 >= NUDGE_AFTER_H → 提醒
    - 在用 workbench（有账本事件或有 workbench 目录）但从未写 journal → 提醒（新环境引导）
    - 最新一条 spoor.nudge.shown 距今 < NUDGE_COOLDOWN_H → 冷却中，静默
    - 账本缺失且无 workbench 目录 → 没人在用，不多嘴
    - v0.4.2：journal 提醒静默/缺席时，检查跨项目触达（同冷却共享）
    """
    try:
        lines: list = []
        if LEDGER.exists():
            with open(LEDGER, encoding="utf-8", errors="replace") as f:
                lines = [l for l in f if l.strip()][-NUDGE_SCAN_LINES:]
        last_write = None   # 最新 journal.write 的 ts
        last_shown = None   # 最新 nudge.shown 的 ts
        for raw in reversed(lines):
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue    # 脏行不炸（与 ledger_query 同纪律）
            ev = obj.get("event", "")
            if last_write is None and ev == "threesome.journal.write":
                last_write = obj.get("ts", "")
            if last_shown is None and ev == "spoor.nudge.shown":
                last_shown = obj.get("ts", "")
            if last_write and last_shown:
                break
        now = time.time()

        def _age(ts):
            try:
                return (now - time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))) / 3600.0
            except (ValueError, TypeError, OverflowError):
                return None

        if last_shown is not None:
            age_s = _age(last_shown)
            if age_s is not None and age_s < NUDGE_COOLDOWN_H:
                # journal 提醒冷却中——但跨项目提醒独立判断（ch=xproj
                # 单独记账，不与 ch=text/json 抢冷却：跨项目状态可能
                # 在 journal 提醒冷却期内新出现）
                return pending_xnudge_coolcheck(lines)
        in_use = bool(lines) or (ROOT / "workbench").exists()
        if last_write is None:
            if in_use:
                return _nudge_text(None)
            return None
        age_w = _age(last_write)
        if age_w is not None and age_w >= NUDGE_AFTER_H:
            return _nudge_text(age_w)
        # journal 纪律良好（刚写过）——检查跨项目触达
        return _cross_project_nudge(lines)
    except Exception:
        return None


def pending_xnudge_coolcheck(lines: list) -> "str | None":
    """冷却旁路：只按 spoor.nudge.shown ch=xproj 的独立冷却判断跨项目提醒。"""
    try:
        last_xshown = None
        for raw in reversed(lines):
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if obj.get("event") == "spoor.nudge.shown" and obj.get("ch") == "xproj":
                last_xshown = obj.get("ts", "")
                break
        if last_xshown is not None:
            now = time.time()
            try:
                age = (now - time.mktime(time.strptime(last_xshown, "%Y-%m-%dT%H:%M:%S"))) / 3600.0
            except (ValueError, TypeError, OverflowError):
                age = None
            if age is not None and age < NUDGE_COOLDOWN_H:
                return None
        return _cross_project_nudge(lines)
    except Exception:
        return None


def nudge_json(payload: dict) -> str:
    """JSON 工具返回搭车：pending 时注入 _nudge 字段（不改原字段，json.loads 消费方零影响）。"""
    try:
        g = pending_sessgap()
        if g:
            payload["_sessgap"] = g
            _record_shown_ch("sessgap")
        n = pending_nudge()
        if n:
            payload["_nudge"] = n
            _record_shown(n)
    except Exception:
        pass
    return json.dumps(payload, ensure_ascii=False)


def nudge_text(s: str) -> str:
    """纯文本工具返回搭车：pending 时追加一行（格式统一 [nudge] 前缀，便于消费方识别与剥离）。"""
    try:
        g = pending_sessgap()
        if g:
            _record_shown_ch("sessgap")
            s = f"{s}\n{g}"
        n = pending_nudge()
        if n:
            _record_shown(n)
            return f"{s}\n{n}"
    except Exception:
        pass
    return s


def _record_shown_ch(ch: str) -> None:
    """记账某域提醒闪过（sessgap 用：消费即记录，不占 text/xproj 冷却）。"""
    try:
        append_ledger({"event": "spoor.nudge.shown", "ch": ch})
    except Exception:
        pass


def _record_shown(nudge: str) -> None:
    """记账提醒闪过。ch 按内容分流：跨项目提醒=xproj（独立冷却），其余=text/json 旧域。"""
    try:
        ch = "xproj" if "跨项目触达" in nudge else "text"
        append_ledger({"event": "spoor.nudge.shown", "ch": ch})
    except Exception:
        pass
