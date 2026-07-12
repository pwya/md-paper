# -*- coding: utf-8 -*-
"""
verify_triage.py -- deterministic self-check for swarm/md_triage.md (the 10 items of md-triage P.自检).

WHY (fixes the documented "已知限制" #2, 2026-07-11)
  The self-check used to be prose Select-String regexes executed by the LLM each run: fragile to
  full/half-width colons, stray spaces, blockquote prefixes -- false-negatives (a slightly malformed
  token line counted as "missing") AND false-positives. Per dev-handbook 6.5 META (knowledge into
  code), the whole check now lives here once, normalized, testable, loud.

TOKEN SEMANTICS (kept in sync with md_swarm_gate_hook.ps1 lines 79-86 -- rule 6 DRY, three sites:
  the gate hook, md-iterate/check_state.py, and here; change one -> check the others):
  - a token LINE is found tolerantly (optional blockquote '>' prefix, CN/EN colon, spacing, emoji);
  - at generation time (--expect pending, the default) the line must ALSO be verbatim-canonical
    `**人工确认：** 待确认` -- because that exact form is the contract users and docs rely on.
    A found-but-malformed line is now a PRECISE diagnosis ("line N, rewrite as ...") instead of a
    silent miss.
  - --expect confirmed mirrors the gate hook exactly: token-shaped line containing 已确认 and not
    待确认 passes (canonical or not), because that is what the gate itself will accept.

THE 10 CHECKS (ids match md-triage/SKILL.md P.自检):
  (1) #### entry count == meta 输入条目数
  (2) entry ids continuous per prefix; Part-1 tables vs Part-2 id sets (WARN -- table layout is fuzzy)
  (3) X-N external resources: referenced-but-undefined = FAIL, defined-but-unreferenced = WARN
  (4) meta counts (纯评价/合并簇/外部资源/需人类操作/低信心/补丁/重写) == body counts
  (5) per-entry field lines follow the canonical relative order (omissions allowed, no reordering)
  (6) every entry has a non-empty 原文
  (7) exactly one token line, verbatim-canonical for the expected state (see above)
  (8) every 人类修改思路 is still the placeholder -- only while the token is 待确认 (after the human
      confirms, the author fills these; the check then no longer applies)
  (9) every entry has 改动类型 in {补丁, 重写}
  (10) the 「需你确认引用授权」 list == exactly the set of 改动类型=重写 entries

Usage:
  py verify_triage.py --triage <workdir>/swarm/md_triage.md [--expect pending|confirmed|any]
  py verify_triage.py --selftest
Exit: 0 = pass (warnings allowed), 1 = at least one FAIL, 2 = fatal (file/structure unreadable).
"""
import argparse
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

CANON_PENDING = '**人工确认：** 待确认'
CANON_CONFIRMED = '**人工确认：** 已确认'

FIELD_ORDER = ['所属合并簇', '涉及外部资源', '需人类操作', '纯评价', '涉及章节',
               '批次', '改动类型', '抽取置信度', '中文摘要', '原文', '人类修改思路']

# one field line, tolerant of: blockquote prefix, ** placement, CN/EN colon, spacing.
_FIELD_RE = re.compile(
    r'^\s*>?\s*\*{0,3}\s*(' + '|'.join(FIELD_ORDER) + r')\s*[:：]?\s*\*{0,3}\s*[:：]?\s*(.*?)\s*$')
_HEAD_RE = re.compile(r'^####\s+(\S+)')
_SEC_RE = re.compile(r'^##[^#]')
_PLACEHOLDER_RE = re.compile(r'^[（(]\s*你填[^）)]{0,6}[）)]$')
_ID_SPLIT = re.compile(r'^(.*?)(\d+)$')


def _norm(s):
    """fold the noise the old regexes choked on: NBSP/zero-width, runs of blanks."""
    s = s.replace(' ', ' ').replace('​', '').replace('﻿', '')
    return re.sub(r'[ \t]+', ' ', s).strip()


class Report:
    def __init__(self):
        self.fails, self.warns, self.oks = [], [], []

    def ok(self, item, msg):
        self.oks.append('[OK]   (%s) %s' % (item, msg))

    def warn(self, item, msg):
        self.warns.append('[WARN] (%s) %s' % (item, msg))

    def fail(self, item, msg):
        self.fails.append('[FAIL] (%s) %s' % (item, msg))

    def dump(self):
        for x in self.oks + self.warns + self.fails:
            print(x)
        print('')
        verdict = 'FAIL (%d)' % len(self.fails) if self.fails else \
                  ('OK with %d warning(s)' % len(self.warns) if self.warns else 'ALL OK')
        print('=== verify_triage: %s ===' % verdict)
        return 1 if self.fails else 0


