# Third-Party Notices — bundled-lua/

The Lua files in this directory are **third-party software, bundled unmodified**
(or with only trivial path adjustments) and used at build time by
`md-build/build.ps1` via Pandoc's `--lua-filter`.

They are **NOT** covered by this repository's Apache-2.0 license. Each remains
under its original **MIT** license, reproduced in full below. MIT only requires
that the copyright notice and permission notice be retained — which this file does.

---

## 1. zotero.lua, locator.lua, utils.lua, pandoc-zotero-live-citemarkers.lua

- **Source:** https://github.com/retorquere/zotero-better-bibtex (`pandoc/` directory)
- **Project:** Better BibTeX for Zotero — "bbt-to-live-doc" Pandoc filters
- **License:** MIT
- **Copyright (c) 2020 Emiliano Heyns**

```
MIT License

Copyright (c) 2020 Emiliano Heyns

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

> Note: `pandoc-zotero-live-citemarkers.lua` already carries this MIT header
> inline. `zotero.lua`, `locator.lua`, and `utils.lua` are the same project's
> files that did not have the header inline — this notice supplies it for them.

---

## 2. lunajson.lua and lunajson/ (decoder.lua, encoder.lua, sax.lua)

- **Source:** https://github.com/grafi-tt/lunajson
- **Project:** lunajson — pure-Lua JSON decoder/encoder
- **License:** MIT
- **Copyright (c) 2015-2017 Shunsuke Shimizu (grafi)**

```
The MIT License (MIT)

Copyright (c) 2015-2017 Shunsuke Shimizu (grafi)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

---

## Not bundled here (for completeness)

`pandoc.exe` and `pandoc-crossref.exe` (both **GPL-2.0**) are **external programs**,
not redistributed in this repository. `md-build/setup_md_tools.ps1` downloads the
official pinned releases at setup time. See the repository-root `NOTICE` file.
