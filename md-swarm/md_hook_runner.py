#!/usr/bin/env python3
"""Stable Claude Code bridge for the md-* PowerShell hooks on Windows.

Claude Code 2.1.220 runs hook commands through Git Bash when Git Bash exists.
Windows PowerShell 5.1 can emit redirected stdout in an encoding/shape that the
hook JSON parser does not accept. This bridge:

1. accepts Claude's UTF-8 JSON on stdin;
2. runs one allowlisted md-* PowerShell hook with explicit UTF-8 console I/O;
3. parses PowerShell output across the common Windows encodings;
4. re-emits non-deny JSON as ASCII-only JSON with no BOM; and
5. converts a PreToolUse "deny" into Claude's documented exit code 2.

The protection-hook logic remains in the existing .ps1 files. This file only
normalizes transport and cannot be used to launch arbitrary scripts.
"""

from __future__ import annotations

import argparse
import json
import locale
import os
from pathlib import Path
import subprocess
import sys


ALLOWED_HOOKS = {
    "md_dev_checklist_hook.ps1",
    "md_protect_hook.ps1",
    "md_swarm_gate_hook.ps1",
}


def _decode_output(data: bytes) -> str:
    if not data:
        return ""
    candidates: list[str] = []
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        candidates.append("utf-16")
    if data.startswith(b"\xef\xbb\xbf"):
        candidates.append("utf-8-sig")
    if b"\x00" in data[:32]:
        candidates.extend(("utf-16-le", "utf-16-be"))
    candidates.extend(
        (
            "utf-8-sig",
            locale.getpreferredencoding(False),
            "gb18030",
            "utf-16-le",
        )
    )
    seen: set[str] = set()
    for encoding in candidates:
        key = encoding.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")


def _extract_json_object(text: str) -> dict | None:
    cleaned = text.lstrip("\ufeff \t\r\n")
    if not cleaned:
        return None
    decoder = json.JSONDecoder()
    for pos, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(cleaned[pos:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _resolve_allowlisted_hook(raw_path: str) -> Path:
    hook = Path(raw_path).expanduser().resolve(strict=True)
    expected_dir = (
        Path.home() / ".claude" / "skills" / "md-swarm"
    ).resolve(strict=True)
    if hook.name not in ALLOWED_HOOKS or hook.parent != expected_dir:
        raise ValueError(
            "hook must be an allowlisted script under "
            + str(expected_dir)
        )
    return hook


def _run_hook(hook: Path, payload: bytes) -> subprocess.CompletedProcess[bytes]:
    bootstrap = (
        "[Console]::InputEncoding="
        "[System.Text.UTF8Encoding]::new($false);"
        "[Console]::OutputEncoding="
        "[System.Text.UTF8Encoding]::new($false);"
        "& $env:MD_HOOK_RUNNER_TARGET; exit $LASTEXITCODE"
    )
    env = os.environ.copy()
    # Keep paths containing `&` out of PowerShell's -Command tokenization.
    env["MD_HOOK_RUNNER_TARGET"] = str(hook)
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            bootstrap,
        ],
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )


def _handle_result(result: subprocess.CompletedProcess[bytes]) -> int:
    stdout = _decode_output(result.stdout)
    parsed = _extract_json_object(stdout)

    if parsed is not None:
        specific = parsed.get("hookSpecificOutput")
        decision = (
            specific.get("permissionDecision")
            if isinstance(specific, dict)
            else None
        )
        if decision == "deny":
            reason = specific.get("permissionDecisionReason")
            if not isinstance(reason, str) or not reason.strip():
                reason = "md-* protection hook denied this tool call."
            sys.stderr.write(reason.rstrip() + "\n")
            sys.stderr.flush()
            return 2

        # ASCII-only output prevents Windows code-page/BOM ambiguity.
        sys.stdout.write(
            json.dumps(parsed, ensure_ascii=True, separators=(",", ":"))
        )
        sys.stdout.flush()
        return 0

    if stdout.strip() or result.returncode != 0:
        detail = _decode_output(result.stderr).strip()
        suffix = (": " + detail[:500]) if detail else ""
        sys.stderr.write(
            "[md-hook-runner WARN] hook output was not valid JSON; "
            "failing open" + suffix + "\n"
        )
        sys.stderr.flush()
    return 0


def _selftest() -> int:
    samples = (
        b'{"hookSpecificOutput":{"permissionDecision":"deny"}}',
        b"\xef\xbb\xbf{\"x\":1}",
        '{"x":"中文"}'.encode("utf-16"),
    )
    parsed = [_extract_json_object(_decode_output(item)) for item in samples]
    if parsed != [
        {"hookSpecificOutput": {"permissionDecision": "deny"}},
        {"x": 1},
        {"x": "中文"},
    ]:
        print("selftest failed: decode/JSON normalization", file=sys.stderr)
        return 1
    print("md_hook_runner selftest: PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hook")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return _selftest()
    if not args.hook:
        parser.error("--hook is required unless --selftest is used")
    try:
        hook = _resolve_allowlisted_hook(args.hook)
    except (OSError, ValueError) as exc:
        print("[md-hook-runner ERROR] " + str(exc), file=sys.stderr)
        return 64
    payload = sys.stdin.buffer.read()
    return _handle_result(_run_hook(hook, payload))


if __name__ == "__main__":
    raise SystemExit(main())
