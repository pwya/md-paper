---
name: md-unpack
description: 【第三代 md-* 套件·摄取动词·入口】把一篇 Word 原稿摄取成 pandoc 语法的 Markdown 真源 `manuscript.md`——Zotero 引文→`[@citekey]`、图→`![](){#fig:N}`、表→pipe 表`{#tbl:N}`、交叉引用→`[@fig:N]`、公式→`$..$`、脚注→`^[..]`，并把已有引用对账成你真实的 Better BibTeX citekey。这是 md-* 三阶段的第一步（`md-unpack → md-swarm → md-build`），对应第二代 `docx-unpack`，但产物是"可被 pandoc 编译成带活 Zotero 域 Word"的纯文本源。Use when 用户说"摄取原稿做 md 真源"/"把原稿转成 markdown 改"/"md-unpack"/"开始 md 工作流"。**这是 md-* 工作流的唯一入口：你只给原稿 docx 即可——若还没 manifest，本 skill 会自己在内部调 docx-unpack 先拆稿，再转成 md（用户无需另跑 /docx-unpack）。**
user-invocable: true
allowed-tools: [Read, Write, PowerShell, Bash, Glob, AskUserQuestion]
---

# md-unpack — 原稿 docx → pandoc Markdown 真源（第三代·摄取）

> ⛔ **路由铁律**：本 skill 属**第三代 `md-*`**（Markdown 真源 → pandoc 生成）。它产出的 `manuscript.md` 是后续所有改稿的**唯一真源**，最终由 `/md-build` 编译成 Word。要在既有 docx 上做带修订痕迹的外科小改，那是第二代 `docx-*`，别用本套。全套设计/对比/进度见 `../md-技能套件·开发手册.md`。

## 第 0 步 · 前置检查

- [ ] 工具链装了？（`%LOCALAPPDATA%\md-pandoc\pandoc.exe`）没有 → 先跑 `..\md-build\setup_md_tools.ps1`。
- [ ] 项目目录里有原稿 `.docx`。**不需要你手动先跑 docx-unpack**——`unpack.ps1` 发现没有 `manifest\` 时会**自己在内部调 docx-unpack**（Word COM 拆稿，复用它产的 objects.json 精确取引用/图表的 CSL 数据 + Zotero 条目 key），然后接着转成 pandoc md。已经有 `manifest\` 的话就直接复用、跳过这步。
  > 设计说明：摄取引擎（`ingest_manuscript.ps1`，Word COM 抽 Zotero 域代码/图表/脚注）**已内置在本 skill 目录里**（从 docx-unpack 复制而来）。因此 **md-* 套件完全不依赖任何 docx-* skill，可独立公开发布**——用户装了 md-unpack/md-triage/md-swarm/md-build 四个 + 全局工具链即可，无需装第二代。

## 第 1 步 · 摄取（一条命令）

```
powershell -ExecutionPolicy Bypass -File "<本 skill>\unpack.ps1" -WorkDir "<项目目录>" [-SourceDocx 原稿.docx] [-Title "<原稿真实标题>"]
```

> ⚠️ **`-Title` 一般不用给**——摄取时会**自动提取**原稿标题（先取 Word 文档属性 Title，取不到再退到正文第一段「标题」样式），写进 `objects.json.detectedTitle` 并填入 YAML。**只有自动没取到/取错时才手动 `-Title "真标题"`，且绝不照抄上面的占位词**（实测事故：agent 把示例 `"论文标题"` 当真值，YAML title 落成了 `"论文"`）。

它做两件事：① 用全局 pandoc 把原稿**直转**一份 `build\direct.md`（为了收割 OMML 公式的 LaTeX）；② 跑 `transform.py` 把 `manifest\manuscript.md`（占位符版）+ `objects.json` + `direct.md` 转成 pandoc 语法的 **`manuscript.md`**（写在项目根），并产出 `references.json`（临时 CSL 库）、`build\citemap.tsv`、复制图片到 `images\`。

产物自检：脚本打印 `unique citekeys / OMML harvested / xml-illegal ctrl stripped / prose md-escaped / residual markers`。**residual markers 理想为 0**（占位符全转干净）；行内 AxMath `[EQ-N]`、同行拼接 `[FIG-1][FIG-2]…`、OMML 数学字母占位（T21-2）与 front-matter 塌缩 FIG（T21-5）均已处理。⚠️ **但真稿上非 0 仍要当真**：① section 交叉引用 `[XREF-SEC-N]` 是删除止血（T21-4 轻量版），但 **2026-07-11 起删得响亮**：每处删除带 ±30 字上下文记进 `build\xref_sec_removed.md` + 控制台 WARN 点名——**跑完必须把这份账单转告作者**（校对时逐条看语句通顺、需要就手补字）；彻底活链接化仍未做（被真数据卡死：displayText 全空 + `_Ref*` 书签未收割 + 标题编号混排，详见手册 §10 T21-4）；② front-matter 塌缩图已路由到文末 `## (Unanchored figures)` 隔离段并生成 `{#fig:N}`，不再阻断出稿；**2026-07-11 起隔离段自带"名牌+地址条"**——真题注自动从 ingest 题注清单回填、每张图给出 `> 建议去处：『某节』（原稿约 N% 处）` 归位导航（读原稿 docx 定位、失败静默降级）——**跑完必须把"哪几张图、各自建议去哪节"转告作者**，作者只需照条子把图拖回该节；③ AxMath OLE 公式（少数）仍会留成 `$$\text{[TODO 重输 LaTeX]}$$` / 行内 `$\text{[TODO …]}$` 占位，公式内容需人工补（占位符本身已是干净数学、不计入 residual）。**计数器本身已修准**（T21-3：旧版漏报、报 4 真 22；新版如实计数）——所以非 0 时**别忽略，去 `grep` 看是哪几处**，详见手册 §10「T21」块。
> **字符安全（2026-06-26 加固·transform.py）**：出 `manuscript.md` 时自动 ① 用 XML 合法字符**白名单**剥掉所有非法控制字符（`xml-illegal ctrl stripped` 计数·根治"Word 打不开"，详见手册 §6 坑9）；② span-aware 转义 prose 里的 Markdown 元字符（`prose md-escaped` 计数·防 `$33`/`[3]` 等被 pandoc 误当公式/链接，详见 §6 坑10·**简单版**只覆盖正文）；③ **Tier-3 哨兵（2026-07-11）**盯转义网罩不到的三处（脚注体/表格单元格/图题）——只对实证确认会真误渲的形态（脚注 `]`、可配对 `$..$`、成对反引号、图题方括号）WARN 点名到具体对象，**实测安全的计量表 `_`/`*` 绝不报**；命中就按提示在 build 后核对那处、错了手动加反斜杠（AST 重构挂起，见手册 §10）。三项都是确定性后处理、对正常内容零误伤。

