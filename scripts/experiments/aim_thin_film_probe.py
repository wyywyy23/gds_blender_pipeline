#!/usr/bin/env python3
"""Render isolated thin-film reflection experiments; never overwrite source scenes.

Run in Blender with --background --python this_file -- --input scene.blend
--preset preset.yaml --output-dir new-directory. Production material defaults
are imported unchanged, then only the top-cap reflection path is varied.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / 'scripts'))
import aim_build_blender_scene as build
import aim_render_scene as render
from aim_cladding_iridescence import film_cap, softbox


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def geometry_signature(scene):
    return {o.name: {'vertices': len(o.data.vertices), 'polygons': len(o.data.polygons),
                     'matrix': [list(row) for row in o.matrix_world],
                     'modifiers': [(m.name, m.type) for m in o.modifiers]}
            for o in scene.objects if o.type == 'MESH'}


def main():
    import bpy
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--preset', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--scheme', choices=['realistic', 'fancy', 'marketing'], default='realistic')
    parser.add_argument('--thicknesses', nargs='+', type=float, default=[0, 250, 400, 550])
    parser.add_argument('--thickness-span', type=float, default=0)
    parser.add_argument('--render-threads', type=int, default=6)
    parser.add_argument('--camera-tilt', type=float, default=0)
    parser.add_argument('--strength', type=float, default=0.35)
    parser.add_argument('--film-ior', type=float, default=2.0)
    parser.add_argument('--lighting', choices=['current', 'softbox'], default='current')
    parser.add_argument('--softbox-energy', type=float, default=2.0)
    parser.add_argument('--width', type=int, default=800)
    parser.add_argument('--height', type=int, default=500)
    parser.add_argument('--samples', type=int, default=48)
    parser.add_argument('--baseline', action='store_true')
    parser.add_argument('--save-blend', action='store_true')
    parser.add_argument('--camera-yaw', type=float, default=0)
    parser.add_argument('--device', choices=['CPU', 'METAL'], default='CPU')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    if not 0 <= args.strength <= 1 or args.film_ior <= 1 or min(args.thicknesses) < 0:
        parser.error('Require 0 <= strength <= 1, film IOR > 1 and nonnegative thickness')
    if args.thickness_span < 0 or any(t > 0 and t < args.thickness_span / 2 for t in args.thicknesses):
        parser.error('Thickness range must stay nonnegative')
    args.input = args.input.resolve()
    args.preset = args.preset.resolve()
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    source_hash = sha256(args.input)
    preset = render.load_preset(args.preset)
    configs = REPO_ROOT / 'configs/blender/colors/aim' / (args.scheme + '.yaml')
    variants = [('baseline', None)] if args.baseline else []
    variants += [(f'film_{value:g}nm', value) for value in args.thicknesses]
    report = {'blender': bpy.app.version_string, 'source': str(args.input),
              'source_sha256': source_hash, 'preset': str(args.preset),
              'preset_sha256': sha256(args.preset), 'script_sha256': sha256(Path(__file__)),
              'scheme': args.scheme, 'lighting': args.lighting, 'samples': args.samples,
              'resolution': [args.width, args.height], 'variants': []}
    for name, thickness in variants:
        bpy.ops.wm.open_mainfile(filepath=str(args.input))
        scene = bpy.context.scene
        before = geometry_signature(scene)
        build.apply_color_schema(bpy, configs)
        render.apply_camera(scene, preset['camera'])
        render.apply_lighting(scene, preset['lighting'])
        render.apply_color_management(scene, preset['color_management'])
        bpy.context.view_layer.update()
        if args.camera_yaw:
            from mathutils import Matrix, Vector
            c = scene.camera
            v = c.matrix_world.to_quaternion() @ Vector((0, 0, -1))
            pivot = c.location + v * (-c.location.z / v.z)
            matrix = Matrix.Translation(pivot) @ Matrix.Rotation(math.radians(args.camera_yaw), 4, 'Z') @ Matrix.Translation(-pivot)
            c.matrix_world = matrix @ c.matrix_world
            bpy.context.view_layer.update()
        light_info = softbox(scene, args.softbox_energy) if args.lighting == 'softbox' else None
        if args.camera_tilt:
            from mathutils import Matrix, Vector
            c = scene.camera
            v = c.matrix_world.to_quaternion() @ Vector((0, 0, -1))
            pivot = c.location + v * (-c.location.z / v.z)
            right = c.matrix_world.to_quaternion() @ Vector((1, 0, 0))
            matrix = Matrix.Translation(pivot) @ Matrix.Rotation(math.radians(args.camera_tilt), 4, right) @ Matrix.Translation(-pivot)
            c.matrix_world = matrix @ c.matrix_world
            bpy.context.view_layer.update()
        scene.render.engine = 'CYCLES'
        scene.render.threads_mode = 'FIXED'
        scene.render.threads = args.render_threads
        scene.cycles.samples = args.samples
        scene.cycles.seed = 73
        scene.cycles.use_adaptive_sampling = False
        scene.cycles.use_denoising = True
        for layer in scene.view_layers:
            layer.samples = 0
            layer.cycles.use_denoising = True
        scene.render.resolution_x = args.width
        scene.render.resolution_y = args.height
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = 'PNG'
        scene.render.image_settings.color_mode = 'RGBA'
        scene.render.film_transparent = False
        scene.render.use_compositing = False
        if args.device == 'METAL':
            preferences = bpy.context.preferences.addons['cycles'].preferences
            preferences.compute_device_type = 'METAL'
            preferences.get_devices()
            for device in preferences.devices:
                device.use = device.type == 'METAL'
            if not any(d.use for d in preferences.devices):
                raise RuntimeError('No Metal GPU available')
            scene.cycles.device = 'GPU'
        else:
            scene.cycles.device = 'CPU'
        cladding = bpy.data.objects['LCLADDING_RENDER']
        cladding.hide_render = False
        cladding.hide_set(False)
        material = bpy.data.materials['Mat_CLADDING_RENDER']
        # Old imported booleans sometimes carry an empty second material slot.
        # Fill only missing slots with the same cladding material in every variant.
        for i, item in enumerate(cladding.data.materials):
            if item is None:
                cladding.data.materials[i] = material
        info = {'name': name, 'camera_matrix': [list(row) for row in scene.camera.matrix_world]}
        if thickness is not None:
            info['film'] = film_cap(material, thickness, args.film_ior, args.strength, args.thickness_span)
        if light_info is not None:
            info['softbox'] = light_info
        info['camera_tilt_degrees'] = args.camera_tilt
        assert geometry_signature(scene) == before, 'Geometry changed during shader experiment'
        info['geometry_unchanged'] = True
        info['png'] = name + '.png'
        scene.render.filepath = str(args.output_dir / info['png'])
        if args.save_blend:
            bpy.ops.wm.save_as_mainfile(filepath=str(args.output_dir / (name + '.blend')))
        bpy.ops.render.render(write_still=True)
        info['png_sha256'] = sha256(args.output_dir / info['png'])
        report['variants'].append(info)
        (args.output_dir / 'manifest.json').write_text(json.dumps(report, indent=2))
        print('EXPERIMENT_RENDERED=' + name, flush=True)
    assert sha256(args.input) == source_hash, 'Source scene changed'


if __name__ == '__main__':
    main()
