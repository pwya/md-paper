# md_protect_hook.ps1
# PreToolUse 硬闸（md-swarm 真源保护）：阻止【模型】直接写 md-swarm 的纯文本真源 manuscript.md。
#
# 受保护真源 = 文件名为 manuscript.md 且【同目录有 manifest\ 子目录】（即已 md-unpack）。
# 设计：manuscript.md 在改稿期间只能由确定性管线脚本写，模型一律不许用 Write/Edit 直写——
#       多 agent 并行直写同一文件会互相覆盖、漏改（实测 Phase 2 事故根因）。
#
# 放行：
#   ① 管线脚本（apply_md_changeset.py / reconcile_*.py / verify_refs.py / ingest_manuscript.ps1 /
#      transform.py / unpack.ps1 / build.ps1）——它们写 manuscript.md 是合法的确定性写入；
#   ② 一切不涉及受保护 manuscript.md 的调用；
#   ③ Bash/PowerShell 对它的【只读】（无写意图关键字）。
# 拦截：
#   · Write / Edit / MultiEdit 工具的 file_path 指向受保护 manuscript.md（模型直写的主要途径）；
#   · Bash/PowerShell 对受保护 manuscript.md 带【写意图】的命令（Out-File / Set-Content / > 重定向 / WriteAllText 等）。
#
# 装法：全局 ~/.claude/settings.json 的 hooks.PreToolUse，matcher = "Write|Edit|MultiEdit|Bash|PowerShell"。
# 原则：宁可漏放也不误杀——任何异常/解析失败一律放行（exit 0），绝不因守门钩子本身出错而阻断工作。
# 注意：与 docx 的 protect_manuscript_hook 互不冲突——后者只认 .docx（碰 .md 直接放行），本闸只认 manuscript.md。

$ErrorActionPreference = 'SilentlyContinue'
try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }
    $in = $raw | ConvertFrom-Json
    $tool = [string]$in.tool_name

    $scan = ''
    try { $scan = ($in.tool_input | ConvertTo-Json -Depth 20 -Compress) } catch {}
    if ([string]::IsNullOrWhiteSpace($scan)) { $scan = $raw }

    if ($scan -notmatch 'manuscript\.md') { exit 0 }   # 不涉及 manuscript.md → 放行（快速退出）

    # 2026-06-22 收紧：管线脚本白名单从“整串($scan，含 Write 的 content / 命令注释)里出现脚本名就整条放行”
    # 改为“命令里确有该脚本的【真实调用】才放行”，并下移进 Bash/PowerShell 分支（见 $pipelineInvoke）。
    # Write/Edit/MultiEdit 不再享有任何管线白名单——它们永远不该直写 manuscript.md（旧版只要 content/注释里
    # 提一嘴 transform.py 就能整串命中、绕过保护，这正是 §10 P3 记的子串绕过漏）。

    function Test-Protected([string]$p) {
        if ([string]::IsNullOrWhiteSpace($p)) { return $false }
        if ((Split-Path -Leaf $p) -ne 'manuscript.md') { return $false }
        $dir = Split-Path -Parent $p
        if ([string]::IsNullOrWhiteSpace($dir)) { return $false }
        return (Test-Path -LiteralPath (Join-Path $dir 'manifest'))
    }

    $hit = $null

    if ($tool -eq 'Write' -or $tool -eq 'Edit' -or $tool -eq 'MultiEdit') {
        # 工具直写：tool_input.file_path 指向 manuscript.md 且旁有 manifest\ → 拦
        $fp = [string]$in.tool_input.file_path
        if (Test-Protected $fp) { $hit = $fp }
    }
    elseif ($tool -eq 'Bash' -or $tool -eq 'PowerShell') {
        # tool_input 经 ConvertTo-Json 后：命令里的双引号被转义成 \"、Windows 路径反斜杠双写转义；PS5.1 还把 <>& 转义成
        # \\u003c / \\u003e / \\u0026。一律还原成真实字符再检测，否则 `>`/`>>` 重定向或 "路径" 包裹的写意图会失配 → 绕过
        # （\" 还原与 gate hook 同款修复，2026-06-18）。
        $scanPaths = $scan -replace '\\"', '"' -replace '\\\\', '\' -replace '\\u003e', '>' -replace '\\u003c', '<' -replace '\\u0026', '&'
        # 2026-06-21 硬化：补 Python open(...manuscript.md...,'w'/'a') 写法 + WriteAllLines（DeepSeek 实测在 Edit 被拦后改用脚本 find-replace 绕过）。
        $writeIntent = 'Set-Content|Add-Content|Out-File|WriteAllText|WriteAllLines|\.Save\b|open\s*\([^)]*manuscript\.md[^)]*,\s*[''"][wax]|>\s*"?[^"\r\n]*manuscript\.md|>>\s*"?[^"\r\n]*manuscript\.md'
        if ($scanPaths -match $writeIntent) {
            # 抽取命令里指向 manuscript.md 的路径 token——绝对路径(盘符开头)与相对路径都收。
            # 旧正则只认 [A-Za-z]:\\ 盘符绝对路径，漏掉 `Set-Content manuscript.md` 这类相对路径写意图 = 绕过（与 gate 钩子 BUG #2 同款）。
            $paths = [regex]::Matches($scanPaths, '([A-Za-z]:\\(?:[^"\\/:*?<>|\r\n]+\\)*manuscript\.md|(?:\.[\\/])?(?:[^"\s\\/:*?<>|\r\n]*[\\/])?manuscript\.md)') |
                     ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique
            # 相对路径的解析基目录：钩子进程 cwd（=项目目录）+ CLAUDE_PROJECT_DIR 兜底
            $bases = @((Get-Location).Path)
            if ($env:CLAUDE_PROJECT_DIR) { $bases += $env:CLAUDE_PROJECT_DIR }
            foreach ($p in $paths) {
                $cand = $p
                if (-not [System.IO.Path]::IsPathRooted($p)) {
                    $cand = $null
                    foreach ($base in $bases) {
                        $j = Join-Path $base $p
                        if (Test-Path -LiteralPath $j) { $cand = $j; break }
                    }
                    if (-not $cand) { continue }   # 相对路径解析不到 = 不在本项目，放行
                }
                if (Test-Protected $cand) { $hit = $cand; break }
            }
        }
    }

    if (-not $hit) { exit 0 }

    $reason = "[受保护真源] $hit 已 md-unpack、是 md-swarm 的纯文本真源，禁止【直接】写：多 agent 并行直写会互相覆盖、漏改（这是 Phase 2 改未生效的根因）。请改走管线——子 agent 只回 patch JSON，主控汇入 swarm\changeset.json，再跑 apply_md_changeset.py（唯一能写 manuscript.md 的确定性脚本）。要手改请在编辑器里改、或临时停用本 hook。"
    $out = @{ hookSpecificOutput = @{ hookEventName = 'PreToolUse'; permissionDecision = 'deny'; permissionDecisionReason = $reason } }
    Write-Output ($out | ConvertTo-Json -Depth 6 -Compress)
    exit 0
} catch {
    exit 0
}