## 第 2 步 · 先问一次：要不要引用原文之外的新文献？（决定要不要对账 + 要不要外部引文文件）

`transform.py` 出来的引用是**临时 authorYear key**（如 `[@moynihan2015]`），和你 Zotero 里 Better BibTeX 的真 key 不一定一致。**但这只在你要加新文献时才需要处理。** 摄取完**必须用 AskUserQuestion 一次性给出下面这【三个】显式选项**（⛔ 别合并成"加/不加"两个、⛔ 别省掉"Zotero 开着现查"那个）：
> ① **不加新文献、只改原文已有内容** → 走 (A)，不对账、直接出稿。
> ② **要加新文献，且 Zotero 现在开着** → 走 (B1)，现查 Better BibTeX 拿真 key（首选）。
> ③ **要加新文献，但 Zotero 没开** → 走 (B2)，离线对账兜底。
>
> 下面 (A) / (B1) / (B2) 是这三个选项各自的执行细节：

- **(A) 不加新文献（只改原文已有内容）→ 不用任何外部引文文件，用文章本来的就够。**
  - 出活域：`/md-build -Mode rebuild`（用 ingest 时从 Word 抓下的 `objects.json` 离线重建，临时 key 不用对账）。
  - 只校对：`/md-build -Mode static`（自动用 md-unpack 产出的 `references.json` 当文献库，你不用导出任何东西）。
  - ⚠️ **选 (A) 的两条边界（真稿事故 2026-07-09·手册 §10 P1-5）**：① 本项目引用停留在**临时 authorYear 命名空间**，出稿**只能 rebuild，绝不尝试 live**（live 会全挂——好在 build 现有"钥匙试锁"硬闸会在门口拦下并给修法）；② 改稿期间**禁止新增引用**——写手契约已焊死"不许照猫画虎自造 key"，新增诉求一律落 `[@NEW:]` 占位；**确要真加新文献 → 先回头走 (B1/B2) 对账，再继续改稿**。
