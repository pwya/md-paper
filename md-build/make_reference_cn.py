# -*- coding: utf-8 -*-
"""make_reference_cn.py -- generate a style-only reference.docx (样式马甲) for md-build -Reference.

Deterministic ElementTree surgery on pandoc's default reference styles.xml (rule 3: AST over
regex). Parameterized (2026-07-11) so /md-build's format-asking step can build a vest from the
user's answers; all defaults = the author's spec:
  body    SongTi + Times New Roman, 12pt (xiao-4), 1.5 line spacing,
          0 before/after, first-line indent 2 CJK characters
  chapter (md '##' -> Word Heading2; Heading1 dressed the same as a fallback)  HeiTi 16pt BOLD
  section (md '###' -> Word Heading3)                                          HeiTi 14pt not bold
  caption (Image/Table/Caption)                  body font, own pt, 1.0x, centered, no indent
  tables  academic three-line style + AutoFit to contents.
Suite heading convention (md-unpack): paper chapters are markdown '##', so the vest dresses
Heading2 as the chapter style; Heading1 is normally unworn.

Usage:
  py make_reference_cn.py [--out PATH] [--body-cn 宋体] [--body-latin "Times New Roman"]
                          [--body-pt 12] [--line 1.5]
                          [--chapter-cn 黑体] [--chapter-pt 16] [--chapter-bold|--no-chapter-bold]
                          [--section-cn 黑体] [--section-pt 14] [--section-bold|--no-section-bold]
                          [--caption-pt 12] [--caption-center|--no-caption-center]
  py make_reference_cn.py --selftest
字号速查: 三号=16pt 四号=14 小四=12 五号=10.5 (pt 可带小数).
"""
import argparse, io, os, subprocess, sys, zipfile
import xml.etree.ElementTree as ET

PANDOC = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'md-pandoc', 'pandoc.exe')
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reference-cn.docx')
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
ET.register_namespace('w', W)
def q(tag): return '{%s}%s' % (W, tag)
def half(pt): return str(int(round(float(pt) * 2)))           # pt -> half-points
def line_of(mult): return str(int(round(float(mult) * 240)))  # 1.0x = 240

def ensure(parent, tag, first=False):
    el = parent.find(q(tag))
    if el is None:
        el = ET.Element(q(tag))
        (parent.insert(0, el) if first else parent.append(el))
    return el

def ensure_before(parent, tag, before_tags):
    """Ensure tag exists, inserting it before the first schema-later sibling."""
    el = parent.find(q(tag))
    if el is not None:
        return el
    el = ET.Element(q(tag))
    wanted = {q(x) for x in before_tags}
    for i, child in enumerate(list(parent)):
        if child.tag in wanted:
            parent.insert(i, el)
            return el
    parent.append(el)
    return el

def patch(style, *, east, latin, sz_half, bold=None, line=None, center=False,
          before=None, after=None, first_line_chars=None, first_line_dxa=None):
    rpr = ensure(style, 'rPr'); ppr = ensure(style, 'pPr')
    f = ensure(rpr, 'rFonts', first=True)
    f.set(q('ascii'), latin); f.set(q('hAnsi'), latin)
    f.set(q('eastAsia'), east); f.set(q('cs'), latin)
    for t in ('sz', 'szCs'):
        ensure(rpr, t).set(q('val'), sz_half)
    if bold is not None:
        for t in ('b', 'bCs'):
            b = ensure(rpr, t)
            if bold: b.attrib.pop(q('val'), None)
            else: b.set(q('val'), '0')
    if line is not None or before is not None or after is not None:
        sp = ensure(ppr, 'spacing')
        if line is not None:
            sp.set(q('line'), line); sp.set(q('lineRule'), 'auto')
        if before is not None:
            sp.set(q('before'), str(before))
            for name in ('beforeLines', 'beforeAutospacing'):
                sp.attrib.pop(q(name), None)
        if after is not None:
            sp.set(q('after'), str(after))
            for name in ('afterLines', 'afterAutospacing'):
                sp.attrib.pop(q(name), None)
    if first_line_chars is not None:
        ind = ensure_before(ppr, 'ind', ('contextualSpacing', 'mirrorIndents',
                                         'suppressOverlap', 'jc'))
        ind.attrib.pop(q('hanging'), None)
        ind.attrib.pop(q('hangingChars'), None)
        ind.set(q('firstLineChars'), str(first_line_chars))
        ind.set(q('firstLine'), str(first_line_dxa if first_line_dxa is not None else 0))
    if center:
        ensure(ppr, 'jc').set(q('val'), 'center')


def set_black(style):
    """Force a style's text color to black (removes the default blue/gray theme color)."""
    rpr = ensure(style, 'rPr')
    color = ensure(rpr, 'color')
    color.set(q('val'), '000000')
    color.attrib.pop(q('themeColor'), None)
    color.attrib.pop(q('themeShade'), None)
    color.attrib.pop(q('themeTint'), None)


