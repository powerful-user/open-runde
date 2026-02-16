#!/usr/bin/env python3
"""
Generate Open Runde font files from Inter source fonts.

Usage:
    python generate_fonts.py                    # Generate all missing weights
    python generate_fonts.py --weights 100 200  # Generate specific weights
    python generate_fonts.py --calibrate        # Run calibration against existing weights
"""

import argparse
import math
import os
import sys
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import RecordingPen, DecomposingRecordingPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.fontBuilder import FontBuilder

from round_corners import (
    Segment, round_glyph_contours, vec_len, sub,
)

# Directories
BUILD_DIR = Path(__file__).parent
INTER_SOURCE_DIR = BUILD_DIR / "inter-source"
SRC_DIR = BUILD_DIR.parent / "src"
DESKTOP_DIR = SRC_DIR / "desktop"
WEB_DIR = SRC_DIR / "web"
GLYPHS_DIR = SRC_DIR / "glyphs"

# UPM scaling: Inter uses 2048, existing Open Runde uses 2816
INTER_UPM = 2048
TARGET_UPM = 2816
SCALE_FACTOR = TARGET_UPM / INTER_UPM  # 1.375

# Weight configuration
# GSCornerRadius values from existing files: Regular=90, Medium=100, SemiBold=110, Bold=110
WEIGHT_CONFIG = {
    100: {"name": "Thin",       "inter_file": "Inter-Thin.ttf",       "radius": 60},
    200: {"name": "ExtraLight", "inter_file": "Inter-ExtraLight.ttf", "radius": 70},
    300: {"name": "Light",      "inter_file": "Inter-Light.ttf",      "radius": 80},
    400: {"name": "Regular",    "inter_file": "Inter-Regular.ttf",    "radius": 90},
    500: {"name": "Medium",     "inter_file": "Inter-Medium.ttf",     "radius": 100},
    600: {"name": "SemiBold",   "inter_file": "Inter-SemiBold.ttf",   "radius": 110},
    700: {"name": "Bold",       "inter_file": "Inter-Bold.ttf",       "radius": 110},
    800: {"name": "ExtraBold",  "inter_file": "Inter-ExtraBold.ttf",  "radius": 115},
    900: {"name": "Black",      "inter_file": "Inter-Black.ttf",      "radius": 120},
}

# Italic weight configuration (same radii as roman)
ITALIC_WEIGHT_CONFIG = {
    100: {"name": "Thin Italic",       "suffix": "ThinItalic",       "inter_file": "Inter-ThinItalic.ttf",       "radius": 60},
    200: {"name": "ExtraLight Italic", "suffix": "ExtraLightItalic", "inter_file": "Inter-ExtraLightItalic.ttf", "radius": 70},
    300: {"name": "Light Italic",      "suffix": "LightItalic",      "inter_file": "Inter-LightItalic.ttf",      "radius": 80},
    400: {"name": "Italic",            "suffix": "Italic",           "inter_file": "Inter-Italic.ttf",           "radius": 90},
    500: {"name": "Medium Italic",     "suffix": "MediumItalic",     "inter_file": "Inter-MediumItalic.ttf",     "radius": 100},
    600: {"name": "SemiBold Italic",   "suffix": "SemiBoldItalic",   "inter_file": "Inter-SemiBoldItalic.ttf",   "radius": 110},
    700: {"name": "Bold Italic",       "suffix": "BoldItalic",       "inter_file": "Inter-BoldItalic.ttf",       "radius": 110},
    800: {"name": "ExtraBold Italic",  "suffix": "ExtraBoldItalic",  "inter_file": "Inter-ExtraBoldItalic.ttf",  "radius": 115},
    900: {"name": "Black Italic",      "suffix": "BlackItalic",      "inter_file": "Inter-BlackItalic.ttf",      "radius": 120},
}

# Only generate these by default (the missing weights)
DEFAULT_WEIGHTS = [100, 200, 300, 800, 900]

# All weights (for calibration or full regeneration)
ALL_WEIGHTS = [100, 200, 300, 400, 500, 600, 700, 800, 900]


def scale_point(pt, factor=SCALE_FACTOR):
    """Scale a point by the given factor."""
    return (round(pt[0] * factor), round(pt[1] * factor))


