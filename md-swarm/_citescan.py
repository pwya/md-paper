# -*- coding: utf-8 -*-
"""
_citescan.py -- single source of truth for "what citations / cross-refs does this Markdown contain".

Three md-swarm scripts (apply_md_changeset.py, verify_refs.py, render_changeset.py) used to EACH
carry their own copy of the [@citekey] regex + the rules for what counts (exclude @fig:/@tbl:/@eq:/
@sec: cross-refs and [@NEW:...] placeholders). Per dev handbook section 6.5 rule (6) DRY they now all
import from here -- change the rule once, not in three files.

One consequence of consolidating: the scanner also STRIPS code before counting -- fenced ``` / ~~~
blocks and inline `code` spans -- so a literal [@key] inside a code listing is not mistaken for a real
citation (handbook 6.5 rule (3): code has syntax; don't count tokens inside it). Without this, deleting
a code block that contains a [@key] would false-trip apply's "dropped citation" guard, and a fake
[@key] written inside a fence would slip past every check.

Scope note (the "light" code strip, #5): fenced blocks and inline spans are removed; 4-space *indented*
code blocks are NOT -- academic prose indents freely, so stripping those would risk eating real text.
The fully-general fix is a pandoc AST pass (handbook #13); this light pass covers the realistic case.
"""
import re

XREF_KINDS = ('fig', 'tbl', 'eq', 'sec')

# fenced code block: a line of >=3 backticks or tildes (with optional info string), up to a closing
# fence of the SAME run. (?ms): ^/$ are per-line, . spans newlines. \1 requires an identical closer.
_FENCE = re.compile(r'(?ms)^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?^[ \t]*\1[ \t]*$')
# inline code span: a backtick run, content (no newline), the same-length run.
_INLINE_CODE = re.compile(r'(`+)[^\n]*?\1')


def strip_code(md):
    """Remove fenced code blocks and inline code spans, each replaced by a space (so neighbouring
    tokens stay separated). Returns the code-free text used for all citation counting."""
    if not md:
        return md or ''
    md = _FENCE.sub(' ', md)
    md = _INLINE_CODE.sub(' ', md)
    return md


def _cite_inners(md):
    """Yield the inner text of every [...] containing an '@' (code already stripped)."""
    for m in re.finditer(r'\[([^\]\[]*@[^\]\[]*)\]', md):
        yield m.group(1)


def _real_keys_of(inner):
    """From one bracket group's inner text, yield the real citekeys (skip xref kinds + NEW)."""
    for tok in inner.split(';'):
        cm = re.match(r'@([^\s;,]+)', tok.strip())
        if not cm:
            continue
        key = cm.group(1)
        if key.split(':', 1)[0].lower() in XREF_KINDS:
            continue
        if key.upper().startswith('NEW'):
            continue
        yield key


def citekeys_in(md):
    """Real [@citekey] tokens WITH multiplicity (a list). Excludes @fig:/@tbl:/@eq:/@sec: cross-refs
    and [@NEW:...] placeholders. Code is stripped first. THE citekey rule for every md-swarm script."""
    md = strip_code(md or '')
    keys = []
    for inner in _cite_inners(md):
        keys.extend(_real_keys_of(inner))
    return keys


def scan(md):
    """Full structural scan (used by verify_refs). Returns dict:
       citekeys (list w/ multiplicity), groups (list[frozenset]), new_ph (list of '[@NEW: ...]'),
       xref_refs (set 'kind:label'), xref_defs (set 'kind:label'). Code-stripped before scanning."""
    md = strip_code(md or '')
    citekeys, groups, new_ph = [], [], []
    for inner in _cite_inners(md):
        for nm in re.finditer(r'@NEW:[^;\]]*', inner):
            new_ph.append('[' + nm.group(0).strip() + ']')
        grp = set()
        for key in _real_keys_of(inner):
            citekeys.append(key)
            grp.add(key)
        if len(grp) >= 1:
            groups.append(frozenset(grp))
    xref_refs = set()
    for m in re.finditer(r'@(fig|tbl|eq|sec):([A-Za-z0-9_][A-Za-z0-9_:.\-]*)', md):
        xref_refs.add(m.group(1) + ':' + m.group(2))
    xref_defs = set()
    for m in re.finditer(r'\{#(fig|tbl|eq|sec):([A-Za-z0-9_][A-Za-z0-9_:.\-]*)\}', md):
        xref_defs.add(m.group(1) + ':' + m.group(2))
    return dict(citekeys=citekeys, groups=groups, new_ph=new_ph,
                xref_refs=xref_refs, xref_defs=xref_defs)


if __name__ == '__main__':
    # Regression self-test (handbook 6.5 rule (4): one bug = one test). `python _citescan.py` -> OK.
    import io, sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    # basic: real keys counted with multiplicity; xrefs + NEW excluded
    md = 'See [@li2020; @zhao2019] and [@li2020]. Cf. [@fig:1], [@tbl:2], [@NEW: Smith 2021].'
    assert citekeys_in(md) == ['li2020', 'zhao2019', 'li2020'], citekeys_in(md)
    s = scan(md)
    assert sorted(set(s['citekeys'])) == ['li2020', 'zhao2019'], s['citekeys']
    assert s['xref_refs'] == {'fig:1', 'tbl:2'}, s['xref_refs']
    assert s['new_ph'] == ['[@NEW: Smith 2021]'], s['new_ph']
    assert frozenset({'li2020', 'zhao2019'}) in s['groups']

    # #5 code-fence: [@fake] inside a fenced block and an inline span must NOT be counted
    fenced = 'Real [@real2020].\n\n```python\nx = "[@fake2099]"\n```\n\nInline `[@alsoFake]` here.'
    assert citekeys_in(fenced) == ['real2020'], citekeys_in(fenced)

    # tilde fence too
    t = 'A [@a2020].\n\n~~~\n[@b1999]\n~~~\n'
    assert citekeys_in(t) == ['a2020'], citekeys_in(t)

    # xref definitions detected
    d = scan('Figure caption {#fig:3}\n\n: A table {#tbl:4}')
    assert d['xref_defs'] == {'fig:3', 'tbl:4'}, d['xref_defs']

    print('OK _citescan self-test passed')
