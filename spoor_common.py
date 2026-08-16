"""
共享层：多agent并发的公共设施。

SPOOR_AGENT: 环境变量，设了就给 journal/ledger 条目盖名字戳。
  - 不设 = 匿名（单住户兼容，现有数据零影响）
  - 设了  = 每条 journal 行变成 "- **[判断]** (名字) 时间 正文"
            ledger 每条事件多一个 "agent": "名字" 字段

SPOOR_LOCK_*: 跨进程文件锁。fcntl.flock 在同一文件描述符上
  排队等待，两个进程同时写 journal/ledger 不会交错损坏。
  锁文件放 {root}/.locks/，gitignore 掉（运行时产物）。

无锁退化：非POSIX平台 import fcntl 失败时直接裸写——
  单住户场景本来就无并发，行为与旧版完全一致。
"""
import json
import os
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: no fcntl — degrade to unlocked single-writer mode
    fcntl = None

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

    完全自定义：任何字符串都可以（UTF-8 支持中文名）。
    开源场景——每个住户在**自己的环境变量**里声明自己的名字，
    不存在任何写死的名单。fork 仓库的陌生人 set SPOOR_AGENT=自己的名字
    即可署名，无需改任何代码。
    """
    return os.environ.get("SPOOR_AGENT", "").strip()


def stamped(now: str) -> str:
    """journal 行内的时间段：匿名 → 时间；具名 → (名字) 时间。"""
    n = agent_name()
    return f"({n}) {now}" if n else now


def _with_lock(path: Path, write_fn):
    """在排它锁保护下执行 write_fn(path)。锁失败静默退化裸写。

    fcntl is None（Windows）→ 无锁直接写：单住户无并发，安全；
    多住户并发写在 Windows 上不受保护——README 已声明。
    """
    if fcntl is None:
        return write_fn(path)
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


def append_ledger(event: dict) -> None:
    """带锁的 ledger 追加。具名住户自动盖 agent 字段。"""
    event["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    n = agent_name()
    if n:
        event["agent"] = n

    def _do(p: Path) -> None:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    _with_lock(LEDGER, _do)


def append_journal(jf: Path, line: str) -> None:
    """带锁的 journal 追加：读→拼→写，全程持锁。

    line 已由调用方拼好（含 mark 与时间戳）。jf 父目录需存在。
    """

    def _do(p: Path) -> None:
        content = p.read_text(encoding="utf-8") if p.exists() else f"# {time.strftime('%Y-%m-%d')}\n"
        p.write_text(content + line + "\n", encoding="utf-8")

    _with_lock(jf, _do)
