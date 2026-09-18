#!/usr/bin/env python3
"""Controlled experiments for band density, direction, thickness warp and lighting.

Run inside Blender. Production shaders remain unchanged; every variant reloads
its immutable input and uses identical camera, exposure, samples and geometry.
"""
from pathlib import Path
import argparse
import json
import math
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import aim_build_blender_scene as build
import aim_render_scene as render
from aim_cladding_iridescence import film_cap, softbox, reset_iridescence, OWNER
from aim_thin_film_probe import sha256, geometry_signature
from aim_cladding_pattern import visible_projection, adapt_pattern, pattern

VARIANTS = {
    'view_15': dict(film=True, center=400, span=500, angle=28, warp=25, strength=.85, energy=.4, sun=4.5, linked=True, sheen=.1, optical_cycles=1.5),
    'view_20': dict(film=True, center=400, span=500, angle=28, warp=25, strength=.85, energy=.4, sun=4.5, linked=True, sheen=.1, optical_cycles=2.0),

    'adaptive_15': dict(film=True, center=400, span=500, angle=28, warp=60, strength=.85, energy=.4, sun=4.5, linked=True, sheen=.1, adaptive_sweeps=1.5),
    'adaptive_20': dict(film=True, center=400, span=500, angle=28, warp=60, strength=.85, energy=.4, sun=4.5, linked=True, sheen=.1, adaptive_sweeps=2.0),

    'sparse_sun45_white04_sheen01': dict(film=True, center=400, span=500, angle=28, warp=140, cycles=1.5, strength=.85, energy=.4, sun=4.5, linked=True, sheen=.1),
    'sparse_sun35_white065_linked': dict(film=True, center=400, span=500, angle=28, warp=140, cycles=1.5, strength=.85, energy=.65, sun=3.5, linked=True),
    'sparse_sun4_white05_linked': dict(film=True, center=400, span=500, angle=28, warp=140, cycles=1.5, strength=.85, energy=.5, sun=4, linked=True),
    'sparse_sun5_white1': dict(film=True, center=400, span=500, angle=28, warp=140, cycles=1.5, strength=.6, energy=1, sun=5),
    'sparse_sun3_white1': dict(film=True, center=400, span=500, angle=28, warp=140, cycles=1.5, strength=.6, energy=1, sun=3),
    'sparse_sun1_white1': dict(film=True, center=400, span=500, angle=28, warp=140, cycles=1.5, strength=.6, energy=1, sun=1),
    'sparse_sun25_white065': dict(film=True, center=400, span=500, angle=28, warp=140, cycles=1.5, strength=.6, energy=.65, sun=2.5),
    'ripple_straight': dict(film=True, center=400, span=500, angle=28, warp=0, cycles=3, strength=.6, energy=1),
    'ripple_warp': dict(film=True, center=400, span=500, angle=28, warp=90, cycles=3, strength=.6, energy=1),
    'ripple_linked': dict(film=True, center=400, span=500, angle=28, warp=90, cycles=3, strength=.85, energy=.2, linked=True),
    'balanced_curved': dict(film=True, center=400, span=500, angle=28, warp=220, cycles=3, strength=1, energy=0, balanced=True),
    'balanced_straight': dict(film=True, center=400, span=500, angle=28, warp=0, cycles=3, strength=1, energy=0, balanced=True),
    'balanced_warp': dict(film=True, center=400, span=500, angle=28, warp=90, cycles=3, strength=1, energy=0, balanced=True),
    'off': dict(film=False, energy=0),
    'previous': dict(film=True, center=400, span=500, angle=0, warp=0, strength=.6, energy=1),
    'dense_tilted': dict(film=True, center=950, span=1500, angle=28, warp=0, strength=.6, energy=1),
    'warped': dict(film=True, center=950, span=1500, angle=28, warp=160, strength=.6, energy=1),
    'dimmed': dict(film=True, center=950, span=1500, angle=28, warp=160, strength=.85, energy=.2),
    'linked': dict(film=True, center=950, span=1500, angle=28, warp=160, strength=.85, energy=.2, linked=True),
}


