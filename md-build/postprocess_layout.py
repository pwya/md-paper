# -*- coding: utf-8 -*-
"""postprocess_layout.py -- md-build post-build layout pass on the produced .docx.

Adds a real blank paragraph before/after figure and table blocks and removes the
first-line indent from recognized note paragraphs, so the published layout is:

    blank line -> caption -> figure/table -> note -> blank line

WHY: pandoc emits a figure as one drawing paragraph + an ImageCaption paragraph, and
a table as a TableCaption paragraph + w:tbl + (optional) note paragraph that reuses
the BodyText style (which carries a first-line indent). The author wants a real blank
line around each block and notes WITHOUT the indent.

Safety (md-* rules):
  * Only touches the BUILD OUTPUT docx -- never manuscript.md (single-writer rule
    stays intact).
  * Parses word/document.xml with ElementTree (AST over regex, rule 3).
  * Inserts only empty paragraphs; the document stays well-formed (build.ps1 re-runs
    its XML well-formedness gate afterwards).
  * Note recognition uses an ALLOWLIST of leading words + separator check, so a plain
    sentence like "Note that ..." is never treated as a note.
  * --selftest covers the recognition edge cases (rule 4: one bug = one test).

Usage:
  py postprocess_layout.py --docx out.docx
  py postprocess_layout.py --selftest
"""
import argparse
import io
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def q(tag):
    return '{%s}%s' % (W, tag)


P = q('p')
PPR = q('pPr')
PSTYLE = q('pStyle')
SPACING = q('spacing')
IND = q('ind')
FIRST_LINE = q('firstLine')
FIRST_LINE_CHARS = q('firstLineChars')
HANGING = q('hanging')
HANGING_CHARS = q('hangingChars')
TBL = q('tbl')
T = q('t')
DRAWING = q('drawing')
PICT = q('pict')
JC = q('jc')
VML = 'urn:schemas-microsoft-com:vml'
IMAGEDATA = '{%s}imagedata' % VML

CAPTION_FIG = 'ImageCaption'
CAPTION_TBL = 'TableCaption'

# Allowlist of note-leading words (case-insensitive). English notes must be followed
# by a colon/period so prose like "Note that ..." is never treated as a note; Chinese
# notes additionally allow a space (常见 "资料来源 某处").
EN_NOTE_LEADS = (
    'note', 'notes', 'source', 'sources', 'data source', 'data sources',
    'figure note', 'figure notes', 'fig. note', 'fig. notes', 'fig note', 'fig notes',
    'table note', 'table notes',
)
ZH_NOTE_LEADS = ('注', '注释', '资料来源', '数据来源', '来源', '说明', '图注', '表注')
EN_NOTE_SEP = (':', '：', '.', '．')
ZH_NOTE_SEP = (':', '：', '.', '．', ' ', '\t')


def _text(el):
    return ''.join(t.text or '' for t in el.iter(q('t')))


def _style_id(p):
    ppr = p.find(PPR)
    if ppr is None:
        return None
    el = ppr.find(PSTYLE)
    return None if el is None else el.get(q('val'))


def is_note(p):
    """True when p is a recognized note paragraph (allowlist lead + separator)."""
    if p.tag != P:
        return False
    text = _text(p).strip()
    if not text:
        return False
    low = text.lower()
    for lead in EN_NOTE_LEADS:
        if not low.startswith(lead):
            continue
        rest = low[len(lead):]
        if not rest or rest[0] in EN_NOTE_SEP:
            return True
    for lead in ZH_NOTE_LEADS:
        if not low.startswith(lead):
            continue
        rest = low[len(lead):]
        if not rest or rest[0] in ZH_NOTE_SEP:
            return True
    return False


def is_standalone_image(p):
    """True when p is a standalone image paragraph (has a drawing and no text runs)."""
    if p.tag != P:
        return False
    if _text(p).strip():
        return False
    # find() only looks at direct children; the drawing lives inside w:r, so search deep.
    if p.find('.//' + DRAWING) is not None:
        return True
    # w:pict with v:imagedata = a real picture. w:pict with v:rect + o:hr is pandoc's
    # horizontal rule (---), which must NOT be treated as a figure.
    pict = p.find('.//' + PICT)
    return pict is not None and pict.find('.//' + IMAGEDATA) is not None


