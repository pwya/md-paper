# -*- coding: utf-8 -*-
"""
preflight.py -- shared environment gate for the md-* suite (dev-manual section 7.6).

WHY THIS EXISTS
  The two md-swarm protection hooks (md_protect_hook, md_swarm_gate_hook) are
  fail-open AND get silently wiped: cc-switch overwrites ~/.claude/settings.json
  from a provider template on every switch/startup, and a template without a
  "hooks" key blanks the registration -- you then run with no second-layer
  protection and nothing warns you. The hooks are only the SECOND layer
  (hardening). The load-bearing safety is script-level: apply_md_changeset.py is
  the single writer with citation/uniqueness/order guards, and sub-agents are
  read-only by contract -- those hold even if the hooks are 100% gone. This module
  makes "the second layer is down" LOUD instead of silent.

DESIGN (locked 2026-06-28, see dev-manual section 7.6)
  - Single source of truth (DRY): the check lives here once; apply imports gate().
  - Install-method agnostic: it checks the hook .ps1 files sitting NEXT TO this
    file, so it works whether the skill was installed via a junction OR a plain
    directory copy. It does NOT check for a junction (a public user may have no
    junction; checking one would false-alarm a perfectly fine install).
  - Registration check reuses verify_hooks.ps1's approach (raw substring match on
    settings.json) -- robust against the deeply-escaped permission strings that
    choke a strict JSON parse.
  - Call points by risk: md-swarm apply + md-unpack unpack.ps1 -> HARD BLOCK;
    md-build + md-triage -> WARN.
  - Loud self-heal: if registration is gone, re-run setup_hooks.ps1 to restore it,
    then HARD-STOP (never pretend it is fixed: the running session still holds the
    OLD hook snapshot; only a NEW session picks up the re-registration).

HONEST LIMIT
  This verifies REGISTRATION (which is exactly what cc-switch wipes). It canNOT
  verify that the CURRENT session is actually firing the hooks (the harness
  snapshots hooks at session start). A stale snapshot from before the last hook
  change can still pass here. The live check is the current-session ritual in
  probe_live_hooks.py: prepare a sacrificial project, then really attempt the
  printed Write probe and shell apply probe through the assistant tools. Both
  must be DENIED. See dev-manual section 7.6.

USAGE
  py preflight.py --mode block [--allow-no-hooks] [--no-heal]   # apply / md-unpack
  py preflight.py --mode warn                                   # md-build / md-triage
  py preflight.py --selftest                                    # unit checks, exit 0/1
  py probe_live_hooks.py --prepare                              # current-session live check
EXIT
  0 = env OK, or warn-mode, or dry-run, or --allow-no-hooks (proceed)
  3 = hard block (protection hooks down, no override)
"""
import argparse, os, sys, subprocess

# NOTE: all output here is deliberately ASCII-only, so it prints correctly on ANY Windows code
# page without a TextIOWrapper hack. That also matters because apply_md_changeset.py imports this
# module AFTER it has already rebound sys.stdout to its own UTF-8 wrapper -- if we re-wrapped
# sys.stdout at import time we would orphan apply's wrapper and close the shared buffer
# ("I/O operation on closed file"). So: no stdout rebinding here, ASCII messages only.

# The two PROTECTION hooks (the non-blocking dev-checklist hook is NOT required here).
HOOK_NAMES = ('md_protect_hook', 'md_swarm_gate_hook')
HOOK_FILES = ('md_protect_hook.ps1', 'md_swarm_gate_hook.ps1')

RESTART_HELP = (
    "  RESTART REQUIRED (the running session still uses the OLD hook snapshot):\n"
    "    - VS Code: open a BRAND-NEW Claude conversation. NOT reopen the panel\n"
    "      (a reopen may restore the old session = old snapshot). /clear does NOT\n"
    "      reload hooks.\n"
    "    - CLI: quit the current `claude` process, start a new one.\n"
    "    - Confirm: in the new session run verify_hooks.ps1 -> expect 6/6 green.\n"
    "    - Full guide: the 'restart Claude Code' section of the new-machine setup guide.\n"
)


def settings_path():
    return os.path.join(os.path.expanduser('~'), '.claude', 'settings.json')


