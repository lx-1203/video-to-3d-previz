import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orderflow.fullclip_guardrail_material import material_matches_cloth
from orderflow.fullclip_guardrail_material_bind import augment
from orderflow.fullclip_guardrail_motion import edge_support


class SurfaceMaterialTest(unittest.TestCase):
    def test_no_rendered_occlusion_has_zero_change(self):
        with (
            patch(
                "orderflow.fullclip_guardrail_material_bind.collect_pairs",
                return_value=[],
            ),
            patch("subprocess.run") as worker,
        ):
            data = {"source_sha256": "test"}
            self.assertIsNone(augment(None, None, {}, {}, data, {}))
            worker.assert_not_called()
            self.assertEqual(data, {"source_sha256": "test"})

    def test_verified_surface_contradiction_vs_same_material_or_a_crossing(self):
        coat = np.array([24.0, 125.0, 132.0])
        chair = np.array([104.0, 108.0, 153.0])
        continuous = np.vstack((coat + 2, coat + 3, coat))
        self.assertTrue(material_matches_cloth(coat, chair, [continuous]))
        self.assertFalse(material_matches_cloth(chair, chair + 4, [continuous]))
        broken = np.vstack((coat, chair, coat))
        self.assertFalse(material_matches_cloth(coat, chair, [broken]))

    def test_real_foreground_rail_stays_in_front(self):
        im = np.full((120, 200), 40, np.uint8)
        im[56:65, :] = 127
        crossed, interrupted = edge_support(im, (100, 60), 50.0)
        self.assertTrue(crossed)
        self.assertFalse(interrupted)
        im[45:75, 81:119] = 40
        crossed, _ = edge_support(im, (100, 60), 50.0)
        self.assertFalse(crossed)
