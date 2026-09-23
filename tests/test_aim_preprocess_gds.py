from __future__ import annotations

import unittest

from kfactory import kdb

from scripts.aim_preprocess_gds import (
    apply_region_offset,
    merge_region,
    region_topology,
    round_xy_region_preserving_topology,
)


class MetalVisualGdsRoundingTests(unittest.TestCase):
    DBU = 0.001

    def rounded(self, source, radius=0.2):
        return round_xy_region_preserving_topology(
            source, radius_um=radius, dbu=self.DBU, region_name="M1AM_RENDER"
        )

    def test_safe_shape_retains_the_exact_requested_offset_chain(self) -> None:
        source = kdb.Region(kdb.Box(0, 0, 10_000, 10_000))
        expected = source.dup()
        for distance in (-0.2, 0.4, -0.2):
            expected = apply_region_offset(
                expected,
                operation=dict(type="offset", distance=distance, join="round", tolerance=72),
                dbu=self.DBU, region_name="M1AM_RENDER",
            )
        rounded, stats = self.rounded(source)
        self.assertTrue((rounded ^ expected).is_empty())
        self.assertGreater((source ^ rounded).area(), 0)
        self.assertEqual(region_topology(rounded), (1, 0))
        self.assertEqual(stats["adapted_components"], 0)
        self.assertEqual(stats["min_radius_um"], 0.2)

    def test_shrinks_only_the_small_component(self) -> None:
        large = kdb.Region(kdb.Box(0, 0, 10_000, 10_000))
        small = kdb.Region(kdb.Box(20_000, 0, 20_300, 300))
        source = large + small
        before = source.dup()
        rounded, stats = self.rounded(source)
        large_expected, _ = self.rounded(large)
        small_expected, _ = self.rounded(small, 0.1)
        self.assertTrue((rounded ^ (large_expected + small_expected)).is_empty())
        self.assertTrue((source ^ before).is_empty())
        self.assertEqual(region_topology(rounded), (2, 0))
        self.assertEqual(stats["adapted_components"], 1)
        self.assertEqual(stats["min_radius_um"], 0.1)
        self.assertEqual(stats["max_radius_um"], 0.2)

    def test_preserves_a_narrow_connection(self) -> None:
        source = (kdb.Region(kdb.Box(0, 0, 1000, 1000))
                  + kdb.Region(kdb.Box(2000, 0, 3000, 1000))
                  + kdb.Region(kdb.Box(1000, 450, 2000, 550)))
        rounded, stats = self.rounded(source)
        self.assertEqual(region_topology(rounded), (1, 0))
        self.assertFalse((rounded & kdb.Region(kdb.Box(1400, 450, 1600, 550))).is_empty())
        self.assertEqual(stats["adapted_components"], 1)
        self.assertLess(stats["max_radius_um"], 0.2)

    def test_preserves_a_small_hole(self) -> None:
        hole = kdb.Region(kdb.Box(4900, 4900, 5100, 5100))
        source = kdb.Region(kdb.Box(0, 0, 10_000, 10_000)) - hole
        rounded, stats = self.rounded(source)
        self.assertEqual(region_topology(rounded), (1, 1))
        self.assertTrue((rounded & kdb.Region(kdb.Box(4990, 4990, 5010, 5010))).is_empty())
        self.assertEqual(stats["adapted_components"], 1)

    def test_independent_rounding_cannot_join_neighbours(self) -> None:
        corner = (kdb.Region(kdb.Box(0, 0, 5000, 1000))
                  + kdb.Region(kdb.Box(0, 0, 1000, 5000)))
        small = kdb.Region(kdb.Box(1010, 1010, 1080, 1080))
        corner_alone, _ = self.rounded(corner)
        small_alone, _ = self.rounded(small)
        self.assertEqual(region_topology(merge_region(corner_alone + small_alone)), (1, 0))
        rounded, stats = self.rounded(corner + small)
        self.assertEqual(region_topology(rounded), (2, 0))
        self.assertEqual(stats["adapted_components"], 2)

    def test_kissing_corners_remain_separate(self) -> None:
        source = (kdb.Region(kdb.Box(0, 0, 1000, 1000))
                  + kdb.Region(kdb.Box(1000, 1000, 2000, 2000)))
        rounded, _ = self.rounded(source)
        self.assertEqual(region_topology(rounded), (2, 0))

    def test_grid_limit_keeps_original_shape(self) -> None:
        source = kdb.Region(kdb.Box(0, 0, 1, 1))
        rounded, stats = self.rounded(source)
        self.assertTrue((rounded ^ source).is_empty())
        self.assertEqual(stats["adapted_components"], 1)
        self.assertEqual(stats["unrounded_components"], 1)
        self.assertEqual(stats["min_radius_um"], 0)

    def test_zero_radius_preserves_geometry(self) -> None:
        source = kdb.Region(kdb.Box(0, 0, 10_000, 10_000))
        rounded, stats = self.rounded(source, 0)
        self.assertTrue((source ^ rounded).is_empty())
        self.assertEqual(stats["area_before_dbu2"], stats["area_after_dbu2"])
        self.assertEqual(stats["adapted_components"], 0)

    def test_empty_region_remains_empty(self) -> None:
        rounded, stats = self.rounded(kdb.Region())
        self.assertTrue(rounded.is_empty())
        self.assertEqual(stats["components_after"], 0)

    def test_invalid_radius_is_still_rejected(self) -> None:
        source = kdb.Region(kdb.Box(0, 0, 1000, 1000))
        for radius, message in [(-0.1, "zero or greater"), (0.0001, "below one DBU")]:
            with self.subTest(radius=radius), self.assertRaisesRegex(ValueError, message):
                self.rounded(source, radius)


if __name__ == "__main__":
    unittest.main()
