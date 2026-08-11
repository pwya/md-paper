# 更新日志 · Changelog

本文件记录 md-paper 每个版本的显著变化，中文为主、每节配一行英文摘要。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。
*This file tracks notable changes (Chinese first; a one-line English summary per section), loosely following Keep a Changelog.*

---

## [未发版 / Unreleased]
### 出稿自动排版：图表上下空行 + 引用前缀中英 + 标题全黑

*(EN: post-build layout pass -- blank lines around figure/table blocks, de-indented notes, automatic Figure/Table vs 图/表 reference prefixes, all headings black.)*

- 新增出稿后处理 `postprocess_layout.py`（带 `--selftest`）：每个图块（图→图题）和表块（表题→表→注释）前后插入**真实空段落**，形成 `空行→题注→对象→注释→空行` 版式；注释段自动去掉首行缩进。注释识别用"紧邻块 + 段首白名单"（`Note:`/`Source:`/`Data source:`/`Figure note:`/`Table note:` 与 `注：`/`资料来源：`/`数据来源：`/`图注：`/`表注：` 等，大小写不敏感），`Note that ...` 不误判。
- 新增 `dedup_crossref_prefix.lua` + `build.ps1` 语言检测：按正文中文字符占比自动判定，英文稿引用渲染为 `Figure 1`/`Table 1`，中文稿为 `图 1`/`表 1`；同时自动去掉正文手打的重复前缀（`Figure [@fig:1]` → `Figure 1`），修掉 `Figure fig. 1` 类重复。只改出稿产物，`manuscript.md` 一个字不动（单写者铁律）。
- `make_reference_cn.py`：**所有标题（Heading1–Heading9）统一黑色**（清除 pandoc 默认的蓝色/灰色主题色），自检新增 9 项颜色校验；`reference-cn.docx` 同步重新生成。
- `build.ps1` 接线：pandoc 参数加 `dedup_crossref_prefix.lua`（在 crossref 之前）+ 按语言生成 `build/md_crossref_meta.yaml`（`--metadata-file`），出稿后跑 `postprocess_layout.py`，再走既有 XML 良构闸与数量守恒闸。
- 已知边界：Word 原生"交叉引用"对话框引用图/表仍不可用（pandoc 图题/表题不是 Word 题注域）；正文请用 `[@fig:1]`/`[@tbl:1]` 交叉引用。

### 默认中文排版升级：三线表 + 段落/题注细节

*(EN: upgrade the default Chinese layout — three-line tables, body paragraph spacing/indent, single-spaced captions.)*

- `md-build` 默认中文马甲（`reference-cn.docx`）升级：正文样式补**段前段后 0、首行缩进 2 字符**；章节标题/小节保持原规格；图/表题注改**单倍行距·段前段后 0·无首行缩进·居中**。
- 普通表格默认套**三线表**（上/下 1.5pt、表头下 0.75pt、无竖线）并按内容自动调整列宽——不再"表格不动"。
- `make_reference_cn.py` 相应扩展：设置 `Normal`/`BodyText`/`FirstParagraph` 的段落间距与首行缩进、题注单倍行距无缩进、三线表边框 + 表头底线，并把新增项纳入确定性自检（新增 10+ 项校验）。
- 内置 `reference-cn.docx` 已同步重新生成（2026-08-10）。

## [v0.11.2] - 2026-07-28

### Codex Hook 调度回归：响亮停机 + 文档勘误

