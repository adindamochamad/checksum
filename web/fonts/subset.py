"""Pin the width and slant axes, keep weight, and cut everything the page never
draws. The shipped file has to be small enough to be worth self-hosting."""
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer
from fontTools.subset import Subsetter, Options

# Ranges only where the whole block is wanted. The geometric-shapes block is
# listed glyph by glyph instead: some of its glyphs carry no gvar entry in this
# font and subsetting the range whole raises KeyError on the first one.
KEEP = []
for a, b in [(0x20, 0x7E), (0xA0, 0xFF)]:
    KEEP.extend(range(a, b + 1))
KEEP += [0x2010, 0x2011, 0x2013, 0x2014, 0x2018, 0x2019, 0x201C, 0x201D,
         0x2022, 0x2026, 0x2030, 0x2039, 0x203A, 0x2192, 0x2193, 0x2212,
         0x2260, 0x2264, 0x2265, 0x2713]

RENAME = {"Xenon": "Checksum Doc", "Neon": "Checksum Data"}

for name in RENAME:
    src = f"monaspace/Variable Web Fonts/Monaspace {name}/Monaspace {name} Var.woff2"
    f = TTFont(src)
    f = instancer.instantiateVariableFont(f, {"wdth": 100, "slnt": 0}, updateFontNames=False)
    # A glyph that never varies is allowed to have no gvar entry, but the
    # subsetter indexes the table by every retained glyph and raises KeyError on
    # the first one missing. Materialise the lazy dict and fill the gaps.
    if "gvar" in f:
        gvar = f["gvar"]
        gvar.variations = dict(gvar.variations)
        for glyph in f.getGlyphOrder():
            gvar.variations.setdefault(glyph, [])

    opt = Options()
    # No liga/calt. Two reasons: Monaspace's contextual alternates pull in glyphs
    # that carry no gvar entry and break subsetting, and a page whose whole claim
    # is "this is the query that ran" must not render >= as one glyph.
    opt.layout_features = ["kern", "tnum", "ccmp", "locl", "mark", "mkmk"]
    opt.name_IDs = [0, 1, 2, 3, 4, 6, 13, 14]
    opt.notdef_outline = True
    opt.drop_tables += ["DSIG"]
    sub = Subsetter(options=opt)
    sub.populate(unicodes=KEEP)
    sub.subset(f)
    # The OFL reserves "Monaspace" and its subfamily names for the unmodified
    # font. This one is instanced and subset, so it is a Modified Version and
    # must not carry them. Copyright and licence records (0, 13, 14) stay.
    new = RENAME[name]
    compact = new.replace(" ", "")
    for record in f["name"].names:
        if record.nameID in (0, 13, 14):
            continue
        value = record.toUnicode()
        value = value.replace(f"Monaspace {name}", new).replace(name, compact)
        value = value.replace("Monaspace", "Checksum")
        record.string = value
    f["name"].setName(new, 1, 3, 1, 0x409)
    f["name"].setName("Regular", 2, 3, 1, 0x409)
    f["name"].setName(new, 4, 3, 1, 0x409)
    f["name"].setName(f"{compact}-Regular", 6, 3, 1, 0x409)
    f["name"].setName(
        "Copyright (c) 2023, GitHub (https://github.com/githubnext/monaspace). "
        f"Modified: instanced to a single width and subset to the Latin set. "
        f"Derived from Monaspace {name}; renamed per the OFL reserved font name "
        "clause.", 0, 3, 1, 0x409)
    f["name"].setName("SIL Open Font License, Version 1.1", 13, 3, 1, 0x409)
    f["name"].setName("https://scripts.sil.org/OFL", 14, 3, 1, 0x409)

    f.flavor = "woff2"
    out = f"out-{name.lower()}.woff2"
    f.save(out)
    print(f"{name}: {f['maxp'].numGlyphs} glyphs -> {out}")
