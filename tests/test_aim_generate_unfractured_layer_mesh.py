from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from kfactory import kdb
import numpy as np

from scripts.aim_generate_unfractured_layer_mesh import (
    generate_unfractured_layer_mesh,
)


class UnfracturedLayerSidecarTests(unittest.TestCase):
    def test_merges_touching_fragments_and_preserves_only_outer_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gds = root / "fractured.gds"
            stack = root / "stack.yaml"
            output = root / "m2.npz"

            layout = kdb.Layout()
            layout.dbu = 0.001
            top = layout.create_cell("TOP")
            layer_index = layout.layer(5030, 0)
            top.shapes(layer_index).insert(kdb.Box(0, 0, 1000, 1000))
            top.shapes(layer_index).insert(kdb.Box(1000, 0, 2000, 1000))
            layout.write(str(gds))
            stack.write_text(
                "M2AM_RENDER:\n"
                "  index: 5030\n"
                "  type: 0\n"
                "  z: 3.15\n"
                "  height: 0.89\n",
                encoding="utf-8",
            )

            generate_unfractured_layer_mesh(
                input_gds=gds,
                stack_config=stack,
                layer_name="M2AM_RENDER",
                output=output,
            )

            with np.load(output, allow_pickle=False) as archive:
                self.assertEqual(str(archive["layer_name"][0]), "M2AM_RENDER")
                self.assertEqual(tuple(archive["gds_layer"]), (5030, 0))
                self.assertEqual(
                    int(archive["imported_fractured_polygon_count"][0]), 2
                )
                self.assertEqual(
                    int(archive["logical_component_count"][0]), 1
                )
                self.assertEqual(len(archive["boundary_edges"]), 4)
                self.assertEqual(len(archive["triangles"]), 2)
                self.assertEqual(
                    int(archive["validated_closed_planar_topology"][0]), 1
                )

    def test_rejects_missing_stack_layer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stack = Path(temporary) / "stack.yaml"
            stack.write_text("OTHER: {}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing from stack"):
                generate_unfractured_layer_mesh(
                    input_gds=Path(temporary) / "unused.gds",
                    stack_config=stack,
                    layer_name="M2AM_RENDER",
                    output=Path(temporary) / "unused.npz",
                )


if __name__ == "__main__":
    unittest.main()
