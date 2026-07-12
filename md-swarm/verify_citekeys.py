# -*- coding: utf-8 -*-
"""
verify_citekeys.py -- catch FABRICATED citations (the other half of citation safety).

verify_refs.py guards "did the edit DROP a real citation?". This guards the opposite failure: did a
sub-agent INVENT one? A hallucinated [@smith2099] that matches nothing in your library used to sail
through collect -> apply -> verify_refs untouched and only surface as "not found" at build time (or
silently vanish in -Mode rebuild). This is the boundary check the dev handbook P1 asked for.

TWO LAYERS (agreed design):
  Layer 1 (always, offline, deterministic): a real [@key] in the manuscript is "known" if it is in the
    paper's own references.json OR in build/citemap.tsv (the provisional_citekey column -- which reconcile
    rewrites to real keys, so this covers both pre- and post-reconcile namespaces). Anything else is
    SUSPECT. ([@NEW:...] placeholders and @fig:/@tbl:/@eq:/@sec: cross-refs are excluded by _citescan.)
  Layer 2 (optional, only with --zotero AND a reachable Zotero+BBT): ask Zotero about each SUSPECT key.
    - in Zotero      -> it's a REAL reference you legitimately added during revision. Downgraded to a
                        note; it will resolve at build with -Mode live. NOT blocked.
    - NOT in Zotero  -> confirmed fabricated. HARD (exit 2).
    - couldn't ask   -> see below.

WHY suspect-without-Zotero is a WARN, not a HARD: offline we cannot tell "AI hallucination" from "a real
key you just added", and we must not block a legitimate addition. So Layer 1 alone only SURFACES suspects
(named, loudly) and exits 0; a HARD only comes from a Zotero-confirmed "no". This is exactly the
false-positive the author flagged.

Usage:
  py verify_citekeys.py --manuscript manuscript.md [--references references.json] [--citemap build/citemap.tsv] [--zotero]
Exit: 0 = clean OR only-unverifiable-suspects (with clear guidance); 2 = Zotero-confirmed fabricated key(s).
"""
import argparse
import io
import json
import os
import shutil
import sys
import tempfile

from _citescan import citekeys_in   # code-fence-aware, single source of truth (DRY)
from _zotero import tier_banner, probe, citekeys_exist

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def load_known(references_path, citemap_path):
    """Build the 'known citekey' set: references.json ids UNION citemap provisional_citekey column.
    Returns (known_set, sources_used_list)."""
    known = set()
    used = []
    if references_path and os.path.exists(references_path):
        try:
            refs = json.load(open(references_path, encoding='utf-8'))
            for it in refs if isinstance(refs, list) else []:
                cid = it.get('id') or it.get('citation-key')
                if isinstance(cid, str) and cid:
                    known.add(cid)
            used.append(os.path.basename(references_path))
        except Exception as e:
            print('WARN: could not read references.json (%s) -- skipping it as a known source.' % e)
    if citemap_path and os.path.exists(citemap_path):
        try:
            lines = open(citemap_path, encoding='utf-8').read().split('\n')
            if lines and lines[0].startswith('placeholder'):
                for ln in lines[1:]:
                    if not ln.strip():
                        continue
                    cols = ln.split('\t')
                    if len(cols) > 1 and cols[1].strip() and cols[1] != 'PARSE_ERROR':
                        known.add(cols[1].strip())
                used.append(os.path.basename(citemap_path))
        except Exception as e:
            print('WARN: could not read citemap.tsv (%s) -- skipping it as a known source.' % e)
    return known, used


def find_suspects(manuscript_text, known):
    """Return (sorted unique citekeys used, suspect keys not in `known`). Shared by main + selftest."""
    used_keys = sorted(set(citekeys_in(manuscript_text)))
    suspect = [k for k in used_keys if k not in known]
    return used_keys, suspect


def classify_readiness(total, resolved):
    """Pure verdict for the pre-live key-lock test (testable without Zotero).
    'provisional' = NOTHING resolves and the sample is big enough to rule out a couple of typos --
    the md-unpack path-(A) signature (whole manuscript still in the temporary authorYear namespace,
    reconcile never ran); -Mode live would emit a docx full of 'not found' (real incident 2026-07-09,
    dev handbook 10 P1-5)."""
    if total == 0:
        return 'no-keys'
    if resolved == 0 and total >= 3:
        return 'provisional'
    if resolved < total:
        return 'partial'
    return 'ready'


