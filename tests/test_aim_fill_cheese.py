from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from kfactory import kdb
import yaml

from scripts.aim_fill_cheese import (
    AreaKeepout, FinishConfig, LayerRecipe, Pattern, finish_regions,
    load_fill_cheese_config,
)
from scripts.aim_preprocess_gds import preprocess_aim_gds

M1, M2, ML = (710, 727), (725, 727), (780, 727)
DIAM, TUAM, VIA = (726, 727), (777, 727), (715, 727)
BLOCK, CHEESE_BLOCK = (751, 727), (752, 727)


def box(*coords):
    return kdb.Region(kdb.DBox(*coords).to_itype(0.001))


def region(cell, layer):
    index = cell.layout().find_layer(*layer)
    return kdb.Region() if index is None else kdb.Region(cell.begin_shapes_rec(index)).merged()


class FillCheeseTests(unittest.TestCase):
    def layout(self, shapes=()):
        layout = kdb.Layout()
        layout.dbu = 0.001
        cell = layout.create_cell('SOURCE_' + uuid4().hex[:10])
        for layer, shape in shapes:
            cell.shapes(layout.layer(*layer)).insert(shape)
        return layout, cell

    def assert_same(self, actual, expected):
        self.assertTrue((actual ^ expected).is_empty())

    def isolated_config(self, recipe, **kwargs):
        return FinishConfig((recipe,), waveguide_layers=(), global_fill_keepouts=(),
                            global_keepouts=(), **kwargs)

    def test_default_groups_have_aligned_complete_shapes(self):
        layout, cell = self.layout([((999, 0), box(0, 0, 90, 90))])
        output, report = finish_regions(cell)
        self.assert_same(output[(709, 727)], output[(733, 727)])
        self.assert_same(output[(709, 727)], output[(735, 727)])
        self.assert_same(output[M1], output[M2])
        waves = list(output[(709, 727)].each())
        self.assertTrue(any(p.bbox().width() == 2800 and p.bbox().height() == 600 for p in waves))
        self.assertTrue(any(p.bbox().width() == p.bbox().height() == 600 for p in waves))
        self.assertTrue(all(p.bbox().width() == p.bbox().height() == 1600 for p in output[M1].each()))
        self.assertTrue(all(p.bbox().width() == p.bbox().height() == 8000 for p in output[ML].each()))
        self.assertEqual(report['bounds_um'], [0, 0, 90, 90])
        self.assert_same(region(cell, M1), kdb.Region())

    def test_independent_fill_cheese_masks_and_vias_are_respected(self):
        old = box(0, 0, 30, 30)
        layout, cell = self.layout([(M1, old), (BLOCK, box(33, 0, 45, 30)),
                                   (CHEESE_BLOCK, box(0, 0, 9, 30)), (VIA, box(14, 14, 16, 16))])
        recipe = next(r for r in load_fill_cheese_config().layers if r.layer == M1)
        cfg = self.isolated_config(recipe)
        output, _ = finish_regions(cell, bounds=(0, 0, 60, 30), config=cfg)
        added, holes = output[M1] - old, old - output[M1]
        self.assertGreater(added.area(), 0)
        self.assertGreater(holes.area(), 0)
        self.assertTrue((added & region(cell, BLOCK)).is_empty())
        self.assertTrue((holes & region(cell, CHEESE_BLOCK)).is_empty())
        self.assertTrue(holes.separation_check(region(cell, VIA), 500).is_empty())
        self.assertTrue(holes.separation_check(box(-1, -1, 31, 31) - old, 1500).is_empty())
        self.assertTrue(all(p.bbox().width() == p.bbox().height() == 1000 for p in holes.each()))
        self.assert_same(region(cell, M1), old)
        self.assert_same(region(cell, VIA), box(14, 14, 16, 16))

    def test_undercut_and_per_layer_diam_clearance_protect_both_operations(self):
        config = load_fill_cheese_config()
        shapes = [(DIAM, box(48, 30, 52, 60)), (TUAM, box(68, 30, 72, 60)),
                  ((999, 0), box(0, 0, 100, 90))]
        shapes += [(r.layer, box(0, 0, 50, 90)) for r in config.layers if r.cheese]
        layout, cell = self.layout(shapes)
        before = {r.layer: region(cell, r.layer) for r in config.layers}
        output, report = finish_regions(cell, config=config)
        for recipe in config.layers:
            with self.subTest(layer=recipe.name):
                delta = output[recipe.layer] ^ before[recipe.layer]
                self.assertGreater(delta.area(), 0)
                self.assertTrue(delta.separation_check(region(cell, DIAM), int(recipe.diam_clearance*1000)).is_empty())
                self.assertTrue(delta.separation_check(region(cell, TUAM), 3750).is_empty())
                self.assert_same(region(cell, recipe.layer), before[recipe.layer])
        self.assertEqual(report['keepout_join'], 'round')
        self.assert_same(region(cell, TUAM), box(68, 30, 72, 60))

    def test_default_device_keepouts_block_fill_and_cheese(self):
        config = load_fill_cheese_config()
        self.assertEqual(config.global_fill_keepouts, ())
        for layer in (802, 803, 804):
            with self.subTest(layer=layer):
                old = box(0, 0, 45, 30)
                keepout = box(27, 0, 63, 30)
                layout, cell = self.layout([(M1, old), ((layer, 727), keepout),
                                           ((999, 0), box(0, 0, 90, 30))])
                recipe = next(r for r in config.layers if r.layer == M1)
                for cfg in (replace(config, layers=(recipe,)), FinishConfig((recipe,)),
                            replace(config, layers=(recipe,), waveguide_keepout=9),
                            replace(config, layers=(recipe,), global_keepout_clearance=11)):
                    output, _ = finish_regions(cell, config=cfg)
                    added, holes = output[M1]-old, old-output[M1]
                    self.assertGreater(added.area(), 0)
                    self.assertGreater(holes.area(), 0)
                    self.assertTrue((added & keepout).is_empty())
                    self.assertTrue((holes & keepout).is_empty())
                    clearance = round(max(cfg.waveguide_keepout, cfg.global_keepout_clearance)*1000)
                    self.assertTrue(added.separation_check(keepout, clearance).is_empty())
                    self.assertTrue(holes.separation_check(keepout, clearance).is_empty())
                    self.assert_same(region(cell, (layer, 727)), keepout)

    def test_layer_order_uses_original_waveguide_projection(self):
        layout, cell = self.layout([((709, 727), box(0, 29, 70, 31)), ((999, 0), box(0, 0, 90, 70))])
        cfg = load_fill_cheese_config()
        forward, _ = finish_regions(cell, config=cfg)
        backward, _ = finish_regions(cell, config=replace(cfg, layers=tuple(reversed(cfg.layers))))
        for layer in forward:
            self.assert_same(forward[layer], backward[layer])
            self.assertTrue((forward[layer]-region(cell, layer)).separation_check(region(cell, (709, 727)), 7000).is_empty())

    def test_round_keepout_accepts_safe_diagonal_but_not_axis_candidate(self):
        layout, cell = self.layout([(DIAM, box(10, 10, 12, 12))])
        recipe = LayerRecipe('ML', ML, fill=Pattern(width=.2, pitch=(4, 4), phase=(16.2, 16.2)))
        output, _ = finish_regions(cell, bounds=(0, 0, 30, 30), config=self.isolated_config(recipe))
        self.assertTrue((box(16.15, 16.15, 16.25, 16.25)-output[ML]).is_empty())
        self.assertTrue(output[ML].separation_check(region(cell, DIAM), 5000).is_empty())
        recipe = replace(recipe, fill=replace(recipe.fill, phase=(16.5, 11)))
        output, _ = finish_regions(cell, bounds=(0, 0, 30, 30), config=self.isolated_config(recipe))
        self.assertTrue((box(16.45, 10.95, 16.55, 11.05) & output[ML]).is_empty())

    def test_width_trigger_is_strict_and_candidates_never_clip_at_roi(self):
        old = box(0, 0, 5, 40)
        layout, cell = self.layout([(M1, old)])
        recipe = LayerRecipe('M1', M1, fill=Pattern(width=1.6, pitch=(3, 3)),
                             cheese=Pattern(width=1, pitch=(3, 3)), width_trigger=5)
        output, _ = finish_regions(cell, bounds=(0, 0, 11, 19), config=self.isolated_config(recipe))
        self.assertTrue((old-output[M1]).is_empty())
        fills = output[M1]-old
        self.assertGreater(fills.area(), 0)
        self.assertTrue((fills-box(0, 0, 11, 19)).is_empty())
        self.assertTrue(all(p.bbox().width() == p.bbox().height() == 1600 for p in fills.each()))
        self.assertTrue((old-output[M1]).is_empty())

    def test_transformed_hierarchy_is_read_without_modifying_input(self):
        layout, child = self.layout([(M1, box(0, 0, 30, 30)), (TUAM, box(8, 8, 12, 12))])
        top = layout.create_cell('TOP')
        top.insert(kdb.CellInstArray(child.cell_index(), kdb.Trans(1, False, 70000, -20000)))
        before = region(top, M1)
        result, report = finish_regions(top)
        self.assertEqual(report['bounds_um'], [40, -20, 70, 10])
        self.assertGreater((result[M1]^before).area(), 0)
        self.assert_same(region(top, M1), before)
        self.assertEqual(sum(1 for _ in top.each_inst()), 1)

    def test_invalid_profiles_and_candidate_limit_fail_explicitly(self):
        with self.assertRaisesRegex(ValueError, 'DIAM clearance.*minimum'):
            self.isolated_config(LayerRecipe('M1', M1, diam_clearance=0))
        cfg = replace(load_fill_cheese_config(), max_candidates=1)
        layout, cell = self.layout([(M1, box(0, 0, 30, 30))])
        with self.assertRaisesRegex(ValueError, 'Candidate budget exceeded'):
            finish_regions(cell, config=cfg)
        self.assert_same(region(cell, M1), box(0, 0, 30, 30))
        with self.assertRaises(ValueError):
            FinishConfig.from_dict({'layers': [], 'unknown': 1})

    def test_large_component_offsets_match_original_bulk_round_geometry(self):
        import math
        import gdstk
        from scripts.aim_fill_cheese import offset_region
        source = kdb.Region()
        for y in range(24):
            for x in range(32):
                source.insert(kdb.DPolygon.ellipse(kdb.DBox(x*4, y*4, x*4+2, y*4+2), 32).to_itype(.001))
        points = [[(p.x*.001, p.y*.001) for p in polygon.each_point_hull()] for polygon in source.each()]
        for distance in (-.2, 1.1):
            actual = offset_region(source, distance, .001, conservative=True)
            effective = math.copysign(abs(distance)/math.cos(math.pi/144)+math.sqrt(2)*.001, distance)
            expected = kdb.Region()
            for polygon in gdstk.offset(points, effective, join='round', tolerance=144, precision=.001, use_union=True):
                expected.insert(kdb.DPolygon([kdb.DPoint(float(x), float(y)) for x, y in polygon.points]).to_itype(.001))
            self.assert_same(actual, expected.merged())

    def test_preprocessor_option_changes_render_regions_and_keeps_bounds(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            layout, cell = self.layout([(M1, box(0, 0, 30, 30)), (TUAM, box(13, 13, 17, 17)),
                                       ((999, 0), box(0, 0, 60, 60))])
            raw = root / (cell.name + '.gds')
            layout.write(str(raw))
            original = raw.read_bytes()
            expected, _ = finish_regions(cell)
            layers = {
                'SUBSTRATE_BASE_RENDER': dict(layer=[2000, 0], generated_from_bbox=True, bbox_margin=10),
                'SUBSTRATE_ETCHABLE_RENDER': dict(layer=[2001, 0], preprocessing_etch_expression='DIAM'),
                'CLADDING_RENDER': dict(layer=[2010, 0], preprocessing_exclusion_expression='DIAM'),
                'CLADDING_UNDERCUT_CUTTER_RENDER': dict(layer=[2050, 0], expression='TUAM'),
                'CLADDING_PASSIVATION_CUTTER_RENDER': dict(layer=[2051, 0], expression='EMPTY'),
            }
            inputs = {'DIAM':dict(layer=DIAM), 'TUAM':dict(layer=TUAM), 'EMPTY':dict(layer=[998, 0])}
            for index, recipe in enumerate(load_fill_cheese_config().layers):
                inputs[recipe.name] = dict(layer=recipe.layer)
                layers[recipe.name+'_RENDER'] = dict(layer=[6000+index, 0], source='static', expression=recipe.name)
            registry = root/'registry.yaml'
            registry.write_text(yaml.safe_dump({'input_layers':{'raw':inputs}, 'render_layers':layers,
                'processing':{'silicon_doping_resolution':{'silicon_bodies':{}}}}))
            for enabled in (False, True):
                # Unique raw basename prevents gdsfactory's global cell-name cache collision.
                source = root/(f'case_{int(enabled)}_'+cell.name+'.gds')
                cell.name = source.stem
                layout.write(str(source))
                out = root/f'visual_{enabled}.gds'
                with redirect_stdout(io.StringIO()):
                    result = preprocess_aim_gds(source, registry, out, include_fill_cheese=enabled, max_polygon_vertices=64)
                for recipe in load_fill_cheese_config().layers:
                    actual = region(result.kdb_cell, layers[recipe.name+'_RENDER']['layer'])
                    self.assert_same(actual, expected[recipe.layer] if enabled else region(cell, recipe.layer))
                self.assert_same(region(result.kdb_cell, (2000, 0)), box(-10, -10, 70, 70))
                self.assert_same(region(result.kdb_cell, (2050, 0)), region(cell, TUAM))
                self.assertEqual('fill_cheese' in result.info, enabled)
            self.assertEqual(raw.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