def blank_paragraph():
    """A real, empty paragraph: no text, no indent, single line spacing."""
    p = ET.Element(P)
    ppr = ET.SubElement(p, PPR)
    sp = ET.SubElement(ppr, SPACING)
    sp.set(q('after'), '0')
    sp.set(q('before'), '0')
    sp.set(q('line'), '240')
    sp.set(q('lineRule'), 'auto')
    ind = ET.SubElement(ppr, IND)
    ind.set(FIRST_LINE, '0')
    ind.set(FIRST_LINE_CHARS, '0')
    return p


def _ind_insert_pos(ppr):
    """Insertion index for w:ind that keeps pPr schema order (before w:jc)."""
    pos = len(list(ppr))
    for i, child in enumerate(list(ppr)):
        if child.tag == JC:
            pos = i
            break
    return pos


def clear_first_line_indent(p):
    """Remove the first-line indent from paragraph p (override at paragraph level)."""
    ppr = p.find(PPR)
    if ppr is None:
        ppr = ET.Element(PPR)
        p.insert(0, ppr)
    ind = ppr.find(IND)
    if ind is None:
        ind = ET.Element(IND)
        ppr.insert(_ind_insert_pos(ppr), ind)
    ind.set(FIRST_LINE, '0')
    ind.set(FIRST_LINE_CHARS, '0')
    ind.attrib.pop(HANGING, None)
    ind.attrib.pop(HANGING_CHARS, None)


def _prev_para(children, start):
    """Nearest paragraph at or before start-1, skipping non-paragraph siblings
    (bookmarkStart/bookmarkEnd etc. that pandoc emits between table and note)."""
    j = start - 1
    while j >= 0:
        if children[j].tag == P:
            return j, children[j]
        j -= 1
    return None, None


def _next_para(children, start):
    """Nearest paragraph after start, skipping non-paragraph siblings."""
    j = start + 1
    while j < len(children):
        if children[j].tag == P:
            return j, children[j]
        j += 1
    return None, None


def process(body):
    """Insert blank lines and fix note indents inside a w:body element, in place.

    Returns (blank_lines_added, notes_cleared) so build.ps1 can report what changed.
    """
    children = list(body)
    n = len(children)
    ops = []  # ('insert_before', index, element)  |  ('clear_indent', paragraph)
    i = 0
    while i < n:
        el = children[i]
        if el.tag == TBL:
            # Table block: optional TableCaption paragraph just before, optional note just after.
            cap_idx, cap = _prev_para(children, i)
            has_cap = cap is not None and _style_id(cap) == CAPTION_TBL
            note_idx, note = _next_para(children, i)
            has_note = note is not None and is_note(note)
            if has_cap:
                ops.append(('insert_before', cap_idx, blank_paragraph()))
            else:
                ops.append(('insert_before', i, blank_paragraph()))
            if has_note:
                ops.append(('clear_indent', note))
                ops.append(('insert_before', note_idx + 1, blank_paragraph()))
            else:
                ops.append(('insert_before', i + 1, blank_paragraph()))
            i += 1
            continue
        if el.tag == P and is_standalone_image(el):
            # Figure block: drawing paragraph, then optional ImageCaption, then optional note.
            cap_idx, cap = _next_para(children, i)
            has_cap = cap is not None and _style_id(cap) == CAPTION_FIG
            search_from = cap_idx if has_cap else i
            note_idx, note = _next_para(children, search_from)
            has_note = note is not None and is_note(note)
            ops.append(('insert_before', i, blank_paragraph()))
            if has_note:
                ops.append(('clear_indent', note))
                ops.append(('insert_before', note_idx + 1, blank_paragraph()))
            else:
                ops.append(('insert_before', (cap_idx if has_cap else i) + 1, blank_paragraph()))
            i += 1
            continue
        i += 1

    # Apply from the END so earlier indices stay valid.
    blanks = 0
    for kind, idx_or_p, *rest in reversed(ops):
        if kind == 'insert_before':
            body.insert(idx_or_p, rest[0])
            blanks += 1
        else:
            clear_first_line_indent(idx_or_p)
    return blanks, sum(1 for op in ops if op[0] == 'clear_indent')


def _register_namespaces(xml_text):
    """Re-register every xmlns:* prefix from the source XML so ElementTree serializes
    with the ORIGINAL prefixes (w:, pic:, ...) instead of ns0:/ns1: -- downstream
    checks (verify_conservation) match document.xml by those literal prefixes."""
    for m in re.finditer(r'xmlns:([A-Za-z0-9_.-]+)="([^"]+)"', xml_text):
        if not re.match(r'ns\d+$', m.group(1)):   # ns0.. are ElementTree internal
            ET.register_namespace(m.group(1), m.group(2))
    m = re.search(r'xmlns="([^"]+)"', xml_text)
    if m:
        ET.register_namespace('', m.group(1))