def live_readiness(manuscript_path):
    """Key-lock test before -Mode live: do the manuscript's citekeys actually resolve in Better
    BibTeX? Connectivity alone is NOT enough (the label can lie; asking is the only reliable judge).
    Exit 2 only on the unambiguous 'provisional namespace' verdict; every unreachable/inconclusive
    path degrades to proceed (build.ps1's own connectivity probe governs those)."""
    keys = sorted(set(citekeys_in(open(manuscript_path, encoding='utf-8').read())))
    print('=== live-readiness (key-lock test before -Mode live) ===')
    print('manuscript: %s | unique citekeys: %d' % (manuscript_path, len(keys)))
    if not keys:
        print('no citekeys -> nothing to resolve; proceed.')
        return 0
    state, detail = probe()
    if state != 'ready':
        print('SKIP: Zotero/BBT not reachable (%s) -- the build script own probe governs.' % detail)
        return 0
    known_z, unknown_z, consulted = citekeys_exist(keys)
    if not consulted:
        print('SKIP: BBT lookup inconclusive -- proceeding; live itself will surface failures.')
        return 0
    verdict = classify_readiness(len(keys), len(known_z))
    print('resolved in Better BibTeX: %d / %d  -> %s' % (len(known_z), len(keys), verdict.upper()))
    if verdict == 'provisional':
        print('')
        print('[!] NONE of the citekeys resolve: this manuscript is still in the TEMPORARY authorYear')
        print('    namespace (md-unpack option (A) -- reconcile never ran). -Mode live would produce a')
        print('    docx where EVERY citation is "not found".')
        print('    Fix (pick one):')
        print('      1. reconcile then live:  py <md-unpack>/reconcile_live.py --citemap build/citemap.tsv --manuscript manuscript.md')
        print('      2. no new refs needed:   use -Mode rebuild (offline bridge for the original citations)')
        return 2
    if verdict == 'partial':
        for k in sorted(unknown_z):
            print('   not-in-BBT  [@%s]' % k)
        print('these will come out as "not found" in live (typo / leftover [@NEW:] / not yet in Zotero).')
    else:
        print('READY: every citekey resolves.')
    return 0


