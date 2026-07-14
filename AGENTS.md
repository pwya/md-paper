# AGENTS.md — md-paper rules for AI agents · 跨工具代理守则

> 中文速览:这是 md-paper 给【任何】AI 编程代理(Codex / OpenCode / Hermes Agent / Claude Code / …)的工作守则。Claude Code 有成熟保护钩子，Codex / OpenCode 可安装兼容适配层；**无论钩子是否存在，下面铁律始终是第一层约束**。

## What this repo is

md-paper revises Word manuscripts through a Markdown source of truth (`manuscript.md`), in five stages — each a standard Agent Skill (`<skill>/SKILL.md`, [agentskills.io](https://agentskills.io)-compatible):

`md-unpack` (docx → manuscript.md) → `md-triage` (any revision intent → human-confirmed checklist) → `md-swarm` (batch revision) / `md-iterate` (single-spot revision) → `md-build` (manuscript.md → Word with live Zotero fields).

- **Opened this repo to install?** Follow [INSTALL.md](INSTALL.md) step by step — it covers Claude Code, Codex, OpenCode, Hermes and generic agents.
- **Working on a paper project that uses md-paper?** The rules below are binding in every session.

## Iron rules — binding even when a harness hook is unavailable or incomplete

1. **Never write `manuscript.md` directly.** Not with a file-edit tool, not with a script (`open(...,'w')`, `Set-Content`, `Out-File`, …). The ONLY writer is `md-swarm/apply_md_changeset.py` (`--dry-run` → apply → verify). In Claude Code a hook physically denies stray writes; in your harness nothing will stop you — so don't. (Measured incident: parallel direct writes left 1 surviving section and silently lost 38 citations.)
2. **Citations are load-bearing.** Never delete or rewrite `[@citekey]` marks while editing prose — the apply gate refuses patches that drop citations; don't route around it. Never invent new keys that imitate existing ones: a manuscript full of `[@authorYear]`-looking keys may be an unreconciled *provisional* namespace, and imitation = fabrication.
3. **The human gate is human-only.** `swarm/md_triage.md` carries the token `**人工确认：** 待确认`. Only the human flips it to `已确认`. Never flip or paraphrase it yourself.
4. **Each stage's `SKILL.md` is the authoritative runbook.** Read it in full before running that stage; call the pipeline scripts exactly as written there — don't improvise replacements.
5. **Windows + PowerShell is the assumed environment** (ingest needs Word COM). Encoding: `.py` / `.md` = UTF-8 no BOM, LF; `.ps1` containing Chinese = UTF-8 **with** BOM.

## Harness notes

- **Skills discovery**: Codex reads `~/.codex/skills`; OpenCode natively reads `~/.claude/skills` (and `~/.config/opencode/skills`); Hermes Agent reads `~/.hermes/skills`. No skills mechanism at all? Just open `<skill>/SKILL.md` and follow it — they are plain Markdown runbooks. Install/linking: [INSTALL.md](INSTALL.md) Step 2.
- **No parallel sub-agent tool?** Run md-swarm's drafting phase serially — one entry at a time, same patch contract, each draft still writes only its own `swarm/patches/*.json`. Collection / apply / verify are deterministic scripts and don't care.
- **preflight**: `md-swarm/preflight.py` currently verifies Claude registration only. In Codex/OpenCode it still prints the older layer-1 notice even when the compatible adapter is installed; treat layer 1 as authoritative until cross-harness live-probe integration lands.
- **Recommended**: copy this `AGENTS.md` into your paper project's root (or merge the Iron rules into the project's existing `AGENTS.md`), so every future session in that project sees these rules automatically.