def split_sections(lines):
    """-> list of (title, start_idx, end_idx) for '## ' sections; title '' = preamble."""
    secs, cur_title, cur_start = [], '', 0
    for i, ln in enumerate(lines):
        if _SEC_RE.match(ln):
            secs.append((cur_title, cur_start, i))
            cur_title, cur_start = _norm(ln.lstrip('#').strip()), i
    secs.append((cur_title, cur_start, len(lines)))
    return secs


def find_section(secs, key):
    for title, a, b in secs:
        if key in title:
            return a, b
    return None


def parse_entries(lines, a, b):
    """#### blocks in [a,b) -> list of dict(id, head_line, fields=[(label, content, line_no)])."""
    entries, cur = [], None
    for i in range(a, b):
        m = _HEAD_RE.match(lines[i])
        if m:
            cur = {'id': m.group(1), 'line': i + 1, 'fields': []}
            entries.append(cur)
            continue
        if cur is None:
            continue
        fm = _FIELD_RE.match(lines[i])
        if fm:
            cur['fields'].append((fm.group(1), fm.group(2), i + 1))
    return entries


def field(entry, label):
    for lab, content, ln in entry['fields']:
        if lab == label:
            return content, ln
    return None, None


def field_nonempty(entry, label, lines):
    """content on the field line, else continuation lines until the next field/heading."""
    content, ln = field(entry, label)
    if content is None:
        return False
    if _norm(content):
        return True
    for j in range(ln, len(lines)):          # ln is 1-based -> lines[ln] is the NEXT line
        s = lines[j]
        if _HEAD_RE.match(s) or _SEC_RE.match(s) or _FIELD_RE.match(s):
            return False
        if _norm(s):
            return True
    return False


def _id_pattern(eid):
    # P1 must not match P11 / X-1 not X-12; ids may hold CJK, letters, digits, '-'
    return re.compile(r'(?<![A-Za-z0-9])' + re.escape(eid) + r'(?![0-9])')


