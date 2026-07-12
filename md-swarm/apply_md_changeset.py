# -*- coding: utf-8 -*-
"""
apply_md_changeset.py -- deterministic SINGLE-WRITER apply for md-swarm Phase 2.

The whole point: sub-agents NEVER write manuscript.md. They return patches; the
main controller accumulates them into swarm/changeset.json; THIS script is the
ONLY thing that writes manuscript.md. It applies patches in order, validating
each against the running text, so an earlier patch that eats a later patch's
anchor is caught (the "Q8 sequential" check). No model in the write loop -> no
lost updates, no "forgot to apply agent D/E", no concurrent-write race.

  Input A (changeset.json + manuscript.md) -> output B (rewritten manuscript.md).
  No LLM, no randomness. Run it instead of having the model Edit manuscript.md.

CITATION SAFETY (the md-swarm citation-loss fix):
  Unless a patch is explicitly authorized to change citations, any [@citekey]
  present in `find` but absent from `replace` is a HARD violation and the apply
  refuses to write. A patch is authorized only when:
      mode   == "replace-section"            (whole-section rewrite), OR
      intent in {"rewrite","delete-citation"} (explicit restructure/removal)
  So a plain reword can never silently drop a reference -- you must opt in.
  NOTE on "replace-section": it does NOT locate by heading. `find` is STILL matched
  verbatim (work.replace(find, repl, 1)) and must be the whole old subsection copied
  character-for-character from manuscript.md; the mode flag only SWITCHES OFF the
  drop-a-citation guard for that patch. It never auto-selects a range by `##` title.

changeset.json shape (UTF-8, no BOM):
{
  "source_md": "manuscript.md",          # path; abs, or relative to the changeset file
  "patches": [
    { "id": "机改-1",                     # id starting with 机改 = agent batch (the gate hook keys on this)
      "target": "## INTRODUCTION",        # human label, not used for matching
      "mode": "patch",                    # "patch" (minimal fragment, default) | "replace-section"
                                          #   (replace-section = "allow dropping citations here"; find is STILL
                                          #    a verbatim whole-section copy, NOT auto-located by heading)
      "intent": "modify",                 # "modify"(default) | "rewrite" | "delete-citation" | "add-citation"
                                          #   | "replace-all" (swap this exact find at EVERY occurrence; or set "all":true)
      "find": "<exact slice copied verbatim from manuscript.md>",
      "replace": "<the new text>" }
  ]
}

Usage:
  py apply_md_changeset.py --changeset swarm\\changeset.json --manuscript manuscript.md
  py apply_md_changeset.py --changeset ... --dry-run     # check + report only, write nothing
  py apply_md_changeset.py --changeset ... --force       # apply the clean patches, skip+report the bad ones

Exit: 0 = applied (or --dry-run clean); 1 = hard violations (nothing written unless --force); 2 = fatal.
"""
import argparse, io, json, os, re, sys
from collections import Counter
from _citescan import citekeys_in   # single source of truth for the [@citekey] rule (DRY, see _citescan.py)

# Force UTF-8 I/O regardless of the Windows console code page (same trick as verify_refs.py).
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# invisible-whitespace fold: Word/Zotero leave non-breaking / thin spaces (U+00A0,
# U+202F, ...) that look identical to a normal space but break exact substring
# matching. md-unpack now strips these at the source; we ALSO fold them here so a
# stray invisible char (a re-paste, a hand edit, a different source) can never
# HARD-fail an otherwise-correct patch. Applied to BOTH the manuscript and every
# find/replace, so matching and the written result stay consistent. [2026-06 fix]
_WS_TO_SPACE = tuple(chr(o) for o in (0x00a0, 0x2007, 0x2009, 0x202f))
_WS_DROP = tuple(chr(o) for o in (0x200b, 0x200c, 0x200d, 0x2060, 0xfeff, 0x00ad))


def norm_ws(s):
    if s is None:
        return s
    for c in _WS_TO_SPACE:
        s = s.replace(c, ' ')
    for c in _WS_DROP:
        s = s.replace(c, '')
    return s


def read_text(path):
    # newline='' so Python does not translate the file's own \n / \r\n on the way in or out.
    with open(path, encoding='utf-8', newline='') as f:
        return f.read()


