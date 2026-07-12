# unpack.ps1 -- md-unpack orchestrator: ingest manifest -> pandoc manuscript.md.
# Prereq: just the source .docx. If there's no manifest/ yet, this runs the BUNDLED ingest
#         (Word COM) internally to build it (manuscript.md + objects.json + images/) -- no docx-* skill needed.
# Steps: (1) pandoc direct-convert the docx (for OMML LaTeX harvest) -> build/direct.md
#        (2) transform.py: placeholder md + objects.json + direct.md -> pandoc manuscript.md
# Then reconcile provisional citekeys to real ones (see SKILL.md; live md-unpack uses Zotero,
# offline fallback = reconcile_citekeys.py).
# ASCII-only on purpose (PowerShell 5.1 mis-reads UTF-8 Chinese in .ps1 files).
param(
  [Parameter(Mandatory=$true)][string]$WorkDir,
  [string]$SourceDocx,
  [string]$ManifestDir,
  [string]$Title = "",
  [switch]$AllowNoHooks   # escape hatch: ingest even if the md-* protection hooks are down
)
$ErrorActionPreference = 'Stop'
$WorkDir = (Resolve-Path $WorkDir).Path
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

# --- preflight gate (dev-manual section 7.6): md-unpack is where the protected manuscript.md
# is BORN, so verify the protection hooks are live before creating it. HARD-block if they are
# down (cc-switch silently wipes them). Override with -AllowNoHooks. Degrades to a skip if
# python or preflight.py is unavailable -- never crash the ingest over the check itself.
$pf = Join-Path $here '..\md-swarm\preflight.py'
if (Test-Path $pf) {
  $pyExe = (Get-Command py -ErrorAction SilentlyContinue).Source
  if (-not $pyExe) { $pyExe = (Get-Command python -ErrorAction SilentlyContinue).Source }
  if ($pyExe) {
    $pfArgs = @($pf, '--mode', 'block', '--context', 'md-unpack')
    if ($AllowNoHooks) { $pfArgs += '--allow-no-hooks' }
    & $pyExe @pfArgs
    if ($LASTEXITCODE -ne 0) {
      throw "preflight blocked: md-* protection hooks are down (see output above + dev-manual 7.6). Fix with setup_hooks.ps1 + restart, or re-run with -AllowNoHooks."
    }
  } else {
    Write-Host "[preflight] python not found -- skipping protection-hook check (install python to enable)."
  }
} else {
  Write-Host "[preflight] md-swarm/preflight.py not found -- skipping protection-hook check."
}

# source docx (needed if we must run docx-unpack ourselves)
if (-not $SourceDocx) {
  $SourceDocx = (Get-ChildItem $WorkDir -Filter *.docx | Where-Object { $_.Name -notmatch '_revised_|out_' } | Select-Object -First 1).FullName
}

# md-unpack is the SINGLE ENTRY POINT of the md-* workflow: if there's no manifest yet,
# it runs docx-unpack INTERNALLY (the user only ever types /md-unpack, never /docx-unpack).
if (-not $ManifestDir) { $ManifestDir = Join-Path $WorkDir 'manifest' }
$phMd = Join-Path $ManifestDir 'manuscript.md'
$objs = Join-Path $ManifestDir 'objects.json'
if (-not (Test-Path $phMd) -or -not (Test-Path $objs)) {
  if (-not ($SourceDocx -and (Test-Path $SourceDocx))) {
    throw "No manifest and no source .docx in $WorkDir. Pass -SourceDocx or put the original .docx there."
  }
  # BUNDLED ingest copy -> md-* depends on NO docx-* skill (self-contained, publishable).
  $ingest = Join-Path $here 'ingest_manuscript.ps1'
  if (-not (Test-Path $ingest)) { throw "bundled ingest engine missing: $ingest" }
  Write-Host "no manifest yet -> running bundled ingest (Word COM) ..."
  & powershell -ExecutionPolicy Bypass -File $ingest -DocxPath $SourceDocx -OutDir $ManifestDir
  if (-not (Test-Path $phMd) -or -not (Test-Path $objs)) { throw "bundled ingest ran but manifest is incomplete ($phMd / $objs)." }
}
$outMd = Join-Path $WorkDir 'manuscript.md'

# global pandoc
$toolHome = if ($env:MD_PANDOC_HOME) { $env:MD_PANDOC_HOME } else { Join-Path $env:LOCALAPPDATA 'md-pandoc' }
$pandoc = Join-Path $toolHome 'pandoc.exe'
if (-not (Test-Path $pandoc)) { throw "pandoc not found in $toolHome. Run ..\md-build\setup_md_tools.ps1 first." }

# resolve the Python launcher: prefer 'py', fall back to 'python' (same as build.ps1).
# Hard-coding 'py' breaks on machines that only expose 'python' on PATH.
$pyExe = Get-Command py -ErrorAction SilentlyContinue
if (-not $pyExe) { $pyExe = Get-Command python -ErrorAction SilentlyContinue }
if (-not $pyExe) { throw "Python not found on PATH (need 'py' or 'python'). Install Python 3 from python.org and retry." }

