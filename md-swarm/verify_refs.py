# -*- coding: utf-8 -*-
"""
verify_refs.py -- deterministic citation/cross-reference integrity check for md-swarm.

Compares a BASELINE manuscript (before the swarm edits) against the CURRENT one and
reports, with a nonzero exit on HARD violations:

  HARD (exit 2):
    - dropped citekeys    : a [@key] present in baseline but gone from current
    - undefined xrefs     : a [@fig:x] / [@tbl:x] / [@eq:x] in current with no matching
                            {#fig:x} / {#tbl:x} / {#eq:x} definition anywhere in current
  SOFT (exit 0, warn only):
    - split citation groups : a baseline group [@a; @b] no longer appears intact in current
    - dropped/added figure/table/equation DEFINITIONS ({#fig:}/{#tbl:}/{#eq:}) vs baseline.
      WARN-only (NOT like dropped citekeys): unlike [@cite] (default-keep), the author may
      legitimately delete/move a figure/table/equation during revision. This just makes the count
      change VISIBLE at every md-swarm batch -- so figures/tables/formulas ride the same delta
      report as citations, not only the md-build output check. (A dropped def that is still
      [@referenced] is already a HARD undefined-xref above.) Unlabeled figures/tables/math have no
      label to track here; md-build's verify_conservation (AST-based) counts ALL of them at output.
  INFO:
    - NEW placeholders      : [@NEW: ...] still awaiting a real Better BibTeX key

Input A -> output B, no LLM, no randomness. Run after every merge batch in md-swarm
Phase 2, and optionally as a md-build preflight.

Usage:
  py verify_refs.py --baseline <path-to-pre-edit manuscript.md> --current manuscript.md
  py verify_refs.py --current manuscript.md          # current-only checks (undefined xrefs, NEW)

Tip for --baseline: snapshot the pre-swarm manuscript, e.g.
  git show <pre-swarm-commit>:manuscript.md > build/_baseline.md
Use a baseline from the SAME citekey namespace (before any citekey reconciliation),
or dropped-citekey detection will false-positive.
"""
import argparse, glob, io, json, os, sys
from _citescan import scan as parse   # shared citekey/xref/group scanner (DRY + code-fence aware, see _citescan.py)

