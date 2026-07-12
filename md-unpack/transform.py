# -*- coding: utf-8 -*-
"""
md-unpack core: turn a docx-unpack manifest into a pandoc-syntax manuscript.md.
Base = manifest/manuscript.md (deterministic placeholder positions).
Citations -> provisional authorYear citekeys + CSL-JSON bib (reconcile later to real BBT keys).
OMML equations -> harvested positionally from a pandoc direct-conversion md.

Usage:
  python transform.py --placeholder-md manifest/manuscript.md --objects-json manifest/objects.json \
                      --direct-md build/direct.md --images-src manifest/images \
                      --out-md manuscript.md --references-out references.json \
                      --citemap-out build/citemap.tsv --images-dst images
All paths may be absolute or relative to CWD.
"""
import json, re, io, sys, os, unicodedata, shutil, argparse
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

ap = argparse.ArgumentParser()
ap.add_argument('--placeholder-md', required=True)
ap.add_argument('--objects-json', required=True)
ap.add_argument('--direct-md', default=None, help='pandoc direct-conversion md, for OMML LaTeX harvest (legacy/fallback)')
ap.add_argument('--direct-json', default=None, help='pandoc -t json of the docx; preferred OMML math source (the AST labels math, so $currency$ in prose is not mis-harvested)')
ap.add_argument('--images-src', default=None)
ap.add_argument('--out-md', required=True)
ap.add_argument('--references-out', required=True)
ap.add_argument('--citemap-out', required=True)
ap.add_argument('--images-dst', default='images')
ap.add_argument('--title', default='')
a = ap.parse_args()

obj = json.load(open(a.objects_json, encoding='utf-8'))
ph  = obj['placeholders']
md  = open(a.placeholder_md, encoding='utf-8').read()
direct = open(a.direct_md, encoding='utf-8').read() if (a.direct_md and os.path.exists(a.direct_md)) else ''

# ---------- 0. T21-2: fold Word-math-styled placeholders back to ASCII ----------
# ingest replaces an OMath range's .Text with "[EQ-OMML-N]"; because that range IS a math zone,
# Word restyles the inserted letters/digits as Mathematical Alphanumeric Symbols (U+1D400-1D7FF)
# and the hyphen as MINUS SIGN (U+2212), so "[EQ-OMML-1]" comes back math-bold (E,Q,O,M,L,1 from the
# U+1D400 block, '-' as U+2212). Every downstream matcher below is ASCII (the harvest count line,
# _PH_INLINE, the [EQ-OMML-N] substitution), so without this they all miss it -> 0 OMML matches and
# the equations are left as literal garbage in Word (this was 测试21 bug T21-2).
# NFKC folds the math-alphanumeric block to plain ASCII; U+2212 is NOT folded by NFKC so map it
# explicitly. Scope = only the chars inside a bracketed token that actually contains a math-styled
# char (the unmistakable signature); prose ligatures / full-width CJK punctuation stay byte-identical.
# NOTE: covers the U+1D400-1D7FF block only -- italic lowercase 'h' and the script/fraktur/double-
# struck "holes" live in BMP letterlike symbols (e.g. U+210E) and are not folded; harmless here
# because the only math-styled placeholder is [EQ-OMML-N] (uppercase E,Q,O,M,L, all in-block).
def _fold_math_glyphs(tok):
    out = []
    for ch in tok:
        if 0x1D400 <= ord(ch) <= 0x1D7FF:
            out.append(unicodedata.normalize('NFKC', ch))
        elif ord(ch) == 0x2212:   # MINUS SIGN -> ASCII HYPHEN-MINUS (NFKC leaves U+2212 as-is)
            out.append('-')
        else:
            out.append(ch)
    return ''.join(out)

_MATH_PH = re.compile(r'\[[^\[\]\n]*[\U0001D400-\U0001D7FF][^\[\]\n]*\]')
md = _MATH_PH.sub(lambda m: _fold_math_glyphs(m.group(0)), md)

# ---------- 1. citation: [CITE-N] -> [citekey,...] + CSL-JSON bib ----------
def ascii_family(name):
    if not name: return 'anon'
    n = unicodedata.normalize('NFKD', name).encode('ascii','ignore').decode('ascii')
    return re.sub(r'[^A-Za-z]', '', n).lower() or 'anon'

bib, used, zkey_to_ck, cite_keys, citemap_rows = {}, {}, {}, {}, []