def extract_contours(font, glyph_name):
    """
    Extract contours from a TTF glyph as cubic bezier operations.

    Returns list of (start_point, [Segment, ...]) tuples.
    """
    glyf = font["glyf"]
    glyph = glyf[glyph_name]

    if glyph.numberOfContours == 0:
        return []

    if glyph.numberOfContours == -1:
        # Composite glyph - decompose via glyphSet to flatten components
        glyphSet = font.getGlyphSet()
        rec = DecomposingRecordingPen(glyphSet)
        glyphSet[glyph_name].draw(rec)
        return _parse_recording(rec)

    # Simple glyph - extract directly from coordinates
    coords = glyph.coordinates
    flags = glyph.flags
    end_pts = glyph.endPtsOfContours

    contours = []
    start_idx = 0

    for contour_idx in range(glyph.numberOfContours):
        end_idx = end_pts[contour_idx]
        contour_points = []
        contour_flags = []

        for i in range(start_idx, end_idx + 1):
            contour_points.append((coords[i][0], coords[i][1]))
            contour_flags.append(flags[i] & 1)  # on-curve bit

        contours.append(_convert_tt_contour(contour_points, contour_flags))
        start_idx = end_idx + 1

    return contours


def _convert_tt_contour(points, on_curve_flags):
    """
    Convert a TrueType contour (with quadratic b-splines) to cubic segments.

    Returns (start_point, [Segment, ...])
    """
    n = len(points)
    if n == 0:
        return ((0, 0), [])

    # First, resolve implied on-curve points between consecutive off-curve points
    resolved = []
    for i in range(n):
        resolved.append((points[i], on_curve_flags[i]))
        if not on_curve_flags[i] and not on_curve_flags[(i + 1) % n]:
            # Insert implied on-curve point midway
            next_pt = points[(i + 1) % n]
            mid = ((points[i][0] + next_pt[0]) / 2,
                   (points[i][1] + next_pt[1]) / 2)
            resolved.append((mid, True))  # implied on-curve

    # Find first on-curve point to use as start
    start_offset = 0
    for i, (pt, on) in enumerate(resolved):
        if on:
            start_offset = i
            break

    # Rotate so we start with an on-curve point
    resolved = resolved[start_offset:] + resolved[:start_offset]

    start_pt = resolved[0][0]
    segments = []

    i = 1
    while i < len(resolved):
        pt, on = resolved[i]
        if on:
            # Line segment
            segments.append(Segment('line', [pt]))
            i += 1
        else:
            # Quadratic curve: off-curve followed by on-curve
            off_pt = pt
            i += 1
            if i < len(resolved):
                end_pt = resolved[i][0]
                i += 1
            else:
                end_pt = start_pt

            # Convert quadratic to cubic
            # Quadratic: P0, P1(off), P2(on)
            # Cubic: P0, P0+2/3*(P1-P0), P2+2/3*(P1-P2), P2
            prev_pt = segments[-1].endpoint if segments else start_pt
            c1 = (prev_pt[0] + 2/3 * (off_pt[0] - prev_pt[0]),
                  prev_pt[1] + 2/3 * (off_pt[1] - prev_pt[1]))
            c2 = (end_pt[0] + 2/3 * (off_pt[0] - end_pt[0]),
                  end_pt[1] + 2/3 * (off_pt[1] - end_pt[1]))
            segments.append(Segment('curve', [c1, c2, end_pt]))

    # Add closing segment if the last point doesn't coincide with start
    if segments:
        last_pt = segments[-1].endpoint
        dist = math.sqrt((last_pt[0] - start_pt[0])**2 + (last_pt[1] - start_pt[1])**2)
        if dist > 0.5:
            segments.append(Segment('line', [start_pt]))

    return (start_pt, segments)


