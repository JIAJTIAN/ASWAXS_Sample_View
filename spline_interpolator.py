# spline_interpolator.py — Position path interpolation, no external dependencies.
# catmull_rom_resample() — centripetal Catmull-Rom smooth spline, then equal arc-length resample.
#   Straight sections stay straight; curved sections produce smooth curves.
# resample_polyline()    — linear arc-length resampling (kept as internal fallback for 2-point paths).
# No LAPACK: only numpy array arithmetic and numpy.interp.

import numpy as np


# ── Shared helper ──────────────────────────────────────────────────────────────

def _pts_from_positions(positions: list) -> np.ndarray:
    return np.array(
        [[float(p.get("x", 0)), float(p.get("y", 0)), float(p.get("z", 0))]
         for p in positions],
        dtype=float,
    )


def _arclen_resample(dense: np.ndarray, spacing: float) -> list:
    """Resample a dense point array at equal arc-length spacing."""
    diffs    = np.diff(dense, axis=0)
    seg_lens = np.sqrt((diffs ** 2).sum(axis=1))
    cum_len  = np.concatenate([[0.0], np.cumsum(seg_lens)])
    total    = cum_len[-1]

    if total < 1e-12:
        return [{"x": float(dense[0, 0]), "y": float(dense[0, 1]), "z": float(dense[0, 2])}]

    sample_lens = np.arange(0.0, total + spacing * 0.5, spacing)
    sample_lens = np.clip(sample_lens, 0.0, total)

    x_out = np.interp(sample_lens, cum_len, dense[:, 0])
    y_out = np.interp(sample_lens, cum_len, dense[:, 1])
    z_out = np.interp(sample_lens, cum_len, dense[:, 2])

    return [{"x": float(x), "y": float(y), "z": float(z)}
            for x, y, z in zip(x_out, y_out, z_out)]


# ── Linear resampling (internal fallback for 2-point paths) ───────────────────

def resample_polyline(positions: list, spacing: float) -> list:
    """Resample a polyline at equal arc-length spacing (straight segments, no smoothing)."""
    if spacing <= 0 or len(positions) < 2:
        return list(positions)

    pts = _pts_from_positions(positions)
    return _arclen_resample(pts, spacing)


# ── Mode 2: Smooth — centripetal Catmull-Rom spline ───────────────────────────

def catmull_rom_resample(positions: list, spacing: float,
                         samples_per_segment: int = 300) -> list:
    """
    Fit a centripetal Catmull-Rom spline through all waypoints, then resample at
    equal arc-length spacing.

    Centripetal parameterisation (alpha=0.5) prevents cusps and self-intersections
    at sharp corners, so the curve naturally handles both straight channel sections
    and tight bends without overshooting.

    positions            — list of {x, y, z} dicts (waypoints)
    spacing              — desired arc-length spacing between output points (mm)
    samples_per_segment  — dense samples per input segment before arc-length resampling;
                           higher = more accurate arc-length but slower (300 is fine for
                           beamline use: < 1 ms for < 100 waypoints)
    """
    if spacing <= 0 or len(positions) < 2:
        return list(positions)

    pts = _pts_from_positions(positions)
    N   = len(pts)

    if N == 2:
        # Only two points → Catmull-Rom degenerates to a straight line anyway
        return resample_polyline(positions, spacing)

    # Phantom end-points so the spline passes through the first and last real points
    p_start = pts[0]  + (pts[0]  - pts[1])   # mirror of pts[1]  around pts[0]
    p_end   = pts[-1] + (pts[-1] - pts[-2])   # mirror of pts[-2] around pts[-1]
    all_pts = np.vstack([p_start, pts, p_end])  # shape (N+2, 3)

    dense_list = []

    for i in range(1, len(all_pts) - 2):          # i iterates over real points
        P0, P1, P2, P3 = all_pts[i-1], all_pts[i], all_pts[i+1], all_pts[i+2]

        # Centripetal knot spacing: t_{k+1} = t_k + |P_{k+1} - P_k|^0.5
        def _knot(a, b):
            d = np.sqrt(np.sum((b - a) ** 2))   # euclidean — safe, no LAPACK
            return d ** 0.5

        t0 = 0.0
        t1 = t0 + _knot(P0, P1)
        t2 = t1 + _knot(P1, P2)
        t3 = t2 + _knot(P2, P3)

        # Degenerate segment (coincident points) → just emit P1
        if abs(t2 - t1) < 1e-12:
            dense_list.append(P1)
            continue

        # Include endpoint only on the very last segment to avoid duplicate
        endpoint = (i == len(all_pts) - 3)
        ts = np.linspace(t1, t2, samples_per_segment, endpoint=endpoint)

        # Barry-Goldman algorithm (numerically stable recursive interpolation)
        # Each row of ts produces one 3-D point; vectorised over ts.
        t = ts[:, None]               # (S, 1) for broadcasting with (3,) row vectors

        def _lerp(ta, tb, Pa, Pb):
            # ta, tb: scalars; Pa, Pb: (3,); t: (S,1) → returns (S, 3)
            if abs(tb - ta) < 1e-12:
                return np.tile(Pa, (len(ts), 1))
            return ((tb - t) * Pa + (t - ta) * Pb) / (tb - ta)

        A1 = _lerp(t0, t1, P0, P1)
        A2 = _lerp(t1, t2, P1, P2)
        A3 = _lerp(t2, t3, P2, P3)
        B1 = _lerp(t0, t2, A1, A2)
        B2 = _lerp(t1, t3, A2, A3)
        C  = _lerp(t1, t2, B1, B2)   # (S, 3) — points on the segment

        dense_list.append(C)

    dense = np.vstack(dense_list)     # (total_samples, 3)
    return _arclen_resample(dense, spacing)
