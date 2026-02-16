"""
Corner rounding algorithm for font outlines.

Implements the same rounding approach as Glyphs Mini's RoundCorner filter,
producing cubic bezier fillet arcs at sharp convex corners.
"""

import math
from typing import List, Tuple, Optional

Point = Tuple[float, float]


def normalize(v: Point) -> Point:
    length = math.sqrt(v[0] ** 2 + v[1] ** 2)
    if length < 1e-10:
        return (0.0, 0.0)
    return (v[0] / length, v[1] / length)


def vec_len(v: Point) -> float:
    return math.sqrt(v[0] ** 2 + v[1] ** 2)


def dot(a: Point, b: Point) -> float:
    return a[0] * b[0] + a[1] * b[1]


def cross(a: Point, b: Point) -> float:
    return a[0] * b[1] - a[1] * b[0]


def sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1])


def add(a: Point, b: Point) -> Point:
    return (a[0] + b[0], a[1] + b[1])


def scale(v: Point, s: float) -> Point:
    return (v[0] * s, v[1] * s)


# Glyphs Mini uses a kappa factor ~16.7% larger than the standard circular arc
# approximation, creating slightly squarish "continuous" corners.
# Measured from existing Open Runde files: for 90° corners, kappa = 58/90 ≈ 0.6444
# Standard circular: kappa = (4/3)*tan(π/8) ≈ 0.5523
# Ratio ≈ 7/6
GLYPHS_KAPPA_FACTOR = 7.0 / 6.0


def compute_bezier_kappa(arc_angle: float) -> float:
    """Compute bezier kappa for a circular arc, adjusted with Glyphs Mini factor."""
    k_circle = (4.0 / 3.0) * math.tan(arc_angle / 4.0)
    return k_circle * GLYPHS_KAPPA_FACTOR


def contour_winding(points: List[Point]) -> float:
    """Signed area via shoelace. Positive = CCW, Negative = CW."""
    area = 0.0
    n = len(points)
    for i in range(n):
        j = (i + 1) % n
        area += points[i][0] * points[j][1]
        area -= points[j][0] * points[i][1]
    return area / 2.0


class Segment:
    """A path segment: line or cubic curve."""
    def __init__(self, seg_type: str, points: List[Point]):
        self.type = seg_type
        self.points = points

    @property
    def endpoint(self) -> Point:
        return self.points[-1]


def _compute_fillet(P, v1, v2, offset, theta):
    """Compute fillet tangent points and bezier control points."""
    T1 = add(P, scale(v1, offset))
    T2 = add(P, scale(v2, offset))
    arc_angle = math.pi - theta
    kappa = compute_bezier_kappa(arc_angle)
    C1 = add(T1, scale(v1, -kappa * offset))
    C2 = add(T2, scale(v2, -kappa * offset))
    return {'T1': T1, 'C1': C1, 'C2': C2, 'T2': T2}


