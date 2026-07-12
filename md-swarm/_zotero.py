# -*- coding: utf-8 -*-
"""
_zotero.py -- talk to a running Zotero + Better BibTeX (localhost JSON-RPC), DEGRADE-SAFE.

Used by:
  - verify_citekeys.py LAYER 2 (is an unknown citekey real-in-Zotero, or hallucinated?)
  - the md-swarm pre-flight tier banner ("which citation capability tier am I in right now?")

Every call bypasses a local proxy (Clash/TUN 502s localhost, the way reconcile_live.py handles it) and
NEVER raises into the caller: a closed Zotero / missing BBT / odd API shape returns a clear "down" /
"couldn't determine" so a citation check can DOWNGRADE to "couldn't verify" rather than crash or, worse,
mislabel a real reference as fake.

This is a .py (UTF-8, no BOM) -- Chinese user-facing text is fine here. The ASCII-only rule is only for
the .ps1 files (build.ps1 etc.) that PS 5.1 would garble.
"""
import json
import urllib.request
import urllib.error

ENDPOINT = 'http://127.0.0.1:23119/better-bibtex/json-rpc'
PROBE_URL = 'http://127.0.0.1:23119/better-bibtex/cayw?probe=probe'


def _opener():
    # empty ProxyHandler => ignore system/WinINET proxy; localhost must not go through Clash/TUN.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def probe(timeout=4):
    """Return (state, detail). state in {'ready','no-bbt','down'} -- mirrors build.ps1's preflight so
    md-swarm and md-build agree:
       'ready'  = Zotero up and Better BibTeX answering
       'no-bbt' = Zotero up but BBT endpoint 404s (BBT missing/disabled)
       'down'   = Zotero unreachable (closed, or a proxy is eating localhost)."""
    try:
        with _opener().open(PROBE_URL, timeout=timeout) as r:
            return ('ready', 'HTTP %d' % r.getcode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return ('no-bbt', 'HTTP 404 (Better BibTeX not answering on its endpoint)')
        return ('ready', 'HTTP %d (something answered on the port)' % e.code)
    except Exception as e:
        return ('down', repr(e)[:120])


def citekeys_exist(keys, endpoint=ENDPOINT, timeout=8):
    """Best-effort existence check against a running Zotero/BBT.

    Returns (known, unknown, consulted):
      known      = set of input keys Zotero confirms exist
      unknown    = set of input keys Zotero was consulted about but did NOT confirm
      consulted  = True only if Zotero was actually reached with a sane response shape.
    On ANY failure (Zotero down, weird shape, exception) returns (set(), set(keys), False) so the
    caller treats everything as "couldn't verify" -- NEVER a false "hallucinated".

    Mechanism: BBT JSON-RPC `item.search(<citekey>)` -> matching items; a key is real if any returned
    item's citation key equals it. SMOKE-TEST NOTE: the method/field shape varies by BBT version
    (reconcile_live.py hit the same variance). With Zotero OPEN, run `python _zotero.py --check <a-real-key>`
    once to confirm the wiring before trusting a 'not found' as 'fake'.
    """
    keys = list(dict.fromkeys(k for k in keys if k))
    if not keys:
        return (set(), set(), True)
    known = set()
    try:
        for k in keys:
            payload = json.dumps({'jsonrpc': '2.0', 'method': 'item.search',
                                  'params': [k], 'id': 1}).encode('utf-8')
            req = urllib.request.Request(endpoint, data=payload,
                                         headers={'Content-Type': 'application/json'})
            with _opener().open(req, timeout=timeout) as r:
                resp = json.loads(r.read().decode('utf-8'))
            res = resp.get('result') if isinstance(resp, dict) else None
            if not isinstance(res, list):
                return (set(), set(keys), False)   # unexpected shape -> couldn't verify (fail safe)
            for it in res:
                if not isinstance(it, dict):
                    continue
                ck = (it.get('citekey') or it.get('citationKey')
                      or it.get('citation-key') or it.get('id'))
                if isinstance(ck, str) and ck == k:
                    known.add(k)
                    break
    except Exception:
        return (set(), set(keys), False)
    return (known, set(keys) - known, True)


def tier_banner(timeout=4):
    """One-glance 'what can I do with citations right now' banner. Pure string (caller prints)."""
    state, detail = probe(timeout=timeout)
    if state == 'ready':
        return ('[Zotero 体检] 连接 OK（Better BibTeX 在线，%s）\n'
                '  全功能：能现查引用、能加新文献、改稿时新引用能自动核实真假。' % detail)
    if state == 'no-bbt':
        return ('[Zotero 体检] Zotero 开着，但 Better BibTeX 没应答（%s）\n'
                '  装/启用 BBT 后即全功能；当前：新加引用无法自动核实真假（只会列出来让你自己看）。' % detail)
    return ('[Zotero 体检] Zotero 没连上（%s）\n'
            '  离线模式：新加引用无法自动核实真假（只会列出来让你自己看）；出稿只能 -Mode static / rebuild。\n'
            '  想要全功能（自动判真假 / 加新文献 / 现查）：开着 Zotero（装 Better BibTeX）再来。' % detail)


if __name__ == '__main__':
    import io
    import sys
    import argparse
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    ap = argparse.ArgumentParser(description='Zotero/BBT probe + optional citekey existence check.')
    ap.add_argument('--check', nargs='*', default=None,
                    help='citekeys to look up (smoke-test: pass one you KNOW exists, when Zotero is open)')
    args = ap.parse_args()
    print(tier_banner())
    if args.check is not None:
        known, unknown, consulted = citekeys_exist(args.check)
        print('\nconsulted Zotero:', consulted)
        print('  known in Zotero  :', sorted(known))
        print('  NOT found        :', sorted(unknown))
        if not consulted:
            print('  (Zotero not reached or unexpected response shape -- treated as "couldn\'t verify".)')