def make_citekey(itemData, uri):
    m = re.search(r'/items/(\w+)', uri or ''); zkey = m.group(1) if m else None
    if zkey and zkey in zkey_to_ck: return zkey_to_ck[zkey], zkey
    fam, yr = 'anon', 'nd'
    au = itemData.get('author') or itemData.get('editor')
    if isinstance(au, list) and au:
        fam = ascii_family(au[0].get('family') or au[0].get('literal') or au[0].get('name'))
    try: yr = str(itemData.get('issued',{})['date-parts'][0][0])
    except Exception:
        raw = itemData.get('issued',{}).get('raw','')
        yr = (re.sub(r'\D','',raw)[:4] or 'nd') if raw else 'nd'
    base = f"{fam}{yr}"; ck = base
    if base in used and (not zkey or zkey not in zkey_to_ck):
        used[base]+=1; ck = base + chr(ord('a')+used[base]-1)
    else: used.setdefault(base,1)
    if zkey: zkey_to_ck[zkey]=ck
    bib[ck] = {**itemData, 'id': ck}
    return ck, zkey

for k,v in ph.items():
    m = re.match(r'\[CITE-(\d+)\]', k)
    if not m or v.get('type')!='zotero': continue
    n = m.group(1); jm = re.search(r'CSL_CITATION\s*(\{.*\})\s*$', v.get('fieldCode',''), re.S)
    keys=[]
    if jm:
        try:
            for ci in json.loads(jm.group(1)).get('citationItems',[]):
                idata = ci.get('itemData',{}); uri=(ci.get('uris') or [None])[0]
                ck,zk = make_citekey(idata, uri); keys.append(ck)
                citemap_rows.append((f"CITE-{n}", ck, zk or '', (idata.get('title') or '')[:80]))
        except Exception as e:
            citemap_rows.append((f"CITE-{n}", "PARSE_ERROR", '', str(e)[:60]))
    cite_keys[f"CITE-{n}"] = keys

# ---------- 2. harvest OMML LaTeX (prefer pandoc AST; avoids mistaking $currency$ for math) ----------
# The old approach -- re.findall(r'\$...\$', direct) on the rendered md -- also grabbed literal
# dollar signs in prose ("$5 million"), which shifted the positional [EQ-OMML-N] mapping and
# scrambled equations (a real risk in econ/finance papers). The AST labels Math nodes explicitly,
# so money is never collected; we walk it in document order to keep the N-th-placeholder mapping.
n_omml_ph = len(re.findall(r'\[EQ-OMML-\d+\]', md))

def _harvest_math_ast(js):
    out = []
    def walk(node):
        if isinstance(node, dict):
            if node.get('t') == 'Math':
                c = node.get('c')
                if isinstance(c, list) and len(c) == 2 and isinstance(c[1], str):
                    disp = isinstance(c[0], dict) and c[0].get('t') == 'DisplayMath'
                    out.append(('$$' + c[1] + '$$') if disp else ('$' + c[1] + '$'))
                return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(js.get('blocks', js) if isinstance(js, dict) else js)
    return out

omml_tex, _omml_src = [], 'none'
if a.direct_json and os.path.exists(a.direct_json):
    try:
        omml_tex = _harvest_math_ast(json.load(open(a.direct_json, encoding='utf-8'))); _omml_src = 'pandoc-AST'
    except Exception as e:
        print('WARN: could not parse --direct-json (%s).' % e)
if not omml_tex and n_omml_ph and direct:
    omml_tex = re.findall(r'\$[^$\n]+\$', direct); _omml_src = 'regex-fallback (may over-collect $currency$)'

def omml_for(i): return omml_tex[i-1] if 0 < i <= len(omml_tex) else None

if n_omml_ph != len(omml_tex):
    print('WARN: OMML count mismatch -- %d [EQ-OMML-N] placeholder(s) vs %d harvested (%s). '
          'Equation positions may be off; verify the $...$ by hand.' % (n_omml_ph, len(omml_tex), _omml_src))

# ---------- 2.5 Tier-3: escape Markdown-significant chars in PROSE (protect placeholders) ----------
# Word run text is literal, but a bare  $ [ ] _ * ` < | ~ ^ @  is (mis)read as Markdown by pandoc --
# e.g. "$33 ... $325" becomes inline math "33 ... ". We cannot blanket-escape: md still holds
# placeholders ([CITE-N], [FIG-N:..], ...) whose brackets must survive the replacements below, which
# then EMIT clean Markdown (added AFTER this step, so never double-escaped). So: protect placeholder
# tokens, escape only the prose between them; skip whole-line structure (headings, [FIG/TBL/EQ-N]
# block lines). KNOWN RESIDUAL (the "simple" pass): footnote bodies, table cells and figure captions
# are injected from objects.json later and are NOT covered here -- the AST refactor would close those.
_MD_SPECIAL = set('\\`*_[]$<|~^@')
# protect EVERY placeholder form (incl. inline block-type [EQ-N]/[TBL-N]/[FIG-N:..] that some docs
# put mid-paragraph) so the escaper never adds a stray backslash inside a placeholder token.
# XREF-SEC MUST be here too: it is removed at section 3 (below) by a regex matching a literal ']';
# if the escaper isn't told it's a placeholder, it escapes the bracket to '\]' and the removal then
# silently misses every one (15 left as literal junk on the 测试21 real manuscript, 2026-06-29).
_PH_INLINE = re.compile(r'\[(?:CITE|XREF-FIG|XREF-TBL|XREF-EQ|XREF-SEC|EQ-OMML|SEQ-FIG|SEQ-TBL|EQNUM|FN|TBL|EQ)-\d+\]|\[FIG-\d+:[^\]\n]*\]')
_esc_count = 0
def _esc_prose(s):
    global _esc_count
    r = []
    for c in s:
        if c in _MD_SPECIAL: r.append('\\'); _esc_count += 1
        r.append(c)
    return ''.join(r)