def check(text, expect='pending'):
    rep = Report()
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    secs = split_sections(lines)

    # ---------- (7) token line ----------
    cands = [(i + 1, ln) for i, ln in enumerate(lines)
             if '人工确认' in ln and ('待确认' in ln or '已确认' in ln)]
    state = None
    if not cands:
        rep.fail('7', '令牌行缺失：全文找不到含「人工确认 + 待确认/已确认」的行。必须原样加回：' + CANON_PENDING)
    elif len(cands) > 1:
        rep.fail('7', '令牌行出现 %d 次（有歧义）：行 %s。只允许顶部一行。'
                 % (len(cands), ', '.join(str(n) for n, _ in cands)))
    else:
        n, ln = cands[0]
        raw = ln.rstrip()
        confirmed = ('已确认' in ln) and ('待确认' not in ln)
        state = 'confirmed' if confirmed else 'pending'
        gate_shape = re.match(r'^\s*>?\s*\*\*人工确认', ln) is not None
        if expect == 'pending':
            if confirmed:
                rep.fail('7', '行 %d 令牌已是「已确认」——triage 生成时必须是「待确认」（AI 自确认嫌疑）。' % n)
            elif raw == CANON_PENDING:
                rep.ok('7', '令牌行逐字规范（待确认）。')
            else:
                rep.fail('7', '行 %d 找到令牌但写歪了：「%s」。下游与用户只认逐字形式，改成：%s'
                         % (n, raw.strip(), CANON_PENDING))
        elif expect == 'confirmed':
            if not confirmed:
                rep.fail('7', '行 %d 令牌仍是「待确认」——尚未人工确认。' % n)
            elif not gate_shape:
                rep.fail('7', '行 %d 已确认但行首不是 **人工确认 形状，gate 钩子认不出：「%s」。' % (n, raw.strip()))
            elif raw == CANON_CONFIRMED:
                rep.ok('7', '令牌行逐字规范（已确认）。')
            else:
                rep.warn('7', '行 %d 已确认（gate 可识别的宽容写法）：「%s」——建议改成逐字 %s'
                         % (n, raw.strip(), CANON_CONFIRMED))
        else:
            rep.ok('7', '令牌行在（状态=%s）：「%s」' % (state, raw.strip()))

    # ---------- structure ----------
    p2 = find_section(secs, '第二部分')
    if not p2:
        rep.fail('结构', '找不到「## 第二部分」——无法解析条目。')
        return rep, 2
    entries = parse_entries(lines, p2[0], p2[1])
    if not entries:
        rep.fail('结构', '第二部分里没有任何 #### 条目。')
        return rep, 2
    ids = [e['id'] for e in entries]

    p3 = find_section(secs, '第三部分')
    clusters = parse_entries(lines, p3[0], p3[1]) if p3 else []
    p4 = find_section(secs, '第四部分')
    p1 = find_section(secs, '第一部分')
    auth = find_section(secs, '引用授权')

    def _meta_int(label):
        m = re.search(re.escape(label) + r'[^0-9\n]{0,15}(\d+)', text)
        return int(m.group(1)) if m else None

    # ---------- (1) entry count ----------
    meta_n = _meta_int('输入条目数')
    if meta_n is None:
        rep.warn('1', '元信息里没找到「输入条目数」计数（条目实数 %d）。' % len(ids))
    elif meta_n != len(ids):
        rep.fail('1', '元信息输入条目数=%d，但第二部分实有 %d 条。' % (meta_n, len(ids)))
    else:
        rep.ok('1', '条目数 %d 与元信息一致。' % len(ids))

    # ---------- (2) id continuity + part1 set ----------
    if len(set(ids)) != len(ids):
        dup = sorted({x for x in ids if ids.count(x) > 1})
        rep.fail('2', '条目 ID 重复：%s' % ', '.join(dup))
    groups = {}
    bad_ids = []
    for eid in ids:
        m = _ID_SPLIT.match(eid)
        if not m:
            bad_ids.append(eid)
            continue
        groups.setdefault(m.group(1), []).append(int(m.group(2)))
    if bad_ids:
        rep.warn('2', '这些 ID 结尾不是数字、无法查连续性：%s' % ', '.join(bad_ids))
    gaps = []
    for pref, nums in groups.items():
        want = list(range(1, max(nums) + 1))
        if sorted(nums) != want:
            missing = sorted(set(want) - set(nums))
            gaps.append('%s 缺 %s' % (pref, ','.join(str(x) for x in missing)))
    if gaps:
        rep.fail('2', 'ID 跳号：' + '；'.join(gaps))
    elif not bad_ids:
        rep.ok('2', 'ID 连续不跳号（%d 个前缀组）。' % len(groups))

    pure_ids = {e['id'] for e in entries if _norm(field(e, '纯评价')[0] or '').startswith('是')}
    if p1:
        p1_text = '\n'.join(lines[p1[0]:p1[1]])
        found1 = {eid for eid in ids if _id_pattern(eid).search(p1_text)}
        expected1 = set(ids) - pure_ids
        if found1 != expected1:
            only_p2 = sorted(expected1 - found1)
            only_p1 = sorted(found1 - expected1)
            msg = []
            if only_p2:
                msg.append('第一部分缺：%s' % ', '.join(only_p2))
            if only_p1:
                msg.append('第一部分多（纯评价不该进表）：%s' % ', '.join(only_p1))
            rep.warn('2', '第一/第二部分 ID 集不一致（表格解析有模糊性，人工瞄一眼）——' + '；'.join(msg))
        else:
            rep.ok('2', '第一部分表格 ID 与第二部分一致（纯评价已除外）。')
    else:
        rep.warn('2', '没找到「## 第一部分」，跳过两部分 ID 对账。')

    # ---------- (3) X-N ----------
    xdef = set()
    if p4:
        xdef = set(re.findall(r'X-\d+', '\n'.join(lines[p4[0]:p4[1]])))
    body = '\n'.join(lines[p2[0]:p2[1]] + (lines[p3[0]:p3[1]] if p3 else []))
    xref = set(re.findall(r'X-\d+', body))
    und = sorted(xref - xdef)
    unused = sorted(xdef - xref)
    if und:
        rep.fail('3', '条目引用了第四部分没定义的外部资源：%s' % ', '.join(und))
    if unused:
        rep.warn('3', '第四部分定义了但没条目引用：%s' % ', '.join(unused))
    if not und and not unused:
        rep.ok('3', '外部资源 X-N 正反向一致（%d 个）。' % len(xdef))

    # ---------- (4) meta counts ----------
    n_human = sum(1 for e in entries
                  if _norm(field(e, '需人类操作')[0] or '') not in ('', '无') and field(e, '需人类操作')[0] is not None)
    n_low = sum(1 for e in entries if _norm(field(e, '抽取置信度')[0] or '').startswith('低'))
    kinds = {e['id']: _norm(field(e, '改动类型')[0] or '') for e in entries}
    n_patch = sum(1 for v in kinds.values() if v.startswith('补丁'))
    n_rewrite = sum(1 for v in kinds.values() if v.startswith('重写'))
    actual = {'纯评价数': len(pure_ids), '合并簇数': len(clusters), '外部资源数': len(xdef),
              '需人类操作数': n_human, '低信心条目数': n_low}
    for label, real in actual.items():
        got = _meta_int(label)
        if got is None:
            rep.warn('4', '元信息没找到「%s」（实数 %d）。' % (label, real))
        elif got != real:
            rep.fail('4', '元信息 %s=%d，但正文实数 %d。' % (label, got, real))
    mp = re.search(r'补丁[^0-9\n]{0,6}(\d+)\s*条', text)
    mr = re.search(r'重写[^0-9\n]{0,6}(\d+)\s*条', text)
    if mp and int(mp.group(1)) != n_patch:
        rep.fail('4', '改动类型分布：补丁计数 %s ≠ 实数 %d。' % (mp.group(1), n_patch))
    if mr and int(mr.group(1)) != n_rewrite:
        rep.fail('4', '改动类型分布：重写计数 %s ≠ 实数 %d。' % (mr.group(1), n_rewrite))
    if not (mp and mr):
        rep.warn('4', '元信息没找到完整「改动类型分布（补丁 N 条 / 重写 M 条）」。')
    if not [f for f in rep.fails if f.startswith('[FAIL] (4)')]:
        rep.ok('4', '元信息计数与正文一致（可解析的都对上了）。')

    # ---------- (5) field order ----------
    order_idx = {lab: i for i, lab in enumerate(FIELD_ORDER)}
    for e in entries:
        seen, last, bad = set(), -1, None
        for lab, _c, ln in e['fields']:
            if lab in seen:
                rep.warn('5', '条目 %s 字段「%s」出现多次（行 %d）。' % (e['id'], lab, ln))
                continue
            seen.add(lab)
            if order_idx[lab] < last:
                bad = (lab, ln)
                break
            last = order_idx[lab]
        if bad:
            rep.fail('5', '条目 %s 行序违约：「%s」（行 %d）出现在更靠后的字段之后。规范顺序：%s'
                     % (e['id'], bad[0], bad[1], ' → '.join(FIELD_ORDER)))
    if not [f for f in rep.fails if f.startswith('[FAIL] (5)')]:
        rep.ok('5', '每条字段行序符合规范（允许省略、不允许换位）。')

    # ---------- (6) 原文 non-empty ----------
    empty6 = [e['id'] for e in entries if not field_nonempty(e, '原文', lines)]
    if empty6:
        rep.fail('6', '这些条目缺非空「原文」：%s' % ', '.join(empty6))
    else:
        rep.ok('6', '每条都有非空「原文」。')

    # ---------- (8) 思路 placeholder (only while pending) ----------
    if state != 'confirmed':
        filled = []
        for m in re.finditer(r'\*{0,3}\s*人类修改思路\s*[:：]?\s*\*{0,3}\s*[:：]?\s*([^\n/]*)', text):
            c = _norm(m.group(1))
            if c and not _PLACEHOLDER_RE.match(c):
                ln_no = text[:m.start()].count('\n') + 1
                filled.append('行 %d：「%s…」' % (ln_no, c[:24]))
        if filled:
            rep.fail('8', 'AI 违规填写了「人类修改思路」（应一律留 （你填） 占位）：' + '；'.join(filled))
        else:
            rep.ok('8', '所有「人类修改思路」都是占位，AI 未越权代填。')
    else:
        rep.ok('8', '令牌已确认——「人类修改思路」由作者填写，本项不再检查。')

    # ---------- (9) 改动类型 ----------
    bad9 = [eid for eid, v in kinds.items() if v not in ('补丁', '重写')]
    if bad9:
        rep.fail('9', '这些条目缺「改动类型」或值不合法（只能 补丁/重写）：%s' % ', '.join(bad9))
    else:
        rep.ok('9', '每条「改动类型」合法（补丁 %d / 重写 %d）。' % (n_patch, n_rewrite))

    # ---------- (10) rewrite authorization list ----------
    rewrite_ids = sorted(eid for eid, v in kinds.items() if v.startswith('重写'))
    if auth:
        atext = '\n'.join(lines[auth[0]:auth[1]])
        missing = [eid for eid in rewrite_ids if not _id_pattern(eid).search(atext)]
        extra = [eid for eid in sorted(set(ids) - set(rewrite_ids)) if _id_pattern(eid).search(atext)]
        if rewrite_ids:
            if missing:
                rep.fail('10', '「引用授权」清单漏了重写条目：%s' % ', '.join(missing))
            if extra:
                rep.fail('10', '「引用授权」清单多列了非重写条目：%s' % ', '.join(extra))
            if not missing and not extra:
                rep.ok('10', '「引用授权」清单与重写条目一一对应（%d 条）。' % len(rewrite_ids))
        else:
            if '无' in atext:
                rep.ok('10', '无重写条目，清单如实写「无」。')
            else:
                rep.warn('10', '无重写条目，但「引用授权」节没写「无」。')
    else:
        if rewrite_ids:
            rep.fail('10', '有 %d 条重写（%s）但找不到「⚠️ 需你确认引用授权」节。'
                     % (len(rewrite_ids), ', '.join(rewrite_ids)))
        else:
            rep.warn('10', '找不到「引用授权」节（无重写条目，影响小）。')

    return rep, None


