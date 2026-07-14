---
name: md-swarm
description: 【第三代 md-* 套件·批量并行改稿后端（Phase 2）】读 `md-triage` 产出的 `swarm/md_triage.md`（离散修订条目清单·令牌已确认），按 Task 分批【并行】派子 agent【只读】各起草自己的 patch 文件 → `collect_patches.py` 确定性收齐 → `apply_md_changeset.py` 串行落盘（真源只读、子 agent 不直写、含「引用默认不删」硬闸）→ verify → 强制终审，把改动落进 Markdown 真源 `manuscript.md`。对应第二代 `docx-swarm`。**本代不要修订模式、不要三选一。** 整理意见那一步已独立成前端 `md-triage`；改完交 `/md-build` 编译出带活 Zotero 域的 Word。Use when 用户说"按这份 md_triage 批量改稿"/"多 agent 并行改稿"/"md-swarm"，或在 md-triage + 人工确认后要落地改动。**前置：先 `/md-triage` 出 `swarm/md_triage.md` 并把令牌改「已确认」；并已 `md-unpack` 出 `manuscript.md`。**
user-invocable: true
allowed-tools: [Read, Write, Edit, PowerShell, Bash, Grep, Glob, Agent, AskUserQuestion, Skill]
---

# md-swarm — 批量并行改 Markdown 真源（第三代·Phase 2·并行）

> ⛔ **路由铁律**：本 skill 属**第三代 `md-*`**，是改稿的**后端（Phase 2）**。整理意见 → 写 `md_triage.md` 那一步（旧 Phase 1）**已独立成前端 `md-triage`**。本 skill 只做：读已确认的 `md_triage.md` → 按 Task 并行起草 → 确定性落盘 → 终审。改的是 `manuscript.md`（纯文本），**不**碰 docx、**不**走修订模式。改完交 `/md-build` 出 Word。
> 🧭 **什么时候用**：手改扛不动的**大批量乱意见**才走 `md-triage → md-swarm`。小修直接手改 `manuscript.md` → `/md-build`；让 AI 改某一两处用 `md-iterate`。别为小修启动本套重机械。
> **2026-06-21 架构**：Phase 1（整理）→ 独立 skill `md-triage`；本 skill = Phase 2（并行落地）。旧"串行 + 内置 Phase 1"版存档 `../LEGACY/md-swarm·串行编排与简单整理·归档2026-06-21.md`。

## 前置（缺一不可，先查）

1. `manuscript.md`（真源）存在 —— 由 `md-unpack` 从原稿摄取。没有 → 先 `/md-unpack`。
2. `swarm/md_triage.md` 存在、且顶部令牌 = **`**人工确认：** 已确认`** —— 由 `md-triage` 产出 + **你**人工审过确认。
   - 没有 `md_triage.md` → 本 skill 先用 `Skill` 工具调 `md-triage` 生成、**停在人工闸**等你审确认，再回来继续 Phase 2（像 `md-unpack` 内部调 `docx-unpack`）。
   - 有但令牌还是「待确认」→ **停**，让用户审完改令牌。⛔ **AI 不得自行确认。**

> **当前会话 hook live-probe（写入型 md-swarm 必查）**：第一次在本会话跑 `md-swarm`，或用户刚换过 CC Switch / provider / 模型代理 / 新开供应商后，先跑：
> ```powershell
> py "<本skill>\probe_live_hooks.py" --prepare
> ```
> 然后**按脚本打印的 ACTION 1/2 原样做**：用当前 assistant 的 Write 工具尝试写临时 `manuscript.md`，再用当前 assistant 的 PowerShell/Bash 工具尝试 apply 临时 `机改` changeset。必须亲眼看到两次都被 DENY，再跑脚本打印的 `--check` 和 `--cleanup`。
>
> ⛔ 弱模型适配铁律：**不许只读这个脚本、不许直接调用 hook `.ps1`、不许把 `--check` 的“没落盘”当成已通过**；`--check` 只能发现“真的落盘了”的失败，不能区分 DENY 和你偷懒没做。没看到两个 DENY → 停下，让用户新开 Claude Code 会话并重跑 `verify_hooks.ps1` + live-probe。

## Phase 2 总流程（并行起草 + 确定性落盘）

```
存基线 → 对每个 Task（批次号相同=一批）：清空 patches/ → 【并行】派子 agent【只读】各写自己的
        swarm/patches/机改-<ID>.json → Glob 二次防线（少了就 inline 兜底）→ collect_patches.py 收齐
        → apply_md_changeset.py 串行落盘（唯一写真源，含硬闸）→ verify_applied + verify_refs → 归档 patches → 下一 Task
   ▼
全部 Task 完 → 【D·完整性+忠实度审计】主控先跑 audit_coverage.py 列清单（只列事实）→ 派 1 只读 agent 照清单逐条核"做对没/真漏"
              （漏/半拉子/跑偏 → 重派写手→collect→apply，最多 2 轮）
   ▼ D 通过
【E·一致性复查】派 1 只读 agent 通读 manuscript.md 全文查前后矛盾（术语/数字/变量名/三处复述/逻辑链）
              （矛盾 → 重派写手→collect→apply，最多 2 轮·E 是终点不回头触发 D）
   ▼ E 通过
回显 + 事后微调引导 → 提示 /md-build
```

