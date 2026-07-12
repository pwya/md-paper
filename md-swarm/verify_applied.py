# -*- coding: utf-8 -*-
"""
verify_applied.py -- deterministic "did each patch actually land?" check for md-swarm Phase 2.

Replaces the non-deterministic "model greps to confirm the change landed". For every patch in
changeset.json, inspects the CURRENT manuscript and classifies:

  LANDED      : the patch's `replace` text is present  -> it applied and was not later overwritten
  NOT-APPLIED : `replace` absent but `find` still present verbatim -> this patch did NOT land  (HARD)
  UNCONFIRMED : neither present -> the region changed but not to this exact text; usually a LATER
                patch edited the same area (overlap). NOT a hard failure -- listed for a human glance.

Only NOT-APPLIED is a hard failure (something the agent claimed but the file does not show).
UNCONFIRMED is expected when patches touch overlapping regions, so it only warns.

Input A -> report B, no LLM, no randomness. Run right after apply_md_changeset.py in Phase 2.

Usage:
  py verify_applied.py --changeset swarm\\changeset.json --manuscript manuscript.md
Exit: 0 = all LANDED/UNCONFIRMED; 1 = >=1 NOT-APPLIED; 2 = fatal.
"""
import argparse, io, json, os, sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# fold invisible whitespace (NBSP etc.) so this check uses the same match basis as
# apply_md_changeset.py -- otherwise a folded manuscript vs an un-folded find could disagree.
_WS_TO_SPACE = tuple(chr(o) for o in (0x00a0, 0x2007, 0x2009, 0x202f))
_WS_DROP = tuple(chr(o) for o in (0x200b, 0x200c, 0x200d, 0x2060, 0xfeff, 0x00ad))


def norm_ws(s):
    for c in _WS_TO_SPACE:
        s = s.replace(c, ' ')
    for c in _WS_DROP:
        s = s.replace(c, '')
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--changeset', required=True)
    ap.add_argument('--manuscript', default=None, help='override source_md from the changeset')
    a = ap.parse_args()

    if not os.path.exists(a.changeset):
        print('FATAL: changeset not found:', a.changeset); sys.exit(2)
    try:
        cs = json.load(open(a.changeset, encoding='utf-8'))
    except Exception as e:
        print('FATAL: cannot parse changeset json:', e); sys.exit(2)

    man = a.manuscript or cs.get('source_md') or 'manuscript.md'
    if not os.path.isabs(man) and not os.path.exists(man):
        cand = os.path.join(os.path.dirname(os.path.abspath(a.changeset)), man)
        if os.path.exists(cand):
            man = cand
    if not os.path.exists(man):
        print('FATAL: manuscript not found:', man); sys.exit(2)
    # newline='' so the file's own \n / \r\n is compared as-is (matches apply_md_changeset.py)
    with open(man, encoding='utf-8', newline='') as f:
        text = norm_ws(f.read().replace('\r\n', '\n'))   # normalize CRLF->LF + fold invisible ws to match apply

    patches = cs.get('patches') or []
    results = []
    not_applied = 0
    unconfirmed = 0
    for i, p in enumerate(patches):
        pid = str(p.get('id') or ('patch-%d' % (i + 1)))
        find = norm_ws((p.get('find') or '').replace('\r\n', '\n'))
        repl = norm_ws((p.get('replace') or '').replace('\r\n', '\n'))
        if repl and repl in text:
            results.append((pid, 'LANDED', ''))
        elif find and find in text:
            results.append((pid, 'NOT-APPLIED', 'find still present verbatim -- this patch did not land'))
            not_applied += 1
        else:
            results.append((pid, 'UNCONFIRMED', 'neither find nor replace present (likely a later patch edited the same region)'))
            unconfirmed += 1

    print('=== verify_applied ===')
    print('changeset :', a.changeset)
    print('manuscript:', man)
    print('patches   :', len(patches))
    print('')
    for pid, st, msg in results:
        print('  [%-11s] %s   %s' % (st, pid, msg))
    landed = sum(1 for _, s, _ in results if s == 'LANDED')
    print('')
    print('landed: %d / %d   NOT-APPLIED: %d   unconfirmed: %d'
          % (landed, len(patches), not_applied, unconfirmed))
    print('=== result: %s ===' % ('FAIL' if not_applied else 'OK'))
    sys.exit(1 if not_applied else 0)


if __name__ == '__main__':
    main()
