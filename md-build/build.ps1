# build.ps1 -- md-build engine: compile manuscript.md -> Word via pandoc.
# Tools are resolved from the GLOBAL toolchain (%LOCALAPPDATA%\md-pandoc), installed
# once by setup_md_tools.ps1. NOT bundled per-project.
#
# Usage:
#   build.ps1 -WorkDir <dir-with-manuscript.md> -Mode live|rebuild|static|smoke [-Bib library.json] [-Out path] [-Reference reference.docx]
#     live    : real Zotero fields (needs Zotero running + Better BibTeX). Default.
#     rebuild : LIVE Zotero fields rebuilt OFFLINE from data captured at ingest
#               (manifest/objects.json + build/citemap.tsv). No Zotero needed. Existing cites only.
#               Use this for an md-unpack'd paper that added NO new refs (its citekeys are
#               provisional authorYear, not real BBT keys, so plain -Mode live would miss them).
#     static  : baked citations + bibliography from -Bib (no Zotero needed; for proofing).
#     smoke   : like live but on smoke.md (2-citekey mechanism test).
#   -SkipRefCheck      : skip the verify_refs preflight (undefined cross-references).
#   -SkipConservation  : skip the post-build figure/table/formula count-conservation gate.
# ASCII-only on purpose (PowerShell 5.1 mis-reads UTF-8 Chinese in .ps1 files).
param(
  [string]$WorkDir = ".",
  [ValidateSet('live','static','smoke','rebuild')][string]$Mode = 'live',
  [string]$Bib,
  [string]$Out,
  [string]$Reference,
  [switch]$SkipPreflight,
  [switch]$SkipRefCheck,
  [switch]$SkipConservation
)
$ErrorActionPreference = 'Stop'
$WorkDir = (Resolve-Path $WorkDir).Path

# --- resolve GLOBAL toolchain ---
$toolHome = if ($env:MD_PANDOC_HOME) { $env:MD_PANDOC_HOME } else { Join-Path $env:LOCALAPPDATA 'md-pandoc' }
$pandoc   = Join-Path $toolHome 'pandoc.exe'
$crossref = Join-Path $toolHome 'pandoc-crossref.exe'
$zlua     = Join-Path $toolHome 'pandoc-zotero-live-citemarkers.lua'
$here     = Split-Path -Parent $MyInvocation.MyCommand.Path
$zoff     = Join-Path $here 'zotero_offline.lua'   # skill-local; no toolchain dep
if (-not (Test-Path $pandoc)) {
  throw "pandoc not found in $toolHome. Run setup_md_tools.ps1 first (global install)."
}

# --- preflight (dev-manual section 7.6): WARN if the md-* protection hooks are down. build only
# READS manuscript.md (low risk), so this never blocks -- it just surfaces a wiped-hooks state
# (cc-switch) before you ship. Skips silently if python or preflight.py is unavailable.
$pf = Join-Path $here '..\md-swarm\preflight.py'
if (Test-Path $pf) {
  $pyExe = (Get-Command py -ErrorAction SilentlyContinue).Source
  if (-not $pyExe) { $pyExe = (Get-Command python -ErrorAction SilentlyContinue).Source }
  if ($pyExe) { & $pyExe $pf --mode warn --context 'md-build' }
}

$src = if ($Mode -eq 'smoke') { Join-Path $WorkDir 'smoke.md' } else { Join-Path $WorkDir 'manuscript.md' }
if (-not (Test-Path $src)) { throw "source not found: $src" }
if (-not $Out) { $Out = Join-Path $WorkDir ("build\out_" + $Mode + ".docx") }
New-Item -ItemType Directory -Path (Split-Path $Out) -Force | Out-Null
$err = Join-Path (Split-Path $Out) ("pandoc_err_" + $Mode + ".txt")