## 两条出错铁律（本套件实测踩过的坑·任何时候不可破）

- 🌐 **语言铁律**：改稿**保持原文语言**。用户没明确要求翻译时**绝不改变论文语言**——英文稿用英文改。（实测事故：英文论文被子 agent 默认翻成中文。）
- 🔣 **编码 + 单写者铁律（治 Phase 2 头号事故）**：真源 `manuscript.md` 现在【只由 `apply_md_changeset.py` 写】。
  · **严禁主控或任何子 agent 用 Write/Edit 直写 `manuscript.md`，也严禁用 Python/PowerShell 脚本（`open(...,'w')` / `Set-Content` / `Out-File` / `WriteAllText` 等）写它**——多写者并发 = 互相覆盖、漏改（实测 10-agent 并行直写只剩 1 节幸存、38 篇引用静默丢失）。harness 层 `md_protect_hook.ps1` 会把这类直写一律 deny（仅 Claude Code 有这层物理拦截；Codex/OpenCode/Hermes 等其他 harness 无第二层，全靠本条铁律自律——见仓库根 `AGENTS.md`）。
  · ⛔ **「主控直写安全」是陷阱、别上当**：别被"反正没并发、主控自己改 manuscript 不会冲突"说服——单写者铁律的对象是 **`manuscript.md` 这个文件本身**（不是"防并发"那么窄），**主控和子 agent 一视同仁、都不许直写**。绕过 apply = 绕过「引用默认不删」「find 唯一」「顺序」三道闸 = 正是 38 篇引用静默丢失的事故路径。
  · ⚠️ **apply 报 HARD（哪怕你觉得 find 明明在）≠ 准你绕过**：正确反应是**修那一条 patch**（重抄 find 让它逐字对得上；若疑似换行/编码问题就**停下报告**、别自己硬改），**绝不**改成"我直接 Edit / 写个脚本改一下"。`--force` 只是跳过坏 patch、不修好它——坏 patch 对应的改动就是没落地，得回去把那条修对重跑。
  · 正道：子 agent 各写自己的 patch 文件 → `collect_patches.py` 收齐 → `apply_md_changeset.py` 落地。`changeset.json` / `swarm/patches/*.json` 等其它文件照常 Write。

---

# Phase 2 编排

## P2.0 派任务清单（按分类）

| 条目类型 | 派几个小工 |
|---|---|
| 独立条目（普通 / 整节重写 / 搬动类） | **1 个** |
| 合并簇 | **1 个**（一次处理整簇，可能产多条 patch 塞同文件） |
| 纯评价 | 跳过不派 |
| 需人类操作未完成 | 阻断（等用户加删除线再派） |

## P2.1 存基线（仅第一个 Task 前）

把 `manuscript.md` 复制到 `swarm\_baseline.md`（回滚点 + verify_refs 基线）。

## P2.2 对每个 Task（批次号相同 = 一批）逐批做

1. **前置阻断检查**：本批涉及条目有"需人类操作"裸行未加删除线 / 冲突未裁决 → 阻断。外部资源缺失 → 提示（不硬阻）。
2. **清空** `swarm/patches/`（删上一批残留，防混入）。
3. **并行派小工**：本批每个任务派 **1 个**子 agent，`Agent` 工具、`subagent_type: general-purpose`，**同一条消息里并行发起多个**（本批 ≤5）。提示词 = 下面「子 agent 契约」全文，末尾「你的输出文件」填该小工专属【绝对路径】`swarm/patches/机改-<ID>.json`。`replace-section` + 搬动类单独成批（批大小 1）。**若当前 harness 没有并行子 agent 工具（部分非 Claude Code 环境）：按同一契约【串行】逐个起草**——每个仍只写自己的 patch 文件、绝不直写真源，后续收集/应用/终审流程一字不变。
4. **Glob 二次防线**：本批跑完，`Glob swarm/patches/*.json`，核对"派几个 = 落地几个文件"。少了 = 那个小工没写成 → 对那一个走 **inline 兜底**：主控自己照契约起草、直接把该 json 文件 Write 出来（写 patch 文件不违反单写者，单写者只针对 manuscript.md）。
5. **collect**：`py "<本skill>\collect_patches.py" --patches-dir swarm\patches --manuscript manuscript.md --out swarm\changeset.json`。
6. **落盘（唯一写真源步）**：先 `--dry-run` 体检，干净再真落地：
   ```
   py "<本skill>\apply_md_changeset.py" --changeset swarm\changeset.json --manuscript manuscript.md --dry-run
   py "<本skill>\apply_md_changeset.py" --changeset swarm\changeset.json --manuscript manuscript.md
   ```
   有 HARD（find 不唯一/丢引用/被前一条吃掉）→ 不写、列清单 → 把对应那条 patch 让小工（或 inline）重做，**别带病往下走**。被 `[md-swarm 人工闸]` deny = 令牌没『已确认』，回去让用户确认。
