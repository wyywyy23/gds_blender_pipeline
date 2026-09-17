"""Content-checked local layout library. Originals and generated revisions never overwrite."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid
from pipeline import FILES, file_hash, resolve_file

PREPROCESS_KEYS = ('metal_fillet', 'via_fillet', 'max_vertices', 'undercut', 'passivation')
PREPROCESS_SCRIPTS = ('aim_generate_doping_render_layers.py', 'aim_merge_render_layers.py',
                      'aim_build_layer_registry.py', 'aim_generate_blendergds_config.py', 'aim_preprocess_gds.py')
VISUAL_FILES = ('doping.yaml', 'layers.yaml', 'registry.yaml', 'stack.yaml')


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def slug(name):
    stem = re.sub(r'(?i)[_-]raw$', '', Path(name).stem)
    return re.sub(r'[^A-Za-z0-9_-]+', '_', stem).strip('_-')[:48] or 'layout'


def canonical_settings(value):
    """Keep numeric identity stable across JSON, YAML and browser round trips."""
    if isinstance(value, dict):
        return {key: canonical_settings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [canonical_settings(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def automatic_preset(value):
    value = canonical_settings(copy.deepcopy(value))
    # Browser mesh budgets do not affect the built scene or rendered output.
    value['webapp']['options'] = {
        key: item for key, item in value['webapp']['options'].items()
        if key not in ('preview_tolerance', 'preview_limit')
    }
    settings = {key: item for key, item in value.items() if key not in ('name', 'output', 'webapp')}
    settings['options'] = value['webapp']['options']
    fingerprint = hashlib.sha256(json.dumps(settings, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    lens = format(value['camera']['lens_mm'], '.6g').replace('.', 'p')
    render = value['render']
    value['name'] = f"auto_{settings['options']['scheme']}_{lens}mm_{render['resolution_x']}x{render['resolution_y']}_{fingerprint[:12]}"
    value['webapp']['automatic'] = {'settings_sha256': fingerprint}
    return value


def valid_gds(path):
    path = Path(path)
    if not path.is_file() or path.suffix.lower() != '.gds':
        raise ValueError('Choose an existing .gds file')
    with path.open('rb') as stream:
        if stream.read(4) != b'\x00\x06\x00\x02':
            raise ValueError('This file does not have a valid binary GDSII header')


class Library:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.base = self.root / '.local/webapp/layouts'
        self.base.mkdir(parents=True, exist_ok=True)
        self.safe(self.base)

    def safe(self, path):
        path = Path(path)
        path.resolve().relative_to(self.base.resolve())
        for part in (path, *path.parents):
            if part.is_symlink():
                raise ValueError('Library paths must not contain symlinks')
            if part == self.root:
                break
        return path

    def layout_dir(self, key):
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,48}--[a-f0-9]{12}', key):
            raise ValueError('Invalid layout identity')
        return self.safe(self.base / key)

    def layout(self, key):
        value = read_json(self.layout_dir(key) / 'layout.json')
        if value.get('id') != key:
            raise ValueError('Layout identity mismatch')
        return value

    def raw(self, layout):
        path = self.safe(self.layout_dir(layout['id']) / 'raw' / (layout['name'] + '_raw.gds'))
        if not path.is_file() or file_hash(path) != layout['raw_sha256']:
            raise ValueError('The archived raw GDS changed or is missing. Import the source again as a new raw revision.')
        return path

    def catalog(self):
        items = []
        for path in sorted(self.base.glob('*/layout.json')):
            try:
                value = self.layout(path.parent.name)
                value['directory'] = path.parent.relative_to(self.root).as_posix()
                value['raw_ready'] = self.raw(value).is_file()
                items.append(value)
            except (OSError, ValueError, KeyError):
                continue
        return sorted(items, key=lambda item: item['created'], reverse=True)

    def import_raw(self, source, original_name=None, origin=None):
        source = resolve_file(self.root, str(source))
        valid_gds(source)
        sha = file_hash(source)
        name = slug(original_name or source.name)
        key = name + '--' + sha[:12]
        directory = self.layout_dir(key)
        source_ref = origin or str(source)
        if (directory / 'layout.json').exists():
            layout = self.layout(key)
            if layout['raw_sha256'] != sha:
                raise ValueError('Raw identity collision; existing data was preserved')
            self.raw(layout)
            if source_ref not in layout['sources'] and source != self.raw(layout):
                layout['sources'].append(source_ref)
                write_json(directory / 'layout.json', layout)
            return layout
        if directory.exists():
            raise ValueError('Incomplete layout archive exists; inspect it before importing again')
        temporary = self.safe(self.base / ('.import-' + uuid.uuid4().hex))
        try:
            (temporary / 'raw').mkdir(parents=True)
            target = temporary / 'raw' / (name + '_raw.gds')
            shutil.copyfile(source, target)
            if file_hash(target) != sha or file_hash(source) != sha:
                raise ValueError('Source changed during import; retry with a stable GDS')
            layout = dict(id=key, name=name, original_name=original_name or source.name,
                          raw_sha256=sha, size_bytes=target.stat().st_size,
                          raw_path=f'raw/{name}_raw.gds', sources=[source_ref], created=time.time())
            write_json(temporary / 'layout.json', layout)
            temporary.rename(directory)
            return layout
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def environment(self, python):
        code = 'import json,sys,importlib.metadata as m;print(json.dumps({"python":sys.version,"packages":{k:m.version(k) for k in ["gdstk","gdsfactory","kfactory","shapely","PyYAML"]}},sort_keys=True))'
        result = subprocess.run([python, '-c', code], capture_output=True, text=True, timeout=25)
        if result.returncode:
            raise ValueError('Cannot inspect preprocessing environment; check the configured Python packages')
        return json.loads(result.stdout)

    def recipe(self, layout, config, opts):
        self.raw(layout)
        files = {key: file_hash(config[key]) for key in FILES if key != 'gds'}
        scripts = {name: file_hash(self.root / 'scripts' / name) for name in PREPROCESS_SCRIPTS}
        # This module's version plus command construction cover adapter changes as well.
        scripts['webapp/pipeline.py'] = file_hash(self.root / 'webapp/pipeline.py')
        value = dict(format=1, raw_sha256=layout['raw_sha256'], inputs=files, scripts=scripts,
                     options={key: opts[key] for key in PREPROCESS_KEYS}, environment=self.environment(config['python']))
        return dict(fingerprint=digest(value), recipe=value)

    def visual_dir(self, layout, version):
        if not isinstance(version, str) or not re.fullmatch(r'v\d{4,}', version):
            raise ValueError('Invalid visual revision')
        return self.safe(self.layout_dir(layout['id']) / 'visual' / version)

    def visual(self, layout, version):
        directory = self.visual_dir(layout, version)
        value = read_json(directory / 'manifest.json')
        if value.get('status') != 'ready' or value.get('id') != version or value.get('layout_id') != layout['id']:
            raise ValueError('Visual revision is incomplete')
        required = [layout['name'] + '_visual.gds', *VISUAL_FILES]
        if set(value.get('outputs', {})) != set(required):
            raise ValueError('Visual output manifest is incomplete')
        for name, sha in value['outputs'].items():
            path = self.safe(directory / name)
            if not path.is_file() or file_hash(path) != sha:
                raise ValueError('Visual output is missing or changed; regenerate visual GDS')
        return value

    def visuals(self, layout):
        result = []
        directory = self.layout_dir(layout['id']) / 'visual'
        for path in sorted(directory.glob('v*/manifest.json'), reverse=True):
            try:
                value = self.visual(layout, path.parent.name)
                result.append(value)
            except (ValueError, OSError, KeyError):
                continue
        return result

    def check_visual(self, layout, current):
        valid = self.visuals(layout)
        matching = next((value for value in valid if value['fingerprint'] == current['fingerprint']), None)
        if matching:
            return dict(state='ready', reason=f"Ready · {matching['id']} will be reused", visual=matching)
        reasons = []
        if valid:
            previous = valid[0]['recipe']
            for key, label in [('raw_sha256', 'raw GDS'), ('inputs', 'configuration files'), ('scripts', 'preprocessing code'), ('options', 'preprocessing options'), ('environment', 'Python environment')]:
                if previous.get(key) != current['recipe'].get(key):
                    reasons.append(label)
        return dict(state='stale' if valid else 'missing', reason=('Changed: ' + ', '.join(reasons)) if reasons else 'No valid visual GDS; generation is required', visual=None)

    def new_visual(self, layout, current, forced):
        directory = self.layout_dir(layout['id']) / 'visual'
        directory.mkdir(exist_ok=True)
        numbers = [int(p.name[1:]) for p in directory.iterdir() if re.fullmatch(r'v\d{4,}', p.name)]
        version = f'v{max(numbers, default=0) + 1:04d}'
        target = self.visual_dir(layout, version)
        target.mkdir()
        value = dict(id=version, layout_id=layout['id'], status='generating', created=time.time(), forced=forced, **current)
        write_json(target / 'manifest.json', value)
        return value, target

    def finish_visual(self, layout, value):
        directory = self.visual_dir(layout, value['id'])
        outputs = [layout['name'] + '_visual.gds', *VISUAL_FILES]
        hashes = {name: file_hash(self.safe(directory / name)) for name in outputs}
        value = dict(value, status='ready', outputs=hashes)
        write_json(directory / 'manifest.json', value)
        return self.visual(layout, value['id'])

    def allocate_run(self, layout, action, preset_name):
        directory = self.layout_dir(layout['id']) / 'runs'
        directory.mkdir(exist_ok=True)
        numbers = [int(p.name.split('_')[1]) for p in directory.iterdir() if re.match(r'run_\d+_', p.name)]
        name = f"run_{max(numbers, default=0) + 1:04d}_{slug(preset_name)}_{action}"
        target = self.safe(directory / name)
        target.mkdir()
        return target

    def save_preset(self, layout, value, expected_etag=None):
        name = value['name']
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', name):
            raise ValueError('Invalid preset name')
        directory = self.safe(self.layout_dir(layout['id']) / 'presets' / name)
        directory.mkdir(parents=True, exist_ok=True)
        existing = sorted(directory.glob('p[0-9]*.yaml'))
        latest = existing[-1] if existing else None
        if latest and file_hash(latest) != expected_etag:
            raise ValueError('This preset changed or exists. Load its latest version first, or use a new name.')
        import yaml
        body = yaml.safe_dump(value, sort_keys=False)
        if latest and latest.read_text() == body:
            target = latest
        else:
            number = max([int(p.stem[1:]) for p in existing], default=0) + 1
            target = self.safe(directory / f'p{number:04d}.yaml')
            with target.open('x') as stream:
                stream.write(body)
        return dict(file=f"{layout['id']}/{name}/{target.stem}", path=target.relative_to(self.root).as_posix(), etag=file_hash(target), preset=value)

    def save_automatic_preset(self, layout, value):
        """Reuse exact settings + lineage, or append an immutable linked version.

        Caller holds the studio lock. Existing named/legacy presets are never edited.
        A short-name collision is resolved with a new version, never an overwrite.
        """
        import yaml
        value = automatic_preset(value)
        directory = self.safe(self.layout_dir(layout['id']) / 'presets' / value['name'])
        existing = sorted(directory.glob('p[0-9]*.yaml'))
        for path in existing:
            self.safe(path)
            stored = yaml.safe_load(path.read_text())
            if canonical_settings(stored) == value:
                return dict(file=f"{layout['id']}/{value['name']}/{path.stem}",
                            path=path.relative_to(self.root).as_posix(), etag=file_hash(path),
                            preset=stored, reused=True)
        latest = self.safe(existing[-1]) if existing else None
        result = self.save_preset(layout, value, file_hash(latest) if latest else None)
        return dict(result, reused=False)

    def preset_entries(self, layout):
        entries = []
        if layout:
            directory = self.layout_dir(layout['id'])
            for path in sorted(directory.glob('presets/*/p[0-9]*.yaml'), reverse=True):
                self.safe(path)
                import yaml
                visual_id=(yaml.safe_load(path.read_text()) or {}).get('webapp',{}).get('lineage',{}).get('visual_id','unlinked')
                entries.append(dict(id=f"{layout['id']}/{path.parent.name}/{path.stem}", label=f'{path.parent.name} · {path.stem} · {visual_id}', visual_id=visual_id, path=path.relative_to(self.root).as_posix()))
        return entries

    def preset_path(self, identifier):
        parts = identifier.split('/')
        if len(parts) != 3 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', parts[1]) or not re.fullmatch(r'p\d{4,}', parts[2]):
            raise ValueError('Invalid preset identifier')
        return self.safe(self.layout_dir(parts[0]) / 'presets' / parts[1] / (parts[2] + '.yaml'))

    def file(self, layout, relative):
        path = self.safe(self.layout_dir(layout['id']) / relative)
        if Path(relative).is_absolute() or '..' in Path(relative).parts:
            raise ValueError('Invalid artifact path')
        allowed = {layout['raw_path'], 'layout.json'}
        for visual in self.visuals(layout):
            allowed.update(f"visual/{visual['id']}/{name}" for name in [*visual['outputs'], 'manifest.json'])
        allowed.update(entry['path'].split('/' + layout['id'] + '/', 1)[1] for entry in self.preset_entries(layout))
        if relative not in allowed or not path.is_file():
            raise ValueError('Unknown library artifact')
        return path
