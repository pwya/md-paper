# -*- coding: utf-8 -*-
"""
_recon.py -- shared citekey-reconciliation helpers for md-unpack.

Both reconcile_live.py (online, via Better BibTeX JSON-RPC) and reconcile_citekeys.py
(offline, via a CSL-JSON export) end the same way: rewrite every provisional @authorYear
key in manuscript.md to its real BBT key, then sync the provisional_citekey column of
build/citemap.tsv so -Mode rebuild still matches. That ~40-line tail used to be copy-pasted
in both files; the copies had already drifted (one had the prov==real skip, the other didn't).
Per the dev handbook section 6.5 rule (6) DRY: define it once here, import it in both.

No I/O side effects in rewrite_citekeys (pure str->str); sync_citemap touches one file and is
the single source of the "keep citemap in step with the manuscript" rule.
"""
import os
import re
import tempfile


def rewrite_citekeys(md_text, prov_to_real):
    """Return md_text with each @<provisional> rewritten to @<real>.

    - LONGEST provisional first: a key that is a prefix of another (e.g. @li2020 vs
      @li2020a) must not be clobbered before the longer one is matched.
    - Negative lookahead (?![A-Za-z0-9]): never cut into a longer adjacent token, so
      @li2020 does not match inside @li2020a.
    - prov == real is a no-op and skipped (idempotent: re-running after a partial
      reconcile, or feeding already-real keys, changes nothing).
    """
    new_md = md_text
    for prov, real in sorted(prov_to_real.items(), key=lambda kv: -len(kv[0])):
        if prov == real:
            continue
        new_md = re.sub(r'@' + re.escape(prov) + r'(?![A-Za-z0-9])', '@' + real, new_md)
    return new_md


def atomic_write_text(path, text):
    """Write UTF-8/LF text atomically beside *path*, then replace the target."""
    parent = os.path.dirname(os.path.abspath(path)) or '.'
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + '.', suffix='.tmp', dir=parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text.replace('\r\n', '\n').replace('\r', '\n'))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def reconcile_manuscript(manuscript_path, prov_to_real):
    """Reconcile the CURRENT manuscript and preserve its first provisional state once.

    The backup is recovery-only. It must never become the input of a later run, otherwise
    post-reconcile edits can be silently rolled back to stale content.
    Returns (rewritten_text, backup_path).
    """
    with open(manuscript_path, encoding='utf-8', newline='') as f:
        current = f.read().replace('\r\n', '\n').replace('\r', '\n')
    rewritten = rewrite_citekeys(current, prov_to_real)
    backup = manuscript_path.replace('.md', '_provisional.md')
    if not os.path.exists(backup):
        atomic_write_text(backup, current)
    atomic_write_text(manuscript_path, rewritten)
    return rewritten, backup


def sync_citemap(citemap_path, prov_to_real):
    """Rewrite the provisional_citekey column (col 1) of citemap.tsv to the reconciled
    real keys, so -Mode rebuild -- which matches the offline citemap by these keys -- does
    not silently match nothing after the manuscript moves to real keys.

    One-time backup to <citemap>.provbak (never overwritten). Returns (count_changed,
    backup_basename); returns (None, None) when there is no citemap or no recognizable
    header (nothing to do). Raises on real I/O errors so the caller can WARN.
    """
    if not os.path.exists(citemap_path):
        return (None, None)
    cm_lines = open(citemap_path, encoding='utf-8').read().split('\n')
    if not cm_lines or not cm_lines[0].startswith('placeholder'):
        return (None, None)
    bak = citemap_path + '.provbak'
    if not os.path.exists(bak):
        atomic_write_text(bak, '\n'.join(cm_lines))
    changed = 0
    for i in range(1, len(cm_lines)):
        if not cm_lines[i].strip():
            continue
        cols = cm_lines[i].split('\t')
        if len(cols) > 1 and cols[1] in prov_to_real:
            cols[1] = prov_to_real[cols[1]]
            changed += 1
            cm_lines[i] = '\t'.join(cols)
    atomic_write_text(citemap_path, '\n'.join(cm_lines))
    return (changed, os.path.basename(bak))


if __name__ == '__main__':
    # Regression self-test (dev handbook section 6.5 rule (4): one bug = one test).
    # Run:  python _recon.py    ->  prints OK, exits 0; any failure raises AssertionError.
    import io, sys, tempfile
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    # rewrite_citekeys: prefix protection -- @li2020 must not clobber @li2020a
    md = 'see [@li2020] and [@li2020a] and [@zhao2019].'
    out = rewrite_citekeys(md, {'li2020': 'liReal2020', 'li2020a': 'liReal2020a', 'zhao2019': 'zhao2019'})
    assert out == 'see [@liReal2020] and [@liReal2020a] and [@zhao2019].', out
    # the prov==real key (zhao2019) was a no-op; the prefix pair both rewrote correctly

    # rewrite_citekeys: lookahead -- a provisional key embedded as a prefix of a NON-mapped
    # longer token is left alone
    assert rewrite_citekeys('[@li2020bis]', {'li2020': 'X'}) == '[@li2020bis]'

    # P0 regression: rerunning reconciliation must read the current manuscript, never the
    # one-time provisional backup. A later edit therefore survives a second reconciliation.
    d = tempfile.mkdtemp()
    man = os.path.join(d, 'manuscript.md')
    atomic_write_text(man, 'first [@li2020]\n')
    reconcile_manuscript(man, {'li2020': 'liReal2020'})
    atomic_write_text(man, 'later edit [@other2021]\n')
    reconcile_manuscript(man, {'other2021': 'otherReal2021'})
    assert open(man, encoding='utf-8').read() == 'later edit [@otherReal2021]\n'
    assert open(man.replace('.md', '_provisional.md'), encoding='utf-8').read() == 'first [@li2020]\n'

    # sync_citemap: rewrites col 1, leaves a one-time backup, returns the count
    cm = os.path.join(d, 'citemap.tsv')
    open(cm, 'w', encoding='utf-8').write(
        'placeholder\tprovisional_citekey\tzotero_item_key\ttitle\n'
        'CITE-1\tli2020\tABC123\tSome paper\n'
        'CITE-2\tzhao2019\tDEF456\tOther paper\n')
    n, bak = sync_citemap(cm, {'li2020': 'liReal2020'})
    assert n == 1, n
    assert bak == 'citemap.tsv.provbak'
    body = open(cm, encoding='utf-8').read()
    assert 'liReal2020' in body and '\tli2020\t' not in body, body
    assert os.path.exists(cm + '.provbak')

    # sync_citemap: no citemap / no header -> (None, None), no crash
    assert sync_citemap(os.path.join(d, 'nope.tsv'), {}) == (None, None)
    open(cm + '.x', 'w', encoding='utf-8').write('garbage\nno header\n')
    assert sync_citemap(cm + '.x', {'li2020': 'X'}) == (None, None)

    print('OK _recon self-test passed')
