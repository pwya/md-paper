# verify_hooks.ps1 -- 自检：md-* 的两个保护钩子是否【真的装好了 + 真的拦得住】。
#
# 为什么需要它：两个钩子是 fail-open（没注册 / junction 缺失 / settings.json 没配 / 改了钩子没重启 /
#   .ps1 没存成 UTF-8 带 BOM —— 任一环不对，就【静默不拦、也不报警】）。一旦没拦住，md-swarm 多 agent
#   改稿就退回头号事故：并行直写、只剩一节幸存、几十篇引用静默丢。远程根本看不出是钩子没装。
#   本脚本把"静默失效"变成"大声自检"：模拟受保护场景，断言两钩子分别 deny；任一没拦就红字报警 + 列排查项。
#
# 跑法：  powershell -NoProfile -ExecutionPolicy Bypass -File verify_hooks.ps1
# 退出码：0 = 两钩子逻辑都拦得住；1 = 有钩子没拦住 / 没注册（看红字排查）。
#
# 注意：本脚本含中文，必须存成【UTF-8 带 BOM】，否则 PS 5.1 会乱码崩（与两个保护钩子同规矩）。
# 局限：本脚本验的是"钩子脚本逻辑拦得住" + "settings.json 注册了"；它【验不了】"当前会话已重启生效"
#       （harness 在会话启动时快照钩子）。改过钩子后仍需新开一个 Claude Code 会话才在会话层激活。

param([switch]$FunctionalOnly)   # 预检模式：只验"脚本在 / 逻辑拦得住 / BOM 对"，跳过 settings.json 注册检查。
                                 # 一键设置阶段钩子还没注册、没重启，用它做"注册前预检"，避免在注册项上假报红。

$ErrorActionPreference = 'Continue'
$here    = Split-Path -Parent $MyInvocation.MyCommand.Path
$protect = Join-Path $here 'md_protect_hook.ps1'
$gate    = Join-Path $here 'md_swarm_gate_hook.ps1'
$fail    = 0
$temps   = @()

function New-TempDir {
    $d = Join-Path $env:TEMP ('mdhookcheck_' + [System.IO.Path]::GetRandomFileName())
    New-Item -ItemType Directory -Path $d -Force | Out-Null
    return $d
}

function Invoke-Hook([string]$scriptPath, [string]$jsonInput) {
    # 把 JSON 喂进子进程 powershell 的真实 stdin（与 harness 调用钩子的方式一致），取回 stdout。
    $out = $jsonInput | & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $scriptPath 2>$null
    if ($null -eq $out) { return '' }
    return ($out -join "`n")
}

function Is-Deny([string]$hookOut) {
    return ($hookOut -match '"permissionDecision"\s*:\s*"deny"')
}

Write-Host "=== md-* 钩子自检 ===" -ForegroundColor Cyan

# ---- 0. 钩子脚本文件在不在（junction / 真身路径能不能找到）----
foreach ($p in @($protect, $gate)) {
    if (-not (Test-Path -LiteralPath $p)) {
        Write-Host ("  [FAIL] 钩子脚本不存在: " + $p) -ForegroundColor Red
        $fail++
    }
}
if ($fail -gt 0) {
    Write-Host "钩子脚本都找不到，后面没法测。先确认 junction / 路径。" -ForegroundColor Red
    exit 1
}

# ---- 1. settings.json 里注册了没（没注册 = 会话层根本不会调用 = 等于没装）----
if ($FunctionalOnly) {
    Write-Host "  [skip] 预检模式：跳过 settings.json 注册检查（注册钩子 + 重启后，用无参 verify_hooks.ps1 复验）" -ForegroundColor Yellow
} else {
    $settings = Join-Path $env:USERPROFILE '.claude\settings.json'
    if (Test-Path -LiteralPath $settings) {
        $sjson = Get-Content -LiteralPath $settings -Raw -Encoding UTF8
        foreach ($name in @('md_protect_hook', 'md_swarm_gate_hook')) {
            if ($sjson -match $name) {
                Write-Host ("  [OK]   settings.json 已注册 " + $name) -ForegroundColor Green
            } else {
                Write-Host ("  [FAIL] settings.json 未注册 " + $name + " —— 没注册则会话层不会调用它，钩子静默失效。") -ForegroundColor Red
                $fail++
            }
        }
    } else {
        Write-Host ("  [FAIL] 找不到 settings.json: " + $settings) -ForegroundColor Red
        $fail++
    }
}

# ---- 2. protect 钩子：直写【受保护】manuscript.md（旁有 manifest\）应 deny ----
$T1 = New-TempDir; $temps += $T1
New-Item -ItemType Directory -Path (Join-Path $T1 'manifest') -Force | Out-Null
Set-Content -LiteralPath (Join-Path $T1 'manuscript.md') -Value 'x' -Encoding UTF8
$in1  = @{ tool_name = 'Write'; tool_input = @{ file_path = (Join-Path $T1 'manuscript.md'); content = 'x' } } | ConvertTo-Json -Compress -Depth 6
$out1 = Invoke-Hook $protect $in1
if (Is-Deny $out1) {
    Write-Host "  [OK]   protect 钩子拦住了【直写受保护 manuscript.md】" -ForegroundColor Green
} else {
    Write-Host "  [FAIL] protect 钩子【没拦住】直写受保护 manuscript.md —— 单写者保护失效！" -ForegroundColor Red
    $fail++
}

