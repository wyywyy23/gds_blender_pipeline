"""Reflection geometry and Studio-to-render lighting handoff."""
import copy
import math
from pathlib import Path
import sys
import tempfile
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'webapp')]
import aim_render_scene as render
import pipeline


def axis(rotation):
    x, y, z = map(math.radians, rotation)
    return (math.cos(z)*math.sin(y)*math.cos(x)+math.sin(z)*math.sin(x),
            math.sin(z)*math.sin(y)*math.cos(x)-math.cos(z)*math.sin(x),
            math.cos(y)*math.cos(x))


class CameraLightingTests(unittest.TestCase):
    def test_baseline_reproduces_original_tx_sun(self):
        actual = axis(render.camera_relative_light_rotation([27.927,0.000008,-21.83]))
        expected = axis([27.927,0,21.83])
        for a,b in zip(actual,expected): self.assertAlmostEqual(a,b,places=10)

    def test_camera_light_angle_is_constant_for_arbitrary_angles(self):
        for angles in ([0,0,0], [27.927,0.000008,-21.83], [55,20,170], [0,60,90], [80,-25,-135], [180,0,30]):
            with self.subTest(angles=angles):
                view = axis(angles)
                source = axis(render.camera_relative_light_rotation(angles))
                self.assertAlmostEqual(sum(a*b for a,b in zip(view,source)),render.TX_CHECKERED_RELATIVE_DIRECTION[2],places=10)

    def load(self, value):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'preset.yaml'; path.write_text(yaml.safe_dump(value))
            return render.load_preset(path)

    def preset(self, base=None):
        return pipeline.preset('view', {'location':[10,-20,30], 'rotation_degrees':[35,15,75], 'lens_mm':100}, pipeline.options({}), [], base)

    def test_new_studio_and_legacy_missing_lighting(self):
        value = self.preset()
        self.assertEqual(value['lighting']['sun']['strength'],5)
        self.assertFalse(value['lighting']['sun']['follow_camera'])
        for value in (value, {k:v for k,v in value.items() if k!='lighting'}):
            loaded = self.load(value)
            self.assertEqual(loaded['lighting']['sun']['rotation_source'],'camera-z-follow')
            self.assertEqual(loaded['lighting']['sun']['rotation_degrees'],(27.927,0.0,118.66))

    def test_fixed_baseline_independent_of_camera_and_explicit_follow_supported(self):
        for angles in ([0,0,0], [65,20,-130]):
            value = self.preset({'lighting':{'sun':{'strength':5,'follow_camera':False,'rotation_degrees':[27.927,0,21.83]}}}); value['camera']['rotation_degrees'] = angles
            self.assertEqual(self.load(value)['lighting']['sun']['rotation_degrees'],render.TX_CHECKERED_SUN_ROTATION)
            value['lighting']['sun']['follow_camera'] = True
            loaded = self.load(value)['lighting']['sun']
            self.assertEqual(loaded['rotation_source'],'camera-derived default')
            self.assertEqual(loaded['rotation_degrees'],render.camera_relative_light_rotation(angles))

    def test_z_only_keeps_elevation_and_azimuth_offset(self):
        for angles in ([0,0,0], [27.927,0,-21.83], [70,20,120]):
            value = self.preset(); value['camera']['rotation_degrees'] = angles
            sun = self.load(value)['lighting']['sun']
            self.assertEqual(sun['rotation_source'],'camera-z-follow')
            self.assertAlmostEqual(sun['rotation_degrees'][0],27.927)
            self.assertAlmostEqual(sun['rotation_degrees'][2]-angles[2],43.66)

    def test_invalid_z_modes_refused(self):
        for fields in ({'follow_camera_z':'yes'}, {'follow_camera_z':True,'follow_camera':True}, {'follow_camera_z':True,'azimuth_offset_degrees':float('nan')}, {'follow_camera_z':True,'rotation_degrees':[27.927,5,0]}):
            with self.subTest(fields=fields), self.assertRaises(ValueError): self.load(self.preset({'lighting':{'sun':{'strength':5,**fields}}}))

    def test_explicit_fixed_light_preserved(self):
        lighting = {'sun':{'strength':2.5,'rotation_degrees':[25,5,60]}}
        value = self.preset({'lighting':copy.deepcopy(lighting)})
        self.assertEqual(value['lighting'],lighting)
        loaded = self.load(value)['lighting']['sun']
        self.assertEqual(loaded['rotation_source'],'preset')
        self.assertEqual(list(loaded['rotation_degrees']),[25,5,60])

    def test_invalid_follow_camera_refused(self):
        for sun in ({'strength':5,'follow_camera':'yes'}, {'strength':5,'follow_camera':False,'rotation_degrees':None}, {'strength':5,'camera_relative_direction':[0,0,0]}):
            with self.subTest(sun=sun), self.assertRaises(ValueError): self.load(self.preset({'lighting':{'sun':sun}}))


if __name__ == '__main__': unittest.main()
