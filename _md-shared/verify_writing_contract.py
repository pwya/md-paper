# -*- coding: utf-8 -*-
"""
verify_writing_contract.py -- keep the anti-AI writing contract byte-for-byte aligned.

The md suite deliberately keeps the anti-AI rules inline in both md-swarm and
md-iterate, while _md-shared/writing_contract.md is the maintenance baseline.
This script makes that duplication loud and testable.

Usage:
  py _md-shared/verify_writing_contract.py
  py _md-shared/verify_writing_contract.py --selftest
"""
import argparse
import shutil
import sys
import tempfile
from pathlib import Path


START = "5.【去AI味·所有 replacement 正文必过·逐条照办，别凭记忆简化成一句】："
END = "   · 一句话：同样的意思、同样的引用，用更短、更直、更不工整、更少破折号、更不像 AI 的话，重写一遍。"


def read_text(path):
    return Path(path).read_text(encoding="utf-8")


def extract_block(path):
    text = read_text(path).replace("\r\n", "\n")
    start = text.find(START)
    if start < 0:
        raise ValueError("start marker not found in %s" % path)
    end = text.find(END, start)
    if end < 0:
        raise ValueError("end marker not found in %s" % path)
    return text[start:end + len(END)]


def compare(root):
    root = Path(root)
    files = [
        root / "md-swarm" / "SKILL.md",
        root / "md-iterate" / "SKILL.md",
        root / "_md-shared" / "writing_contract.md",
    ]
    blocks = [(path, extract_block(path)) for path in files]
    baseline_path, baseline = blocks[0]
    mismatches = []
    for path, block in blocks[1:]:
        if block != baseline:
            mismatches.append((baseline_path, path))
    return baseline, mismatches


def selftest():
    root = Path(__file__).resolve().parents[1]
    tmp = Path(tempfile.mkdtemp(prefix="md_contract_selftest_"))
    try:
        for name in ("md-swarm", "md-iterate", "_md-shared"):
            (tmp / name).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / "md-swarm" / "SKILL.md", tmp / "md-swarm" / "SKILL.md")
        shutil.copyfile(root / "md-iterate" / "SKILL.md", tmp / "md-iterate" / "SKILL.md")
        shutil.copyfile(root / "_md-shared" / "writing_contract.md", tmp / "_md-shared" / "writing_contract.md")

        _, mismatches = compare(tmp)
        if mismatches:
            print("[FAIL] copied files should match")
            return 1
        print("[OK] matching contract passes")

        p = tmp / "md-iterate" / "SKILL.md"
        text = read_text(p)
        p.write_text(text.replace("少修辞", "少修辞X", 1), encoding="utf-8", newline="\n")
        _, mismatches = compare(tmp)
        if not mismatches:
            print("[FAIL] mutated contract should be detected")
            return 1
        print("[OK] mutated contract is detected")
        print("=== selftest: ALL PASSED ===")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest())

    root = Path(__file__).resolve().parents[1]
    try:
        block, mismatches = compare(root)
    except Exception as exc:
        print("FATAL:", exc)
        sys.exit(2)

    if mismatches:
        print("=== writing contract: MISMATCH ===")
        for left, right in mismatches:
            print(" - %s differs from %s" % (right, left))
        sys.exit(1)

    print("=== writing contract: OK ===")
    print("md-swarm == md-iterate == _md-shared")
    print("chars:", len(block))
    sys.exit(0)


if __name__ == "__main__":
    main()
