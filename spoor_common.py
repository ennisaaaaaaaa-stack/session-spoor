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


def append_ledger(event: dict, root: "Path | None" = None) -> None:
    """带锁的 ledger 追加。具名住户自动盖 agent 字段。root 可覆盖（测试隔离）。"""
    event["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    n = agent_name()
    if n:
        event["agent"] = n

    ledger = (Path(root) / "ledger.jsonl") if root else LEDGER

    def _do(p: Path) -> None:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    _with_lock(ledger, _do)


def append_journal(jf: Path, line: str) -> None:
    """带锁的 journal 追加：读→拼→写，全程持锁。

    line 已由调用方拼好（含 mark 与时间戳）。jf 父目录需存在。
    """

    def _do(p: Path) -> None:
        content = p.read_text(encoding="utf-8") if p.exists() else f"# {time.strftime('%Y-%m-%d')}\n"
        p.write_text(content + line + "\n", encoding="utf-8")

    _with_lock(jf, _do)


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
            f"（mark: 坑/判断/数据，一句话即可）——journal 是下个 session 的交接凭据。")


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
    """session 末尾调用：算缺口、落账本。返回提醒文本或 None。"""
    try:
        spoor_hooks = _load_spoor_hooks()
        r = Path(root) if root else ROOT
        text = spoor_hooks.session_gap_nudge(messages, r)
        if text:
            projects = sorted(spoor_hooks.touched_projects(messages, r))
            append_ledger({"event": "spoor.session.gap", "text": text,
                           "projects": projects}, root=r)
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