def selftest():
    """Offline Layer-1 regression (handbook 6.5 rule (4): one bug = one test). No Zotero, no real
    manuscript. Covers the fiddly part -- building the 'known' set from references.json + citemap.tsv,
    then flagging a fabricated key while sparing real / [@NEW:] / xref tokens."""
    tmp = tempfile.mkdtemp(prefix='verify_citekeys_')
    fails = 0

    def check(cond, msg):
        nonlocal fails
        print(('[OK]   ' if cond else '[FAIL] ') + msg)
        if not cond:
            fails += 1

    try:
        refs = os.path.join(tmp, 'references.json')
        cmap = os.path.join(tmp, 'citemap.tsv')
        with open(refs, 'w', encoding='utf-8') as f:
            json.dump([{'id': 'moynihan2015'}, {'citation-key': 'herd2018'}], f)
        with open(cmap, 'w', encoding='utf-8') as f:
            f.write('placeholder\tprovisional_citekey\tzotero_item_key\n'
                    '[CITE-1]\tolsen2019\tABC123\n'
                    '[CITE-2]\tPARSE_ERROR\t\n')

        known, used = load_known(refs, cmap)
        check('moynihan2015' in known, "references.json 'id' -> known")
        check('herd2018' in known, "references.json 'citation-key' -> known")
        check('olsen2019' in known, 'citemap provisional_citekey column -> known')
        check('PARSE_ERROR' not in known, 'citemap PARSE_ERROR row ignored')
        check(len(used) == 2, 'both known-sources counted')

        md = ('Real [@moynihan2015], fabricated [@smith2099]; new [@NEW: Foo 2024]; '
              'xref [@fig:1]; group [@herd2018; @olsen2019].')
        used_keys, suspect = find_suspects(md, known)
        check(suspect == ['smith2099'], 'only the fabricated key is suspect (real/group keys spared)')
        check('moynihan2015' not in suspect, 'known key -> not suspect')
        check(all(':' not in k for k in used_keys) and all(not k.upper().startswith('NEW') for k in used_keys),
              '[@NEW:] and [@fig:] excluded from citekeys')

        _, used_none = load_known(os.path.join(tmp, 'nope.json'), os.path.join(tmp, 'nope.tsv'))
        check(used_none == [], 'missing sources -> no known-source (the skip/exit-0 path)')

        # live-readiness verdicts (pure part of the pre-live key-lock test)
        check(classify_readiness(0, 0) == 'no-keys', 'readiness: no keys')
        check(classify_readiness(56, 0) == 'provisional', 'readiness: 0/56 -> provisional (the real-incident shape)')
        check(classify_readiness(2, 0) == 'partial', 'readiness: 0/2 too small to call provisional (could be typos)')
        check(classify_readiness(56, 53) == 'partial', 'readiness: 53/56 -> partial (typos/NEW leftovers named)')
        check(classify_readiness(56, 56) == 'ready', 'readiness: all resolve')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('=== verify_citekeys self-test:', 'ALL PASSED' if fails == 0 else '%d FAILED' % fails, '===')
    return 0 if fails == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--manuscript', default=None)
    ap.add_argument('--references', default=None, help='default: references.json next to the manuscript')
    ap.add_argument('--citemap', default=None, help='default: build/citemap.tsv next to the manuscript')
    ap.add_argument('--zotero', action='store_true',
                    help='enable Layer 2: ask a running Zotero/BBT whether suspect keys are real')
    ap.add_argument('--selftest', action='store_true',
                    help='run the offline Layer-1 regression test (no Zotero, no manuscript needed)')
    ap.add_argument('--live-readiness', action='store_true',
                    help='pre-live key-lock test: do the manuscript citekeys resolve in Better BibTeX? '
                         'exit 2 = provisional authorYear namespace (run reconcile first / use rebuild)')
    a = ap.parse_args()

    if a.selftest:
        sys.exit(selftest())

    if not a.manuscript:
        ap.error('--manuscript is required (or use --selftest)')
    if not os.path.exists(a.manuscript):
        print('FATAL: manuscript not found:', a.manuscript); sys.exit(2)

    if a.live_readiness:
        sys.exit(live_readiness(a.manuscript))
    wd = os.path.dirname(os.path.abspath(a.manuscript))
    references = a.references or os.path.join(wd, 'references.json')
    citemap = a.citemap or os.path.join(wd, 'build', 'citemap.tsv')

    # pre-flight tier banner (doubles as the "what can I do right now" reminder)
    print(tier_banner())
    print('')
    print('=== verify_citekeys (fabricated-citation guard) ===')
    print('manuscript:', a.manuscript)

    known, used = load_known(references, citemap)
    if not used:
        print('\n(no references.json or citemap.tsv found -- cannot judge which citekeys are "known".')
        print(' Run md-unpack first, or pass --references/--citemap. Skipping, exit 0.)')
        sys.exit(0)
    print('known-citation sources:', ', '.join(used), '(%d keys)' % len(known))

    used_keys, suspect = find_suspects(open(a.manuscript, encoding='utf-8').read(), known)
    print('citekeys in manuscript: %d   suspect (not in known sources): %d' % (len(used_keys), len(suspect)))

    if not suspect:
        print('\n=== result: OK (every citekey is accounted for) ===')
        sys.exit(0)

    # ---- there are suspects ----
    if a.zotero:
        state, detail = probe()
        if state == 'ready':
            known_z, unknown_z, consulted = citekeys_exist(suspect)
            if consulted:
                if known_z:
                    print('\n[Layer 2] %d suspect key(s) ARE in your Zotero -- look like references you'
                          ' legitimately added; they will resolve at build with -Mode live:' % len(known_z))
                    for k in sorted(known_z):
                        print('   note  [@%s]' % k)
                if unknown_z:
                    print('\n[Layer 2] %d key(s) are in NEITHER your references NOR your Zotero library'
                          ' -- these look FABRICATED (hallucinated). Fix or remove them:' % len(unknown_z))
                    for k in sorted(unknown_z):
                        print('   HARD  [@%s]' % k)
                    print('\n=== result: FAIL (%d Zotero-confirmed fabricated citekey(s)) ===' % len(unknown_z))
                    sys.exit(2)
                print('\n=== result: OK (suspects are real, just newly added) ===')
                sys.exit(0)
            # consulted == False: Zotero up but the lookup couldn't be trusted -> fall through to WARN
            print('\n[Layer 2] Zotero answered but the citekey lookup was inconclusive (BBT API shape?).'
                  ' Treating suspects as UN-verified -- see below. (Smoke-test: python _zotero.py --check <key>.)')
        else:
            why = 'Better BibTeX not answering' if state == 'no-bbt' else 'Zotero not reachable'
            print('\n[Layer 2] requested (--zotero) but skipped: %s (%s).' % (why, detail))

    # ---- WARN path: suspects we could NOT confirm against Zotero (offline, no --zotero, or inconclusive) ----
    print('\n[!] %d citekey(s) are NOT in this paper\'s known citations and could NOT be verified'
          ' against Zotero:' % len(suspect))
    for k in suspect:
        print('    [@%s]' % k)
    print('  WHY un-verified: ' + ('Zotero/BBT not reachable' if a.zotero else 'Layer 2 not enabled (no --zotero)')
          + '.')
    print('  These are EITHER references you legitimately added (real in your Zotero) OR fabricated by an'
          ' AI. Offline we cannot tell which, so this is a WARNING, not a hard block.')
    print('  To auto-classify: open Zotero (with Better BibTeX) and re-run with --zotero.')
    print('  Otherwise: eyeball the list above -- a real new ref should be written [@NEW: description]'
          ' (and inserted via Zotero), a fabricated one should be removed.')
    print('\n=== result: OK-with-warnings (%d unverified suspect(s); nothing blocked) ===' % len(suspect))
    sys.exit(0)


if __name__ == '__main__':
    main()