7. **核对 + 体检**：
   ```
   py "<本skill>\verify_applied.py" --changeset swarm\changeset.json --manuscript manuscript.md   # 每条 LANDED
   py "<本skill>\verify_refs.py" --baseline swarm\_baseline.md --current manuscript.md --changeset swarm\changeset.json --authorized-patches-dir swarm\patches_applied   # 引用 + 图/表/公式体检
   py "<本skill>\verify_citekeys.py" --manuscript manuscript.md   # 防"编造引用"（citekey 不在库=可疑；离线 WARN，Zotero 开着可加 --zotero 坐实编造才 HARD）
   ```
   退出码非 0 → 有没落地 / 丢引用 / Zotero 坐实的编造引用，回去修，别带病走。
   > **累计授权删除（T21-6 + P0 hardening）**：`--changeset` 提供本批授权，`--authorized-patches-dir swarm\patches_applied` 从已归档 patch 累计前面批次的授权；两者取并集。只有 `intent=delete-citation/rewrite` 且 find 有、replace 没有的 citekey 才从 HARD 降为 WARN。归档里任何 JSON 损坏都会 HARD 停下，绝不静默忘掉旧授权；没授权就丢的 key 仍 HARD（默认保护不削弱）。
   > `verify_refs` 除了引用，**每批还报图/表/公式（`{#fig:}`/`{#tbl:}`/`{#eq:}`）相对基线的增删**——丢了哪个、加了哪个都逐条列出（**只 WARN 不阻断**：改稿删图删表是合法的；但若删掉的定义仍被 `[@fig:x]` 引用，会照旧 HARD 拦）。这让图表公式和引用一样、在**每个改稿批次**都被盯着，不只出稿那一下。未带 label 的图/表/公式这里盯不到 → 由 `md-build` 的 `verify_conservation`（AST 全量数）在出稿时兜底。
   > **`verify_citekeys`（防"编造引用"·2026-07-11 接入）**：与 `verify_refs`（防"丢引用"）互补的另一半。它扫稿里每个真 `[@key]` 在不在这篇的已知文献里（`references.json` ∪ `build\citemap.tsv`），不在的当**可疑**大声念名。**离线永远 exit 0、只 WARN**——离线分不清"你改稿时真新加的文献"和"AI 凭空编的"，绝不误杀真引用。只有**加 `--zotero` 且 Zotero+BBT 开着**、问库坐实"库里也没有"时才 **HARD（exit 2）**。主控看到 WARN 名单要判断：明显 authorYear 瞎编的 → 回去把那条 patch 引用改对/删掉；你真新加的文献 → 应写成 `[@NEW: 作者 年]` 占位、事后经 Zotero 补真 key。**做 live 工作流（Zotero 开着）时建议加 `--zotero`**，把"编造"从 WARN 升成 HARD 拦死。
8. **归档**：把本批 `swarm/patches/*.json` 移进 `swarm/patches_applied/`（留审计/人读）。`swarm/patches/` 清空，进下一 Task。

> **为什么按 Task 逐批 apply（而非攒到最后）**：后一批小工读到的是**已更新过的手稿**，更利于前后一致；同批内小节互不重叠，看不到彼此改动也不要紧。

## P2.3 全部 Task 完 → D 审计 → E 复查（三省制：提 / 行 / 审 各自分权、互不兼任）

> **三省制**（架构理念·详见 `../md-技能套件·开发手册.md`）：triage 管「提」（整理意图）不管写、写手管「行」（起草 patch）不管审、D/E 管「审」（只读审计）不管写。**D/E 只出报告**，发现的漏 / 半拉子 / 跑偏 / 矛盾 → **主控重派写手** agent（套写手契约）起草修正 patch → collect → apply。D/E 自己不碰 patch、不碰手稿。**先 D 后 E，各最多 2 轮，E 是终点、不回头再触发 D。**

### P2.3-D 完整性 + 忠实度审计（只读 · 先跑）

1. **主控先跑清单生成器**（确定性·只读·**只列事实不判断**）：
   ```
   py "<本skill>\audit_coverage.py" --triage swarm\md_triage.md --patches-dir swarm\patches_applied --out swarm\coverage_check.txt
   ```
   它扫 triage 列出所有意见编号 + 列出所有 patch 文件 + 同名对账（哪些意见有 `机改-<编号>.json`、哪些没有）+ patches 里有但 triage 没有的文件（合并文件/簇 patch/终审 patch）。**脚本只列事实、不做判断**——判断（经合并覆盖/经簇覆盖/待人工/真漏）交 D agent。这样脚本不怕子 agent 偶发的不规范命名（如 `机改-P9-P11.json` 一文件塞两条），如实列出来交 D 读内容核。
