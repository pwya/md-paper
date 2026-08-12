#! /usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_formulas.py -- md-unpack product self-check for formulas (T21-2b gate).

Runs right after transform.py to answer: "did every [EQ-OMML-N] placeholder become a
real formula, and do the rendered formulas match the ground truth?"

  1. residual placeholder scan  -- after folding non-ASCII variants back to ASCII, any
     leftover [EQ-OMML-N] / `TODO-EQ-OMML-N` token is a HARD fail (this is exactly what
     the 2026-08-11 real manuscript tripped on: 31 leaked tokens). Tolerates the
     backslash-escaped bracket form ("\\[EQ-OMML-34\\]") that the Markdown escaper
     produces when a token is NOT recognized as a placeholder.
  2. ground-truth formula audit -- every rendered inline/display $...$ in the manuscript is
     located in the pandoc direct-conversion (build/direct.md = ground truth straight from
     the original Word) by a local-context overlap score around it, then compared byte-for-byte
     (whitespace-folded). Confirmed mismatch = HARD fail; formulas we could not anchor are
     reported as coverage (WARN only, never a silent pass).
  3. formula totals            -- informational (ms vs direct), non-fatal.

Exit: 0 = PASS (0 residual, 0 mismatch), 1 = FAIL.

Usage:
  py verify_formulas.py --manuscript manuscript.md --direct build/direct.md
