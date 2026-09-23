"""Controls must follow the operation needed to apply them, including build gating."""
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'webapp'))
import pipeline
import storage


class Containers(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.ancestors = [], {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ancestors[attrs['id']] = list(self.stack)
        if tag not in {'meta', 'link', 'input', 'br', 'img'}:
            self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


class OptionStagesTests(unittest.TestCase):
    def test_visual_controls_exactly_cover_preprocessing_identity(self):
        by_stage = {}
        for spec in pipeline.OPTIONS:
            self.assertIn(spec['stage'], {'visual', 'scene', 'preset', 'preview'})
            by_stage.setdefault(spec['stage'], set()).add(spec['key'])
        self.assertEqual(by_stage['visual'], set(storage.PREPROCESS_KEYS))
        self.assertEqual(by_stage['scene'], {'scheme', 'metal_bevel', 'bevel_width', 'cladding', 'z_scale', 'merge_layers'})
        self.assertEqual(by_stage['preview'], {'preview_tolerance', 'preview_limit'})
        self.assertTrue({'width', 'height', 'samples', 'iridescence'} <= by_stage['preset'])

    def test_controls_have_the_requested_page_regions(self):
        parser = Containers()
        parser.feed((ROOT/'webapp/static/index.html').read_text())
        for control, side in [('visual-options', 'left'), ('options', 'left'), ('preset-options', 'right'), ('layers', 'right'), ('camera-fields', 'right')]:
            self.assertIn(('aside', {'class': side}), parser.ancestors[control])
        self.assertIn(('div', {'class': 'center'}), parser.ancestors['preview-options'])
        source = (ROOT/'webapp/static/index.html').read_text()
        self.assertLess(source.index('id="visual-options"'), source.index('id="preview"'))
        self.assertLess(source.index('id="preview"'), source.index('id="options"'))

    @unittest.skipUnless(shutil.which('node'), 'Node.js is required for browser handler tests')
    def test_actual_browser_handlers_gate_only_visual_changes(self):
        result = subprocess.run([shutil.which('node'), str(ROOT/'tests/webapp_stages.js'), json.dumps(pipeline.OPTIONS)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout)['options'], len(pipeline.OPTIONS))


if __name__ == '__main__':
    unittest.main()
