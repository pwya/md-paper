# -*- coding: utf-8 -*-
"""
smoke_iterate.py -- reversible md-iterate smoke test using a temporary project.

This tests the md-iterate apply path without touching a real manuscript:
  changeset -> apply dry-run -> apply -> verify_applied -> verify_refs -> reverse apply

Hook registration is tested elsewhere by md-swarm/verify_hooks.ps1. This smoke
passes --allow-no-hooks to apply_md_changeset.py so it exercises the layer-1
single-writer/citation/uniqueness path in any development shell.

Usage:
  py md-iterate/smoke_iterate.py
"""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ORIGINAL = "# Smoke\n\nThis sentence needs a small polish.\n"
FORWARD_FIND = "This sentence needs a small polish."
FORWARD_REPLACE = "This sentence needs a tiny polish."


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(cmd, cwd):
    print("$ " + " ".join(str(x) for x in cmd))
    proc = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True)
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    if proc.returncode != 0:
        raise RuntimeError("command failed with exit %s" % proc.returncode)


def write_changeset(path, patch_id, find, replace):
    data = {
        "source_md": "manuscript.md",
        "patches": [
            {
                "id": patch_id,
                "target": "temporary smoke sentence",
                "mode": "patch",
                "intent": "modify",
                "find": find,
                "replace": replace,
            }
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main():
    suite = Path(__file__).resolve().parents[1]
    md_swarm = suite / "md-swarm"
    apply_py = md_swarm / "apply_md_changeset.py"
    verify_applied_py = md_swarm / "verify_applied.py"
    verify_refs_py = md_swarm / "verify_refs.py"

    tmp = Path(tempfile.mkdtemp(prefix="md_iterate_smoke_"))
    try:
        manuscript = tmp / "manuscript.md"
        changeset = tmp / "swarm" / "iterate_last_changeset.json"
        manuscript.write_text(ORIGINAL, encoding="utf-8", newline="\n")
        before_hash = sha256(manuscript)

        write_changeset(changeset, "iterate-smoke-forward", FORWARD_FIND, FORWARD_REPLACE)
        run([sys.executable, str(apply_py), "--changeset", str(changeset), "--manuscript", str(manuscript), "--dry-run", "--allow-no-hooks"], tmp)
        run([sys.executable, str(apply_py), "--changeset", str(changeset), "--manuscript", str(manuscript), "--allow-no-hooks"], tmp)
        run([sys.executable, str(verify_applied_py), "--changeset", str(changeset), "--manuscript", str(manuscript)], tmp)
        run([sys.executable, str(verify_refs_py), "--current", str(manuscript), "--changeset", str(changeset)], tmp)
        if FORWARD_REPLACE not in manuscript.read_text(encoding="utf-8"):
            raise RuntimeError("forward patch did not land")

        write_changeset(changeset, "iterate-smoke-revert", FORWARD_REPLACE, FORWARD_FIND)
        run([sys.executable, str(apply_py), "--changeset", str(changeset), "--manuscript", str(manuscript), "--dry-run", "--allow-no-hooks"], tmp)
        run([sys.executable, str(apply_py), "--changeset", str(changeset), "--manuscript", str(manuscript), "--allow-no-hooks"], tmp)
        run([sys.executable, str(verify_applied_py), "--changeset", str(changeset), "--manuscript", str(manuscript)], tmp)
        run([sys.executable, str(verify_refs_py), "--current", str(manuscript), "--changeset", str(changeset)], tmp)

        after_hash = sha256(manuscript)
        if after_hash != before_hash:
            raise RuntimeError("revert hash mismatch: %s != %s" % (after_hash, before_hash))

        print("=== md-iterate smoke: ALL PASSED ===")
        print("temp project removed:", tmp)
        print("final hash:", after_hash)
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
