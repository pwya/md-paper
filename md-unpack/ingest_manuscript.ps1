# ingest_manuscript.ps1
# 把原稿 docx 摄取为占位符化的 manifest/ 目录，供下游任何消费 manifest/ 的 skill 共享只读。
# 全程不修改原稿——所有改动只发生在 temp 副本上，关闭时 SaveChanges=False，再删除副本。
#
# 入参：
#   -DocxPath   原稿 .docx 绝对路径
#   -OutDir     manifest 输出目录（不存在会自动创建）
#
# 产物：
#   <OutDir>/manuscript.md             占位符化全文（章节标题为 ## 二级标题）
#   <OutDir>/manuscript_sections/      按 OutlineLevel=1 切片（带 YAML frontmatter）
#   <OutDir>/objects.json              占位符 ↔ 原始对象映射 + 章节索引
#   <OutDir>/sections_index.md         真实章节标题清单
#   <OutDir>/footnotes.md              脚注全文
#   <OutDir>/images/                   提取的图片 + caption 索引
#   <OutDir>/ingest_warnings.md        单对象处理失败日志
#
# 退出码：
#   0  成功（哪怕个别对象失败也算）
#   2  致命错误（Word COM 不可用 / docx 打不开 / 输出路径不可写）

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$DocxPath,

    [Parameter(Mandatory = $true)]
    [string]$OutDir,

    # B 方案（书签锚点）：默认关。开启后给每个域/公式范围打 __INGEST_ 临时书签，
    # 阶段2 按书签重解析活范围再替换——对任意结构改动免疫，代价是数百次额外 COM 往返。
    # 默认走 A 方案（normalize 先于 capture）+ 回归断言；断言一旦报警，调用方应提示用户加本开关重摄取。
    [switch]$UseBookmarkAnchor
)

$ErrorActionPreference = 'Stop'

# B/断言 相关脚本级状态
$script:UseBookmarkAnchor = [bool]$UseBookmarkAnchor
$script:anchorIdx    = 0   # __INGEST_ 临时书签自增序号
$script:collapseCount = 0  # 回归断言命中次数（范围塌缩到文首被拦截）

# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------

function Write-Utf8NoBom {
    param([string]$Path, [string]$Text)
    $dir = Split-Path -Path $Path -Parent
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    [System.IO.File]::WriteAllText($Path, $Text, [System.Text.UTF8Encoding]::new($false))
}

function Write-JsonUtf8 {
    param([object]$Obj, [string]$Path)
    $json = $Obj | ConvertTo-Json -Depth 12
    Write-Utf8NoBom -Path $Path -Text $json
}

function Slugify {
    param([string]$Text, [int]$Index)
    if ([string]::IsNullOrWhiteSpace($Text)) { return ('{0:D2}_section' -f $Index) }
    $s = $Text -replace '[^\w一-龥]+', '_'
    $s = $s.Trim('_').ToLower()
    if ($s.Length -gt 40) { $s = $s.Substring(0, 40) }
    if ([string]::IsNullOrWhiteSpace($s)) { $s = 'section' }
    return ('{0:D2}_{1}' -f $Index, $s)
}

function Get-Sha256 {
    param([string]$Path)
    try {
        $h = Get-FileHash -LiteralPath $Path -Algorithm SHA256
        return $h.Hash.ToLower()
    } catch { return $null }
}

function Append-Warning {
    param([string]$Msg)
    $script:warningsLog += "- $Msg`n"
}

# B 方案锚点：给活范围打一个隐藏临时书签（名以 _ 开头→Word 视为隐藏，默认不进 Bookmarks 枚举），
# 返回书签名；未开 -UseBookmarkAnchor 或失败时返回 $null。阶段2 用它重解析活范围，最后统一清理。
function Make-Anchor {
    param($Doc, $Range)
    if (-not $script:UseBookmarkAnchor) { return $null }
    $script:anchorIdx++
    $bn = "__INGEST_$($script:anchorIdx)"
    try { $null = $Doc.Bookmarks.Add($bn, $Range); return $bn }
    catch { Append-Warning ("锚点书签创建失败 [$bn]: " + $_.Exception.Message); return $null }
}

function Normalize-DisplayText {
    param([string]$Text)
    if ($null -eq $Text) { return '' }
    # 智能引号 → 直引号；NBSP → 空格
    $Text = $Text -replace [char]0x201C, '"' -replace [char]0x201D, '"' `
                  -replace [char]0x2018, "'" -replace [char]0x2019, "'"
    $Text = $Text -replace [char]0x00A0, ' '
    # Word field 边界字符（私用区，常出现在 Range.Text 中）
    $Text = $Text -replace [char]0x0013, '' -replace [char]0x0014, '' -replace [char]0x0015, ''
    # 制表符、回车
    $Text = $Text -replace "`t", ' '
    $Text = $Text -replace "`r`n", "`n" -replace "`r", "`n"
    return $Text
}

# I2（§6.2）：按 sequence 名把 SEQ 题注分类为 fig / tbl / unknown。中英都认；不再硬编 FIGURE/TABLE。
function Classify-SeqName {
    param([string]$Name)
    if ([string]::IsNullOrWhiteSpace($Name)) { return 'unknown' }
    if ($Name -match '(?i)^(figure|fig|chart|graph|plate|illustration|illus)') { return 'fig' }
    if ($Name -match '^(图|图片|插图|示意图|附图)')                              { return 'fig' }
    if ($Name -match '(?i)^(table|tab)')                                          { return 'tbl' }
    if ($Name -match '^(表|表格|附表)')                                           { return 'tbl' }
    return 'unknown'
}

# I2（§6.2 step 3）：在 SEQ 域所在段落里探出题注 4 段格式。
#   label+sep1 = 域【前】同段文字(尾随空白=sep1，其余=label)；sep2 = 域【后】到标题前的前导空白/标点；
#   numStyle 取自 fieldCode 的 \* ARABIC 等；position 按类默认(图 below / 表 above，精确上下位检测留待后续)。
function Probe-CaptionFormat {
    param($Doc, $Field, $FieldRange, [string]$SeqName, [string]$Class)
    $paraRng = $FieldRange.Paragraphs.First.Range
    $pStart = [int]$paraRng.Start; $pEnd = [int]$paraRng.End
    $cStart = [int]$Field.Code.Start; $rEnd = [int]$Field.Result.End
    $before = ''; $after = ''
    try { if (($cStart - 1) -gt $pStart) { $before = [string]$Doc.Range($pStart, $cStart - 1).Text } } catch {}
    try { if (($rEnd + 1) -lt $pEnd)     { $after  = [string]$Doc.Range($rEnd + 1, $pEnd).Text } } catch {}
    $before = (Normalize-DisplayText $before) -replace "[`r`n`a`f`v`0]", ''
    $after  = (Normalize-DisplayText $after)  -replace "[`r`n`a`f`v`0]", ''
    $label = $before -replace '\s+$', ''
    $sep1 = ''; if ($before -match '(\s+)$') { $sep1 = $matches[1] }
    $sep2 = ''; if ($after -match '^(\s*[.:、，。)]?\s*)') { $sep2 = $matches[1] }
    $numStyle = 'ARABIC'
    $codeTxt = ''; try { $codeTxt = [string]$Field.Code.Text } catch {}
    if ($codeTxt -match '\\\*\s*([A-Za-z]+)') { $numStyle = $matches[1].ToUpperInvariant() }
    $position = if ($Class -eq 'tbl') { 'above' } else { 'below' }
    return [ordered]@{ label = $label; sep1 = $sep1; sequence = $SeqName; numStyle = $numStyle; sep2 = $sep2; position = $position }
}

# I2（§6.2 step 4）：把同类多个题注格式聚合成主档案——取 label 出现最多那组的首个为代表（6 字段）。
function Aggregate-CaptionFormat {
    param($List)
    if (-not $List -or @($List).Count -eq 0) { return $null }
    $byLabel = @($List) | Group-Object { [string]$_.label } | Sort-Object Count -Descending
    $r = $byLabel[0].Group[0]
    return [ordered]@{ label = [string]$r.label; sep1 = [string]$r.sep1; sequence = [string]$r.sequence; numStyle = [string]$r.numStyle; sep2 = [string]$r.sep2; position = [string]$r.position }
}

