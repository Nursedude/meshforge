# Third-Party Attribution

MeshForge is licensed under GPL-3.0 (see `LICENSE`). It incorporates the
following third-party work, whose original licenses are reproduced/honored here.

---

## LXMFace — deterministic avatar/identicon generator

- **Upstream:** https://github.com/ratspeak/LXMFace
- **License:** MIT
- **Used in:**
  - `src/utils/lxmface.py` — a faithful Python port of the upstream
    `js/lxmface.js` algorithm (xorshift128 PRNG → HSL palette → mirrored grid →
    SVG).
  - `web/js/lxmface.js` — the upstream JavaScript, vendored verbatim, plus a
    `seedForId` canonicalizer matching the Python `seed_string_for_node`.
  - `tests/fixtures/lxmface_vectors.json` — shared cross-implementation test
    vectors copied from upstream `tests/vectors.json`, used to lock our output
    byte-for-byte to upstream (see `tests/test_lxmface.py`).

LXMFace itself notes it is *"adapted from https://github.com/download13/blockies
(MIT license)"* — Copyright (c) 2015 download13. That attribution carries
through here.

Why MeshForge uses it: the avatar is a pure function of an LXMF/RNS destination
hash, so a node's face is identical in MeshForge and in Ratspeak (and any other
RNS-ecosystem client using LXMFace). MIT is compatible with GPL-3.0; the
combined work is distributed under GPL-3.0.

### MIT License (LXMFace / blockies)

```
MIT License

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
