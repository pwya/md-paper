---
name: md-build
description: 【第三代 md-* 套件·出稿动词】把 Markdown 真源 `manuscript.md` 用 pandoc 编译成 Word——出**带活 Zotero 引用域**（可在 Word 里 Zotero Refresh）+ 图表自动编号 + 交叉引用 + 公式 + 期刊样式的 docx。这是 md-* 三阶段的最后一步（`md-unpack → md-swarm → md-build`），对应第二代的 `docx-commit`。两种产物：live（活 Zotero 域，需 Zotero 开着）、static（烤死引用+自动文献表，校对用，不需 Zotero）。工具链是**全局安装**（`%LOCALAPPDATA%\md-pandoc`，由 setup_md_tools.ps1 一次装好），不按项目捆绑。Use when 用户说"出稿/生成 Word/编译 md/build/出活域稿/md-build"，或在 md 改完后要落地成 Word。**注意：这是从 md 全新生成，不是改既有 docx；要改既有 docx 走第二代 docx-commit。**
user-invocable: true
allowed-tools: [Read, Write, PowerShell, Bash, Glob]
---

# md-build — 把 manuscript.md 编译成 Word（第三代·出稿）

> ⛔ **路由铁律**：本 skill 属**第三代 `md-*`**（Markdown 真源 → pandoc 生成）。它**全新生成** docx，**不**碰既有 docx、**不**走修订模式。若用户要"在既有期刊稿上做带修订痕迹的外科小改"，那是第二代 `docx-commit` 的活，别用本 skill。完整链路：`/md-unpack 原稿`（出 manuscript.md）→ 多 agent `/md-swarm` 改 → **`/md-build`（本 skill）出 Word**。
> 全套设计/对比/进度见 `../md-技能套件·开发手册.md`。

## 第 0 步 · 工具链（全局安装·只装一次）

本 skill 依赖**全局工具链**（不按项目捆绑）：`%LOCALAPPDATA%\md-pandoc\` 下的
**pandoc 3.9.0.2 + pandoc-crossref 0.3.24a + zotero 活引用 lua 过滤器全套依赖**。

- **没装过 / 报 "pandoc not found"** → 跑一次：
  ```
  powershell -ExecutionPolicy Bypass -File "<本 skill>\setup_md_tools.ps1"
  ```
  它会把全部工具装进 `%LOCALAPPDATA%\md-pandoc`（机器本地、不进 OneDrive、不污染 PATH）。`-Force` 重装。
- **安装鲁棒性（面向公开发布·尽量少折腾用户）**：① **复用**——若本机 PATH 上已有 pandoc **3.9.0.2**，直接拿来用、不重下；版本不符则忽略并提示原因（不会误用 winget 装的最新版）。② **内置 lua**——那套最易凑不齐的 zotero 活引用 lua 依赖已随 skill 打包在 `bundled-lua\`，安装时**本地拷贝、不联网**。③ **下载兜底**——大二进制（pandoc/crossref）走 GitHub，失败自动试镜像；全失败则打印**手动下载步骤** + `$env:MD_PANDOC_HOME` / `$env:MD_GH_MIRROR` 逃生口，绝不静默失败。
- **GitHub 被墙/慢**（国内常见）→ `setup_md_tools.ps1 -Mirror https://<你的镜像>`，或先设 `$env:MD_GH_MIRROR`。
- 装到别处了 → 设 `$env:MD_PANDOC_HOME` 指过去（build.ps1 也读它）。
- ⚠️ **版本锁死**：pandoc 必须 **3.9.0.2**（crossref 0.3.24a 是对它编译的）。**别 `winget install pandoc`**（装的是最新版，crossref 会静默失灵）。要升级必须同时换配套 crossref——setup 脚本顶部 `$PANDOC_VER` / `$CROSSREF_TAG` 两处一起改。

## 第 0.5 步 · 排版规格（每个项目**只问一次**·2026-07-11）

> 机制：问询的答案落成**项目马甲** `<项目目录>\reference.docx`——build.ps1 出稿时**自动穿**（无需每次传参）。所以**有这个文件就绝不再问**；用户想换排版时说一声（重新问询覆盖生成）或自己删掉该文件（回落 pandoc 默认）。