def _parse_recording(rec):
    """Parse a RecordingPen into contours."""
    contours = []
    current_start = None
    current_segments = []

    for op, args in rec.value:
        if op == 'moveTo':
            if current_start is not None:
                contours.append((current_start, current_segments))
            current_start = args[0]
            current_segments = []
        elif op == 'lineTo':
            current_segments.append(Segment('line', [args[0]]))
        elif op == 'curveTo':
            current_segments.append(Segment('curve', list(args)))
        elif op == 'qCurveTo':
            # Convert quadratic to cubic
            if len(args) == 2:
                prev = current_segments[-1].endpoint if current_segments else current_start
                off, end = args
                c1 = (prev[0] + 2/3 * (off[0] - prev[0]),
                      prev[1] + 2/3 * (off[1] - prev[1]))
                c2 = (end[0] + 2/3 * (off[0] - end[0]),
                      end[1] + 2/3 * (off[1] - end[1]))
                current_segments.append(Segment('curve', [c1, c2, end]))
            else:
                # Multiple off-curve points - handle each sub-segment
                prev = current_segments[-1].endpoint if current_segments else current_start
                off_points = list(args[:-1])
                end = args[-1]
                # Insert implied on-curve midpoints
                all_pts = [prev]
                for j in range(len(off_points)):
                    all_pts.append(off_points[j])
                    if j < len(off_points) - 1:
                        mid = ((off_points[j][0] + off_points[j+1][0]) / 2,
                               (off_points[j][1] + off_points[j+1][1]) / 2)
                        all_pts.append(mid)
                all_pts.append(end)
                # Convert pairs
                for j in range(0, len(all_pts) - 2, 2):
                    p0 = all_pts[j]
                    p1 = all_pts[j + 1]
                    p2 = all_pts[j + 2]
                    c1 = (p0[0] + 2/3 * (p1[0] - p0[0]),
                          p0[1] + 2/3 * (p1[1] - p0[1]))
                    c2 = (p2[0] + 2/3 * (p1[0] - p2[0]),
                          p2[1] + 2/3 * (p1[1] - p2[1]))
                    current_segments.append(Segment('curve', [c1, c2, p2]))
        elif op in ('closePath', 'endPath'):
            if current_start is not None and current_segments:
                # Add explicit closing segment if last endpoint != start
                last_pt = current_segments[-1].endpoint
                dist = math.sqrt((last_pt[0] - current_start[0])**2 +
                                 (last_pt[1] - current_start[1])**2)
                if dist > 0.5:
                    current_segments.append(Segment('line', [current_start]))
                contours.append((current_start, current_segments))
                current_start = None
                current_segments = []

    if current_start is not None:
        contours.append((current_start, current_segments))

    return contours


def scale_contours(contours, factor=SCALE_FACTOR):
    """Scale all contour coordinates by the given factor."""
    result = []
    for start, segments in contours:
        new_start = scale_point(start, factor)
        new_segments = []
        for seg in segments:
            new_pts = [scale_point(p, factor) for p in seg.points]
            new_segments.append(Segment(seg.type, new_pts))
        result.append((new_start, new_segments))
    return result


def contours_to_charstring(contours, width):
    """Convert contours to a CFF Type2 charstring."""
    pen = T2CharStringPen(width, None)
    for start, segments in contours:
        pen.moveTo(start)
        for seg in segments:
            if seg.type == 'line':
                pen.lineTo(seg.endpoint)
            elif seg.type == 'curve':
                pen.curveTo(*seg.points)
        pen.closePath()
    return pen.getCharString()