# --- preflight: deterministic reference integrity (md-swarm\verify_refs.py) ---
# HARD-gate the build on UNDEFINED cross-references ([@fig:/@tbl:/@eq:] with no {#...} definition):
# those compile to broken refs in the docx. This is exactly the gate the Phase-2 accident walked past
# (verify_refs said FAIL, the build ran anyway). Dropped-citekey detection is intentionally NOT gated
# here: it needs a pre-reconcile baseline (post-reconcile namespace drift false-positives), so it lives
# in md-swarm Phase 2 where the baseline is clean. Bypass this gate with -SkipRefCheck.
if ($Mode -ne 'smoke' -and -not $SkipRefCheck) {
  $verify = Join-Path (Split-Path -Parent $here) 'md-swarm\verify_refs.py'
  if (Test-Path $verify) {
    $pyExe = Get-Command py -ErrorAction SilentlyContinue
    if (-not $pyExe) { $pyExe = Get-Command python -ErrorAction SilentlyContinue }
    if ($pyExe) {
      Write-Host "[preflight] verify_refs: checking for undefined cross-references ..."
      & $pyExe.Source $verify --current $src
      if ($LASTEXITCODE -ne 0) {
        throw "verify_refs found hard violations (undefined [@fig:/@tbl:/@eq:] cross-references) in $src. Fix them (see the list above) before building, or pass -SkipRefCheck. This is the gate the Phase-2 build bypassed."
      }
      Write-Host "  OK  no undefined cross-references."
    } else {
      Write-Host "[preflight] verify_refs SKIPPED (no 'py'/'python' on PATH). Reference integrity NOT checked."
    }
  } else {
    Write-Host "[preflight] verify_refs SKIPPED (md-swarm\verify_refs.py not found next to md-build)."
  }
}

