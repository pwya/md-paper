# _test_t21_fixes.py -- standalone regex assertions for the 测试21 (2026-06-28) transform.py fixes.
# transform.py parses args at top level (cannot be imported), so per dev-manual 6.5-4 we mirror its
# regexes here and assert their logic. Run: python _test_t21_fixes.py   (ASCII-only prints; PS5.1 GBK-safe)
import re, unicodedata

# ---- Bug T21-2: Word-math-styled OMML placeholders must be folded back to ASCII ----
# MUST mirror transform.py section 0 exactly (_fold_math_glyphs + _MATH_PH). ingest writes the
# OMML placeholder into a math zone, so Word returns "[EQ-OMML-N]" as Mathematical Alphanumeric
# Symbols (U+1D400-1D7FF) + MINUS SIGN (U+2212). Folding restores the ASCII the downstream
# matchers expect (the count regex r'\[EQ-OMML-\d+\]', _PH_INLINE, the [EQ-OMML-N] substitution).
def _fold_math_glyphs(tok):
    out = []
    for ch in tok:
        if 0x1D400 <= ord(ch) <= 0x1D7FF:
            out.append(unicodedata.normalize('NFKC', ch))
        elif ord(ch) == 0x2212:
            out.append('-')
        else:
            out.append(ch)
    return ''.join(out)
_MATH_PH = re.compile(r'\[[^\[\]\n]*[\U0001D400-\U0001D7FF][^\[\]\n]*\]')
def fold(md): return _MATH_PH.sub(lambda m: _fold_math_glyphs(m.group(0)), md)

def test_omml_placeholder_fold():
    # the real test-21 shape: [EQ-OMML-1] math-bold = [<mathE><mathQ>-<mathO><mathM><mathM><mathL>-1]
    mathbold = "[\U0001D438\U0001D444−\U0001D442\U0001D440\U0001D440\U0001D43F−1]"
    out = fold(mathbold)
    assert out == "[EQ-OMML-1]", repr(out)
    # multiple, with surrounding prose untouched
    src = "The model " + mathbold.replace("1]", "2]") + " shows that " + mathbold
    folded = fold(src)
    assert "[EQ-OMML-2]" in folded and "[EQ-OMML-1]" in folded, repr(folded)
    # after folding, the ASCII count regex (mirrors transform.py:83) now finds both
    assert len(re.findall(r'\[EQ-OMML-\d+\]', folded)) == 2, folded
    # math digits in the index also fold (mathbold '3' = U+1D7D1, since U+1D7CE is bold zero)
    assert fold("[\U0001D438\U0001D444−\U0001D442\U0001D440\U0001D440\U0001D43F−\U0001D7D1]") == "[EQ-OMML-3]"
    # surgical scope: a bracketed token with NO math-styled char is left byte-identical
    assert fold("[range -5 to 5]") == "[range -5 to 5]"
    assert fold("[EQ-OMML-7]") == "[EQ-OMML-7]"          # already-ASCII placeholder untouched
    assert fold("[@fig:1] and [Smith 2020]") == "[@fig:1] and [Smith 2020]"
    # a lone math char OUTSIDE brackets (defensive) is not touched (no enclosing [...] match)
    assert fold("E = mc^2 with \U0001D438 free") == "E = mc^2 with \U0001D438 free"
    print("[OK] Bug T21-2 OMML math-glyph fold: 8 assertions pass")

# ---- Bug T21-3: residual-marker counter must catch ANY leftover placeholder shape ----
# MUST mirror transform.py's residual-markers regex exactly. Escape range (not astral literal):
# U+1D400-1D7FF = Mathematical Alphanumeric Symbols block (covers mathbold 𝐸𝑄𝑂𝑀𝑀𝐿 etc.).
RESID = r'\[(?:[A-Z]+-)+[A-Za-z0-9]' + '|\\[[\U0001D400-\U0001D7FF]'
def resid(s): return len(re.findall(RESID, s))

