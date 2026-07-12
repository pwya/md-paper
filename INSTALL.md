# Installing md-paper · md-paper 安装手册

**This file is an executable runbook.** In a [Claude Code](https://www.claude.com/product/claude-code) (or compatible agent) session, just say:
> *"Read INSTALL.md and set up md-paper for me."*
> *"读一下 INSTALL.md，帮我把 md-paper 装好。"*

…and the AI will run the steps below. You do not need to type the commands yourself. (They're all shown so you *can* run them by hand if you prefer.)

---

## FOR THE AI ASSISTANT — follow these steps in order

> You are installing md-paper: five Claude Code skills + a global pandoc toolchain + two manuscript-protection hooks. Run each step, check the result, and stop with a clear message if a precondition fails. **Do not skip the verification.** All commands are PowerShell on Windows.

### Step 0 — Preconditions

md-paper's ingest needs **Windows + Microsoft Word**. Check the essentials:

```powershell
"OS      : $([System.Environment]::OSVersion.VersionString)"
"git     : $((Get-Command git -EA SilentlyContinue).Version)"
"python  : $(try { (& py --version) 2>&1 } catch { (& python --version) 2>&1 })"
```

- Not on Windows → **stop** and tell the user: ingest (`md-unpack`) requires Windows + Word; the rest of the pipeline is cross-platform but this installer targets Windows.
- `git` or `python` missing → tell the user to install them (`winget install Git.Git`, `winget install Python.Python.3`), then re-run.

### Step 1 — Locate the repo root

This `INSTALL.md` sits in the md-paper repo root (the folder that contains `md-unpack/`, `md-build/`, etc.). Determine that folder's absolute path — call it `$repo`. If the user hasn't cloned it yet:

```powershell
git clone https://github.com/pwya/md-paper.git
$repo = (Resolve-Path .\md-paper).Path
```

If they already cloned it, set `$repo` to that folder.

### Step 2 — Link the five skills into Claude Code

Claude Code auto-discovers skills under `~/.claude/skills/`. Junction the five user-facing skill folders there (junctions need no admin and no developer mode):

```powershell
$skillsDir = Join-Path $env:USERPROFILE ".claude\skills"
New-Item -ItemType Directory -Path $skillsDir -Force | Out-Null
'md-unpack','md-triage','md-swarm','md-iterate','md-build' | ForEach-Object {
    $link = Join-Path $skillsDir $_
    if (Test-Path $link) { "skip (exists): $_" }
    else { New-Item -ItemType Junction -Path $link -Target (Join-Path $repo $_) | Out-Null; "linked: $_" }
}
```

> Note: `md--develop` / `md--explain` are the author's private dev tools and are **not** part of this repo. `_md-shared` is a maintenance base, not a skill — do not link it.

### Step 3 — Install the global pandoc toolchain (once per machine)

```powershell
powershell -ExecutionPolicy Bypass -File (Join-Path $repo "md-build\setup_md_tools.ps1")
```

This downloads the pinned **pandoc 3.9.0.2 + pandoc-crossref 0.3.24a** into `%LOCALAPPDATA%\md-pandoc` (no admin, no PATH pollution) and copies the bundled Zotero Lua filters locally. Behind a firewall / in mainland China, add `-Mirror https://<a-github-mirror>`. If it prints "pandoc not found" later, this step didn't complete.

### Step 4 — Register the manuscript-protection hooks

```powershell
powershell -ExecutionPolicy Bypass -File (Join-Path $repo "md-unpack\setup_hooks.ps1")
```

This writes two PreToolUse hooks into `~/.claude/settings.json` (and patches cc-switch provider templates if present, so switching providers won't wipe them). The hooks physically block any AI tool call from directly overwriting `manuscript.md` — all AI edits must go through the safety-checked apply pipeline.

### Step 5 — Restart, then verify (do not skip)

Hooks activate only in a **new** Claude Code session (the harness snapshots hooks at session start).

1. Tell the user: **"Open a brand-new Claude Code conversation now"** (`/clear` does not reload hooks).
2. In the new session, verify:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $env:USERPROFILE ".claude\skills\md-swarm\verify_hooks.ps1")
```

Expect **6/6 green**. Any red → follow its on-screen hint (junction / registration / restart / BOM).

### Step 6 — Report

Tell the user, concisely: which skills were linked, that the toolchain installed, that hooks are registered, and that they must open a new session + run `verify_hooks.ps1` to confirm 6/6. Then point them at the README's "How to use" section: `/md-unpack "path\to\paper.docx"` → revise → `/md-build`.

---

## Manual install (if you'd rather not use an AI)

Run Steps 1–5 above yourself in PowerShell, substituting the cloned repo path for `$repo`. That's the entire install: **link 5 skills → `setup_md_tools.ps1` → `setup_hooks.ps1` → restart → `verify_hooks.ps1` (6/6).**

## Other agents (Codex, etc.)

The skill/hook auto-loading in Steps 2 & 4 is Claude Code-specific. In other harnesses, the pipeline scripts are still plain CLI tools you can call directly (`py md-swarm\apply_md_changeset.py …`, `powershell md-build\build.ps1 …`); only the natural-language "skill" wrapper and the protection hooks won't auto-load. Full workflow support currently assumes Claude Code.
