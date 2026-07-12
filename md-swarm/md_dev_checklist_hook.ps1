# md_dev_checklist_hook.ps1
# PreToolUse REMINDER (non-blocking): when you (or an AI) are about to edit md-* SKILL CODE
# (transform.py / build.ps1 / ingest_manuscript.ps1 / *.py / *.ps1 / *.lua under a md-* skill
# folder), surface the engineering self-check checklist (handbook section 6.5) so the same
# classes of bugs don't recur. This NEVER blocks -- it only prints a reminder and exits 0.
#
# Author's request (2026-06-26): "remind me every time I iterate on md-* bugs."
# Install: ~/.claude/settings.json hooks.PreToolUse, matcher = "Write|Edit|MultiEdit"
#   (cross-machine: cmd /c ... "%USERPROFILE%\.claude\skills\md-swarm\md_dev_checklist_hook.ps1").
# ASCII-only ON PURPOSE: the two protective hooks contain Chinese and need UTF-8-with-BOM;
# this one stays pure ASCII so it is BOM-agnostic and can never garble under PS 5.1.
# Principle: never block on a reminder -- any parse error / unexpected input -> exit 0.

$ErrorActionPreference = 'SilentlyContinue'
try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }
    $in = $raw | ConvertFrom-Json
    $tool = [string]$in.tool_name
    if ($tool -ne 'Write' -and $tool -ne 'Edit' -and $tool -ne 'MultiEdit') { exit 0 }

    $fp = [string]$in.tool_input.file_path
    if ([string]::IsNullOrWhiteSpace($fp)) { exit 0 }

    # md-* SKILL CODE only: a .py/.ps1/.lua under a md-<name> skill folder, reached via either the
    # ~/.claude/skills junction OR the OneDrive "...\SKILL\md-..." master. (Editing manuscripts or
    # .md docs does NOT trigger -- that is not "iterating on the skill code".)
    if ($fp -notmatch '(?i)[\\/](?:\.claude[\\/]skills|SKILL)[\\/]md-[a-z]+[\\/][^\\/]+\.(?:ps1|py|lua)$') { exit 0 }

    $msg = @"
[md-* DEV REMINDER] You are editing md-* skill code. Before you finish, run the engineering
self-check (dev handbook section 6.5):
  (1) allowlist > denylist        -- enumerate what's ALLOWED, not what's banned
  (2) encoding explicit           -- bytes != chars; declare UTF-8/BOM/LF, don't assume
  (3) AST > regex                 -- don't hand-assemble Markdown/JSON/XML with re.sub
  (4) one bug = one test          -- leave an automatic regression test, not a manual run
  (5) fail loud / fail fast       -- validate at the boundary; exit 0 != correct
  (6) DRY / single source         -- define a rule once, don't copy it across files
  (7) [removed 2026-06-29]        -- testability check retired by the author
  (8) single-writer               -- never let two things write one file/state concurrently
  META: push knowledge into CODE (types/tests/asserts), not into docs you must remember.
  Full checklist: the md-* dev handbook, section 6.5.
"@

    # Non-blocking surfacing: emit as PreToolUse additionalContext (the model sees it) AND to
    # stderr (the user sees it in the transcript). NO permissionDecision -> normal flow, never blocks.
    $out = @{ hookSpecificOutput = @{ hookEventName = 'PreToolUse'; additionalContext = $msg } }
    Write-Output ($out | ConvertTo-Json -Depth 6 -Compress)
    [Console]::Error.WriteLine($msg)
    exit 0
} catch {
    exit 0
}
