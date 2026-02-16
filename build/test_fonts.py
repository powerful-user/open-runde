#!/usr/bin/env python3
"""
Tests for the Open Runde build pipeline.

Validates generated fonts against their Inter source fonts to catch regressions
like missing glyphs, wrong metrics, or lost outlines.

Usage:
    cd build && pytest test_fonts.py -v
"""

import math
from pathlib import Path

import pytest
from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import RecordingPen

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

BUILD_DIR = Path(__file__).parent
INTER_SOURCE_DIR = BUILD_DIR / "inter-source"
SRC_DIR = BUILD_DIR.parent / "src"
DESKTOP_DIR = SRC_DIR / "desktop"
WEB_DIR = SRC_DIR / "web"
GLYPHS_DIR = SRC_DIR / "glyphs"

SCALE_FACTOR = 2816 / 2048  # 1.375

# --------------------------------------------------------------------------- #
# Variant definitions
# --------------------------------------------------------------------------- #

# (weight_class, output_suffix, style_name, is_italic, inter_filename)
VARIANTS = [
    (100, "Thin",              "Thin",              False, "Inter-Thin.ttf"),
    (200, "ExtraLight",        "ExtraLight",        False, "Inter-ExtraLight.ttf"),
    (300, "Light",             "Light",             False, "Inter-Light.ttf"),
    (400, "Regular",           "Regular",           False, "Inter-Regular.ttf"),
    (500, "Medium",            "Medium",            False, "Inter-Medium.ttf"),
    (600, "Semibold",          "SemiBold",          False, "Inter-SemiBold.ttf"),
    (700, "Bold",              "Bold",              False, "Inter-Bold.ttf"),
    (800, "ExtraBold",         "ExtraBold",         False, "Inter-ExtraBold.ttf"),
    (900, "Black",             "Black",             False, "Inter-Black.ttf"),
    (100, "ThinItalic",        "Thin Italic",       True,  "Inter-ThinItalic.ttf"),
    (200, "ExtraLightItalic",  "ExtraLight Italic", True,  "Inter-ExtraLightItalic.ttf"),
    (300, "LightItalic",       "Light Italic",      True,  "Inter-LightItalic.ttf"),
    (400, "Italic",            "Italic",            True,  "Inter-Italic.ttf"),
    (500, "MediumItalic",      "Medium Italic",     True,  "Inter-MediumItalic.ttf"),
    (600, "SemiBoldItalic",    "SemiBold Italic",   True,  "Inter-SemiBoldItalic.ttf"),
    (700, "BoldItalic",        "Bold Italic",       True,  "Inter-BoldItalic.ttf"),
    (800, "ExtraBoldItalic",   "ExtraBold Italic",  True,  "Inter-ExtraBoldItalic.ttf"),
    (900, "BlackItalic",       "Black Italic",      True,  "Inter-BlackItalic.ttf"),
]

VARIANT_IDS = [v[1] for v in VARIANTS]

# Composites that historically had bugs (e.g. empty `i`)
KNOWN_COMPOSITES = ["i", "j", "iacute", "Agrave", "Ntilde"]

# Rectangular glyphs that should gain curveTo ops from rounding
RECTANGULAR_GLYPHS = ["L", "H", "I", "T"]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def otf_path(suffix: str) -> Path:
    return DESKTOP_DIR / f"OpenRunde-{suffix}.otf"


def source_path(inter_file: str) -> Path:
    return INTER_SOURCE_DIR / inter_file


def open_font(path: Path) -> TTFont:
    return TTFont(str(path))


def get_charstring_ops(font: TTFont, glyph_name: str) -> list:
    """Return the list of (op, args) for a CFF glyph."""
    cff = font["CFF "].cff.topDictIndex[0].CharStrings
    rec = RecordingPen()
    cff[glyph_name].draw(rec)
    return rec.value


def has_drawing_ops(font: TTFont, glyph_name: str) -> bool:
    """True if the glyph has any lineTo or curveTo operations."""
    try:
        ops = get_charstring_ops(font, glyph_name)
        return any(op in ("lineTo", "curveTo") for op, _ in ops)
    except Exception:
        return False


