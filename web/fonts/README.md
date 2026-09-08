# Fonts

Two typefaces, self-hosted. No CDN: the page loads nothing from a third party.

| File | Used for |
|---|---|
| `checksum-doc.woff2` | headings, prose, anything a person wrote |
| `checksum-data.woff2` | anything that came out of the warehouse — SQL, values, ranks |

Both are variable across weight 200–800 and total 38 KB, which is less than a
single request to a font CDN would have cost.

## Provenance

Derived from [Monaspace](https://github.com/githubnext/monaspace) v1.400 by
GitHub, licensed under the SIL Open Font License 1.1 (`LICENSE-Monaspace.txt`).
`checksum-doc` comes from Monaspace Xenon, `checksum-data` from Monaspace Neon.

`subset.py` is the exact script that produced them. It pins the width and slant
axes, keeps the weight axis, cuts the character set to Latin plus the handful of
symbols this page draws, and drops `liga`/`calt`. Dropping the contextual
alternates is deliberate as well as convenient: a page whose entire claim is
*this is the query that ran* must not render `>=` as a single glyph.

The OFL reserves the name "Monaspace" and its subfamily names for the unmodified
font. These are modified versions, so they are renamed, and each carries the
original copyright, the licence name and the licence URL in its `name` table.