def _esc_line(line):
    st = line.lstrip()
    if st.startswith('#') or re.match(r'\[(?:FIG|TBL|EQ)-\d+', st):
        return line  # structural line: keep headings' # and [FIG/TBL/EQ-N] block placeholders intact
    out, last = [], 0
    for m in _PH_INLINE.finditer(line):
        out.append(_esc_prose(line[last:m.start()])); out.append(m.group(0)); last = m.end()
    out.append(_esc_prose(line[last:]))
    return ''.join(out)
md = '\n'.join(_esc_line(l) for l in md.split('\n'))

# ---------- 3. inline replacements ----------
def repl_cite(mm):
    ks = cite_keys.get(f"CITE-{mm.group(1)}",[])
    return ("[" + "; ".join("@"+k for k in ks) + "]") if ks else f"[@MISSING-CITE-{mm.group(1)}]"
md = re.sub(r'\[CITE-(\d+)\]', repl_cite, md)
md = re.sub(r'\[XREF-FIG-(\d+)\]', lambda m: f"[@fig:{m.group(1)}]", md)
md = re.sub(r'\[XREF-TBL-(\d+)\]', lambda m: f"[@tbl:{m.group(1)}]", md)
md = re.sub(r'\[XREF-EQ-(\d+)\]',  lambda m: f"[@eq:{m.group(1)}]", md)
# [XREF-SEC-N]: cross-refs to section/bookmarks whose visible text was lost at ingest. In real drafts
# they replaced CONNECTIVE prose ("it can be seen from"), not section numbers -> emitting "Section N"
# would fabricate. Lightweight 止血 (T21-4, 2026-06-28): drop them (same as SEQ-* below) so they don't
# leave literal junk in the Word output; proofreading restores any missing connective. Full fix (later)
# = real live [@sec:N] links + {#sec:N} heading anchors -- BLOCKED on real data (2026-07-11 survey of
# 测试21 objects.json): displayText is empty on all 15, the _Ref* target bookmarks were never harvested
# by ingest (0/15 resolvable), and headings are inconsistently hand-numbered; see dev handbook 10 T21-4.
# C0 (2026-07-11): the drop is now LOUD -- every removal site is collected with +/-30 chars of context
# and reported at the end (console WARN + build/xref_sec_removed.md), so proofreading gets a checklist
# instead of a silent hole. Context is captured at THIS mid-pipeline stage (neighbouring placeholders
# may still look like [CITE-N]); locate by the prose words, not the tokens.
# Mirrored in _test_t21_fixes.py (test_xref_sec_cleanup / test_xref_sec_loud_removal).
_xref_sec_removed = []
def _drop_xref_sec(m):
    s, i, j = m.string, m.start(), m.end()
    _xref_sec_removed.append(
        (s[max(0, i - 30):i] + '>>>' + m.group(0) + '<<<' + s[j:j + 30]).replace('\n', ' '))
    return ''
md = re.sub(r'\[XREF-SEC-\d+\]', _drop_xref_sec, md)
md = re.sub(r'\[EQ-OMML-(\d+)\]', lambda m: (omml_for(int(m.group(1))) or f"`<TODO-EQ-OMML-{m.group(1)}>`"), md)
md = re.sub(r'\[SEQ-FIG-\d+\]', '', md); md = re.sub(r'\[SEQ-TBL-\d+\]', '', md); md = re.sub(r'\[EQNUM-\d+\]', '', md)
md = re.sub(r'\[FN-(\d+)\]', lambda m: "^[" + (ph.get(f"[FN-{m.group(1)}]",{}).get('body','').replace('\n',' ').strip()) + "]", md)

# ---------- 4. line-aware FIG / TBL / AxMath EQ ----------
img_ext = {}
for k,v in ph.items():
    fm = re.match(r'\[FIG-(\d+):', k)
    if fm and v.get('type')=='image': img_ext[fm.group(1)] = os.path.splitext(v.get('src',''))[1] or '.jpeg'
