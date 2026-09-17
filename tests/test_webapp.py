import copy
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import yaml
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'webapp'))
sys.path.insert(0,str(ROOT/'scripts'))
import pipeline
spec=importlib.util.spec_from_file_location('studio_server',ROOT/'webapp/server.py')
server=importlib.util.module_from_spec(spec);spec.loader.exec_module(server)
import aim_render_scene
import aim_build_blender_scene
CAMERA={'location':[150,-200,300],'rotation_degrees':[30,0,25],'lens_mm':100,'dof':{'enabled':True,'focus_point':[1,2,3],'aperture_fstop':4}}

class StudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def test_strict_options_and_camera_validation(self):
        for values in [{'samples':0},{'denoise':'false'},{'scheme':'../../private'},{'width':10.5},{'z_scale':float('nan')},{'unknown':1}]:
            with self.subTest(values=values),self.assertRaises(ValueError):pipeline.options(values)
        for values in [dict(CAMERA,lens_mm=float('inf')),dict(CAMERA,location=[1,2]),dict(CAMERA,lens_mm=True)]:
            with self.assertRaises(ValueError):pipeline.camera(values)
    def test_geometry_defaults_match_production_makefile(self):
        text=(ROOT/'Makefile').read_text()
        mapping={'metal_fillet':'AIM_PREPROCESS_METAL_XY_FILLET_WIDTH_UM','via_fillet':'AIM_PREPROCESS_CONTACT_VIA_XY_FILLET_WIDTH_UM','bevel_width':'AIM_BLENDER_PRESENTATION_METAL_Z_BEVEL_WIDTH_UM','max_vertices':'AIM_PREPROCESS_MAX_POLYGON_VERTICES','merge_layers':'AIM_BLENDER_MERGE_LAYERS','metal_bevel':'AIM_BLENDER_PRESENTATION_METAL_Z_BEVEL_ENABLED'}
        import re
        for key,var in mapping.items():self.assertEqual(float(re.search(r'^'+var+r' \?= (.+)$',text,re.M)[1]),pipeline.DEFAULTS[key])
    def test_preset_round_trip_through_real_blender_loader(self):
        o=pipeline.options({'width':900,'height':1600,'denoise':False})
        value=pipeline.preset('test',CAMERA,o,['M2AM_RENDER'],{'lighting':{'sun':{'strength':3}},'color_management':{'view_transform':'AgX','look':'AgX - High Contrast'}})
        path=self.root/'preset.yaml';path.write_text(yaml.safe_dump(value));loaded=aim_render_scene.load_preset(path)
        self.assertEqual(loaded['camera']['dof'],dict(CAMERA['dof'],focus_point=(1.,2.,3.)));self.assertEqual(loaded['camera']['sensor_width_mm'],36)
        self.assertEqual(loaded['render']['resolution_x'],900);self.assertEqual(loaded['runs'][0]['hide_layers'],['M2AM_RENDER'])
        self.assertEqual(loaded['lighting']['sun']['strength'],3)
    def test_legacy_preset_still_supported(self):
        loaded=aim_render_scene.load_preset(ROOT/'configs/blender/render_presets/disk_array_oblique_100mm.yaml')
        self.assertEqual(loaded['camera']['lens_mm'],100);self.assertNotIn('dof',loaded['camera'])
    def test_shared_legacy_preset_remains_available_without_layout(self):
        studio=server.Studio(self.root);studio.preset_dir.mkdir(parents=True)
        (studio.preset_dir/'legacy.yaml').write_text(yaml.safe_dump(pipeline.preset('legacy',CAMERA,pipeline.options({}),[])))
        self.assertEqual(studio.presets()[0]['id'],'legacy/legacy.yaml')
        self.assertEqual(studio.get_preset('legacy/legacy.yaml')['preset']['name'],'legacy')
        with self.assertRaises(ValueError):studio.get_preset('../config.json')
    def test_config_checks_actual_file_existence(self):
        studio=server.Studio(self.root)
        for key,(_,default) in pipeline.FILES.items():
            if default:
                path=self.root/default;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('local input')
        with patch.object(server.subprocess,'run',return_value=__import__('subprocess').CompletedProcess([],0)):
            status=studio.status();self.assertEqual(next(x for x in status['checks'] if x['key']=='tech')['state'],'ready')
            self.assertEqual(next(x for x in status['checks'] if x['key']=='gds')['state'],'missing')
            (self.root/pipeline.FILES['tech'][1]).unlink()
            self.assertEqual(next(x for x in studio.status()['checks'] if x['key']=='tech')['state'],'missing')
    def test_commands_preserve_literal_paths_and_never_call_blender_for_preview(self):
        cfg={k:'/private files/$(touch nope);file' for k in pipeline.FILES}|{'python':sys.executable,'blender':'/Applications/Blender.app/Contents/MacOS/Blender'}
        cmds=pipeline.prepare_commands(cfg,pipeline.options({}),self.root,preview=True)
        self.assertTrue(all(c[0]==sys.executable for c in cmds));self.assertIn(cfg['gds'],cmds[-1])
        self.assertEqual(cmds[-1][cmds[-1].index('--presentation-metal-xy-fillet-width-um')+1],'0.2')
        built=pipeline.build_commands(cfg,pipeline.options({}),self.root)
        self.assertEqual(len(built),10);self.assertIn('--layer-name',built[5]);self.assertIn('--python-exit-code',built[-1])
        off=pipeline.build_commands(cfg,pipeline.options({'metal_bevel':False}),self.root)
        self.assertEqual(len(off),7);self.assertIn('--no-merge-layers',off[-2])
    def test_preview_preserves_hole_and_z_coordinates(self):
        import gdstk
        lib=gdstk.Library(unit=1e-6);cell=lib.new_cell('TEST');polys=gdstk.boolean(gdstk.rectangle((0,0),(10,10)),gdstk.rectangle((3,3),(7,7)),'not',layer=10);cell.add(*polys);gds=self.root/'test.gds';meta=lib.new_cell('$$$CONTEXT_INFO$$$');meta.add(gdstk.rectangle((0,0),(0,0),layer=9999));lib.write_gds(str(gds))
        stack=self.root/'stack.yaml';stack.write_text(yaml.safe_dump({'METAL':{'index':10,'type':0,'z':2,'height':3}}))
        colors=self.root/'configs/blender/colors/aim';colors.mkdir(parents=True)
        for name in ['realistic','fancy','marketing']:(colors/(name+'.yaml')).write_text('layers: {}\n')
        result=pipeline.preview_mesh(gds,stack,pipeline.options({'preview_tolerance':0,'z_scale':2}),root=self.root)
        self.assertEqual(result['bounds'],[[0,0,4],[10,10,10]])
        vertices=result['meshes'][0]['positions'];area=0
        from shapely.geometry import Polygon,Point
        for i in range(0,len(vertices),9):
            t=vertices[i:i+9]
            if t[2]==t[5]==t[8]==10:
                face=Polygon([(t[0],t[1]),(t[3],t[4]),(t[6],t[7])]);area+=face.area;self.assertFalse(face.contains(Point(5,5)))
        self.assertAlmostEqual(area,84)
        with self.assertRaisesRegex(ValueError,'budget'):pipeline.preview_mesh(gds,stack,pipeline.DEFAULTS|{'preview_limit':1},root=self.root)
    def test_empty_metal_layers_do_not_request_nonexistent_sidecars(self):
        import gdstk
        lib=gdstk.Library();cell=lib.new_cell('SILICON_ONLY');cell.add(gdstk.rectangle((0,0),(10,10),layer=10));lib.write_gds(str(self.root/'visual.gds'))
        (self.root/'stack.yaml').write_text(yaml.safe_dump({'SILICON':{'index':10,'type':0},'M1AM_RENDER':{'index':20,'type':0}}))
        cfg={key:'/tmp/input' for key in pipeline.FILES}|{'python':sys.executable,'blender':'blender'}
        commands=pipeline.build_commands(cfg,pipeline.options({}),self.root)
        self.assertEqual(len(commands),7)
        self.assertNotIn('--presentation-metal-z-bevel-sidecar',commands[-2])
        args=aim_build_blender_scene.parse_args(commands[-2][commands[-2].index('--')+1:])
        self.assertEqual(args.source_max_polygon_vertices,256)
        self.assertEqual(args.presentation_metal_z_bevel_width_um,0)
        self.assertTrue(pipeline.DEFAULTS['metal_bevel'])
    def test_fractured_metal_layers_get_matching_sidecars_and_valid_blender_arguments(self):
        import gdstk
        names=('M1AM_RENDER','M2AM_RENDER','MLAM_RENDER')
        stack={name:{'index':20+i,'type':0} for i,name in enumerate(names)}
        (self.root/'stack.yaml').write_text(yaml.safe_dump(stack))
        cfg={key:'/tmp/input' for key in pipeline.FILES}|{'python':sys.executable,'blender':'blender'}
        for present in [(name,) for name in names]+[names]:
            with self.subTest(present=present):
                lib=gdstk.Library();child=lib.new_cell('METAL');top=lib.new_cell('TOP');top.add(gdstk.Reference(child))
                for name in present:
                    shape=gdstk.ellipse((0,0),10,tolerance=0.001,layer=stack[name]['index'])
                    child.add(*shape.fracture(max_points=32))
                lib.write_gds(str(self.root/'visual.gds'))
                opts=pipeline.options({'max_vertices':32});original=copy.deepcopy(opts)
                commands=pipeline.build_commands(cfg,opts,self.root)
                build=commands[-2];args=aim_build_blender_scene.parse_args(build[build.index('--')+1:])
                generators=[cmd for cmd in commands if '--layer-name' in cmd]
                self.assertEqual({cmd[cmd.index('--layer-name')+1] for cmd in generators},set(present))
                self.assertEqual({Path(cmd[cmd.index('--output')+1]).resolve() for cmd in generators},set(args.presentation_metal_z_bevel_sidecar))
                self.assertEqual(args.source_max_polygon_vertices,32)
                self.assertEqual(args.presentation_metal_z_bevel_width_um,opts['bevel_width'])
                self.assertEqual(opts,original)
    def test_disabled_or_zero_width_bevel_has_valid_arguments_even_with_metal(self):
        import gdstk
        lib=gdstk.Library();cell=lib.new_cell('METAL');cell.add(gdstk.rectangle((0,0),(10,10),layer=20));lib.write_gds(str(self.root/'visual.gds'))
        (self.root/'stack.yaml').write_text(yaml.safe_dump({'M1AM_RENDER':{'index':20,'type':0}}))
        cfg={key:'/tmp/input' for key in pipeline.FILES}|{'python':sys.executable,'blender':'blender'}
        for changes in ({'metal_bevel':False},{'bevel_width':0}):
            with self.subTest(changes=changes):
                commands=pipeline.build_commands(cfg,pipeline.options(changes),self.root)
                self.assertFalse(any('--layer-name' in cmd for cmd in commands))
                args=aim_build_blender_scene.parse_args(commands[-2][commands[-2].index('--')+1:])
                self.assertEqual(args.presentation_metal_z_bevel_width_um,0)
                self.assertEqual(args.presentation_metal_z_bevel_sidecar,[])
                self.assertEqual(args.source_max_polygon_vertices,256)
    def test_job_conflicts_do_not_overwrite_configuration(self):
        studio=server.Studio(self.root);studio.active='existing'
        with self.assertRaisesRegex(ValueError,'active job'):studio.save_config({'gds':'test.gds'})
        self.assertFalse(studio.config_path.exists())

if __name__=='__main__':unittest.main()