def capture_linear(scene, directory, name):
    import bpy
    import numpy as np
    settings=scene.render.image_settings
    previous=(settings.file_format,settings.color_mode,settings.color_depth)
    settings.file_format='OPEN_EXR';settings.color_mode='RGBA';settings.color_depth='32'
    path=directory/(name+'.exr')
    bpy.data.images['Render Result'].save_render(str(path),scene=scene)
    settings.file_format,settings.color_mode,settings.color_depth=previous
    im=bpy.data.images.load(str(path),check_existing=False)
    values=np.empty(len(im.pixels),dtype=np.float32);im.pixels.foreach_get(values)
    values=values.reshape((im.size[1],im.size[0],4))[::-1,:,:3]
    np.save(directory/(name+'.linear.npy'),values)
    bpy.data.images.remove(im)
    lum=values @ np.array([.2126,.7152,.0722])
    return dict(mean_linear_luminance=float(lum.mean()),std_linear_luminance=float(lum.std()),percentiles=[float(x) for x in np.percentile(lum,[5,50,95])])


def make_native_lut(directory):
    """Sample Blender's native reflection in a unit white world, then keep its hue.

    This avoids inventing rainbow RGB stops. Normalization deliberately discards
    absolute film reflectance, so using this lookup is a presentation approximation.
    """
    import bpy
    import numpy as np
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene=bpy.context.scene
    scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=8
    scene.cycles.use_denoising=False;scene.cycles.use_adaptive_sampling=False
    scene.render.threads_mode='FIXED';scene.render.threads=6
    scene.render.resolution_x=768;scene.render.resolution_y=192;scene.render.resolution_percentage=100
    world=bpy.data.worlds.new('UnitWhite');world.use_nodes=True;world.node_tree.nodes['Background'].inputs['Color'].default_value=(1,1,1,1);world.node_tree.nodes['Background'].inputs['Strength'].default_value=1;scene.world=world
    bpy.ops.mesh.primitive_plane_add(size=2)
    plane=bpy.context.object;plane.scale=(4,1,1)
    mat=bpy.data.materials.new('NativeFilmCalibration');mat.use_nodes=True;plane.data.materials.append(mat)
    tree=mat.node_tree;n=tree.nodes;film=n['Principled BSDF']
    film.inputs['Base Color'].default_value=(0,0,0,1);film.inputs['Roughness'].default_value=0
    film.inputs['IOR'].default_value=1.45;film.inputs['Thin Film IOR'].default_value=2
    uv=n.new('ShaderNodeTexCoord');xy=n.new('ShaderNodeSeparateXYZ');tree.links.new(uv.outputs['UV'],xy.inputs[0])
    def mathnode(op,a,b=None):
        node=n.new('ShaderNodeMath');node.operation=op
        for i,v in enumerate([a,b]):
            if v is None:continue
            if isinstance(v,(int,float)):node.inputs[i].default_value=v
            else:tree.links.new(v,node.inputs[i])
        return node.outputs[0]
    thickness=mathnode('MULTIPLY',xy.outputs['X'],1500)
    cosine=mathnode('ADD',mathnode('MULTIPLY',xy.outputs['Y'],.8),.2)
    sine=mathnode('SQRT',mathnode('SUBTRACT',1,mathnode('MULTIPLY',cosine,cosine)))
    normal=n.new('ShaderNodeCombineXYZ');tree.links.new(sine,normal.inputs['X']);tree.links.new(cosine,normal.inputs['Z'])
    tree.links.new(normal.outputs[0],film.inputs['Normal']);tree.links.new(thickness,film.inputs['Thin Film Thickness'])
    bpy.ops.object.camera_add(location=(0,0,10));camera=bpy.context.object;camera.data.type='ORTHO';camera.data.ortho_scale=8;scene.camera=camera
    scene.render.image_settings.file_format='OPEN_EXR';scene.render.image_settings.color_mode='RGBA';scene.render.image_settings.color_depth='32'
    scene.render.filepath=str(directory/'native_reflectance.exr')
    bpy.ops.render.render(write_still=True)
    image=bpy.data.images.load(scene.render.filepath,check_existing=False)
    values=np.empty(len(image.pixels),np.float32);image.pixels.foreach_get(values);values=values.reshape((-1,4))
    rgb=np.maximum(values[:,:3],0);lum=rgb @ np.array([.2126,.7152,.0722]);rgb/=np.maximum(lum[:,None],1e-7)
    values[:,:3]=rgb;values[:,3]=1
    image.pixels.foreach_set(values.ravel());image.file_format='OPEN_EXR';image.filepath_raw=str(directory/'native_chromaticity.exr');image.save()
    (directory/'lut.json').write_text(json.dumps({'source':'Blender native Principled, black base, IOR 1.45, thin film IOR 2, roughness 0, unit white world','thickness_nm':[0,1500],'view_cosine':[.2,1],'size':[768,192],'normalized_Y_max_error':float(abs(rgb @ np.array([.2126,.7152,.0722])-1).max()),'note':'Normalizes reflected color; does not preserve physical absolute thin-film reflectance.'},indent=2))
    return directory/'native_chromaticity.exr'