def run(docx_path):
    """Rewrite docx_path's word/document.xml in place. Returns (blanks, notes)."""
    with zipfile.ZipFile(docx_path, 'r') as zin:
        names = zin.namelist()
        data = {name: zin.read(name) for name in names}
    xml_text = data['word/document.xml'].decode('utf-8')
    _register_namespaces(xml_text)
    root = ET.fromstring(xml_text)
    body = root.find(q('body'))
    if body is None:
        raise RuntimeError('word/document.xml has no w:body')
    blanks, notes = process(body)
    body_xml = ET.tostring(root, encoding='unicode')
    data['word/document.xml'] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + body_xml
    ).encode('utf-8')
    with zipfile.ZipFile(docx_path, 'w', zipfile.ZIP_DEFLATED) as zout:
        for name in names:
            zout.writestr(name, data[name])
    return blanks, notes


def _mk_doc(children_xml):
    """Build a minimal document.xml around the given body children (for tests)."""
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="%s"><w:body>%s</w:body></w:document>' % (W, children_xml)
    )
    return ET.fromstring(xml)


def _para(style=None, text=None, drawing=False):
    parts = ['<w:p>']
    if style is not None:
        parts.append('<w:pPr><w:pStyle w:val="%s"/></w:pPr>' % style)
    if drawing:
        parts.append('<w:r><w:drawing><w:inline/></w:drawing></w:r>')
    if text:
        parts.append('<w:r><w:t>%s</w:t></w:r>' % text)
    parts.append('</w:p>')
    return ''.join(parts)


def _tbl():
    return ('<w:tbl><w:tr><w:tc><w:p/></w:tc></w:tr></w:tbl>')


