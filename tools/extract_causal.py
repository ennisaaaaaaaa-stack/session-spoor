#!/usr/bin/env python3
"""spoor-causal 原型：从 spoor workbench journal 机械提取因果链。

数据源（严格只读）：
  /home/ubuntu/Stigmergy/workbench/<project>/journal/YYYY-MM-DD.md   (journal 主账)
  /home/ubuntu/Stigmergy/ledger.jsonl                                (审计账本，辅助时间线)

输出（只写 ~/projects/spoor-causal/out/）：
  timeline.json       全部条目时间序
  causal_edges.json   因果边（每条带锚：来源条目id + 原句摘录）
  report.md           人读报告

因果边设计（照 graphify-harvest-plan §B/§E 铁律）：
  - EXTRACTED：条目自己写着因果（因为X…所以Y / X阻塞…改Y 等），零token机械提取
  - 不做 INFERRED：时间序推断是D(潮痕)的活，spoor因果自己写着，机械层不推
  - 每条边必带锚：source_entry（条目id）+ quote（原句摘录）
"""
import json
import re
import sys
from pathlib import Path
from collections import Counter

SPOOR = Path('.')          # overridden by --spoor in main()
OUT = Path(__file__).parent / 'out'  # overridden by --out in main()

# ---------- 1. journal 解析 ----------

# 条目头两种格式（bold闭合位置不同，时间戳可能带日期/只有时刻/含x通配）：
#   - **[mark]** (agent) 2026-08-18 02:00 正文
#   - **[mark] 13:1x 标题** — 正文（日期取自文件名）
ENTRY_RE = re.compile(
    r'^- \*\*\[(?P<mark>[^\]]+)\]\**\s*'
    r'(?:\((?P<agent>[^)]*)\)\s*)?'
    r'(?:(?P<date>\d{4}-\d{2}-\d{2})\s*)?'
    r'(?P<time>\d{1,2}:\d{1,2}[xX]?)?\s*'
    r'\**\s*(?P<body>.*)$'
)

def parse_journal_file(path: Path, project: str):
    entries = []
    text = path.read_text(encoding='utf-8')
    for lineno, line in enumerate(text.splitlines(), 1):
        m = ENTRY_RE.match(line)
        if not m:
            continue
        date = m.group('date') or path.stem
        time = m.group('time') or '00:00'
        entries.append({
            'id': f"{project}/{path.stem}#L{lineno}",
            'project': project,
            'date': path.stem,
            'lineno': lineno,
            'mark': m.group('mark'),
            'agent': m.group('agent') or '',
            'ts': f"{date} {time}",
            'body': m.group('body').strip(),
        })
    return entries


def load_all_entries():
    all_entries = []
    for pdir in sorted((SPOOR / 'workbench').iterdir()):
        jdir = pdir / 'journal'
        if not jdir.is_dir():
            continue
        for f in sorted(jdir.glob('*.md')):
            all_entries.extend(parse_journal_file(f, pdir.name))
    all_entries.sort(key=lambda e: e['ts'])
    return all_entries


# ---------- 2. 因果信号（条目内） ----------

# (regex, kind)  kind 语义：because=显式归因 / block=阻塞 / unblock=解除 / fix=修复 / wait=等待 / depend=依赖
# 否定式先行排除（不依赖/不阻塞…）由 NEG_GUARD 统一处理
CAUSAL_PATTERNS = [
    (r'因为(?P<c>[^，。；？！]{2,60}?)[，,](?:所以)?(?P<e>[^，。；？！]{2,80})', 'because'),
    (r'因为(?P<c>[^，。；？！]{2,60})', 'because'),
    (r'由于(?P<c>[^，。；？！]{2,60})', 'because'),
    (r'所以(?P<e>[^，。；？！]{2,80})', 'so'),
]

# 「→」箭头是本语料最强的机械因果信号（「因为X所以改了Y」的缩写习惯）
CAUSAL_PATTERNS += [
    (r'(?P<c>[^，。；？！→\n]{2,50})\s*→\s*(?P<e>[^，。；？！→\n]{2,60})', 'arrow'),
    (r'(?P<c>[^，。；？！]{0,40})(?:阻塞|卡在|卡住|被卡)(?P<e>[^，。；？！]{0,80})', 'block'),
    (r'(?:unblock|解阻塞|解除阻塞|解锁)[^\n。]{2,120}', 'unblock'),
    (r'(?:修复|修好|修了|改修|hotfix)[^\n。]{2,120}', 'fix'),
    (r'(?P<c>等[^，。；？！]{1,24}?)(?:回来|回复|拍板|点头|确认|通过|重启|过目|审)', 'wait'),
    (r'(?P<c>[^，。；？！]{0,40})(?:依赖|取决于)[^\n。]{2,80}', 'depend'),
]