def test_residual_counter():
    # should count
    assert resid("[XREF-SEC-9]") == 1
    assert resid("[XREF-SEC-1]from Figures [XREF-SEC-2]") == 2
    assert resid("[FIG-1: Figure 1][FIG-2: Figure 2]") == 2
    assert resid("[EQ-OMML-3]") == 1
    assert resid("[\U0001D438\U0001D444−\U0001D442\U0001D440\U0001D440\U0001D43F−5]") == 1  # [𝐸𝑄−𝑂𝑀𝑀𝐿−5]
    assert resid("[CITE-7]") == 1 and resid("[EQNUM-2]") == 1 and resid("[FN-4]") == 1
    # should NOT count (legit content)
    assert resid("[@fig:1]") == 0
    assert resid("[@wilson2015; @broek1995]") == 0
    assert resid("[Smith 2020] and [see above]") == 0
    assert resid(r"$\text{[TODO: AxMath eq 2 -- re-enter LaTeX]}$") == 0
    # real test-21 shape: 4 FIG + 15 XREF-SEC + 3 Unicode = 22
    sample = ("[FIG-1: a][FIG-2: b][FIG-3: c][FIG-4: d]"
              + "".join("[XREF-SEC-%d]" % i for i in range(1, 16))
              + "[\U0001D438\U0001D444-1][\U0001D438\U0001D444-2][\U0001D438\U0001D444-3]")
    assert resid(sample) == 22, resid(sample)
    print("[OK] Bug T21-3 residual counter: 14 assertions pass (sample=22)")

# ---- Bug T21-4 (lightweight 止血): [XREF-SEC-N] dropped to empty, not left as junk ----
# In the real draft these placeholders replaced LOST connective prose ("it can be seen from"),
# NOT actual section numbers -- so emitting "Section N" would FABRICATE wrong text. Lightweight fix
# removes them (same as SEQ-FIG/SEQ-TBL at transform.py:158); proofreading restores any connective.
# Full fix (deferred) = real live [@sec:N] links + {#sec:N} heading anchors.
# mirrors transform.py section 3 C0 (2026-07-11): the removal now COLLECTS each site's +/-30 char
# context (one line, >>>token<<< marker) for the loud report. Keep collector logic in sync.
def clean_sec_loud(md, sink):
    def _drop(m):
        s, i, j = m.string, m.start(), m.end()
        sink.append((s[max(0, i - 30):i] + '>>>' + m.group(0) + '<<<' + s[j:j + 30]).replace('\n', ' '))
        return ''
    return re.sub(r'\[XREF-SEC-\d+\]', _drop, md)
def clean_sec(md): return clean_sec_loud(md, [])

def test_xref_sec_cleanup():
    # the literal [XREF-SEC-N] junk must be gone
    out = clean_sec("As [XREF-SEC-9] Figure 10, the proportion")
    assert "[XREF-SEC-" not in out, out
    assert out == "As  Figure 10, the proportion"   # placeholder gone (leaves a double space, harmless)
    out2 = clean_sec("see [XREF-SEC-3] and [XREF-SEC-12] above")
    assert "[XREF-SEC-" not in out2, out2
    # must NOT touch the other xref kinds (those become live [@fig:]/[@tbl:]/[@eq:])
    assert clean_sec("[XREF-FIG-2]") == "[XREF-FIG-2]"
    print("[OK] Bug T21-4 lightweight XREF-SEC removal: 3 assertions pass")

