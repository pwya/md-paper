# -*- coding: utf-8 -*-
"""
md-unpack OFFLINE fallback: map provisional authorYear citekeys -> real Better BibTeX keys,
by matching DOI (exact) > normalized title > author+year, against a BBT CSL-JSON export.
(The PREFERRED path is live resolution at unpack time via BBT JSON-RPC item.citationkey();
 use this only when Zotero wasn't running during unpack.)

Usage:
  python reconcile_citekeys.py --bbt-export library.json --references references.json \
         --manuscript manuscript.md [--manual manual.json]
Rewrites manuscript.md in place (backup -> manuscript_provisional.md), writes build/citekey_*.tsv.
--manual: optional {"provkey":"realkey",...} for items not in the export.
"""
import json, re, io, sys, os, unicodedata, argparse
from _recon import rewrite_citekeys, sync_citemap   # shared rewrite + citemap-sync (DRY, see _recon.py)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
ap = argparse.ArgumentParser()
ap.add_argument('--bbt-export', required=True); ap.add_argument('--references', required=True)
ap.add_argument('--manuscript', required=True); ap.add_argument('--manual', default=None)
a = ap.parse_args()
WD = os.path.dirname(os.path.abspath(a.manuscript)); BD = os.path.join(WD,'build'); os.makedirs(BD, exist_ok=True)

bbt  = json.load(open(a.bbt_export, encoding='utf-8'))
mine = json.load(open(a.references, encoding='utf-8'))
prov_backup = a.manuscript.replace('.md','_provisional.md')
src = prov_backup if os.path.exists(prov_backup) else a.manuscript
md  = open(src, encoding='utf-8').read()

def ndoi(d):
    if not d: return None
    return re.sub(r'^https?://(dx\.)?doi\.org/','',str(d).lower().strip()) or None
def ntitle(t):
    if not t: return None
    return re.sub(r'[^a-z0-9]','', unicodedata.normalize('NFKD',str(t)).encode('ascii','ignore').decode('ascii').lower()) or None
def fam(it):
    au = it.get('author') or it.get('editor') or []
    return ntitle(au[0].get('family') or au[0].get('literal') or au[0].get('name') or '') if au else None
def yr(it):
    try: return str(it.get('issued',{})['date-parts'][0][0])
    except Exception: return None

by_doi, by_title, by_fy = {}, {}, {}
for it in bbt:
    ck = it.get('id') or it.get('citation-key')
    if not ck: continue
    if ndoi(it.get('DOI')): by_doi.setdefault(ndoi(it.get('DOI')), ck)
    if ntitle(it.get('title')): by_title.setdefault(ntitle(it.get('title')), ck)
    if all((fam(it),yr(it))): by_fy.setdefault((fam(it),yr(it)), ck)

prov_to_real, report, unmatched = {}, [], []
for it in mine:
    prov = it.get('id'); real=method=None
    if ndoi(it.get('DOI')) in by_doi: real,method = by_doi[ndoi(it.get('DOI'))],'DOI'
    if not real and ntitle(it.get('title')) in by_title: real,method = by_title[ntitle(it.get('title'))],'title'
    if not real and all((fam(it),yr(it))) and (fam(it),yr(it)) in by_fy: real,method = by_fy[(fam(it),yr(it))],'author+year'
    if real: prov_to_real[prov]=real; report.append((prov,real,method,(it.get('title') or '')[:70]))
    else: unmatched.append((prov, yr(it), (it.get('title') or '')[:70]))

if a.manual and os.path.exists(a.manual):
    for prov,real in json.load(open(a.manual,encoding='utf-8')).items():
        if prov not in prov_to_real:
            prov_to_real[prov]=real; report.append((prov,real,'manual',''))
    unmatched = [u for u in unmatched if u[0] not in prov_to_real]

new_md = rewrite_citekeys(md, prov_to_real)   # longest-first + lookahead + prov==real skip (see _recon.py)
if not os.path.exists(prov_backup): open(prov_backup,'w',encoding='utf-8').write(md)
open(a.manuscript,'w',encoding='utf-8').write(new_md)
# keep build/citemap.tsv in sync (see reconcile_live.py): -Mode rebuild matches by these
# provisional keys; if manuscript.md moves to real keys but the citemap stays provisional,
# rebuild silently matches nothing. Rewrite the provisional_citekey column for reconciled keys.
try:
    cchg, cm_bak = sync_citemap(os.path.join(BD, 'citemap.tsv'), prov_to_real)
    if cchg is not None:
        print("citemap.tsv: %d provisional key(s) synced to real (backup: %s)" % (cchg, cm_bak))
except Exception as e:
    print("WARN: could not sync citemap.tsv; after reconcile use -Mode live (not rebuild):", e)
with open(os.path.join(BD,'citekey_reconcile_report.tsv'),'w',encoding='utf-8') as f:
    f.write("provisional\treal\tmethod\ttitle\n")
    for r in report: f.write("\t".join(map(str,r))+"\n")
with open(os.path.join(BD,'citekey_unmatched.tsv'),'w',encoding='utf-8') as f:
    f.write("provisional\tyear\ttitle\n")
    for r in unmatched: f.write("\t".join(map(str,r))+"\n")
from collections import Counter
print("matched:", len(report), dict(Counter(m for _,_,m,_ in report)), "| UNMATCHED:", len(unmatched))
if unmatched:
    print("unmatched (give real keys via --manual, or add to the export & re-run):")
    for p,y,t in unmatched: print("  ", p, y, t)
