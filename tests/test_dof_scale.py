"""Regression checks for visible, scale-independent illustrative depth of field."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "webapp")]
import aim_render_scene as render
import pipeline

CAMERA = {"location": [183.08, -280, 413.32], "rotation_degrees": [27.927, 0, -21.83],
          "lens_mm": 100, "dof": {"enabled": True, "focus_point": [247.379, -78.375, 4.04],
                                  "aperture_fstop": 5.6}}


def pixel_blur(fstop, distance, object_depth, width=1600, lens=100, sensor=36):
    diameter = lens * .001 / fstop
    return diameter * abs(distance - object_depth) / (distance * object_depth) * lens / sensor * width


class DepthOfFieldScaleTests(unittest.TestCase):
    def test_matched_view_scaling_preserves_blur_and_aperture_adjustment(self):
        distance, depth = 460.4742, 492.6003
        native = pixel_blur(5.6, distance, depth)
        self.assertLess(native, .02)  # The reported failure is subpixel, despite DoF being on.
        reference = None
        for scale in (.001, 1, 100):
            effective = render.adaptive_dof_fstop(5.6, 100, distance * scale)
            blur = pixel_blur(effective, distance * scale, depth * scale)
            self.assertGreater(blur, 8)
            self.assertLess(blur, 12)
            if reference is not None:
                self.assertAlmostEqual(blur, reference)
            reference = blur
            stronger = render.adaptive_dof_fstop(2.8, 100, distance * scale)
            self.assertAlmostEqual(pixel_blur(stronger, distance * scale, depth * scale), 2 * blur)
        self.assertEqual(pixel_blur(effective, distance * scale, distance * scale), 0)

    def test_studio_preset_roundtrip_native_choice_and_legacy_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "preset.yaml"
            for choice in (None, True, False):
                camera = copy.deepcopy(CAMERA)
                if choice is not None:
                    camera["dof"]["adapt_to_view"] = choice
                preset = pipeline.preset("dof", camera, pipeline.options({}), [])
                path.write_text(yaml.safe_dump(preset))
                loaded = render.load_preset(path)
                self.assertEqual(loaded["camera"]["dof"]["adapt_to_view"], choice is not False)
                self.assertEqual(loaded["camera"]["dof"]["aperture_fstop"], 5.6)
                self.assertEqual(list(loaded["camera"]["dof"]["focus_point"]), CAMERA["dof"]["focus_point"])
            del preset["camera"]["dof"]["adapt_to_view"]
            path.write_text(yaml.safe_dump(preset))
            self.assertFalse(render.load_preset(path)["camera"]["dof"]["adapt_to_view"])
            for bad in ("true", 1, None):
                camera = copy.deepcopy(CAMERA);camera["dof"]["adapt_to_view"] = bad
                with self.assertRaises(ValueError):pipeline.camera(camera)
                preset["camera"]["dof"]["adapt_to_view"] = bad
                path.write_text(yaml.safe_dump(preset))
                with self.assertRaises(ValueError):render.load_preset(path)

    def test_degenerate_focus_and_cycles_floor_are_explicit(self):
        for distance in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):render.adaptive_dof_fstop(5.6, 100, distance)
        self.assertEqual(render.adaptive_dof_fstop(.1, 1, 1e9), 1e-5)


if __name__ == "__main__":
    unittest.main()
