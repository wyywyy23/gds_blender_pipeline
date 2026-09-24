from __future__ import annotations

from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from kfactory import kdb
import yaml

from scripts.aim_fill_cheese import finish_regions
from scripts.aim_preprocess_gds import preprocess_aim_gds


DBU = 0.001
RAW = {"FNAM": (733, 727), "SNAM": (735, 727), "M1AM": (710, 727),
       "DIAM": (726, 727), "TUAM": (777, 727), "PAAM": (795, 727)}


def box(*coordinates):
    return kdb.Region(kdb.DBox(*coordinates).to_itype(DBU))


def region(cell, layer):
    index = cell.layout().find_layer(*layer)
    if index is None:
        return kdb.Region()
    return kdb.Region(cell.begin_shapes_rec(index)).merged(True, 0)


class NitrideDicingTests(unittest.TestCase):
    def generate(self, diam, *, fnam=None, snam=None, fill=False,
                 already_subtracted=False, openings=True):
        fnam = box(0, 0, 40, 90) if fnam is None else fnam
        snam = box(0, 0, 35, 90) if snam is None else snam
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            layout = kdb.Layout()
            layout.dbu = DBU
            top = layout.create_cell("raw_" + uuid4().hex[:12])
            child = layout.create_cell(top.name + "_child")
            for name, shape in {"FNAM": fnam, "SNAM": snam, "DIAM": diam,
                                "M1AM": box(0, 0, 35, 90)}.items():
                child.shapes(layout.layer(*RAW[name])).insert(shape)
            child.shapes(layout.layer(999, 0)).insert(box(0, 0, 100, 100))
            # Real hierarchical input, including a translated trench.
            top.insert(kdb.CellInstArray(child.cell_index(), kdb.Trans(7000, 13000)))
            raw = root / (top.name + ".gds")
            layout.write(str(raw))
            original = raw.read_bytes()
            expected = {name: region(top, layer) for name, layer in RAW.items()}
            if fill:
                finished, report = finish_regions(top)
                for name in ("FNAM", "SNAM"):
                    self.assertGreater(next(item["fill_shapes"] for item in report["layers"]
                                            if item["name"] == name), 0)
                expected.update({name: finished[layer] for name, layer in RAW.items()
                                 if layer in finished})
            render = {
                "SUBSTRATE_BASE_RENDER": dict(layer=[2000, 0], generated_from_bbox=True),
                "SUBSTRATE_ETCHABLE_RENDER": dict(layer=[2001, 0], preprocessing_etch_expression="DIAM"),
                "CLADDING_RENDER": dict(layer=[2010, 0], preprocessing_exclusion_expression="DIAM"),
                "CLADDING_UNDERCUT_CUTTER_RENDER": dict(layer=[2050, 0], expression="TUAM"),
                "CLADDING_PASSIVATION_CUTTER_RENDER": dict(layer=[2051, 0], expression="PAAM"),
            }
            # As in existing setup bundles, plain raw expressions need dicing too.
            for name, number in [("FNAM", 4000), ("SNAM", 4001), ("M1AM", 5010)]:
                expression = name + " - DIAM" if already_subtracted and name != "M1AM" else name
                render[name + "_RENDER"] = dict(layer=[number, 0], source="static", expression=expression)
            registry = root / "registry.yaml"
            registry.write_text(yaml.safe_dump({
                "input_layers": {"raw": {name: dict(layer=layer) for name, layer in RAW.items()}},
                "output_layers": {"render": render},
                "processing": {"silicon_doping_resolution": {"silicon_bodies": {}}},
            }))
            output = root / "visual.gds"
            with redirect_stdout(io.StringIO()):
                preprocess_aim_gds(raw, registry, output, include_fill_cheese=fill,
                                   include_undercut=openings, include_passivation_opening=openings,
                                   max_polygon_vertices=8)
            exported = kdb.Layout()
            exported.read(str(output))
            cell = exported.top_cell()
            actual = {name: region(cell, spec["layer"]) for name, spec in render.items()}
            for name in ("FNAM", "SNAM"):
                with self.subTest(layer=name):
                    wanted = expected[name] - expected["DIAM"]
                    self.assertTrue((actual[name + "_RENDER"] ^ wanted).is_empty(),
                                    f"{name} must equal its preprocessed geometry minus DIAM")
                    self.assertTrue((actual[name + "_RENDER"] & expected["DIAM"]).is_empty())
            self.assertTrue((actual["M1AM_RENDER"] ^ expected["M1AM"]).is_empty())
            self.assertEqual(raw.read_bytes(), original)
            return actual

    def test_partial_trench_splits_nitride_without_removing_other_layers(self):
        for openings in (True, False):
            with self.subTest(openings=openings):
                self.generate(box(15, -1, 20, 91), openings=openings)

    def test_closed_trench_removes_nitride_inside_the_outer_boundary(self):
        outer = box(0, 0, 100, 100)
        result = self.generate(outer - box(5, 5, 95, 95), fnam=outer, snam=outer)
        self.assertEqual(result["FNAM_RENDER"].area() * DBU**2, 90 * 90)
        self.assertTrue((result["SUBSTRATE_BASE_RENDER"] ^
                         outer.transformed(kdb.Trans(7000, 13000))).is_empty())

    def test_internal_trench_hole_survives_gds_export_and_fracture(self):
        self.generate(box(10, 20, 20, 30))

    def test_fully_covered_nitride_is_removed(self):
        result = self.generate(box(-1, -1, 41, 91))
        self.assertTrue(result["FNAM_RENDER"].is_empty())
        self.assertTrue(result["SNAM_RENDER"].is_empty())

    def test_missing_diam_preserves_nitride(self):
        self.generate(kdb.Region())

    def test_existing_subtraction_expression_is_idempotent(self):
        self.generate(box(10, 20, 20, 30), already_subtracted=True)

    def test_dicing_applies_after_fill_cheese(self):
        self.generate(box(30, 30, 45, 60), fill=True)


if __name__ == "__main__":
    unittest.main()
