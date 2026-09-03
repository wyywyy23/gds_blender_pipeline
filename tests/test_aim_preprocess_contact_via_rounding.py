from __future__ import annotations

import unittest
from unittest.mock import patch

import gdstk
from kfactory import kdb

from scripts.aim_preprocess_gds import (
    presentation_xy_fillet_radius_for_layer,
    region_topology,
    round_xy_region_preserving_topology,
)


class ContactViaVisualGdsRoundingTests(unittest.TestCase):
    DBU = 0.001

    def test_assigns_independent_layer_group_radii(self) -> None:
        for layer_name in ("M1AM_RENDER", "M2AM_RENDER", "MLAM_RENDER"):
            self.assertEqual(
                presentation_xy_fillet_radius_for_layer(
                    layer_name,
                    metal_radius_um=0.2,
                    contact_via_radius_um=0.1,
                ),
                0.2,
            )
        for layer_name in ("CBAM_RENDER", "V1AM_RENDER", "VAAM_RENDER"):
            self.assertEqual(
                presentation_xy_fillet_radius_for_layer(
                    layer_name,
                    metal_radius_um=0.2,
                    contact_via_radius_um=0.1,
                ),
                0.1,
            )
        self.assertEqual(
            presentation_xy_fillet_radius_for_layer(
                "FNAM_RENDER",
                metal_radius_um=0.2,
                contact_via_radius_um=0.1,
            ),
            0.0,
        )

    def test_contact_via_radius_uses_requested_gdstk_chain(self) -> None:
        source = kdb.Region(kdb.Box(0, 0, 10_000, 10_000))
        real_offset = gdstk.offset

        with patch(
            "scripts.aim_preprocess_gds.gdstk.offset", wraps=real_offset
        ) as offset:
            rounded, _ = round_xy_region_preserving_topology(
                source,
                radius_um=0.1,
                dbu=self.DBU,
                region_name="CBAM_RENDER",
            )

        self.assertEqual(
            [call.args[1] for call in offset.call_args_list],
            [-0.1, 0.2, -0.1],
        )
        self.assertEqual(region_topology(rounded), (1, 0))

    def test_rejects_negative_group_radius(self) -> None:
        with self.assertRaisesRegex(ValueError, r"Contact/via XY rounding"):
            presentation_xy_fillet_radius_for_layer(
                "CBAM_RENDER",
                metal_radius_um=0.2,
                contact_via_radius_um=-0.1,
            )


if __name__ == "__main__":
    unittest.main()
