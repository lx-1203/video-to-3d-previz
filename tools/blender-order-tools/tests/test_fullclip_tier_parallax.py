import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orderflow.fullclip_tier_parallax import check_scale


class StaticSeatTests(unittest.TestCase):
    def evidence(self, fit, held, chair_count=6):
        base = np.full((6, 36), 3.8)
        proposed = np.repeat(np.array([fit] * 3 + [held] * 3)[:, None], 36, axis=1)
        return check_scale(
            [base, proposed], np.arange(36) % chair_count, range(1, 7), 1.4, 900
        )

    def test_expansion_must_improve_fit_and_separate_views(self):
        self.assertEqual(self.evidence(1, 1)["state"], "source_supports_expansion")
        self.assertEqual(self.evidence(1, 5)["state"], "retain_registered_geometry")
        self.assertEqual(self.evidence(4, 2)["state"], "retain_registered_geometry")

    def test_low_baseline_or_single_part_is_inconclusive(self):
        self.assertEqual(self.evidence(3.6, 3.5)["state"], "retain_registered_geometry")
        self.assertEqual(
            self.evidence(1, 1, chair_count=1)["state"], "insufficient_static_witnesses"
        )

    def test_occlusion_cannot_be_scored_as_zero_pixel_error(self):
        values = np.full((2, 6, 36), 3.0)
        values[:, 3:, :33] = np.nan
        evidence = check_scale(values, np.arange(36), range(6), 1.55, 1280)
        self.assertEqual(evidence["state"], "insufficient_static_witnesses")
        self.assertNotIn("held_out", evidence)


if __name__ == "__main__":
    unittest.main()