def _selftest():
    import tempfile

    def run_case(name, children_xml, want_blanks, want_notes, checks):
        doc = _mk_doc(children_xml)
        body = doc.find(q('body'))
        blanks, notes = process(body)
        ok = blanks == want_blanks and notes == want_notes
        for ck in checks:
            if not ck(body):
                ok = False
        print('  [%s] %-38s blanks=%d/%d notes=%d/%d' % (
            'OK ' if ok else 'FAIL', name, blanks, want_blanks, notes, want_notes))
        return ok

    def body_paras(body):
        return [c for c in list(body) if c.tag == P]

    def body_text(body):
        return ''.join(_text(p) for p in body_paras(body))

    fails = 0

    # 1. Figure + caption + note -> blank before, blank after note, note not indented.
    def _note_no_indent(b):
        # After processing: blank(0) image(1) caption(2) note(3) blank(4) next(5).
        note = b[3]
        ppr = note.find(PPR)
        if ppr is None:
            return False
        ind = ppr.find(IND)
        return ind is not None and ind.get(FIRST_LINE_CHARS) == '0'

    if not run_case(
        'figure-with-note',
        _para(drawing=True) + _para('ImageCaption', 'Figure 1: cap') +
        _para('BodyText', 'Note: something') + _para('BodyText', 'next para'),
        2, 1,
        [lambda b: body_text(b) == 'Figure 1: capNote: somethingnext para', _note_no_indent],
    ):
        fails += 1
    # 2. Figure + caption, no note -> blank before + after caption.
    if not run_case(
        'figure-no-note',
        _para(drawing=True) + _para('ImageCaption', 'Figure 1: cap') + _para('BodyText', 'next'),
        2, 0, [],
    ):
        fails += 1
    # 1b. Figure + bookmark between blocks + note.
    if not run_case(
        'figure-with-bookmark-note',
        _para(drawing=True) + '<w:bookmarkEnd w:id="2"/>' +
        _para('ImageCaption', 'Figure 1: cap') + '<w:bookmarkEnd w:id="3"/>' +
        _para('BodyText', 'Note: x'),
        2, 1, [],
    ):
        fails += 1
    # 3. Table + caption + note -> blank before caption, blank after note.
    if not run_case(
        'table-with-note',
        _para('TableCaption', 'Table 1: cap') + _tbl() + _para('BodyText', 'Source: data'),
        2, 1, [],
    ):
        fails += 1
    # 3b. Table + bookmarkEnd before the note (real pandoc output shape).
    if not run_case(
        'table-with-bookmark-note',
        _para('TableCaption', 'Table 1: cap') + _tbl() + '<w:bookmarkEnd w:id="1"/>' +
        _para('BodyText', 'Source: data'),
        2, 1, [],
    ):
        fails += 1
    # 4. Table + caption, no note -> blank before caption + after table.
    if not run_case(
        'table-no-note',
        _para('TableCaption', 'Table 1: cap') + _tbl() + _para('BodyText', 'next'),
        2, 0, [],
    ):
        fails += 1
    # 5. Inline image inside a text paragraph is NOT a standalone figure.
    if not run_case(
        'inline-image-not-figure',
        _para('BodyText', 'text <w:r><w:drawing/></w:r> here'),
        0, 0, [],
    ):
        fails += 1
    # 5b. pandoc's horizontal rule (--- -> v:rect + o:hr) is NOT a figure.
    hr = ('<w:p><w:r><w:pict xmlns:v="urn:schemas-microsoft-com:vml" '
          'xmlns:o="urn:schemas-microsoft-com:office:office">'
          '<v:rect style="width:0;height:1.5pt" o:hr="t"/></w:pict></w:r></w:p>')
    if not run_case(
        'horizontal-rule-not-figure',
        hr + _para('BodyText', 'next'),
        0, 0, [],
    ):
        fails += 1
    # 6. "Note that ..." is NOT a note.
    if not run_case(
        'note-that-not-a-note',
        _para('TableCaption', 'Table 1: cap') + _tbl() + _para('BodyText', 'Note that results'),
        2, 0, [],
    ):
        fails += 1
    # 7. Chinese note lead.
    if not run_case(
        'chinese-note',
        _para('TableCaption', 'Table 1: cap') + _tbl() + _para('BodyText', '资料来源：某处'),
        2, 1, [],
    ):
        fails += 1
    # 8. Case-insensitive "DATA SOURCE:".
    if not run_case(
        'uppercase-source',
        _para('TableCaption', 'Table 1: cap') + _tbl() + _para('BodyText', 'DATA SOURCE: x'),
        2, 1, [],
    ):
        fails += 1
    # 9. Standalone image WITHOUT caption (rare) still gets blanks.
    if not run_case(
        'image-no-caption',
        _para(drawing=True) + _para('BodyText', 'next'),
        2, 0, [],
    ):
        fails += 1
    # 10. No figures/tables -> no changes.
    if not run_case(
        'plain-doc',
        _para('BodyText', 'hello') + _para('BodyText', 'world'),
        0, 0, [],
    ):
        fails += 1

    # End-to-end: run() on a real docx round-trips the zip.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='md_layout_') as td:
        docx = os.path.join(td, 'out.docx')
        with zipfile.ZipFile(docx, 'w', zipfile.ZIP_DEFLATED) as zout:
            zout.writestr('word/document.xml', (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<w:document xmlns:w="%s"><w:body>%s</w:body></w:document>'
                % (W, _para(drawing=True) + _para('ImageCaption', 'Figure 1: cap') +
                   _para('BodyText', 'Note: x'))
            ).encode('utf-8'))
        blanks, notes = run(docx)
        with zipfile.ZipFile(docx, 'r') as zin:
            roundtrip_xml = zin.read('word/document.xml').decode('utf-8')
            root = ET.fromstring(roundtrip_xml)
        para_count = len(root.findall('.//' + P))
        ok = (blanks == 2 and notes == 1 and para_count >= 5
              and '<w:p' in roundtrip_xml          # namespace prefix preserved
              and 'standalone="yes"' in roundtrip_xml)
        print('  [%s] %-38s blanks=%d notes=%d' % ('OK ' if ok else 'FAIL', 'zip-roundtrip', blanks, notes))
        if not ok:
            fails += 1

    print('=== postprocess_layout selftest:', 'ALL PASSED' if fails == 0 else '%d FAILED' % fails, '===')
    return 0 if fails == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--docx')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        sys.exit(_selftest())
    if not a.docx:
        ap.error('--docx is required (or use --selftest)')
    if not os.path.exists(a.docx):
        ap.error('docx not found: ' + a.docx)
    blanks, notes = run(a.docx)
    print('[layout] blank lines added: %d | notes de-indented: %d -> %s' % (blanks, notes, a.docx))
    sys.exit(0)


if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    main()