2. **派 1 个【只读】D agent**（`Agent` 工具、`subagent_type: general-purpose`），提示词 = 下面「D agent 契约」全文，末尾「输入/输出文件」填绝对路径。它读 `coverage_check.txt` + `md_triage.md` + `patches_applied/` + `manuscript.md`，对"无同名文件"的意见逐条核（合并覆盖？簇成员？需人工？真漏？）、对"有同名文件"的逐条审做对没，出 `swarm/audit_report.md`。
3. **处置（A 方案 · D 不碰手稿、不写 patch）**：`audit_report` 标 缺失/半拉子/跑偏 的 → **主控重派写手** agent（套 P2.2 同一份写手契约 + 去AI味）起草修正 patch → collect → apply → verify。存疑/需人工 → 高亮交用户。
4. **最多 2 轮**：修完重跑 D 再审一轮；2 轮仍搞不定的列清单交用户，**不硬往下走**。

### P2.3-E 一致性复查（只读 · D 通过后跑 · 终点）

1. **派 1 个【只读】E agent**（`Agent` 工具、`subagent_type: general-purpose`），提示词 = 下面「E agent 契约」全文。它**通读 `manuscript.md` 全文**查五类矛盾（术语统一 / 数字一致 / 变量名符号 / 摘要-引言-结论三处复述 / 逻辑链），出 `swarm/consistency_report.md`。
2. **处置（同 D · E 不碰手稿、不写 patch）**：标"打回修正"的 → 主控重派写手 agent 起草修正 patch → collect → apply → verify。
3. **最多 2 轮**。**E 是最后一道**：改完不回头再触发 D。
4. **E 通过 + verify_refs 干净 + verify_applied 全 LANDED，才算改完。**

### D agent 契约（原样塞给 D agent · 内联全文 · 只读·只出报告）

```
你是资深论文审稿编辑，做【完整性 + 忠实度审计】。任务：逐条核对 md-swarm 刚改完的这篇论文——【每条修订意见到底做没做、做对没】。你只读、只出一份报告，绝不改手稿。

## 0. 你的产出 = 写一个报告文件（不是改手稿，不是回消息）
- 你【只能写一个文件】：下面「你的输出文件」给的那个 swarm/audit_report.md。
- 【绝不许碰 manuscript.md】——碰了会被 harness 钩子直接拦下、你白干。也不碰任何 patch 文件、不碰 md_triage.md、不跑任何脚本。
- 写完那份报告就结束，不要再输出别的解释。

## 1. 读对账清单，逐条核"无同名文件"的意见（别直接当漏）
- 主控已替你跑过 audit_coverage.py，结果在 swarm/coverage_check.txt。**脚本只列事实、不判断**——它列出：所有意见编号、所有 patch 文件、哪些意见有同名 `机改-<编号>.json`、哪些没有、以及 patches 里有但 triage 没有的文件（合并文件/簇 patch/终审 patch）。
- 【无同名文件 ≠ 漏】。对每个"无同名文件"的意见，逐条核以下可能（按顺序）：
  ① **被合并文件覆盖？**——看清单第五节有没有像 `机改-P9-P11.json` 这种【一个文件名含多个编号】的，读该 patch 文件内容确认它覆盖了这条意见。覆盖了→不算漏（报告标"经合并文件覆盖"+证据）。
  ② **是合并簇成员？**——读 triage 该条的「所属合并簇」字段，若有簇（MC-N/MD-N），看清单里有没有 `机改-<簇编号>.json`，读它确认覆盖了这条。覆盖了→不算漏（标"经簇覆盖"+证据）。
  ③ **需人类操作/纯评价？**——读 triage 该条的「需人类操作」「纯评价」「批次」字段。若需人类/纯评价/批次=—，本就不该有文件→不算漏（标"待人工/纯评价"）。
  ④ **以上都不是 → 真漏**，列入"漏项"，打回重做。
- 清单第五节的文件（patch 有 triage 无）：是合并文件/簇 patch/终审 patch。确保它们都被 ①② 核过覆盖关系，别遗漏。

## 2. 逐条审"有改动文件"的条目
对每条有对应改动文件（swarm/patches_applied/机改-<编号>.json）的意见，读三样：
  · 这条意见本身（编号 + 摘要 + 原文 + 作者批注）——在 swarm/md_triage.md 里找。
  · 它的改动文件——swarm/patches_applied/机改-<编号>.json，看里面的 find→replace（改前→改后）。
  · 手稿里的实际结果——去 manuscript.md 对应位置看【现在到底是什么样】。
判四档：
  · 【做对了】：意见要的改动了、改对了。
  · 【半拉子】：改了一半没改完（典型：表说移走但正文还在、指向语加了但被指物没动）。
  · 【跑偏了】：改了但改错方向/改错地方/理解错意见。
  · 【存疑】：拿不准。宁可标存疑交人工，也别瞎判"做对了"让错误溜走。

## 3. 三条铁钉（专门治"半拉子"，必照做）
① 凡涉【删/移/搬】类意见（删图、挪表入附录、附录独立成章、删某节、移动段落），必须【亲自去 manuscript.md 对应位置核对实物】：
   - 该删的东西——还在不在？
   - 该搬的东西——正文和附录是不是【只存一份】？有没有"正文一份 + 附录一份"的重复？
   - 【不能只看 patch 文件里写了什么】——文件里写"删了"，手稿里可能还在。
② 每条判定必须【附手稿原文证据】：引用 manuscript.md 当前实际的那几行，证明你的判定。比如判"半拉子"，要贴出"正文第 X 行 Table 2 仍在 + 附录第 Y 行又有 Table 2"。【没证据的"做对了"一律视作不合格、打回。】
③ 半拉子典型信号（看到就双向核对）：
   - 正文出现 "See Appendix for details" / "见附录" 之类指向语，但被指的表/图在正文原位【仍然存在】。
   - 附录里有某表/图，正文里同表/图【没删】。
   - 某节说"已删除"但实际还在。

## 4. 不查一致性
术语/数字/变量名前后矛盾——【不归你管】，那是 E 的活。你只管"这条意见做没做、做对没"。别越界。

## 5. 报告格式（写进你的输出文件）
# 完整性审计报告（第 N 轮，最多 2 轮）

## 一、有同名文件（D 逐条审做对没）
| 条目 | 意见摘要 | 判定 | 证据（手稿原文） | 建议 |
|---|---|---|---|---|
| 审-3 | 删 Fig2/3、Table2/3 移附录 | 半拉子 | "正文 L120: ![图2](images/fig2.png)... 仍在；附录 L610: : Table 2 ... 也有" | 打回重做 |
| P10 | 引用间距修正 | 做对了 | "manuscript L45: [@a] 已改为 [@a]..." | 无需动 |

## 二、无同名文件（D 逐条核后归类）
| 条目 | 意见摘要 | D 判定 | 证据 | 建议 |
|---|---|---|---|---|
| 审-2 | 引言去标题 | 经簇覆盖(MD-3) | "读 机改-MD-3.json 含审-2 改动" | 无需动 |
| P9 | 结论去重 | 经合并文件覆盖 | "读 机改-P9-P11.json 含 P9 改动" | 无需动 |
| P8 | AxMath 公式 | 待人工 | "triage 需人类操作:补充采集" | 等人工 |
| 审-4 | 附录独立成章 | 真漏 | "无同名/无合并覆盖/非簇/非需人工" | 打回重做 |
| P18 | 引用间距 | 真漏 | "无同名/无合并覆盖/非簇/非需人工" | 打回重做 |

## 处置建议
- 半拉子/跑偏/真漏 → 打回当新 patch，重走 写手→collect→apply（不归你写）。
- 存疑/需人工 → 高亮交作者。

## 6. 你的边界
- 你不碰手稿、不写 patch、不跑脚本。你只出报告。
- 打回后由主控重新派写手 agent 起草修正 patch，不归你写。
- 最多兜 2 轮（这是第 N 轮）。再搞不定的，列清单交作者。

## 你的输入文件（主控填路径）：
- 意见清单：<swarm/md_triage.md>
- 改动账本目录：<swarm/patches_applied/>
- 手稿全文：<manuscript.md>
- 对账结果：<swarm/coverage_check.txt>

## 你的输出文件（主控填绝对路径）：
<例如 ...\项目目录\swarm/audit_report.md>
```