# I1（§6.1）：把一张 Word 表渲染成 markdown 表（首行当表头）。best-effort：合并单元格取不到时该格留空。
function Render-TableMd {
    param($Table)
    $rows = [int]$Table.Rows.Count; $cols = [int]$Table.Columns.Count
    $lines = New-Object System.Collections.ArrayList
    for ($r = 1; $r -le $rows; $r++) {
        $cells = @()
        for ($c = 1; $c -le $cols; $c++) {
            $tx = ''
            try { $tx = ("$($Table.Cell($r,$c).Range.Text)" -replace "[`r`n`a`f`v`0`t]", ' ' -replace '\s+', ' ').Trim() } catch { $tx = '' }
            $cells += ($tx -replace '\|', '\|')
        }
        $null = $lines.Add('| ' + ($cells -join ' | ') + ' |')
        if ($r -eq 1) { $null = $lines.Add('|' + (' --- |' * $cols)) }
    }
    return ($lines -join "`n")
}

function Extract-Images-From-InlineGroup {
    param([object]$groupShape)
    $wdInlineShapePicture = 3
    $wdInlineShapeLinkedPicture = 14
    foreach ($item in $groupShape.GroupItems) {
        try {
            $itemType = [int]$item.Type
            if ($itemType -eq 12) {
                Extract-Images-From-InlineGroup -groupShape $item
            } elseif ($itemType -eq $wdInlineShapePicture -or $itemType -eq $wdInlineShapeLinkedPicture) {
                $script:figIdx++
                $caption = "Figure $($script:figIdx)"
                $ph = "[FIG-$($script:figIdx): $caption]"
                $script:placeholders[$ph] = [ordered]@{
                    type        = 'image'
                    caption     = $caption
                    src         = "manifest/images/image_$($script:figIdx).png"
                    displayText = $caption
                }
                $script:imagesIndex.Add(@{
                    placeholder = $ph
                    caption     = $caption
                    src         = "manifest/images/image_$($script:figIdx).png"
                }) | Out-Null
                $rng = $item.Range
                $script:replacePlan.Add(@{
                    range = $rng; sortStart = [int]$rng.Start
                    replacement = $ph
                    type = 'image'
                }) | Out-Null
            }
        } catch {}
    }
}

# ----------------------------------------------------------------------
# 路径准备 + 副本
# ----------------------------------------------------------------------

if (-not (Test-Path -LiteralPath $DocxPath)) {
    Write-Error "DocxPath 不存在: $DocxPath"
    exit 2
}

if (-not (Test-Path -LiteralPath $OutDir)) {
    New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
}
$sectionsDir = Join-Path $OutDir 'manuscript_sections'
$imagesDir   = Join-Path $OutDir 'images'
New-Item -ItemType Directory -Path $sectionsDir -Force | Out-Null
New-Item -ItemType Directory -Path $imagesDir   -Force | Out-Null