def balanced_reflection(material, thickness, lookup, strength):
    """Color the existing reflection budget, without adding illumination."""
    import bpy
    tree=material.node_tree
    def node(kind):
        n=tree.nodes.new(kind);n[OWNER]=True;return n
    scale=node('ShaderNodeMath');scale.operation='DIVIDE';scale.inputs[1].default_value=1500;tree.links.new(thickness,scale.inputs[0])
    geometry=tree.nodes['AIM_Glass_Geometry']
    dot=node('ShaderNodeVectorMath');dot.operation='DOT_PRODUCT';tree.links.new(geometry.outputs['Incoming'],dot.inputs[0]);tree.links.new(geometry.outputs['True Normal'],dot.inputs[1])
    absolute=node('ShaderNodeMath');absolute.operation='ABSOLUTE';tree.links.new(dot.outputs['Value'],absolute.inputs[0])
    offset=node('ShaderNodeMath');offset.operation='SUBTRACT';offset.inputs[1].default_value=.2;tree.links.new(absolute.outputs[0],offset.inputs[0])
    angle=node('ShaderNodeMath');angle.operation='DIVIDE';angle.inputs[1].default_value=.8;tree.links.new(offset.outputs[0],angle.inputs[0])
    uv=node('ShaderNodeCombineXYZ');tree.links.new(scale.outputs[0],uv.inputs['X']);tree.links.new(angle.outputs[0],uv.inputs['Y'])
    texture=node('ShaderNodeTexImage');texture.image=bpy.data.images.load(str(lookup),check_existing=False);texture.image.pack();texture.interpolation='Linear';texture.extension='EXTEND';tree.links.new(uv.outputs[0],texture.inputs['Vector'])
    mix=node('ShaderNodeMixRGB');mix.blend_type='MIX';mix.inputs[0].default_value=strength;mix.inputs[1].default_value=(1,1,1,1);tree.links.new(texture.outputs['Color'],mix.inputs[2])
    for name in ['AIM_Glass_Glass reflection','AIM_Glass_Neutral studio reflection']:
        tree.links.new(mix.outputs[0],tree.nodes[name].inputs['Color'])
    return {'mode':'native-film chromaticity, fixed reflection budget','strength':strength,'lookup':str(lookup),'lookup_sha256':sha256(lookup),'added_light':False}