def strip_fig(c): return re.sub(r'^Figure\s+\d+\s*', '', c).strip()
def strip_tbl(c): return re.sub(r'^Table\b\s*\d*\s*', '', c).strip()

# same-line concatenated block placeholders ("[FIG-1: a][FIG-2: b]", "[FIG-1: a][EQ-2]") -> one per
# line, so the whole-line matchers below catch each (the test-19 residual: 4 figures jammed on one
# line). ONLY fires when the whole line is 2+ block placeholders with nothing else, so it can never
# split normal prose. Caption boundary [^\]\n]* matches the assumption already used by _PH_INLINE.
_BLK = re.compile(r'\[FIG-\d+:[^\]\n]*\]|\[TBL-\d+\]|\[EQ-\d+\]')
_PURE_BLK_LINE = re.compile(r'^\s*(?:(?:\[FIG-\d+:[^\]\n]*\]|\[TBL-\d+\]|\[EQ-\d+\])\s*){2,}$')
def _split_block_line(line):
    return '\n'.join(_BLK.findall(line)) if _PURE_BLK_LINE.match(line) else line
md = '\n'.join(_split_block_line(l) for l in md.split('\n'))

# ---------- 4.5 T21-5: route Front-Matter (collapsed) figures to an isolation section ----------
# ingest's ConvertToInlineShape can collapse group-extracted images to Range.Start=0..3, so their
# [FIG-N:] placeholders land in the Front Matter block (before the first real section) -- on 测试21
# they were jammed onto the title line. Left there, the whole-line FIG matcher below never fires, so
# {#fig:N} is never emitted and body [@fig:N] dangles -> md-build HARD (the T21-5 bug). We do NOT
# fabricate a position: pull them out of the front matter and re-emit proper image definitions in an
# explicit "unanchored" section at the end (so {#fig:N} exists and [@fig:N] resolves), with a WARN,
# for the author to place by hand. Done in this TEXT stage on purpose -- never touch the Word-COM
# ingest (the suite's most fragile, untestable layer); this routing stays unit-testable instead.
_unanchored = []
_fm_lines = md.split('\n')
# Bound the scan to the Front Matter block: the '## (Front Matter)' line (ingest always emits it) up
# to the next heading. NOTE: must anchor on that marker, NOT "first heading that isn't (Front Matter)"
# -- the placeholder manifest's line 1 is '# Manuscript (Placeholder-protected)', which would falsely
# end the region at index 0 (the bug caught on the 测试21 e2e re-run, 2026-06-29).
_fm_start = next((_i for _i, _l in enumerate(_fm_lines)
                  if _l.lstrip().startswith('#') and '(Front Matter)' in _l), None)
if _fm_start is not None:
    _fm_end = next((_i for _i in range(_fm_start + 1, len(_fm_lines))
                    if _fm_lines[_i].lstrip().startswith('#')), len(_fm_lines))
    _FM_FIG = re.compile(r'\[FIG-(\d+):\s*([^\]\n]*)\]')
    def _pull_fig(m): _unanchored.append((m.group(1), m.group(2).strip())); return ''
    for _i in range(_fm_start + 1, _fm_end):
        _fm_lines[_i] = _FM_FIG.sub(_pull_fig, _fm_lines[_i])
    md = '\n'.join(_fm_lines)

lines = md.split('\n'); out=[]; i=0
while i < len(lines):
    ln = lines[i]; fm = re.match(r'^\[FIG-(\d+):\s*(.*?)\]\s*$', ln.strip())
    tm = re.match(r'^\[TBL-(\d+)\]\s*$', ln.strip()); em = re.match(r'^\[EQ-(\d+)\]\s*$', ln.strip())
    if fm:
        n=fm.group(1); out.append(f"![{strip_fig(fm.group(2))}](images/fig_{n}{img_ext.get(n,'.jpeg')}){{#fig:{n}}}")
        j=i+1
        while j<len(lines) and lines[j].strip()=='' : j+=1
        if j<len(lines) and re.match(r'^Figure\b', lines[j].strip()): i=j+1; continue
        i+=1; continue
    if tm:
        n=tm.group(1); cap=''
        for bi in range(len(out)-1, max(0,len(out)-4), -1):
            if re.match(r'^Table\b', out[bi].strip()): cap=strip_tbl(out[bi].strip()); out[bi]='__DROP__'; break
        out += ['', ph.get(f"[TBL-{n}]",{}).get('md','').strip(), '', (f": {cap} {{#tbl:{n}}}" if cap else f": Table {n} {{#tbl:{n}}}"), '']
        j=i+1
        while j<len(lines):
            s=lines[j].strip()
            if s=='': j+=1; continue
            if len(s)<=40 and not s.endswith('.') and not s.startswith('#') and not s.startswith('!['): j+=1; continue
            break
        i=j; continue
    if em:
        n=em.group(1); out.append(f"$$\\text{{[TODO: AxMath eq {n} -- re-enter LaTeX; preview images/eq_{n}_preview.wmf]}}$$ {{#eq:{n}}}"); i+=1; continue
    out.append(ln); i+=1

