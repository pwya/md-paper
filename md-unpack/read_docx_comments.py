# -*- coding: utf-8 -*-
"""
read_docx_comments.py -- extract Word comments (批注) from a .docx as revision intent.

A .docx is a zip. Comment bodies live in word/comments.xml; the text each comment is ANCHORED to
is delimited in word/document.xml by <w:commentRangeStart/End w:id="N"/> around the runs. We pair
them by id so each comment carries both the reviewer's note AND the original sentence it points at.

Pure stdlib (zipfile + xml.etree) -- NO Word COM, so it is cross-platform and cheap. This is the
SHARED extractor used by BOTH md-unpack (harvests comments as a side-artifact when the source docx
already contains them) and md-triage (extracts on demand from a later, separately-annotated docx).
The intelligence -- turning these raw comments into a discrete revision checklist -- is md-triage's
job; this script only does the mechanical parse.

Output: a JSON list, one object per comment:
  {"id","author","date","comment","anchor"}   (anchor = the original text the comment is attached to)

Usage:
  py read_docx_comments.py --docx paper.docx [--out swarm/comments_raw.json] [--print]
  py read_docx_comments.py --selftest
Exit: 0 ok (also when there are 0 comments); 2 = docx not found / not a zip.
"""
import argparse
import io
import json
import os
import sys
import zipfile
import xml.etree.ElementTree as ET

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def _local(tag):
    return tag.split('}')[-1]


def _parse_comments_xml(data):
    """word/comments.xml bytes -> {id: {author, date, comment}} (order preserved via dict insertion)."""
    out = {}
    if not data:
        return out
    root = ET.fromstring(data)
    for cel in root.iter(W + 'comment'):
        cid = cel.get(W + 'id')
        if cid is None:
            continue
        paras = cel.findall('.//' + W + 'p')
        if paras:
            text = '\n'.join(''.join(t.text or '' for t in p.iter(W + 't')) for p in paras)
        else:
            text = ''.join(t.text or '' for t in cel.iter(W + 't'))
        out[cid] = {
            'author': cel.get(W + 'author') or '',
            'date': cel.get(W + 'date') or '',
            'comment': text.strip(),
        }
    return out


def _parse_anchors(doc_data):
    """word/document.xml bytes -> {id: anchored_text}.
    Walks the body in document order; a <w:t> is appended to every comment range currently open
    (between its commentRangeStart and commentRangeEnd)."""
    anchors = {}
    if not doc_data:
        return anchors
    root = ET.fromstring(doc_data)
    open_ids = set()
    for el in root.iter():
        tag = _local(el.tag)
        if tag == 'commentRangeStart':
            cid = el.get(W + 'id')
            if cid is not None:
                open_ids.add(cid)
                anchors.setdefault(cid, [])
        elif tag == 'commentRangeEnd':
            cid = el.get(W + 'id')
            open_ids.discard(cid)
        elif tag == 't' and open_ids:
            txt = el.text or ''
            for cid in open_ids:
                anchors[cid].append(txt)
    return {cid: ''.join(parts).strip() for cid, parts in anchors.items()}


def extract_comments(docx_path):
    """Open the .docx zip, parse comment bodies + anchors, return a list of dicts (id order)."""
    with zipfile.ZipFile(docx_path) as z:
        names = set(z.namelist())
        comments_data = z.read('word/comments.xml') if 'word/comments.xml' in names else b''
        doc_data = z.read('word/document.xml') if 'word/document.xml' in names else b''
    bodies = _parse_comments_xml(comments_data)
    anchors = _parse_anchors(doc_data)
    result = []
    for cid, c in bodies.items():
        result.append({
            'id': cid,
            'author': c['author'],
            'date': c['date'],
            'comment': c['comment'],
            'anchor': anchors.get(cid, ''),
        })
    return result


# ---------------------------------------------------------------------------
def _make_test_docx(path, comments_xml, document_xml):
    with zipfile.ZipFile(path, 'w') as z:
        if comments_xml is not None:
            z.writestr('word/comments.xml', comments_xml)
        z.writestr('word/document.xml', document_xml)