def main():
    import bpy
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True);p.add_argument('--preset',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--variants',nargs='+',choices=VARIANTS,default=list(VARIANTS))
    p.add_argument('--width',type=int,default=1000);p.add_argument('--height',type=int,default=625)
    p.add_argument('--lens-scale',type=float,default=1,help='Diagnostic camera lens/orthographic zoom multiplier')
    p.add_argument('--samples',type=int,default=64);p.add_argument('--save-blend',action='store_true')
    args=p.parse_args(sys.argv[sys.argv.index('--')+1:])
    if not math.isfinite(args.lens_scale) or args.lens_scale <= 0:
        p.error('--lens-scale must be positive and finite')
    args.input=args.input.resolve();args.preset=args.preset.resolve();args.output_dir=args.output_dir.resolve()
    args.output_dir.mkdir(parents=True,exist_ok=False)
    report={'source':str(args.input),'source_sha256':sha256(args.input),'preset':str(args.preset),'preset_sha256':sha256(args.preset),'script_sha256':sha256(Path(__file__)),'blender':bpy.app.version_string,'resolution':[args.width,args.height],'samples':args.samples,'variants':[]}
    preset=render.load_preset(args.preset)
    lookup=make_native_lut(args.output_dir) if any(VARIANTS[name].get('balanced') for name in args.variants) else None
    for name in args.variants:
        bpy.ops.wm.open_mainfile(filepath=str(args.input));scene=bpy.context.scene
        reset_iridescence(scene)
        before=geometry_signature(scene)
        build.apply_color_schema(bpy,ROOT/'configs/blender/colors/aim/realistic.yaml')
        render.apply_camera(scene,preset['camera']);render.apply_lighting(scene,preset['lighting']);render.apply_color_management(scene,preset['color_management'])
        scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.render.threads_mode='FIXED';scene.render.threads=6
        scene.cycles.seed=73;scene.cycles.samples=args.samples;scene.cycles.use_adaptive_sampling=False;scene.cycles.use_denoising=True
        for layer in scene.view_layers:layer.samples=0;layer.cycles.use_denoising=True
        scene.render.resolution_x=args.width;scene.render.resolution_y=args.height;scene.render.resolution_percentage=100
        scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGBA';scene.render.image_settings.color_depth='8'
        scene.render.film_transparent=False;scene.render.use_compositing=False
        cladding=bpy.data.objects['LCLADDING_RENDER'];cladding.hide_render=False;cladding.hide_set(False)
        mat=bpy.data.materials['Mat_CLADDING_RENDER']
        for i,item in enumerate(cladding.data.materials):
            if item is None:cladding.data.materials[i]=mat
        bpy.context.view_layer.update()
        if scene.camera.data.type == 'ORTHO':
            scene.camera.data.ortho_scale /= args.lens_scale
        else:
            scene.camera.data.lens *= args.lens_scale
        cfg=adapt_pattern(scene, cladding, VARIANTS[name])
        if 'sun' in cfg:
            if not preset['lighting']:
                raise ValueError('Sun-balance experiments require preset lighting.sun')
            render.apply_lighting(scene, {'sun': {**preset['lighting']['sun'], 'strength': cfg['sun']}})
        info={'name':name,'config':cfg,'camera_matrix':[list(row) for row in scene.camera.matrix_world],'exposure':scene.view_settings.exposure,'look':scene.view_settings.look,'lens_scale':args.lens_scale,'lens_mm':scene.camera.data.lens}
        info['sun_strength'] = scene.objects[preset['lighting']['sun']['object']].data.energy if preset['lighting'] else None
        if 'sheen' in cfg:
            sheen = mat.node_tree.nodes['AIM_Glass_Camera-only sheen'].inputs[1]
            info['neutral_sheen_before'] = sheen.default_value
            sheen.default_value = cfg['sheen']
            info['neutral_sheen_after'] = sheen.default_value
        if cfg['film']:
            if not cfg.get('balanced'):
                film_cap(mat,cfg['center'],2,cfg['strength'],0)
            thickness,info['pattern']=pattern(mat,cfg)
            if cfg.get('balanced'):
                info['balanced']=balanced_reflection(mat,thickness,lookup,cfg['strength'])
        if cfg['energy']:
            info['softbox']=softbox(scene,cfg['energy'])
            if cfg.get('linked'):
                light=next(o for o in scene.objects if o.type=='LIGHT' and o.get(OWNER))
                receivers=bpy.data.collections.new('Pattern_CladdingReceivers');receivers.objects.link(cladding)
                light.light_linking.receiver_collection=receivers
                info['linked_receivers']=[o.name for o in receivers.objects]
        assert geometry_signature(scene)==before
        info['geometry_unchanged']=True;info['png']=name+'.png'
        scene.render.filepath=str(args.output_dir/info['png'])
        if args.save_blend:bpy.ops.wm.save_as_mainfile(filepath=str(args.output_dir/(name+'.blend')))
        bpy.ops.render.render(write_still=True)
        info['png_sha256']=sha256(args.output_dir/info['png'])
        info['linear']=capture_linear(scene,args.output_dir,name)
        report['variants'].append(info)
        (args.output_dir/'manifest.json').write_text(json.dumps(report,indent=2))
        print('PATTERN_RENDERED='+name,flush=True)
    assert sha256(args.input)==report['source_sha256']

if __name__=='__main__':main()