def source_glyph_has_outlines(source_font: TTFont, glyph_name: str) -> bool:
    """True if the TTF source glyph has outlines (simple or composite)."""
    glyf = source_font["glyf"]
    if glyph_name not in glyf:
        return False
    g = glyf[glyph_name]
    return g.numberOfContours != 0


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture(params=VARIANTS, ids=VARIANT_IDS)
def variant(request):
    """Yields (weight, suffix, style_name, is_italic, inter_file) for each variant."""
    return request.param


@pytest.fixture
def gen_font(variant):
    """Open the generated OTF for the current variant."""
    _, suffix, _, _, _ = variant
    path = otf_path(suffix)
    assert path.exists(), f"Generated font missing: {path}"
    return open_font(path)


@pytest.fixture
def src_font(variant):
    """Open the Inter source TTF for the current variant."""
    _, _, _, _, inter_file = variant
    path = source_path(inter_file)
    assert path.exists(), f"Inter source missing: {path}"
    return open_font(path)


# --------------------------------------------------------------------------- #
# 1. File existence
# --------------------------------------------------------------------------- #

class TestFileExistence:
    """All 18 variants should have OTF, WOFF, WOFF2, and .glyphs files."""

    @pytest.mark.parametrize("suffix", VARIANT_IDS)
    def test_otf_exists(self, suffix):
        assert otf_path(suffix).exists()

    @pytest.mark.parametrize("suffix", VARIANT_IDS)
    def test_woff_exists(self, suffix):
        assert (WEB_DIR / f"OpenRunde-{suffix}.woff").exists()

    @pytest.mark.parametrize("suffix", VARIANT_IDS)
    def test_woff2_exists(self, suffix):
        assert (WEB_DIR / f"OpenRunde-{suffix}.woff2").exists()

    @pytest.mark.parametrize("suffix", VARIANT_IDS)
    def test_glyphs_exists(self, suffix):
        assert (GLYPHS_DIR / f"OpenRunde-{suffix}.glyphs").exists()


# --------------------------------------------------------------------------- #
# 2. Glyph completeness vs source
# --------------------------------------------------------------------------- #

class TestGlyphCompleteness:

    def test_glyph_count_matches(self, gen_font, src_font):
        gen_count = len(gen_font.getGlyphOrder())
        src_count = len(src_font.getGlyphOrder())
        assert gen_count == src_count, (
            f"Glyph count mismatch: generated={gen_count}, source={src_count}"
        )

    def test_cmap_coverage_matches(self, gen_font, src_font):
        gen_cmap = set(gen_font.getBestCmap().keys())
        src_cmap = set(src_font.getBestCmap().keys())
        missing = src_cmap - gen_cmap
        assert not missing, (
            f"Missing {len(missing)} codepoints in generated font: "
            f"{sorted(list(missing))[:20]}"
        )


# --------------------------------------------------------------------------- #
# 3. No outline loss
# --------------------------------------------------------------------------- #

class TestNoOutlineLoss:

    def test_all_outlined_glyphs_have_outlines(self, gen_font, src_font, variant):
        """Every glyph with outlines in the source should have outlines in output."""
        glyph_order = src_font.getGlyphOrder()
        missing = []
        for name in glyph_order:
            if source_glyph_has_outlines(src_font, name):
                if not has_drawing_ops(gen_font, name):
                    missing.append(name)
        assert not missing, (
            f"{len(missing)} glyphs lost outlines: {missing[:20]}"
        )

    @pytest.mark.parametrize("glyph_name", KNOWN_COMPOSITES)
    def test_known_composites_have_outlines(self, glyph_name, variant):
        """Explicit check on composites that historically had bugs."""
        _, suffix, _, _, _ = variant
        font = open_font(otf_path(suffix))
        assert has_drawing_ops(font, glyph_name), (
            f"Composite glyph '{glyph_name}' has no outlines in {suffix}"
        )


# --------------------------------------------------------------------------- #
# 4. Metric scaling (UPM 2048 → 2816)
# --------------------------------------------------------------------------- #