def run(triage_path, expect):
    try:
        text = open(triage_path, encoding='utf-8-sig').read()
    except OSError as e:
        print('FATAL: 打不开 triage 文件：%s (%s)' % (triage_path, e))
        return 2
    print('=== verify_triage (md_triage.md 十项确定性自检) ===')
    print('file  :', triage_path)
    print('expect:', expect)
    print('')
    rep, fatal = check(text, expect)
    rc = rep.dump()
    return fatal if fatal is not None else rc


# ------------------------------------------------------------------ selftest
GOOD = """# 修订意见整理报告

> # ⛔ 待你确认 ⛔
> **审完下面这份清单后，把「待确认」手动改成「已确认」**。

**人工确认：** 待确认
**来源**：测试 / **输入条目数**：3 / **改动类型分布**（补丁 2 条 / 重写 1 条）/ **需人类操作数**：1 / **合并簇数**：1 / **外部资源数**：1 / **纯评价数**：1 / **低信心条目数**：1 / **生成时间**：2026-07-11

## 分批方案总览（AI 默认·你可改）
- Task 1 并行(1小工): P1 改引言
- Task 2 单独串行(整节重写): P2 重写理论框架

## ⚠️ 需你确认引用授权（标了「重写」= md-swarm 会删/换本节引用）
- P2（重写：整段重写理论框架）

## ⚠️ 需你重点核对（低信心 / 待补）
- P3（低信心）

## 第一部分：按内在逻辑分类
| 编号 | 来源 | 章节 | 摘要 | 重要性 | 批次 |
|---|---|---|---|---|---|
| P1 | 自撰 | 引言 | 摘要一 | 次要 | Task 1 Agent 1 |
| P2 | 自撰 | 理论 | 摘要二 | 重大 | Task 2 Agent 1 |

## 第二部分：按原始顺序
#### P1 · 写作 · 次要 · 引言
**涉及外部资源：** X-1
**需人类操作：** 补充采集
**纯评价：** 否
**涉及章节：** 引言
**批次：** Task 1 Agent 1
**改动类型：** 补丁
**抽取置信度：** 高
**中文摘要：** 摘要一
**原文：** original text one
**人类修改思路：**（你填）

#### P2 · 理论 · 重大 · 理论
**所属合并簇：** MC-1（跨审稿人共识）
**纯评价：** 否
**涉及章节：** 理论
**批次：** Task 2 Agent 1
**改动类型：** 重写
**抽取置信度：** 中
**中文摘要：** 摘要二
**原文：** original text two
**人类修改思路：**（你填）

#### P3 · 不明 · 次要 · 全局
**纯评价：** 是
**涉及章节：** 全局
**批次：** —
**改动类型：** 补丁
**抽取置信度：** 低
**中文摘要：** 客套话
**原文：** great paper overall
**人类修改思路：**（你填）

## 第三部分：合并问题簇
#### MC-1 · 理论 · 重大 · 理论
**统一摘要：** 重写理论框架 / **成员条目：** P2 / **合并理由：** 同指向 / **批次：** Task 2 Agent 1

## 第四部分：外部资源清单
| 编号 | 类别 | 来源条目 | 获取建议 |
|---|---|---|---|
| X-1 | 文献 | P1 | 找作者要 |

## 第五部分：需人类操作查阅指南
搜 `**需人类操作：**` 定位；完成后加删除线。
"""