# --- preflight (light) ---
if ($Mode -eq 'static') {
  if (-not $Bib) {
    # Documented fallback: prefer an explicit library.json, else use the paper's OWN
    # references.json that md-unpack auto-produced (the paper's own bib). Only throw if neither exists.
    $lib = Join-Path $WorkDir 'library.json'
    $ref = Join-Path $WorkDir 'references.json'
    if     (Test-Path $lib) { $Bib = $lib }
    elseif (Test-Path $ref) { $Bib = $ref; Write-Host "[static] no -Bib/library.json -> using paper's own references.json" }
    else   { throw "static mode needs a bibliography: pass -Bib, or run md-unpack first (it writes references.json). Looked for: $lib / $ref" }
  } elseif (-not (Test-Path $Bib)) { throw "static mode: -Bib not found: $Bib" }
}
if ($Mode -eq 'live' -or $Mode -eq 'smoke') {
  # live/smoke hit Zotero on localhost; bypass any local proxy (e.g. Clash 7890) or it 502s.
  $env:NO_PROXY = "127.0.0.1,localhost,::1"; $env:no_proxy = $env:NO_PROXY
  $env:HTTP_PROXY=""; $env:HTTPS_PROXY=""; $env:http_proxy=""; $env:https_proxy=""

  # --- preflight: is Zotero + Better BibTeX actually reachable? ---
  # Without this, a closed Zotero / missing BBT / intercepting proxy yields a docx full of
  # unresolved citations with no obvious cause. The probe sets $req.Proxy=$null so it bypasses
  # any system/WinINET proxy -> a failure here means Zotero is genuinely unreachable.
  if (-not $SkipPreflight) {
    Write-Host "[preflight] probing Zotero + Better BibTeX at 127.0.0.1:23119 ..."
    $zstate = 'down'; $zcode = 0
    try {
      $req = [System.Net.HttpWebRequest]::Create('http://127.0.0.1:23119/better-bibtex/cayw?probe=probe')
      $req.Proxy = $null; $req.Timeout = 4000
      $resp = $req.GetResponse(); $zcode = [int]$resp.StatusCode; $resp.Close(); $zstate = 'ready'
    } catch [System.Net.WebException] {
      $r = $_.Exception.Response
      if ($r) { $zcode = [int]$r.StatusCode; $r.Close(); $zstate = $(if ($zcode -eq 404) { 'no-bbt' } else { 'ready' }) }
      else { $zstate = 'down' }
    } catch { $zstate = 'down' }

    if ($zstate -eq 'down') {
      Write-Host ""
      Write-Host "  X  Cannot reach Zotero at 127.0.0.1:23119 -- live/smoke citations WILL fail."
      Write-Host "     Check, in order:"
      Write-Host "       1) Is Zotero RUNNING? (open the desktop app and leave it open)"
      Write-Host "       2) Better BibTeX installed/enabled? (Zotero > Tools > Add-ons)"
      Write-Host "       3) A proxy (Clash 7890 / TUN mode) swallowing localhost? This script clears"
      Write-Host "          HTTP(S)_PROXY, but a system/TUN-level proxy needs you to turn it off or"
      Write-Host "          whitelist 127.0.0.1."
      Write-Host "       4) Did Better BibTeX change its port from the default 23119?"
      Write-Host ""
      Write-Host "     To proof WITHOUT Zotero:  build.ps1 -Mode static"
      Write-Host "     To skip this check:       build.ps1 -SkipPreflight"
      throw "Zotero preflight failed (unreachable). Aborting before pandoc so you don't get a silently-broken docx."
    } elseif ($zstate -eq 'no-bbt') {
      Write-Host ""
      Write-Host "  X  Zotero answered but Better BibTeX did not (HTTP 404 on its endpoint)."
      Write-Host "     Install/enable Better BibTeX in Zotero, then retry (or use -Mode static to proof)."
      throw "Zotero preflight failed (Better BibTeX missing). Aborting."
    } else {
      Write-Host "  OK  Zotero + Better BibTeX reachable (HTTP $zcode). Proceeding."
      # --- key-lock test (2026-07-11, real incident: dev handbook 10 P1-5) ---
      # Connectivity is NOT enough: a manuscript still in the provisional authorYear namespace
      # (md-unpack option A, reconcile never ran) resolves to NOTHING in BBT and live would emit
      # a docx where every citation is 'not found'. Test-resolve the actual keys and stop AT THE
      # DOOR with the exact fix. Asking beats trusting labels; -SkipPreflight skips this too.
      $vck = Join-Path (Split-Path -Parent $here) 'md-swarm\verify_citekeys.py'
      if (Test-Path $vck) {
        $pyExe2 = (Get-Command py -ErrorAction SilentlyContinue).Source
        if (-not $pyExe2) { $pyExe2 = (Get-Command python -ErrorAction SilentlyContinue).Source }
        if ($pyExe2) {
          & $pyExe2 $vck --manuscript $src --live-readiness
          if ($LASTEXITCODE -eq 2) {
            throw "live-readiness: NO manuscript citekey resolves in Better BibTeX (provisional authorYear namespace, md-unpack option A). Run reconcile_live.py first, or use -Mode rebuild. Commands printed above."
          }
        }
      }
    }
  } else {
    Write-Host "[preflight] skipped (-SkipPreflight). Make sure Zotero + Better BibTeX are up."
  }
}