def process_font(weight: int, output_dir: Path = None, italic: bool = False):
    """
    Process an Inter font file to create an Open Runde variant.

    Returns the path to the generated OTF file.
    """
    if italic:
        config = ITALIC_WEIGHT_CONFIG[weight]
    else:
        config = WEIGHT_CONFIG[weight]
    inter_path = INTER_SOURCE_DIR / config["inter_file"]

    if not inter_path.exists():
        print(f"  ERROR: Inter source not found: {inter_path}")
        return None

    print(f"  Loading {inter_path.name}...")
    source_font = TTFont(str(inter_path))

    # Get glyph order and character map
    glyph_order = source_font.getGlyphOrder()
    cmap = source_font.getBestCmap()

    print(f"  Processing {len(glyph_order)} glyphs with radius={config['radius']}...")

    # Process each glyph
    charstrings = {}
    glyf = source_font["glyf"]
    hmtx = source_font["hmtx"]
    glyph_count = 0
    rounded_count = 0

    for glyph_name in glyph_order:
        glyph = glyf[glyph_name]
        width = hmtx[glyph_name][0]
        scaled_width = round(width * SCALE_FACTOR)

        if glyph.numberOfContours == 0 or glyph.isComposite():
            # Empty or composite glyph - create minimal charstring
            if glyph.isComposite():
                # Decompose composite and process
                try:
                    contours = extract_contours(source_font, glyph_name)
                    if contours:
                        scaled = scale_contours(contours)
                        rounded = round_glyph_contours(scaled, config["radius"])
                        charstrings[glyph_name] = contours_to_charstring(rounded, scaled_width)
                        glyph_count += 1
                        continue
                except Exception:
                    pass

            # Fallback: empty charstring
            pen = T2CharStringPen(scaled_width, None)
            charstrings[glyph_name] = pen.getCharString()
            continue

        # Simple glyph - extract, scale, round, convert
        try:
            contours = extract_contours(source_font, glyph_name)
            scaled = scale_contours(contours)
            rounded = round_glyph_contours(scaled, config["radius"])
            charstrings[glyph_name] = contours_to_charstring(rounded, scaled_width)

            # Check if any corners were actually rounded
            total_orig = sum(len(segs) for _, segs in scaled)
            total_new = sum(len(segs) for _, segs in rounded)
            if total_new > total_orig:
                rounded_count += 1

            glyph_count += 1
        except Exception as e:
            # Fallback: use unrounded scaled outlines
            try:
                contours = extract_contours(source_font, glyph_name)
                scaled = scale_contours(contours)
                charstrings[glyph_name] = contours_to_charstring(scaled, scaled_width)
                glyph_count += 1
            except Exception:
                pen = T2CharStringPen(scaled_width, None)
                charstrings[glyph_name] = pen.getCharString()

    print(f"  Processed {glyph_count} glyphs, {rounded_count} with rounded corners")

    # Build the output font
    print(f"  Building output font...")

    fb = FontBuilder(TARGET_UPM, isTTF=False)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(cmap)

    # Name table
    #
    # OpenType family grouping uses two levels:
    #   nameID 1/2  — "RIBBI" grouping: nameID 2 must be one of
    #                  Regular, Bold, Italic, Bold Italic.
    #                  Non-RIBBI weights fold into nameID 1
    #                  (e.g. "Open Runde Thin" + "Regular").
    #   nameID 16/17 — Typographic family: groups ALL weights under one
    #                   family name so Font Book shows a single entry
    #                   with a style dropdown.
    #
    typo_family = "Open Runde"

    if italic:
        ps_suffix = config["suffix"]        # e.g. "BoldItalic"
        typo_subfamily = config["name"]     # e.g. "Bold Italic"
    else:
        weight_name = config["name"]        # e.g. "Bold", "Regular"
        ps_suffix = weight_name
        typo_subfamily = weight_name

    ps_name = f"OpenRunde-{ps_suffix}"
    full_name = f"{typo_family} {typo_subfamily}" if typo_subfamily != "Regular" else typo_family

    # Determine RIBBI nameID 1/2 values
    is_bold = weight == 700
    ribbi_style = ""
    if is_bold and italic:
        ribbi_style = "Bold Italic"
    elif is_bold:
        ribbi_style = "Bold"
    elif italic:
        ribbi_style = "Italic"
    else:
        ribbi_style = "Regular"

    # For non-RIBBI weights, fold the weight name into nameID 1
    if weight in (400, 700):
        ribbi_family = typo_family  # "Open Runde"
    else:
        # e.g. "Open Runde Thin", "Open Runde SemiBold"
        ribbi_family = f"{typo_family} {config['name'].replace(' Italic', '').replace('Italic', '').strip() or config['name']}"
        if not ribbi_family.strip():
            ribbi_family = typo_family

    version_string = "Version 1.100"
    unique_id = f"1.100;ORND;{ps_name}"

    name_entries = {
        "familyName": ribbi_family,
        "styleName": ribbi_style,
        "uniqueFontIdentifier": unique_id,
        "fullName": full_name,
        "version": version_string,
        "psName": ps_name,
    }

    # Only add nameID 16/17 when they differ from nameID 1/2.
    # Redundant entries confuse some renderers including macOS Font Book.
    is_ribbi_weight = weight in (400, 700)
    if not is_ribbi_weight:
        name_entries["typographicFamily"] = typo_family
        name_entries["typographicSubfamily"] = typo_subfamily

    fb.setupNameTable(name_entries)

    # Metrics from source, scaled
    head = source_font["head"]
    os2 = source_font["OS/2"]
    hhea = source_font["hhea"]

    # macStyle: bit 0 = BOLD, bit 1 = ITALIC
    mac_style = 0
    if is_bold:
        mac_style |= 0x0001
    if italic:
        mac_style |= 0x0002

    head_kwargs = dict(
        unitsPerEm=TARGET_UPM,
        created=head.created,
        modified=head.modified,
        macStyle=mac_style,
    )
    fb.setupHead(**head_kwargs)

    # Scale horizontal metrics
    scaled_hmtx = {}
    for glyph_name in glyph_order:
        w, lsb = hmtx[glyph_name]
        scaled_hmtx[glyph_name] = (round(w * SCALE_FACTOR), round(lsb * SCALE_FACTOR))
    fb.setupHorizontalMetrics(scaled_hmtx)

    fb.setupHorizontalHeader(
        ascent=round(hhea.ascent * SCALE_FACTOR),
        descent=round(hhea.descent * SCALE_FACTOR),
    )

    # fsSelection: bit 0 = ITALIC, bit 5 = BOLD, bit 6 = REGULAR, bit 7 = USE_TYPO_METRICS
    fs_selection = 0x0080  # USE_TYPO_METRICS always set
    if italic:
        fs_selection |= 0x0001
    if is_bold:
        fs_selection |= 0x0020
    if weight == 400 and not italic:
        fs_selection |= 0x0040  # REGULAR

    os2_kwargs = dict(
        sTypoAscender=round(os2.sTypoAscender * SCALE_FACTOR),
        sTypoDescender=round(os2.sTypoDescender * SCALE_FACTOR),
        sTypoLineGap=round(os2.sTypoLineGap * SCALE_FACTOR),
        usWinAscent=round(os2.usWinAscent * SCALE_FACTOR),
        usWinDescent=abs(round(os2.usWinDescent * SCALE_FACTOR)),
        sxHeight=round(os2.sxHeight * SCALE_FACTOR),
        sCapHeight=round(os2.sCapHeight * SCALE_FACTOR),
        usWeightClass=weight,
        fsType=0,  # Installable embedding
        fsSelection=fs_selection,
    )
    fb.setupOS2(**os2_kwargs)

    post_kwargs = dict(
        isFixedPitch=0,
        underlinePosition=round(-200 * SCALE_FACTOR),
        underlineThickness=round(100 * SCALE_FACTOR),
    )
    if italic:
        post_kwargs["italicAngle"] = -9.4
    fb.setupPost(**post_kwargs)

    # Setup CFF
    fb.setupCFF(
        psName=ps_name,
        fontInfo={"FullName": full_name, "FamilyName": typo_family},
        charStringsDict=charstrings,
        privateDict={},
    )

    # Setup features from source if available
    # (simplified - just copy basic features)

    # Output paths
    if output_dir is None:
        output_dir = DESKTOP_DIR

    output_dir.mkdir(parents=True, exist_ok=True)
    file_suffix = config["suffix"] if italic else config["name"]
    otf_filename = f"OpenRunde-{file_suffix}.otf"
    otf_path = output_dir / otf_filename

    fb.font.save(str(otf_path))
    print(f"  Saved {otf_path}")

    return otf_path