md_out = '\n'.join(l for l in out if l!='__DROP__')
# inline AxMath [EQ-N] left mid-sentence (the whole-line ones already became $$..$$ display math
# above, so no literal [EQ-N] remains in those). Render as inline math TODO so it never survives as
# literal "[EQ-N]" text in the docx. Inline math can't carry a {#eq:N} crossref label, so none added.
md_out = re.sub(r'\[EQ-(\d+)\]',
                lambda m: "$\\text{[TODO: AxMath eq %s -- re-enter LaTeX; preview images/eq_%s_preview.wmf]}$" % (m.group(1), m.group(1)),
                md_out)
# ---- T21-5 C3/C4 (2026-07-11): dress the quarantine -- true captions + placement hints ----
# C3: the collapsed figures' REAL captions are not lost -- ingest's caption-format survey line in
# manifest/ingest_warnings.md lists every "Figure N <caption>" label. Parse it (split anchored on
# '/Figure <digit>', rule 1 allowlist, so captions containing '/' survive) and re-attach.
# C4: locate each figure's HOME section by reading the ORIGINAL docx (objects.json sourceDocx):
# media name -> rId (document.xml.rels), rId offset in document.xml (searched as a bare quoted
# attribute value -- group-born images don't use r:embed), nearest PRECEDING level-1 section title
# (objects.json sections) found in the concatenated w:t text stream (tag-stripped, so titles split
# across runs still match; verified on 测试21: image1/2/3/50 -> 4.3/21/24/29.6%, matches diagnosis).
# Pure zipfile+regex, NO Word COM; ANY failure degrades silently to the plain quarantine.
# Mirrored in _test_t21_fixes.py::test_t21_5_captions_and_homes.
def _parse_true_captions(warn_text):
    m = re.search(r'图题注存在多套格式\(label:\s*(.*)\)，caption_formats\.figure', warn_text)
    caps = {}
    if not m:
        return caps
    for piece in re.split(r'/(?=Figure\s+\d)', m.group(1)):
        pm = re.match(r'Figure\s+(\d+)\s*[:：]?\s*(.*)$', piece.strip())
        if pm and pm.group(2).strip():
            caps[pm.group(1)] = pm.group(2).strip()
    return caps

def _wt_stream(doc_xml):
    """Concatenate all <w:t> run texts; keep (stream_start, xml_offset) per run so a hit in the
    stream maps back to a document.xml offset (headings split across runs become findable)."""
    parts, starts, xml_offs, pos = [], [], [], 0
    for m in re.finditer(r'<w:t[^>]*>([^<]*)</w:t>', doc_xml):
        parts.append(m.group(1)); starts.append(pos); xml_offs.append(m.start()); pos += len(m.group(1))
    return ''.join(parts), starts, xml_offs

def _title_xml_offset(stream, starts, xml_offs, title):
    t = ' '.join(title.split())
    p = stream.find(t)
    if p < 0:  # flexible whitespace fallback (runs may carry odd spacing)
        m = re.search(re.sub(r'\\?\s+', r'\\s+', re.escape(t)), stream)
        p = m.start() if m else -1
    if p < 0:
        return None
    for i in range(len(starts) - 1, -1, -1):
        if starts[i] <= p:
            return xml_offs[i]
    return None

def _figure_homes(objects_json_path, obj, fig_nums, placeholders):
    """fig_num -> (level-1 section title, position pct in original docx). {} on any problem."""
    try:
        import zipfile
        src = obj.get('sourceDocx') or ''
        workdir = os.path.dirname(os.path.dirname(os.path.abspath(objects_json_path)))
        if not (src and os.path.exists(src)):
            cand = os.path.join(workdir, os.path.basename(src)) if src else ''
            if cand and os.path.exists(cand):
                src = cand
            else:
                return {}
        z = zipfile.ZipFile(src)
        rels = z.read('word/_rels/document.xml.rels').decode('utf-8', 'ignore')
        doc = z.read('word/document.xml').decode('utf-8', 'ignore')
        rid = {m.group(2): m.group(1) for m in
               re.finditer(r'Id="(rId\d+)"[^>]*Target="media/([^"]+)"', rels)}
        stream, starts, xml_offs = _wt_stream(doc)
        beacons = []
        for s in obj.get('sections') or []:
            if s.get('level') == 1 and '(Front Matter)' not in (s.get('title') or ''):
                off = _title_xml_offset(stream, starts, xml_offs, s['title'])
                if off is not None:
                    beacons.append((off, s['title']))
        beacons.sort()
        media_of = {}
        for k, v in placeholders.items():
            fm = re.match(r'\[FIG-(\d+):', k)
            if fm and v.get('originalMedia'):
                media_of[fm.group(1)] = v['originalMedia']
        homes = {}
        for n in fig_nums:
            r = rid.get(media_of.get(n, ''))
            if not r:
                continue
            pos = doc.find('"%s"' % r)
            if pos < 0:
                continue
            prior = [b for b in beacons if b[0] < pos]
            title = prior[-1][1] if prior else '(Front Matter)'
            homes[n] = (title, 100.0 * pos / max(len(doc), 1))
        return homes
    except Exception:
        return {}

