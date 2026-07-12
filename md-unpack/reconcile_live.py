# -*- coding: utf-8 -*-
"""
md-unpack LIVE citekey reconciliation -- resolve provisional authorYear keys to the REAL
Better BibTeX citekeys by asking a RUNNING Zotero (no manual export needed).

Needs: Zotero open + Better BibTeX installed. Reads build/citemap.tsv (provisional_citekey ->
zotero_item_key, produced by md-unpack), asks BBT JSON-RPC `item.citationkey(["<libraryID>:<itemKey>"])`
for each real key, then rewrites manuscript.md in place (backup -> manuscript_provisional.md).
Only keys BBT actually resolves are replaced; the rest stay provisional and are reported.

Usage:
  py reconcile_live.py --citemap build/citemap.tsv --manuscript manuscript.md [--library-id <GROUP_LIB_ID>] [--endpoint URL]

--library-id : OPTIONAL. By default BARE item keys are sent, which BBT resolves against "My Library"
               (the common case) -- no id needed. Only pass this if your refs live in a GROUP library
               and bare keys don't resolve; give that group's local libraryID.

Safety: if Zotero/BBT is unreachable it exits NONZERO WITHOUT touching manuscript.md. For the
offline path (Zotero closed) use reconcile_citekeys.py with a Better-CSL-JSON export instead.
"""
import json, re, io, sys, os, csv, argparse, urllib.request
from _recon import rewrite_citekeys, sync_citemap   # shared rewrite + citemap-sync (DRY, see _recon.py)

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

ap = argparse.ArgumentParser()
ap.add_argument('--citemap', required=True)
ap.add_argument('--manuscript', required=True)
ap.add_argument('--library-id', type=int, default=None,
                help='optional BBT local libraryID prefix for GROUP libraries; '
                     'default = bare item keys (resolve in My Library)')
ap.add_argument('--endpoint', default='http://127.0.0.1:23119/better-bibtex/json-rpc')
a = ap.parse_args()

# --- provisional citekey -> Zotero item key, from citemap.tsv ---
prov_to_item = {}
with open(a.citemap, encoding='utf-8', newline='') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        prov = (row.get('provisional_citekey') or '').strip()
        item = (row.get('zotero_item_key') or '').strip()
        if prov and item and prov != 'PARSE_ERROR':
            prov_to_item[prov] = item
if not prov_to_item:
    print('no (provisional -> item key) pairs in', a.citemap, '-- nothing to reconcile.')
    sys.exit(0)

# --- ask Better BibTeX for the real citekeys (bypass any system proxy; localhost gets 502'd by Clash/TUN) ---
def ref_of(k):
    return ('%d:%s' % (a.library_id, k)) if a.library_id else k   # bare key by default (My Library)
refs = sorted({ref_of(k) for k in prov_to_item.values()})
payload = json.dumps({'jsonrpc': '2.0', 'method': 'item.citationkey',
                      'params': [refs], 'id': 1}).encode('utf-8')
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
req = urllib.request.Request(a.endpoint, data=payload,
                            headers={'Content-Type': 'application/json'})
try:
    with opener.open(req, timeout=8) as r:
        resp = json.loads(r.read().decode('utf-8'))
except Exception as e:
    print('ERROR: cannot reach Better BibTeX at', a.endpoint, '->', e)
    print('  Is Zotero running with Better BibTeX? A proxy (Clash/TUN) may be eating localhost.')
    print('  Offline alternative: export Better-CSL-JSON and run reconcile_citekeys.py.')
    sys.exit(2)
if isinstance(resp, dict) and resp.get('error'):
    print('ERROR: Better BibTeX returned an error:', resp['error'])
    sys.exit(2)
result = (resp or {}).get('result') or {}

# result maps "<libraryID>:<itemKey>" (or sometimes bare itemKey) -> citekey; shapes vary by BBT version
def real_for(itemkey):
    for k in (ref_of(itemkey), itemkey):
        if k in result:
            v = result[k]
            if isinstance(v, dict):
                v = v.get('citationKey') or v.get('citekey') or v.get('citation-key')
            return v if isinstance(v, str) and v else None
    return None

prov_to_real, unmatched = {}, []
for prov, item in prov_to_item.items():
    rk = real_for(item)
    if rk:
        prov_to_real[prov] = rk
    else:
        unmatched.append((prov, item))

# --- rewrite manuscript.md (longest provisional first so prefixes don't clobber) ---
# Read the CURRENT manuscript (preserve any later edits); replacements are idempotent -- an
# already-real key won't match a provisional pattern. Back up the original provisional ONCE.
prov_backup = a.manuscript.replace('.md', '_provisional.md')
md = open(a.manuscript, encoding='utf-8').read()
new_md = rewrite_citekeys(md, prov_to_real)   # longest-first + lookahead + prov==real skip (see _recon.py)
if not os.path.exists(prov_backup):
    open(prov_backup, 'w', encoding='utf-8').write(md)
open(a.manuscript, 'w', encoding='utf-8').write(new_md)

# keep build/citemap.tsv in sync: -Mode rebuild matches the offline citemap by these provisional
# keys, so if manuscript.md moves to real keys but the citemap stays provisional, rebuild would
# silently match nothing. Rewrite the provisional_citekey column for every key we reconciled.
try:
    cchg, cm_bak = sync_citemap(a.citemap, prov_to_real)
    if cchg is not None:
        print('citemap.tsv: %d provisional key(s) synced to real (backup: %s)' % (cchg, cm_bak))
except Exception as e:
    print('WARN: could not sync citemap.tsv; after reconcile use -Mode live (not rebuild):', e)

print('matched %d / %d provisional keys via live Better BibTeX.' % (len(prov_to_real), len(prov_to_item)))
if unmatched:
    print('UNMATCHED %d (left provisional). For group libs pass the right --library-id, '
          'or fall back to reconcile_citekeys.py:' % len(unmatched))
    for p, i in unmatched:
        print('  ', p, '(item', i + ')')
