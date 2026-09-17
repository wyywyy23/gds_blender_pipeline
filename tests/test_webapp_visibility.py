"""Regression for legacy preset aliases leaking past checked layer controls."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'webapp'))
sys.path.insert(0, str(ROOT / 'scripts'))
import pipeline
import aim_render_scene


class LayerVisibilityTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is required for browser regression tests')
    def test_real_browser_checkbox_payload_matches_blender_visibility(self):
        aliases = ['cladding', 'CLADDING_RENDER', 'LCLADDING_RENDER', ' cladding ',
                   'cladding-render', 'm1am', 'LM1AM_RENDER', 'si_rib', 'ld_render']
        expected = {name: aim_render_scene.normalize_layer_name(name) for name in aliases}
        result = subprocess.run([shutil.which('node'), str(ROOT / 'tests/webapp_visibility.js'),
                                 json.dumps(expected)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        browser = json.loads(result.stdout)
        self.assertEqual(browser['cases'], 10)
        self.assertEqual(browser['parityCases'], len(aliases))
        with tempfile.TemporaryDirectory() as temp:
            for mode, hidden in [('hidden', ['CLADDING_RENDER']), ('enabled', [])]:
                with self.subTest(mode=mode):
                    payload = browser[mode]
                    value = pipeline.preset('visibility_check', payload['camera'],
                                            pipeline.options({}), payload['hidden_layers'], payload['base'])
                    preset_path = Path(temp) / 'preset.yaml'
                    preset_path.write_text(yaml.safe_dump(value))
                    loaded = aim_render_scene.load_preset(preset_path)
                    self.assertEqual(loaded['runs'][0]['hide_layers'], hidden)
                    self.assertEqual(value['runs'][0]['hide_layers'], hidden)


if __name__ == '__main__':
    unittest.main()
