from __future__ import annotations

import unittest

from kfactory import kdb

from scripts.aim_preprocess_gds import (
    region_topology,
    round_xy_region_preserving_topology,
)


class MetalVisualGdsRoundingTests(unittest.TestCase):
    DBU = 0.001

    def test_rounds_xy_without_changing_final_topology(self) -> None:
        source = kdb.Region(kdb.Box(0, 0, 10_000, 10_000))

        rounded, stats = round_xy_region_preserving_topology(
            source,
            radius_um=0.2,
            dbu=self.DBU,
            region_name="M1AM_RENDER",
        )

        self.assertEqual(region_topology(source), (1, 0))
        self.assertEqual(region_topology(rounded), (1, 0))
        self.assertGreater((source ^ rounded).area(), 0)
        self.assertEqual(stats["components_before"], 1)
        self.assertEqual(stats["components_after"], 1)
        self.assertEqual(stats["holes_before"], 0)
        self.assertEqual(stats["holes_after"], 0)

    def test_refuses_a_radius_that_deletes_a_component(self) -> None:
        source = kdb.Region(kdb.Box(0, 0, 300, 300))

        with self.assertRaisesRegex(
            ValueError,
            r"M2AM_RENDER XY rounding changed topology: components 1->0",
        ):
            round_xy_region_preserving_topology(
                source,
                radius_um=0.2,
                dbu=self.DBU,
                region_name="M2AM_RENDER",
            )

    def test_zero_radius_preserves_geometry(self) -> None:
        source = kdb.Region(kdb.Box(0, 0, 10_000, 10_000))

        rounded, stats = round_xy_region_preserving_topology(
            source,
            radius_um=0.0,
            dbu=self.DBU,
            region_name="MLAM_RENDER",
        )

        self.assertTrue((source ^ rounded).is_empty())
        self.assertEqual(stats["area_before_dbu2"], stats["area_after_dbu2"])


if __name__ == "__main__":
    unittest.main()
