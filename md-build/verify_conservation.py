# -*- coding: utf-8 -*-
"""
verify_conservation.py -- post-build OBJECT-COUNT conservation CHECK for md-build (WARN-only).

The build pipeline (manuscript.md -> pandoc + crossref + zotero/citeproc -> docx) can SILENTLY
drop a figure, table or formula: pandoc still exits 0, the docx still opens, and you only notice
the missing object when you proofread the printout. verify_refs guards CITATIONS (dropped/undefined/
fabricated keys) across the md text; this surfaces the FINAL OUTPUT count: it counts figures /
tables / formulas (and citations) in the source vs the produced word/document.xml and WARNS LOUDLY
if they differ.

WHAT THE TWO SIDES ARE (important -- this is NOT "original paper vs revised"):
  source = the CURRENT manuscript.md (its pandoc AST). output = the docx built FROM that same
  manuscript.md. Both derive from the same current source, so an AUTHOR deletion/addition shows up
  on BOTH sides and never causes a mismatch. A mismatch means the COMPILE STEP changed the count
  (pandoc/a filter dropped or duplicated an object) -- something the author never asked for.

WHY count from the pandoc AST, not regex on the .md (handbook 6.5 rule (3): AST over regex):
  Markdown math/figure/table syntax is genuinely hard to count with regex (escaped $, pipe tables,
  implicit figures). The pandoc AST (`pandoc manuscript.md -t json`) is the authoritative parse of
  what the author wrote, so we count Image / Table / Math / Cite nodes there. The OUTPUT side is
  counted in word/document.xml (drawings / w:tbl / m:oMath / ZOTERO_ITEM), which is post-everything.

WARN-ONLY, NEVER BLOCKS THE BUILD (author's call, and the right one):
  A count mismatch is reported LOUDLY but NEVER fails the build (always exit 0). Reason: some
  mismatches are "rendered imperfectly", not "catastrophically missing" -- e.g. an exotic LaTeX
  formula pandoc can't parse falls back to plain text (no m:oMath), or an image format it can't embed
  is dropped. Those produce a perfectly usable docx; blocking the WHOLE build over one rendering
  quirk is too heavy. So we surface the difference (output<source = likely a real loss, worth a look;
  output>source = likely a reference-doc template artifact) and let the author decide. Citations are
  also WARN-only and already have dense coverage (verify_refs + verify_citekeys + the build's own
  'not found' counter); static bakes citations (no ZOTERO_ITEM) so citations are skipped there.

Usage:
  py verify_conservation.py --ast build/_ast_<mode>.json --docx build/out_<mode>.docx --mode rebuild
Exit: ALWAYS 0 (warn-only). A nonzero exit means the script itself errored (bad args / unreadable
file), not a count mismatch.
"""
import argparse
import io
import json
import os
import re
import sys
import zipfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Cross-ref kinds whose [@kind:label] Cite nodes are NOT real citations (consumed by pandoc-crossref,
# never become Zotero fields). Mirrors md-swarm/_citescan.XREF_KINDS (kept inline so md-build stays
# self-contained -- it must not import across skill directories).
XREF_KINDS = ('fig', 'tbl', 'eq', 'sec')


def _is_real_key(cid):
    """A citationId is a real citekey if it is not a fig:/tbl:/eq:/sec: cross-ref and not a NEW marker."""
    if not isinstance(cid, str) or not cid:
        return False
    if cid.split(':', 1)[0].lower() in XREF_KINDS:
        return False
    if cid.upper().startswith('NEW'):
        return False
    return True


def count_ast(ast):
    """Walk the pandoc AST. Return (real_cite_groups, images, tables, math).
    real_cite_groups = number of Cite nodes containing >=1 real citekey (each becomes ONE Zotero
    field downstream, mirroring zotero_offline.lua's one-field-per-Cite behaviour)."""
    cnt = {'cite': 0, 'image': 0, 'table': 0, 'math': 0}

    def walk(x):
        if isinstance(x, dict):
            t = x.get('t')
            if t == 'Cite':
                ids = [c.get('citationId') for c in x['c'][0]]
                if any(_is_real_key(i) for i in ids):
                    cnt['cite'] += 1
            elif t == 'Image':
                cnt['image'] += 1
            elif t == 'Table':
                cnt['table'] += 1
            elif t == 'Math':
                cnt['math'] += 1
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(ast)
    return cnt['cite'], cnt['image'], cnt['table'], cnt['math']


def count_docx(docx_path):
    """Count objects in word/document.xml. Returns (zotero_fields, drawings, tables, math).
    document.xml is BODY-only (headers/footers are separate parts), so reference-doc template logos
    don't inflate these. Tag-shape choices verified against pandoc 3.9.0.2 output:
      drawings  <pic:pic ...>     (one per embedded image)
      tables    <w:tbl>           (exact; <w:tblPr>/<w:tblGrid>/<w:tblStyle...> must NOT match)
      math      <m:oMath ...>     (display+inline; <m:oMathPara> must NOT match -> uses [ >] after oMath)
      citations literal 'ZOTERO_ITEM' (live/rebuild field code; absent in static)"""
    with zipfile.ZipFile(docx_path) as z:
        x = z.read('word/document.xml').decode('utf-8')
    zotero = x.count('ZOTERO_ITEM')
    drawings = len(re.findall(r'<pic:pic[ >]', x))
    tables = len(re.findall(r'<w:tbl>', x))
    math = len(re.findall(r'<m:oMath[ >]', x))
    return zotero, drawings, tables, math


def _line(label, src, out, src_name, out_name):
    """Format one conservation row. Returns (text, status) where status is 'ok' | 'loss' | 'extra'.
    Both 'loss' and 'extra' are WARN-only (never block the build); the distinction is informational."""
    if out < src:
        return ('[%-8s] %s=%d  %s=%d  -> WARN: %d %s in source did NOT make it into the docx'
                ' (missing image file? unparsable formula rendered as text? -- check, not blocked)'
                % (label, src_name, src, out_name, out, src - out, label), 'loss')
    if out > src:
        return ('[%-8s] %s=%d  %s=%d  -> WARN: %d extra in output (reference-doc template? duplicate?)'
                % (label, src_name, src, out_name, out, out - src), 'extra')
    return ('[%-8s] %s=%d  %s=%d  -> OK' % (label, src_name, src, out_name, out), 'ok')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ast', required=True, help='pandoc -t json of the SOURCE manuscript.md')
    ap.add_argument('--docx', required=True, help='the produced output docx')
    ap.add_argument('--mode', default='live', choices=['live', 'rebuild', 'static', 'smoke'])
    a = ap.parse_args()

    if not os.path.exists(a.ast):
        print('ERROR: --ast not found:', a.ast); sys.exit(2)
    if not os.path.exists(a.docx):
        print('ERROR: --docx not found:', a.docx); sys.exit(2)

    with open(a.ast, encoding='utf-8') as f:
        ast = json.load(f)
    cite_s, img_s, tbl_s, math_s = count_ast(ast)
    zot_o, draw_o, tbl_o, math_o = count_docx(a.docx)

    print('=== verify_conservation ===')
    print('source AST :', a.ast)
    print('output docx:', a.docx, '(mode: %s)' % a.mode)
    print('')

    warns = 0
    for label, src, out, sn, on in (
        ('figure', img_s, draw_o, 'images', 'drawings'),
        ('table', tbl_s, tbl_o, 'tables', 'tables'),
        ('formula', math_s, math_o, 'math', 'math'),
    ):
        text, status = _line(label, src, out, sn, on)
        print(text)
        if status != 'ok':
            warns += 1

    # Citations: WARN-only, and only where the output actually carries Zotero fields (live/rebuild/smoke).
    if a.mode == 'static':
        print('[cite    ] skipped (static bakes citations -> no Zotero fields to conserve)')
    else:
        if zot_o == cite_s:
            print('[cite    ] groups=%d  zotero-fields=%d  -> OK' % (cite_s, zot_o))
        else:
            warns += 1
            print('[cite    ] groups=%d  zotero-fields=%d  -> WARN: %d citation group(s) not realized'
                  ' as Zotero fields.' % (cite_s, zot_o, abs(cite_s - zot_o)))
            print('           In -Mode rebuild these are refs added AFTER unpack (not in the offline'
                  ' map) -> use -Mode live. In -Mode live they are keys Zotero could not resolve'
                  " (see the build's 'not found' warning). Not a silent-loss bug; not blocked.")

    print('')
    # WARN-only: always exit 0. The summary just tells the author whether anything is worth a look.
    if warns:
        print('=== result: OK-with-warnings (%d count mismatch(es) above; nothing blocked --'
              ' eyeball them) ===' % warns)
    else:
        print('=== result: OK (every figure/table/formula/citation accounted for) ===')
    sys.exit(0)


def _selftest():
    """Regression self-test (handbook 6.5 rule (4)). `python verify_conservation.py --selftest`."""
    # AST with: one real cite group [@a;@b], one xref [@fig:1] (must NOT count as cite),
    # two images, one table, two math.
    def cite(ids):
        return {'t': 'Cite', 'c': [[{'citationId': i} for i in ids], []]}
    ast = {'blocks': [
        cite(['a', 'b']), cite(['fig:1']), cite(['NEW: x 2020']),
        {'t': 'Image', 'c': []}, {'t': 'Image', 'c': []},
        {'t': 'Table', 'c': []},
        {'t': 'Math', 'c': []}, {'t': 'Math', 'c': []},
    ]}
    cg, im, tb, mt = count_ast(ast)
    assert (cg, im, tb, mt) == (1, 2, 1, 2), (cg, im, tb, mt)

    # docx counting against a hand-built document.xml; oMathPara must NOT inflate math, tblPr must
    # NOT inflate tables.
    body = ('<w:document><w:body>'
            '<pic:pic></pic:pic><pic:pic></pic:pic>'
            '<w:tbl><w:tblPr/></w:tbl>'
            '<m:oMathPara><m:oMath>x</m:oMath></m:oMathPara><m:oMath>y</m:oMath>'
            'ZOTERO_ITEM'
            '</w:body></w:document>')
    import tempfile
    p = os.path.join(tempfile.mkdtemp(), 'd.docx')
    with zipfile.ZipFile(p, 'w') as z:
        z.writestr('word/document.xml', body)
    zo, dr, tb2, m2 = count_docx(p)
    assert (zo, dr, tb2, m2) == (1, 2, 1, 2), (zo, dr, tb2, m2)

    # _line classification (all WARN-only; status is informational, never blocks)
    assert _line('figure', 2, 1, 'images', 'drawings')[1] == 'loss'    # output < source
    assert _line('figure', 2, 3, 'images', 'drawings')[1] == 'extra'   # output > source
    assert _line('figure', 2, 2, 'images', 'drawings')[1] == 'ok'      # equal
    print('OK verify_conservation self-test passed')


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        _selftest()
    else:
        main()