### E agent 契约（原样塞给 E agent · 内联全文 · 只读·只出报告）

```
你是资深论文审稿编辑，做【一致性复查】。任务：通读 md-swarm 改完的【整篇论文】，查前后矛盾。你只读、只出一份报告，绝不改手稿。

## 0. 你的产出 = 写一个报告文件
- 你【只能写一个文件】：下面「你的输出文件」给的那个 swarm/consistency_report.md。
- 【绝不许碰 manuscript.md】——碰了会被 harness 钩子直接拦下、你白干。也不碰任何 patch 文件、不跑任何脚本。
- 写完那份报告就结束。

## 1. 重读全文（不是只看改过的地方）
- 【完整通读 manuscript.md 全文】。一致性问题是"最终文本里"对不对得上，必须看终态全文，不能只看改过的几十处。
- 同时参考 swarm/patches_applied/ 里的 find→replace，知道哪些地方被改过——这些是【新矛盾最可能冒出来的地方】，重点留意，但不止看这些。

## 2. 查五类矛盾
① 术语：该统一的词全文统一了没？比如某处把 national capacity 改成 state capacity，全文还有没有漏网的 national capacity？
② 数字：同一个数在多处报得一致没？摘要里的 2.47、正文表格里的、结论里的——是不是都是 2.47？
③ 变量名/符号：同一个变量全文一个样没？D_positive 还是 "D positive"，有没有混用？
④ 三处复述：摘要 / 引言 / 结论说的是不是同一件事？有没有摘要说东、结论说西？
⑤ 逻辑链：有没有哪段论证被改稿打断、前后接不上？比如前文假设的变量后文没用到、结论提到的原因引言没铺垫。

## 3. 每条必须附证据
- 引用 manuscript.md 的原文，指出矛盾的两处。比如"摘要 L3 写 2.47，结论 L200 写 2.74，对不上"。
- 没证据不报。但也别为凑数乱报——真矛盾才报。

## 4. 报告格式（写进你的输出文件）
# 一致性复查报告（第 N 轮，最多 2 轮）

| # | 类型 | 矛盾描述 | 证据（手稿原文两处） | 建议 |
|---|---|---|---|---|
| 1 | 数字 | 摘要 2.47 vs 结论 2.74 | "L3: ...2.47..." / "L200: ...2.74..." | 打回修正 |
| 2 | 术语 | national capacity 漏网 1 处 | "L150: ...national capacity..." | 打回改 state capacity |
| 3 | 逻辑链 | 引言假设 X 后文未用 | "L12 假设 X..." / "全文未再提 X" | 打回补论证或删假设 |

## 5. 处置
- 发现矛盾 → 报告里标【打回修正】。由主控派写手 agent 起草修正 patch → collect → apply（不归你写）。
- E 不碰手稿、不写 patch。
- 最多 2 轮（这是第 N 轮）。
- 【E 是最后一道】：改完不再回头触发 D。

## 你的输入文件（主控填路径）：
- 手稿全文：<manuscript.md>
- 改动账本目录：<swarm/patches_applied/>（参考，知道改了哪些）

## 你的输出文件（主控填绝对路径）：
<例如 ...\项目目录\swarm/consistency_report.md>
```

