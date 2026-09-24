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
    def test_iridescence_preset_and_preprocessing_independence(self):
        off=pipeline.options({});on=pipeline.options({'iridescence':True,'iridescence_strength':.6})
        cfg={key:'/tmp/input' for key in pipeline.FILES}|{'python':sys.executable,'blender':'blender'}
        self.assertEqual(pipeline.prepare_commands(cfg,off,self.root),pipeline.prepare_commands(cfg,on,self.root))
        self.assertFalse(off['iridescence'])
        self.assertEqual(off['iridescence_strength'], .85)
        for opts in [off,on,pipeline.options({'iridescence':True,'iridescence_strength':0})]:
            value=pipeline.preset('film',CAMERA,opts,[])
            path=self.root/'film.yaml';path.write_text(yaml.safe_dump(value))
            loaded=aim_render_scene.load_preset(path)
            self.assertEqual(loaded['appearance']['cladding_iridescence'],dict(enabled=opts['iridescence'],strength=opts['iridescence_strength']))
        legacy=aim_render_scene.load_preset(ROOT/'configs/blender/render_presets/disk_array_oblique_100mm.yaml')
        self.assertFalse(legacy['appearance']['cladding_iridescence']['enabled'])
        for bad in [{'iridescence':'true'},{'iridescence_strength':float('nan')},{'iridescence_strength':1.1}]:
            with self.assertRaises(ValueError):pipeline.options(bad)
    def test_strict_options_and_camera_validation(self):
        for values in [{'samples':0},{'denoise':'false'},{'scheme':'../../private'},{'width':10.5},{'z_scale':float('nan')},{'unknown':1}]:
            with self.subTest(values=values),self.assertRaises(ValueError):pipeline.options(values)
        for values in [dict(CAMERA,lens_mm=float('inf')),dict(CAMERA,location=[1,2]),dict(CAMERA,lens_mm=True)]:
            with self.assertRaises(ValueError):pipeline.camera(values)
    def test_geometry_defaults_match_production_makefile(self):
        text=(ROOT/'Makefile').read_text()
        mapping={'fill_cheese':'AIM_PREPROCESS_FILL_CHEESE','metal_fillet':'AIM_PREPROCESS_METAL_XY_FILLET_WIDTH_UM','via_fillet':'AIM_PREPROCESS_CONTACT_VIA_XY_FILLET_WIDTH_UM','bevel_width':'AIM_BLENDER_PRESENTATION_METAL_Z_BEVEL_WIDTH_UM','max_vertices':'AIM_PREPROCESS_MAX_POLYGON_VERTICES','merge_layers':'AIM_BLENDER_MERGE_LAYERS','metal_bevel':'AIM_BLENDER_PRESENTATION_METAL_Z_BEVEL_ENABLED'}
        import re
        for key,var in mapping.items():self.assertEqual(float(re.search(r'^'+var+r' \?= (.+)$',text,re.M)[1]),pipeline.DEFAULTS[key])
    def test_adaptive_rounding_uses_saved_radii_in_shared_preprocessor(self):
        import subprocess
        from kfactory import kdb
        from scripts.aim_preprocess_gds import ensure_active_pdk, add_static_expression_render_layers, region_topology
        import gdsfactory as gf
        cfg={key:'/tmp/input' for key in pipeline.FILES}|{'python':sys.executable}
        ensure_active_pdk()
        for values in [{}, {'metal_fillet':.12, 'via_fillet':.06}, {'metal_fillet':0, 'via_fillet':0}]:
            opts=pipeline.options(values)
            saved=pipeline.preset('adaptive',CAMERA,opts,[])
            restored=pipeline.options(saved['webapp']['options'])
            command=pipeline.prepare_commands(cfg,restored,self.root)[-1]
            # Run the generated argv through the actual parser, then exercise
            # the same layer dispatch with both safe and disappearing shapes.
            parsed=subprocess.run(command+['--help'],capture_output=True,text=True)
            self.assertEqual(parsed.returncode,0,parsed.stderr)
            metal=float(command[command.index('--presentation-metal-xy-fillet-width-um')+1])
            via=float(command[command.index('--presentation-contact-via-xy-fillet-width-um')+1])
            self.assertEqual((metal,via),(opts['metal_fillet'],opts['via_fillet']))
            source=kdb.Region(kdb.Box(0,0,50,50))+kdb.Region(kdb.Box(1000,1000,11000,11000))
            layers={name:dict(source='static',expression='RAW',layer=[200+i,0],z=0,height=1) for i,name in enumerate(('M1AM_RENDER','CBAM_RENDER'))}
            output=gf.Component()
            stats=add_static_expression_render_layers(c_out=output,render_layers=layers,region_symbols={'RAW':source},handled_layers=set(),min_export_z=None,presentation_metal_xy_fillet_width_um=metal,presentation_contact_via_xy_fillet_width_um=via)
            for name,layer in layers.items():
                result=kdb.Region(output.kdb_cell.begin_shapes_rec(output.kcl.layer(*layer['layer'])))
                self.assertEqual(region_topology(result.merged(True,0)),(2,0))
                if metal and via:
                    self.assertEqual(stats['presentation_xy_rounding'][name]['adapted_components'],1)
                else:
                    self.assertTrue((source ^ result).is_empty())

    def test_fill_cheese_flag_defaults_preset_and_cli_parser(self):
        import subprocess
        self.assertFalse(pipeline.options({})['fill_cheese'])
        with self.assertRaisesRegex(ValueError, 'fill_cheese must be boolean'):
            pipeline.options({'fill_cheese': 'yes'})
        cfg={key:'/tmp/input' for key in pipeline.FILES}|{'python':sys.executable}
        for enabled in (False, True):
            opts=pipeline.options({'fill_cheese':enabled})
            saved=pipeline.preset('filled',CAMERA,opts,[])
            restored=pipeline.options(saved['webapp']['options'])
            command=pipeline.prepare_commands(cfg,restored,self.root)[-1]
            self.assertEqual('--fill-cheese' in command,enabled)
            self.assertEqual(subprocess.run(command+['--help'],capture_output=True).returncode,0)
            self.assertEqual(saved['render']['samples'],1024)
            self.assertEqual(saved['render']['resolution_x'],3200)

    def test_preset_round_trip_through_real_blender_loader(self):
        o=pipeline.options({'width':900,'height':1600,'denoise':False})
        value=pipeline.preset('test',CAMERA,o,['M2AM_RENDER'],{'lighting':{'sun':{'strength':3}},'color_management':{'view_transform':'AgX','look':'AgX - High Contrast'}})
        path=self.root/'preset.yaml';path.write_text(yaml.safe_dump(value));loaded=aim_render_scene.load_preset(path)
        self.assertEqual(loaded['camera']['dof'],dict(CAMERA['dof'],adapt_to_view=True,focus_point=(1.,2.,3.)));self.assertEqual(loaded['camera']['sensor_width_mm'],36)
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
    def test_dense_preview_adapts_and_keeps_all_shapes_and_render_inputs(self):
        import gdstk
        from shapely.geometry import Point, Polygon
        lib=gdstk.Library(unit=1e-6);cell=lib.new_cell('DENSE')
        for row in range(10):
            for col in range(10):cell.add(gdstk.rectangle((col*2,row*2),(col*2+1,row*2+1),layer=10))
        cell.add(*gdstk.boolean(gdstk.rectangle((25,0),(35,10)),gdstk.rectangle((28,3),(32,7)),'not',layer=20))
        gds=self.root/'dense.gds';lib.write_gds(str(gds))
        stack=self.root/'stack.yaml';stack.write_text(yaml.safe_dump({'VIA':{'index':10,'type':0,'z':2,'height':3},'RING':{'index':20,'type':0,'z':1,'height':1}}))
        colors=self.root/'configs/blender/colors/aim';colors.mkdir(parents=True)
        for name in ['realistic','fancy','marketing']:(colors/(name+'.yaml')).write_text('layers: {}\n')
        hashes={p:pipeline.file_hash(p) for p in [gds,stack]}
        opts=pipeline.options({'preview_limit':1000});before=copy.deepcopy(opts)
        result=pipeline.preview_mesh(gds,stack,opts,root=self.root)
        self.assertLessEqual(result['triangles'],1000)
        self.assertTrue(result['preview_quality']['adapted'])
        self.assertGreater(result['preview_quality']['flat_components'],0)
        self.assertEqual(result['preview_quality']['components'],101)
        self.assertEqual(opts,before)
        self.assertEqual({p:pipeline.file_hash(p) for p in hashes},hashes)
        self.assertEqual(result['bounds'],[[0,0,1],[35,19,5]])
        self.assertEqual({x['name'] for x in result['meshes']},{'VIA','RING'})
        meshes={x['name']:x for x in result['meshes']}
        top=[];v=meshes['VIA']['positions']
        for i in range(0,len(v),9):
            t=v[i:i+9]
            if t[2]==t[5]==t[8]==5:top.append(Polygon([(t[0],t[1]),(t[3],t[4]),(t[6],t[7])]))
        for row in range(10):
            for col in range(10):self.assertTrue(any(face.covers(Point(col*2+.5,row*2+.5)) for face in top))
        v=meshes['RING']['positions']
        for i in range(0,len(v),9):
            t=v[i:i+9]
            if t[2]==t[5]==t[8]==2:self.assertFalse(Polygon([(t[0],t[1]),(t[3],t[4]),(t[6],t[7])]).contains(Point(30,5)))
        self.assertIn('Final Blender rendering',result['notes'])

    def test_auto_preview_simplifies_curves_before_using_flat_shapes(self):
        import gdstk
        lib=gdstk.Library();cell=lib.new_cell('CURVES')
        for i in range(8):cell.add(gdstk.ellipse((i*3,0),1,tolerance=.0002,layer=10))
        gds=self.root/'curves.gds';lib.write_gds(str(gds))
        stack=self.root/'stack.yaml';stack.write_text(yaml.safe_dump({'VIA':{'index':10,'type':0,'z':0,'height':1}}))
        colors=self.root/'configs/blender/colors/aim';colors.mkdir(parents=True)
        for name in ['realistic','fancy','marketing']:(colors/(name+'.yaml')).write_text('layers: {}\n')
        result=pipeline.preview_mesh(gds,stack,pipeline.options({'preview_tolerance':0,'preview_limit':1000}),root=self.root)
        quality=result['preview_quality']
        self.assertTrue(quality['adapted'])
        self.assertGreater(quality['effective_tolerance_um'],0)
        self.assertEqual(quality['flat_components'],0)
        self.assertLessEqual(result['triangles'],1000)
        self.assertEqual(quality['components'],8)

    def test_preview_detail_cannot_lower_production_commands_or_render_quality(self):
        cfg={key:'/tmp/input' for key in pipeline.FILES}|{'python':sys.executable,'blender':'blender'}
        full=pipeline.options({});coarse=pipeline.options({'preview_tolerance':10,'preview_limit':1000})
        self.assertEqual(pipeline.prepare_commands(cfg,full,self.root),pipeline.prepare_commands(cfg,coarse,self.root))
        self.assertEqual(pipeline.build_commands(cfg,full,self.root),pipeline.build_commands(cfg,coarse,self.root))
        self.assertEqual(pipeline.preset('full',CAMERA,full,[])['render'],pipeline.preset('coarse',CAMERA,coarse,[])['render'])
        self.assertEqual(full['samples'],1024)
        self.assertEqual((full['width'],full['height']),(3200,2000))

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
