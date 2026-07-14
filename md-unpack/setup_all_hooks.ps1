# setup_all_hooks.ps1 -- install md-paper hooks for Claude Code, Codex, and OpenCode.
# ASCII-only on purpose for Windows PowerShell 5.1 compatibility.

$ErrorActionPreference = 'Stop'
$pyExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $pyExe) { $pyExe = (Get-Command py -ErrorAction SilentlyContinue).Source }
if (-not $pyExe) {
    Write-Host '[ABORT] python (or py) not on PATH.' -ForegroundColor Red
    exit 1
}

$helper = Join-Path $PSScriptRoot 'setup_all_hooks.py'
& $pyExe $helper
$rc = $LASTEXITCODE
if ($rc -ne 0) { exit $rc }

Write-Host ''
Write-Host 'Restart Claude Code, Codex, and OpenCode.' -ForegroundColor Cyan
Write-Host 'Codex only: run /hooks and trust the new md-paper hook definition.'