## P2.4 收尾 + 产物回显（UX-1·必做）

→ `/md-build -Mode static` 自查正文/图表/交叉引用 → 原有引用走 `-Mode rebuild`（离线活域）；有改稿新增引用 → 对账后 `-Mode live`。

> 📋 **回显铁律**：Phase 2 跑完**必须**回显——① 改后 `manuscript.md` 绝对路径 + 改动条数/小节摘要；② `swarm\changeset.json`；③ `verify_refs.py` + `verify_citekeys.py` 体检结果；④ D 审计报告（`swarm/audit_report.md`）+ E 复查报告（`swarm/consistency_report.md`）结论；⑤ **事后微调引导（固定话术）**："改动逐条看 `swarm\changeset_review.md`（可跑 `render_changeset.py` 生成）；想微调哪句，优先在 VS Code 选中 `manuscript.md` 片段后用 `/md-iterate`；纯手改也可直接在编辑器改（保护钩子只拦 AI、不拦你）。"；⑥ **下一步**：微调完 `/md-build -Mode rebuild` 或对账后 `live`。别只说"改完了"。

---

# 子 agent 契约（原样塞给每个子 agent · 内联全文 · 重中之重）

```
你是资深论文编辑。任务：针对【一条】修订意见（或一个合并簇），为这篇论文的 Markdown 源起草改动。

## 0. 你的产出 = 写一个文件（不是回消息）
- 你【只能写一个文件】：下面「你的输出文件」给出的那个 swarm/patches/机改-<ID>.json。
- 【绝不允许】写或改任何别的文件——尤其【绝不碰 manuscript.md】（碰了会被 harness 钩子直接拦下、你白干）。也不碰别的 patch 文件、不碰任何 docx、不跑任何脚本。
- 写完那一个 json 文件就结束，不要再输出别的解释。

## 1. 语言铁律（最高优先）
输出语言【必须与目标小节原文一致】。用户没明确要求翻译时【绝不改变语言】——原文英文就用英文改，原文中文就用中文。意见/本指令是中文，不代表正文要变中文。若你正要把英文段落写成中文，停：那是越权翻译。

## 2. 先通读全文、对前后一致性负责（务必做到）
- 动手前【先完整通读 manuscript.md 全文】（不只你的目标小节）。
- 你对【前后一致性】负责：术语、缩写、数值、变量名、符号、结论方向，都必须和全文其余部分对得上，不得制造前后矛盾或逻辑断裂。
- 你读到的是当前手稿这一份基线；【只改你被分到的那一小节，别动别的小节】（别的小节有别的小工在改）。若你发现别处也需配合改，写进 notes 提示主控，别自己去改别的小节。

## 3. 你的任务（由主控填，来自 md_triage.md）
- 目标小节/章节：<## 标题 / 涉及章节>
- 意见：编号 / 分类(理论/实证/贡献/局限/写作) / 重要性 / 章节 / 摘要 / 原文(保留原语言) /
  改动类型(`补丁`=这节引用必须全保留；`重写`=已授权你动这节引用) /
  是否搬动(`是`=跨节删+增，见第 6 节) / 人类批注(作者直接指令·最高优先；为空写"无")
- （合并簇任务：成员条目清单 + 每条的上述信息，你一次性处理整个簇）

## 4. Markdown 写法铁律（pandoc + crossref + zotero 管道）
1.【引用默认不删·最高铁律】已有 [@citekey] 一律【原样保留】，除非这条意见的【改动类型=重写】或意见本身明确要删/重写。默认(改动类型=补丁、intent=modify)：replace 里必须把 find 出现的每一个 [@key] 都原样写回。成组 [@a; @b; @c] 整组保留、不拆不重排。严禁自行授权删引用。新增且知道库里真 key→[@key]；不确定→[@NEW: 作者 年 简题]（绝不瞎编 authorYear，**也绝不模仿本稿现有 key 的样子自造新 key**——满眼 [@authorYear] 可能只是 md-unpack 未对账的临时命名空间，照猫画虎＝编造）。
   —— 这是硬闸：plain patch 丢了 find 里的 [@key]，apply 判 HARD 拒写、你白干。
2. 交叉引用：图 [@fig:N]、表 [@tbl:N]、公式 [@eq:N]（冒号式）。【引用前必先定义】：写 [@fig:x] 前 {#fig:x} 必须已在文中存在。
3. 新增图：![<题注>](images/<文件名>){#fig:<label>}；新增表：标准 pipe 表 + 紧跟一行 `: <题注> {#tbl:<label>}`；公式：行内 $..$、独立 $$..$$ {#eq:<label>}。
4. 结构：可自由加/改/重排 ##/### 小标题（纯文本重排是安全的）。
5.【去AI味·所有 replacement 正文必过·逐条照办，别凭记忆简化成一句】：
   · 何时套用：你在撰写/改写正文 prose（reword、整段重写、改措辞、补论述句）。
   · 何时跳过（套用反而出错）：纯加引用（只插 [@NEW:…]/[@citekey]，其余原样）；纯删引用；纯标点/空格微调；引用与交叉引用 token 前后的纯机械改动；把原文原样保留的等价替换（没真正改写文字时）。
   · 七条改写要求：
     ① 少修辞：减少修辞词、避免堆砌形容词，保持直白、精确。
     ② 短句：采用短句结构、避免复杂复合句和从句，多用简单直接的陈述句。
     ③ 打破工整：灵活调整句子结构（如主谓宾语序变动）、增强多样性，别每句一个模子。
     ④ 降正式度：把过于正式/书面化的语言换成更自然流畅、贴近日常的表达（仍守学术规范、不口语化到失格）。
     ⑤ 保信息：原文的关键信息、数据、论断、因果与意图完整保留、不失真。
     ⑥ 少破折号：不要过多使用破折号（——），能用逗号/句号/拆句解决的就别用。
     ⑦ 一句号一意思：每个句号结束一个完整独立的意思。严禁用两个或多个句子从不同角度重复啰嗦同一观点——一句话只说一次，说清楚就过。发现前后句在说同一件事 → 合并成最精练的一句或删掉冗余那句。
     ⑧ 覆盖全部元素：语言风格、结构布局、术语运用、标点都按上述调整；消除 AI 生成痕迹、降低与原文雷同，符合学术写作规范。
   · 两条硬护栏（任何时候不可破）：
     a) 引用与交叉引用是原子：[@citekey]、原文整组 [@a; @b; @c]、[@fig:N]/[@tbl:N]/[@eq:N]、[@NEW:…] 一律原样保留——不增、不删、不挪、不改编号。
     b) 只改"怎么说"、不改"说什么"：去AI味只动表达、不动语义；不得借润色之名改变论点强度、增删事实、调整数据或引用口径。
   · 一句话：同样的意思、同样的引用，用更短、更直、更不工整、更少破折号、更不像 AI 的话，重写一遍。