class TestMetricScaling:

    def test_upm(self, gen_font):
        assert gen_font["head"].unitsPerEm == 2816

    def test_hmtx_widths(self, gen_font, src_font):
        gen_hmtx = gen_font["hmtx"]
        src_hmtx = src_font["hmtx"]
        mismatches = []
        for name in src_font.getGlyphOrder()[:200]:  # spot-check first 200
            src_w = src_hmtx[name][0]
            expected = round(src_w * SCALE_FACTOR)
            actual = gen_hmtx[name][0]
            if actual != expected:
                mismatches.append((name, expected, actual))
        assert not mismatches, (
            f"{len(mismatches)} width mismatches (first 5): {mismatches[:5]}"
        )

    def test_vertical_metrics(self, gen_font, src_font):
        """OS/2 and hhea vertical metrics should match round(source * 1.375)."""
        checks = [
            ("OS/2", "sTypoAscender"),
            ("OS/2", "sTypoDescender"),
            ("OS/2", "sCapHeight"),
            ("OS/2", "sxHeight"),
            ("hhea", "ascent"),
            ("hhea", "descent"),
        ]
        for table, attr in checks:
            src_val = getattr(src_font[table], attr)
            expected = round(src_val * SCALE_FACTOR)
            actual = getattr(gen_font[table], attr)
            assert actual == expected, (
                f"{table}.{attr}: expected {expected}, got {actual}"
            )


# --------------------------------------------------------------------------- #
# 5. Weight metadata
# --------------------------------------------------------------------------- #

class TestWeightMetadata:

    def test_weight_class(self, gen_font, variant):
        weight_class, _, _, _, _ = variant
        assert gen_font["OS/2"].usWeightClass == weight_class

    def test_style_name(self, gen_font, variant):
        _, _, style_name, _, _ = variant
        name_table = gen_font["name"]
        # nameID 2 is the style/subfamily name
        style_record = name_table.getName(2, 3, 1, 0x0409)
        assert style_record is not None, "Missing nameID 2 (styleName)"
        actual = style_record.toUnicode()
        assert actual == style_name, (
            f"styleName: expected '{style_name}', got '{actual}'"
        )


# --------------------------------------------------------------------------- #
# 6. Italic vs roman metadata
# --------------------------------------------------------------------------- #

class TestItalicMetadata:

    def test_italic_angle(self, gen_font, variant):
        _, _, _, is_italic, _ = variant
        angle = gen_font["post"].italicAngle
        if is_italic:
            assert abs(angle - (-9.4)) < 0.1, f"Expected ~-9.4, got {angle}"
        else:
            assert angle == 0, f"Roman font has italicAngle={angle}"

    def test_fs_selection_italic_bit(self, gen_font, variant):
        _, _, _, is_italic, _ = variant
        fs = gen_font["OS/2"].fsSelection
        italic_bit = fs & 0x0001
        if is_italic:
            assert italic_bit, f"Italic font missing fsSelection ITALIC bit (fs=0x{fs:04x})"
        else:
            assert not italic_bit, f"Roman font has fsSelection ITALIC bit (fs=0x{fs:04x})"

    def test_mac_style_italic_bit(self, gen_font, variant):
        _, _, _, is_italic, _ = variant
        mac = gen_font["head"].macStyle
        italic_bit = mac & 0x0002
        if is_italic:
            assert italic_bit, f"Italic font missing macStyle ITALIC bit (mac=0x{mac:04x})"
        else:
            assert not italic_bit, f"Roman font has macStyle ITALIC bit (mac=0x{mac:04x})"


# --------------------------------------------------------------------------- #
# 7. Rounding evidence
# --------------------------------------------------------------------------- #

