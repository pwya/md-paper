# Installing md-paper · md-paper 安装手册

**This file is an executable runbook.** In a [Claude Code](https://www.claude.com/product/claude-code), [Codex](https://github.com/openai/codex), [OpenCode](https://opencode.ai), or [Hermes Agent](https://github.com/NousResearch/hermes-agent) session (any capable agent works), just say:
> *"Read INSTALL.md and set up md-paper for me."*
> *"读一下 INSTALL.md，帮我把 md-paper 装好。"*

…and the AI will run the steps below. You do not need to type the commands yourself. (They're all shown so you *can* run them by hand if you prefer.)

---

## FOR THE AI ASSISTANT — follow these steps in order

> You are installing md-paper: five agent skills + a global pandoc toolchain + (Claude Code only) two manuscript-protection hooks. Run each step, check the result, and stop with a clear message if a precondition fails. **Do not skip the verification.** All commands are PowerShell on Windows.

### Step 0 — Preconditions

md-paper's ingest needs **Windows + Microsoft Word**. Check the essentials:

```powershell
"OS      : $([System.Environment]::OSVersion.VersionString)"
"git     : $((Get-Command git -EA SilentlyContinue).Version)"
"python  : $(try { (& py --version) 2>&1 } catch { (& python --version) 2>&1 })"
```

- Not on Windows → **stop** and tell the user: ingest (`md-unpack`) requires Windows + Word; the rest of the pipeline is cross-platform but this installer targets Windows.
- `python` missing → tell the user to install it (`winget install Python.Python.3`), then re-run.
- `git` missing → only needed for the *clone* path; **Download ZIP** users can skip it.

### Step 1 — Locate the repo root

This `INSTALL.md` sits in the md-paper repo root (the folder that contains `md-unpack/`, `md-build/`, etc.) — almost always the folder the user has open in their editor. Set `$repo` to that folder's absolute path (the directory containing this file):

```powershell
$repo = (Get-Location).Path   # if the working dir IS the md-paper folder; otherwise use its full path
```

If the files aren't on disk yet, the user can obtain them either way: **Download ZIP** (the green **`< > Code` → Download ZIP** button on GitHub, then unzip) — or `git clone https://github.com/pwya/md-paper.git`. Then set `$repo` to that unzipped/cloned `md-paper` folder. Confirm it's right: `Test-Path (Join-Path $repo 'md-build\setup_md_tools.ps1')` should return `True`.

### Step 2 — Link the five skills into YOUR agent

All five skills are standard **Agent Skills** ([agentskills.io](https://agentskills.io) `SKILL.md` format). Each tool discovers them from its own directory — link into every tool the user actually uses (several at once is fine):

| Tool | Skills directory | Note |
|---|---|---|
| **Claude Code** | `~\.claude\skills` | the default target below |
| **OpenCode** | `~\.claude\skills` works as-is | reads Claude's dir natively; `~\.config\opencode\skills` also works |
| **Codex** | `~\.codex\skills` | project-level `.codex\skills` works too |
| **Hermes Agent** | `~\.hermes\skills` | agentskills.io standard |
| anything else | your tool's skills dir | any SKILL.md-compatible agent works |

Ask the user which tool(s) they use, then junction the five skill folders into each target (junctions need no admin and no developer mode):

```powershell
$targets = @("$env:USERPROFILE\.claude\skills")            # Claude Code — OpenCode reads this too
# $targets += "$env:USERPROFILE\.codex\skills"             # uncomment if the user uses Codex
# $targets += "$env:USERPROFILE\.hermes\skills"            # uncomment if the user uses Hermes Agent
foreach ($skillsDir in $targets) {
    New-Item -ItemType Directory -Path $skillsDir -Force | Out-Null
    'md-unpack','md-triage','md-swarm','md-iterate','md-build' | ForEach-Object {
        $link = Join-Path $skillsDir $_
        if (Test-Path $link) { "skip (exists): $skillsDir\$_" }
        else { New-Item -ItemType Junction -Path $link -Target (Join-Path $repo $_) | Out-Null; "linked: $skillsDir\$_" }
    }
}
```

> Note: `md--develop` / `md--explain` are the author's private dev tools and are **not** part of this repo. `_md-shared` is a maintenance base, not a skill — do not link it.

### Step 3 — Install the global pandoc toolchain (once per machine, same for every agent)

```powershell
powershell -ExecutionPolicy Bypass -File (Join-Path $repo "md-build\setup_md_tools.ps1")
```

This downloads the pinned **pandoc 3.9.0.2 + pandoc-crossref 0.3.24a** into `%LOCALAPPDATA%\md-pandoc` (no admin, no PATH pollution) and copies the bundled Zotero Lua filters locally. Behind a firewall / in mainland China, add `-Mirror https://<a-github-mirror>`. If it prints "pandoc not found" later, this step didn't complete.

### Step 4 — Claude Code only: register the manuscript-protection hooks

```powershell
powershell -ExecutionPolicy Bypass -File (Join-Path $repo "md-unpack\setup_hooks.ps1")
```

This writes two PreToolUse hooks into `~/.claude/settings.json` (and patches cc-switch provider templates if present, so switching providers won't wipe them). The hooks physically block any AI tool call from directly overwriting `manuscript.md` — all AI edits must go through the safety-checked apply pipeline.

**Not using Claude Code?** Skip this step — hooks are a Claude Code feature and cannot exist in Codex / OpenCode / Hermes. The pipeline stays safe on **layer 1** (the scripts themselves: single-writer apply + citation gates), and the suite's `preflight.py` recognizes non-Claude-Code sessions (it prints a notice instead of blocking). Two things stand in for the hooks there:

1. The rules in [AGENTS.md](AGENTS.md) — Codex / OpenCode / Hermes read that file automatically as workspace instructions.
2. **Recommended:** copy it into the user's *paper project* root as well, so every future session there sees the rules:

```powershell
Copy-Item (Join-Path $repo 'AGENTS.md') 'D:\path\to\your-paper-project\AGENTS.md'   # adjust the destination
```

(If the paper project already has an `AGENTS.md`, merge the "Iron rules" section into it instead of overwriting.)

### Step 5 — Restart, then verify (do not skip)

**Claude Code:** hooks activate only in a **new** session (the harness snapshots hooks at session start).

1. Tell the user: **"Open a brand-new Claude Code conversation now"** (`/clear` does not reload hooks).
2. In the new session, verify:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $env:USERPROFILE ".claude\skills\md-swarm\verify_hooks.ps1")
```

Expect **6/6 green**. Any red → follow its on-screen hint (junction / registration / restart / BOM).

**Codex / OpenCode / Hermes / other agents:** no hooks to verify. Instead check:

```powershell
py (Join-Path $repo "md-swarm\preflight.py") --mode warn --context install-check   # expect the "non-Claude-Code session" notice + exit 0
Test-Path "$env:LOCALAPPDATA\md-pandoc"                                            # expect True (toolchain installed)
```

Then restart the agent (skills are usually scanned at session start) and ask it: *"What md-paper skills can you see?"* — it should list all five.

### Step 6 — Report

Tell the user, concisely: which skills were linked into which tool(s), that the toolchain installed, whether hooks were registered (Claude Code) or the AGENTS.md rules apply instead (other agents), and what verification to run per Step 5. Then point them at the README's "How to use" section: `md-unpack "path\to\paper.docx"` → revise → `md-build`.

---

## Manual install (if you'd rather not use an AI)

Run Steps 1–5 above yourself in PowerShell, substituting the cloned repo path for `$repo`. That's the entire install: **link 5 skills (into each tool you use) → `setup_md_tools.ps1` → (Claude Code) `setup_hooks.ps1` → restart → verify.**

## Per-tool notes

- **Claude Code** — the full experience: skills auto-load, plus the two protection hooks physically deny any stray direct write to `manuscript.md` (layer 2).
- **OpenCode** — reads `~/.claude/skills` natively, so the default Step 2 target is enough; zero extra configuration. No hook layer: [AGENTS.md](AGENTS.md) rules stand in.
- **Codex** — junction into `~/.codex/skills` (Step 2). Reads `AGENTS.md` automatically as workspace instructions.
- **Hermes Agent** — junction into `~/.hermes/skills` (Step 2). Compatible with the agentskills.io standard; supports `AGENTS.md` workspace instructions.
- **Any other agent** — the five `SKILL.md` files are plain Markdown runbooks and every pipeline step is an ordinary CLI script (`py md-swarm\apply_md_changeset.py …`, `powershell md-build\build.ps1 …`). Tell the agent to read the relevant `SKILL.md` and follow it.
- **Safety model everywhere**: the load-bearing protection is **layer 1, inside the scripts** — `apply_md_changeset.py` is the single writer of `manuscript.md`, with citation / uniqueness / order gates. Claude Code's hooks are an *additional* layer 2. Outside Claude Code you lose only layer 2; keep the [AGENTS.md](AGENTS.md) iron rules in front of the agent and never let it edit `manuscript.md` directly.
