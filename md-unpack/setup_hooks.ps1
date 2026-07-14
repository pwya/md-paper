# setup_hooks.ps1 -- install Claude Code md-paper hooks without replacing unrelated hooks.
# The merge logic lives in setup_hooks.py so it is importable and regression-tested.
# ASCII-only on purpose for Windows PowerShell 5.1 compatibility.

$ErrorActionPreference = 'Stop'
$pyExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $pyExe) { $pyExe = (Get-Command py -ErrorAction SilentlyContinue).Source }
if (-not $pyExe) {
    Write-Host '[ABORT] python (or py) not on PATH.' -ForegroundColor Red
    exit 1
}

$helper = Join-Path $PSScriptRoot 'setup_hooks.py'
if (-not (Test-Path -LiteralPath $helper)) {
    Write-Host "[ABORT] helper not found: $helper" -ForegroundColor Red
    exit 1
}

& $pyExe $helper
$rc = $LASTEXITCODE
if ($rc -ne 0) {
    Write-Host "[ERROR] setup_hooks.py exited with code $rc" -ForegroundColor Red
    exit $rc
}

Write-Host ''
Write-Host 'Next (do both):' -ForegroundColor Cyan
Write-Host '  1. Restart Claude Code and open a NEW conversation.'
Write-Host '  2. Run md-swarm\verify_hooks.ps1 and expect 6/6 green.'
