"""Preset/browser checks plus Blender checks for the optional reflection path."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from aim_cladding_iridescence import normalize_appearance, apply_iridescence, reset_iridescence, OWNER, SUN_BASELINE, SHEEN_BASELINE

try:
    import bpy
except ImportError:
    bpy = None


class PresetTests(unittest.TestCase):
    def test_strict_config_and_legacy_default(self):
        self.assertFalse(normalize_appearance()['cladding_iridescence']['enabled'])
        for value in [True, {'typo': {}}, {'cladding_iridescence': True},
                      {'cladding_iridescence': {'enabled': 'false'}},
                      *({'cladding_iridescence': {'strength': x}} for x in
                        [True, -1, 1.01, float('nan'), float('inf')])]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_appearance(value)

    @unittest.skipUnless(shutil.which('node'), 'Node required')
    def test_browser_load_reset_and_saved_payload(self):
        # Reuse the existing DOM/Three fixture, exercising the actual app handlers.
        fixture = (ROOT / 'tests/webapp_visibility.js').read_text().split('const fixture =')[0]
        fixture = fixture.replace("path.resolve(__dirname, '..')", json.dumps(str(ROOT)))
        code = fixture + '''
const a=app();
const preset={name:'test',camera:{type:'PERSP'},render:{},runs:[{hide_layers:[]}]};
a.applyPreset({preset:{...preset,appearance:{cladding_iridescence:{enabled:true,strength:.75}}}});
assert.equal(a.payload().options.iridescence,true);
assert.equal(a.payload().options.iridescence_strength,.75);
a.applyPreset({preset});
assert.equal(a.payload().options.iridescence,false,'legacy preset disables a previous effect');
assert.equal(a.payload().options.iridescence_strength,.85);
a.state.specs=[{key:'iridescence',label:'Film',group:'Appearance',default:false},{key:'iridescence_strength',label:'Strength',group:'Appearance',default:.85,min:0,max:1,step:.05}];
a.applyPreset({preset});
const inputs=a.ids.get('options').querySelectorAll('input');
inputs[0].checked=true;inputs[0].onchange();
inputs[1].value='.45';inputs[1].oninput();
assert.equal(a.payload().options.iridescence,true);
assert.equal(a.payload().options.iridescence_strength,.45);
'''
        result = subprocess.run([shutil.which('node'), '-e', code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipIf(bpy is None, 'Run in Blender')
class BlenderTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.mesh.primitive_cube_add()
        self.obj = bpy.context.object
        self.obj.name = 'LCLADDING_RENDER'
        self.obj.rotation_euler = (.1, .2, .3)
        self.mat = bpy.data.materials.new('Mat_CLADDING_RENDER')
        self.mat.use_nodes = True
        self.obj.data.materials.append(self.mat)
        bpy.ops.object.camera_add(location=(0, 0, 15))
        bpy.context.scene.camera = bpy.context.object
        from aim_build_blender_scene import apply_color_schema
        self.colors = apply_color_schema
        self.on = {'cladding_iridescence': {'enabled': True, 'strength': .85}}
        bpy.ops.object.light_add(type='SUN')
        self.sun = bpy.context.object
        self.sun.name = 'Sun'
        self.sun.data.energy = 5

    def fingerprint(self):
        return ([tuple(v.co) for v in self.obj.data.vertices],
                [tuple(p.vertices) for p in self.obj.data.polygons],
                [tuple(row) for row in self.obj.matrix_world])

    def lights(self):
        return [o for o in bpy.context.scene.objects if o.get(OWNER)]

    def test_schemes_idempotence_camera_and_exact_off_restore(self):
        scene = bpy.context.scene
        bpy.context.view_layer.update()
        before = self.fingerprint()
        for scheme in ('realistic', 'fancy', 'marketing'):
            self.colors(bpy, ROOT / f'configs/blender/colors/aim/{scheme}.yaml')
            nodes = self.mat.node_tree.nodes
            original = {n.name for n in nodes if not n.get(OWNER)}
            original_sheen = nodes['AIM_Glass_Camera-only sheen'].inputs[1].default_value
            wall = nodes['Principled BSDF']
            apply_iridescence(scene, self.on)
            count = len(nodes)
            self.assertAlmostEqual(self.sun.data.energy, 4.5)
            self.assertAlmostEqual(nodes['AIM_Glass_Camera-only sheen'].inputs[1].default_value, original_sheen*.25)
            self.assertEqual(list(self.lights()[0].light_linking.receiver_collection.objects), [self.obj])
            self.assertEqual(len(self.lights()), 1)
            old_location = tuple(self.lights()[0].location)
            scene.camera.location.x += 3
            scene.camera.rotation_euler = (self.obj.location-scene.camera.location).to_track_quat('-Z', 'Y').to_euler()
            apply_iridescence(scene, self.on)
            self.assertEqual(len(nodes), count)
            self.assertAlmostEqual(self.sun.data.energy, 4.5)
            self.assertEqual(sum(bool(c.get(OWNER)) for c in bpy.data.collections), 1)
            self.assertEqual(len(self.lights()), 1)
            self.assertNotEqual(tuple(self.lights()[0].location), old_location)
            self.assertEqual(nodes['AIM_Glass_Refractive opening walls'].inputs[2].links[0].from_node, wall)
            self.assertEqual(tuple(nodes['AIM_Glass_Clear transmission'].inputs['Color'].default_value), (1, 1, 1, 1))
            self.assertEqual(self.fingerprint(), before)
            apply_iridescence(scene, {})
            self.assertEqual({n.name for n in nodes}, original)
            self.assertEqual(self.sun.data.energy, 5)
            self.assertEqual(nodes['AIM_Glass_Camera-only sheen'].inputs[1].default_value, original_sheen)
            self.assertNotIn(SUN_BASELINE, self.sun.data)
            self.assertNotIn(SHEEN_BASELINE, self.mat)
            self.assertFalse(any(c.get(OWNER) for c in bpy.data.collections))
            self.assertFalse(self.lights())
            self.assertEqual(nodes['AIM_Glass_Refractive opening walls'].inputs[1].links[0].from_node.name, 'AIM_Glass_Glass cap')

    def test_hidden_zero_and_portable_save(self):
        scene = bpy.context.scene
        self.colors(bpy, ROOT / 'configs/blender/colors/aim/realistic.yaml')
        self.obj.hide_render = True
        self.assertFalse(apply_iridescence(scene, self.on)['enabled'])
        self.assertFalse(self.lights())
        self.obj.hide_render = False
        self.assertFalse(apply_iridescence(scene, {'cladding_iridescence': {'enabled': True, 'strength': 0}})['enabled'])
        apply_iridescence(scene, self.on)
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / 'portable.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            self.assertEqual(len(self.lights()), 1)
            mat = bpy.data.materials['Mat_CLADDING_RENDER']
            self.assertAlmostEqual(mat.node_tree.nodes['AIM_Iridescence_ReflectionStrength'].inputs[0].default_value, .85)
            apply_iridescence(bpy.context.scene, {})
            self.assertFalse(self.lights())
            self.assertEqual(bpy.data.objects['Sun'].data.energy, 5)
            self.assertAlmostEqual(mat.node_tree.nodes['AIM_Glass_Camera-only sheen'].inputs[1].default_value, .4)


    def test_zoom_adapts_and_off_frame_restores(self):
        from aim_cladding_pattern import visible_projection
        scene = bpy.context.scene
        self.colors(bpy, ROOT / 'configs/blender/colors/aim/realistic.yaml')
        scene.camera.data.lens = 150
        result = apply_iridescence(scene, self.on)
        first = result['patterns'][self.obj.name]
        scene.camera.data.lens = 250
        result = apply_iridescence(scene, self.on)
        second = result['patterns'][self.obj.name]
        self.assertGreater(second['span'], first['span'])
        for fitted in (first, second):
            a = fitted['adaptive']
            self.assertAlmostEqual(fitted['span']*a['projection_span']/a['thickness_period_nm'], 1.5)
        scene.camera.rotation_euler.y = 3.141592653589793
        result = apply_iridescence(scene, self.on)
        self.assertFalse(result['enabled'])
        self.assertEqual(self.sun.data.energy, 5)
        self.assertFalse(self.lights())

    def test_prepare_changed_sun_and_disable_from_enabled_scene(self):
        import argparse
        import aim_prepare_render_blend as prepare
        import yaml
        scene = bpy.context.scene
        self.colors(bpy, ROOT / 'configs/blender/colors/aim/realistic.yaml')
        apply_iridescence(scene, self.on)
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            source = temp / 'enabled.blend'
            bpy.ops.wm.save_as_mainfile(filepath=str(source))
            value = dict(version=1, name='sun_change',
                camera=dict(type='PERSP',location=[0,0,15],rotation_degrees=[0,0,0],lens_mm=100),
                lighting=dict(sun=dict(object='Sun',strength=8)),
                render=dict(samples=8,denoise=True),output=dict(directory=str(temp)),
                appearance=self.on, runs=[dict(name='all',hide_layers=[])])
            preset = temp/'preset.yaml';preset.write_text(yaml.safe_dump(value))
            args = argparse.Namespace(preset=preset,run='all',output=temp/'changed.blend',
                overwrite_source=False,render_output=None,no_pack_resources=True)
            prepare.prepare_render_blend(args)
            self.assertAlmostEqual(scene.objects['Sun'].data.energy, 7.2, places=5)
            value['appearance'] = {};value['lighting']['sun']['strength'] = 3
            preset.write_text(yaml.safe_dump(value));args.output = temp/'disabled.blend'
            prepare.prepare_render_blend(args)
            self.assertEqual(scene.objects['Sun'].data.energy, 3)
            self.assertFalse(self.lights())

if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]])
