# setup_hooks.ps1
# One-shot register the three md-* hooks into (~/.claude/settings.json) AND, if
# cc-switch is installed, into EVERY Claude provider's settings_config template in
# the cc-switch DB -- so cc-switch stops wiping hooks every time you switch provider.
#
# LOCATION: this lives in md-unpack/ (junctioned), so it is reachable on any machine
#   via %USERPROFILE%\.claude\skills\md-unpack\setup_hooks.ps1 -- no drive-letter /
#   OneDrive-path dependency. (The 3 hook scripts it registers + verify_hooks.ps1
#   live in md-swarm/; this registrar sits one dir over, in the workflow entry skill.)
#
# WHY THIS EXISTS:
#   cc-switch overwrites ~/.claude/settings.json from the current Claude provider's
#   settings_config template (stored in ~/.cc-switch/cc-switch.db) on switch/startup.
#   Provider templates that LACK a "hooks" key silently wipe your hooks every refresh.
#   cc-switch.db also stores ~10 deeply-escaped PowerShell permission strings in
#   permissions.allow; PowerShell 5.1's ConvertFrom-Json chokes on those, so this
#   script does ALL json read/write via python's json module (AST over regex, rule 3),
#   guaranteeing the live settings.json + every provider template get a valid hooks tree.
#
# THREE HOOKS (cross-machine junction form %USERPROFILE%\.claude\skills\md-swarm\*):
#   - md_dev_checklist_hook.ps1    Write|Edit|MultiEdit                (dev reminder, NON-blocking)
#   - md_protect_hook.ps1          Write|Edit|MultiEdit|Bash|PowerShell (hard gate 1: NEVER direct-write source)
#   - md_swarm_gate_hook.ps1       Bash|PowerShell                    (hard gate 2: token not confirmed -> deny apply)
#
# Idempotent: re-running replaces the hooks key with the canonical tree (no duplication).
# Prereq: the ~/.claude/skills/md-swarm junction must exist (built by 新电脑设置指南 one-shot script).
# After running: RESTART Claude Code (open a NEW conversation; /clear does NOT reload hooks),
# then VERIFY:
#   powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.claude\skills\md-swarm\verify_hooks.ps1"
# Expect 6/6 green.
#
# Requires python (or py). If neither is on PATH, install python or patch manually -- see 新电脑设置指南.md (手动兜底节).

$ErrorActionPreference = 'Stop'

if (-not (Get-Command python -ErrorAction SilentlyContinue) -and
    -not (Get-Command py       -ErrorAction SilentlyContinue)) {
    Write-Host '[ABORT] python (or py) not on PATH. Install python, or patch settings.json + cc-switch.db by hand.' -ForegroundColor Red
    exit 1
}

$pySrc = @'
import json, os, shutil, datetime, sqlite3, sys

CMD_TMPL = (r'cmd /c powershell -NoProfile -ExecutionPolicy Bypass -File '
            r'"%USERPROFILE%\.claude\skills\md-swarm\{}"')

hooks_tree = {'PreToolUse': [
    {'matcher': 'Write|Edit|MultiEdit',
     'hooks': [{'type': 'command', 'command': CMD_TMPL.format('md_dev_checklist_hook.ps1')}]},
    {'matcher': 'Write|Edit|MultiEdit|Bash|PowerShell',
     'hooks': [{'type': 'command', 'command': CMD_TMPL.format('md_protect_hook.ps1')}]},
    {'matcher': 'Bash|PowerShell',
     'hooks': [{'type': 'command', 'command': CMD_TMPL.format('md_swarm_gate_hook.ps1')}]},
]}

# ---- 1. LIVE settings.json (UTF-8 no BOM, LF) ----
sp = os.path.expanduser('~/.claude/settings.json')
d = json.load(open(sp, encoding='utf-8')) if os.path.exists(sp) else {}
d['hooks'] = hooks_tree
text = json.dumps(d, ensure_ascii=False, indent=2)
open(sp, 'w', encoding='utf-8', newline='\n').write(text)
print('[live]  patched %s -> PreToolUse %d hooks' % (sp, len(hooks_tree['PreToolUse'])))

# ---- 2. cc-switch DB (if installed): patch EVERY Claude provider template ----
db = os.path.expanduser('~/.cc-switch/cc-switch.db')
if not os.path.exists(db):
    print('[cc-switch] not installed -> only live settings.json patched (no DB needed).')
    print('OK'); sys.exit(0)

bakdir = os.path.join(os.path.dirname(db), 'backups')
os.makedirs(bakdir, exist_ok=True)
bak = os.path.join(bakdir, 'md_hooks_patch_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S') + '.db')
shutil.copy2(db, bak)

c = sqlite3.connect(db); cur = c.cursor()
cur.execute("SELECT id,name,is_current,settings_config FROM providers WHERE app_type='claude'")
count = 0
for pid, name, isc, sc in cur.fetchall():
    cfg = json.loads(sc) if sc else {}
    cfg['hooks'] = hooks_tree
    cur.execute('UPDATE providers SET settings_config=? WHERE id=?',
                (json.dumps(cfg, ensure_ascii=False, separators=(',', ':')), pid))
    mark = ' <== CURRENT' if isc == 1 else ''
    print('         patched template: %s%s' % (name, mark))
    count += 1
c.commit(); c.close()
print('[cc-switch] patched %d Claude provider templates in db; backup -> %s' % (count, os.path.basename(bak)))
print('OK')
'@

$tmpPy = [System.IO.Path]::GetTempFileName() + '.py'
[System.IO.File]::WriteAllText($tmpPy, $pySrc, (New-Object System.Text.UTF8Encoding($false)))
$pyExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $pyExe) { $pyExe = (Get-Command py -ErrorAction SilentlyContinue).Source }
& $pyExe $tmpPy
$rc = $LASTEXITCODE
Remove-Item $tmpPy -Force -ErrorAction SilentlyContinue
if ($rc -ne 0) { Write-Host '[ERROR] python helper exited with code ' $rc -ForegroundColor Red; exit $rc }

Write-Host ''
Write-Host 'Next (do both):' -ForegroundColor Cyan
Write-Host '  1. Restart Claude Code -> open a NEW conversation (/clear does NOT reload hooks).'
Write-Host '  2. Verify:'
Write-Host '     powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.claude\skills\md-swarm\verify_hooks.ps1"'
Write-Host '     Expect 6/6 green. Any FAIL -> red hints (junction / registration / restart / BOM).'