def set_border(parent, tag, *, val, size, color):
    border = ensure(parent, tag)
    border.set(q('val'), val)
    border.set(q('sz'), str(size))
    border.set(q('space'), '0')
    border.set(q('color'), color)

def patch_three_line_table(style):
    """Patch pandoc's Table style: three rules, no vertical grid, AutoFit."""
    tbl_pr = ensure(style, 'tblPr')
    borders = ensure_before(tbl_pr, 'tblBorders', ('shd', 'tblLayout', 'tblCellMar'))
    borders.clear()
    set_border(borders, 'top', val='single', size=12, color='000000')
    set_border(borders, 'left', val='nil', size=0, color='auto')
    set_border(borders, 'bottom', val='single', size=12, color='000000')
    set_border(borders, 'right', val='nil', size=0, color='auto')
    set_border(borders, 'insideH', val='nil', size=0, color='auto')
    set_border(borders, 'insideV', val='nil', size=0, color='auto')

    layout = ensure_before(tbl_pr, 'tblLayout', ('tblCellMar', 'tblLook'))
    layout.set(q('type'), 'autofit')

    first_row = next((x for x in style.findall(q('tblStylePr'))
                      if x.get(q('type')) == 'firstRow'), None)
    if first_row is None:
        first_row = ET.SubElement(style, q('tblStylePr'), {q('type'): 'firstRow'})
    tc_pr = ensure(first_row, 'tcPr')
    tc_borders = ensure(tc_pr, 'tcBorders')
    bottom = ensure(tc_borders, 'bottom')
    bottom.set(q('val'), 'single')
    bottom.set(q('sz'), '6')
    bottom.set(q('space'), '0')
    bottom.set(q('color'), '000000')

def build(cfg, out_path):
    ref = subprocess.run([PANDOC, '--print-default-data-file', 'reference.docx'],
                         capture_output=True)
    assert ref.returncode == 0 and ref.stdout[:2] == b'PK', 'pandoc default reference dump failed'
    zin = zipfile.ZipFile(io.BytesIO(ref.stdout))
    root = ET.fromstring(zin.read('word/styles.xml'))
    styles = {s.get(q('styleId')): s for s in root.findall(q('style'))}
    assert all(i in styles for i in ('Normal', 'Heading2', 'Heading3', 'Table')), \
        'unexpected reference layout'

    ln = line_of(cfg['line'])
    body_first_line = str(int(round(float(cfg['body_pt']) * 40)))  # 2 em in DXA
    for i in ('Normal', 'BodyText', 'FirstParagraph'):
        if i in styles:
            patch(styles[i], east=cfg['body_cn'], latin=cfg['body_latin'],
                  sz_half=half(cfg['body_pt']), line=ln, before=0, after=0,
                  first_line_chars=200, first_line_dxa=body_first_line)
    for i in ('Heading1', 'Heading2'):   # chapter level (suite: '##' == chapter)
        if i in styles:
            patch(styles[i], east=cfg['chapter_cn'], latin=cfg['body_latin'],
                  sz_half=half(cfg['chapter_pt']), bold=cfg['chapter_bold'], line=ln,
                  before=0, after=0, first_line_chars=0, first_line_dxa=0)
    patch(styles['Heading3'], east=cfg['section_cn'], latin=cfg['body_latin'],
          sz_half=half(cfg['section_pt']), bold=cfg['section_bold'], line=ln,
          before=0, after=0, first_line_chars=0, first_line_dxa=0)
    # All headings black (author spec: 所有标题标黑). H4-H9 keep the default pandoc
    # fonts/sizes -- only their color is forced to black.
    for i in ('Heading1', 'Heading2', 'Heading3', 'Heading4', 'Heading5',
              'Heading6', 'Heading7', 'Heading8', 'Heading9'):
        if i in styles:
            set_black(styles[i])
    for i in ('ImageCaption', 'TableCaption', 'Caption'):
        if i in styles:
            patch(styles[i], east=cfg['body_cn'], latin=cfg['body_latin'],
                  sz_half=half(cfg['caption_pt']), line=line_of(1.0),
                  center=cfg['caption_center'], before=0, after=0,
                  first_line_chars=0, first_line_dxa=0)
    if 'Compact' in styles:
        patch(styles['Compact'], east=cfg['body_cn'], latin=cfg['body_latin'],
              sz_half=half(cfg['body_pt']), line=ln, before=0, after=0,
              first_line_chars=0, first_line_dxa=0)
    patch_three_line_table(styles['Table'])

    out_styles = ET.tostring(root, xml_declaration=True, encoding='UTF-8')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = out_styles if item.filename == 'word/styles.xml' else zin.read(item.filename)
            zout.writestr(item, data)
    with open(out_path, 'wb') as fh:
        fh.write(buf.getvalue())
    return len(buf.getvalue())

