# -*- coding: utf-8 -*-
"""
collect_patches.py -- deterministically gather per-comment patch files written by parallel
sub-agents (swarm/patches/*.json) into ONE changeset.json for apply_md_changeset.py.
No LLM in the merge loop. Order patches by where each `find` sits in the manuscript
(top-to-bottom) so apply's sequential check is natural.

Each input file (one per comment):
  { "id":"<jigai>-R1-C3", "target":"## INTRODUCTION",
    "patches":[ {"mode":..,"intent":..,"find":..,"replace":..}, ... ],
    "new_citations":[...], "new_objects":[...], "notes":"..." }
Output changeset.json:
  { "source_md":"manuscript.md", "patches":[ {"id","target","mode","intent","find","replace"}, ... ] }

Usage:
  py collect_patches.py --patches-dir swarm\\patches --manuscript manuscript.md --out swarm\\changeset.json
Exit: 0 ok; 2 fatal (including any malformed/empty patch file).
"""
import argparse, glob, io, json, os, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def _pick(d, *names):
    """First present-and-non-None value among aliases. Weak models sometimes invent their own
    field names (old_string/new_string/action) instead of the contract's find/replace; map them
    back so a mis-labelled-but-otherwise-valid patch isn't silently dropped. [2026-06 fix]"""
    for n in names:
        if isinstance(d, dict) and d.get(n) is not None:
            return d.get(n)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--patches-dir', required=True)
    ap.add_argument('--manuscript', default='manuscript.md')
    ap.add_argument('--out', default=None)
    ap.add_argument('--skip-stale', action='store_true',
                    help='skip patch files older than manuscript.md (leftover from a previous batch)')
    a = ap.parse_args()

    if not os.path.isdir(a.patches_dir):
        print('FATAL: patches dir not found:', a.patches_dir); sys.exit(2)
    files = sorted(glob.glob(os.path.join(a.patches_dir, '*.json')))
    if not files:
        print('FATAL: no *.json in', a.patches_dir); sys.exit(2)

    man_text = ''
    man_mtime = 0
    if os.path.exists(a.manuscript):
        man_mtime = os.path.getmtime(a.manuscript)
        with open(a.manuscript, encoding='utf-8', newline='') as f:
            man_text = f.read().replace('\r\n', '\n')   # normalize for position-finding (patch find is LF)

    collected = []   # (sort_pos, tiebreak, patch)
    stale = []       # explicitly tolerated only with --skip-stale
    invalid = []     # malformed/empty agent output: fail the whole collection loudly
    aliased = []     # files whose patches used non-standard field names (auto-mapped)
    for fp in files:
        if a.skip_stale and man_mtime and os.path.getmtime(fp) < man_mtime:
            stale.append((os.path.basename(fp), 'stale (older than manuscript.md) -- skipped')); continue
        try:
            # strict=False tolerates raw control chars inside JSON strings -- a common weak-model
            # output flaw that used to make the whole file unparseable and silently dropped.
            with open(fp, encoding='utf-8') as fh:
                obj = json.load(fh, strict=False)
        except Exception as e:
            invalid.append((os.path.basename(fp), 'parse error (not valid JSON even leniently): %s' % e)); continue
        fid = str(_pick(obj, 'id') or os.path.splitext(os.path.basename(fp))[0])
        plist = _pick(obj, 'patches', 'edits', 'changes') or []
        if not plist:
            invalid.append((os.path.basename(fp), 'no patches')); continue
        for i, p in enumerate(plist):
            find = _pick(p, 'find', 'old_string', 'old', 'search', 'from') or ''
            repl = _pick(p, 'replace', 'new_string', 'new', 'replacement', 'to')
            if isinstance(p, dict) and p.get('find') is None and find:   # came in under an alias
                if os.path.basename(fp) not in aliased:
                    aliased.append(os.path.basename(fp))
            pid = fid if len(plist) == 1 else ('%s#%d' % (fid, i + 1))
            pos = man_text.find(find.replace('\r\n', '\n')) if find else -1
            if pos < 0:
                pos = 10 ** 12  # not located -> push to end, keep file order
            collected.append((pos, len(collected), {
                'id': pid,
                'target': _pick(obj, 'target') or '',
                'mode': (p.get('mode') if isinstance(p, dict) else None) or 'patch',
                'intent': _pick(p, 'intent', 'action') or 'modify',
                'find': find,
                'replace': repl if repl is not None else '',
            }))

    collected.sort(key=lambda t: (t[0], t[1]))
    patches = [t[2] for t in collected]

    print('=== collect_patches ===')
    print('files  :', len(files), ' patches:', len(patches))
    for nm, why in stale:
        print('  SKIPPED', nm, '-', why)
    for nm, why in invalid:
        print('  FATAL  ', nm, '-', why)
    for nm in aliased:
        print('  NOTE   ', nm, '- non-standard field names (old_string/new_string/action/...) auto-mapped to find/replace/intent')
    if invalid:
        print('FATAL: refusing to write a partial changeset; fix every invalid patch file and rerun.')
        sys.exit(2)
    if not patches:
        print('FATAL: no usable patches remain after collection.')
        sys.exit(2)

    out = a.out or os.path.join(os.path.dirname(a.patches_dir) or '.', 'changeset.json')
    with open(out, 'w', encoding='utf-8', newline='') as f:
        json.dump({'source_md': os.path.basename(a.manuscript), 'patches': patches},
                  f, ensure_ascii=False, indent=1)

    print('-> wrote', out)
    sys.exit(0)


def _selftest():
    """P0 regression: malformed agent output must not yield exit 0 or a partial changeset."""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory(prefix='md_collect_selftest_') as td:
        man = os.path.join(td, 'manuscript.md')
        patch_dir = os.path.join(td, 'patches')
        out = os.path.join(td, 'changeset.json')
        os.makedirs(patch_dir)
        open(man, 'w', encoding='utf-8').write('alpha\n')
        open(os.path.join(patch_dir, 'bad.json'), 'w', encoding='utf-8').write('{broken')
        p = subprocess.run(
            [sys.executable, os.path.abspath(__file__), '--patches-dir', patch_dir,
             '--manuscript', man, '--out', out],
            capture_output=True, text=True, encoding='utf-8', errors='replace')
        assert p.returncode == 2, (p.returncode, p.stdout, p.stderr)
        assert not os.path.exists(out), 'partial changeset must not be written'

        os.unlink(os.path.join(patch_dir, 'bad.json'))
        good = {'id': 'p1', 'patches': [{'find': 'alpha', 'replace': 'beta'}]}
        open(os.path.join(patch_dir, 'good.json'), 'w', encoding='utf-8').write(
            json.dumps(good, ensure_ascii=False))
        p = subprocess.run(
            [sys.executable, os.path.abspath(__file__), '--patches-dir', patch_dir,
             '--manuscript', man, '--out', out],
            capture_output=True, text=True, encoding='utf-8', errors='replace')
        assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
        assert len(json.load(open(out, encoding='utf-8'))['patches']) == 1
    print('OK collect_patches self-test passed')


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        _selftest()
    else:
        main()
