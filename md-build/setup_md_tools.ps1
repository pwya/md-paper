# setup_md_tools.ps1 -- GLOBAL (once-per-machine) installer for the md-* toolchain.
# Installs a PINNED toolchain into %LOCALAPPDATA%\md-pandoc (machine-local, NOT OneDrive,
# NOT per-project). All md-* build scripts resolve their tools from there.
#
# WHY a pinned dir instead of `winget install pandoc`:
#   pandoc-crossref 0.3.24a is compiled against pandoc 3.9.0.2 EXACTLY. A newer pandoc
#   (e.g. winget's latest) makes crossref fail SILENTLY (cross-refs leak, "fig:1 not found").
#   So we pin BOTH, together, in a dedicated dir the skills point at -- never the system pandoc.
#
# ROBUSTNESS (so a public user has the fewest headaches):
#   1. REUSE   : if pandoc 3.9.0.2 already exists (target dir or on PATH), reuse it.
#   2. BUNDLE  : the small/fragile zotero lua deps ship next to this script (bundled-lua\)
#                and are copied locally -- no network needed for those.
#   3. FALLBACK: big binaries download from GitHub with mirror fallback; if all fail, print
#                clear MANUAL steps + the $env:MD_PANDOC_HOME / $env:MD_GH_MIRROR escape hatches.
#
# Usage:  powershell -ExecutionPolicy Bypass -File setup_md_tools.ps1 [-Force] [-Mirror <prefix>]
# ASCII-only on purpose (PowerShell 5.1 mis-reads UTF-8 Chinese in .ps1 files).
param([switch]$Force, [string]$Mirror)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# ---- PINNED versions (to upgrade: bump BOTH, together; they must be a matched pair) ----
$PANDOC_VER   = '3.9.0.2'
$CROSSREF_TAG = 'v0.3.24a'
$PANDOC_URL   = "https://github.com/jgm/pandoc/releases/download/$PANDOC_VER/pandoc-$PANDOC_VER-windows-x86_64.zip"
$CROSSREF_URL = "https://github.com/lierdakil/pandoc-crossref/releases/download/$CROSSREF_TAG/pandoc-crossref-Windows-X64.7z"

# GitHub mirrors tried (in order) AFTER the direct URL fails. These rotate over time; override
# with -Mirror or $env:MD_GH_MIRROR. The prefix is prepended to the full github URL (ghproxy style).
$Mirrors = @()
if ($Mirror)           { $Mirrors += $Mirror }
if ($env:MD_GH_MIRROR) { $Mirrors += $env:MD_GH_MIRROR }
$Mirrors += @('https://ghfast.top','https://mirror.ghproxy.com','https://gh.ddlc.top')
$Mirrors = $Mirrors | Select-Object -Unique

$Home2 = if ($env:MD_PANDOC_HOME) { $env:MD_PANDOC_HOME } else { Join-Path $env:LOCALAPPDATA 'md-pandoc' }
$here  = Split-Path -Parent $MyInvocation.MyCommand.Path
$bundleBin = Join-Path $here 'bundled-bin'   # vendored pandoc.exe + pandoc-crossref.exe (offline, GFW-proof)
New-Item -ItemType Directory -Path $Home2 -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Home2 'lunajson') -Force | Out-Null
Write-Host "md-* toolchain dir: $Home2"
Write-Host ("mirrors (fallback): " + ($Mirrors -join ', '))

function Need($name) { return ($Force -or -not (Test-Path (Join-Path $Home2 $name))) }

# Try the direct URL, then each mirror; return $true on first success.
function Fetch($url, $out) {
  $tries = @($url)
  foreach ($m in $Mirrors) { $tries += ($m.TrimEnd('/') + '/' + $url) }
  $n = 0
  foreach ($u in $tries) {
    $n++
    try {
      Write-Host ("    [{0}/{1}] {2}" -f $n, $tries.Count, $u)
      Invoke-WebRequest $u -OutFile $out -TimeoutSec 60
      if ((Test-Path $out) -and ((Get-Item $out).Length -gt 0)) { return $true }
    } catch { Write-Host ("        x " + $_.Exception.Message) }
  }
  return $false
}

function Fail-Manual($desc, $url, $finalPath) {
  Write-Host ""
  Write-Host "  !! Could not download $desc from GitHub or any mirror (common behind GFW/proxy)."
  Write-Host "     Pick ONE:"
  Write-Host "       a) Download it by hand:   $url"
  Write-Host "          if it's a .zip/.7z, extract and copy the .exe to:"
  Write-Host "          $finalPath"
  Write-Host "       b) Use a working mirror:  setup_md_tools.ps1 -Mirror https://<your-mirror>"
  Write-Host "          (or set `$env:MD_GH_MIRROR before running)"
  Write-Host "       c) Already have the toolchain elsewhere? Skip this installer and set:"
  Write-Host "          `$env:MD_PANDOC_HOME='<dir with pandoc.exe + crossref + lua>'"
  throw "$desc download failed (see options above)."
}