# --- rebuild mode: build offline citemap (citeset -> stored Zotero field) from manifest ---
# Reuses the citation data ALREADY captured from Word at ingest (objects.json fieldCode +
# build/citemap.tsv key-sets). No Zotero, no Better BibTeX key reconciliation. Existing cites only;
# NEW refs added during revision won't be in the map -> they fall through (use -Mode live for those).
if ($Mode -eq 'rebuild') {
  if (-not (Test-Path $zoff)) { throw "offline filter missing: $zoff" }
  $objsPath = Join-Path $WorkDir 'manifest\objects.json'
  $cmapPath = Join-Path $WorkDir 'build\citemap.tsv'
  if (-not (Test-Path $objsPath)) { throw "rebuild needs manifest\objects.json (run md-unpack). Missing: $objsPath" }
  if (-not (Test-Path $cmapPath)) { throw "rebuild needs build\citemap.tsv (run md-unpack). Missing: $cmapPath" }
  $objs = Get-Content $objsPath -Raw -Encoding UTF8 | ConvertFrom-Json
  $ph   = $objs.placeholders
  $rows = Import-Csv $cmapPath -Delimiter "`t"
  $groups = @{}
  foreach ($r in $rows) {
    if (-not $groups.ContainsKey($r.placeholder)) { $groups[$r.placeholder] = New-Object System.Collections.ArrayList }
    [void]$groups[$r.placeholder].Add([string]$r.provisional_citekey)
  }
  $sb = New-Object System.Text.StringBuilder
  $nGroups = 0
  foreach ($cite in $groups.Keys) {
    $entry = $ph.('[' + $cite + ']')
    if (-not $entry) { continue }
    $code = [string]$entry.fieldCode
    if ($code -notmatch 'ZOTERO_ITEM') { continue }
    $disp = [string]$entry.displayText
    $keys = (($groups[$cite] | Sort-Object) -join ';')
    $code = ($code -replace "`r",' ' -replace "`n",' ')
    $disp = ($disp -replace "`r",' ' -replace "`n",' ')
    [void]$sb.AppendLine($keys + "`t" + $disp + "`t" + $code)
    $nGroups++
  }
  $offMap = Join-Path $WorkDir 'build\offline_citemap.tsv'
  [System.IO.File]::WriteAllText($offMap, $sb.ToString(), (New-Object System.Text.UTF8Encoding($false)))
  $env:MD_OFFLINE_CITEMAP = $offMap
  Write-Host ("[rebuild] offline citemap: $nGroups citation group(s) -> $offMap")

  # namespace-drift guard: if NONE of the manuscript's citekeys appear in the offline map, the
  # citemap is stale -- typically you reconciled to real Better BibTeX keys (which rewrote
  # manuscript.md) but the citemap predates that. rebuild would then silently emit EMPTY citations.
  # Fail loudly instead of shipping a broken docx.
  $manText = Get-Content $src -Raw -Encoding UTF8
  $manKeys = @{}
  foreach ($mm in [regex]::Matches($manText, '\[([^\]\[]*@[^\]\[]*)\]')) {
    foreach ($tok in ($mm.Groups[1].Value -split ';')) {
      $cm2 = [regex]::Match($tok.Trim(), '^@([^\s;,]+)')
      if ($cm2.Success) {
        $kk = $cm2.Groups[1].Value
        $kind = ($kk -split ':',2)[0].ToLower()
        if (($kind -notin @('fig','tbl','eq','sec')) -and (-not $kk.ToUpper().StartsWith('NEW'))) { $manKeys[$kk] = $true }
      }
    }
  }
  $mapKeys = @{}
  foreach ($ln in ($sb.ToString() -split "`r?`n")) {
    if (-not $ln.Trim()) { continue }
    foreach ($kk in (($ln -split "`t")[0] -split ';')) { if ($kk) { $mapKeys[$kk] = $true } }
  }
  if ($manKeys.Count -gt 0) {
    $hit = 0; foreach ($kk in $manKeys.Keys) { if ($mapKeys.ContainsKey($kk)) { $hit++ } }
    Write-Host ("[rebuild] citekey overlap with offline map: $hit / " + $manKeys.Count)
    if ($hit -eq 0) {
      throw ("rebuild: NONE of the manuscript's " + $manKeys.Count + " citekeys are in build\citemap.tsv. The citemap is stale -- you most likely reconciled citekeys to real Better BibTeX keys (which rewrote manuscript.md). Use -Mode live instead (it queries Zotero), or re-run md-unpack. This guard prevents the silent-empty-citations bug.")
    }
  }
}

