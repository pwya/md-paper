#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit_coverage.py — md-swarm【D·完整性审计】的清单生成器（只读·确定性·只列事实不判断）。

干什么：扫 swarm/md_triage.md 列出所有意见编号 + 扫 swarm/patches_applied/ 列出所有 patch 文件，
        做"同名对账"——每条意见有没有 机改-<编号>.json 同名文件。把这份原始清单交给 D agent，
        由 D 逐条判断（被合并文件覆盖？需人工？真漏？）。

设计原则：脚本只列事实、不做判断。判断（经合并覆盖/经簇覆盖/待人工/真漏）全交 D agent——
        D 能读 patch 文件内容、理解语义；脚本是死的，做不好判断、且会被 LLM 偶发的不规范命名
        （如 机改-P9-P11.json 一个文件塞两条意见）绊倒。脚本只保证一件事：不漏列——把所有意见
        编号、所有 patch 文件、谁有同名谁没有，原样摆出来。D 照着清单逐条核，清单兜底不漏。

        所以脚本不解析 triage 的「需人类操作/纯评价/批次/所属合并簇」字段（那些判断交 D 读 triage
        自己做），只抽四级标题的编号 + 列 patch 文件名。依赖的格式假设最小化，最不易被上游绊倒。

用法：
  py audit_coverage.py --triage swarm/md_triage.md \\
                       --patches-dir swarm/patches_applied \\
                       --out swarm/coverage_check.txt

退出码：0 = 正常跑完；1 = triage 文件读不到。
"""

import sys
import os
import re
import argparse


def _utf8_stdout():
    """Windows GBK 码页下 Python stdout/stderr 默认非 UTF-8，含中文会 UnicodeEncodeError。统一重设 utf-8。"""
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass


# 四级标题抽编号：  #### 审-1 · 理论 · 重大 · Literature review...   →  审-1
# 编号格式（md-triage 规定）：R1-C1 / ED-C1 / P1 / N1 / MC-N / MD-N；实际可能还有 审-N 等变体。
_HEAD_RE = re.compile(r'^####\s+(\S+)')


def list_triage_ids(path):
    """扫 md_triage.md，返回所有意见编号（四级标题第一个 token），保留出现顺序。
    跳过簇条目（MC-N/MD-N）——簇通过其成员体现，不单独对账。"""
    ids = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            m = _HEAD_RE.match(line)
            if not m:
                continue
            cid = m.group(1)
            if cid.startswith('MC-') or cid.startswith('MD-'):
                continue  # 簇条目，跳过
            ids.append(cid)
    return ids


def list_patches(patches_dir):
    """扫 patches_applied/，返回 (文件名列表, 编号列表)，按文件名排序。
    编号 = 文件名去 机改- 前缀和 .json 后缀。"""
    files = []
    ids = []
    if os.path.isdir(patches_dir):
        for name in sorted(os.listdir(patches_dir)):
            if not name.endswith('.json'):
                continue
            if not name.startswith('机改-'):
                continue
            files.append(name)
            ids.append(name[len('机改-'):-len('.json')])
    return files, ids


def main():
    _utf8_stdout()
    ap = argparse.ArgumentParser(description='md-swarm D 审计的清单生成器（意见↔patch 同名对账·只列事实）')
    ap.add_argument('--triage', required=True, help='swarm/md_triage.md 路径')
    ap.add_argument('--patches-dir', required=True, help='swarm/patches_applied 目录')
    ap.add_argument('--out', required=True, help='输出 coverage_check.txt 路径')
    args = ap.parse_args()

    if not os.path.isfile(args.triage):
        print(f'[audit_coverage] 找不到 triage 文件: {args.triage}', file=sys.stderr)
        sys.exit(1)

    triage_ids = list_triage_ids(args.triage)
    patch_files, patch_ids = list_patches(args.patches_dir)
    patch_id_set = set(patch_ids)
    triage_id_set = set(triage_ids)

    # 同名对账：意见编号 → 有无 机改-<编号>.json 同名文件
    same_name = [tid for tid in triage_ids if tid in patch_id_set]
    no_same = [tid for tid in triage_ids if tid not in patch_id_set]
    # patches 里有、但 triage 没有对应意见的（合并文件如 P9-P11 / 簇 patch 如 MD-1 / 终审 patch 等）
    extra = [pid for pid in patch_ids if pid not in triage_id_set]

    lines = []
    lines.append('# 对账原始清单（audit_coverage.py 产出 · 只列事实 · 判断交 D）')
    lines.append('')
    lines.append(f'意见编号数: {len(triage_ids)}    patch 文件数: {len(patch_files)}')
    lines.append('')
    lines.append('## 一、md_triage.md 的意见编号（脚本扫四级标题·跳过 MC/MD 簇）')
    lines.append(', '.join(triage_ids) if triage_ids else '(无)')
    lines.append('')
    lines.append('## 二、patches_applied/ 的 patch 文件')
    lines.append(', '.join(patch_files) if patch_files else '(无)')
    lines.append('')
    lines.append('## 三、同名对账（意见编号 → 有无 机改-<编号>.json 同名文件）')
    lines.append('')
    lines.append('### 有同名文件（D 逐条审: 做对/半拉子/跑偏/存疑）')
    lines.append(', '.join(same_name) if same_name else '(无)')
    lines.append('')
    lines.append('### 无同名文件（D 必须逐条核, 别直接当漏 —— 见第四节提示）')
    lines.append(', '.join(no_same) if no_same else '(无)')
    lines.append('')
    lines.append('## 四、给 D 的提示: 无同名文件 ≠ 漏, 逐条核以下可能')
    lines.append('  ① 被合并文件覆盖? ——看第五节有没有像 机改-P9-P11.json 这种【一个文件名含多个编号】的,')
    lines.append('     读该 patch 文件内容确认它覆盖了这条意见。覆盖了→不算漏(报告标"经合并文件覆盖"+证据)。')
    lines.append('  ② 是合并簇成员? ——读 triage 该条的「所属合并簇」字段, 若有簇(MC-N/MD-N), 看有没有')
    lines.append('     机改-<簇编号>.json, 读它确认覆盖了这条。覆盖了→不算漏(标"经簇覆盖"+证据)。')
    lines.append('  ③ 需人类操作/纯评价? ——读 triage 该条的「需人类操作」「纯评价」「批次」字段。')
    lines.append('     若需人类/纯评价/批次=—, 本就不该有文件→不算漏(标"待人工/纯评价")。')
    lines.append('  ④ 以上都不是 → 真漏, 列入"漏项", 打回重做。')
    lines.append('')
    lines.append('## 五、patches 里有、但 triage 没有对应意见的文件（合并文件/簇 patch/终审 patch 等）')
    lines.append('   D 要确保这些都被 ①② 核过覆盖关系, 别遗漏。')
    lines.append(', '.join(extra) if extra else '(无)')
    lines.append('')

    text = '\n'.join(lines)
    out_dir = os.path.dirname(args.out)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        f.write(text)

    print(text)
    print(f'\n[audit_coverage] 已写出: {args.out}', file=sys.stderr)


if __name__ == '__main__':
    main()
