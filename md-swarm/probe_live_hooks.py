# -*- coding: utf-8 -*-
"""
probe_live_hooks.py -- prepare and check a CURRENT-SESSION hook live probe.

verify_hooks.ps1 proves that hook scripts and registration are correct. It cannot
prove that the already-running Claude Code session has loaded that registration,
because the harness snapshots hooks at session start.

This script prepares a harmless temporary md project. The *current assistant
session* must then perform two tool calls that should be denied by hooks:

  1. Write directly to the protected manuscript.md -> md_protect_hook should deny.
  2. Run apply_md_changeset.py with a pending "机改" changeset -> md_swarm_gate_hook
     should deny.

After the two attempted tool calls, run --check. If either hook did not fire, the
temporary manuscript will have changed and --check exits non-zero.

Usage:
  py md-swarm/probe_live_hooks.py --prepare
  py md-swarm/probe_live_hooks.py --check --root <probe-root>
  py md-swarm/probe_live_hooks.py --cleanup --root <probe-root>
  py md-swarm/probe_live_hooks.py --selftest
"""
import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path


SENTINEL = "LIVE_HOOK_PROBE_SENTINEL_DO_NOT_CHANGE\n"
PROTECT_BAD = "PROTECT_HOOK_PROBE_WRITE_SHOULD_NOT_LAND\n"
GATE_BAD = "GATE_HOOK_PROBE_APPLY_SHOULD_NOT_LAND\n"
STATE_REL = Path("swarm") / "live_hook_probe_state.json"


def suite_root():
    return Path(__file__).resolve().parents[1]


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def read_text(path):
    return Path(path).read_text(encoding="utf-8")


def make_changeset(path):
    data = {
        "source_md": "manuscript.md",
        "patches": [
            {
                "id": "机改-LIVE-PROBE",
                "target": "temporary live hook probe",
                "mode": "patch",
                "intent": "modify",
                "find": SENTINEL.rstrip("\n"),
                "replace": GATE_BAD.rstrip("\n"),
            }
        ],
    }
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def prepare(root=None):
    if root:
        root = Path(root).resolve()
        if root.exists() and any(root.iterdir()):
            raise RuntimeError("probe root is not empty: %s" % root)
        root.mkdir(parents=True, exist_ok=True)
    else:
        root = Path(tempfile.mkdtemp(prefix="md_live_hook_probe_")).resolve()

    (root / "manifest").mkdir(parents=True, exist_ok=True)
    (root / "swarm").mkdir(parents=True, exist_ok=True)
    write_text(root / "manuscript.md", SENTINEL)
    write_text(root / "swarm" / "md_triage.md", "> **人工确认：** 待确认\n")
    make_changeset(root / "swarm" / "probe_changeset.json")

    apply_py = suite_root() / "md-swarm" / "apply_md_changeset.py"
    state = {
        "root": str(root),
        "manuscript": str(root / "manuscript.md"),
        "changeset": str(root / "swarm" / "probe_changeset.json"),
        "apply_py": str(apply_py),
        "protect_bad": PROTECT_BAD,
        "gate_bad": GATE_BAD,
    }
    write_text(root / STATE_REL, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    print_instructions(state)
    return root


def print_instructions(state):
    root = state["root"]
    manuscript = state["manuscript"]
    changeset = state["changeset"]
    apply_py = state["apply_py"]
    check_cmd = 'py "%s" --check --root "%s"' % (Path(__file__).resolve(), root)
    cleanup_cmd = 'py "%s" --cleanup --root "%s"' % (Path(__file__).resolve(), root)
    gate_cmd = 'py "%s" --changeset "%s" --manuscript "%s" --allow-no-hooks' % (
        apply_py, changeset, manuscript)

    print("=== md live hook probe prepared ===")
    print("probe root :", root)
    print("manuscript :", manuscript)
    print("")
    print("IMPORTANT: The next two actions must be real CURRENT-SESSION tool calls.")
    print("Do not replace them with direct script calls to the hook files.")
    print("")
    print("ACTION 1 - use the assistant Write tool:")
    print("  file_path:", manuscript)
    print("  content  :", PROTECT_BAD.rstrip("\n"))
    print("  EXPECTED : DENIED by md_protect_hook. If it writes, current-session protect hook is NOT live.")
    print("")
    print("ACTION 2 - use the assistant PowerShell/Bash tool:")
    print("  command  :", gate_cmd)
    print("  EXPECTED : DENIED by md_swarm_gate_hook. If it runs/applies, current-session gate hook is NOT live.")
    print("")
    print("Then run:")
    print(" ", check_cmd)
    print("Cleanup when done:")
    print(" ", cleanup_cmd)


def load_state(root):
    root = Path(root).resolve()
    state_path = root / STATE_REL
    if not state_path.exists():
        raise RuntimeError("probe state not found: %s" % state_path)
    return json.loads(read_text(state_path))


def check(root):
    state = load_state(root)
    manuscript = Path(state["manuscript"])
    if not manuscript.exists():
        raise RuntimeError("probe manuscript missing: %s" % manuscript)
    text = read_text(manuscript)

    protect_failed = PROTECT_BAD in text
    gate_failed = GATE_BAD in text
    ok = (not protect_failed) and (not gate_failed) and (text == SENTINEL)

    print("=== md live hook probe check ===")
    print("root:", state["root"])
    if protect_failed:
        print("[FAIL] protected manuscript was directly written. md_protect_hook is not live in this session.")
    else:
        print("[OK]   protect probe did not land (valid only if ACTION 1 was actually attempted).")

    if gate_failed:
        print("[FAIL] pending agent apply landed. md_swarm_gate_hook is not live in this session.")
    else:
        print("[OK]   gate probe did not land (valid only if ACTION 2 was actually attempted).")

    if ok:
        print("")
        print("RESULT: PASS if you observed both expected DENY messages during ACTION 1 and ACTION 2.")
        print("NOTE  : This filesystem check cannot distinguish DENIED from SKIPPED; the assistant must not skip actions.")
        return 0

    print("")
    print("RESULT: FAIL -- current-session hook layer is not live. Open a brand-new Claude Code session,")
    print("        run verify_hooks.ps1, then rerun this live probe before md-swarm/md-iterate writes.")
    return 1


def cleanup(root):
    root = Path(root).resolve()
    if root.exists():
        shutil.rmtree(root)
    print("removed:", root)


def selftest():
    tmp = Path(tempfile.mkdtemp(prefix="md_live_probe_selftest_"))
    try:
        root = tmp / "probe"
        prepare(root)
        if check(root) != 0:
            print("[FAIL] unchanged probe should check clean")
            return 1
        write_text(root / "manuscript.md", PROTECT_BAD)
        if check(root) == 0:
            print("[FAIL] protect failure should be detected")
            return 1
        write_text(root / "manuscript.md", GATE_BAD)
        if check(root) == 0:
            print("[FAIL] gate failure should be detected")
            return 1
        print("=== selftest: ALL PASSED ===")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", action="store_true")
    group.add_argument("--check", action="store_true")
    group.add_argument("--cleanup", action="store_true")
    group.add_argument("--selftest", action="store_true")
    ap.add_argument("--root", help="probe root directory")
    args = ap.parse_args()

    try:
        if args.selftest:
            return selftest()
        if args.prepare:
            prepare(args.root)
            return 0
        if not args.root:
            print("FATAL: --root is required for --check/--cleanup")
            return 2
        if args.check:
            return check(args.root)
        if args.cleanup:
            cleanup(args.root)
            return 0
    except Exception as exc:
        print("FATAL:", exc)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