1. **先查** `<项目目录>\reference.docx`：**在 → 跳过本步**（出稿回显时带一句"本次穿的马甲：项目 reference.docx"）；用户显式给了 `-Reference` → 也跳过。
2. **不在 → AskUserQuestion 问一次**（三个显式选项，⛔ 别省）：
   > ① **默认中文规格（推荐·现成马甲）**——正文 宋体·小四·1.5 倍行距；章节标题 三号黑体加粗；小节 四号黑体不加粗；图表题注 小四·1.5 倍·居中；**全文英文/数字统一 Times New Roman**（含标题里的英文——Word 每个样式自带"中文槽+西文槽"两个字体位，中英文自动各走各的，无需分段设置）；表格不动。
   > ② **自定义**——追问四件（只这四件，**摘要/致谢等特殊件不管**，那是 Word 里手调的活）：正文（**中文字体**/字号/行距）、章节标题（**中文字体**/字号/粗否）、小节标题（同）、题注（字号/居中否）；**西文字体单独问一次、全文统一**（默认 Times New Roman，对应 `--body-latin`，标题/正文/题注共用）。
   > ③ **不排版**——pandoc 默认样式，回头自己在 Word 里调。
3. **落地**：① → 把本 skill 的 `reference-cn.docx` **复制**为 `<项目目录>\reference.docx`；② → 跑
   ```
   py "<本 skill>\make_reference_cn.py" --out "<项目目录>\reference.docx" [--body-cn 宋体 --body-pt 12 --line 1.5 --chapter-pt 16 --section-pt 14 --caption-pt 12 ...]
   ```
   （字号速查：三号=16pt·四号=14·小四=12·五号=10.5；参数全表 `--help`；脚本自带 10 项自检，FAIL 会报清楚）；③ → 什么都不放。
4. **档位映射注意**（别搞反）：论文**章节**=md `##`=Word Heading2，**小节**=`###`=Heading3——生成器已按此约定挂衣服，`--chapter-*` 就是问用户的"一级标题"、`--section-*` 就是"二级标题"。

## 第 1 步 · 出稿（一条命令）

```
powershell -ExecutionPolicy Bypass -File "<本 skill>\build.ps1" -WorkDir "<项目目录>" -Mode <live|rebuild|static>
```

`<项目目录>` = 含 `manuscript.md` 的目录（md-unpack 的产物所在）。产物落在 `<项目目录>\build\out_<mode>.docx`。

> ⭐ **选 mode 一句话**：**没加新文献 → `rebuild`**（原有引用离线重建活域，不用开 Zotero，这是 md-unpack 稿子的默认正解）；**加了新文献 → 对账后 `live`**（现查 Zotero）；**只想校对正文/图表 → `static`**。⚠️ 直接对 md-unpack 的稿子跑 `live` 会全挂——因为它的 citekey 是临时 authorYear，不是 Zotero 真 key。`rebuild` 是**已实现**的正经 mode（见下表 + `build.ps1` 内 `-Mode rebuild` 分支），不是占位功能。

| Mode | 干什么 | 前置 | 用途 |
|---|---|---|---|
| **live** | 把 `[@citekey]` 现查 Zotero 建成**活引用域** | **Zotero 开着 + 装了 Better BibTeX**；citekey 在库里真实存在 | 最终出稿（含改稿**新增**的引用） |
| **rebuild**（★默认·推荐） | 用 ingest 时从 Word 抓下的引用数据（`objects.json` 的 fieldCode）**离线重建活 Zotero 域**——不开 Zotero、不对账 citekey | 该项目 md-unpack 过（有 `manifest\objects.json` + `build\citemap.tsv`） | **原有引用**要活域、又不想开 Zotero/不想对账。新增的引用不在 map 里 → 留在原地并报出来（那些得走 live） |
| **static** | 引用烤成文字 + 从 `-Bib`(默认 `library.json`) 生成文献表 | 一份 CSL-JSON 文献库（无则用 `references.json`） | 不开 Zotero 时校对正文/图表/交叉引用 |
| smoke | 同 live 但跑 `smoke.md`（2 个 citekey） | 同 live | 验活域机制是否通 |

> **live vs rebuild（都出"活 Zotero 域"，区别在数据来源）**：`rebuild` 把 ingest 时从 Word 域代码里抓下、存进 `objects.json` 的**原始 CSL+条目 key 离线拼回活域**——原有引用免开 Zotero、免对账。`live` 则现查运行中的 Zotero——能解析**改稿时新增**的、原稿里没有的引用。决策：**出稿模式必须显式选（建议用 AskUserQuestion 问用户）；用户没指定就默认 rebuild（活域·免开 Zotero）**。不加新文献 → rebuild；要加新文献 → 对账后走 live。⛔ **绝不默认 static**——static 把引用烤成**死文字**、只供离线校对，**不是最终稿**；要"活 Zotero 域"必须走 rebuild 或 live。md-unpack 跑完会打印这个分叉提示。

