---
name: md-iterate
description: 【第三代 md-* 套件·单点即时修订】在已有 `manuscript.md` 上，针对用户指定的一处文本（VS Code 选区/行号、原文片段、章节标题、或 `@` 某个 triage/swarm 条目）做一轮轻量 AI 修改：默认直接落地到 `manuscript.md` 以便所见即所得，但 AI 绝不直写真源，必须生成单条 changeset → `apply_md_changeset.py --dry-run` → `apply` → `verify_applied.py` → `verify_refs.py`。用于 `/md-unpack` 后的小修、`/md-swarm` 后补刀、`/md-build` 后回源修正；不用于一堆意见（那走 `md-triage → md-swarm`）。Use when 用户说“改这段/选中这段/这句太AI了/再短一点/继续改刚才那处/润色这一段/重写这一小段/压缩这段/保留引用改自然”等单点修改意图，即使没有显式说 md-iterate。
user-invocable: true
allowed-tools: [Read, Write, PowerShell, Bash, Grep, Glob, AskUserQuestion]
---

# md-iterate — 单点即时修订 Markdown 真源

> ⛔ **路由铁律**：本 skill 属第三代 `md-*`。它只处理**一处**用户点名的修改，不整理大批量意见、不并行、不做 D/E 审计、不生成 Word。大批量乱意见走 `md-triage → md-swarm`；出稿走 `md-build`。
>
> ⛔ **单写者铁律**：默认会让用户在 VS Code 里看到 `manuscript.md` 真实变动，但**绝不允许 AI 用 Edit/Write/Python/PowerShell 直接写 `manuscript.md`**。唯一写入路径是 `md-swarm/apply_md_changeset.py`。

## 定位

`md-iterate` 是“单处即时改稿笔”：

- `/md-unpack` 后：已有 `manuscript.md`，可直接单点打磨。
- `/md-swarm` 后：批量改稿完成后，用它补刀、去 AI 味、压缩句子。
- `/md-build` 后：看 Word 发现问题，回源用它修，再重新 build。
- `/md-triage` 后、`md-swarm` 前：默认拦住，避免 triage 清单和真源不同步。

## 用户最佳入口

优先支持 VS Code 选区/行号。目标来源按优先级：

| 来源 | 行为 |
|---|---|
| 用户在 `manuscript.md` 选中一段并说“改选中这段…” | 若 harness 提供文件/range，视为唯一目标，直接用 |
| 用户给 `manuscript.md:行号` 或粘贴带行号的片段 | 视为唯一目标 |
| 用户粘贴原文片段 | 必须在 `manuscript.md` 唯一命中 |
| 用户给章节标题/一句话描述 | 主控定位；不唯一就问 |
| 用户说“继续刚才那处” | 读 `swarm/iterate_history.jsonl` 最近目标；没有历史就问 |
| 用户说 `@P7` / `@R1-C2` | 回查 `swarm/md_triage.md`、`swarm/patches_applied/` 或历史；定位不唯一就问 |

若拿不到真实 VS Code selection，不许假装知道；让用户粘贴片段、给行号或给标题。

## 触发判断

即使用户不说 `/md-iterate`，满足这些条件就默认路由到本 skill：

```text
已有 manuscript.md
+ 用户要求修改/润色/压缩/重写/去AI味
+ 目标是单处、选区、句子、段落、小节，或可唯一定位
= md-iterate
```

以下不走本 skill：

- “按这 30 条意见改全文” → `md-triage → md-swarm`
- “整理这些意见” → `md-triage`
- “出 Word / 生成 docx” → `md-build`
- “把原稿转 md” → `md-unpack`
- “帮我改一下论文”但没有目标 → 先问目标，不猜

## 总流程

```
0. check_state.py 状态护栏
0.5 当前会话 hook live-probe（需要时）
1. 定位目标（选区/行号/片段/标题/@ID/最近一次）
2. 判断模式：默认落地 or 预览
3. 起草单条 changeset -> swarm/iterate_last_changeset.json
4. apply dry-run
5. 默认落地：apply 写入 manuscript.md；预览模式：先回显，等用户确认再 apply
6. verify_applied + verify_refs
7. 对话框短 BEFORE/AFTER；长改动才写 swarm/iterate_last.md
```

## 0. 状态护栏（必须先跑）

```
py "<本skill>\check_state.py" --workdir "<项目目录>"
```

阻断时停下，不起草 patch。典型阻断：

| 状态 | 处理 |
|---|---|
| 无 `manuscript.md` | 停：先 `/md-unpack` |
| `swarm/md_triage.md` 是 `待确认` | 停：把想法写进对应条目的 `人类修改思路`，或先完成 triage |
| `md_triage.md` 已确认但 md-swarm 未完成 | 停：先跑完 `md-swarm`，或明确放弃/重做 triage |
| `swarm/patches/*.json` 残留 | 停：先处理/清理中断的 swarm 批次 |

`build/out_*.docx` 过期只 WARN，不阻断；完成 iterate 后提示用户重新 `/md-build`。

### 0.5 当前会话 hook live-probe（写入前护栏）

`md-iterate` 默认会实际写入 `manuscript.md`，所以第一次在本会话跑 `md-iterate`，或用户刚换过 CC Switch / provider / 模型代理 / 新开供应商后，先跑 sibling 脚本：