def selftest():
    fails = 0

    def case(name, text, expect, want_rc, want_frag=None):
        nonlocal fails
        rep, fatal = check(text, expect)
        rc = 1 if rep.fails else 0
        if fatal is not None:
            rc = fatal
        out = '\n'.join(rep.fails + rep.warns)
        good = (rc == want_rc) and (want_frag is None or any(want_frag in f for f in rep.fails))
        print('[%s] %s' % ('OK  ' if good else 'FAIL', name))
        if not good:
            fails += 1
            print('   rc=%d want=%d; fails=%s' % (rc, want_rc, rep.fails))
        return out

    # 1. canonical good doc -> all pass, zero FAIL
    case('good doc passes', GOOD, 'pending', 0)

    # 2. token malformed (blockquote + EN colon + emoji) -> found but named non-canonical
    t = GOOD.replace(CANON_PENDING, '> **人工确认:** ⬜ 待确认')
    case('malformed token -> precise diagnosis, not "missing"', t, 'pending', 1, '写歪了')

    # 3. token already confirmed at generation -> self-confirm suspicion
    t = GOOD.replace(CANON_PENDING, CANON_CONFIRMED)
    case('self-confirmed token FAILs', t, 'pending', 1, '自确认')

    # 4. AI filled 思路
    t = GOOD.replace('**人类修改思路：**（你填）\n\n#### P2', '**人类修改思路：** 我建议大改。\n\n#### P2', 1)
    case('AI-filled 思路 FAILs', t, 'pending', 1, '违规填写')

    # 5. id gap: rename P3 -> P4
    t = GOOD.replace('#### P3', '#### P4').replace('P3（低信心）', 'P4（低信心）')
    case('id gap FAILs', t, 'pending', 1, '跳号')

    # 6. missing 改动类型 on P1
    t = GOOD.replace('**改动类型：** 补丁\n**抽取置信度：** 高', '**抽取置信度：** 高', 1)
    case('missing 改动类型 FAILs', t, 'pending', 1, '改动类型')

    # 7. authorization list misses P2
    t = GOOD.replace('- P2（重写：整段重写理论框架）', '- （无）')
    case('auth list missing rewrite entry FAILs', t, 'pending', 1, '漏了重写')

    # 8. empty 原文
    t = GOOD.replace('**原文：** original text one', '**原文：**', 1)
    case('empty 原文 FAILs', t, 'pending', 1, '原文')

    # 9. field order violated (原文 before 中文摘要)
    t = GOOD.replace('**中文摘要：** 摘要一\n**原文：** original text one',
                     '**原文：** original text one\n**中文摘要：** 摘要一', 1)
    case('field-order violation FAILs', t, 'pending', 1, '行序')

    # 10. confirmed doc + author-filled 思路 + expect confirmed -> passes ((8) skipped)
    t = GOOD.replace(CANON_PENDING, CANON_CONFIRMED).replace(
        '**人类修改思路：**（你填）\n\n#### P2', '**人类修改思路：** 按我邮件里的思路改。\n\n#### P2', 1)
    case('confirmed + filled 思路 passes with --expect confirmed', t, 'confirmed', 0)

    # 11. meta count mismatch
    t = GOOD.replace('**输入条目数**：3', '**输入条目数**：4')
    case('meta entry-count mismatch FAILs', t, 'pending', 1, '输入条目数')

    # 12. undefined X reference
    t = GOOD.replace('**涉及外部资源：** X-1', '**涉及外部资源：** X-9', 1)
    case('undefined X-N FAILs', t, 'pending', 1, 'X-9')

    # 13. tolerant-confirmed (gate-shape, emoji) passes expect=confirmed with warning only
    t = GOOD.replace(CANON_PENDING, '> **人工确认：** ✅ 已确认')
    case('gate-tolerant confirmed passes (warn only)', t, 'confirmed', 0)

    print('')
    print('=== selftest: %s ===' % ('ALL PASSED' if fails == 0 else '%d FAILED' % fails))
    return 0 if fails == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--triage', help='path to swarm/md_triage.md')
    ap.add_argument('--expect', choices=['pending', 'confirmed', 'any'], default='pending',
                    help='pending = right after generation (default); confirmed = before md-swarm')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    if not a.triage:
        ap.error('--triage is required (or use --selftest)')
    sys.exit(run(a.triage, a.expect))


if __name__ == '__main__':
    main()