## 5. 改动幅度（默认最小片段）
- 默认 mode=patch：只圈你真要改的那一小段当 find（从 manuscript.md 逐字复制、要能在全文唯一定位），replace 是这一小片的完整等价替换（没改的原样留着）。改一个词就只圈那个词——越小越安全、越不会误删引用。
- mode=replace-section：仅当这节确实要整段/整节推倒重写（且改动类型=重写）。find=逐字复制的整个旧小节（含其中所有 [@key]），replace=新小节；即便重写也保留精确数值、把仍有用的引用带回新文。
- find 必须能在 manuscript.md 里【唯一】匹配；不唯一就把片段取长一点。
- **全文同词替换**（如术语 national capacity→state capacity 全文 N 处）：把 `intent` 设成 `replace-all`、find 给那个词/短语本身（**可以不唯一**），apply 会一次替换所有出现。**仅用于机械同词替换**，别拿它改语义、也别用来删引用（引用安全闸照旧生效）。

## 6. 搬动/增删类（仅当 triage 标了"搬动=是"）
- 你要产出【两条 patch】，塞同一个文件 patches[]，顺序【先增后删】：
  · 动作2(先)：在新文位置的锚点后插入内容。find=锚点句（逐字复制、全文唯一，优先小标题或独特句），replace=锚点句 + 新内容。
  · 动作1(后)：在原文锚点章节删掉那段。find=要删的那段原文（逐字复制、全文唯一），replace=空字符串""或衔接句。
- 锚点找不到唯一匹配 → 取长一点。整节重写的节 → 新内容直接写进新节，不用单独锚点。
- 纯删（无动作2）/ 纯增（无动作1）→ 只写对应那一条 patch。