$tempDir = Join-Path $env:TEMP ("ingest_" + [Guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
$tempDocx = Join-Path $tempDir ([System.IO.Path]::GetFileName($DocxPath))
Copy-Item -LiteralPath $DocxPath -Destination $tempDocx -Force

$sourceSha = Get-Sha256 -Path $DocxPath
$script:warningsLog = ""

# ----------------------------------------------------------------------
# 启动 Word
# ----------------------------------------------------------------------

try {
    $word = New-Object -ComObject Word.Application
} catch {
    Write-Error "Word COM unavailable: $($_.Exception.Message)"
    Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
    exit 2
}
$word.Visible = $false
$word.DisplayAlerts = 0

# Word 常量
$wdInlineShapeEmbeddedOLEObject = 1
$wdInlineShapePicture            = 3
$wdInlineShapeLinkedPicture      = 4
$wdOutlineLevelBodyText          = 10

# 占位符注册表
$placeholders = [ordered]@{}
$citeIdx = 0; $eqIdx = 0; $eqOmmlIdx = 0; $eqnumIdx = 0
$xrefEqIdx = 0; $xrefFigIdx = 0; $xrefTblIdx = 0; $xrefSecIdx = 0
$seqFigIdx = 0; $seqTblIdx = 0
$fnIdx = 0; $figIdx = 0

# I2 题注格式探测累积器（每类一份列表，循环后聚合成 caption_formats 主档案）
$capFmtFig = New-Object System.Collections.ArrayList
$capFmtTbl = New-Object System.Collections.ArrayList

# I1 原生表格 → [TBL-N]（数+定位+保护+md 渲染）
$tblIdx = 0
$tablesIndex = New-Object System.Collections.ArrayList   # 供报告：每张表 ph/rows/cols/skipped

# 替换计划（按 Range.Start 倒序处理）
# 每项：@{ start = int; end = int; replacement = string; type = string }
$replacePlan = New-Object System.Collections.ArrayList

# 嵌入图片导出索引
$imagesIndex = New-Object System.Collections.ArrayList

# 章节占位回填映射（用于 footnote 正文采集）
$footnoteBodies = New-Object System.Collections.ArrayList

$doc = $null
try {
    # 打开副本（读写，但关闭时不保存）
    try {
        $doc = $word.Documents.Open([string]$tempDocx, $false, $false, $false)
    } catch {
        Write-Error "打开 docx 失败: $($_.Exception.Message)"
        try { $word.Quit() } catch {}
        Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
        exit 2
    }

    # ------------------------------------------------------------------
    # 阶段 0：normalize —— 把所有会改动文档结构的操作集中在这里，
    #         必须先于阶段 1 的任何对象捕获（否则缓存的活范围会被后续重排塌缩到文首）。
    # ------------------------------------------------------------------

    # ---- 0.1 浮动图片转 InlineShape（确保所有图片被占位符覆盖）----
    # Group 形状需递归 Ungroup 释放为独立 Shape，再逐一转换。
    # ConvertToInlineShape 对 GroupItems 内的子元素会返回 E_FAIL。
    $msoPicture = 13
    $msoLinkedPicture = 14
    $msoGroup = 6
    try {
        # 递归 Ungroup：反复展开直到没有 Group 为止。
        # 改进：本轮一个都没拆成功（剩下的全是锁定组合）→ 立即跳出，不再空转剩余轮次；
        #       锁定的组合按名字去重，循环后只合并报 1 条警告（旧逻辑会每轮每个失败各报一条 → 噪音）。
        $maxUngroupRounds = 10
        $lockedGroups = @{}   # name -> 子项类型摘要（只记一次）
        for ($round = 0; $round -lt $maxUngroupRounds; $round++) {
            $groupsThisRound = @()
            foreach ($sh in $doc.Shapes) {
                try {
                    if ([int]$sh.Type -eq $msoGroup) { $groupsThisRound += $sh }
                } catch {}
            }
            if ($groupsThisRound.Count -eq 0) { break }
            $ungroupedAny = $false
            foreach ($sh in $groupsThisRound) {
                $gname = ''; try { $gname = [string]$sh.Name } catch {}
                try { $null = $sh.Ungroup(); $ungroupedAny = $true }
                catch {
                    if (-not $lockedGroups.ContainsKey($gname)) {
                        $kids = ''
                        try { $kids = (@($sh.GroupItems | ForEach-Object { [int]$_.Type }) -join ',') } catch {}
                        $lockedGroups[$gname] = $kids
                    }
                }
            }
            if (-not $ungroupedAny) { break }   # 本轮无任何成功 → 剩余皆锁死，停止空转
        }
        # 拆不开的锁定组合：合并报 1 条信息完整的警告（不再每轮每个各报一条）。
        # 注：其内图片仍会被后续 XML 精确映射阶段导出为文件（images/fig_N.*）并在 _index.md 标为 (unmatched)，
        # 只是因组合锁定无法在正文定位、不会获得正文 [FIG-N] 占位符。
        # （曾尝试「整组 ConvertToInlineShape」救回，但实测转换后类型不确定[type12/14 不一致]，
        #   盲目按图片处理会误伤其它文档的图表/控件，风险高收益低，故不采用——见交接文档问题四。）
        if ($lockedGroups.Count -gt 0) {
            $names = ($lockedGroups.Keys) -join '；'
            Append-Warning ("$($lockedGroups.Count) 个组合图已锁定、无法拆分（$names），其内图片已作为文件导出到 images/ 并在 _index.md 标为 (unmatched)，但未分配正文 [FIG-N]（这正是后面「图片计数失配」的原因）。如需在正文引用它们：在 Word 中选中该组合→右键→取消组合（或先解除锁定）后重新摄取。")
        }
        # 收集所有图片形状
        $picturesToConvert = @()
        foreach ($sh in $doc.Shapes) {
            try {
                $stype = [int]$sh.Type
                if ($stype -eq $msoPicture -or $stype -eq $msoLinkedPicture) {
                    $picturesToConvert += $sh
                }
            } catch {}
        }
        # 转换为 InlineShape
        foreach ($sh in $picturesToConvert) {
            try {
                $null = $sh.ConvertToInlineShape()
            } catch {
                Append-Warning ("Floating shape 转 inline 失败: " + $_.Exception.Message)
            }
        }
    } catch {
        Append-Warning ("Shapes 集合遍历异常: " + $_.Exception.Message)
    }

    # ------------------------------------------------------------------
    # 阶段 1：枚举所有特殊对象，分配占位符，加入替换计划
    #         （此时文档结构已 normalize 定型，捕获的活范围全程有效）
    # ------------------------------------------------------------------

    # ---- 1.1 Fields（ADDIN ZOTERO / REF / SEQ / MACROBUTTON / GOTOBUTTON）----
    try {
        foreach ($f in $doc.Fields) {
            try {
                $code   = ''
                $result = ''
                try { $code   = [string]$f.Code.Text } catch {}
                try { $result = Normalize-DisplayText -Text ([string]$f.Result.Text) } catch {}
                # ★ 根因修复：本环境/本文档下 $f.Range.Start 恒为 0（域范围塌缩，实测 243/243 全塌），
                # 会导致所有 [CITE-]/[XREF-]/[EQNUM-] 被插到文首 Front Matter。
                # 用 Code.Start/Result.End 重建是真正修好此 bug 的核心；
                # A 方案（不变式：先定型再捕获，见阶段 0）和 B 方案（书签锚点，默认关，见 -UseBookmarkAnchor）
                # 是额外的健壮层——不是修复本体，但 A 零成本应常开，B 作应急按需启用。
                # 实测重建范围 .Text 精确等于域的 displayText，零越界。
                # （历史注释曾称弃用 Code/Result 凑边界改用 $f.Range——那次"改进"正是本 bug 的来源。）
                # $f 一并留存，供阶段 2 失败时走域感知 Delete 兜底。
                $fieldRng = $null
                $cStart = $null; $rEnd = $null
                try { $cStart = [int]$f.Code.Start } catch {}
                try { $rEnd   = [int]$f.Result.End } catch {}
                if ($null -ne $cStart -and $null -ne $rEnd -and $rEnd -ge $cStart) {
                    try {
                        $base = [Math]::Max(0, $cStart - 1)
                        $fieldRng = $doc.Range($base, $rEnd + 1)
                        # 不变式：良构域范围的 .Text 末字符应是域结束符 \x15 (chr 21)。
                        # 嵌套域（如标题处 AxMath: MACROBUTTON 套 SEQ AMEqn/AMSec/AMChap）下 Result.End+1
                        # 会越界吃掉域后第一个真字符（实测：标题 "Burdens" 的 B 被 strip 成 "urdens"）。
                        # 自校正：末字符若非 \x15，回退到最后一个 \x15 处，绝不吞掉域外真内容。
                        # 简单域的 .Text 本就以 \x15 结尾 → 不进此分支 → 行为零变化。
                        $ftxt = [string]$fieldRng.Text
                        if ($ftxt.Length -gt 0 -and $ftxt[$ftxt.Length - 1] -ne [char]21) {
                            $lastEnd = $ftxt.LastIndexOf([char]21)
                            if ($lastEnd -ge 0) { $fieldRng = $doc.Range($base, $base + $lastEnd + 1) }
                        }
                    } catch { $fieldRng = $null }
                }
                if ($null -eq $fieldRng) { $fieldRng = $f.Range }   # 兜底（无 result 的域）
                $sortStart = [int]$fieldRng.Start

                $codeUpper = $code.ToUpperInvariant().Trim()
                $ph = $null
                $entry = $null

                if ($codeUpper -match '^\s*ADDIN\s+ZOTERO_ITEM') {
                    $citeIdx++
                    $ph = "[CITE-$citeIdx]"
                    $entry = [ordered]@{
                        type        = 'zotero'
                        displayText = $result
                        fieldCode   = $code.Trim()
                    }
                } elseif ($codeUpper -match '^\s*ADDIN\s+EN\.') {
                    # EndNote 兼容（极少见）
                    $citeIdx++
                    $ph = "[CITE-$citeIdx]"
                    $entry = [ordered]@{
                        type        = 'endnote'
                        displayText = $result
                        fieldCode   = $code.Trim()
                    }
                } elseif ($codeUpper -match '^\s*REF\s+ZEQNNUM') {
                    $xrefEqIdx++
                    $ph = "[XREF-EQ-$xrefEqIdx]"
                    $bookmark = $null
                    if ($code -match 'REF\s+(\S+)') { $bookmark = $matches[1] }
                    $entry = [ordered]@{
                        type           = 'ref_eqnum'
                        displayText    = $result
                        fieldCode      = $code.Trim()
                        targetBookmark = $bookmark
                    }
                } elseif ($codeUpper -match '^\s*REF\s+') {
                    # 普通 REF：用 displayText 判断子类型
                    $bookmark = $null
                    if ($code -match 'REF\s+(\S+)') { $bookmark = $matches[1] }
                    $sub = 'sec'
                    if ($result -match 'Figure\s*\d+|图\s*\d+|Fig\.?\s*\d+') { $sub = 'fig' }
                    elseif ($result -match 'Table\s*\d+|表\s*\d+|Tab\.?\s*\d+') { $sub = 'tbl' }
                    elseif ($result -match '^\s*\(?\d+(\.\d+)?\)?\s*$') {
                        # 纯数字 / 带括号数字 → 可能是公式号或章节号
                        if ($result -match '^\s*\(\d+\)\s*$') { $sub = 'eq' } else { $sub = 'sec' }
                    }
                    switch ($sub) {
                        'fig' { $xrefFigIdx++; $ph = "[XREF-FIG-$xrefFigIdx]" }
                        'tbl' { $xrefTblIdx++; $ph = "[XREF-TBL-$xrefTblIdx]" }
                        'eq'  { $xrefEqIdx++;  $ph = "[XREF-EQ-$xrefEqIdx]" }
                        default { $xrefSecIdx++; $ph = "[XREF-SEC-$xrefSecIdx]" }
                    }
                    $entry = [ordered]@{
                        type           = "ref_$sub"
                        displayText    = $result
                        fieldCode      = $code.Trim()
                        targetBookmark = $bookmark
                    }
                } elseif ($codeUpper -match '^\s*SEQ\s+AMEQN') {
                    $eqnumIdx++
                    $ph = "[EQNUM-$eqnumIdx]"
                    $entry = [ordered]@{
                        type        = 'axmath_seqnum'
                        displayText = $result
                        fieldCode   = $code.Trim()
                    }
                } elseif ($codeUpper -match '^\s*SEQ\b') {
                    # I2（§6.2）：广义题注 SEQ —— 不再硬编 FIGURE/TABLE（旧逻辑漏中文 SEQ 图/SEQ 表）。
                    # 取 SEQ 后第一 token 为 sequence 名，按名分类 fig/tbl；并探测题注 4 段格式累积进档案。
                    # （SEQ AMEQN 已在上面分支拦截，不会落到这里。）
                    $seqName = ''
                    if ($code -match '(?i)\bSEQ\s+"?([^"\\\s]+)') { $seqName = $matches[1] }
                    $cls = Classify-SeqName -Name $seqName
                    if ($cls -eq 'fig') {
                        $seqFigIdx++
                        $ph = "[SEQ-FIG-$seqFigIdx]"
                        $entry = [ordered]@{ type = 'seq_figure'; displayText = $result; fieldCode = $code.Trim() }
                    } elseif ($cls -eq 'tbl') {
                        $seqTblIdx++
                        $ph = "[SEQ-TBL-$seqTblIdx]"
                        $entry = [ordered]@{ type = 'seq_table'; displayText = $result; fieldCode = $code.Trim() }
                    } else {
                        # 既非图非表（也非 AMEQN）的 SEQ → 保持旧行为：不占位、不替换（跳过），仅记一条警告供人工核
                        Append-Warning ("SEQ 题注 sequence 名『$(if($seqName){$seqName}else{'?'})』无法分类为图/表，已跳过占位（如需收录请 Phase 1 显式指定 caption.format_ref）。")
                    }
                    # 探测并累积题注 4 段格式（仅 fig/tbl）→ 后续聚合成 objects.json 的 caption_formats
                    if ($cls -eq 'fig' -or $cls -eq 'tbl') {
                        try {
                            $cf = Probe-CaptionFormat -Doc $doc -Field $f -FieldRange $fieldRng -SeqName $seqName -Class $cls
                            if ($cls -eq 'fig') { $null = $capFmtFig.Add($cf) } else { $null = $capFmtTbl.Add($cf) }
                        } catch { Append-Warning ("题注格式探测失败 [$seqName]: " + $_.Exception.Message) }
                    }
                } elseif ($codeUpper -match '^\s*(MACROBUTTON|GOTOBUTTON)') {
                    # AxMath 公式触发域 —— 由 OLE 阶段处理；这里只标记此 field 也归入 EQ
                    # 不分配新占位符，删除 field 的显示文字（用空字符串替换，因为相邻 OLE 已分配 [EQ-N]）
                    $entry = [ordered]@{
                        type        = 'axmath_trigger'
                        displayText = $result
                        fieldCode   = $code.Trim()
                    }
                    # 给一个 sentinel 占位符 key（不计入主索引但用于替换为空）
                    $replacePlan.Add(@{
                        range = $fieldRng; sortStart = $sortStart; obj = $f
                        bookmark = (Make-Anchor -Doc $doc -Range $fieldRng)
                        replacement = ''
                        type = 'axmath_trigger_strip'
                    }) | Out-Null
                    continue
                } else {
                    # 其他 field 类型（DATE / TOC / PAGE 等）跳过，不替换不入册
                    continue
                }

                if ($null -ne $ph -and $null -ne $entry) {
                    $placeholders[$ph] = $entry
                    $replacePlan.Add(@{
                        range = $fieldRng; sortStart = $sortStart; obj = $f
                        bookmark = (Make-Anchor -Doc $doc -Range $fieldRng)
                        replacement = $ph
                        type = $entry.type
                    }) | Out-Null
                }
            } catch {
                Append-Warning ("Field 处理失败: " + $_.Exception.Message)
            }
        }
    } catch {
        Append-Warning ("Fields 集合遍历异常: " + $_.Exception.Message)
    }

    # ---- 1.2 OMaths（Word 原生公式）----
    try {
        foreach ($om in $doc.OMaths) {
            try {
                $eqOmmlIdx++
                $ph = "[EQ-OMML-$eqOmmlIdx]"
                $rng = $om.Range
                $start = [int]$rng.Start
                $end   = [int]$rng.End
                $displayText = ''
                try { $displayText = Normalize-DisplayText -Text ([string]$rng.Text) } catch {}
                $placeholders[$ph] = [ordered]@{
                    type        = 'omml'
                    displayText = $displayText
                }
                $replacePlan.Add(@{
                    range = $rng; sortStart = $start
                    bookmark = (Make-Anchor -Doc $doc -Range $rng)
                    replacement = $ph
                    type = 'omml'
                }) | Out-Null
            } catch {
                Append-Warning ("OMML 处理失败: " + $_.Exception.Message)
            }
        }
    } catch {
        Append-Warning ("OMaths 集合遍历异常: " + $_.Exception.Message)
    }

    # ---- 1.25 已上移至「阶段 0：normalize」（doc 打开后、所有对象捕获之前）----
    # 原因：浮动图转内联会重排文字流，若在 1.1/1.2 捕获域/公式的活范围之后再做，
    # 会使那些缓存范围塌缩到文首（历史 bug：所有 [CITE-]/[XREF-]/[EQNUM-] 堆到 Front Matter）。
    # 不变式：任何会改动文档结构的操作，必须排在任何对象捕获之前。详见阶段 0。

    # ---- 1.3 InlineShapes（嵌入 OLE + 内联图片）----
    try {
        foreach ($ish in $doc.InlineShapes) {
            try {
                $type = [int]$ish.Type
                $rng = $ish.Range
                $start = [int]$rng.Start
                $end   = [int]$rng.End

                if ($type -eq $wdInlineShapeEmbeddedOLEObject) {
                    $classType = ''
                    try { $classType = [string]$ish.OLEFormat.ClassType } catch {}
                    if ($classType -match 'Excel') {
                        # 嵌入 Excel —— 当作图块处理（不替换为 EQ）
                        $figIdx++
                        $ph = "[FIG-$($figIdx): Embedded Excel Worksheet]"
                        $placeholders[$ph] = [ordered]@{
                            type        = 'embedded_excel'
                            classType   = $classType
                            displayText = '[Embedded Excel]'
                        }
                        $replacePlan.Add(@{
                            range = $rng; sortStart = $start
                            replacement = $ph
                            type = 'embedded_excel'
                        }) | Out-Null
                    } else {
                        # 默认归为 AxMath / 其他公式 OLE
                        $eqIdx++
                        $ph = "[EQ-$eqIdx]"
                        $placeholders[$ph] = [ordered]@{
                            type        = 'axmath_ole'
                            classType   = $classType
                            displayText = '[AxMath formula]'
                            blockLevel  = $true  # 实际块/行内由占位符所在段落判断，下游写回 skill 可再细化
                        }
                        $replacePlan.Add(@{
                            range = $rng; sortStart = $start
                            replacement = $ph
                            type = 'axmath_ole'
                        }) | Out-Null
                    }
                } elseif ($type -eq $wdInlineShapePicture -or $type -eq $wdInlineShapeLinkedPicture) {
                    $figIdx++
                    # 尝试找最近的 caption（向后扫描 5 段，含 "Figure N" / "图 N"）
                    $caption = $null
                    try {
                        $hostPara = $rng.Paragraphs.First
                        $cursor = $hostPara
                        for ($k = 0; $k -lt 5; $k++) {
                            try {
                                $tx = [string]$cursor.Range.Text
                                if ($tx -match '(Figure|Fig\.?|图)\s*\d+') {
                                    $caption = ($tx -replace "[`r`n`a]", ' ').Trim()
                                    if ($caption.Length -gt 200) { $caption = $caption.Substring(0, 200) }
                                    break
                                }
                                $cursor = $cursor.Next()
                                if ($null -eq $cursor) { break }
                            } catch { break }
                        }
                    } catch {}
                    if ([string]::IsNullOrEmpty($caption)) { $caption = "Figure $figIdx" }
                    $ph = "[FIG-${figIdx}: $caption]"
                    $imgFilename = "image_$figIdx.png"
                    $imgPath = Join-Path $imagesDir $imgFilename
                    try {
                        # 把图片复制出来：用 InlineShape.Range.CopyAsPicture 或直接读 ImageData
                        # 最简单：保存整个 docx 的 word/media/ 目录到 images/（在脚本末尾做一次）
                    } catch {}
                    $placeholders[$ph] = [ordered]@{
                        type        = 'image'
                        caption     = $caption
                        src         = "manifest/images/$imgFilename"
                        displayText = $caption
                    }
                    $imagesIndex.Add(@{
                        placeholder = $ph
                        caption     = $caption
                        src         = "manifest/images/$imgFilename"
                    }) | Out-Null
                    $replacePlan.Add(@{
                        range = $rng; sortStart = $start
                        replacement = $ph
                        type = 'image'
                    }) | Out-Null
                } elseif ($type -eq 12) {
                    # InlineShape Group：递归提取内部图片
                    try {
                        Extract-Images-From-InlineGroup -groupShape $ish
                    } catch {
                        Append-Warning ("InlineShape Group (type=12) 图片提取失败: " + $_.Exception.Message)
                    }
                }
            } catch {
                Append-Warning ("InlineShape 处理失败: " + $_.Exception.Message)
            }
        }
    } catch {
        Append-Warning ("InlineShapes 集合遍历异常: " + $_.Exception.Message)
    }

    # ---- 1.4 Footnotes ----
    try {
        foreach ($fn in $doc.Footnotes) {
            try {
                $fnIdx++
                $ph = "[FN-$fnIdx]"
                $refRng = $fn.Reference
                $start = [int]$refRng.Start
                $end   = [int]$refRng.End
                $body = ''
                try { $body = Normalize-DisplayText -Text ([string]$fn.Range.Text) } catch {}
                $placeholders[$ph] = [ordered]@{
                    type        = 'footnote'
                    displayText = '[footnote ref]'
                    body        = $body
                }
                $replacePlan.Add(@{
                    range = $refRng; sortStart = $start
                    replacement = $ph
                    type = 'footnote'
                }) | Out-Null
                $footnoteBodies.Add(@{ ph = $ph; body = $body }) | Out-Null
            } catch {
                Append-Warning ("Footnote 处理失败: " + $_.Exception.Message)
            }
        }
    } catch {
        Append-Warning ("Footnotes 集合遍历异常: " + $_.Exception.Message)
    }

    # ---- 1.6 Tables（I1：原生表 → [TBL-N]，整表原子保护 + md 渲染）----
    # 仅收【不含域/图】的表：含 Zotero/REF/SEQ 域或内联图的表跳过 [TBL-N]，保留其内部占位符各自捕获（契约不丢任何 [CITE-N] 等）。
    # v1 只做"数+定位+保护+md 渲染"，不解析成可再编辑结构（§6.1）。
    try {
        foreach ($tb in $doc.Tables) {
            try {
                $rng = $tb.Range
                $nf = 0; try { $nf = [int]$tb.Range.Fields.Count } catch {}
                $ns = 0; try { $ns = [int]$tb.Range.InlineShapes.Count } catch {}
                if ($nf -gt 0 -or $ns -gt 0) {
                    Append-Warning ("表(含 $nf 域/$ns 内联图)未作 [TBL-N] 原子保护——保留其内部占位符各自捕获；如需整表保护请先把表内对象移出后重摄取。")
                    continue
                }
                $tblIdx++
                $ph = "[TBL-$tblIdx]"
                $rows = [int]$tb.Rows.Count; $cols = [int]$tb.Columns.Count
                $md = ''
                try { $md = Render-TableMd -Table $tb } catch { Append-Warning ("表 md 渲染失败 [$ph]: " + $_.Exception.Message) }
                $placeholders[$ph] = [ordered]@{
                    type        = 'table'
                    rows        = $rows
                    cols        = $cols
                    md          = $md
                    displayText = $ph
                    rangeStart  = [int]$rng.Start
                    rangeEnd    = [int]$rng.End
                }
                $replacePlan.Add(@{
                    range = $rng; sortStart = [int]$rng.Start
                    replacement = $ph
                    type = 'table'
                }) | Out-Null
                $tablesIndex.Add(@{ placeholder = $ph; rows = $rows; cols = $cols }) | Out-Null
            } catch {
                Append-Warning ("Table 处理失败: " + $_.Exception.Message)
            }
        }
    } catch {
        Append-Warning ("Tables 集合遍历异常: " + $_.Exception.Message)
    }

    # ---- 1.5 Bookmarks（不替换，只记录）----
    $bookmarksList = New-Object System.Collections.ArrayList
    try {
        foreach ($bm in $doc.Bookmarks) {
            try {
                # 跳过 B 方案的临时锚点书签（__INGEST_*），绝不让其污染 objects.json 真实书签清单
                if ([string]$bm.Name -like '__INGEST_*') { continue }
                $rng = $bm.Range
                $bookmarksList.Add(@{
                    name = [string]$bm.Name
                    start = [int]$rng.Start
                    end   = [int]$rng.End
                }) | Out-Null
            } catch {
                Append-Warning ("Bookmark 处理失败: " + $_.Exception.Message)
            }
        }
    } catch {
        Append-Warning ("Bookmarks 集合遍历异常: " + $_.Exception.Message)
    }

    # ------------------------------------------------------------------
    # 阶段 2：按 Range.Start 倒序执行替换
    # ------------------------------------------------------------------
    # 替换用的活范围：
    #   · A 方案（默认）：用阶段 1 捕获的 $p.range。因阶段 0 已 normalize、捕获到此处无结构改动，
    #     倒序替换又自洽，范围全程有效。
    #   · B 方案（-UseBookmarkAnchor）：优先用 $p.bookmark 重解析活范围，对任意结构改动免疫。
    # 回归断言：若某非空替换项的活范围塌缩到文首（start==0）——这正是历史 bug 的特征——
    #   则【拦截不替换】（避免把占位符污染到 Front Matter），计数并警告，提示调用方加 -UseBookmarkAnchor 重摄取。
    $sortedPlan = @($replacePlan | Sort-Object -Property @{Expression={[int]$_.sortStart}; Descending=$true})
    foreach ($p in $sortedPlan) {
        # 解析本项要替换的活范围（B 模式优先走书签）
        $useRange = $p.range
        if ($p.bookmark) {
            try { if ($doc.Bookmarks.Exists($p.bookmark)) { $useRange = $doc.Bookmarks.Item($p.bookmark).Range } } catch {}
        }
        $atStart = $null
        try { $atStart = [int]$useRange.Start } catch {}

        # 回归断言：仅对【域类】生效——域类才会因 $f.Range 假性塌缩到 0（本次根因）；
        # 图片(InlineShape.Range)/OMML(OMath.Range) 用的是可靠 Range，start==0 是「合法地在篇首」，不得误判。
        $isFieldType = ([string]$p.type) -match '^(zotero|endnote|ref_|axmath_seqnum|seq_|axmath_trigger)'
        if ($isFieldType -and $null -ne $atStart -and $atStart -eq 0 -and -not [string]::IsNullOrEmpty([string]$p.replacement)) {
            $script:collapseCount++
            Append-Warning ("位置塌陷拦截 [$($p.type)]: 域范围塌缩到文首(start=0)，已跳过以免污染 Front Matter。多为文档内损坏的域(如 Error! Reference source not found)；如普遍出现请加 -UseBookmarkAnchor 重摄取。")
            continue
        }

        try {
            $useRange.Text = [string]$p.replacement
        } catch {
            # 兜底：偶发"无法删除范围"时，对域走域感知 Delete()（$p.obj），其余走 Range.Delete()，再原位插占位符
            try {
                if ($null -ne $p.obj) { $null = $p.obj.Delete() } else { $null = $useRange.Delete() }
                if ($null -ne $atStart -and $atStart -gt 0 -and -not [string]::IsNullOrEmpty([string]$p.replacement)) {
                    $ins = $doc.Range($atStart, $atStart)
                    $ins.Text = [string]$p.replacement
                }
            } catch {
                Append-Warning ("替换失败 [$($p.type) @ $atStart]: " + $_.Exception.Message)
            }
        }
    }

    # 清理 B 方案的临时锚点书签（多数已被 .Text 替换消费，Exists 兜住剩余的），防污染产物
    if ($script:UseBookmarkAnchor) {
        for ($ai = 1; $ai -le $script:anchorIdx; $ai++) {
            $bn = "__INGEST_$ai"
            try { if ($doc.Bookmarks.Exists($bn)) { $null = $doc.Bookmarks.Item($bn).Delete() } } catch {}
        }
    }

    # ------------------------------------------------------------------
    # 阶段 3：按 OutlineLevel=1 切章节
    # ------------------------------------------------------------------
    $sectionsList = New-Object System.Collections.ArrayList   # 每项 @{ index; title; level; slug; pageStart; pageEnd; bodyLines = @() }
    $currentSection = $null
    $sectionIdx = 0

    # 重新分页确保页码可读
    try { $doc.Repaginate() } catch {}
    $wdActiveEndPageNumber = 3

    # 标题自动提取：① Word 内置文档属性 Title；② 退而求其次，正文第一段「标题(Title)」样式的文字。
    # 跨语言：用文档自己内置 Title 样式(wdStyleTitle=-63)的本地化名做匹配，不硬编 "Title"/"标题"。
    $detectedTitle = ''
    try { $detectedTitle = ([string]$doc.BuiltInDocumentProperties.Item('Title').Value).Trim() } catch {}
    $titleStyleName = $null
    try { $titleStyleName = [string]$doc.Styles.Item(-63).NameLocal } catch {}

    foreach ($para in $doc.Paragraphs) {
        try {
            $lvl = $wdOutlineLevelBodyText
            try { $lvl = [int]$para.OutlineLevel } catch {}
            $rawText = ''
            try { $rawText = [string]$para.Range.Text } catch {}
            $cleanText = ($rawText -replace "[`r`n`a`f`v`0]+\s*$", '').Trim()
            # 标题样式探测：文档属性没给标题时，取正文第一段 Title 样式的文字
            if ([string]::IsNullOrWhiteSpace($detectedTitle) -and $titleStyleName -and -not [string]::IsNullOrWhiteSpace($cleanText)) {
                $styleName = ''
                try { $styleName = [string]$para.Range.Style.NameLocal } catch {}
                if ($styleName -eq $titleStyleName) { $detectedTitle = $cleanText }
            }
            $pageNum = $null
            try { $pageNum = [int]$para.Range.Information($wdActiveEndPageNumber) } catch {}

            $isTopHeading = ($lvl -ge 1 -and $lvl -le 1)
            $isSubHeading = ($lvl -ge 2 -and $lvl -le 6)

            if ($isTopHeading) {
                # 关闭旧章节
                if ($null -ne $currentSection) {
                    $currentSection.pageEnd = $pageNum
                    $null = $sectionsList.Add($currentSection)
                }
                $sectionIdx++
                $currentSection = @{
                    index     = $sectionIdx
                    title     = $cleanText
                    level     = 1
                    slug      = Slugify -Text $cleanText -Index $sectionIdx
                    pageStart = $pageNum
                    pageEnd   = $pageNum
                    bodyLines = New-Object System.Collections.ArrayList
                }
                # 标题不写入 bodyLines（在 md 模板里再加 ## 头）
            } else {
                if ($null -eq $currentSection) {
                    # 还没出现一级标题的内容（如摘要 / 标题页）→ 建一个"序章"
                    $sectionIdx++
                    $currentSection = @{
                        index     = $sectionIdx
                        title     = '(Front Matter)'
                        level     = 0
                        slug      = '00_front_matter'
                        pageStart = $pageNum
                        pageEnd   = $pageNum
                        bodyLines = New-Object System.Collections.ArrayList
                    }
                }
                $line = $cleanText
                if ($isSubHeading -and -not [string]::IsNullOrWhiteSpace($line)) {
                    $hashes = '#' * ($lvl + 1)  # OutlineLevel 2 → ###；3 → ####
                    $line = "$hashes $cleanText"
                }
                if (-not [string]::IsNullOrEmpty($line)) {
                    $null = $currentSection.bodyLines.Add($line)
                }
            }
        } catch {
            Append-Warning ("段落处理失败: " + $_.Exception.Message)
        }
    }
    if ($null -ne $currentSection) {
        $null = $sectionsList.Add($currentSection)
    }

    # ------------------------------------------------------------------
    # 阶段 4：写产物
    # ------------------------------------------------------------------

    # 4.1 章节切片
    $manifestVersion = '1.0'
    foreach ($s in $sectionsList) {
        $fmPath = Join-Path $sectionsDir ($s.slug + '.md')
        $bodyText = ($s.bodyLines -join "`n`n")
        $pageRangeStr = if ($s.pageStart -and $s.pageEnd) {
            "[$($s.pageStart), $($s.pageEnd)]"
        } else { '[]' }
        $titleEscaped = ($s.title -replace '"', '\"')
        $front = @"
---
section_index: $($s.index)
title: "$titleEscaped"
level: $($s.level)
page_range: $pageRangeStr
parent_manuscript: "manifest/manuscript.md"
---

## $($s.title)

$bodyText
"@
        Write-Utf8NoBom -Path $fmPath -Text $front
    }

    # 4.2 整体 manuscript.md
    $sbManuscript = New-Object System.Text.StringBuilder
    $null = $sbManuscript.AppendLine("# Manuscript (Placeholder-protected)")
    $null = $sbManuscript.AppendLine("")
    $null = $sbManuscript.AppendLine("Source: ``$DocxPath``  ")
    $null = $sbManuscript.AppendLine("Ingest time: $((Get-Date -Format 'yyyy-MM-ddTHH:mm:sszzz'))  ")
    $null = $sbManuscript.AppendLine("")
    $null = $sbManuscript.AppendLine("> 占位符（``[CITE-/EQ-/EQNUM-/XREF-/SEQ-/FN-/FIG-]``）是 ingest 阶段对原稿域对象的保护标记，下游任何改写工具禁止修改其内部。详见 ``objects.json``。")
    $null = $sbManuscript.AppendLine("")
    foreach ($s in $sectionsList) {
        $null = $sbManuscript.AppendLine("## $($s.title)")
        $null = $sbManuscript.AppendLine("")
        foreach ($ln in $s.bodyLines) {
            $null = $sbManuscript.AppendLine($ln)
            $null = $sbManuscript.AppendLine("")
        }
    }
    Write-Utf8NoBom -Path (Join-Path $OutDir 'manuscript.md') -Text ($sbManuscript.ToString())

    # 4.3 sections_index.md
    $sbIdx = New-Object System.Text.StringBuilder
    $null = $sbIdx.AppendLine("# Sections Index")
    $null = $sbIdx.AppendLine("")
    $null = $sbIdx.AppendLine("> 真实章节标题清单（OutlineLevel=1）。供下游 skill 匹配真实章节字段使用。")
    $null = $sbIdx.AppendLine("")
    $null = $sbIdx.AppendLine("| 序号 | 标题 | 页码范围 | 切片文件 |")
    $null = $sbIdx.AppendLine("|------|------|---------|---------|")
    foreach ($s in $sectionsList) {
        $pageStr = if ($s.pageStart -and $s.pageEnd) { "$($s.pageStart)-$($s.pageEnd)" } else { '-' }
        $null = $sbIdx.AppendLine("| $($s.index) | $($s.title) | $pageStr | manuscript_sections/$($s.slug).md |")
    }
    Write-Utf8NoBom -Path (Join-Path $OutDir 'sections_index.md') -Text ($sbIdx.ToString())

    # 4.4 footnotes.md
    if ($footnoteBodies.Count -gt 0) {
        $sbFn = New-Object System.Text.StringBuilder
        $null = $sbFn.AppendLine("# Footnotes")
        $null = $sbFn.AppendLine("")
        foreach ($fb in $footnoteBodies) {
            $null = $sbFn.AppendLine("## $($fb.ph)")
            $null = $sbFn.AppendLine("")
            $null = $sbFn.AppendLine($fb.body)
            $null = $sbFn.AppendLine("")
        }
        Write-Utf8NoBom -Path (Join-Path $OutDir 'footnotes.md') -Text ($sbFn.ToString())
    } else {
        Write-Utf8NoBom -Path (Join-Path $OutDir 'footnotes.md') -Text "# Footnotes`n`n(原稿无脚注)`n"
    }

    # 4.5 图片精确映射（按 plan A.4.1 算法）
    # 解 docx zip → 读 word/_rels/document.xml.rels → 按 XPath 顺序遍历 <w:drawing>/<w:object>/<w:pict>
    # 与 Word COM 阶段 1.3 分配的 [FIG-N]/[EQ-N] 严格 1:1 对齐
    try {
        $extractTemp = Join-Path $tempDir 'extract'
        if (-not (Test-Path -LiteralPath $extractTemp)) {
            New-Item -ItemType Directory -Path $extractTemp -Force | Out-Null
            $zipCopy = Join-Path $tempDir 'docx_for_extract.zip'
            Copy-Item -LiteralPath $DocxPath -Destination $zipCopy -Force
            Expand-Archive -LiteralPath $zipCopy -DestinationPath $extractTemp -Force
        }

        $relsPath   = Join-Path $extractTemp 'word\_rels\document.xml.rels'
        $docXmlPath = Join-Path $extractTemp 'word\document.xml'
        $imageMappings = New-Object System.Collections.ArrayList

        if ((Test-Path -LiteralPath $relsPath) -and (Test-Path -LiteralPath $docXmlPath)) {

            # 步骤 1：rels 字典
            [xml]$relsXml = [System.IO.File]::ReadAllText($relsPath, [System.Text.Encoding]::UTF8)
            $rIdToTarget = @{}
            foreach ($rel in $relsXml.Relationships.Relationship) {
                $rIdToTarget[$rel.Id] = $rel.Target
            }

            # 步骤 2：document.xml + 命名空间
            [xml]$docXml = [System.IO.File]::ReadAllText($docXmlPath, [System.Text.Encoding]::UTF8)
            $nsmgr = New-Object System.Xml.XmlNamespaceManager($docXml.NameTable)
            $nsmgr.AddNamespace('w', 'http://schemas.openxmlformats.org/wordprocessingml/2006/main')
            $nsmgr.AddNamespace('a', 'http://schemas.openxmlformats.org/drawingml/2006/main')
            $nsmgr.AddNamespace('r', 'http://schemas.openxmlformats.org/officeDocument/2006/relationships')
            $nsmgr.AddNamespace('v', 'urn:schemas-microsoft-com:vml')
            $nsmgr.AddNamespace('o', 'urn:schemas-microsoft-com:office:office')
            $relsNs = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'

            # 预收集占位符 key（按 Word COM 插入顺序，等于文档阅读顺序）
            $figKeys = @($placeholders.Keys | Where-Object { $_ -match '^\[FIG-\d+' })
            $eqKeys  = @($placeholders.Keys | Where-Object { $_ -match '^\[EQ-\d+\]$' })

            # 步骤 3：按文档顺序遍历
            $nodes = $docXml.SelectNodes('//w:drawing | //w:object | //w:pict', $nsmgr)
            $figCounter = 0
            $eqCounter  = 0
            # 公式预览图共用追踪：key=预览 wmf 文件名, value=该图被哪些 EQ 共用 + 各自嵌入对象 .bin 哈希
            # 循环结束后据此检测"字节不同的公式共用同一预览图"（真·张冠李戴），仅此情形发警告
            $eqPreviewShare = @{}

            foreach ($node in $nodes) {
                try {
                    # 跳过嵌套在 object 内的 drawing（其 wmf 由 object 自身处理）
                    if ($node.LocalName -eq 'drawing') {
                        $ancestor = $node.ParentNode
                        $skipDrawing = $false
                        while ($null -ne $ancestor) {
                            if ($ancestor.LocalName -eq 'object') { $skipDrawing = $true; break }
                            $ancestor = $ancestor.ParentNode
                        }
                        if ($skipDrawing) { continue }
                    }

                    if ($node.LocalName -eq 'object') {
                        # 判断是 Excel 还是 AxMath/其他 OLE
                        $ole = $node.SelectSingleNode('.//o:OLEObject', $nsmgr)
                        $progID = ''
                        if ($null -ne $ole) { $progID = [string]$ole.GetAttribute('ProgID') }
                        $isExcel = ($progID -match 'Excel')

                        # 找 wmf 预览
                        $imageData = $node.SelectSingleNode('.//v:imagedata', $nsmgr)
                        $rId = $null
                        if ($null -ne $imageData) { $rId = $imageData.GetAttribute('id', $relsNs) }
                        if ([string]::IsNullOrEmpty($rId)) { continue }
                        if (-not $rIdToTarget.ContainsKey($rId)) {
                            Append-Warning "OLE 节点：rId $rId 在 rels 中未登记"
                            continue
                        }
                        $relTarget = $rIdToTarget[$rId]
                        $origMedia = Split-Path -Leaf $relTarget
                        $ext = [System.IO.Path]::GetExtension($origMedia)
                        $srcFile = Join-Path $extractTemp ('word\' + ($relTarget -replace '/', '\'))

                        if ($isExcel) {
                            # Excel OLE 被 Word COM 归为 [FIG-N]
                            $figCounter++
                            $destName = "fig_${figCounter}${ext}"
                            $destFile = Join-Path $imagesDir $destName
                            if (Test-Path -LiteralPath $srcFile) {
                                Copy-Item -LiteralPath $srcFile -Destination $destFile -Force
                                if ($figCounter -le $figKeys.Count) {
                                    $phKey = $figKeys[$figCounter - 1]
                                    $placeholders[$phKey]['src'] = "manifest/images/$destName"
                                    $placeholders[$phKey]['originalMedia'] = $origMedia
                                    $null = $imageMappings.Add(@{ placeholder = $phKey; file = $destName; originalMedia = $origMedia; kind = 'embedded_excel' })
                                } else {
                                    $null = $imageMappings.Add(@{ placeholder = "[FIG-$figCounter](unmatched)"; file = $destName; originalMedia = $origMedia; kind = 'embedded_excel' })
                                }
                            } else {
                                Append-Warning "Excel FIG-${figCounter}: 源文件不存在 $srcFile"
                            }
                        } else {
                            # AxMath / 其他公式 OLE → [EQ-N] 预览
                            $eqCounter++
                            # 记录"该预览图被哪些 EQ 共用"+ 嵌入对象 .bin 哈希（用于检测不同公式共用同一预览图）
                            try {
                                $oleEmbedRid = if ($null -ne $ole) { [string]$ole.GetAttribute('id', $relsNs) } else { '' }
                                $binHash = $null
                                if (-not [string]::IsNullOrEmpty($oleEmbedRid) -and $rIdToTarget.ContainsKey($oleEmbedRid)) {
                                    $binFile = Join-Path $extractTemp ('word\' + ($rIdToTarget[$oleEmbedRid] -replace '/', '\'))
                                    if (Test-Path -LiteralPath $binFile) { $binHash = (Get-FileHash -LiteralPath $binFile -Algorithm MD5).Hash }
                                }
                                if (-not $eqPreviewShare.ContainsKey($origMedia)) { $eqPreviewShare[$origMedia] = New-Object System.Collections.ArrayList }
                                $null = $eqPreviewShare[$origMedia].Add(@{ eq = $eqCounter; binHash = $binHash })
                            } catch {}
                            $destName = "eq_${eqCounter}_preview${ext}"
                            $destFile = Join-Path $imagesDir $destName
                            if (Test-Path -LiteralPath $srcFile) {
                                Copy-Item -LiteralPath $srcFile -Destination $destFile -Force
                                if ($eqCounter -le $eqKeys.Count) {
                                    $phKey = $eqKeys[$eqCounter - 1]
                                    $placeholders[$phKey]['src'] = "manifest/images/$destName"
                                    $placeholders[$phKey]['originalMedia'] = $origMedia
                                    $null = $imageMappings.Add(@{ placeholder = $phKey; file = $destName; originalMedia = $origMedia; kind = 'axmath_ole_preview' })
                                } else {
                                    $null = $imageMappings.Add(@{ placeholder = "[EQ-$eqCounter](unmatched)"; file = $destName; originalMedia = $origMedia; kind = 'axmath_ole_preview' })
                                }
                            } else {
                                Append-Warning "AxMath EQ-${eqCounter}: 源文件不存在 $srcFile"
                            }
                        }
                    }
                    else {
                        # 独立 drawing 或 pict → 真实图片
                        $rId = $null
                        if ($node.LocalName -eq 'drawing') {
                            $blip = $node.SelectSingleNode('.//a:blip', $nsmgr)
                            if ($null -ne $blip) {
                                $rId = $blip.GetAttribute('embed', $relsNs)
                                if ([string]::IsNullOrEmpty($rId)) {
                                    $rId = $blip.GetAttribute('link', $relsNs)
                                }
                            }
                        } else {
                            $imageData = $node.SelectSingleNode('.//v:imagedata', $nsmgr)
                            if ($null -ne $imageData) { $rId = $imageData.GetAttribute('id', $relsNs) }
                        }
                        if ([string]::IsNullOrEmpty($rId)) { continue }
                        if (-not $rIdToTarget.ContainsKey($rId)) {
                            Append-Warning "Picture 节点：rId $rId 在 rels 中未登记"
                            continue
                        }
                        $relTarget = $rIdToTarget[$rId]
                        $origMedia = Split-Path -Leaf $relTarget
                        $ext = [System.IO.Path]::GetExtension($origMedia)
                        $srcFile = Join-Path $extractTemp ('word\' + ($relTarget -replace '/', '\'))

                        $figCounter++
                        $destName = "fig_${figCounter}${ext}"
                        $destFile = Join-Path $imagesDir $destName
                        if (Test-Path -LiteralPath $srcFile) {
                            Copy-Item -LiteralPath $srcFile -Destination $destFile -Force
                            if ($figCounter -le $figKeys.Count) {
                                $phKey = $figKeys[$figCounter - 1]
                                $placeholders[$phKey]['src'] = "manifest/images/$destName"
                                $placeholders[$phKey]['originalMedia'] = $origMedia
                                $null = $imageMappings.Add(@{ placeholder = $phKey; file = $destName; originalMedia = $origMedia; kind = 'picture' })
                            } else {
                                $null = $imageMappings.Add(@{ placeholder = "[FIG-$figCounter](unmatched)"; file = $destName; originalMedia = $origMedia; kind = 'picture' })
                            }
                        } else {
                            Append-Warning "Picture FIG-${figCounter}: 源文件不存在 $srcFile"
                        }
                    }
                } catch {
                    Append-Warning ("图片映射节点处理失败: " + $_.Exception.Message)
                }
            }

            # 步骤 4：对齐校验
            if ($figCounter -ne $figIdx) {
                Append-Warning "图片计数失配：XML 遍历得到 $figCounter 张 FIG，Word COM 阶段 1.3 分配了 $figIdx 个 [FIG-N]。查 manifest/images/_index.md 人工核对。"
            }
            if ($eqCounter -ne $eqIdx) {
                Append-Warning "OLE 计数失配：XML 遍历得到 $eqCounter 个 OLE wmf，Word COM 阶段 1.3 分配了 $eqIdx 个 [EQ-N]。"
            }

            # 步骤 4.5：公式预览图共用检测
            # 仅当【同一张预览 wmf 被 .bin 字节不同的多个公式共用】才报警——这才是"不同公式拿到错预览"。
            # .bin 相同的共用是同一公式在文中重复出现，Word 合法去重，不报警。
            try {
                foreach ($kv in $eqPreviewShare.GetEnumerator()) {
                    $entries = @($kv.Value)
                    if ($entries.Count -lt 2) { continue }
                    $distinctHashes = @($entries | ForEach-Object { $_.binHash } | Where-Object { $_ } | Sort-Object -Unique)
                    if ($distinctHashes.Count -gt 1) {
                        $eqList = ($entries | ForEach-Object { "EQ-$($_.eq)" }) -join ', '
                        Append-Warning "公式预览图共用：$eqList 是【不同的】嵌入公式对象（.bin 字节不同）却共用同一张预览图 $($kv.Key)——其中部分 EQ 的预览可能不准，请在原稿中核对这些公式。（.bin 相同的共用属同一公式重复，正常去重，不在此列。）"
                    }
                }
            } catch { Append-Warning ("公式预览共用检测异常: " + $_.Exception.Message) }

            # 步骤 5：写 _index.md
            $sbImg = New-Object System.Text.StringBuilder
            $null = $sbImg.AppendLine('# Images Index')
            $null = $sbImg.AppendLine('')
            $null = $sbImg.AppendLine('占位符 ↔ 导出文件 ↔ 原 docx media 文件名 的对应关系（由 ingest_manuscript.ps1 自动生成）。')
            $null = $sbImg.AppendLine('')
            $null = $sbImg.AppendLine("- 真实图 [FIG-N]：$figCounter 张")
            $null = $sbImg.AppendLine("- OLE 预览 [EQ-N]：$eqCounter 个")
            $null = $sbImg.AppendLine('')
            $null = $sbImg.AppendLine('| 占位符 | 导出文件 | 原 docx media | 类型 |')
            $null = $sbImg.AppendLine('|---|---|---|---|')
            foreach ($m in $imageMappings) {
                $null = $sbImg.AppendLine("| $($m.placeholder) | $($m.file) | $($m.originalMedia) | $($m.kind) |")
            }
            Write-Utf8NoBom -Path (Join-Path $imagesDir '_index.md') -Text ($sbImg.ToString())

        } else {
            Append-Warning "document.xml 或 document.xml.rels 缺失，跳过精确映射"
        }
    } catch {
        Append-Warning ("图片精确映射失败: " + $_.Exception.Message)
    }

    # 4.6 objects.json
    $sectionsForJson = @()
    foreach ($s in $sectionsList) {
        $sectionsForJson += [ordered]@{
            index = $s.index
            title = $s.title
            level = $s.level
            slug  = $s.slug
            pageRange = if ($s.pageStart -and $s.pageEnd) { @($s.pageStart, $s.pageEnd) } else { @() }
            sliceFile = "manifest/manuscript_sections/$($s.slug).md"
        }
    }
    $bookmarksJson = @($bookmarksList | ForEach-Object { [ordered]@{ name = $_.name; start = $_.start; end = $_.end } })

    # I2（§6.2 step 4）：聚合题注格式主档案。多套/不一致时报告（取代表 + 警告），异常处 Phase 1 用 caption.format_ref 显式覆盖。
    $captionFormats = [ordered]@{}
    $cfFig = Aggregate-CaptionFormat $capFmtFig
    $cfTbl = Aggregate-CaptionFormat $capFmtTbl
    if ($cfFig) { $captionFormats['figure'] = $cfFig }
    if ($cfTbl) { $captionFormats['table']  = $cfTbl }
    $figLabels = @(@($capFmtFig) | ForEach-Object { [string]$_.label } | Sort-Object -Unique)
    $tblLabels = @(@($capFmtTbl) | ForEach-Object { [string]$_.label } | Sort-Object -Unique)
    if ($figLabels.Count -gt 1) { Append-Warning ("图题注存在多套格式(label: $($figLabels -join '/'))，caption_formats.figure 取主档案，异常处请 Phase 1 显式 format_ref。") }
    if ($tblLabels.Count -gt 1) { Append-Warning ("表题注存在多套格式(label: $($tblLabels -join '/'))，caption_formats.table 取主档案，异常处请 Phase 1 显式 format_ref。") }

    $objectsJson = [ordered]@{
        manifest_version = $manifestVersion
        sourceDocx       = $DocxPath
        detectedTitle    = $detectedTitle
        sourceSha256     = $sourceSha
        ingestTime       = (Get-Date -Format 'yyyy-MM-ddTHH:mm:sszzz')
        stats            = [ordered]@{
            zotero      = $citeIdx
            axmath_ole  = $eqIdx
            omml        = $eqOmmlIdx
            axmath_seqnum = $eqnumIdx
            ref_eq      = $xrefEqIdx
            ref_fig     = $xrefFigIdx
            ref_tbl     = $xrefTblIdx
            ref_sec     = $xrefSecIdx
            seq_figure  = $seqFigIdx
            seq_table   = $seqTblIdx
            footnotes   = $fnIdx
            images      = $figIdx
            tables      = $tblIdx
            bookmarks   = $bookmarksList.Count
            collapse_intercepted = $script:collapseCount
        }
        anchorMode       = if ($script:UseBookmarkAnchor) { 'bookmark' } else { 'inplace' }
        placeholders     = $placeholders
        caption_formats  = $captionFormats
        sections         = $sectionsForJson
        bookmarks        = $bookmarksJson
    }
    Write-JsonUtf8 -Obj $objectsJson -Path (Join-Path $OutDir 'objects.json')

    # 4.7 ingest_warnings.md
    if (-not [string]::IsNullOrWhiteSpace($script:warningsLog)) {
        $warnHeader = "# Ingest Warnings`n`n以下对象在摄取过程中触发了异常，已跳过对应的占位符替换，但整体流程继续：`n`n"
        Write-Utf8NoBom -Path (Join-Path $OutDir 'ingest_warnings.md') -Text ($warnHeader + $script:warningsLog)
    }

    # 关闭副本
    try { $doc.Close($false) } catch {}
    $doc = $null

} finally {
    if ($null -ne $doc) {
        try { $doc.Close($false) } catch {}
    }
    try { $word.Quit() } catch {}
    try { [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null } catch {}
    [System.GC]::Collect()
    [System.GC]::WaitForPendingFinalizers()
    Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
}

# ----------------------------------------------------------------------
# 完成摘要（stdout 末尾）
# ----------------------------------------------------------------------
Write-Output "INGEST_OK"
Write-Output "MANIFEST_DIR: $OutDir"
Write-Output ("ANCHOR_MODE: " + $(if ($script:UseBookmarkAnchor) { 'bookmark (B)' } else { 'inplace (A)' }))
Write-Output "PLACEHOLDERS: zotero=$citeIdx, axmath_ole=$eqIdx, omml=$eqOmmlIdx, ref_eq=$xrefEqIdx, ref_fig=$xrefFigIdx, ref_tbl=$xrefTblIdx, ref_sec=$xrefSecIdx, seq_fig=$seqFigIdx, seq_tbl=$seqTblIdx, footnotes=$fnIdx, images=$figIdx, tables=$tblIdx, bookmarks=$($bookmarksList.Count)"
# 塌陷哨兵：调用方（agent）据此决定是否提示用户加 -UseBookmarkAnchor 重摄取
Write-Output "COLLAPSE_INTERCEPTED: $($script:collapseCount)"
if ($script:collapseCount -gt 0 -and -not $script:UseBookmarkAnchor) {
    Write-Output "ACTION_REQUIRED: 检测到 $($script:collapseCount) 处占位符位置塌陷（已拦截未污染）。建议加 -UseBookmarkAnchor 重新运行本脚本以启用 B 方案（书签锚点）。"
}
exit 0