# --- assemble args (crossref MUST precede citeproc/zotero so it consumes @fig:/@tbl:) ---
$args = @($src, '--filter', $crossref, '--resource-path', $WorkDir)
if     ($Mode -eq 'static')  { $args += @('--citeproc','--bibliography',$Bib) }
elseif ($Mode -eq 'rebuild') { $args += @('--lua-filter',$zoff) }
else                         { $args += @('--lua-filter',$zlua) }
# Per-project style vest (2026-07-11): when -Reference is not given but <WorkDir>\reference.docx
# exists (dropped there by the md-build SKILL's one-time format Q&A, or by the user), wear it
# automatically -- so the format answer sticks per project without re-asking on every build.
# Explicit -Reference always wins; delete the file to fall back to pandoc defaults.
if (-not $Reference) {
  $projRef = Join-Path $WorkDir 'reference.docx'
  if (Test-Path -LiteralPath $projRef) {
    $Reference = $projRef
    Write-Host "[style] wearing project vest: $projRef  (pass -Reference to override, delete file to reset)"
  }
}
if ($Reference) { $args += @('--reference-doc',$Reference) }
$args += @('-o',$Out)

Push-Location $WorkDir
# Filters (zotero.lua / zotero_offline.lua) and citeproc log to stderr; under EAP=Stop the 2>
# redirect would turn that into a terminating error and abort before reporting. Relax to Continue
# for the native call so stderr is captured to $err without throwing; $code is the real exit code.
$eapPrev = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $pandoc @args 2> $err
$code = $LASTEXITCODE
$ErrorActionPreference = $eapPrev
Pop-Location