## 7. 输出文件格式（把下面这段 json 写进【你的输出文件】，文件里别的什么都不写）
{ "id":"机改-<你的意见ID>", "target":"<目标小节标题>",
  "patches":[
    { "mode":"patch|replace-section", "intent":"modify|rewrite|delete-citation|add-citation",
      "find":"<从 manuscript.md 逐字复制的最小片段(默认)或整节(replace-section)>",
      "replace":"<新文本：成品正文本身，不是'要做什么'的描述>" } ],
  "new_citations":[{"placeholder":"[@NEW: …]","note":"需入库/确认 key"}],
  "new_objects":[{"type":"figure|table","label":"fig:x","src":"images/x.png","caption":"…"}],
  "notes":"<删了哪些引用及依据 / 需主控配合改别处 / 留人工的事；没有就空字符串>" }
- intent 默认 modify（引用全保留）；只有意见授权才用 rewrite/delete-citation。
- 一条意见多处改 → patches[] 放多条。搬动类 → 先增后删两条。真无可改 → patches 空数组，notes 写"建议反驳/无需改"。

## 8. 人类批注非空时最高优先
- triage 的「人类修改思路」字段非空时，先照它定调再落实。它是作者直接指令，优先级高于你的临场判断。
- 若批注与意见本身冲突（如意见要删、批注说保留），照批注（批注代表作者最终意愿），并在 notes 注明"依作者批注保留/反驳"。

## 你的输出文件（主控填绝对路径）：
<例如 D:\...\项目目录\swarm\patches\机改-P2.json>
```

---

## 关系

上游 **`md-triage`**（出 `swarm/md_triage.md` 离散条目清单·令牌已确认）+ **`md-unpack`**（出 `manuscript.md`）；下游 **`md-iterate`**（单处事后微调，可选）+ **`md-build`**（出 Word）。去AI味规范已【内联进上面子 agent 契约第 4.5 条】，运行时 **不依赖任何外部文件**；共享底本见 `../_md-shared/writing_contract.md`，但本 skill 仍内联全文，防止子 agent 漏读。同目录自带脚本：
- `collect_patches.py`（确定性收齐各小工的 patch 文件成 changeset.json）
- `apply_md_changeset.py`（**唯一**写真源的确定性 apply，含「引用默认不删」硬闸 + 唯一性/顺序闸）
- `verify_refs.py`（引用体检 + 图/表/公式 `{#fig:}`/`{#tbl:}`/`{#eq:}` 定义增删·每批跑·只 WARN）、`verify_citekeys.py`（防"编造引用"：citekey 不在库=可疑·每批跑·离线 WARN / `--zotero` 坐实才 HARD·带 `--selftest`）、`verify_applied.py`（落地核对）、`render_changeset.py`（人读版 BEFORE→AFTER）、`audit_coverage.py`（D 用：意见↔改动文件对账，出 `coverage_check.txt`）
- `md_protect_hook.ps1`（真源保护·harness 闸）、`md_swarm_gate_hook.ps1`（人工确认令牌·harness 闸，查 `swarm/md_triage.md` 令牌）

两个 hook 需在本机 `~/.claude/settings.json` 注册一次（见 `../新电脑设置指南.md`）。**apply 和两个钩子一个字都不要动**（验证过的安全底座）。
> **2026-06-25 D/E 改造（已落地；测试21代表性子集已跑通；作者 2026-07-11 拍板：全量真稿批次不再单独补测，随下次真实改稿自然覆盖）**：把 P2.3「强制终审」拆成两道只读审计——**D（完整性+忠实度审计）**先跑新增的 `audit_coverage.py` **列对账清单（只列事实·意见↔patch 同名对账，不判断）** + 派 agent 照清单逐条核"做对没/真漏"（抓漏项/半拉子/跑偏），**E（一致性复查）**通读 `manuscript.md` 全文查前后矛盾。D/E **只出报告**，发现的漏/错/矛盾→**重派写手**起草修正 patch→collect→apply（A 方案·D/E 不碰 patch 不碰手稿）。先 D 后 E，各最多 2 轮，E 是终点。**三道安全闸 + 两个钩子一字未动**。设计详见 `../LEGACY/md-swarm改造方案-完整记录-全貌版.md` + `../LEGACY/md-swarm改造讨论纪要-续会到收束.md`；架构理念（三省制）见 `../md-技能套件·开发手册.md`。
> **2026-06-25 维护（已复审 + 实测）**：为修 test-18 的 NBSP 事故，给 `transform.py`/`apply`/`collect`/`verify_applied` 加了「隐形空格折叠」（U+00A0 等折成普通空格、源头 + 匹配两层）；`apply` 加了**可选** `intent:"replace-all"`（全文同词替换，默认行为不变、普通 patch 的唯一性闸照旧）；`collect` 加了字段别名映射（old_string→find 等）+ `strict=False` 容错 + `--skip-stale`。**三道安全闸（引用默认不删 / find 唯一 / 顺序）一字未动**，已用合成 + 真实 baseline 实测通过。这是一次**刻意的、留痕的**维护，不违反上面「运行时别擅改 apply」的铁律。旧"串行 + 内置 Phase 1"版存档见 `../LEGACY/md-swarm·串行编排与简单整理·归档2026-06-21.md`；整理意见的前端见 `../md-triage/SKILL.md`；全套见 `../md-技能套件·开发手册.md`。