def evaluate(hooks_dir=None, settings_file=None):
    """Pure, side-effect-free environment check. Never exits, never heals.

    Returns a dict:
      registered      : both protection hooks named in settings.json (raw substring,
                        same approach as verify_hooks.ps1 -- avoids JSON-parse fragility).
      scripts_present : both hook .ps1 files exist next to this module (install-agnostic).
      ok              : registered and scripts_present.
    """
    hooks_dir = hooks_dir or os.path.dirname(os.path.abspath(__file__))
    sp = settings_file or settings_path()

    text = ''
    if os.path.exists(sp):
        try:
            with open(sp, encoding='utf-8') as f:
                text = f.read()
        except Exception:
            text = ''
    registered = all(name in text for name in HOOK_NAMES)

    missing_files = [fn for fn in HOOK_FILES
                     if not os.path.exists(os.path.join(hooks_dir, fn))]
    scripts_present = not missing_files

    missing = []
    if not registered:
        missing.append('hook registration in ' + sp
                       + ' (need both of: ' + ', '.join(HOOK_NAMES) + ')')
    for fn in missing_files:
        missing.append('hook script file: ' + os.path.join(hooks_dir, fn))

    return {
        'ok': registered and scripts_present,
        'registered': registered,
        'scripts_present': scripts_present,
        'settings_path': sp,
        'settings_exists': os.path.exists(sp),
        'hooks_dir': hooks_dir,
        'missing': missing,
    }


def _find_setup_hooks(hooks_dir):
    # setup_hooks.ps1 lives in md-unpack/ (the registrar's home since 2026-06-28). hooks_dir
    # is md-swarm/, so md-unpack is a sibling: ../md-unpack/setup_hooks.ps1. Also try the
    # legacy spots (suite root, next to the hooks) for older layouts. First hit wins.
    for c in (os.path.join(hooks_dir, '..', 'md-unpack', 'setup_hooks.ps1'),  # current home
              os.path.join(hooks_dir, '..', 'setup_hooks.ps1'),               # legacy: suite root
              os.path.join(hooks_dir, 'setup_hooks.ps1')):                    # legacy: next to hooks
        c = os.path.abspath(c)
        if os.path.exists(c):
            return c
    return None


def self_heal(hooks_dir):
    """LOUD self-heal: re-run setup_hooks.ps1 to re-register. Returns True if it ran clean.
    The caller HARD-STOPS afterwards -- re-registration does NOT help the current session
    (old snapshot); only a new session picks it up."""
    sh = _find_setup_hooks(hooks_dir)
    if not sh:
        print('  [heal] setup_hooks.ps1 not found -- cannot auto re-register.')
        print('         Run it yourself: powershell -ExecutionPolicy Bypass -File <suite>/setup_hooks.ps1')
        return False
    print('  [heal] re-registering hooks via: ' + sh)
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', sh],
                           capture_output=True, text=True)
        if r.stdout:
            sys.stdout.write(r.stdout)
        if r.returncode != 0:
            if r.stderr:
                sys.stdout.write(r.stderr)
            print('  [heal] setup_hooks.ps1 exited ' + str(r.returncode) + ' -- see output above.')
            return False
        print('  [heal] re-registration done.')
        return True
    except Exception as e:
        print('  [heal] could not run setup_hooks.ps1: ' + str(e))
        return False


def print_status(res):
    print('=== md-* preflight: protection-hook environment ===')
    print('  settings.json   : ' + res['settings_path']
          + ('' if res['settings_exists'] else '  (MISSING)'))
    print('  registered      : ' + ('OK' if res['registered'] else 'NO'))
    print('  hook scripts    : ' + ('OK' if res['scripts_present'] else 'NO'))
    for m in res['missing']:
        print('    - missing: ' + m)


def gate(mode='block', context='', allow_no_hooks=False, dry=False, no_heal=False):
    """Full decision: evaluate -> print -> (loud) self-heal -> return exit code.
    Returns 0 (proceed) or 3 (hard block). Does NOT call sys.exit -- the caller does,
    so this is reusable from apply_md_changeset.py and from main() alike (DRY)."""
    res = evaluate()
    print_status(res)
    if res['ok']:
        print('  => protection hooks registered.')
        print('     (NOTE: REGISTRATION only -- not a proof THIS session fires them. A snapshot')
        print('      taken before the last hook change can still be stale.)')
        print('     For write-heavy md-swarm/md-iterate after cc-switch/provider changes, run:')
        print('       py <md-swarm>/probe_live_hooks.py --prepare')
        print('     Then perform the printed current-session Write + shell probes; both must DENY.')
        return 0

    ctx = (' [' + context + ']') if context else ''
    print('')
    print('!!! PROTECTION HOOKS ARE DOWN' + ctx + ' -- md-* second-layer safety is OFF. !!!')
    print('    Most likely: cc-switch overwrote ~/.claude/settings.json on a provider switch')
    print('    (a provider template with no "hooks" key blanks the registration).')
    print('    Layer-1 (single-writer + citation/uniqueness/order guards) still protects the')
    print('    write itself; what is OFF is the harness physically blocking a stray direct-write.')

    healed = False
    if not no_heal and not res['registered']:
        healed = self_heal(res['hooks_dir'])

    if mode == 'warn':
        print('  [warn mode] continuing (this step does not write the protected manuscript.md).')
        if healed:
            print('  Hooks were re-registered; restart a NEW session before any write-heavy step.')
            print(RESTART_HELP)
        return 0

    # --- block mode ---
    if dry:
        print('  [--dry-run] writes nothing -> continuing despite hooks down.')
        return 0
    if allow_no_hooks:
        print('  [--allow-no-hooks] PROCEEDING WITH HOOKS DOWN by explicit request.')
        print('  Make sure no sub-agent writes manuscript.md directly. Layer-1 still applies.')
        return 0

    print('')
    print('  HARD STOP (--mode block): refusing to run with the protection layer down.')
    if healed:
        print('  Re-registered just now, BUT this session holds the OLD snapshot. Restart:')
    else:
        print('  Re-register: powershell -ExecutionPolicy Bypass -File <suite>/setup_hooks.ps1')
        print('  then restart and re-run:')
    print(RESTART_HELP)
    print('  Override (only if you understand layer-1 still applies): re-run with --allow-no-hooks.')
    return 3