Write-Host ("pandoc exit: " + $code)
$errTxt = Get-Content $err -Raw -ErrorAction SilentlyContinue
if ($errTxt) {
  $nf = ([regex]::Matches($errTxt,'not found')).Count
  if ($nf) { Write-Host ("WARN: " + $nf + " citation(s)/ref(s) not found -- see $err") }
  # pandoc prints a precise per-formula warning when it cannot convert a LaTeX math expression to a
  # Word equation (it then renders that formula as RAW LaTeX TEXT, exit 0). Surface it loudly and
  # specifically -- this is the real, actionable signal behind a 'formula' shortfall in the
  # conservation check below (the formula is still in the docx, just as text, not a proper equation).
  $mathBad = ([regex]::Matches($errTxt,'Could not convert TeX math')).Count
  if ($mathBad) {
    Write-Host ("WARN: " + $mathBad + " formula/formulas could NOT be converted to a Word equation -- pandoc rendered them as raw LaTeX TEXT (still present, just not a real equation, and the cause of any 'formula' shortfall reported just below). Find the exact LaTeX in $err (search 'Could not convert TeX math'), fix it in manuscript.md, then rebuild.")
  }
  if (($Mode -eq 'live' -or $Mode -eq 'smoke') -and $errTxt -match 'refused|timed out|timeout|502|Could not|ConnectionFailure') {
    Write-Host "WARN: looks like a Zotero connection problem DURING the run --"
    Write-Host "      Zotero may have closed mid-build, or a proxy intercepted localhost."
    Write-Host "      Re-open Zotero (with Better BibTeX) and rebuild; or -Mode static to proof offline."
  }
}
# Unreplaced [@NEW: ...] placeholders in the SOURCE won't resolve to a real citation.
try {
  $srcTxt = Get-Content $src -Raw -Encoding UTF8
  $nNew = ([regex]::Matches($srcTxt,'\[@NEW:')).Count
  if ($nNew) { Write-Host ("WARN: $nNew unreplaced [@NEW: ...] placeholder(s) in source -- replace with real citekeys before final build (run ..\md-swarm\verify_refs.py for the list).") }
} catch {}
$o = Get-Item $Out -ErrorAction SilentlyContinue
if ($o) {
  # --- post-build HARD GATE: word/document.xml MUST be well-formed XML ---
  # A single XML-illegal char (e.g. U+0001 from a flattened Word object/field) makes document.xml
  # non-well-formed, and Word silently refuses to open the file while pandoc still exits 0. Catch it
  # HERE, loudly, with a precise diagnosis -- instead of at submission time. (test-19 root cause.)
  try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction SilentlyContinue
    $zip = [System.IO.Compression.ZipFile]::OpenRead($Out)
    try {
      $entry = $zip.GetEntry('word/document.xml')
      if (-not $entry) { throw "word/document.xml is missing from the docx" }
      $sr = New-Object System.IO.StreamReader($entry.Open(), [System.Text.Encoding]::UTF8)
      $docXml = $sr.ReadToEnd(); $sr.Close()
    } finally { $zip.Dispose() }
    $bad = [regex]::Matches($docXml, '[\x00-\x08\x0B\x0C\x0E-\x1F]')
    if ($bad.Count -gt 0) {
      $cp = ('U+{0:X4}' -f [int][char]$bad[0].Value)
      throw ("document.xml has " + $bad.Count + " XML-illegal control char(s) (first: " + $cp + "). Word will refuse to open the file. This is a Tier-1 char leak from a flattened Word object/field -- re-run md-unpack (transform.py now strips these) or clean manuscript.md, then rebuild.")
    }
    $null = [xml]$docXml   # full well-formedness parse (catches bad entities/tags/etc.)
    Write-Host "[postflight] word/document.xml is well-formed XML -- Word will open it."
  } catch {
    Write-Host ""
    Write-Host ("  X  POSTFLIGHT FAILED: " + $_.Exception.Message)
    throw "Build produced a .docx that Word cannot open (document.xml not well-formed). See the diagnosis above."
  }

  # --- post-build CONSERVATION check (WARN-only, never blocks): figures/tables/formulas count ---
  # The XML gate above proves the docx OPENS; this surfaces whether it is COMPLETE. pandoc can drop a
  # figure/table/formula and still exit 0 (e.g. a missing image file, or an exotic formula it renders
  # as plain text), and you'd only catch it by eye at submission. verify_conservation counts objects
  # in the source pandoc AST vs the output document.xml and WARNS LOUDLY on any difference -- it
  # NEVER fails the build (a rendering quirk shouldn't block out a usable docx; the author decides).
  # Bypass entirely with -SkipConservation. (handbook P3 "verification web"; was a real gap for fig/tbl/eq.)
  if (-not $SkipConservation) {
    $cons = Join-Path $here 'verify_conservation.py'
    $pyC = Get-Command py -ErrorAction SilentlyContinue
    if (-not $pyC) { $pyC = Get-Command python -ErrorAction SilentlyContinue }
    if ((Test-Path $cons) -and $pyC) {
      $astFile = Join-Path (Split-Path $Out) ("_ast_" + $Mode + ".json")
      $eapC = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
      & $pandoc $src -t json -o $astFile 2> $null
      $astCode = $LASTEXITCODE
      if ($astCode -eq 0 -and (Test-Path $astFile)) {
        Write-Host "[postflight] conservation: counting figures/tables/formulas source vs output ..."
        & $pyC.Source $cons --ast $astFile --docx $Out --mode $Mode   # warn-only; exit code ignored on purpose
      } else {
        Write-Host "[postflight] conservation SKIPPED (could not generate source AST)."
      }
      $ErrorActionPreference = $eapC
    } else {
      Write-Host "[postflight] conservation SKIPPED (verify_conservation.py or python not available)."
    }
  }

  Write-Host ("OK -> " + $Out + " (" + $o.Length + " bytes)")
  if ($Mode -ne 'static') { Write-Host "Open in Word -> Zotero doc prefs OK -> Refresh." }
} else { Write-Host "NO OUTPUT -- see $err"; exit 1 }