def verify(out_path, cfg):
    """Assert the written vest carries the resolved values. Returns list of failed check names."""
    r = ET.fromstring(zipfile.ZipFile(out_path).read('word/styles.xml'))
    s = {x.get(q('styleId')): x for x in r.findall(q('style'))}
    def got(sid, path, attr):
        el = s[sid].find('/'.join(q(p) for p in path))
        return None if el is None else el.get(q(attr))
    def got_first_row(path, attr):
        row = next((x for x in s['Table'].findall(q('tblStylePr'))
                    if x.get(q('type')) == 'firstRow'), None)
        if row is None:
            return None
        el = row.find('/'.join(q(p) for p in path))
        return None if el is None else el.get(q(attr))
    ln = line_of(cfg['line'])
    checks = [
        ('Normal eastAsia', got('Normal', ['rPr', 'rFonts'], 'eastAsia'), cfg['body_cn']),
        ('Normal latin', got('Normal', ['rPr', 'rFonts'], 'ascii'), cfg['body_latin']),
        ('Normal sz', got('Normal', ['rPr', 'sz'], 'val'), half(cfg['body_pt'])),
        ('Normal line', got('Normal', ['pPr', 'spacing'], 'line'), ln),
        ('Normal before', got('Normal', ['pPr', 'spacing'], 'before'), '0'),
        ('Normal after', got('Normal', ['pPr', 'spacing'], 'after'), '0'),
        ('Normal first-line chars', got('Normal', ['pPr', 'ind'], 'firstLineChars'), '200'),
        ('BodyText before', got('BodyText', ['pPr', 'spacing'], 'before'), '0'),
        ('BodyText after', got('BodyText', ['pPr', 'spacing'], 'after'), '0'),
        ('BodyText first-line chars', got('BodyText', ['pPr', 'ind'], 'firstLineChars'), '200'),
        ('FirstParagraph before', got('FirstParagraph', ['pPr', 'spacing'], 'before'), '0'),
        ('FirstParagraph after', got('FirstParagraph', ['pPr', 'spacing'], 'after'), '0'),
        ('FirstParagraph first-line chars', got('FirstParagraph', ['pPr', 'ind'], 'firstLineChars'), '200'),
        ('Chapter(H2) cn', got('Heading2', ['rPr', 'rFonts'], 'eastAsia'), cfg['chapter_cn']),
        ('Chapter(H2) latin', got('Heading2', ['rPr', 'rFonts'], 'ascii'), cfg['body_latin']),
        ('Section(H3) latin', got('Heading3', ['rPr', 'rFonts'], 'ascii'), cfg['body_latin']),
        ('H1 black', got('Heading1', ['rPr', 'color'], 'val'), '000000'),
        ('H2 black', got('Heading2', ['rPr', 'color'], 'val'), '000000'),
        ('H3 black', got('Heading3', ['rPr', 'color'], 'val'), '000000'),
        ('H4 black', got('Heading4', ['rPr', 'color'], 'val'), '000000'),
        ('H5 black', got('Heading5', ['rPr', 'color'], 'val'), '000000'),
        ('H6 black', got('Heading6', ['rPr', 'color'], 'val'), '000000'),
        ('H7 black', got('Heading7', ['rPr', 'color'], 'val'), '000000'),
        ('H8 black', got('Heading8', ['rPr', 'color'], 'val'), '000000'),
        ('H9 black', got('Heading9', ['rPr', 'color'], 'val'), '000000'),
        ('Caption latin', got('ImageCaption', ['rPr', 'rFonts'], 'ascii'), cfg['body_latin']),
        ('Chapter(H2) sz', got('Heading2', ['rPr', 'sz'], 'val'), half(cfg['chapter_pt'])),
        ('Section(H3) sz', got('Heading3', ['rPr', 'sz'], 'val'), half(cfg['section_pt'])),
        ('Section(H3) bold', got('Heading3', ['rPr', 'b'], 'val'),
         None if cfg['section_bold'] else '0'),
        ('Caption sz', got('ImageCaption', ['rPr', 'sz'], 'val'), half(cfg['caption_pt'])),
        ('Caption single line', got('ImageCaption', ['pPr', 'spacing'], 'line'), line_of(1.0)),
        ('Caption before', got('ImageCaption', ['pPr', 'spacing'], 'before'), '0'),
        ('Caption after', got('ImageCaption', ['pPr', 'spacing'], 'after'), '0'),
        ('Caption no indent', got('ImageCaption', ['pPr', 'ind'], 'firstLineChars'), '0'),
        ('Caption center', got('ImageCaption', ['pPr', 'jc'], 'val'),
         'center' if cfg['caption_center'] else None),
        ('TableCaption single line', got('TableCaption', ['pPr', 'spacing'], 'line'), line_of(1.0)),
        ('TableCaption before', got('TableCaption', ['pPr', 'spacing'], 'before'), '0'),
        ('TableCaption after', got('TableCaption', ['pPr', 'spacing'], 'after'), '0'),
        ('TableCaption no indent', got('TableCaption', ['pPr', 'ind'], 'firstLineChars'), '0'),
        ('Compact before', got('Compact', ['pPr', 'spacing'], 'before'), '0'),
        ('Compact after', got('Compact', ['pPr', 'spacing'], 'after'), '0'),
        ('Compact no indent', got('Compact', ['pPr', 'ind'], 'firstLineChars'), '0'),
        ('Table top rule', got('Table', ['tblPr', 'tblBorders', 'top'], 'sz'), '12'),
        ('Table bottom rule', got('Table', ['tblPr', 'tblBorders', 'bottom'], 'sz'), '12'),
        ('Table no left rule', got('Table', ['tblPr', 'tblBorders', 'left'], 'val'), 'nil'),
        ('Table no right rule', got('Table', ['tblPr', 'tblBorders', 'right'], 'val'), 'nil'),
        ('Table no inner row rule', got('Table', ['tblPr', 'tblBorders', 'insideH'], 'val'), 'nil'),
        ('Table no vertical rule', got('Table', ['tblPr', 'tblBorders', 'insideV'], 'val'), 'nil'),
        ('Table AutoFit', got('Table', ['tblPr', 'tblLayout'], 'type'), 'autofit'),
        ('Table header rule', got_first_row(['tcPr', 'tcBorders', 'bottom'], 'sz'), '6'),
    ]
    fails = []
    for n, g, w in checks:
        ok = (g == w)
        print('  [%s] %-18s got=%r want=%r' % ('OK ' if ok else 'FAIL', n, g, w))
        if not ok: fails.append(n)
    return fails