New-Item -ItemType Directory -Path (Join-Path $WorkDir 'build') -Force | Out-Null
$direct = Join-Path $WorkDir 'build\direct.md'
$directJson = Join-Path $WorkDir 'build\direct.json'

# (1) direct conversion for OMML LaTeX (optional but recommended).
# Also emit a pandoc JSON AST: transform.py harvests equations from the AST (which labels
# math vs plain text), so a literal $currency$ in prose is never mistaken for an equation.
if ($SourceDocx -and (Test-Path $SourceDocx)) {
  Write-Host "pandoc direct-convert (OMML->LaTeX harvest) ..."
  & $pandoc $SourceDocx -o $direct --extract-media (Join-Path $WorkDir 'build\_media') --wrap=none 2>$null
  & $pandoc $SourceDocx -t json -o $directJson 2>$null
}

# (2) transform
$imgSrc = if (Test-Path (Join-Path $ManifestDir 'images')) { Join-Path $ManifestDir 'images' } else { '' }
Write-Host "transform manifest -> pandoc manuscript.md ..."
$pyArgs = @(
  (Join-Path $here 'transform.py'),
  '--placeholder-md', $phMd,
  '--objects-json', $objs,
  '--direct-md', $direct,
  '--direct-json', $directJson,
  '--out-md', (Join-Path $WorkDir 'manuscript.md'),
  '--references-out', (Join-Path $WorkDir 'references.json'),
  '--citemap-out', (Join-Path $WorkDir 'build\citemap.tsv'),
  '--images-dst', (Join-Path $WorkDir 'images')
)
if ($imgSrc) { $pyArgs += @('--images-src', $imgSrc) }
# Title: explicit -Title wins; otherwise use the title auto-detected at ingest (objects.json detectedTitle).
if (-not $Title) {
  try {
    $oj = Get-Content $objs -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($oj.detectedTitle) { $Title = [string]$oj.detectedTitle; Write-Host "auto-detected title: $Title" }
  } catch {}
}
if ($Title)  { $pyArgs += @('--title', $Title) }
& $pyExe.Source @pyArgs
if ($LASTEXITCODE -ne 0) { throw "transform.py failed (exit $LASTEXITCODE) -- manuscript.md may be missing or partial; see the output above." }

# ---- harvest Word comments (批注) if the source docx has any ----
# Comments are REVISION INTENT, not manuscript content: while the docx is already open we only
# HARVEST them here into a side-artifact (swarm/comments_raw.json); md-triage turns them into a
# checklist. No-op (writes nothing) for a clean draft with no comments. Best place to do it: the
# anchor->manuscript mapping is most accurate where we control the ingest. Failure is non-fatal.
if ($SourceDocx -and (Test-Path $SourceDocx)) {
  $cmtScript = Join-Path $here 'read_docx_comments.py'
  $cmtOut = Join-Path $WorkDir 'swarm\comments_raw.json'
  if (Test-Path $cmtScript) {
    Write-Host "scan for Word comments (批注) ..."
    & $pyExe.Source $cmtScript --docx $SourceDocx --out $cmtOut
    if (Test-Path $cmtOut) {
      Write-Host ""
      Write-Host "NOTE: this docx had Word comments -> harvested to $cmtOut"
      Write-Host "      Run /md-triage to turn them into a revision checklist (it reads this file)."
    }
  }
}

# ---- citation decision fork (surface the choice HERE, where the user usually already knows
#      whether they'll add new refs, and where reconciliation belongs) ----
$nck = 0
try { $nck = ([regex]::Matches((Get-Content $outMd -Raw -Encoding UTF8), '\[@(?!fig:|tbl:|eq:|sec:)')).Count } catch {}
Write-Host ""
Write-Host "================ NEXT: citations -- choose your path ================"
Write-Host " Your ~$nck citations are PROVISIONAL keys, but they are fully backed by the data"
Write-Host " captured from Word at ingest (manifest/objects.json: full CSL + Zotero item keys)."
Write-Host " You do NOT need to reconcile to real Better BibTeX keys UNLESS you will ADD"
Write-Host " references that were not already in the original paper."
Write-Host ""
Write-Host " (A) NOT adding new refs -> nothing to reconcile; build straight away, no Zotero:"
Write-Host "       /md-build -Mode static    (baked citations + auto bibliography; for proofing)"
Write-Host "       /md-build -Mode rebuild   (LIVE Zotero fields rebuilt OFFLINE from captured data)"
Write-Host ""
Write-Host " (B) ADDING new refs (they live only in your Zotero) -> reconcile keys first:"
Write-Host "       keep Zotero open OR export Better-CSL-JSON library.json, reconcile (see SKILL.md),"
Write-Host "       then /md-build -Mode live  (queries Zotero by citekey; old + new uniform & live)"
Write-Host "===================================================================="