# ---- 1. pandoc 3.9.0.2 (PINNED) -- reuse if present, else download ----
if (Need 'pandoc.exe') {
  $reused = $false
  # priority: (1) vendored OneDrive bundle (offline, exact version) -> (2) reuse PATH if exactly
  # the pinned version -> (3) download (mirror fallback). Bundle-first makes a fresh machine work
  # with zero network even behind GFW.
  $bbP = Join-Path $bundleBin 'pandoc.exe'
  if (Test-Path $bbP) {
    Write-Host "using vendored pandoc.exe from OneDrive bundle (offline)"
    Copy-Item $bbP (Join-Path $Home2 'pandoc.exe') -Force; $reused = $true
  }
  if (-not $reused) {
    $sys = Get-Command pandoc.exe -ErrorAction SilentlyContinue
    if ($sys) {
      $sv = (((& $sys.Source --version 2>$null) | Select-Object -First 1) -replace '[^0-9.]','')
      if ($sv -eq $PANDOC_VER) {
        Write-Host "reusing pandoc $PANDOC_VER already on PATH: $($sys.Source)"
        Copy-Item $sys.Source (Join-Path $Home2 'pandoc.exe') -Force; $reused = $true
      } else {
        Write-Host "note: found pandoc '$sv' on PATH but md-* needs EXACTLY $PANDOC_VER"
        Write-Host "      (crossref is compiled against it). Ignoring PATH pandoc; fetching the pinned build."
      }
    }
  }
  if (-not $reused) {
    Write-Host "downloading pandoc $PANDOC_VER ..."
    $zip = Join-Path $Home2 '_pandoc.zip'
    if (-not (Fetch $PANDOC_URL $zip)) { Fail-Manual "pandoc $PANDOC_VER" $PANDOC_URL (Join-Path $Home2 'pandoc.exe') }
    $ex = Join-Path $Home2 '_pandoc_x'
    Expand-Archive $zip -DestinationPath $ex -Force
    Copy-Item (Get-ChildItem -Recurse $ex -Filter 'pandoc.exe' | Select-Object -First 1).FullName (Join-Path $Home2 'pandoc.exe') -Force
    Remove-Item $zip,$ex -Recurse -Force
  }
} else { Write-Host "pandoc.exe present (skip)" }

# ---- 2. pandoc-crossref (PINNED, matches pandoc 3.9.0.2) ----
if (Need 'pandoc-crossref.exe') {
  # priority: vendored OneDrive bundle (offline) -> download (mirror fallback).
  $bbC = Join-Path $bundleBin 'pandoc-crossref.exe'
  if (Test-Path $bbC) {
    Write-Host "using vendored pandoc-crossref.exe from OneDrive bundle (offline)"
    Copy-Item $bbC (Join-Path $Home2 'pandoc-crossref.exe') -Force
  } else {
    Write-Host "downloading pandoc-crossref $CROSSREF_TAG ..."
    $sz = Join-Path $Home2 '_crossref.7z'
    if (-not (Fetch $CROSSREF_URL $sz)) { Fail-Manual "pandoc-crossref $CROSSREF_TAG" $CROSSREF_URL (Join-Path $Home2 'pandoc-crossref.exe') }
    $ex = Join-Path $Home2 '_crossref_x'
    New-Item -ItemType Directory -Path $ex -Force | Out-Null
    & tar.exe -xf $sz -C $ex   # Windows 11 bsdtar handles 7z
    Copy-Item (Get-ChildItem -Recurse $ex -Filter 'pandoc-crossref.exe' | Select-Object -First 1).FullName (Join-Path $Home2 'pandoc-crossref.exe') -Force
    Remove-Item $sz,$ex -Recurse -Force
  }
} else { Write-Host "pandoc-crossref.exe present (skip)" }

# ---- 3. zotero live-citation lua filter + ALL its deps ----
# Main filter keeps its real name (do NOT rename to zotero.lua -- collides with the zotero module).
# BUNDLED next to this script (bundled-lua\) -> copied locally, NO network. Download only if a file
# is somehow missing from the bundle (keeps the historically-fragile lua assembly offline-first).
$bbt = 'https://raw.githubusercontent.com/retorquere/zotero-better-bibtex/master/pandoc'
$lj  = 'https://raw.githubusercontent.com/grafi-tt/lunajson/master/src'
$luaMap = [ordered]@{
  'pandoc-zotero-live-citemarkers.lua' = "$bbt/pandoc-zotero-live-citemarkers.lua"
  'locator.lua' = "$bbt/locator.lua"
  'utils.lua'   = "$bbt/utils.lua"
  'zotero.lua'  = "$bbt/zotero.lua"
  'lunajson.lua'         = "$lj/lunajson.lua"
  'lunajson\decoder.lua' = "$lj/lunajson/decoder.lua"
  'lunajson\encoder.lua' = "$lj/lunajson/encoder.lua"
  'lunajson\sax.lua'     = "$lj/lunajson/sax.lua"
}
$bundleLua = Join-Path $here 'bundled-lua'
foreach ($k in $luaMap.Keys) {
  if (-not (Need $k)) { continue }
  $target = Join-Path $Home2 $k
  $fromBundle = Join-Path $bundleLua $k
  if (Test-Path $fromBundle) {
    Copy-Item $fromBundle $target -Force
    Write-Host "lua (bundled): $k"
  } else {
    Write-Host "lua (download): $k"
    if (-not (Fetch $luaMap[$k] $target)) { Fail-Manual "lua dep $k" $luaMap[$k] $target }
  }
}

# ---- verify ----
Write-Host "`n--- verify ---"
$pandoc = Join-Path $Home2 'pandoc.exe'
$pv = (& $pandoc --version | Select-Object -First 1)
Write-Host ("pandoc:   " + $pv)
if ($pv -notmatch [regex]::Escape($PANDOC_VER)) {
  Write-Host "  !! WARNING: expected pandoc $PANDOC_VER -- crossref may fail SILENTLY with another version."
}
Write-Host ("crossref: present=" + (Test-Path (Join-Path $Home2 'pandoc-crossref.exe')))
$luaOk = ($luaMap.Keys | Where-Object { Test-Path (Join-Path $Home2 $_) }).Count
Write-Host ("lua files: $luaOk / " + $luaMap.Count)
Write-Host "`nDONE. md-* build scripts will auto-find tools in: $Home2"
Write-Host "(override with `$env:MD_PANDOC_HOME if you installed elsewhere.)"