# T21-5: emit the routed Front-Matter figures (see 4.5) as a real, labelled isolation section so
# their {#fig:N} definitions exist (body [@fig:N] resolves, md-build needs no -SkipRefCheck).
if _unanchored:
    _true_caps = {}
    try:
        _warn_p = os.path.join(os.path.dirname(os.path.abspath(a.objects_json)), 'ingest_warnings.md')
        if os.path.exists(_warn_p):
            _true_caps = _parse_true_captions(open(_warn_p, encoding='utf-8').read())
    except Exception:
        _true_caps = {}
    _homes = _figure_homes(a.objects_json, obj, [n for n, _ in _unanchored], ph)
    _iso = ['', '## (Unanchored figures -- please place manually)', '',
            '> WARNING: %d figure(s) collapsed to the document front during ingest (T21-5) and could '
            'not be auto-placed. Their definitions are emitted here so cross-references resolve; please '
            'move each to the suggested location below (captions restored from the ingest survey where '
            'available).' % len(_unanchored), '']
    for _n, _cap in _unanchored:
        _c = _esc_prose(_true_caps[_n]) if _n in _true_caps else strip_fig(_cap)
        _iso.append('![%s](images/fig_%s%s){#fig:%s}' % (_c, _n, img_ext.get(_n, '.png'), _n))
        if _n in _homes:
            _iso.append('')
            _iso.append('> 建议去处：『%s』一节（原稿约 %.0f%% 处）' % _homes[_n])
        _iso.append('')
    md_out = md_out.rstrip() + '\n\n' + '\n'.join(_iso)

fm_idx = md_out.find('## (Front Matter)')
if fm_idx == -1: fm_idx = md_out.find('## INTRODUCTION')
if fm_idx > 0: md_out = md_out[fm_idx:]
md_out = re.sub(r'\n{3,}', '\n\n', md_out)

# --- invisible-whitespace normalization (md-swarm patch-match safety) ---
# Word/Zotero embed non-breaking & thin spaces -- especially BETWEEN AUTHOR NAMES in
# citations ("Hawken and<U+00A0>Kleiman") -- that look identical to a normal space on
# screen. A sub-agent reading this source copies a NORMAL space into its patch `find`,
# but apply does an exact match, so the patch HARD-fails on an invisible character that
# nobody can see (the test-18 accident: ~31 NBSP, all in citations, sank a whole batch).
# Fold them to plain spaces (or drop the zero-width ones) so the .md is clean text.
# Citations are re-rendered by Zotero at build time, so the NBSP carries no meaning here.
_INVIS_FOLD = tuple(chr(o) for o in (0x00a0, 0x2007, 0x2009, 0x202f))           # nbsp/figure/thin/narrow-nbsp -> normal space
_INVIS_DROP = tuple(chr(o) for o in (0x200b, 0x200c, 0x200d, 0x2060, 0xfeff, 0x00ad))  # zero-width/WJ/BOM/soft-hyphen -> remove
_invis_n = sum(md_out.count(c) for c in _INVIS_FOLD + _INVIS_DROP)
for _c in _INVIS_FOLD: md_out = md_out.replace(_c, ' ')
for _c in _INVIS_DROP: md_out = md_out.replace(_c, '')

# --- XML-illegal control-char strip (Tier-1 hard-fail: "Word won't open the .docx") ---
# Word's Range.Text encodes embedded inline objects/fields as C0 control chars (an AxMath/OLE
# object -> U+0001; a field boundary -> U+0013/14/15; etc.). The ingest blacklisted only a few
# of these one code-point at a time, so the rest slipped through (the test-19 accident: a lone
# U+0001 in a figure caption reached the .md; pandoc copied it verbatim into <wp:docPr descr=...>
# in document.xml, and ANY char outside XML 1.0's legal set makes that part non-well-formed -> Word
# refuses to open the whole file). Use XML's legal-Char set as a WHITELIST instead of chasing code
# points forever: drop everything illegal (C0 except tab/LF/CR, lone surrogates, U+FFFE/FFFF).
_XML_ILLEGAL = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F\uD800-\uDFFF\uFFFE\uFFFF]')
_ctrl_n = len(_XML_ILLEGAL.findall(md_out))
md_out = _XML_ILLEGAL.sub('', md_out)

