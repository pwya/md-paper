# -*- coding: utf-8 -*-
"""make_reference_cn.py -- generate a style-only reference.docx (样式马甲) for md-build -Reference.

Deterministic ElementTree surgery on pandoc's default reference styles.xml (rule 3: AST over
regex). Parameterized (2026-07-11) so /md-build's format-asking step can build a vest from the
user's answers; all defaults = the author's spec:
  body    SongTi + Times New Roman, 12pt (xiao-4), 1.5 line spacing
  chapter (md '##' -> Word Heading2; Heading1 dressed the same as a fallback)  HeiTi 16pt BOLD
  section (md '###' -> Word Heading3)                                          HeiTi 14pt not bold
  caption (Image/Table/Caption)                                 body font, own pt, 1.5x, centered
  tables  deliberately untouched (author's spec).
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

def patch(style, *, east, latin, sz_half, bold=None, line=None, center=False):
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
    if line is not None:
        sp = ensure(ppr, 'spacing')
        sp.set(q('line'), line); sp.set(q('lineRule'), 'auto')
    if center:
        ensure(ppr, 'jc').set(q('val'), 'center')

def build(cfg, out_path):
    ref = subprocess.run([PANDOC, '--print-default-data-file', 'reference.docx'],
                         capture_output=True)
    assert ref.returncode == 0 and ref.stdout[:2] == b'PK', 'pandoc default reference dump failed'
    zin = zipfile.ZipFile(io.BytesIO(ref.stdout))
    root = ET.fromstring(zin.read('word/styles.xml'))
    styles = {s.get(q('styleId')): s for s in root.findall(q('style'))}
    assert 'Normal' in styles and 'Heading2' in styles and 'Heading3' in styles, 'unexpected reference layout'

    ln = line_of(cfg['line'])
    for i in ('Normal', 'BodyText', 'FirstParagraph'):
        if i in styles:
            patch(styles[i], east=cfg['body_cn'], latin=cfg['body_latin'],
                  sz_half=half(cfg['body_pt']), line=ln)
    for i in ('Heading1', 'Heading2'):   # chapter level (suite: '##' == chapter)
        if i in styles:
            patch(styles[i], east=cfg['chapter_cn'], latin=cfg['body_latin'],
                  sz_half=half(cfg['chapter_pt']), bold=cfg['chapter_bold'], line=ln)
    patch(styles['Heading3'], east=cfg['section_cn'], latin=cfg['body_latin'],
          sz_half=half(cfg['section_pt']), bold=cfg['section_bold'], line=ln)
    for i in ('ImageCaption', 'TableCaption', 'Caption'):
        if i in styles:
            patch(styles[i], east=cfg['body_cn'], latin=cfg['body_latin'],
                  sz_half=half(cfg['caption_pt']), line=ln, center=cfg['caption_center'])

    out_styles = ET.tostring(root, xml_declaration=True, encoding='UTF-8')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = out_styles if item.filename == 'word/styles.xml' else zin.read(item.filename)
            zout.writestr(item, data)
    open(out_path, 'wb').write(buf.getvalue())
    return len(buf.getvalue())

def verify(out_path, cfg):
    """Assert the written vest carries the resolved values. Returns list of failed check names."""
    r = ET.fromstring(zipfile.ZipFile(out_path).read('word/styles.xml'))
    s = {x.get(q('styleId')): x for x in r.findall(q('style'))}
    def got(sid, path, attr):
        el = s[sid].find('/'.join(q(p) for p in path))
        return None if el is None else el.get(q(attr))
    ln = line_of(cfg['line'])
    checks = [
        ('Normal eastAsia', got('Normal', ['rPr', 'rFonts'], 'eastAsia'), cfg['body_cn']),
        ('Normal latin', got('Normal', ['rPr', 'rFonts'], 'ascii'), cfg['body_latin']),
        ('Normal sz', got('Normal', ['rPr', 'sz'], 'val'), half(cfg['body_pt'])),
        ('Normal line', got('Normal', ['pPr', 'spacing'], 'line'), ln),
        ('Chapter(H2) cn', got('Heading2', ['rPr', 'rFonts'], 'eastAsia'), cfg['chapter_cn']),
        ('Chapter(H2) latin', got('Heading2', ['rPr', 'rFonts'], 'ascii'), cfg['body_latin']),
        ('Section(H3) latin', got('Heading3', ['rPr', 'rFonts'], 'ascii'), cfg['body_latin']),
        ('Caption latin', got('ImageCaption', ['rPr', 'rFonts'], 'ascii'), cfg['body_latin']),
        ('Chapter(H2) sz', got('Heading2', ['rPr', 'sz'], 'val'), half(cfg['chapter_pt'])),
        ('Section(H3) sz', got('Heading3', ['rPr', 'sz'], 'val'), half(cfg['section_pt'])),
        ('Section(H3) bold', got('Heading3', ['rPr', 'b'], 'val'),
         None if cfg['section_bold'] else '0'),
        ('Caption sz', got('ImageCaption', ['rPr', 'sz'], 'val'), half(cfg['caption_pt'])),
        ('Caption center', got('ImageCaption', ['pPr', 'jc'], 'val'),
         'center' if cfg['caption_center'] else None),
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