# ---- T21-4 C0 (2026-07-11): the removal must be LOUD -- context collected per site ----
def test_xref_sec_loud_removal():
    sink = []
    src = "start of a sentence [XREF-SEC-3] tail words here\nnext line has [XREF-SEC-7] too"
    out = clean_sec_loud(src, sink)
    assert '[XREF-SEC-' not in out, out
    assert len(sink) == 2, sink
    # context window carries the surrounding prose + the marked token
    assert '>>>[XREF-SEC-3]<<<' in sink[0] and 'sentence' in sink[0] and 'tail words' in sink[0], sink[0]
    assert '>>>[XREF-SEC-7]<<<' in sink[1], sink[1]
    assert '\n' not in sink[0] and '\n' not in sink[1]      # flattened to one report line each
    # window is clamped at doc start (no negative-index wrap pulling text from the tail)
    sink_edge = []
    clean_sec_loud("[XREF-SEC-1] right at the start", sink_edge)
    assert sink_edge[0].startswith('>>>[XREF-SEC-1]<<<'), sink_edge[0]
    # no placeholders -> no report entries (the WARN block stays silent)
    sink2 = []
    clean_sec_loud("no placeholders here", sink2)
    assert sink2 == []
    print("[OK] T21-4 C0 loud removal: 8 assertions pass")

# ---- Bug T21-4b: XREF-SEC must survive the prose escaper so the removal can still fire ----
# Pipeline-order regression (the isolated test above missed this): in transform.py the §2.5 prose
# escaper runs BEFORE the §3 removal. If _PH_INLINE doesn't list XREF-SEC, the escaper turns ']' into
# '\]' and the literal-']' removal silently misses all of them (15 left as junk on 测试21, 2026-06-29).
# Mirror _PH_INLINE (MUST include XREF-SEC) + the escaper + the removal, in the real order.
_PH_MIRROR = re.compile(r'\[(?:CITE|XREF-FIG|XREF-TBL|XREF-EQ|XREF-SEC|EQ-OMML|SEQ-FIG|SEQ-TBL|EQNUM|FN|TBL|EQ)-\d+\]|\[FIG-\d+:[^\]\n]*\]')
_SPECIAL = set('\\`*_[]$<|~^@')
def _esc_prose(s): return ''.join(('\\' + c if c in _SPECIAL else c) for c in s)
def _esc_line(line):
    out, last = [], 0
    for m in _PH_MIRROR.finditer(line):
        out.append(_esc_prose(line[last:m.start()])); out.append(m.group(0)); last = m.end()
    out.append(_esc_prose(line[last:]))
    return ''.join(out)

def test_xref_sec_survives_escaper():
    src = "As [XREF-SEC-9] see [XREF-SEC-10] and price $5"
    escaped = _esc_line(src)
    # XREF-SEC must come through the escaper UNmangled (bracket not turned into '\]')
    assert "[XREF-SEC-9]" in escaped and "[XREF-SEC-10]" in escaped, escaped
    # ...so the removal (literal ']') then actually deletes them
    removed = re.sub(r'\[XREF-SEC-\d+\]', '', escaped)
    assert "XREF-SEC" not in removed, removed
    # and surrounding prose is still escaped (escaper did run)
    assert "\\$5" in removed, removed
    print("[OK] Bug T21-4b XREF-SEC survives escaper -> removed: 3 assertions pass")

# ---- Bug T21-5: Front-Matter (collapsed) figures routed to an isolation section ----
# MUST mirror transform.py section 4.5. ingest can collapse group-extracted images to the doc front;
# their [FIG-N:] then sit in the Front Matter block (before the first REAL heading) and {#fig:N} never
# gets emitted -> body [@fig:N] dangles -> md-build HARD. Routing pulls them out (so they leave no junk)
# and re-emits {#fig:N} defs in an isolation section so cross-refs resolve.
_FM_FIG = re.compile(r'\[FIG-(\d+):\s*([^\]\n]*)\]')
def route_frontmatter_figs(md):
    unanch = []
    lines = md.split('\n')
    # anchor on the '## (Front Matter)' marker, NOT "first heading" -- the manifest's line 1 is
    # '# Manuscript (Placeholder-protected)', which would falsely end the region at index 0.
    fm_start = next((i for i, l in enumerate(lines)
                     if l.lstrip().startswith('#') and '(Front Matter)' in l), None)
    if fm_start is None:
        return md, unanch
    fm_end = next((i for i in range(fm_start + 1, len(lines))
                   if lines[i].lstrip().startswith('#')), len(lines))
    def take(m): unanch.append((m.group(1), m.group(2).strip())); return ''
    for i in range(fm_start + 1, fm_end):
        lines[i] = _FM_FIG.sub(take, lines[i])
    return '\n'.join(lines), unanch

