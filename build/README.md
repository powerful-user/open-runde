# Open Runde Build Script

Generates Open Runde font files from [Inter](https://github.com/rsms/inter) source fonts by applying corner rounding to sharp convex corners.

## Prerequisites

- Python 3.9+
- Dependencies: `pip install -r requirements.txt`
- Inter v4.1 static TTF files in `build/inter-source/` (download from [Inter releases](https://github.com/rsms/inter/releases))

## Usage

```bash
# Generate missing weights (Thin, ExtraLight, Light, ExtraBold, Black)
python generate_fonts.py

# Generate specific weights
python generate_fonts.py --weights 100 300 900

# Regenerate all 9 weights
python generate_fonts.py --all

# Run calibration (compare generated Regular against existing)
python generate_fonts.py --calibrate
```

## How It Works

1. Loads Inter TTF files (UPM 2048, TrueType quadratic outlines)
2. Converts quadratic outlines to cubic bezier curves
3. Scales coordinates from UPM 2048 to 2816 (matching existing Open Runde)
4. Detects sharp convex corners between line segments
5. Replaces each corner with a cubic bezier fillet arc
6. Exports as OTF (CFF), WOFF, WOFF2, and .glyphs formats

### Corner Rounding Algorithm

The algorithm matches Glyphs Mini's RoundCorner filter:
- Only convex corners (exterior angle < 180°) between straight line segments are rounded
- The fillet radius increases with weight (60 for Thin → 120 for Black)
- The bezier kappa uses a factor of 7/6 above the standard circular arc approximation, creating slightly squarish "continuous" corners matching the Glyphs Mini style
- Adjacent corners on the same edge are checked for overlap and reduced proportionally

### Corner Radius Values

| Weight | Name       | Radius |
|--------|-----------|--------|
| 100    | Thin      | 60     |
| 200    | ExtraLight| 70     |
| 300    | Light     | 80     |
| 400    | Regular   | 90     |
| 500    | Medium    | 100    |
| 600    | SemiBold  | 110    |
| 700    | Bold      | 110    |
| 800    | ExtraBold | 115    |
| 900    | Black     | 120    |