可选：`-Out <路径>` 自定义输出；`-Reference <reference.docx>` 套期刊样式模板——**本 skill 自带一件中文规格样式马甲 `reference-cn.docx`（2026-07-11）**：正文 宋体/Times New Roman·小四·1.5 倍行距，章节标题（md `##`→Word Heading2）三号黑体加粗、小节（`###`→Heading3）四号黑体不加粗，图/表题注 小四·1.5 倍·居中，表格故意不动（作者规格）。用法两种：**项目目录放 `reference.docx` 会被自动穿**（首选·由第 0.5 步问询落地，逐次出稿零参数），或显式 `-Reference <路径>`（优先级更高）。⚠️ **标题档位按套件约定映射**：论文章节=md 二级 `##`=Heading2（Heading1 通常无人穿、已同样式兜底）。要改规格：`make_reference_cn.py` 带参数重跑（`--help` 全表·自带自检），别手改 docx。；`-SkipPreflight` 跳过下面的 Zotero 探活（仅在你确知 Zotero 已就绪、但探活端点被禁用时用）；`-SkipConservation` 跳过出稿后的「图/表/公式数量守恒」硬闸（仅在确知守恒闸误报时用）。

> **live/smoke 出稿前自动「Zotero 探活」**：脚本会先探一下 `127.0.0.1:23119` 上的 Zotero + Better BibTeX。连不上就**直接中止**（不会闷头产出一份引用全废的 docx），并打印分诊清单：① Zotero 没开？② 没装/没启用 Better BibTeX？③ 代理（Clash 7890 / TUN）在拦 localhost？④ 端口被改？想离线先校对就 `-Mode static`。
> **探活通过后还有「钥匙试锁」（2026-07-11·治真稿事故·手册 §10 P1-5）**：门开着 ≠ 钥匙配对——脚本会把全稿 citekey 真问一遍 Better BibTeX：**0 个能解析 = 临时 authorYear 命名空间**（md-unpack 选 (A) 从没对账），**当场中止**并打印两条修法（补跑 `reconcile_live.py` 后再 live / 不加新文献就改 `-Mode rebuild`）——绝不产出一份 56 个 "not found" 的稿让你满头雾水；**部分**解析不到只逐个点名放行（拼错 / `[@NEW:]` 残留 / 真新加没入库）。`-SkipPreflight` 连探活带试锁一并跳过。

## 第 2 步 · Word 里收尾（仅 live/smoke）

打开 `out_live.docx` → Zotero 文档首选项点 OK → **Refresh** → 占位文字 `<Do Zotero Refresh: …>` 变成正式引用、自动生成文献表。

## 已固化进脚本的坑（你不用管，但要知道）

- **本机代理吞 localhost**：脚本在 live/smoke 自动设 `NO_PROXY=127.0.0.1,localhost`——否则到 Zotero（`127.0.0.1:23119`）的请求被 Clash 类代理转成 502。出稿前还有一道 **Zotero 探活**（探针自带 `Proxy=$null` 绕过系统代理），连不上就提前中止报清单；跑完若日志里出现连接类错误（refused/timeout/502）也会再提醒一次。
- **filter 顺序**：crossref 永远在 citeproc/zotero.lua **之前**（先消化 `@fig:`/`@tbl:`，剩下才是真引用）。脚本已固定。
- **Word 锁文件**：若 `out_live.docx` 正被 Word 打开，pandoc 覆盖会 "permission denied"——关掉 Word 或 `-Out` 换名。
- **Markdown 语法**：交叉引用用 crossref 冒号式 `[@fig:1]` `{#fig:1}`（不是 Quarto 连字符）；引用 `[@citekey]`；新表 `{#tbl:label}`；公式 `$$…$$ {#eq:label}`。
- **出稿后 XML 良构硬闸（postflight · 2026-06-26 加固）**：pandoc 出稿后自动解包验 `word/document.xml` 是不是良构 XML——若混进 XML 非法控制字符（多来自 AxMath/域等对象，肉眼不可见，正是会让 Word"文件已损坏"打不开的根因），**当场 throw 中止**并给精确 `U+XXXX` 诊断，绝不产出一个打不开的 docx。正常稿无感放行（日志打 `[postflight] ... well-formed`）。详见手册 §6 坑9。
- **出稿后「图/表/公式/引用数量守恒」检查（postflight · 只警告不阻断 · 2026-06-28 加）**：XML 良构闸证明 docx 能**打开**，这道检查提示它**完整不完整**。pandoc 会把一张图/表/公式悄悄丢掉还照样 exit 0（典型：图片文件缺失、或冷门 LaTeX 公式被降级成纯文字），你只能拿到一份"少了张图但能打开"的稿、肉眼校对才发现。`verify_conservation.py` 数**源 pandoc AST** 里的 Image/Table/Math vs **产物 `document.xml`** 里的 `pic:pic`/`w:tbl`/`m:oMath` 个数,不一致就**大声 WARN**（产物<源="某个图/表/公式没进 docx,去看看"；产物>源="多了,多半 reference-doc 模板对象"）。**两边都来自改后的同一份 `manuscript.md`**（不是拿原稿比）——所以你改稿时主动删图删表**两边一起少、永不触发**;只有 pandoc 编译时自己弄丢/复制才报。**只警告、永不阻断出稿**（一个渲染瑕疵不该把整篇 docx 毙掉,作者自己看着办;`exit 0`、docx 照出）。引用同为 WARN（live/rebuild 比 `ZOTERO_ITEM` 域数；rebuild 里新加引用正常落空、static 烤死引用故跳过)——引用另有 verify_refs/verify_citekeys + 'not found' 多层覆盖。正常稿无感放行（`[postflight] conservation ... OK`）。`-SkipConservation` 整步跳过。详见手册 §10 P3「验证网」。