def generate_woff(otf_path: Path):
    """Generate WOFF from OTF."""
    from fontTools.ttLib import TTFont as TTF
    font = TTF(str(otf_path))
    woff_path = WEB_DIR / otf_path.with_suffix(".woff").name
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    font.flavor = "woff"
    font.save(str(woff_path))
    print(f"  Saved {woff_path}")
    return woff_path


def generate_woff2(otf_path: Path):
    """Generate WOFF2 from OTF."""
    from fontTools.ttLib import TTFont as TTF
    font = TTF(str(otf_path))
    woff2_path = WEB_DIR / otf_path.with_suffix(".woff2").name
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    font.flavor = "woff2"
    font.save(str(woff2_path))
    print(f"  Saved {woff2_path}")
    return woff2_path


def generate_glyphs(otf_path: Path, weight: int, italic: bool = False):
    """Generate .glyphs file from OTF."""
    try:
        from glyphsLib import GSFont, GSGlyph, GSLayer, GSPath, GSNode, GSFontMaster
    except ImportError:
        print("  WARNING: glyphsLib not available, skipping .glyphs generation")
        return None

    config = ITALIC_WEIGHT_CONFIG[weight] if italic else WEIGHT_CONFIG[weight]
    font = TTFont(str(otf_path))

    gs_font = GSFont()
    gs_font.familyName = "Open Runde"
    gs_font.copyright = "Copyright 2023 The Inter Project Authors (https://github.com/rsms/inter). Rounded variant by Laurids Kern."
    gs_font.designer = "Rasmus Andersson / Laurids Kern"
    gs_font.designerURL = "https://lau.ke"
    gs_font.upm = TARGET_UPM
    gs_font.versionMajor = 1
    gs_font.versionMinor = 1

    master = GSFontMaster()
    master.id = "master01"
    master.ascender = round(font["hhea"].ascent)
    master.descender = round(font["hhea"].descent)
    master.capHeight = round(font["OS/2"].sCapHeight)
    master.xHeight = round(font["OS/2"].sxHeight)
    master.userData["GSCornerRadius"] = config["radius"]
    master.weightValue = weight
    if italic:
        master.italicAngle = -9.4
    gs_font.masters.append(master)

    # Add glyphs
    glyph_order = font.getGlyphOrder()
    cmap = font.getBestCmap()
    cff = font["CFF "]
    top_dict = cff.cff.topDictIndex[0]
    charstrings = top_dict.CharStrings
    hmtx = font["hmtx"]

    reverse_cmap = {}
    for unicode_val, glyph_name in cmap.items():
        if glyph_name not in reverse_cmap:
            reverse_cmap[glyph_name] = unicode_val

    for glyph_name in glyph_order:
        gs_glyph = GSGlyph()
        gs_glyph.name = glyph_name
        if glyph_name in reverse_cmap:
            gs_glyph.unicode = format(reverse_cmap[glyph_name], '04X')

        layer = GSLayer()
        layer.layerId = master.id
        layer.associatedMasterId = master.id
        layer.width = hmtx[glyph_name][0]

        # Extract paths from charstring
        try:
            cs = charstrings[glyph_name]
            rec = RecordingPen()
            charstrings[glyph_name].draw(rec)

            current_path = None
            for op, args in rec.value:
                if op == 'moveTo':
                    if current_path is not None:
                        layer.paths.append(current_path)
                    current_path = GSPath()
                    current_path.closed = True
                    x, y = args[0]
                    node = GSNode((x, y), type="line")
                    current_path.nodes.append(node)
                elif op == 'lineTo':
                    x, y = args[0]
                    node = GSNode((x, y), type="line")
                    current_path.nodes.append(node)
                elif op == 'curveTo':
                    for j, (x, y) in enumerate(args):
                        if j < len(args) - 1:
                            node = GSNode((x, y), type="offcurve")
                        else:
                            node = GSNode((x, y), type="curve")
                        current_path.nodes.append(node)
                elif op in ('closePath', 'endPath'):
                    if current_path is not None:
                        layer.paths.append(current_path)
                        current_path = None

            if current_path is not None:
                layer.paths.append(current_path)
        except Exception:
            pass

        gs_glyph.layers.append(layer)
        gs_font.glyphs.append(gs_glyph)

    file_suffix = config["suffix"] if italic else config["name"]
    glyphs_path = GLYPHS_DIR / f"OpenRunde-{file_suffix}.glyphs"
    GLYPHS_DIR.mkdir(parents=True, exist_ok=True)
    gs_font.save(str(glyphs_path))
    print(f"  Saved {glyphs_path}")
    return glyphs_path