```powershell
py "<md-swarm>\probe_live_hooks.py" --prepare
```

按脚本打印的 ACTION 1/2 原样做：用当前 assistant 的 Write 工具尝试写临时 `manuscript.md`，再用当前 assistant 的 PowerShell/Bash 工具尝试 apply 临时 `机改` changeset。必须亲眼看到两次都被 DENY，再跑脚本打印的 `--check` 和 `--cleanup`。

弱模型适配铁律：不许只读脚本、不许直接调用 hook `.ps1`、不许把 `--check` 的“没落盘”当成已通过；`--check` 只能发现“真的落盘了”的失败，不能区分 DENY 和跳过。没看到两个 DENY → 停下，让用户新开 Claude Code 会话并重跑 `verify_hooks.ps1` + live-probe。

## 1. 目标唯一性

目标必须唯一。任何不唯一都停下问用户：

```text
我找到 3 处相似文本，请选：
1. Introduction 第 2 段：...
2. Theory 第 1 段：...
3. Conclusion 第 3 段：...
```

不允许为了省事猜“应该是第一处”。

## 2. 默认落地 vs 预览

普通单句/单段润色默认直接落地。用户说“先看看/先别写/预览”则强制预览。

自动转预览的高风险场景：

| 场景 | 行为 |
|---|---|
| 语义定位、不是精确片段/行号 | 预览 |
| 整节重写/大段重写 | 预览 |
| 删除文字超过明显比例 | 预览 |
| 会删引用 | 预览 + 必须明确授权 |
| 新增 `[@NEW: ...]` | 预览 + 提示需补真 citekey |
| `replace-all` / 全文替换 | 预览 + 确认 N 处 |
| 改标题结构、移动段落 | 预览 |
| 改图表公式定义或交叉引用 | 预览 |

硬闸不能被“直接改进去”绕过：无 `manuscript.md`、pending triage/swarm、目标不唯一、dry-run 失败、未授权删引用、verify_refs hard fail。

## 3. 起草单条 changeset

固定写：

```
<项目>\swarm\iterate_last_changeset.json
```

格式：

```json
{
  "source_md": "manuscript.md",
  "patches": [
    {
      "id": "迭代-YYYYMMDD-HHMMSS",
      "target": "目标位置说明",
      "mode": "patch",
      "intent": "modify",
      "find": "从 manuscript.md 逐字复制的唯一片段",
      "replace": "新文本：成品正文本身，不是说明"
    }
  ]
}
```

默认 `intent=modify`，引用必须全保留。只有用户明确授权删/换引用，才可用 `intent=rewrite` 或 `delete-citation`，且必须预览确认。

## 4. dry-run 和正式写入

先 dry-run：

```
py "<md-swarm>\apply_md_changeset.py" --changeset "<项目>\swarm\iterate_last_changeset.json" --manuscript "<项目>\manuscript.md" --dry-run
```

通过后：

```
py "<md-swarm>\apply_md_changeset.py" --changeset "<项目>\swarm\iterate_last_changeset.json" --manuscript "<项目>\manuscript.md"
```

失败就不写源，修 patch 或报告用户，绝不绕过 apply。

## 5. 验证

写入后必须跑：

```
py "<md-swarm>\verify_applied.py" --changeset "<项目>\swarm\iterate_last_changeset.json" --manuscript "<项目>\manuscript.md"
py "<md-swarm>\verify_refs.py" --current "<项目>\manuscript.md"
```

`verify_refs` 有 hard violation 时，报告并提示修复；不能装作成功。

## 6. 输出与文件噪音控制

默认只在对话框回显短 BEFORE/AFTER。改动长时，覆盖写一个固定文件：

```
<项目>\swarm\iterate_last.md
```

可后台追加机器历史：

```
<项目>\swarm\iterate_history.jsonl
```

不要每轮生成一堆带时间戳的 review 文件。日常用户只看对话框 + `manuscript.md`。若项目是 git 仓库，提示可在 VS Code Source Control 看完整 diff；否则可比较 `manuscript.md.applybak` 与 `manuscript.md`。

## 7. 回显模板

短改动：

```text
已写入 manuscript.md。

BEFORE:
...

AFTER:
...

安全检查：
- apply: OK
- verify_applied: LANDED
- verify_refs: OK

下次可直接在 VS Code 选中 manuscript.md 片段后说“改选中这段...”
```

长改动：

```text
已写入 manuscript.md。改动较长，这里只显示摘要。

本轮变化：
- ...
- 保留原有 N 条引用

完整 BEFORE/AFTER：swarm/iterate_last.md
VS Code 若启用 git，可在 Source Control 查看 manuscript.md diff。
```

## 写手契约（起草 replace 时必须逐字遵守）

下面去AI味原文来自共享底本 `../_md-shared/writing_contract.md`，也是 `md-swarm` 子 agent 契约的同源文本。**不得压缩、改写、删句。**

### Markdown 写法铁律（pandoc + crossref + zotero 管道）

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

## 关系

上游：`md-unpack`（出 `manuscript.md`）、可选 `md-swarm`（批量改完后补刀）。下游：`md-build`。安全底座复用 `../md-swarm/apply_md_changeset.py`、`verify_applied.py`、`verify_refs.py`；状态护栏为本目录 `check_state.py`；共享写作契约底本为 `../_md-shared/writing_contract.md`。
