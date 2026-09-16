from __future__ import annotations

import unittest

from scripts.aim_build_blender_scene import parse_args
from scripts.aim_preprocess_gds import optional_max_polygon_vertices


class PresentationZBevelArgumentTests(unittest.TestCase):
    def test_zero_disables_polygon_fracture(self) -> None:
        self.assertIsNone(optional_max_polygon_vertices("0"))
        self.assertEqual(optional_max_polygon_vertices("256"), 256)

    def test_rejects_shader_bevel_with_fractured_visual_gds(self) -> None:
        with self.assertRaises(SystemExit):
            parse_args(
                [
                    "--presentation-metal-z-bevel-width-um",
                    "0.05",
                    "--source-max-polygon-vertices",
                    "256",
                ]
            )

    def test_allows_shader_bevel_with_unfractured_visual_gds(self) -> None:
        args = parse_args(
            [
                "--presentation-metal-z-bevel-width-um",
                "0.05",
                "--source-max-polygon-vertices",
                "0",
            ]
        )
        self.assertEqual(args.source_max_polygon_vertices, 0)
        self.assertEqual(args.presentation_metal_z_bevel_sidecar, [])

    def test_allows_sidecar_cap_shader_with_fractured_visual_gds(self) -> None:
        args = parse_args(
            [
                "--presentation-metal-z-bevel-width-um",
                "0.05",
                "--presentation-metal-z-bevel-sidecar",
                "m2.npz",
                "--source-max-polygon-vertices",
                "256",
            ]
        )
        self.assertEqual(args.source_max_polygon_vertices, 256)
        self.assertEqual(len(args.presentation_metal_z_bevel_sidecar), 1)

    def test_rejects_sidecar_without_positive_bevel(self) -> None:
        with self.assertRaises(SystemExit):
            parse_args(
                [
                    "--presentation-metal-z-bevel-sidecar",
                    "m2.npz",
                    "--source-max-polygon-vertices",
                    "256",
                ]
            )


if __name__ == "__main__":
    unittest.main()
