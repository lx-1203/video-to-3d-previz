import unittest
import colorsys
from orderflow import palette


class PaletteTests(unittest.TestCase):
    @staticmethod
    def display(linear):
        return [12.92*c if c <= 0.0031308 else 1.055*c**(1/2.4)-0.055 for c in linear[:3]]

    def test_complement_rotates_display_hue_for_known_srgb_color(self):
        # Tabulated linear form of sRGB (.8,.4,.2): display hue is exactly 20deg,
        # so its complementary display hue must be 200deg, independent of V/S.
        body = [0.6038273388553378, 0.13286832155381798, 0.033104766570885055, 1]
        result = palette.complement(body)
        hue, saturation, value = colorsys.rgb_to_hsv(*self.display(result))
        self.assertAlmostEqual(hue*360, 200, places=6)
        self.assertGreaterEqual(value, 0.9-1e-10)

    def test_transfer_boundaries_and_round_trip(self):
        self.assertEqual(palette._linear_to_srgb(0), 0)
        self.assertAlmostEqual(palette._linear_to_srgb(1), 1)
        self.assertAlmostEqual(palette._linear_to_srgb(0.0031308), 0.040449936)
        self.assertAlmostEqual(palette._srgb_to_linear(0.04045), 0.0031308049535603713)
        self.assertAlmostEqual(palette._srgb_to_linear(0.5), 0.21404114048223255)
        for value in (0, 0.001, 0.0031308, 0.2, 0.6, 1):
            self.assertAlmostEqual(palette._srgb_to_linear(palette._linear_to_srgb(value)),value,places=7)

    def test_low_saturation_uses_documented_stable_colored_fallback(self):
        black = palette.complement([0,0,0,1])
        gray = palette.complement([0.4,0.4,0.4,1])
        near_gray = palette.complement([0.4,0.40001,0.4,1])
        self.assertEqual(black,gray)
        self.assertEqual(gray,near_gray)
        for actual, expected in zip(self.display(gray), (1.0,0.45,0.08)):
            self.assertAlmostEqual(actual,expected)

    def test_explicit_marker_linear_rgba_is_preserved(self):
        document=self.document()
        document['actors']['actor_a']['marker_color']=[0.2,0.1,0.8,0.7]
        self.assertEqual(palette.validate(document)['actors']['actor_a']['marker_color'],[0.2,0.1,0.8,0.7])
    def document(self):
        return {'schema_version': 1, 'actors': {'actor_a': {'body_color': [0.2, 0.4, 0.6, 1], 'marker_color': 'complement', 'objects': [{'name': 'ActorMesh', 'body_slots': [0], 'marker_slots': [1]}]}}}

    def test_complement_is_visible_and_normalized(self):
        for color in ([0, 0, 0, 1], [0.001, 0.001, 0.001, 1], [0.8, 0.1, 0.1, 1]):
            result = palette.complement(color)
            self.assertEqual(len(result), 4)
            self.assertGreaterEqual(max(result[:3]), 0.65)
            self.assertGreaterEqual(max(result[:3])-min(result[:3]), 0.35)
            self.assertTrue(all(0 <= channel <= 1 for channel in result))

    def test_validate_rejects_overlap_duplicates_and_invalid_colors(self):
        self.assertEqual(palette.validate(self.document())['actors']['actor_a']['objects'][0]['name'], 'ActorMesh')
        for mutate in (
            lambda d: d['actors']['actor_a'].update(body_color=[float('nan'), 0, 0, 1]),
            lambda d: d['actors']['actor_a']['objects'][0].update(marker_slots=[0]),
            lambda d: d['actors']['actor_a']['objects'][0].update(body_slots=[True]),
            lambda d: d['actors'].update(actor_b=d['actors']['actor_a']),
        ):
            document = self.document()
            mutate(document)
            with self.assertRaises(ValueError):
                palette.validate(document)
