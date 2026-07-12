# -*- coding: utf-8 -*-
"""
check_state.py -- deterministic workflow guard for md-iterate.

This script is deliberately small and boring. It decides whether a single-point
iteration is allowed in the current project directory before any patch is drafted.
It does NOT write manuscript.md and it does NOT replace apply_md_changeset.py.

Allowed state (allowlist):
  - manuscript.md exists
  - no pending md_triage.md waiting for confirmation
  - no confirmed-but-not-finished md-swarm run
  - no leftover swarm/patches/*.json from an interrupted batch

Everything else fails loudly with a concrete next step. This keeps md-iterate from
silently desynchronizing md-triage/md-swarm plans.

Usage:
  py check_state.py --workdir <project>
  py check_state.py --workdir <project> --json
  py check_state.py --selftest

Exit:
  0 = allowed (warnings may be present)
  1 = blocked by workflow state
  2 = fatal / bad arguments
"""
import argparse
import json
import os
import re
import sys
import tempfile
import shutil
from pathlib import Path


TOKEN_PENDING = "**人工确认：** 待确认"
TOKEN_CONFIRMED = "**人工确认：** 已确认"


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return Path(path).read_text(encoding="utf-8-sig")


def _triage_state(triage_path):
    if not triage_path.exists():
        return "absent"
    text = read_text(triage_path)
    if TOKEN_PENDING in text:
        return "pending"
    if TOKEN_CONFIRMED in text:
        return "confirmed"
    return "unknown"


def _has_json_files(path):
    return path.exists() and any(path.glob("*.json"))


def evaluate(workdir):
    root = Path(workdir).resolve()
    swarm = root / "swarm"
    manuscript = root / "manuscript.md"
    triage = swarm / "md_triage.md"
    patches_dir = swarm / "patches"
    patches_applied = swarm / "patches_applied"
    audit_report = swarm / "audit_report.md"
    consistency_report = swarm / "consistency_report.md"

    blocked = []
    warnings = []
    info = []

    if not manuscript.exists():
        blocked.append({
            "code": "no_manuscript",
            "message": "manuscript.md not found. Run md-unpack first.",
        })

    # A leftover live patch directory is always a stop sign: it means a swarm batch or
    # another apply pipeline may be half-finished. Do not draft a new single-point patch
    # on top of that state.
    if _has_json_files(patches_dir):
        blocked.append({
            "code": "live_patches_present",
            "message": "swarm/patches contains JSON patch files. Finish or clean the interrupted md-swarm batch first.",
        })

    triage_state = _triage_state(triage)
    info.append({"triage_state": triage_state})
    if triage_state == "pending":
        blocked.append({
            "code": "triage_pending",
            "message": "swarm/md_triage.md is still pending confirmation. Put this idea into its '人类修改思路' field or finish triage before using md-iterate.",
        })
    elif triage_state == "confirmed":
        # Allow if there is evidence that md-swarm has completed at least one full run:
        # applied patches or the audit/consistency reports. Otherwise the confirmed
        # triage is a queued plan, and editing manuscript.md now will desync it.
        swarm_done = patches_applied.exists() or audit_report.exists() or consistency_report.exists()
        if not swarm_done:
            blocked.append({
                "code": "swarm_confirmed_not_done",
                "message": "md_triage.md is confirmed but md-swarm does not look complete. Run md-swarm first, or explicitly abandon/rebuild this triage plan.",
            })
        else:
            warnings.append({
                "code": "post_swarm",
                "message": "Existing md_triage.md appears completed by md-swarm; md-iterate is allowed as a post-swarm touch-up.",
            })
    elif triage_state == "unknown":
        blocked.append({
            "code": "triage_token_unknown",
            "message": "swarm/md_triage.md exists but its confirmation token is not recognized. Fix the token or move/backup the stale triage file.",
        })

    # Existing build outputs are not blockers, but if they are older than manuscript.md
    # the user should know that another md-build is needed after iteration.
    build_dir = root / "build"
    if manuscript.exists() and build_dir.exists():
        outs = list(build_dir.glob("out_*.docx"))
        if outs:
            newest_out = max(outs, key=lambda p: p.stat().st_mtime)
            if newest_out.stat().st_mtime < manuscript.stat().st_mtime:
                warnings.append({
                    "code": "build_stale",
                    "message": "build/out_*.docx is older than manuscript.md. Re-run md-build after iterating.",
                })

    return {
        "ok": not blocked,
        "workdir": str(root),
        "blocked": blocked,
        "warnings": warnings,
        "info": info,
    }


def print_human(res):
    print("=== md-iterate state guard ===")
    print("workdir:", res["workdir"])
    print("status :", "OK" if res["ok"] else "BLOCKED")
    if res["blocked"]:
        print("")
        print("[blocked]")
        for item in res["blocked"]:
            print(" -", item["code"] + ":", item["message"])
    if res["warnings"]:
        print("")
        print("[warnings]")
        for item in res["warnings"]:
            print(" -", item["code"] + ":", item["message"])
    if res["info"]:
        print("")
        print("[info]")
        for item in res["info"]:
            for k, v in item.items():
                print(" -", k + ":", v)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def selftest():
    tmp = Path(tempfile.mkdtemp(prefix="md_iterate_state_"))
    fails = 0
    try:
        # 1. No manuscript blocks.
        r = evaluate(tmp)
        if r["ok"] or not any(x["code"] == "no_manuscript" for x in r["blocked"]):
            print("[FAIL] no manuscript should block"); fails += 1
        else:
            print("[OK] no manuscript blocks")

        # 2. Clean manuscript allows.
        _write(tmp / "manuscript.md", "hello\n")
        r = evaluate(tmp)
        if not r["ok"]:
            print("[FAIL] clean manuscript should allow", r); fails += 1
        else:
            print("[OK] clean manuscript allows")

        # 3. Pending triage blocks.
        _write(tmp / "swarm" / "md_triage.md", TOKEN_PENDING + "\n")
        r = evaluate(tmp)
        if r["ok"] or not any(x["code"] == "triage_pending" for x in r["blocked"]):
            print("[FAIL] pending triage should block"); fails += 1
        else:
            print("[OK] pending triage blocks")

        # 4. Confirmed but no swarm output blocks.
        _write(tmp / "swarm" / "md_triage.md", TOKEN_CONFIRMED + "\n")
        r = evaluate(tmp)
        if r["ok"] or not any(x["code"] == "swarm_confirmed_not_done" for x in r["blocked"]):
            print("[FAIL] confirmed triage without swarm evidence should block"); fails += 1
        else:
            print("[OK] confirmed triage without swarm evidence blocks")

        # 5. Confirmed + patches_applied allows with warning.
        (tmp / "swarm" / "patches_applied").mkdir(parents=True, exist_ok=True)
        r = evaluate(tmp)
        if not r["ok"] or not any(x["code"] == "post_swarm" for x in r["warnings"]):
            print("[FAIL] completed swarm evidence should allow with warning", r); fails += 1
        else:
            print("[OK] completed swarm evidence allows")

        # 6. Live patches block even after swarm evidence.
        _write(tmp / "swarm" / "patches" / "x.json", "{}\n")
        r = evaluate(tmp)
        if r["ok"] or not any(x["code"] == "live_patches_present" for x in r["blocked"]):
            print("[FAIL] live patches should block"); fails += 1
        else:
            print("[OK] live patches block")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=== selftest:", "ALL PASSED" if fails == 0 else str(fails) + " FAILED", "===")
    return 0 if fails == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest())

    res = evaluate(args.workdir)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print_human(res)
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()