# Force UTF-8 I/O regardless of the Windows console code page (the whole point).
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def read_text(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def _defs_by_kind(defs):
    """Group a set of 'kind:label' definition strings into {kind: set(labels)} for fig/tbl/eq.
    (xref_defs already parses {#fig:}/{#tbl:}/{#eq:}/{#sec:}; we report the object kinds here.)"""
    out = {'fig': set(), 'tbl': set(), 'eq': set()}
    for d in defs:
        k, _, lab = d.partition(':')
        if k in out:
            out[k].add(lab)
    return out


def _authorized_drops(cs):
    """Citekeys a changeset DELIBERATELY removes -> these get downgraded HARD->WARN (T21-6).
    A drop is 'authorized' only when an intent=delete-citation/rewrite patch has the key in its
    `find` but not in its `replace`. intent=modify can NEVER authorize a drop (apply HARD-rejects
    that anyway), so default-keep protection on accidental drops is untouched."""
    out = set()
    for p in cs.get('patches', []) or []:
        if p.get('intent') in ('delete-citation', 'rewrite'):
            fk = set(parse(p.get('find', '') or '')['citekeys'])
            rk = set(parse(p.get('replace', '') or '')['citekeys'])
            out |= (fk - rk)
    return out


def _authorized_drops_from_files(paths):
    """Union deliberate drops from validated JSON patch/changeset files.

    Invalid archived authorization evidence is a hard input error: silently ignoring it would
    make a later batch forget an earlier human-approved deletion.
    """
    out = set()
    for path in paths:
        try:
            with open(path, encoding='utf-8') as f:
                obj = json.load(f)
        except Exception as e:
            raise ValueError('%s: %s' % (path, e))
        if not isinstance(obj, dict):
            raise ValueError('%s: top-level JSON must be an object' % path)
        out |= _authorized_drops(obj)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--current', required=True)
    ap.add_argument('--baseline', default=None)
    ap.add_argument('--changeset', default=None,
                     help='optional changeset.json; citekeys removed by delete-citation/rewrite '
                          'patches are downgraded from HARD to WARN (authorized deletion, T21-6)')
    ap.add_argument('--authorized-patches-dir', default=None,
                    help='optional archive directory (normally swarm/patches_applied); union '
                         'authorized drops from all prior *.json files with the current changeset')
    a = ap.parse_args()

    # set of citekeys the changeset authorized to drop (empty if no --changeset)
    authorized_drop = set()
    auth_files = []
    if a.changeset:
        if not os.path.exists(a.changeset):
            print('  (warn: --changeset not found, treating all drops as HARD:', a.changeset, ')')
        else:
            auth_files.append(a.changeset)
    if a.authorized_patches_dir:
        if os.path.isdir(a.authorized_patches_dir):
            auth_files.extend(sorted(glob.glob(os.path.join(a.authorized_patches_dir, '*.json'))))
        elif os.path.exists(a.authorized_patches_dir):
            print('ERROR: --authorized-patches-dir is not a directory:', a.authorized_patches_dir)
            sys.exit(2)
        # A missing archive is normal before the first batch; it contributes no prior grants.
    try:
        authorized_drop = _authorized_drops_from_files(auth_files)
    except ValueError as e:
        print('ERROR: invalid authorization evidence:', e)
        sys.exit(2)

    if not os.path.exists(a.current):
        print('ERROR: --current not found:', a.current); sys.exit(2)
    cur = parse(read_text(a.current))

    hard = 0
    print('=== verify_refs ===')
    print('current :', a.current)

    # ---- HARD: undefined cross-references (current-only) ----
    undefined = sorted(r for r in cur['xref_refs'] if r not in cur['xref_defs'])
    print('\n[xref] refs=%d defs=%d undefined=%d'
          % (len(cur['xref_refs']), len(cur['xref_defs']), len(undefined)))
    for r in undefined:
        print('  HARD undefined cross-reference: [@%s]  (no {#%s} definition)' % (r, r))
    hard += len(undefined)

    # ---- INFO: NEW placeholders ----
    new_uniq = sorted(set(cur['new_ph']))
    print('\n[new] NEW placeholders awaiting real key: %d' % len(new_uniq))
    for n in new_uniq:
        print('  INFO', n)

    # ---- INFO: figure/table/equation definition inventory (current) ----
    cur_obj = _defs_by_kind(cur['xref_defs'])
    print('\n[object] current labelled definitions: figure=%d table=%d equation=%d'
          % (len(cur_obj['fig']), len(cur_obj['tbl']), len(cur_obj['eq'])))

    if a.baseline:
        if not os.path.exists(a.baseline):
            print('ERROR: --baseline not found:', a.baseline); sys.exit(2)
        base = parse(read_text(a.baseline))
        print('\nbaseline:', a.baseline)

        # ---- dropped citekeys: HARD by default (引用默认不删), WARN if authorized (T21-6) ----
        base_keys = set(base['citekeys'])
        cur_keys = set(cur['citekeys'])
        dropped = sorted(base_keys - cur_keys)
        unauth = [k for k in dropped if k not in authorized_drop]
        auth = [k for k in dropped if k in authorized_drop]
        print('\n[cite] baseline keys=%d current keys=%d dropped=%d (authorized=%d, unauthorized=%d)'
              % (len(base_keys), len(cur_keys), len(dropped), len(auth), len(unauth)))
        for k in unauth:
            print('  HARD dropped citekey: [@%s]  (in baseline, gone from current)' % k)
        for k in auth:
            print('  WARN dropped citekey: [@%s]  (authorized by delete-citation/rewrite -- intended,'
                  ' not blocked)' % k)
        hard += len(unauth)   # authorized drops do NOT fail the gate

        # ---- SOFT: split citation groups ----
        cur_group_set = set(cur['groups'])
        split = [g for g in base['groups'] if len(g) >= 2 and g not in cur_group_set]
        # only count groups whose keys all still exist (otherwise it's a drop, already flagged)
        split = [g for g in split if g <= cur_keys]
        print('\n[group] baseline multi-key groups=%d split/altered=%d'
              % (len([g for g in base['groups'] if len(g) >= 2]), len(split)))
        for g in split:
            print('  WARN split/altered group: [@%s]' % '; @'.join(sorted(g)))

        # ---- WARN/INFO: figure/table/equation definitions dropped or added vs baseline ----
        # WARN-only (does NOT touch `hard`): a figure/table/equation may be legitimately removed or
        # added during revision. This surfaces the change so a SILENT drop during editing is visible,
        # giving figures/tables/formulas the same per-batch delta report citations get. A dropped def
        # that is still referenced is already flagged HARD as an undefined cross-reference above.
        base_obj = _defs_by_kind(base['xref_defs'])
        names = {'fig': 'figure', 'tbl': 'table', 'eq': 'equation'}
        for k in ('fig', 'tbl', 'eq'):
            dropped_d = sorted(base_obj[k] - cur_obj[k])
            added_d = sorted(cur_obj[k] - base_obj[k])
            print('\n[object] %s defs: baseline=%d current=%d (dropped %d, added %d)'
                  % (names[k], len(base_obj[k]), len(cur_obj[k]), len(dropped_d), len(added_d)))
            for lab in dropped_d:
                print('  WARN dropped %s definition: {#%s:%s}  (gone from current -- intended?'
                      ' not blocked)' % (names[k], k, lab))
            for lab in added_d:
                print('  INFO added %s definition: {#%s:%s}' % (names[k], k, lab))
    else:
        print('\n(no --baseline: skipped dropped-citekey, split-group, and object-delta checks)')

    print('\n=== result: %s (hard violations: %d) ==='
          % ('FAIL' if hard else 'OK', hard))
    sys.exit(2 if hard else 0)


def _selftest():
    """Regression self-test for the object-delta logic (handbook 6.5 rule (4))."""
    defs = {'fig:1', 'fig:2', 'tbl:1', 'eq:e1', 'sec:intro'}
    by = _defs_by_kind(defs)
    assert by['fig'] == {'1', '2'} and by['tbl'] == {'1'} and by['eq'] == {'e1'}, by
    # sec is intentionally not grouped as an object kind
    assert 'sec' not in by, by
    # delta semantics: dropped = baseline - current, added = current - baseline
    base = _defs_by_kind({'fig:1', 'fig:2'})
    cur = _defs_by_kind({'fig:1', 'fig:3'})
    assert sorted(base['fig'] - cur['fig']) == ['2'], 'dropped'
    assert sorted(cur['fig'] - base['fig']) == ['3'], 'added'
    # ---- T21-6: authorized-drop extraction ----
    cs = {'patches': [
        {'intent': 'delete-citation', 'find': 'x [@anon2020; @keep1] y', 'replace': 'x [@keep1] y'},
        {'intent': 'rewrite', 'find': 'p [@anon2022] q', 'replace': 'p q'},
        {'intent': 'modify', 'find': 'm [@notauth] n', 'replace': 'm n'},  # modify NEVER authorizes
    ]}
    ad = _authorized_drops(cs)
    assert ad == {'anon2020', 'anon2022'}, ad          # only delete-citation/rewrite drops
    assert 'notauth' not in ad, ad                     # modify intent does not authorize a drop
    assert 'keep1' not in ad, ad                       # kept keys are not "dropped"
    # P0 regression: prior-batch authorization archives accumulate with the current batch.
    import json, tempfile
    with tempfile.TemporaryDirectory(prefix='md_verify_refs_selftest_') as td:
        p1 = os.path.join(td, 'batch1.json')
        p2 = os.path.join(td, 'batch2.json')
        with open(p1, 'w', encoding='utf-8') as f:
            json.dump({'patches': [{'intent': 'delete-citation', 'find': '[@old1]', 'replace': ''}]}, f)
        with open(p2, 'w', encoding='utf-8') as f:
            json.dump({'patches': [{'intent': 'rewrite', 'find': '[@old2]', 'replace': ''}]}, f)
        assert _authorized_drops_from_files([p1, p2]) == {'old1', 'old2'}
        open(p2, 'w', encoding='utf-8').write('{broken')
        try:
            _authorized_drops_from_files([p1, p2])
            raise AssertionError('invalid archive must fail loudly')
        except ValueError:
            pass
    print('OK verify_refs self-test passed')


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        _selftest()
    else:
        main()