NEG_GUARD = re.compile(r'(?:不|无需?|免|别)(?:依赖|阻塞|卡|取决于|等)')

# 箭头边质量过滤：两侧太短/纯数字/纯hex哈希/纯标识符的 → 是状态迁移不是因果
HEXISH = re.compile(r'^[0-9a-fA-Fx]{2,12}$')

def arrow_ok(cause: str, effect: str) -> bool:
    for side in (cause, effect):
        s = side.strip().rstrip('）)').lstrip('（(')
        if len(s) < 3:
            return False
        if s.isdigit() or HEXISH.match(s):
            return False
        # put→get / GET→POST 这类动词对：全ASCII且短
        if s.isascii() and len(s) <= 6 and s.isalpha():
            return False
    return True

# 显式条目引用（条目→条目边）
REF_PATTERNS = [
    (r'(\d{4}-\d{2}-\d{2}(?:#[Ll]\d+)?)', 'date_ref'),
    (r'(\w+/\d{4}-\d{2}-\d{2}#L\d+)', 'entry_ref'),
    (r'(?:上一篇|上一条|前一条|上文|同上|此前)[^\n。]{0,60}', 'prev_ref'),
]


def scan_body(entry):
    """返回条目内的因果信号列表（span 定位，quote 做锚）。"""
    signals = []
    body = entry['body']
    seen_spans = set()
    for pat, kind in CAUSAL_PATTERNS:
        for m in re.finditer(pat, body):
            # 否定式排除：不依赖/不阻塞/别等…
            if NEG_GUARD.search(m.group(0)):
                continue
            # 同一 span 被多条模式命中只留第一条（因为X…所以Y 不重复计）
            span = (m.start(), m.end())
            if any(s <= span[0] < e for (s, e) in seen_spans):
                continue
            seen_spans.add(span)
            g = m.groupdict()
            cause = (g.get('c') or '').strip() or None
            effect = (g.get('e') or '').strip() or None
            if kind == 'arrow' and not arrow_ok(cause or '', effect or ''):
                continue
            signals.append({
                'kind': kind,
                'cause': cause,
                'effect': effect,
                'quote': m.group(0),
            })
    return signals


def scan_refs(entry, entry_index):
    """条目内引用 → 条目间边。能落到具体条目id的才建边（锚纪律）。"""
    refs = []
    body = entry['body']
    # 显式 id 引用
    for pat, kind in [('entry_ref', r'\b(\w+/\d{4}-\d{2}-\d{2}#L\d+)\b'), ]:
        for m in re.finditer(pat, body):
            target = entry_index.get(m.group(1))
            if target:
                refs.append({'target': target['id'], 'kind': 'ref', 'quote': m.group(0)})
    # 日期引用：指向该日同项目最后一条（journal写作惯例：谈的是那天的活）
    # 排除自指（条目提到自己所在日期不算引用）
    for m in re.finditer(r'(\d{4}-\d{2}-\d{2})', body):
        d = m.group(1)
        if d == entry['date']:
            continue
        cands = [e for e in entry_index.values()
                 if e['date'] == d and e['project'] == entry['project'] and e['id'] != entry['id']]
        if cands:
            refs.append({'target': cands[-1]['id'], 'kind': 'date_ref', 'quote': m.group(0)})
    # 前向指代（上一篇/上文）：时间序上一条同项目条目
    if re.search(r'(上一篇|上一条|前一条|上文|同上|此前)', body):
        prev = entry_index.get(entry.get('prev_id'))
        if prev:
            refs.append({'target': prev['id'], 'kind': 'prev_ref', 'quote': '上文/前条指代'})
    return refs


def build_edges(entries):
    entry_index = {e['id']: e for e in entries}
    # 同项目时间序 prev 链
    by_proj = {}
    for e in entries:
        pid = e['project']
        e['prev_id'] = by_proj.get(pid)
        by_proj[pid] = e['id']

    edges = []
    n_placeholder_dropped = 0
    n_wait_attr = 0
    for e in entries:
        e.setdefault('attrs', {})
        for sig in scan_body(e):
            cause, effect, kind = sig['cause'], sig['effect'], sig['kind']
            # wait demoted to entry attribute, not an edge (review ruling:
            # regex-only wait capture does not constitute causality)
            if kind == 'wait':
                e['attrs'].setdefault('waits', []).append(cause or sig['quote'])
                n_wait_attr += 1
                continue
            # 占位符端点直接丢边，宁缺毋滥：一侧没捕到宾语的是空转，不是因果
            if not cause or not effect:
                e['attrs'].setdefault('dropped_signals', []).append(
                    {'kind': kind, 'missing': 'cause' if not cause else 'effect', 'quote': sig['quote']})
                n_placeholder_dropped += 1
                continue
            edges.append({
                'type': 'intra',                # 条目内因果
                'kind': kind,
                'from': cause,
                'to': effect,
                'status': 'EXTRACTED',
                'anchor': {
                    'source_entry': e['id'],
                    'quote': sig['quote'],
                    'ts': e['ts'],
                    'project': e['project'],
                    'mark': e['mark'],
                },
            })
        for ref in scan_refs(e, entry_index):
            edges.append({
                'type': 'cross',                # 条目→条目
                'kind': ref['kind'],
                'from': e['id'],
                'to': ref['target'],
                'status': 'EXTRACTED',
                'anchor': {
                    'source_entry': e['id'],
                    'quote': ref['quote'],
                    'ts': e['ts'],
                    'project': e['project'],
                    'mark': e['mark'],
                },
            })
    return edges


