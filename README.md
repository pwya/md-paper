# md-paper

> **Word → Markdown → Word, for academic manuscripts.** Revise your paper with AI while keeping every Zotero citation, figure, table, cross-reference, and equation intact.
>
> **学术稿件的 Word → Markdown → Word 修订工作流。** 用 AI 改论文，同时让每一个 Zotero 引用、图、表、交叉引用、公式都完好无损。

A suite of [Claude Code](https://www.claude.com/product/claude-code) skills for the *last mile* from an AI-assisted draft to a submission-ready Word document.

> ⚠️ **Early public release.** The engine is battle-tested on real submissions, but this is the first release packaged for people other than the author. Please read [Known Limitations](#known-limitations--已知限制) before relying on it, and open an issue if anything breaks.
> ⚠️ **首个公开版。** 引擎已在真实投稿中反复验证，但这是第一次打包给作者以外的人用。依赖它之前请先读[已知限制](#known-limitations--已知限制)，出问题请提 issue。

---

## English

### Why md-paper

AI is excellent at rewriting prose — but the moment you paste its output back into Word, your **live Zotero citations turn into dead text**, figure numbers drift, and cross-references break. md-paper avoids this by making **Markdown the single source of truth**:

1. It **ingests** your Word manuscript into a plain-text Markdown file — citations become `[@citekey]`, figures `![](img){#fig:1}`, cross-references `[@fig:1]`, equations `$…$`, footnotes `^[…]`.
2. You **revise** that text — by hand, or with AI (organise reviewer comments, batch-revise, or polish one passage).
3. It **compiles** back to Word with **live Zotero fields** (refreshable in Word), automatic figure/table/equation numbering, working cross-references, and your own fonts/heading styles — via [pandoc](https://pandoc.org) + [pandoc-crossref](https://github.com/lierdakil/pandoc-crossref) + [Better BibTeX](https://retorque.re/zotero-better-bibtex/).

Because the citations live as `[@key]` text and are resolved by pandoc/Zotero at compile time, **AI can rewrite freely without ever touching a citation** — and a built-in safety net (single-writer edits, "never drop a citation" gate, fabricated-citation guard) protects the manuscript throughout.

### The pipeline — five skills

| Skill | What it does |
|---|---|
| **`md-unpack`** | Ingest a Word `.docx` → a pandoc-Markdown truth source `manuscript.md` (+ extracts figures, citation data, comments). |
| **`md-triage`** | Turn *any* pile of revision intents — reviewer comments, a supervisor's email, meeting notes, a PDF, an image — into a clean, discrete, human-reviewed checklist. |
| **`md-swarm`** | Batch-revise the manuscript with parallel AI agents. Drafting is parallel; writing to disk is serial and citation-safe. |
| **`md-iterate`** | Polish one selected passage/paragraph with AI (VS Code selection friendly). |
| **`md-build`** | Compile `manuscript.md` back to Word — `live` (query Zotero), `rebuild` (offline live fields), or `static` (proofing). |

**Typical flow:** `/md-unpack paper.docx` → revise (hand-edit / `/md-iterate` / `/md-triage` + `/md-swarm`) → `/md-build`.

### Requirements

- **Windows + Microsoft Word** — ingest reads Word citation fields, figures and footnotes via Word COM. *(macOS ingest not yet supported — see Known Limitations. Compilation itself is cross-platform.)*
- **Python 3** on `PATH` (`py` or `python`).
- **PowerShell** (5.1, built into Windows, is enough).
- **pandoc toolchain** — installed once by `setup_md_tools.ps1` (downloads the pinned pandoc 3.9.0.2 + pandoc-crossref 0.3.24a; has a mirror fallback for restricted networks; no admin needed).
- **Zotero + Better BibTeX** — *optional*, only for `-Mode live` (resolving references you add during revision). Original references work fully offline via `-Mode rebuild`.

### Install

```powershell
# 1. Clone
git clone https://github.com/pwya/md-paper.git
cd md-paper

# 2. Make the five skills discoverable by Claude Code (junction them into ~/.claude/skills).
#    Junctions need no admin and no developer mode.
$repo = (Get-Location).Path
'md-unpack','md-triage','md-swarm','md-iterate','md-build' | ForEach-Object {
  New-Item -ItemType Junction -Path "$env:USERPROFILE\.claude\skills\$_" -Target "$repo\$_" -Force
}

# 3. Install the global pandoc toolchain (once per machine).
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.claude\skills\md-build\setup_md_tools.ps1"
#   Behind a firewall / in mainland China:  add  -Mirror https://<your-mirror>

# 4. (Recommended) Register the manuscript-protection hooks, then restart Claude Code and verify.
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.claude\skills\md-unpack\setup_hooks.ps1"
#   ... restart Claude Code (new conversation) ...
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.claude\skills\md-swarm\verify_hooks.ps1"   # expect 6/6 green
```

The protection hooks are optional but recommended: they physically stop any AI tool call from directly overwriting your `manuscript.md` (all AI edits must go through the safety-checked apply pipeline).

### Quick start

```
/md-unpack "C:\path\to\paper.docx"     # → manuscript.md
# ...revise manuscript.md (hand-edit, or /md-iterate, or /md-triage + /md-swarm)...
/md-build                               # → build/out_*.docx  (asks your page-format once, remembers it)
```

For `live`/`rebuild` output, open the resulting `.docx`, then in Word: **Zotero → Document Preferences → OK → Refresh** to activate citations and the bibliography.

### Known Limitations · 已知限制

This is an honest list of what the current release does *not* do smoothly. None causes silent data loss (there are loud warnings), but you should know them:

1. **Ingest is Windows + Word only.** `md-unpack` drives Microsoft Word via COM. macOS is not supported yet. (Everything after ingest — triage, revision, compilation — is cross-platform.)
2. **Citations must be Zotero + Better BibTeX.** Word documents using EndNote, Citavi, or hand-typed citations are **not** supported by the citation pipeline; ingest will not recover them as live fields.
3. **`rebuild` mode & page locators.** If the same source is cited at different pages (`[@a, p.5]` vs `[@a, p.99]`), offline `rebuild` may give both the same page number. Use `live` mode, or check those citations by hand. (Open issue.)
4. **Escaping in footnotes / table cells / figure captions.** The prose Markdown-escaper fully covers body text; for those three injection points a *sentinel* warns you about risky characters but does not auto-fix them — check the flagged spots after building.
5. **Collapsed floating figures.** A few floating/grouped figures can land at the document front during ingest; they are routed to an "unanchored figures — please place manually" section with their real caption and a suggested home section restored, for you to drag into place.
6. **Old AxMath equations.** Legacy AxMath OLE equations are left as `[TODO: re-enter LaTeX]` placeholders (with a preview image) for you to fill in. Native Word (OMML) equations convert automatically.

### License & credits

- **Your workflow code:** [Apache-2.0](LICENSE) © 2026 Yuang Panwang (潘王雨昂).
- **Third-party:** pandoc & pandoc-crossref (GPL-2.0) are *downloaded at setup*, not redistributed here; the bundled Zotero/Lua filters are MIT. Full attribution in [NOTICE](NOTICE) and `md-build/bundled-lua/THIRD_PARTY_NOTICES.md`.

Built as a set of skills for Claude Code. Citations powered by [Zotero](https://www.zotero.org) + [Better BibTeX](https://retorque.re/zotero-better-bibtex/); typesetting by [pandoc](https://pandoc.org).

---

## 中文说明

### 为什么需要 md-paper

AI 很擅长改文字——但你一把它的结果粘回 Word，**活的 Zotero 引用就变成死文字**，图号错乱，交叉引用断裂。md-paper 让 **Markdown 成为唯一真源**，绕开这个问题：

1. **摄取**：把 Word 原稿转成纯文本 Markdown——引用变 `[@citekey]`、图变 `![](img){#fig:1}`、交叉引用变 `[@fig:1]`、公式 `$…$`、脚注 `^[…]`。
2. **修订**：改这份纯文本——手改，或用 AI（整理审稿意见、批量改稿、润色单段）。
3. **编译**：用 pandoc + crossref + Better BibTeX 编回 Word，带**活 Zotero 引用域**（可在 Word 里 Refresh）、图/表/公式自动编号、可用的交叉引用、你自己的字体与标题样式。

因为引用始终以 `[@key]` 纯文本存在、由 pandoc/Zotero 在编译期解析，**AI 改稿全程碰不到引用本身**；再加上内置安全网（单写者落盘、"默认绝不删引用"硬闸、防编造引用），改稿全程有保护。

### 五个技能

| 技能 | 作用 |
|---|---|
| **`md-unpack`** | Word `.docx` → pandoc-Markdown 真源 `manuscript.md`（并抽出图、引用数据、批注）。 |
| **`md-triage`** | 把**任意形式**的修订意图（审稿意见 / 导师邮件 / 会议记录 / PDF / 图片）归一成一份可人工复核的离散清单。 |
| **`md-swarm`** | 多 AI agent **并行**批量改稿；起草并行、落盘串行且引用安全。 |
| **`md-iterate`** | 用 AI 润色你指定的一段（支持 VS Code 选区）。 |
| **`md-build`** | 把 `manuscript.md` 编回 Word——`live`（现查 Zotero）/ `rebuild`（离线活域）/ `static`（校对）。 |

**典型流程：** `/md-unpack 论文.docx` → 改稿（手改 / `/md-iterate` / `/md-triage` + `/md-swarm`）→ `/md-build`。

### 环境要求

- **Windows + Microsoft Word**——摄取靠 Word COM 读引用域/图/脚注。*（macOS 摄取暂不支持，见已知限制；摄取之后的所有步骤跨平台。）*
- **Python 3**（`py` 或 `python` 在 PATH 上）、**PowerShell**（Windows 自带 5.1 即可）。
- **pandoc 工具链**——由 `setup_md_tools.ps1` 一次装好（锁定版 pandoc 3.9.0.2 + crossref 0.3.24a，带镜像兜底、免管理员）。
- **Zotero + Better BibTeX**——*可选*，只有 `-Mode live`（解析改稿时新加的文献）才需要；原有引用用 `-Mode rebuild` 完全离线可用。

安装步骤见上面英文 **Install** 节的 PowerShell（国内网络给 `setup_md_tools.ps1` 加 `-Mirror` 参数）。已知限制见上面 **Known Limitations**（六条，逐条中文对应：① 摄取仅 Windows+Word ② 引用仅支持 Zotero+Better BibTeX ③ rebuild 同文献多页码可能取同一页 ④ 脚注/表格/图题的转义只警告不自动修 ⑤ 塌缩浮动图路由到"请手动放置"段 ⑥ 老 AxMath 公式留 TODO 待手补）。

### 许可与致谢

工作流代码 [Apache-2.0](LICENSE) © 2026 潘王雨昂 (Yuang Panwang)。第三方：pandoc / pandoc-crossref（GPL-2.0）安装时下载、不随仓库分发；内置 Zotero/Lua 过滤器为 MIT。完整致谢见 [NOTICE](NOTICE)。