def test_frontmatter_fig_routing():
    # leading '# Manuscript ...' title line is REQUIRED here -- it's what exposed the index-0 bug.
    src = ("# Manuscript (Placeholder-protected)\n\n"
           "## (Front Matter)\n"
           "[FIG-1: Figure 1][FIG-2: Figure 2]The Real Title\n\n"
           "## 1. Introduction\n\n"
           "[FIG-5: Figure 5]\n")           # body fig on its own line, AFTER a real heading
    out, unanch = route_frontmatter_figs(src)
    assert [n for n, _ in unanch] == ['1', '2'], unanch        # the two front-matter figs pulled out
    assert '[FIG-1:' not in out and '[FIG-2:' not in out, out  # ...and gone from the front matter
    assert '[FIG-5: Figure 5]' in out, out                     # body fig is NOT touched
    assert 'The Real Title' in out, out                        # title prose preserved (only figs removed)
    assert '# Manuscript' in out, out                          # the title heading is NOT eaten
    # the isolation emit gives each a {#fig:N} so body [@fig:1]/[@fig:2] resolve (no more dangling)
    defs = ''.join('![](images/fig_%s.png){#fig:%s}' % (n, n) for n, _ in unanch)
    assert '{#fig:1}' in defs and '{#fig:2}' in defs, defs
    # no front-matter figs -> no-op (don't route real body-only docs)
    out2, unanch2 = route_frontmatter_figs("## 1. Intro\n\n[FIG-1: Figure 1]\n")
    assert unanch2 == [] and '[FIG-1: Figure 1]' in out2, (unanch2, out2)
    print("[OK] Bug T21-5 front-matter figure routing: 7 assertions pass")

# ---- Tier-3 sentinel (2026-07-11): the escaper's three blind spots get a WARN-only watch ----
# MUST mirror transform.py's _T3_MATH/_T3_CODE/_t3_risks/_t3_scan. Allowlist calibrated on real data:
# today's corpus (stat tables full of `_cons` / `D_positive` / `* * *`, CI brackets [0.05, 0.10])
# must stay SILENT -- those parse literal on the pinned pandoc (verified 2026-07-11).
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

def test_tier3_sentinel():
    # --- negatives: today's REAL corpus patterns must stay silent (allowlist calibration) ---
    quiet = {
        '[FN-1]':  {'body': 'See the appendix for the full derivation.'},
        '[FN-2]':  {'body': 'Prices rose from $33 to $45 per barrel.'},   # pandoc-literal $ shape
        '[TBL-5]': {'md': '| _cons | 0.05744 * * * |\n| D_positive | 0.02846 * * |'},
        '[TBL-6]': {'md': '| CI | [0.05, 0.10] |'},                        # CI brackets in cells: fine
        '[FIG-1: Figure 1]': {'caption': 'Distribution of piracy incidents'},
    }
    assert _t3_scan(quiet) == [], _t3_scan(quiet)
    # --- positives: the forms that WOULD really mangle ---
    loud = {
        '[FN-3]':  {'body': 'see Smith [2020] appendix'},         # ']' ends ^[...] early
        '[FN-4]':  {'body': 'the term $x$ denotes output'},       # pairable math
        '[TBL-7]': {'md': '| price | $33$ |\n| p | `code` |'},    # in-cell math + code pair
        '[FIG-2: Figure 2]': {'caption': 'Effect sizes [subset]'} # bracket breaks ![...]
    }
    hits = _t3_scan(loud)
    who = {k for k, _r, _e in hits}
    assert who == {'[FN-3]', '[FN-4]', '[TBL-7]', '[FIG-2: Figure 2]'}, hits
    assert sum(1 for k, r, _ in hits if k == '[TBL-7]') == 2, hits          # math + code, deduped per kind
    assert any(']' in r for k, r, _ in hits if k == '[FN-3]'), hits
    # $ opener/closer flanking: lone '$5' and spaced '$ 5 $' stay quiet; '$$x$$' trips
    assert _t3_risks('costs $5 million', 'cell') == []
    assert _t3_risks('a $ 5 $ b', 'cell') == []
    assert _t3_risks('$$x$$', 'cell') != []
    print("[OK] Tier-3 sentinel: 8 assertions pass (silent on real corpus, loud on true dangers)")

