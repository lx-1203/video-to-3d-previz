"""Resolve monocular set scale using a static source line with body-crossing witnesses."""

import numpy as np

if __package__:
    from .fullclip_guardrail_fit import fit, line_pixel
else:
    from fullclip_guardrail_fit import fit, line_pixel


def gauge_for_samples(samples, width, height):
    if len(samples) < 15:
        return None
    member = fit(samples, width, height, constrain_depth=False)
    if member is None or member["p90_px"] > height * 0.017:
        return None
    start, end = member["ends"]
    ratios = []
    per_view = []
    for sample in samples:
        if not sample["front"]:
            continue
        columns, z = np.asarray(sample["front"]).T
        _, depth, _ = line_pixel(start, end - start, sample["projection"], columns)
        valid = (depth > 0) & (z > 0)
        if valid.sum() < 2:
            continue
        local = z[valid] / depth[valid]
        ratios.extend(local.tolist())
        per_view.append(float(np.percentile(local, 10)))
    if len(per_view) < 15 or len(ratios) < 40:
        return None
    ratios = np.asarray(ratios)
    before = float(np.mean(ratios > 1))
    if before > 0.45 or np.median(per_view) >= 0.94:
        return None
    factor = float(0.96 * np.percentile(per_view, 2))
    after = float(np.mean(ratios > factor))
    if not 0.15 < factor < 0.94 or after < 0.95:
        return None
    return dict(
        factor=factor,
        crossing_rays=len(ratios),
        crossing_views=len(per_view),
        reprojection_median_px=member["median_px"],
        reprojection_p90_px=member["p90_px"],
        front_fraction_before=before,
        front_fraction_after=after,
        depth_ratio_quantiles=dict(
            zip(
                ("p02", "p10", "p50", "p90"),
                np.percentile(ratios, [2, 10, 50, 90]).tolist(),
            )
        ),
    )
