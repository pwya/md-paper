# -*- coding: utf-8 -*-
"""
render_changeset.py -- render a md-swarm changeset.json into a human-readable Markdown
review sheet: one section per patch showing BEFORE (find) -> AFTER (replace), the target
section, mode/intent, and any citation keys dropped or added.

This is the "porcelain" view of the machine-readable swarm/changeset.json so a human can
eyeball, per comment, exactly what text changed -- before trusting the deterministic apply.
Read-only on the changeset; writes a .md report. No LLM, no randomness.

Usage:
  py render_changeset.py --changeset swarm\\changeset.json [--out swarm\\changeset_review.md]
Exit: 0 = wrote report; 2 = fatal (changeset missing / unparseable).
"""
import argparse, io, json, os, re, sys
from collections import Counter
from _citescan import citekeys_in   # single source of truth for the [@citekey] rule (DRY, see _citescan.py)

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def fence(text):
    """Wrap text in a code fence longer than any backtick run inside, so content that
    itself contains backticks still renders correctly."""
    longest = 0
    for run in re.findall(r'`+', text or ''):
        longest = max(longest, len(run))
    bar = '`' * max(3, longest + 1)
    return bar + '\n' + (text if text else '') + '\n' + bar


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--changeset', required=True)
    ap.add_argument('--out', default=None, help='output md path (default: <changeset>_review.md)')
    a = ap.parse_args()

    if not os.path.exists(a.changeset):
        print('FATAL: changeset not found:', a.changeset); sys.exit(2)
    try:
        cs = json.load(open(a.changeset, encoding='utf-8'))
    except Exception as e:
        print('FATAL: cannot parse changeset json:', e); sys.exit(2)

    patches = cs.get('patches') or []
    out = a.out or (os.path.splitext(a.changeset)[0] + '_review.md')

    L = []
    L.append('# Changeset review  (%d patch(es))' % len(patches))
    L.append('')
    L.append('- source     : `%s`' % (cs.get('source_md') or 'manuscript.md'))
    L.append('- changeset  : `%s`' % a.changeset)
    L.append('')
    L.append('> Human-readable view of `changeset.json`. Per patch: BEFORE (`find`) -> AFTER (`replace`).')
    L.append('> Dropped/added citations are flagged (a plain `modify` patch should drop none).')
    L.append('')
    for i, p in enumerate(patches):
        pid = str(p.get('id') or ('patch-%d' % (i + 1)))
        target = str(p.get('target') or '').strip()
        mode = str(p.get('mode') or 'patch')
        intent = str(p.get('intent') or 'modify')
        find = p.get('find') or ''
        repl = p.get('replace') or ''
        dropped = sorted((Counter(citekeys_in(find)) - Counter(citekeys_in(repl))).keys())
        added = sorted((Counter(citekeys_in(repl)) - Counter(citekeys_in(find))).keys())
        L.append('---')
        L.append('')
        L.append('## %s' % pid)
        L.append('')
        L.append('- section     : %s' % (target if target else '(unspecified)'))
        L.append('- mode/intent : `%s` / `%s`' % (mode, intent))
        if dropped:
            L.append('- **DROPPED citation(s)** : %s' % ', '.join('@' + k for k in dropped))
        if added:
            L.append('- added citation(s)       : %s' % ', '.join('@' + k for k in added))
        L.append('')
        L.append('**BEFORE:**')
        L.append('')
        L.append(fence(find))
        L.append('')
        L.append('**AFTER:**')
        L.append('')
        L.append(fence(repl))
        L.append('')

    with open(out, 'w', encoding='utf-8', newline='') as f:
        f.write('\n'.join(L) + '\n')
    print('OK -> wrote %s  (%d patch(es))' % (out, len(patches)))
    sys.exit(0)


if __name__ == '__main__':
    main()