def calibrate():
    """
    Run calibration: generate weight 400 and compare with existing Open Runde Regular.
    """
    print("\n=== CALIBRATION MODE ===")
    print("Generating Open Runde Regular from Inter Regular...")

    # Generate to a temp location
    import tempfile
    temp_dir = Path(tempfile.mkdtemp())
    otf_path = process_font(400, output_dir=temp_dir)

    if otf_path is None:
        print("Failed to generate calibration font")
        return

    # Compare with existing
    existing_path = DESKTOP_DIR / "OpenRunde-Regular.otf"
    if not existing_path.exists():
        print(f"Existing font not found at {existing_path}")
        return

    print(f"\nComparing generated vs existing Open Runde Regular...")
    generated = TTFont(str(otf_path))
    existing = TTFont(str(existing_path))

    # Compare a few key glyphs
    test_glyphs = ['L', 'H', 'O', 'A', 'V', 'a', 'e', 'o', 'n', 'one', 'zero']

    gen_cff = generated["CFF "].cff.topDictIndex[0].CharStrings
    ext_cff = existing["CFF "].cff.topDictIndex[0].CharStrings

    for glyph_name in test_glyphs:
        if glyph_name not in gen_cff or glyph_name not in ext_cff:
            continue

        gen_rec = RecordingPen()
        gen_cff[glyph_name].draw(gen_rec)

        ext_rec = RecordingPen()
        ext_cff[glyph_name].draw(ext_rec)

        gen_ops = len(gen_rec.value)
        ext_ops = len(ext_rec.value)

        # Count on-curve points
        gen_pts = sum(len(args) for _, args in gen_rec.value if args)
        ext_pts = sum(len(args) for _, args in ext_rec.value if args)

        match = "OK" if abs(gen_ops - ext_ops) <= 2 else "DIFF"
        print(f"  {glyph_name:10s}: gen={gen_ops:3d} ops ({gen_pts:3d} pts) | "
              f"ext={ext_ops:3d} ops ({ext_pts:3d} pts) [{match}]")

    # Detailed comparison of 'L' glyph
    print(f"\nDetailed 'L' comparison:")
    for label, cff in [("Generated", gen_cff), ("Existing", ext_cff)]:
        rec = RecordingPen()
        cff['L'].draw(rec)
        print(f"  {label}:")
        for op, args in rec.value:
            if args:
                pts = ", ".join(f"({a[0]:.0f},{a[1]:.0f})" for a in args)
                print(f"    {op}: {pts}")
            else:
                print(f"    {op}")

    # Cleanup
    import shutil
    shutil.rmtree(temp_dir)