## 出稿前·源健康自检（可选但推荐）

本 skill **只读** `manuscript.md`（pandoc 默认按 UTF-8 读），不会写坏它；但**若上游改稿把源写成了 mojibake**（中文 Windows 上用脚本拼接的典型事故），build 会把乱码原样带进 docx。出稿前可快速体检：
- 乱码：源里若出现私用区字符（U+E000–F8FF）或"鍥/瀹/锛"这类怪字＝多半被按 cp936 误读过，先回上游用 Write/Edit 修，别带病出稿。
- 引用/交叉引用：跑 `py "..\md-swarm\verify_refs.py" --current manuscript.md`，确定性列出无定义的 `[@fig:/@tbl:/@eq:]` 与 NEW 占位符。

## 报告

> 📋 **产物回显铁律（UX-1）**：出稿完**必须**回显产物的**绝对路径 + 用途 + 下一步**，别只说"已出"。作者要能直接点开。

- **`<项目目录>\build\out_<mode>.docx`**——这就是成品。**下一步**：live/rebuild 模式 → 在 Word 里 `Zotero 文档首选项 → OK → Refresh`，占位文字变成正式引用、文献表自动生成；static 模式 → 直接校对正文/图表/编号。
- 读 `build\pandoc_err_<mode>.txt`：若有 `not found` 警告，列出是哪些 citekey/标签没解析（多半是该文献不在 Zotero / citekey 写错 / `@fig:` 标签不存在），转达用户。
- **排错速查（引用类·2026-07-11 自真稿事故提炼）**：① **live 全部 not found 但 Zotero 明明开着** = citekey 不是真 BBT key（临时命名空间）→ 补跑 `reconcile_live.py`（现有"钥匙试锁"闸通常已在门口拦下）；② **rebuild 报 `unmatched key-sets`** = 那几组引用不在离线桥上，三种可能：改稿**新增**的引用（对账后走 live）/ citekey **拼错**（手改）/ **既有引用组被重组**（rebuild 按"整组"桥接，组成员变了桥就断——写手契约本禁止拆组重排，出现即回查是哪条 patch 干的）。
- **公式转换失败警告（`Could not convert TeX math`）**：pandoc 啃不动某条 LaTeX 时会把它**降级成纯文字**（公式还在、但不是真正的 Word 公式域，pandoc 仍 exit 0）。build.ps1 已自动数这条警告并大声 WARN（`N 个公式没转成 Word 公式…`）+ 指到 err 文件里的**具体 LaTeX**。**这也正是 postflight 数量守恒里"formula 少了 N 个"的真因**（不是丢了、是没转成公式域）。转达用户：去 err 文件搜 `Could not convert TeX math` 看是哪条、在 `manuscript.md` 里修对那条 LaTeX 再重出。
- 若报告 `N 个 [@NEW: ...]` 未替换或 AxMath TODO 残留 → 列出来指明位置，让作者补真 key / LaTeX 后重出。
- `pandoc exit 0` + 有输出 = 成功。

## 关系

上游 `md-unpack`（出 manuscript.md）、`md-swarm`（改 md）；本 skill 是终点。工具链 setup 见 `setup_md_tools.ps1`；全套见 `../md-技能套件·开发手册.md`。
