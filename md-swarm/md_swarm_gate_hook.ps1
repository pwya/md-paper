# md_swarm_gate_hook.ps1
# PreToolUse 硬闸（md-swarm 人工闸的兜底）：拦截"令牌未确认却要 apply 多 agent 批量 patch"的调用。
#
# 触发拦截的充要条件（三条全中才拦）：
#   ① 这次调用是在跑 apply_md_changeset.py；
#   ② 它的 --changeset 指向的 changeset.json 里，patches[] 中存在 id 以「机改」开头（= 多 agent 批量产出）；
#   ③ 对应工作目录的 swarm\md_triage.md 里【人工确认】不是「已确认」（没文件 / 待确认 / 读不到那一行都算未确认）。
# 命中 → deny，提示用户先在 md_triage.md 把令牌改成「已确认」（AI 不得自行确认）。
#
# 放行：
#   · 不涉及 apply_md_changeset.py 的调用；
#   · changeset 里没有任何 id 以「机改」开头（= 普通手搓 自改/导师 单条 apply）；
#   · 令牌已是「已确认」。
# 装法：全局 ~/.claude/settings.json 的 hooks.PreToolUse，matcher = "Bash|PowerShell"。
# 原则：宁可漏放也不误杀——任何异常/解析失败一律放行（exit 0）。
# 注意：与 md_protect_hook 互不冲突——后者对 apply_md_changeset.py 一律放行，本闸只在"机改未确认"时 deny。

$ErrorActionPreference = 'SilentlyContinue'
try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }
    $in = $raw | ConvertFrom-Json

    $scan = ''
    try { $scan = ($in.tool_input | ConvertTo-Json -Depth 20 -Compress) } catch {}
    if ([string]::IsNullOrWhiteSpace($scan)) { $scan = $raw }

    # ① 必须是 apply_md_changeset.py 的调用
    if ($scan -notmatch 'apply_md_changeset\.py') { exit 0 }

    # tool_input 经 ConvertTo-Json 后：命令里的双引号被转义成 \"、Windows 路径反斜杠双写成 \\。
    # 必须先还原 \" 再还原 \\，否则 --changeset "路径" 的引号抽不掉 → csPath=null → 人工闸静默绕过
    # （与 BUG #2 同款的路径抽取鲁棒性问题，实测复现并修复 2026-06-18）。
    $scanPaths = $scan -replace '\\"', '"' -replace '\\\\', '\'

    # 抽取 --changeset 的值（changeset.json 路径）—— 兼容绝对路径与相对路径。
    # SKILL.md 教主控用相对形式 `--changeset swarm\changeset.json`；旧正则强制盘符 [A-Za-z]:\\
    # 会漏掉相对路径 → csPath=null → exit 0 放行 → 人工闸被静默绕过（BUG #2）。
    $csPath = $null
    $m = [regex]::Match($scanPaths, '--changeset\s+"?([^\s"]+\.json)"?')
    if ($m.Success) { $csPath = $m.Groups[1].Value }
    if ([string]::IsNullOrWhiteSpace($csPath)) { exit 0 }
    # 相对路径相对项目目录解析成绝对路径（钩子进程 cwd = 项目目录；$env:CLAUDE_PROJECT_DIR 兜底）。
    if (-not [System.IO.Path]::IsPathRooted($csPath)) {
        $resolved = $null
        foreach ($base in @((Get-Location).Path), $env:CLAUDE_PROJECT_DIR) {
            if ([string]::IsNullOrWhiteSpace($base)) { continue }
            $cand = Join-Path $base $csPath
            if (Test-Path -LiteralPath $cand) { $resolved = (Resolve-Path -LiteralPath $cand).Path; break }
        }
        if ($resolved) { $csPath = $resolved } else { exit 0 }
    }
    if (-not (Test-Path -LiteralPath $csPath)) { exit 0 }

    # ② 读 changeset.json，判断是否含 id 以「机改」开头
    $csRaw = [System.IO.File]::ReadAllText($csPath, [System.Text.Encoding]::UTF8)
    if ([string]::IsNullOrWhiteSpace($csRaw)) { exit 0 }
    $cs = $csRaw | ConvertFrom-Json
    $hasAgent = $false
    foreach ($p in @($cs.patches)) {
        if ($p -and ([string]$p.id) -match '^机改') { $hasAgent = $true; break }
    }
    if (-not $hasAgent) { exit 0 }   # 没有机改 patch = 普通手搓 apply → 放行

    # ③ 找 swarm\md_triage.md 并查令牌（changeset.json 多半就在 <workdir>\swarm\ 下）
    $triage = $null
    $jdir = Split-Path -Parent $csPath
    $cand = Join-Path $jdir 'md_triage.md'
    if (Test-Path -LiteralPath $cand) {
        $triage = $cand
    } else {
        $cand2 = Join-Path (Split-Path -Parent $jdir) 'swarm\md_triage.md'
        if (Test-Path -LiteralPath $cand2) { $triage = $cand2 }
    }

    $confirmed = $false
    if ($triage -and (Test-Path -LiteralPath $triage)) {
        $ttext = [System.IO.File]::ReadAllText($triage, [System.Text.Encoding]::UTF8)
        # 令牌行识别（语义判断·鲁棒于写法）：只认令牌行——行首可选 blockquote `>`/空格前缀 + `**人工确认`；
        # 该行含「已确认」且不含「待确认」即算已确认。容忍 ✅/⬜ status emoji、中英冒号、空格变化。
        # 旧正则只认裸 `**人工确认：** 已确认`，遇到 `> **人工确认：** ✅ 已确认`（旧 triage 模板鼓励的写法）
        # 就失配 → 已确认也被拦死（BUG #3）。限定令牌行形状可排除操作说明里提到"已确认"的行误判。
        foreach ($ln in $ttext -split "`r?`n") {
            if ($ln -notmatch '^\s*>?\s*\*\*人工确认') { continue }
            if ($ln -match '已确认' -and $ln -notmatch '待确认') { $confirmed = $true; break }
        }
    }

    if ($confirmed) { exit 0 }   # 令牌已确认 → 放行

    # 命中：机改批量 patch + 未确认 → deny
    $reason = "[md-swarm 人工闸] 检测到多 agent 批量起草的 patch（id=机改*）要 apply 进 manuscript.md，但 swarm\md_triage.md 的【人工确认】尚未改为『已确认』。请先打开 md_triage.md：逐条复核「处理决定」、处理冲突/需人类操作，再把顶部『**人工确认：** 待确认』手动改成『已确认』，然后继续。AI 不得自行确认——这一步只能由用户完成。"
    $out = @{ hookSpecificOutput = @{ hookEventName = 'PreToolUse'; permissionDecision = 'deny'; permissionDecisionReason = $reason } }
    Write-Output ($out | ConvertTo-Json -Depth 6 -Compress)
    exit 0
} catch {
    exit 0
}