# ---- 2b. 反向对照：直写【未受保护】manuscript.md（无 manifest\）应【放行】（证明不是"全拦"）----
$T2 = New-TempDir; $temps += $T2
Set-Content -LiteralPath (Join-Path $T2 'manuscript.md') -Value 'x' -Encoding UTF8
$in2  = @{ tool_name = 'Write'; tool_input = @{ file_path = (Join-Path $T2 'manuscript.md'); content = 'x' } } | ConvertTo-Json -Compress -Depth 6
$out2 = Invoke-Hook $protect $in2
if (Is-Deny $out2) {
    Write-Host "  [FAIL] protect 钩子把【未受保护】的 manuscript.md 也拦了 —— 误杀（不该拦没 manifest 的）。" -ForegroundColor Red
    $fail++
} else {
    Write-Host "  [OK]   protect 钩子放行了未受保护的 manuscript.md（不是无脑全拦）" -ForegroundColor Green
}

# ---- 3. gate 钩子：apply【机改】批量 patch 且令牌【未确认】应 deny ----
$W = New-TempDir; $temps += $W
New-Item -ItemType Directory -Path (Join-Path $W 'swarm') -Force | Out-Null
$csPath = Join-Path $W 'swarm\changeset.json'
'{"source_md":"manuscript.md","patches":[{"id":"机改-1","mode":"patch","intent":"modify","find":"a","replace":"b"}]}' |
    Set-Content -LiteralPath $csPath -Encoding UTF8
$triagePath = Join-Path $W 'swarm\md_triage.md'
Set-Content -LiteralPath $triagePath -Value '> **人工确认：** 待确认' -Encoding UTF8
$cmd  = 'python apply_md_changeset.py --changeset "' + $csPath + '" --manuscript manuscript.md'
$in3  = @{ tool_name = 'Bash'; tool_input = @{ command = $cmd } } | ConvertTo-Json -Compress -Depth 6
$out3 = Invoke-Hook $gate $in3
if (Is-Deny $out3) {
    Write-Host "  [OK]   gate 钩子拦住了【令牌未确认却 apply 机改批量 patch】" -ForegroundColor Green
} else {
    Write-Host "  [FAIL] gate 钩子【没拦住】未确认的机改 apply —— 人工闸失效！" -ForegroundColor Red
    $fail++
}

# ---- 3b. 正向对照：同样的 apply，但令牌已【已确认】应【放行】（证明 gate 尊重确认）----
Set-Content -LiteralPath $triagePath -Value '> **人工确认：** 已确认' -Encoding UTF8
$out4 = Invoke-Hook $gate $in3
if (Is-Deny $out4) {
    Write-Host "  [FAIL] gate 钩子把【已确认】的 apply 也拦了 —— 误杀（确认后就该放行）。" -ForegroundColor Red
    $fail++
} else {
    Write-Host "  [OK]   gate 钩子放行了【已确认】后的 apply（尊重人工确认）" -ForegroundColor Green
}

# ---- 清理临时目录 ----
foreach ($d in $temps) { Remove-Item -LiteralPath $d -Recurse -Force -ErrorAction SilentlyContinue }

# ---- 总结 ----
Write-Host ""
if ($fail -eq 0) {
    Write-Host "=== 结果：两个钩子逻辑都拦得住、且不误杀。 ===" -ForegroundColor Green
    if ($FunctionalOnly) {
        Write-Host "预检模式：只验了脚本/逻辑/BOM；【注册 + 会话激活尚未验】。注册钩子并新开会话后，跑无参 verify_hooks.ps1 复验全绿。" -ForegroundColor Yellow
    } else {
        Write-Host "提醒：若你刚改过钩子脚本，仍需【新开一个 Claude Code 会话】才在会话层生效。" -ForegroundColor Yellow
    }
    exit 0
} else {
    Write-Host ("=== 结果：有 " + $fail + " 项没过 —— 钩子可能没真正生效（fail-open）。 ===") -ForegroundColor Red
    Write-Host "逐项排查：" -ForegroundColor Red
    Write-Host "  1) junction 在不在：~/.claude/skills/md-swarm 是否指向真身（缺了则会话找不到脚本，静默失效）" -ForegroundColor Yellow
    Write-Host "  2) settings.json 注册没：PreToolUse 里有没有 md_protect_hook / md_swarm_gate_hook 两条" -ForegroundColor Yellow
    Write-Host "  3) 重启没：改过钩子要新开一个 Claude Code 会话才激活" -ForegroundColor Yellow
    Write-Host "  4) 编码对不对：两个保护钩子 .ps1 必须存成 UTF-8 带 BOM（否则 PS 5.1 乱码崩、钩子整个失效）" -ForegroundColor Yellow
    Write-Host "  详见《新电脑设置指南》的 md-* 防呆硬闸 Hook 节。" -ForegroundColor Yellow
    exit 1
}