- **(B) 要加原文没有的新文献 → 这些新引用只活在你 Zotero 里，必须拿到真 key。** 两条路都已实现，按 Zotero 开没开选：
  - **B1·现查（Zotero 开着·首选）**——直接问运行中的 Better BibTeX 要真 key，免导出：
    ```
    py "<本 skill>\reconcile_live.py" --citemap "<项目>\build\citemap.tsv" --manuscript "<项目>\manuscript.md" [--library-id <群组库ID>]
    ```
    用 citemap 里的 Zotero item key 一次问全 BBT JSON-RPC（`item.citationkey`），把临时 key 全局换成真 key（备份 `manuscript_provisional.md`）。My Library **不传** `--library-id`（默认发裸 item key，BBT 在 My Library 里解析，这是常见情形）；只有引用在**群组库**、裸 key 解析不到时，才传那个群组的本地 libraryID。连不上 Zotero 会直接报错、不动稿子 → 改走 B2。
  - **B2·离线对账（Zotero 没开·兜底）**——用 Better BibTeX 把库（或本文集合）导成 **Better CSL JSON**（如 `library.json`），再跑：
    ```
    py "<本 skill>\reconcile_citekeys.py" --bbt-export "<项目>\library.json" --references "<项目>\references.json" --manuscript "<项目>\manuscript.md" [--manual manual.json]
    ```
    按 **DOI > 标题 > 作者+年** 换 key。报告 `build\citekey_reconcile_report.tsv`，没匹配的在 `build\citekey_unmatched.tsv`（多半该文献不在导出集合里——加进去重导，或用 `--manual {"prov":"real"}` 喂真 key）。

  对账完再 `/md-build -Mode live`。⚠️ **顺序铁律（B 路径）**：**先对账、后改稿**（md-triage/md-swarm/md-iterate 都排在对账之后）——别让写手在临时命名空间里作业，否则新旧两套 key 混住、rebuild/live 两头都难收拾（手册 §10 P1-5）。

## 第 3 步 · 报告 + 交棒

> 📋 **产物回显铁律（UX-1）**：跑完**必须**在回复里逐项回显每个产物的**绝对路径 + 用途 + 下一步**，别只说"已生成"。作者要能直接点开/复制路径，不用自己满目录找。

告诉用户（用绝对路径，例 `<项目目录>\manuscript.md`）：
- **`manuscript.md`**（★真源）已就绪——引用 N/M 解析、几条 AxMath 公式待补 LaTeX、几条引用待对账。**下一步**：校对 → `/md-build -Mode static`；手改一两处 → 直接编辑 `manuscript.md`；让 AI 改某一两处/VS Code 选区润色 → `/md-iterate`；一堆意见批量改 → `/md-triage` 整理成清单 → 确认 → `/md-swarm` 并行改稿。
- **`images\`**：图片已抽出，路径同上。新增图往这里放。
- **`manifest\`**：只读底稿（别动），`build\citemap.tsv` / `references.json` 供出稿/对账用。
- **`swarm\comments_raw.json`**（★仅当原稿带 Word 批注时才有）：摄取顺手抠出的批注（作者 + 批注文字 + 锚点原文）。批注是**修订意图**、不是正文——下一步交 `/md-triage` 理成清单（它会直接读这份 json，不再解析原 docx）。**提示用户**：这篇有 N 条批注、想按它改 → `/md-triage`。
- 几条 `[@NEW: …]` / `[TODO: AxMath ...]` 待人工补 → 列出来，指明在 `manuscript.md` 哪里。

## Markdown 语法约定（产物长这样·下游都按它）

`[@citekey]` 引用 · `[@fig:N]`/`[@tbl:N]`/`[@eq:N]` 交叉引用（crossref **冒号式**）· `![cap](images/x){#fig:N}` 图 · pipe 表 + `: cap {#tbl:N}` · `$..$`/`$$..$$ {#eq:N}` 公式 · `^[..]` 脚注。新增引用：库里有→`[@key]`；不确定→`[@NEW: 作者 年]`（别瞎编 key）。

## 关系

上游：只需原稿 docx（**无需**先跑 docx-unpack——`unpack.ps1` 内置 ingest，发现没 `manifest\` 会自动用 Word COM 拆稿）；下游 `md-swarm`（改 md）、`md-build`（出 Word）。核心脚本 `transform.py`（摄取转换）、`reconcile_live.py`（现查 Zotero 对账·首选）、`reconcile_citekeys.py`（离线对账兜底）、`read_docx_comments.py`（抠 Word 批注·与 md-triage 共用·纯 zip+XML）、`unpack.ps1`（编排）。全套见 `../md-技能套件·开发手册.md`。
