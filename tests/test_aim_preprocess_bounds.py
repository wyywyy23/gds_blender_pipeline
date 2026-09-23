from __future__ import annotations

from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from kfactory import kdb
import yaml

from scripts.aim_preprocess_gds import preprocess_aim_gds


def box(left, bottom, right, top):
    return kdb.Region(kdb.Box(left, bottom, right, top))


class VisualBoundaryTests(unittest.TestCase):
    DBU = 0.001
    OUTER = box(-1000, -1000, 11000, 11000)
    INNER = box(0, 0, 10000, 10000)
    DEVICE = box(2000, 2000, 8000, 8000)

    def generate(self, diam, device=None, *, margin=None, extra=None, expand=False, label=None):
        device = self.DEVICE if device is None else device
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            layout = kdb.Layout()
            layout.dbu = self.DBU
            cell = layout.create_cell("raw_" + uuid4().hex[:12])
            for layer, region in [((726, 727), diam), ((735, 727), device),
                                  ((123, 45), extra if extra is not None else kdb.Region())]:
                cell.shapes(layout.layer(*layer)).insert(region)
            if label is not None:
                cell.shapes(layout.layer(888, 728)).insert(kdb.Text("port", kdb.Trans(*label)))
            raw = root / f"{cell.name}.gds"
            layout.write(str(raw))
            render = {
                "SUBSTRATE_BASE_RENDER": dict(layer=[2000, 0], generated_from_bbox=True, bbox_margin=10.0),
                "SUBSTRATE_ETCHABLE_RENDER": dict(layer=[2001, 0], preprocessing_etch_expression="DIAM"),
                "CLADDING_RENDER": dict(layer=[2002, 0], preprocessing_exclusion_expression="DIAM"),
                "CLADDING_UNDERCUT_CUTTER_RENDER": dict(layer=[2050, 0], expression="EMPTY"),
                "CLADDING_PASSIVATION_CUTTER_RENDER": dict(layer=[2051, 0], expression="EMPTY"),
                "M1AM_RENDER": dict(layer=[5010, 0], source="static", expression="EXPANDED" if expand else "M1AM"),
            }
            registry = {
                "input_layers": {"raw": {"DIAM": dict(layer=[726, 727]), "M1AM": dict(layer=[735, 727]),
                                           "EMPTY": dict(layer=[999, 0])}},
                "render_layers": render,
                "processing": {"silicon_doping_resolution": {"silicon_bodies": {}}},
            }
            if expand:
                registry["processing"]["static_derived_regions"] = {
                    "EXPANDED": dict(source="M1AM", operations=[dict(type="offset", distance=5, join="miter")])}
            config = root / "registry.yaml"
            config.write_text(yaml.safe_dump(registry))
            output = root / "visual.gds"
            log = io.StringIO()
            with redirect_stdout(log):
                preprocess_aim_gds(raw, config, output, bbox_margin_override=margin,
                                   max_polygon_vertices=16)
            exported = kdb.Layout()
            exported.read(str(output))
            top = max(exported.top_cells(), key=lambda c: c.bbox().area())
            regions = {name: kdb.Region(top.begin_shapes_rec(exported.layer(*spec["layer"]))).merged(True, 0)
                       for name, spec in render.items()}
            return regions, log.getvalue()

    def assert_region(self, actual, expected):
        self.assertTrue((actual ^ expected).is_empty(), f"{actual.bbox()} != {expected.bbox()}")

    def test_closed_trench_uses_outer_edge_and_preserves_etched_interior(self):
        regions, log = self.generate(self.OUTER - self.INNER, margin=123)
        self.assert_region(regions["SUBSTRATE_BASE_RENDER"], self.OUTER)
        self.assert_region(regions["SUBSTRATE_ETCHABLE_RENDER"], self.INNER)
        self.assert_region(regions["CLADDING_RENDER"], self.INNER)
        self.assert_region(regions["M1AM_RENDER"], self.DEVICE)
        self.assertIn("enclosing DIAM outer edge", log)

    def test_text_only_layer_does_not_disqualify_enclosing_trench(self):
        regions, _ = self.generate(self.OUTER - self.INNER, label=(5000, 5000))
        self.assert_region(regions["SUBSTRATE_BASE_RENDER"], self.OUTER)

    def test_separate_touching_strips_form_an_enclosing_trench(self):
        strips = (box(-1000, -1000, 11000, 0) + box(-1000, 10000, 11000, 11000)
                  + box(-1000, 0, 0, 10000) + box(10000, 0, 11000, 10000))
        regions, _ = self.generate(strips)
        self.assert_region(regions["SUBSTRATE_BASE_RENDER"], self.OUTER)

    def test_missing_trench_expands_full_bbox_with_default_or_override(self):
        for margin in (None, 23):
            with self.subTest(margin=margin):
                regions, log = self.generate(kdb.Region(), margin=margin)
                extent = 10000 if margin is None else 23000
                self.assert_region(regions["SUBSTRATE_BASE_RENDER"],
                                   box(2000-extent, 2000-extent, 8000+extent, 8000+extent))
                self.assertIn("expanded layout bbox", log)

    def test_open_trench_even_with_one_grid_gap_keeps_bbox_margin(self):
        for gap in (1, 1000):
            with self.subTest(gap=gap):
                opened = (self.OUTER - self.INNER) - box(5000, -1000, 5000+gap, 0)
                regions, _ = self.generate(opened)
                self.assert_region(regions["SUBSTRATE_BASE_RENDER"], box(-11000, -11000, 21000, 21000))

    def test_internal_loop_does_not_crop_outside_geometry_on_unknown_layer(self):
        regions, _ = self.generate(self.OUTER - self.INNER, extra=box(20000, 2000, 21000, 3000))
        self.assert_region(regions["SUBSTRATE_BASE_RENDER"], box(-11000, -11000, 31000, 21000))

    def test_filled_diam_rectangle_is_not_an_enclosing_trench(self):
        regions, _ = self.generate(self.OUTER)
        self.assert_region(regions["SUBSTRATE_BASE_RENDER"], box(-11000, -11000, 21000, 21000))

    def test_concave_outer_contour_is_preserved_instead_of_its_bbox(self):
        outer = self.OUTER - box(6000, 6000, 11000, 11000)
        trench = outer - outer.sized(-1000)
        regions, _ = self.generate(trench, box(2000, 2000, 4000, 4000))
        self.assert_region(regions["SUBSTRATE_BASE_RENDER"], outer)
        # A raw shape in the concavity fits the bbox but is outside the trench.
        regions, _ = self.generate(trench, extra=box(8000, 8000, 9000, 9000))
        self.assert_region(regions["SUBSTRATE_BASE_RENDER"], box(-11000, -11000, 21000, 21000))

    def test_multiple_enclosed_chips_keep_separate_outer_contours(self):
        second = box(20000, -1000, 32000, 11000)
        trench = (self.OUTER - self.INNER) + (second - second.sized(-1000))
        regions, _ = self.generate(trench, self.DEVICE + box(24000, 2000, 28000, 8000))
        self.assert_region(regions["SUBSTRATE_BASE_RENDER"], self.OUTER + second)

    def test_derived_geometry_cannot_expand_past_enclosing_outer_edge(self):
        regions, log = self.generate(self.OUTER - self.INNER, expand=True)
        self.assert_region(regions["M1AM_RENDER"], self.OUTER)
        self.assertIn("Layers clipped to DIAM outer edge: 1", log)


if __name__ == "__main__":
    unittest.main()