# ---- T21-5 C3/C4 (2026-07-11): quarantine dressing -- true captions + placement hints ----
# MUST mirror transform.py: _parse_true_captions / _wt_stream / _title_xml_offset + nearest-beacon.
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
    parts, starts, xml_offs, pos = [], [], [], 0
    for m in re.finditer(r'<w:t[^>]*>([^<]*)</w:t>', doc_xml):
        parts.append(m.group(1)); starts.append(pos); xml_offs.append(m.start()); pos += len(m.group(1))
    return ''.join(parts), starts, xml_offs

def _title_xml_offset(stream, starts, xml_offs, title):
    t = ' '.join(title.split())
    p = stream.find(t)
    if p < 0:
        m = re.search(re.sub(r'\\?\s+', r'\\s+', re.escape(t)), stream)
        p = m.start() if m else -1
    if p < 0:
        return None
    for i in range(len(starts) - 1, -1, -1):
        if starts[i] <= p:
            return xml_offs[i]
    return None

def test_t21_5_captions_and_homes():
    # --- C3: caption survey line parsing ---
    warn = ('- 图片计数失配：…\n'
            '- 图题注存在多套格式(label: Figure 1 Number of pirate incidents: Central Asia and Africa'
            '/Figure 2 GDP/capita trends by region'
            '/Figure 15: Effect diagram of the permutation test)，caption_formats.figure 取主档案。\n')
    caps = _parse_true_captions(warn)
    assert caps['1'] == 'Number of pirate incidents: Central Asia and Africa', caps
    assert caps['2'] == 'GDP/capita trends by region', caps          # '/' inside a caption survives
    assert caps['15'] == 'Effect diagram of the permutation test', caps  # 'Figure 15:' colon stripped
    assert _parse_true_captions('no survey line here') == {}
    # --- C4: run-split title located + nearest-beacon home selection ---
    doc = ('<w:p><w:t>1.</w:t><w:t> Introduction</w:t></w:p>'
           '<w:p><w:t>intro prose…</w:t></w:p><w:drawing r:embed="rId7"/>'
           '<w:p><w:t>Research </w:t><w:t>Design</w:t></w:p>'
           '<w:p><w:t>design prose…</w:t></w:p><w:pict><v:imagedata r:id="rId9"/></w:pict>')
    stream, starts, xml_offs = _wt_stream(doc)
    o_intro = _title_xml_offset(stream, starts, xml_offs, '1. Introduction')   # split across two runs
    o_rd = _title_xml_offset(stream, starts, xml_offs, 'Research Design')
    assert o_intro is not None and o_rd is not None and o_intro < o_rd, (o_intro, o_rd)
    assert _title_xml_offset(stream, starts, xml_offs, 'No Such Section') is None
    beacons = sorted([(o_intro, '1. Introduction'), (o_rd, 'Research Design')])
    for rid_token, want in (('"rId7"', '1. Introduction'), ('"rId9"', 'Research Design')):
        pos = doc.find(rid_token)
        prior = [b for b in beacons if b[0] < pos]
        home = prior[-1][1] if prior else '(Front Matter)'
        assert home == want, (rid_token, home)
    print("[OK] T21-5 C3/C4 captions+homes: 9 assertions pass")

if __name__ == "__main__":
    test_omml_placeholder_fold()
    test_residual_counter()
    test_xref_sec_cleanup()
    test_xref_sec_loud_removal()
    test_xref_sec_survives_escaper()
    test_frontmatter_fig_routing()
    test_tier3_sentinel()
    test_t21_5_captions_and_homes()
    print("ALL T21 transform.py fix tests passed.")
