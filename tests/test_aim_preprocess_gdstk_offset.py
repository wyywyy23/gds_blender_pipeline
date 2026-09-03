from __future__ import annotations

import unittest
from unittest.mock import patch

import gdstk
from kfactory import kdb

from scripts.aim_preprocess_gds import (
    region_topology,
    round_xy_region_preserving_topology,
)


class GdstkOffsetTests(unittest.TestCase):
    DBU = 0.001

    def test_round_chain_uses_direct_gdstk_offsets(self) -> None:
        source = kdb.Region(kdb.Box(0, 0, 10_000, 10_000))
        real_offset = gdstk.offset

        with patch(
            "scripts.aim_preprocess_gds.gdstk.offset", wraps=real_offset
        ) as offset:
            rounded, _ = round_xy_region_preserving_topology(
                source,
                radius_um=0.2,
                dbu=self.DBU,
                region_name="M1AM_RENDER",
            )

        self.assertEqual(offset.call_count, 3)
        self.assertEqual(
            [call.args[1] for call in offset.call_args_list],
            [-0.2, 0.4, -0.2],
        )
        for call in offset.call_args_list:
            self.assertEqual(call.kwargs["join"], "round")
            self.assertEqual(call.kwargs["tolerance"], 72)
            self.assertEqual(call.kwargs["precision"], self.DBU)
            self.assertTrue(call.kwargs["use_union"])
        self.assertEqual(region_topology(rounded), (1, 0))

    def test_round_chain_preserves_a_hole(self) -> None:
        source = kdb.Region(kdb.Box(0, 0, 10_000, 10_000))
        source -= kdb.Region(kdb.Box(4_000, 4_000, 6_000, 6_000))

        rounded, stats = round_xy_region_preserving_topology(
            source,
            radius_um=0.2,
            dbu=self.DBU,
            region_name="M2AM_RENDER",
        )

        self.assertEqual(region_topology(source), (1, 1))
        self.assertEqual(region_topology(rounded), (1, 1))
        self.assertEqual(stats["components_before"], 1)
        self.assertEqual(stats["components_after"], 1)
        self.assertEqual(stats["holes_before"], 1)
        self.assertEqual(stats["holes_after"], 1)

    def test_rejects_invalid_gdstk_tolerance(self) -> None:
        source = kdb.Region(kdb.Box(0, 0, 10_000, 10_000))

        with self.assertRaisesRegex(ValueError, r"integer >= 2"):
            round_xy_region_preserving_topology(
                source,
                radius_um=0.2,
                dbu=self.DBU,
                region_name="MLAM_RENDER",
                tolerance=1,
            )


if __name__ == "__main__":
    unittest.main()