"""
import argparse, io, re, sys, unicodedata

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# ---- MUST mirror transform.py section 0 exactly (dev handbook 6.5-4 / 6.5-6: single source) ----
_DASH_TO_HYPHEN = {0x2010:'-', 0x2011:'-', 0x2012:'-', 0x2013:'-', 0x2014:'-', 0x2212:'-'}
def _fold_math_glyphs(tok):
    out = []
    for ch in tok:
        o = ord(ch)
        if 0x1D400 <= o <= 0x1D7FF:
            out.append(unicodedata.normalize('NFKC', ch))
        elif o in _DASH_TO_HYPHEN:
            out.append(_DASH_TO_HYPHEN[o])
        elif 0xFF01 <= o <= 0xFF5E:
            out.append(chr(o - 0xFEE0))
        else:
            out.append(ch)
    return ''.join(out)
_MATH_PH = re.compile(r'\[[^\[\]\n]*(?:[\U0001D400-\U0001D7FF]|\u2212)[^\[\]\n]*\]')
def fold(md): return _MATH_PH.sub(lambda m: _fold_math_glyphs(m.group(0)), md)

# inline $...$ and display $$...$$ (display first in alternation)
_MATH = re.compile(r'\$\$[^$]+\$\$|\$[^$\n]+\$')
_RESID = re.compile(r'\\?\[EQ-OMML-\d+\\?\]|`TODO-EQ-OMML-\d+`')
_MARK = '\u00a7'   # section-sign used as the "a formula is here" token

def sec_tokenize(text):
    """Replace every formula with a single MARK token, then lower/space -> token list."""
    t = _MATH.sub(' %s ' % _MARK, text)
    t = re.sub(r'[^a-z0-9%s]+' % _MARK, ' ', t.lower())
    return re.sub(r'\s+', ' ', t).strip().split()

def _context_overlap_score(left_a, right_a, left_b, right_b):
    """Number of matching tokens at the end of left/start of right (contiguous suffix/prefix)."""
    # left: match from the right edge (closest to the formula) outward
    lscore = 0
    for i in range(1, min(len(left_a), len(left_b)) + 1):
        if left_a[-i] == left_b[-i]:
            lscore += 1
        else:
            break
    # right: match from the left edge (closest to the formula) outward
    rscore = 0
    for i in range(min(len(right_a), len(right_b))):
        if right_a[i] == right_b[i]:
            rscore += 1
        else:
            break
    return lscore + rscore

def _best_anchor(j, ms_toks, dir_mk, direct_toks, W):
    """Find direct marker position with best context overlap score.
    Returns (best_idx, best_score, second_score) where best_idx is index into dir_mk."""
    left = ms_toks[max(0, j - W):j]
    right = ms_toks[j + 1:j + 1 + W]
    best = -1
    best_score = -1
    second_score = -1
    for kd, jd in enumerate(dir_mk):
        dleft = direct_toks[max(0, jd - len(left)):jd]
        dright = direct_toks[jd + 1:jd + 1 + len(right)]
        sc = _context_overlap_score(left, right, dleft, dright)
        if sc > best_score:
            second_score = best_score
            best_score = sc
            best = kd
        elif sc > second_score:
            second_score = sc
    return best, best_score, second_score

def main():
    ap = argparse.ArgumentParser(description='Formula self-check (residuals + ground-truth audit)')
    ap.add_argument('--manuscript', required=True)
    ap.add_argument('--direct', required=True)
    ap.add_argument('--window', type=int, default=10, help='context tokens each side (default 10)')
    ap.add_argument('--min-score', type=int, default=4, help='min overlap score to accept an anchor (default 4)')
    a = ap.parse_args()

    with open(a.manuscript, encoding='utf-8') as f:
        ms = f.read()
    with open(a.direct, encoding='utf-8') as f:
        direct = f.read()

    folded = fold(ms)
    folded_lines = folded.splitlines()

    # ---- 1. residual placeholder scan (HARD) ----
    resid = []
    for i, ln in enumerate(folded_lines, 1):
        for m in _RESID.finditer(ln):
            resid.append((i, m.group(0)))

    # ---- 2. ground-truth audit via best-score local context alignment ----
    ms_toks = sec_tokenize(folded)
    direct_toks = sec_tokenize(direct)
    ms_maths = _MATH.findall(folded)
    direct_maths = _MATH.findall(direct)
    ms_mk = [i for i, t in enumerate(ms_toks) if t == _MARK]     # marker positions in ms
    dir_mk = [i for i, t in enumerate(direct_toks) if t == _MARK]

    W = a.window
    MIN_SCORE = a.min_score
    checked = matched = 0
    mism = []
    unanch = []
    for k, j in enumerate(ms_mk):
        best_k, best_score, second_score = _best_anchor(j, ms_toks, dir_mk, direct_toks, W)
        if best_score < MIN_SCORE:
            unanch.append((k, ms_maths[k], 'score %d < min %d' % (best_score, MIN_SCORE)))
            continue
        # need clear winner: best strictly better than second (no tie)
        if second_score >= best_score:
            unanch.append((k, ms_maths[k], 'ambiguous (score %d tied)' % best_score))
            continue
        checked += 1
        got = re.sub(r'\s+', '', direct_maths[best_k])
        exp = re.sub(r'\s+', '', ms_maths[k])
        if got == exp:
            matched += 1
        else:
            mism.append((k, ms_maths[k], direct_maths[best_k]))

    # ---- 3. formula totals (informational) ----
    ms_total = len(_MATH.findall(folded))
    direct_total = len(_MATH.findall(direct))

    # ---- report ----
    print("=== verify_formulas ===")
    print(f"residual placeholders : {len(resid)}")
    for i, tok in resid[:25]:
        print(f"   line {i}: {tok}")
    if len(resid) > 25:
        print(f"   ... and {len(resid)-25} more")
    print(f"formulas checked      : {checked}  (match {matched}, mismatch {len(mism)}, unanchored {len(unanch)})")
    for k, exp, got in mism[:10]:
        print(f"   formula#{k}: expected {exp!r} got {got!r}")
    for k, f, why in unanch[:10]:
        print(f"   [WARN] formula#{k}: could not anchor {f!r} ({why})")
    if len(unanch) > 10:
        print(f"   ... and {len(unanch)-10} more unanchored formula(s)")
    print(f"formula totals        : manuscript {ms_total}, direct-conversion {direct_total}")

    hard = bool(resid) or bool(mism)
    if hard:
        print("=== result: FAIL (exit 1) ===")
        if resid:
            print(f"  [RESIDUAL] {len(resid)} leftover placeholder token(s) -- formulas were not all resolved")
        if mism:
            print(f"  [MISMATCH] {len(mism)} formula(s) differ from the Word ground truth")
        return 1
    print("=== result: OK ===")
    print("  residual 0 / mismatch 0 -- formulas clean vs the direct-conversion ground truth")
    return 0

if __name__ == '__main__':
    sys.exit(main())