def main():
    parser = argparse.ArgumentParser(description="Generate Open Runde font files")
    parser.add_argument("--weights", nargs="+", type=int,
                       help="Specific weights to generate")
    parser.add_argument("--all", action="store_true",
                       help="Generate all 9 weights (including existing 400-700)")
    parser.add_argument("--calibrate", action="store_true",
                       help="Run calibration against existing weights")
    parser.add_argument("--no-woff", action="store_true",
                       help="Skip WOFF generation")
    parser.add_argument("--no-glyphs", action="store_true",
                       help="Skip .glyphs generation")
    parser.add_argument("--roman", action="store_true",
                       help="Generate only roman (upright) variants")
    parser.add_argument("--italic", action="store_true",
                       help="Generate only italic variants")
    args = parser.parse_args()

    if args.calibrate:
        calibrate()
        return

    weights = args.weights or (ALL_WEIGHTS if args.all else DEFAULT_WEIGHTS)

    # Determine which styles to generate
    # If neither --roman nor --italic is specified, generate both
    do_roman = not args.italic or args.roman
    do_italic = not args.roman or args.italic

    styles = []
    if do_roman:
        styles.append(("roman", False))
    if do_italic:
        styles.append(("italic", True))

    style_labels = " + ".join(s[0] for s in styles)
    print(f"Generating Open Runde ({style_labels}) for weights: {weights}")
    print(f"Scale: {INTER_UPM} → {TARGET_UPM} UPM (factor {SCALE_FACTOR})")

    for style_label, is_italic in styles:
        config_dict = ITALIC_WEIGHT_CONFIG if is_italic else WEIGHT_CONFIG
        for weight in weights:
            if weight not in config_dict:
                print(f"\nERROR: Unknown weight {weight}")
                continue

            config = config_dict[weight]
            display_name = config["name"]
            print(f"\n{'='*60}")
            print(f"Weight {weight} ({display_name}), radius={config['radius']}")
            print(f"{'='*60}")

            otf_path = process_font(weight, italic=is_italic)
            if otf_path is None:
                continue

            if not args.no_woff:
                generate_woff(otf_path)
                generate_woff2(otf_path)

            if not args.no_glyphs:
                generate_glyphs(otf_path, weight, italic=is_italic)

    print(f"\n{'='*60}")
    print("Done! Generated fonts are in src/")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