# --- title placeholder guard (P3-3) ---
# Refuse to write a title that is obviously a template/sample placeholder (the prior
# accident: agent copied the example word "论文标题" as a real value -> YAML title became "论文").
# If the auto-detected or passed title hits this blacklist, leave the YAML title field EMPTY
# (better no title than a wrong one) and warn so a human sets it.
_PLACEHOLDER_TITLES = {
    '论文标题', '论文', '标题', '题目', '无标题', '新文档', '文档', '草稿', '示例标题',
    'title', 'the title', 'untitled', 'untitled document', 'new document', 'document', 'placeholder',
}
title_use = (a.title or '').strip()
title_raw = title_use
if title_use:
    if title_use.lower() in _PLACEHOLDER_TITLES or len(title_use) <= 2:
        print('WARN: title looks like a placeholder/sample word (%r) -> NOT writing it into YAML.'
              ' Set the real title with -Title, or edit manuscript.md YAML by hand.' % title_raw)
        title_use = ''

yaml = "---\n"
if title_use: yaml += f'title: "{title_use}"\n'
yaml += "zotero:\n  client: zotero\n  scannable-cite: false\ncrossref:\n  fig-title: Figure\n  tbl-title: Table\n---\n\n"
md_out = yaml + md_out

os.makedirs(os.path.dirname(os.path.abspath(a.out_md)), exist_ok=True)
open(a.out_md,'w',encoding='utf-8',newline='').write(md_out)  # newline='' -> write LF (not CRLF); keeps AI-authored LF patch find/replace matching apply
json.dump(list(bib.values()), open(a.references_out,'w',encoding='utf-8'), ensure_ascii=False, indent=1)
os.makedirs(os.path.dirname(os.path.abspath(a.citemap_out)), exist_ok=True)
with open(a.citemap_out,'w',encoding='utf-8') as f:
    f.write("placeholder\tprovisional_citekey\tzotero_item_key\ttitle\n")
    for r in citemap_rows: f.write("\t".join(map(str,r))+"\n")

copied=0
if a.images_src and os.path.isdir(a.images_src):
    os.makedirs(a.images_dst, exist_ok=True)
    for fn in os.listdir(a.images_src):
        shutil.copy2(os.path.join(a.images_src,fn), os.path.join(a.images_dst,fn)); copied+=1

print("OK", a.out_md)
print("unique citekeys:", len(bib), "| CITE placeholders:", len(cite_keys),
      "| OMML harvested:", "%d (%s)" % (len(omml_tex), _omml_src), "| images:", copied,
      "| invisible ws normalized:", _invis_n,
      "| xml-illegal ctrl stripped:", _ctrl_n,
      "| prose md-escaped:", _esc_count,
      # residual markers: count ANY leftover placeholder shape, not a fixed whitelist.
      # Old regex required `XREF-` then a DIGIT, so it silently missed `[XREF-SEC-9]` (SEC in the
      # middle) and the Unicode math-alphanumeric `[EQ-OMML-N]` written in mathbold -> reported 4
      # when ~22 were really left (T21-3, 2026-06-28 真稿测试21). Now: `[` + one-or-more `UPPER-`
      # groups + an alnum, OR `[` + any char in the Mathematical-Alphanumeric block (U+1D400-1D7FF).
      # Uses an ESCAPE range (not an astral literal in source -- 6.5 rule (2): don't risk a GBK editor
      # garbling a 4-byte char). Won't hit `[@fig:1]`/`[Smith 2020]`/`[TODO:…]` (@ / lower / : block it).
      "| residual markers:",
      len(re.findall(r'\[(?:[A-Z]+-)+[A-Za-z0-9]' + '|\\[[\U0001D400-\U0001D7FF]', md_out)))
if _unanchored:
    _cap_note = ', '.join('fig %s' % n for n, _ in _unanchored if n in _true_caps)
    _home_note = '; '.join('fig %s -> %s (~%.0f%%)' % (n, _homes[n][0], _homes[n][1])
                           for n, _ in _unanchored if n in _homes)
    print('WARN: %d Front-Matter figure(s) routed to an "Unanchored figures" section (T21-5): fig %s '
          '-- placeholders had collapsed to the doc front at ingest; their {#fig:N} are now defined so '
          'cross-refs resolve, but place each figure by hand.' % (len(_unanchored), ', '.join(n for n,_ in _unanchored)))
    if _cap_note:
        print('      true captions restored from the ingest survey for: %s.' % _cap_note)
    if _home_note:
        print('      suggested homes (from the original docx): %s.' % _home_note)