DEFAULTS = dict(body_cn='宋体', body_latin='Times New Roman', body_pt=12.0, line=1.5,
                chapter_cn='黑体', chapter_pt=16.0, chapter_bold=True,
                section_cn='黑体', section_pt=14.0, section_bold=False,
                caption_pt=12.0, caption_center=True)

def selftest():
    import tempfile
    fails = 0
    for name, over in (('default spec', {}),
                       ('custom spec', {'body_pt': 10.5, 'line': 2.0, 'section_bold': True,
                                        'caption_center': False, 'chapter_pt': 22})):
        cfg = dict(DEFAULTS); cfg.update(over)
        out = os.path.join(tempfile.gettempdir(), 'ref_selftest.docx')
        build(cfg, out)
        print('-- %s --' % name)
        fails += len(verify(out, cfg))
        os.remove(out)
    print('=== selftest:', 'ALL PASSED' if fails == 0 else '%d FAILED' % fails, '===')
    return 0 if fails == 0 else 1

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=DEFAULT_OUT)
    ap.add_argument('--body-cn', default=DEFAULTS['body_cn'])
    ap.add_argument('--body-latin', default=DEFAULTS['body_latin'])
    ap.add_argument('--body-pt', type=float, default=DEFAULTS['body_pt'])
    ap.add_argument('--line', type=float, default=DEFAULTS['line'],
                    help='line spacing multiple, e.g. 1.5')
    ap.add_argument('--chapter-cn', default=DEFAULTS['chapter_cn'])
    ap.add_argument('--chapter-pt', type=float, default=DEFAULTS['chapter_pt'])
    ap.add_argument('--chapter-bold', action=argparse.BooleanOptionalAction,
                    default=DEFAULTS['chapter_bold'])
    ap.add_argument('--section-cn', default=DEFAULTS['section_cn'])
    ap.add_argument('--section-pt', type=float, default=DEFAULTS['section_pt'])
    ap.add_argument('--section-bold', action=argparse.BooleanOptionalAction,
                    default=DEFAULTS['section_bold'])
    ap.add_argument('--caption-pt', type=float, default=DEFAULTS['caption_pt'])
    ap.add_argument('--caption-center', action=argparse.BooleanOptionalAction,
                    default=DEFAULTS['caption_center'])
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    cfg = dict(body_cn=a.body_cn, body_latin=a.body_latin, body_pt=a.body_pt, line=a.line,
               chapter_cn=a.chapter_cn, chapter_pt=a.chapter_pt, chapter_bold=a.chapter_bold,
               section_cn=a.section_cn, section_pt=a.section_pt, section_bold=a.section_bold,
               caption_pt=a.caption_pt, caption_center=a.caption_center)
    n = build(cfg, a.out)
    print('written:', a.out, n, 'bytes')
    sys.exit(1 if verify(a.out, cfg) else 0)

if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    main()