*(EN: fail loudly on the upstream Codex hook-dispatch regression; correct v0.11.1's overstatement and stop reinstall/restart loops.)*

- current-session live-probe 失败时明确硬停写入型 `md-swarm` / `md-iterate`，并链接 OpenAI 上游回归 [openai/codex#21639](https://github.com/openai/codex/issues/21639)。
- Codex 已显示 Active/Trusted 但真实动作仍落盘时，不再循环建议重装、Trust、重启；改为切换到已通过双 DENY 的宿主。
- `probe_live_hooks.py --selftest` 新增失败指引回归断言，防止未来重新引入“重启循环”。
- README、INSTALL、用户手册与 v0.11.1 发布说明同步澄清：v0.11.1 修复的是事件形状适配，不能修复宿主未调度 Hook 的上游故障。

## [v0.11.1] - 2026-07-28

### Codex Desktop 工具形状兼容 + Claude runner 加固

*(EN: recognize Codex Desktop's apply_patch/shell_command payload shapes when the host dispatches hooks, while preserving Claude Code's PowerShell hook layer through the shell-neutral runner.)*

> **2026-07-28 勘误：** 本版本修复的是 Codex 事件形状/参数适配，不能修复 Codex Desktop 自身未调度 Hook 的上游回归。物理保护是否可用只认 current-session live-probe 的两个明确 DENY；详见 v0.11.2 与 [openai/codex#21639](https://github.com/openai/codex/issues/21639)。

- Codex policy 兼容 FREEFORM `apply_patch` 与 `shell_command`，并用真实事件形状补回归测试。
- Codex 安装 matcher 同步覆盖动态工具名；live-probe 与 preflight 改为跨 harness 提示。
- live-probe 将 protect / gate 分到两个临时工程，避免第一项失败污染第二项结果。
- Claude Code 安装改走 `md_hook_runner.py`，避免 Git Bash 改写旧 `cmd /c`；两个既有 PowerShell 安全 Hook 与 `apply_md_changeset.py` 不变。

## [v0.11] - 2026-07-14

### 2026-07-14 · P0 稳定性修复 + 跨 harness 保护层

*(EN: P0 hardening and cross-harness protection adapters for Claude Code, Codex, and OpenCode.)*

- 离线 citekey 对账始终读取当前 `manuscript.md`，旧 provisional 备份只作恢复；手稿与 citemap 改为原子写入，二次运行不再回滚后续编辑。
- `collect_patches.py` 遇到坏 JSON / 空 patch 文件立即非零退出，不再写出“部分 changeset + exit 0”。
- `verify_refs.py` 从 `patches_applied/` 累计前批授权删除，后续批次不再把已批准引用删除重新判成 HARD。
- hook 安装改为按命令身份合并，保留用户原有事件和处理器；新增 `setup_all_hooks.ps1`，用共享 Python policy + Codex `hooks.json` + OpenCode `tool.execute.before` 适配层提供跨工具保护。

### 2026-07-13 · 跨工具兼容:Codex / OpenCode / Hermes Agent 也能用了

*(EN: cross-harness compatibility — the suite now runs under Codex / OpenCode / Hermes Agent, not only Claude Code: preflight recognizes non-Claude-Code sessions instead of hard-blocking; new `AGENTS.md` carries the protection rules; INSTALL.md covers per-tool skill linking.)*

- **修复**:`preflight.py` 过去把"保护钩子未注册"一律硬拦(exit 3)——可钩子是 Claude Code 独有机制,Codex / OpenCode / Hermes 用户在第一步 `md-unpack` 就会被拦死。现在它会探测会话环境(Claude Code 注入的 `CLAUDECODE` 环境标记):**在外部工具里**,钩子本来就不可能存在,改为响亮提示后放行(脚本内置的第一层闸门——单写者 apply + 引用不删——照常兜底);**在 Claude Code 里**,钩子被意外清掉照旧硬拦 + 自愈,行为一字未变。自测从 5 例扩到 8 例,全绿。
- **新增**:仓库根 [`AGENTS.md`](AGENTS.md) —— Codex / OpenCode / Hermes 会自动读取的守则文件,把钩子在 Claude Code 里物理拦截的铁律(不许直写 `manuscript.md`、引用默认不删、人工确认令牌只许人翻)写成对任何 AI 都生效的行为规范;建议再拷一份进你的论文项目根目录。
- **改进**:[`INSTALL.md`](INSTALL.md) 第 2 步改为按工具选目录的接线表(Claude Code `~/.claude/skills` —— OpenCode 原生也读这里、零额外配置;Codex `~/.codex/skills`;Hermes `~/.hermes/skills`);第 4 步钩子标注"仅 Claude Code";第 5 步给外部工具单独的验证方法。
- **改进**:`md-swarm` 在没有并行子 agent 工具的环境下,按同一契约改为串行逐条起草,其余流程(收集 / 落盘 / 终审)完全不变。
- **说明**:五个技能文件本身就是开放 Agent Skills 标准([agentskills.io](https://agentskills.io))格式,零改动即可被上述工具识别;README 与用户手册措辞同步更新。

### 2026-07-13 · 文档

*(EN: docs — pain points trimmed to six and re-ordered; README flowchart and usage-order notes; contributors + WeChat QR codes.)*

- 痛点精简为六项并重排(删去 LaTeX 工具对比一项;"无法批量 / 多 Agent"上移到第 2 位);删除开头引语、测试版警告与访谈引子段。README 与用户手册同步。
- README:流程图加"md-swarm 后可选 md-iterate 微调";补三种典型使用顺序与"顺序是活的"说明;新增"为什么必须先转 Markdown"注解;新增贡献者与公众号二维码(计算公共治理 · 人类有趣行为实验室)。

### 2026-07-12 · 文档(发布当日的后续打磨)

*(EN: docs polish on release day — single-page bilingual README, beginner install path, AI-executable INSTALL.md.)*

- README 重写为单页中英双语(页内锚点切换、中文在前);痛点全文前置;安装章节前置于环境要求;标题醒目化。
- 新增小白安装路径:**Download ZIP → VS Code 打开文件夹 → 对 AI 说"读 INSTALL.md 帮我装好"**,全程零命令行。
- 新增 AI 可执行的 `INSTALL.md`(安装手册同时是给 AI 的操作剧本)。
- 用户手册去除私有套件对比与作者备忘,只留对公开用户有用的内容。

## [v0.1] - 2026-07-12

首个公开版本(测试版 / prerelease)。
*(EN: first public release, beta.)*

- 五个技能:`md-unpack`(Word → Markdown 真源摄取)· `md-triage`(任意修订意图 → 人工确认清单)· `md-swarm`(多 agent 批量改稿)· `md-iterate`(单点修订)· `md-build`(编译回带活 Zotero 引用域的 Word)。
- 安全架构:单写者 apply(唯一允许写 `manuscript.md` 的脚本)+ 引用默认不删硬闸 + 人工确认令牌 +(Claude Code)两个保护钩子。
- 锁定版 pandoc 工具链一键安装(pandoc 3.9.0.2 + pandoc-crossref 0.3.24a,版本配套、自动下载)。
- 附:中文用户完全手册、Apache-2.0 许可、NOTICE 第三方声明。

[未发版 / Unreleased]: https://github.com/pwya/md-paper/compare/v0.11.2...HEAD
[v0.11.2]: https://github.com/pwya/md-paper/compare/v0.11.1...v0.11.2
[v0.11.1]: https://github.com/pwya/md-paper/compare/v0.11...v0.11.1
[v0.11]: https://github.com/pwya/md-paper/compare/v0.1...v0.11
[v0.1]: https://github.com/pwya/md-paper/releases/tag/v0.1