class TestRoundingEvidence:

    @pytest.mark.parametrize("glyph_name", RECTANGULAR_GLYPHS)
    def test_rectangular_glyphs_have_curves(self, glyph_name, variant):
        """Key rectangular glyphs should have curveTo ops from corner rounding."""
        _, suffix, _, _, _ = variant
        font = open_font(otf_path(suffix))
        ops = get_charstring_ops(font, glyph_name)
        curve_ops = [op for op, _ in ops if op == "curveTo"]
        assert len(curve_ops) > 0, (
            f"'{glyph_name}' in {suffix} has no curveTo ops — corners not rounded?"
        )

    @pytest.mark.parametrize("glyph_name", RECTANGULAR_GLYPHS)
    def test_bounding_box_within_radius(self, glyph_name, variant):
        """Bounding box shouldn't deviate more than corner radius from scaled source."""
        _, suffix, _, _, inter_file = variant
        gen = open_font(otf_path(suffix))
        src = open_font(source_path(inter_file))

        # Get source bbox scaled
        glyf = src["glyf"]
        if glyph_name not in glyf:
            pytest.skip(f"{glyph_name} not in source")
        g = glyf[glyph_name]
        if g.numberOfContours <= 0:
            pytest.skip(f"{glyph_name} has no simple contours")

        src_bbox = (
            round(g.xMin * SCALE_FACTOR),
            round(g.yMin * SCALE_FACTOR),
            round(g.xMax * SCALE_FACTOR),
            round(g.yMax * SCALE_FACTOR),
        )

        # Get generated bbox from charstring
        from fontTools.pens.boundsPen import BoundsPen
        cff = gen["CFF "].cff.topDictIndex[0].CharStrings
        bp = BoundsPen(None)
        cff[glyph_name].draw(bp)
        if bp.bounds is None:
            pytest.fail(f"No bounds for {glyph_name}")
        gen_bbox = tuple(round(v) for v in bp.bounds)

        max_radius = 120  # largest radius in config
        for i, label in enumerate(["xMin", "yMin", "xMax", "yMax"]):
            diff = abs(gen_bbox[i] - src_bbox[i])
            assert diff <= max_radius + 2, (
                f"{glyph_name} {label} deviation {diff} exceeds max radius "
                f"(gen={gen_bbox[i]}, scaled_src={src_bbox[i]})"
            )


# --------------------------------------------------------------------------- #
# 8. Web font validation
# --------------------------------------------------------------------------- #

class TestWebFonts:

    @pytest.mark.parametrize("suffix", VARIANT_IDS)
    def test_woff_flavor(self, suffix):
        path = WEB_DIR / f"OpenRunde-{suffix}.woff"
        if not path.exists():
            pytest.skip(f"WOFF not found: {path}")
        font = open_font(path)
        assert font.flavor == "woff"

    @pytest.mark.parametrize("suffix", VARIANT_IDS)
    def test_woff2_flavor(self, suffix):
        path = WEB_DIR / f"OpenRunde-{suffix}.woff2"
        if not path.exists():
            pytest.skip(f"WOFF2 not found: {path}")
        font = open_font(path)
        assert font.flavor == "woff2"

    @pytest.mark.parametrize("suffix", VARIANT_IDS)
    def test_web_glyph_count_matches_otf(self, suffix):
        otf = open_font(otf_path(suffix))
        otf_count = len(otf.getGlyphOrder())

        for ext in (".woff", ".woff2"):
            path = WEB_DIR / f"OpenRunde-{suffix}{ext}"
            if not path.exists():
                continue
            web = open_font(path)
            web_count = len(web.getGlyphOrder())
            assert web_count == otf_count, (
                f"{path.name} has {web_count} glyphs, OTF has {otf_count}"
            )


# --------------------------------------------------------------------------- #
# 9. Cross-weight consistency
# --------------------------------------------------------------------------- #

class TestCrossWeightConsistency:
    """
    All roman weights should share glyph order and cmap, and all italic weights
    should share glyph order and cmap. Roman vs italic may legitimately differ
    because the Inter sources have different glyph inventories.
    """

    @pytest.mark.parametrize("style", ["roman", "italic"])
    def test_same_glyph_order_within_style(self, style):
        group = [v for v in VARIANTS if v[3] == (style == "italic")]
        reference = None
        ref_suffix = None
        for _, suffix, _, _, _ in group:
            path = otf_path(suffix)
            if not path.exists():
                pytest.fail(f"Missing: {path}")
            font = open_font(path)
            order = font.getGlyphOrder()
            if reference is None:
                reference = order
                ref_suffix = suffix
            else:
                assert order == reference, (
                    f"Glyph order differs between {ref_suffix} and {suffix}"
                )

    @pytest.mark.parametrize("style", ["roman", "italic"])
    def test_same_cmap_within_style(self, style):
        group = [v for v in VARIANTS if v[3] == (style == "italic")]
        reference = None
        ref_suffix = None
        for _, suffix, _, _, _ in group:
            path = otf_path(suffix)
            if not path.exists():
                pytest.fail(f"Missing: {path}")
            font = open_font(path)
            cmap = set(font.getBestCmap().keys())
            if reference is None:
                reference = cmap
                ref_suffix = suffix
            else:
                missing = reference - cmap
                extra = cmap - reference
                assert not missing and not extra, (
                    f"cmap differs between {ref_suffix} and {suffix}: "
                    f"missing={len(missing)}, extra={len(extra)}"
                )