def write_text(path, text):
    # Atomic write: write a temp file in the SAME directory, fsync it, then os.replace() over
    # the target. os.replace is atomic on a single filesystem (incl. Windows), so a crash
    # mid-write (process killed / power loss / disk full) leaves EITHER the old file OR the new
    # file fully intact -- never a half-written manuscript.md. newline='' keeps the LF the
    # pipeline relies on. [handbook section 10, P2: robustness for the single source of truth]
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='') as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--changeset', required=True)
    ap.add_argument('--manuscript', default=None,
                    help='override source_md from the changeset')
    ap.add_argument('--dry-run', action='store_true',
                    help='validate + report only, write nothing')
    ap.add_argument('--force', action='store_true',
                    help='apply the clean patches even if some are hard violations (bad ones are skipped + listed)')
    ap.add_argument('--allow-no-hooks', action='store_true',
                    help='proceed even if the md-* protection hooks are down (layer-1 guards still '
                         'apply); see dev-manual section 7.6')
    a = ap.parse_args()

    # --- preflight gate (dev-manual section 7.6): the protection hooks are only a SECOND layer
    # and get silently wiped by cc-switch; refuse to write the protected source while they are
    # down (override with --allow-no-hooks; --dry-run writes nothing so it never blocks). This
    # ONLY gates entry -- it does not touch any of the safety logic below. Degrades to a warning
    # if preflight.py is unavailable, since apply itself is the load-bearing layer-1.
    try:
        import preflight as _pf
        _code = _pf.gate(mode='block', context='md-swarm apply',
                         allow_no_hooks=a.allow_no_hooks, dry=a.dry_run)
        if _code != 0:
            sys.exit(_code)
    except SystemExit:
        raise
    except Exception as _e:
        print('[preflight] check skipped (' + str(_e) + ') -- proceeding; layer-1 guards still apply.')

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

    patches = cs.get('patches') or []
    raw_original = read_text(man)
    # newline normalization (CRLF/LF): md-unpack writes CRLF manuscripts, but AI-authored patch
    # find/replace are LF. Exact substring match would false-reject every multi-line `find`. Normalize
    # both to LF for matching, and write the result back as LF (md/pandoc are EOL-agnostic). [2026-06-21]
    original = norm_ws(raw_original.replace('\r\n', '\n'))   # also fold invisible ws (NBSP etc.) for robust matching
    work = original          # progressively-modified working copy (drives the sequential check)

    results = []             # (id, status, message)
    hard = 0

    for i, p in enumerate(patches):
        pid = str(p.get('id') or ('patch-%d' % (i + 1)))
        mode = str(p.get('mode') or 'patch').lower()
        intent = str(p.get('intent') or 'modify').lower()
        find = p.get('find')
        repl = p.get('replace')
        if find is not None:
            find = norm_ws(find.replace('\r\n', '\n'))
        if repl is not None:
            repl = norm_ws(repl.replace('\r\n', '\n'))

        if find is None or repl is None:
            results.append((pid, 'HARD', 'missing "find" or "replace"')); hard += 1; continue
        if find == '':
            results.append((pid, 'HARD', '"find" is empty (cannot locate)')); hard += 1; continue

        # opt-in global swap: a deliberate "replace this exact string EVERYWHERE" (e.g. a term
        # rename national->state capacity across 21 sites). Strictly opt-in so the uniqueness
        # gate -- the thing that stops an ambiguous find from hitting the wrong place -- stays on
        # for every normal patch. The citation guard below still runs, so replace-all can't drop
        # a [@key] without authorization either.
        all_mode = (intent == 'replace-all') or bool(p.get('all'))
        n = work.count(find)
        if n == 0:
            results.append((pid, 'HARD',
                            'find not present in current text (mis-copied, or an earlier patch already consumed it)'))
            hard += 1; continue
        if n > 1 and not all_mode:
            results.append((pid, 'HARD',
                            'find occurs %d times -- not unique; enlarge the fragment to make it unique '
                            '(or set intent="replace-all" / "all":true if you really mean every occurrence)' % n))
            hard += 1; continue

        # --- citation safety: a plain patch may not drop a [@citekey] ---
        fk, rk = citekeys_in(find), citekeys_in(repl)
        dropped = sorted((Counter(fk) - Counter(rk)).keys())
        added = sorted((Counter(rk) - Counter(fk)).keys())
        cite_change_ok = (mode == 'replace-section') or (intent in ('rewrite', 'delete-citation'))
        if dropped and not cite_change_ok:
            results.append((pid, 'HARD',
                            'drops citation(s) %s without authorization. Keep the [@key] in "replace", '
                            'or (only if the revision comment itself asks to restructure/remove) set '
                            'mode=replace-section or intent=rewrite/delete-citation.'
                            % '; '.join('@' + k for k in dropped)))
            hard += 1; continue

        work = work.replace(find, repl) if all_mode else work.replace(find, repl, 1)
        tag = '%s/%s' % (mode, intent)
        if all_mode and n > 1:
            tag += '  x%d (replace-all)' % n
        if dropped:
            tag += '  dropped[%s]' % '; '.join('@' + k for k in dropped)
        if added:
            tag += '  added[%s]' % '; '.join('@' + k for k in added)
        results.append((pid, 'OK', tag))

    # --- report ---
    print('=== apply_md_changeset ===')
    print('changeset :', a.changeset)
    print('manuscript:', man)
    print('patches   :', len(patches))
    print('')
    for pid, st, msg in results:
        print('  [%-4s] %s   %s' % (st, pid, msg))
    ok = sum(1 for _, s, _ in results if s == 'OK')
    print('')
    print('clean: %d / %d   hard violations: %d' % (ok, len(patches), hard))

    if a.dry_run:
        print('\n(--dry-run: manuscript.md NOT written)')
        print('=== result: %s ===' % ('FAIL' if hard else 'OK'))
        sys.exit(1 if hard else 0)

    if hard and not a.force:
        print('\nFAIL: hard violations present -- manuscript.md NOT written.')
        print('Fix the offending patches in the changeset and re-run (or --force to apply the clean ones).')
        sys.exit(1)

    if hard and a.force:
        print('\nWARN: --force -- writing with %d hard violation(s) SKIPPED (see above); only the clean patches landed.' % hard)

    # back up the pre-apply state (undo-last-apply convenience; swarm/_baseline.md remains the pre-swarm anchor)
    write_text(man + '.applybak', raw_original)
    write_text(man, work)
    print('\nOK -> wrote %s  (%d patch(es) applied; pre-apply backup: %s.applybak)' % (man, ok, man))
    print('=== result: %s ===' % ('OK' if not hard else 'OK-with-skips'))
    sys.exit(0)


if __name__ == '__main__':
    main()