def _selftest():
    import tempfile
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    comments_xml = (
        '<?xml version="1.0"?>'
        '<w:comments %s>'
        '<w:comment w:id="0" w:author="Advisor" w:date="2026-06-27T10:00:00Z">'
        '<w:p><w:r><w:t>Please clarify the identification strategy.</w:t></w:r></w:p>'
        '</w:comment>'
        '<w:comment w:id="1" w:author="Reviewer 2" w:date="2026-06-26T09:00:00Z">'
        '<w:p><w:r><w:t>Add a robustness </w:t></w:r><w:r><w:t>check.</w:t></w:r></w:p>'
        '</w:comment>'
        '</w:comments>'
    ) % ns
    document_xml = (
        '<?xml version="1.0"?>'
        '<w:document %s><w:body>'
        '<w:p>'
        '<w:commentRangeStart w:id="0"/>'
        '<w:r><w:t>We use an IV </w:t></w:r><w:r><w:t>approach.</w:t></w:r>'
        '<w:commentRangeEnd w:id="0"/><w:r><w:commentReference w:id="0"/></w:r>'
        '</w:p>'
        '<w:p><w:r><w:t>Some unrelated body text.</w:t></w:r></w:p>'
        '<w:p>'
        '<w:commentRangeStart w:id="1"/>'
        '<w:r><w:t>Table 3 reports the main results.</w:t></w:r>'
        '<w:commentRangeEnd w:id="1"/><w:r><w:commentReference w:id="1"/></w:r>'
        '</w:p>'
        '</w:body></w:document>'
    ) % ns

    d = tempfile.mkdtemp()
    p = os.path.join(d, 'with_comments.docx')
    _make_test_docx(p, comments_xml, document_xml)
    got = extract_comments(p)
    assert len(got) == 2, got
    assert got[0]['author'] == 'Advisor', got[0]
    assert got[0]['comment'] == 'Please clarify the identification strategy.', got[0]
    assert got[0]['anchor'] == 'We use an IV approach.', got[0]  # runs joined
    assert got[1]['author'] == 'Reviewer 2', got[1]
    assert got[1]['comment'] == 'Add a robustness check.', got[1]  # split runs joined
    assert got[1]['anchor'] == 'Table 3 reports the main results.', got[1]

    # no comments.xml at all -> empty list, no crash
    p2 = os.path.join(d, 'no_comments.docx')
    _make_test_docx(p2, None, '<?xml version="1.0"?><w:document %s><w:body>'
                              '<w:p><w:r><w:t>Clean draft.</w:t></w:r></w:p></w:body></w:document>' % ns)
    assert extract_comments(p2) == [], 'no-comments docx should yield []'

    print('OK read_docx_comments self-test passed')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--docx')
    ap.add_argument('--out', default=None, help='write JSON here; default: <docx-dir>/swarm/comments_raw.json')
    ap.add_argument('--print', dest='show', action='store_true', help='also print the comments to stdout')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()

    if a.selftest:
        _selftest()
        return
    if not a.docx:
        print('FATAL: pass --docx <file> (or --selftest).'); sys.exit(2)
    if not os.path.exists(a.docx):
        print('FATAL: docx not found:', a.docx); sys.exit(2)
    try:
        comments = extract_comments(a.docx)
    except zipfile.BadZipFile:
        print('FATAL: not a valid .docx (zip):', a.docx); sys.exit(2)

    print('=== read_docx_comments ===')
    print('docx    :', a.docx)
    print('comments:', len(comments))
    if not comments:
        print('(no Word comments found -- nothing to harvest.)')
        return

    if a.show:
        for c in comments:
            print('\n  [#%s] %s (%s)' % (c['id'], c['author'] or '?', c['date'] or ''))
            print('   note  :', c['comment'])
            print('   anchor:', c['anchor'] or '(no anchored text)')

    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.docx)), 'swarm', 'comments_raw.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(comments, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n-> wrote', out)


if __name__ == '__main__':
    main()