def run_selftest():
    """One bug = one test (dev-manual section 6.5 rule 4). Verifies evaluate()'s two axes."""
    import tempfile, shutil
    fails = 0
    d = tempfile.mkdtemp(prefix='preflight_selftest_')
    try:
        # 1) registration missing -> registered False
        empty = os.path.join(d, 'empty.json')
        open(empty, 'w', encoding='utf-8').write('{}')
        if evaluate(hooks_dir=d, settings_file=empty)['registered']:
            print('  [FAIL] empty settings.json should give registered=False'); fails += 1
        else:
            print('  [OK]   missing registration detected (registered=False)')

        # 2) both hook names present -> registered True
        full = os.path.join(d, 'full.json')
        open(full, 'w', encoding='utf-8').write(
            '{"hooks":{"PreToolUse":[{"hooks":[{"command":'
            '"...md_protect_hook.ps1... ...md_swarm_gate_hook.ps1..."}]}]}}')
        if not evaluate(hooks_dir=d, settings_file=full)['registered']:
            print('  [FAIL] both hook names present should give registered=True'); fails += 1
        else:
            print('  [OK]   present registration detected (registered=True)')

        # 3) only one hook name present -> registered False (need BOTH)
        half = os.path.join(d, 'half.json')
        open(half, 'w', encoding='utf-8').write('{"x":"md_protect_hook.ps1 only"}')
        if evaluate(hooks_dir=d, settings_file=half)['registered']:
            print('  [FAIL] only one hook name should give registered=False'); fails += 1
        else:
            print('  [OK]   partial registration detected (registered=False)')

        # 4) both hook files present -> scripts_present True
        for fn in HOOK_FILES:
            open(os.path.join(d, fn), 'w').write('x')
        if not evaluate(hooks_dir=d, settings_file=full)['scripts_present']:
            print('  [FAIL] both hook files present should give scripts_present=True'); fails += 1
        else:
            print('  [OK]   hook scripts present detected')

        # 5) a hook file missing -> scripts_present False
        os.remove(os.path.join(d, HOOK_FILES[0]))
        if evaluate(hooks_dir=d, settings_file=full)['scripts_present']:
            print('  [FAIL] a missing hook file should give scripts_present=False'); fails += 1
        else:
            print('  [OK]   missing hook script detected')
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print('=== selftest: ' + ('ALL PASSED' if fails == 0 else (str(fails) + ' FAILED')) + ' ===')
    return 0 if fails == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['block', 'warn'], default='warn',
                    help='block = hard-stop if hooks down (apply / md-unpack); '
                         'warn = print + continue (md-build / md-triage)')
    ap.add_argument('--context', default='', help='label for the message, e.g. "md-unpack"')
    ap.add_argument('--allow-no-hooks', action='store_true',
                    help='escape hatch: proceed even with protection hooks down '
                         '(layer-1 guards still apply)')
    ap.add_argument('--no-heal', action='store_true',
                    help='do not auto-run setup_hooks.ps1 on missing registration')
    ap.add_argument('--selftest', action='store_true', help='run unit checks and exit')
    a = ap.parse_args()

    if a.selftest:
        sys.exit(run_selftest())

    sys.exit(gate(mode=a.mode, context=a.context,
                  allow_no_hooks=a.allow_no_hooks, no_heal=a.no_heal))


if __name__ == '__main__':
    main()