def round_contour(start: Point, segments: List[Segment], radius: float,
                  angle_threshold: float = 170.0) -> Tuple[Point, List[Segment]]:
    """
    Apply corner rounding to a closed contour.
    Only rounds sharp convex corners between line segments.
    """
    if len(segments) < 2:
        return start, segments

    n = len(segments)

    # Corner points. corners[i] is the start of segment i.
    corners = [start] + [seg.endpoint for seg in segments]
    # corners has n+1 entries; corners[n] ≈ corners[0] for closed path.

    # Determine winding
    on_curve_pts = corners[:n]
    winding = contour_winding(on_curve_pts)
    is_ccw = winding > 0

    # Phase 1: Identify roundable corners and compute desired offsets.
    # corner_data[i] = {'v1', 'v2', 'theta', 'desired_offset'} or None
    corner_data = [None] * n

    for i in range(n):
        seg_in = segments[(i - 1) % n]
        seg_out = segments[i]

        if seg_in.type != 'line' or seg_out.type != 'line':
            continue

        P = corners[i]
        A = corners[(i - 1) % n]
        B = corners[(i + 1) % n]

        v1 = normalize(sub(A, P))
        v2 = normalize(sub(B, P))
        len_in = vec_len(sub(A, P))
        len_out = vec_len(sub(B, P))

        if len_in < 1e-6 or len_out < 1e-6:
            continue

        d = max(-1.0, min(1.0, dot(v1, v2)))
        theta = math.acos(d)
        theta_deg = math.degrees(theta)

        if theta_deg > angle_threshold or theta_deg < 10:
            continue

        c = cross(v1, v2)
        is_convex = (c < 0) if is_ccw else (c > 0)
        if not is_convex:
            continue

        half_angle = theta / 2.0
        if half_angle < 1e-6:
            continue
        offset = radius / math.tan(half_angle)

        corner_data[i] = {
            'v1': v1, 'v2': v2, 'theta': theta,
            'desired_offset': offset,
            'len_in': len_in, 'len_out': len_out,
        }

    # Phase 2: Resolve overlaps.
    # For each edge (segment i, from corner i to corner (i+1)%n):
    #   - corner_data[i] consumes from the START of the edge (via T2, offset along outgoing)
    #   - corner_data[(i+1)%n] consumes from the END of the edge (via T1, offset along incoming)
    # If both exist, their offsets must sum to <= edge_length.

    actual_offsets = [0.0] * n
    for i in range(n):
        if corner_data[i]:
            actual_offsets[i] = corner_data[i]['desired_offset']

    for i in range(n):
        j = (i + 1) % n
        if corner_data[i] is None and corner_data[j] is None:
            continue

        edge_len = vec_len(sub(corners[j], corners[i]))
        if edge_len < 1e-6:
            continue

        # How much does corner i eat from this edge's start?
        eat_start = actual_offsets[i] if corner_data[i] else 0.0
        # How much does corner j eat from this edge's end?
        eat_end = actual_offsets[j] if corner_data[j] else 0.0

        total = eat_start + eat_end
        if total > edge_len * 0.95:
            # Scale both down proportionally, leaving 5% gap minimum
            available = edge_len * 0.95
            if total > 0:
                ratio = available / total
                if corner_data[i]:
                    actual_offsets[i] = min(actual_offsets[i], eat_start * ratio)
                if corner_data[j]:
                    actual_offsets[j] = min(actual_offsets[j], eat_end * ratio)

    # Phase 3: Build round_info with final offsets.
    round_info = [None] * n
    for i in range(n):
        if corner_data[i] is None:
            continue
        offset = actual_offsets[i]
        if offset < 1.0:
            continue
        cd = corner_data[i]
        round_info[i] = _compute_fillet(
            corners[i], cd['v1'], cd['v2'], offset, cd['theta']
        )

    # Phase 4: Rebuild contour.
    new_segs = []
    for i in range(n):
        seg = segments[i]
        ri_start = round_info[i]
        ri_end = round_info[(i + 1) % n]

        if seg.type == 'line':
            eff_start = ri_start['T2'] if ri_start else corners[i]
            eff_end = ri_end['T1'] if ri_end else corners[(i + 1) % n]

            if vec_len(sub(eff_end, eff_start)) > 0.5:
                new_segs.append(Segment('line', [eff_end]))

            if ri_end:
                new_segs.append(Segment('curve', [
                    ri_end['C1'], ri_end['C2'], ri_end['T2']
                ]))
        else:
            new_segs.append(seg)
            if ri_end:
                new_segs.append(Segment('curve', [
                    ri_end['C1'], ri_end['C2'], ri_end['T2']
                ]))

    new_start = round_info[0]['T2'] if round_info[0] else start
    return new_start, new_segs


def round_glyph_contours(contours: list, radius: float) -> list:
    """Round corners in a list of contours."""
    result = []
    for start, segments in contours:
        new_start, new_segments = round_contour(start, segments, radius)
        result.append((new_start, new_segments))
    return result