if _xref_sec_removed:
    _xsr = os.path.join(os.path.dirname(os.path.abspath(a.citemap_out)), 'xref_sec_removed.md')
    with open(_xsr, 'w', encoding='utf-8', newline='\n') as f:
        f.write('# 摄取时移除的章节交叉引用（[XREF-SEC-N] · T21-4 止血账单）\n\n')
        f.write('这些位置原有一个指向某章节的 Word 交叉引用（或其顶替的连接词），显示文字在摄取时已丢失、\n')
        f.write('无法还原，故已移除（绝不编造 "Section N"）。**校对时逐条看语句是否通顺，需要就手补文字。**\n\n')
        f.write('> 注：上下文取自转换中间态，个别 [CITE-N] 等标记在成品 manuscript.md 里已变成 [@key]——\n')
        f.write('> 按前后正文文字定位即可（`>>>...<<<` 处即被移除的位置）。\n\n')
        for k, ctx in enumerate(_xref_sec_removed, 1):
            f.write('%d. %s\n' % (k, ctx))
    print('WARN: %d section cross-reference placeholder(s) [XREF-SEC-N] removed (display text lost at '
          'ingest, T21-4). Every removal site is listed with context in: %s '
          '-- proofread those sentences and hand-restore wording where needed.'
          % (len(_xref_sec_removed), _xsr))

# ---------- Tier-3 sentinel (2026-07-11): watch the escaper's three blind spots ----------
# The section-2.5 prose escaper covers BODY prose only. Footnote bodies (injected at section 3),
# table pipe-md and figure captions (section 4) enter AFTER it, unescaped -- the parked "Tier-3
# thorough / AST rebase" gap (dev handbook 10). 2026-07-11 double survey: today's real corpus is
# safe (footnotes/captions carry zero metachars; stat-table `_cons`/`D_positive`/`* * *` parse
# LITERAL on the pinned pandoc 3.9.0.2 -- flanking rules protect them). So instead of a 500-900
# line AST rebase, this sentinel WARNs (never rewrites) on the few forms that WOULD really mangle:
#   footnote body : ']' (ends the ^[...] inline footnote early), pairable $...$, paired `...`
#   table cell    : pairable $...$, paired `...`   (NOT `_`/`*`/[..] -- empirically literal; the
#                   sentinel must stay SILENT on today's regression tables, rule 1 allowlist)
#   figure caption: '[' or ']' (breaks ![...]), pairable $...$, paired `...`
# _T3_MATH approximates pandoc's tex_math_dollars flanking rule (open $ hugs non-space on its
# right, close $ hugs non-space on its left, not followed by a digit) -- "$33 to $45" stays quiet,
# "$x$"/"$33$" trips. Mirrored in _test_t21_fixes.py::test_tier3_sentinel.
_T3_MATH = re.compile(r'\$(?!\s)[^$\n]*?(?<!\s)\$(?!\d)')
_T3_CODE = re.compile(r'`[^`\n]+`')
def _t3_risks(text, kind):
    risks = []
    if not text: return risks
    if kind in ('footnote', 'caption') and ']' in text: risks.append("']' breaks the syntax")
    if kind == 'caption' and '[' in text: risks.append("'[' breaks the syntax")
    if _T3_MATH.search(text): risks.append('pairable $...$ would render as math')
    if _T3_CODE.search(text): risks.append('paired `...` would render as code')
    return risks
def _t3_scan(placeholders):
    hits = []
    for k, v in placeholders.items():
        if k.startswith('[FN-'):
            body = v.get('body', '')
            for r in _t3_risks(body, 'footnote'): hits.append((k, r, body[:40]))
        elif k.startswith('[TBL-'):
            seen = set()
            for line in (v.get('md') or '').split('\n'):
                for cell in line.split('|'):
                    for r in _t3_risks(cell, 'cell'):
                        if r not in seen:
                            seen.add(r); hits.append((k, r, cell.strip()[:40]))
        elif k.startswith('[FIG-'):
            cap = v.get('caption') or ''
            for r in _t3_risks(cap, 'caption'): hits.append((k, r, cap[:40]))
    return hits
_t3_hits = _t3_scan(ph)
if _t3_hits:
    print('WARN: Tier-3 sentinel: %d injected item(s) carry metacharacters that WILL mis-render '
          '(footnote/table/caption content is NOT covered by the prose escaper):' % len(_t3_hits))
    for k, r, ex in _t3_hits:
        print('   %s: %s  e.g. %r' % (k, r, ex))
    print('   -> after md-build, eyeball those exact spots in the docx; hand-escape (backslash) the '
          'character in manuscript.md if it rendered wrong.')