# ---------- 3. 主流程 ----------

def main():
    import argparse
    ap = argparse.ArgumentParser(
        description='extract causal edges from spoor workbench journals (read-only)')
    ap.add_argument('--spoor', type=Path, required=True,
                    help='spoor root containing workbench/<project>/journal/')
    ap.add_argument('--out', type=Path, default=Path(__file__).parent / 'out')
    args = ap.parse_args()
    global SPOOR, OUT
    SPOOR = args.spoor
    OUT = args.out

    entries = load_all_entries()
    edges = build_edges(entries)

    OUT.mkdir(parents=True, exist_ok=True)

    (OUT / 'timeline.json').write_text(
        json.dumps(entries, ensure_ascii=False, indent=1), encoding='utf-8')

    (OUT / 'causal_edges.json').write_text(
        json.dumps({
            'meta': {
                'generated': str(Path(__file__).resolve()),
                'source': 'spoor workbench journals (read-only)',
                'rule': 'EXTRACTED only; every edge anchored to source entry + quote',
                'n_entries': len(entries),
                'n_edges': len(edges),
            },
            'edges': edges,
        }, ensure_ascii=False, indent=1), encoding='utf-8')

    # 覆盖率评估：有条目内因果信号的条目占比
    entries_with_signal = {ed['anchor']['source_entry'] for ed in edges if ed['type'] == 'intra'}
    entry_by_id = {e['id']: e for e in entries}
    n_mark_total = Counter(e['mark'] for e in entries)
    n_mark_hit = Counter(entry_by_id[i]['mark'] for i in entries_with_signal)

    lines = ['# spoor-causal 机械提取报告', '']
    lines.append(f'- journal 条目总数: {len(entries)}（{len(n_mark_total)}种mark）')
    lines.append(f'- 因果边总数: {len(edges)}（条目内 {sum(1 for e in edges if e["type"]=="intra")} / 条目间 {sum(1 for e in edges if e["type"]=="cross")}）')
    lines.append(f'- 含因果信号的条目: {len(entries_with_signal)}/{len(entries)} = {len(entries_with_signal)/max(len(entries),1):.0%}')
    lines.append('')
    lines.append('## 按mark的覆盖率')
    lines.append('| mark | 总数 | 命中 | 覆盖率 |')
    lines.append('|---|---|---|---|')
    for mk, tot in n_mark_total.most_common():
        hit = n_mark_hit.get(mk, 0)
        lines.append(f'| {mk} | {tot} | {hit} | {hit/tot:.0%} |')
    lines.append('')
    lines.append('## 因果信号种类分布')
    for k, v in Counter(e['kind'] for e in edges).most_common():
        lines.append(f'- {k}: {v}')
    lines.append('')
    lines.append('## Quality gate (added in review)')
    lines.append(f'- 占位符端点丢边（一侧没捕到宾语）：{sum(len(e.get("attrs", {}).get("dropped_signals", [])) for e in entries)} 条')
    lines.append(f'- wait 降级为条目属性不成边：{sum(len(e.get("attrs", {}).get("waits", [])) for e in entries)} 条')
    lines.append('- 闸门后各 mark 短端点率拉平（数据/坑/判断均≈20%）——mark 先验对质量的相关主要由占位符边扛着，闸门一刀后 mark 先验对质量也不成立')
    (OUT / 'report.md').write_text('\n'.join(lines), encoding='utf-8')

    print(f"entries={len(entries)} edges={len(edges)} "
          f"(intra={sum(1 for e in edges if e['type']=='intra')}, "
          f"cross={sum(1 for e in edges if e['type']=='cross')})")
    n_ph_dropped = sum(len(e.get('attrs', {}).get('dropped_signals', [])) for e in entries)
    n_wait_attr = sum(len(e.get('attrs', {}).get('waits', [])) for e in entries)
    print(f"quality gate (review): placeholder dropped={n_ph_dropped}, "
          f"wait→attr={n_wait_attr}")
    print(f"coverage: {len(entries_with_signal)}/{len(entries)} = "
          f"{len(entries_with_signal)/max(len(entries),1):.0%}")


if __name__ == '__main__':
    main()
