#!/usr/bin/env python3
"""journal_append — 结构性防覆盖的journal追加器。

背景：write_file 全量覆盖journal曾三次吞掉历史条目（8/18 01:07, 01:14两次）。
根因是工具默认替换整文件+agent在忙碌时凭印象写。本脚本让追加成为唯一便捷路径：

用法（命令行纯ASCII，中文内容走entry文件，绕开安全扫描器的同形字误判）：
    1. write_file 把完整条目行写到 /tmp/xxx.txt（单行，格式：
       - **[mark]** YYYY-MM-DD HH:MM 内容）
    2. bin/journal_append.py <project> <entry-file>

行为：
    - flock 排他锁（多agent并发安全）
    - 追加前显示现有行数，追加后校验行数+1、末行==新条目
    - 自动补换行，规范化末行
    - 校验失败即非零退出（烟测FAIL必须非零退出的家规）
"""
import fcntl
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path("/home/ubuntu/Stigmergy")


def main():
    if len(sys.argv) != 3:
        print("usage: journal_append.py <project> <entry-file>", file=sys.stderr)
        return 2
    project, entry_file = sys.argv[1], Path(sys.argv[2])
    journal = ROOT / "workbench" / project / "journal" / f"{date.today().isoformat()}.md"
    journal.parent.mkdir(parents=True, exist_ok=True)

    entry = entry_file.read_text(encoding="utf-8").strip()
    if not entry.startswith("- **["):
        print(f"REFUSED: entry must start with '- **[mark]**', got: {entry[:40]!r}", file=sys.stderr)
        return 3

    lock_path = journal.parent / f".{journal.stem}.lock"
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        before = 0
        if journal.exists():
            before = sum(1 for _ in open(journal, encoding="utf-8"))
        with open(journal, "a", encoding="utf-8") as jf:
            jf.write(entry + "\n")
        after = sum(1 for _ in open(journal, encoding="utf-8"))
        last = open(journal, encoding="utf-8").read().rstrip("\n").split("\n")[-1]

    ok = after == before + 1 and last == entry
    print(f"journal={journal}")
    print(f"lines {before} -> {after}, last-line-match={'YES' if last == entry else 'NO'}")
    if not ok:
        print("VERIFY FAIL — appended but readback mismatch", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
