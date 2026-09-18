"""Deterministic plans shared by the local web service and its tests."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    'gds': ('GDS layout', ''),
    'tech': ('AIM tech.py', 'external_pdks/AIMPhotonics_ACT1/tech.py'),
    'raw_custom': ('Custom raw layers', 'configs/aim/raw_custom_layers.yaml'),
    'doping_rules': ('Doping rules', 'configs/aim/doping_rules.yaml'),
    'render_static': ('Static render stack', 'configs/aim/render_layers.static.yaml'),
}
# Geometry defaults mirror the production Makefile; Studio uses higher-quality render defaults.
OPTIONS = [
    dict(key='scheme', label='Color scheme', group='Appearance', default='realistic', choices=['realistic','fancy','marketing']),
    dict(key='iridescence', label='Adaptive iridescence (final render)', group='Appearance', default=False),
    dict(key='iridescence_strength', label='Iridescence strength', group='Appearance', default=0.85, min=0, max=1, step=0.05),
    dict(key='metal_bevel', label='Metal cap Z bevel', group='Appearance', default=True),
    dict(key='bevel_width', label='Cap bevel width (µm)', group='Appearance', default=0.05, min=0, max=5, step=0.01),
    dict(key='metal_fillet', label='Metal XY rounding (µm)', group='Appearance', default=0.20, min=0, max=10, step=0.05),
    dict(key='via_fillet', label='Contact / via XY rounding (µm)', group='Appearance', default=0.10, min=0, max=10, step=0.05),
    dict(key='undercut', label='TUAM undercut', group='Geometry', default=True),
    dict(key='passivation', label='PAAM opening', group='Geometry', default=True),
    dict(key='cladding', label='Cladding geometry', group='Geometry', default='boolean', choices=['boolean','solid']),
    dict(key='z_scale', label='Vertical scale', group='Geometry', default=1.0, min=0.01, max=100, step=0.1),
    dict(key='denoise', label='Denoise', group='Render', default=True),
    dict(key='transparent', label='Transparent background', group='Render', default=False),
    dict(key='width', label='Image width', group='Render', default=3200, min=64, max=8192, step=1),
    dict(key='height', label='Image height', group='Render', default=2000, min=64, max=8192, step=1),
    dict(key='samples', label='Cycles samples', group='Performance', default=1024, min=1, max=16384, step=1),
    dict(key='adaptive_sampling', label='Adaptive sampling', group='Performance', default=True),
    dict(key='merge_layers', label='Merge objects per layer', group='Performance', default=False),
    dict(key='max_vertices', label='GDS fracture vertex limit', group='Performance', default=256, min=4, max=8190, step=1),
    dict(key='preview_tolerance', label='Preview simplification (µm)', group='Performance', default=0.05, min=0, max=20, step=0.01),
    dict(key='preview_limit', label='Preview triangle budget', group='Performance', default=1000000, min=1000, max=2000000, step=1),
]
DEFAULTS = {x['key']:x['default'] for x in OPTIONS}


def number(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{label} must be a finite number in [{low}, {high}]')
    return value


def options(payload):
    if not isinstance(payload,dict) or set(payload)-set(DEFAULTS):
        raise ValueError('Unknown options')
    result = DEFAULTS | payload
    for spec in OPTIONS:
        value = result[spec['key']]
        if 'choices' in spec:
            if value not in spec['choices']: raise ValueError(f"Invalid {spec['key']}")
        elif isinstance(spec['default'],bool):
            if not isinstance(value,bool): raise ValueError(f"{spec['key']} must be boolean")
        else:
            number(value,spec['key'],spec['min'],spec['max'])
            if spec['step']==1 and int(value)!=value: raise ValueError(f"{spec['key']} must be an integer")
            if spec['step']==1: result[spec['key']]=int(value)
    return result


def vector(value,label):
    if not isinstance(value,list) or len(value)!=3: raise ValueError(f'{label} requires X, Y, Z')
    return [number(x,label,-1e9,1e9) for x in value]


def camera(value):
    if not isinstance(value,dict): raise ValueError('Camera required')
    result = dict(object='Camera',type='PERSP',location=vector(value.get('location'),'location'),rotation_degrees=vector(value.get('rotation_degrees'),'rotation'),lens_mm=number(value.get('lens_mm'),'lens',1,2000),sensor_width_mm=number(value.get('sensor_width_mm',36),'sensor width',1,100),clip_start=number(value.get('clip_start',0.001),'near clip',1e-6,100),clip_end=number(value.get('clip_end',1e7),'far clip',1,1e10))
    if result['clip_end']<=result['clip_start']: raise ValueError('Far clip must exceed near clip')
    dof=value.get('dof',{})
    if not isinstance(dof,dict) or not isinstance(dof.get('enabled',False),bool): raise ValueError('Invalid depth of field')
    result['dof']={'enabled':dof.get('enabled',False),'focus_point':vector(dof.get('focus_point',[0,0,0]),'focus point'),'aperture_fstop':number(dof.get('aperture_fstop',5.6),'f-stop',0.1,128)}
    return result


def preset(name, cam, opts, hidden, base=None):
    import re
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}',name): raise ValueError('Preset name: letters, numbers, _ and - only')
    if not isinstance(hidden,list) or len(hidden)>500 or any(not isinstance(s,str) or not re.fullmatch(r'[A-Za-z0-9_]+',s) for s in hidden): raise ValueError('Invalid layer names')
    # Preserve existing lighting and color-management when editing a CLI preset.
    result={k:v for k,v in (base or {}).items() if k in ('lighting','color_management')}
    result.update(version=1,name=name,camera=camera(cam),render=dict(engine='CYCLES',device='CPU',samples=opts['samples'],denoise=opts['denoise'],adaptive_sampling=opts['adaptive_sampling'],transparent=opts['transparent'],file_format='PNG',resolution_x=opts['width'],resolution_y=opts['height']),output={'directory':'.local/webapp/renders'},runs=[dict(name='web_view',hide_layers=sorted(set(hidden)))],webapp={'options':opts})
    result['appearance'] = {'cladding_iridescence': {'enabled': opts['iridescence'], 'strength': opts['iridescence_strength']}}
    return result


def file_hash(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def resolve_file(root, raw):
    path=Path(raw).expanduser()
    return (root/path).resolve() if not path.is_absolute() else path.resolve()


def prepare_commands(config, opts, directory, *, preview=False, root=ROOT, visual_name="visual.gds"):
    """Invoke existing scripts with argv, never shell interpolation or Make variables."""
    py=config.get('python') or sys.executable
    def script(name,*args): return [py,str(root/'scripts'/name),*map(str,args)]
    p=directory
    commands=[
        script('aim_generate_doping_render_layers.py','--doping-rules',config['doping_rules'],'--output',p/'doping.yaml'),
        script('aim_merge_render_layers.py','--doping',p/'doping.yaml','--static',config['render_static'],'--output',p/'layers.yaml'),
        script('aim_build_layer_registry.py','--tech',config['tech'],'--raw-custom',config['raw_custom'],'--render-layers',p/'layers.yaml','--doping-rules',config['doping_rules'],'--output',p/'registry.yaml'),
        script('aim_generate_blendergds_config.py','--render-layers',p/'layers.yaml','--output',p/'stack.yaml'),
        script('aim_preprocess_gds.py','--input',config['gds'],'--registry',p/'registry.yaml','--output',p/visual_name,'--max-polygon-vertices',opts['max_vertices'],'--presentation-metal-xy-fillet-width-um',opts['metal_fillet'],'--presentation-contact-via-xy-fillet-width-um',opts['via_fillet']),
    ]
    if not opts['undercut']:commands[-1].append('--no-undercut')
    if not opts['passivation']:commands[-1].append('--no-passivation-opening')
    return commands


def build_commands(config,opts,directory,root=ROOT,visual_directory=None,visual_name="visual.gds",build_directory=None):
    commands=prepare_commands(config,opts,directory,root=root)
    source=visual_directory or directory
    output=build_directory or directory
    sidecars=[]
    available=None
    if (source/visual_name).is_file():
        import gdstk
        library=gdstk.read_gds(str(source/visual_name))
        stack=yaml.safe_load((source/'stack.yaml').read_text())
        available={name for name,spec in stack.items() if any(cell.get_polygons(layer=spec['index'],datatype=spec['type']) for cell in library.top_level())}
    if opts['metal_bevel'] and opts['bevel_width']>0:
        for layer in ('M1AM_RENDER','M2AM_RENDER','MLAM_RENDER'):
            if available is not None and layer not in available:continue
            sidecar=output/(layer+'.npz');sidecars.extend(['--presentation-metal-z-bevel-sidecar',str(sidecar)])
            commands.append([config['python'],str(root/'scripts/aim_generate_unfractured_layer_mesh.py'),'--gds',str(source/visual_name),'--stack-config',str(source/'stack.yaml'),'--layer-name',layer,'--output',str(sidecar)])
    # Bevel is inapplicable when the actual visual has no presentation metal.
    # Keep the requested preference, but never enable a shader without its sidecars.
    effective_bevel_width = opts['bevel_width'] if sidecars else 0
    commands.append([config['blender'],'--background','--python-exit-code','1','--python',str(root/'scripts/aim_build_blender_scene.py'),'--','--gds',str(source/visual_name),'--stack-config',str(source/'stack.yaml'),'--color-config',str(root/'configs/blender/colors/aim'/f"{opts['scheme']}.yaml"),'--output',str(output/'scene.blend'),'--z-scale',str(opts['z_scale']),'--source-max-polygon-vertices',str(opts['max_vertices']),'--presentation-metal-z-bevel-width-um',str(effective_bevel_width),'--presentation-metal-bevel-segments','8','--cladding-mode',opts['cladding'],'--cladding-boolean-solver','manifold',*sidecars,*([] if opts['merge_layers'] else ['--no-merge-layers'])])
    commands.append([config['blender'],'--background',str(output/'scene.blend'),'--python-exit-code','1','--python',str(root/'scripts/aim_prepare_render_blend.py'),'--','--preset',str(directory/'preset.yaml'),'--run','web_view','--output',str(output/'ready.blend')])
    return commands


def preview_mesh(visual, stack, opts, root=ROOT):
    import gdstk
    from shapely import make_valid, constrained_delaunay_triangles, union_all
    from shapely.geometry import Polygon
    from shapely.geometry.polygon import orient
    library=gdstk.read_gds(str(visual),unit=1e-6)
    # KLayout writes a zero-area $$$CONTEXT_INFO$$$ metadata cell beside the design.
    cells=[c for c in library.top_level() if c.bounding_box() is not None and c.bounding_box()[1][0]>c.bounding_box()[0][0] and c.bounding_box()[1][1]>c.bounding_box()[0][1]]
    if len(cells)!=1:raise ValueError('Select a GDS with exactly one top-level cell')
    layers=yaml.safe_load(Path(stack).read_text())
    palettes={name:yaml.safe_load((root/'configs/blender/colors/aim'/f'{name}.yaml').read_text())['layers'] for name in ['realistic','fancy','marketing']}
    bounds=[[math.inf]*3,[-math.inf]*3];meshes=[];triangles=0
    def polygons(geom):
        if geom.geom_type=='Polygon':yield geom
        elif hasattr(geom,'geoms'):
            for g in geom.geoms:yield from polygons(g)
    for name,spec in layers.items():
        if 'CUTTER' in name or 'CONFLICT' in name: continue
        raw=cells[0].get_polygons(layer=spec['index'],datatype=spec['type'])
        if not raw:continue
        # Union fractured contours before triangulating so holes survive and internal
        # fracture walls do not appear. Only preview contours are simplified.
        geom=union_all([make_valid(Polygon(poly.points)) for poly in raw])
        if opts['preview_tolerance']:geom=geom.simplify(opts['preview_tolerance'],preserve_topology=True)
        z0=spec['z']*opts['z_scale'];z1=(spec['z']+spec['height'])*opts['z_scale']
        vertices=[]
        def tri(a,b,c):
            nonlocal triangles
            triangles+=1
            if triangles>opts['preview_limit']:
                raise ValueError(f"Preview exceeds the {opts['preview_limit']:,}-triangle budget while adding {name}. Increase Preview triangle budget in Performance, increase preview simplification, or use a smaller GDS crop. No geometry was silently dropped.")
            for point in (a,b,c):
                vertices.extend(point)
                for i in range(3):bounds[0][i]=min(bounds[0][i],point[i]);bounds[1][i]=max(bounds[1][i],point[i])
        for poly in polygons(geom):
            poly=orient(poly,sign=1)
            for face in constrained_delaunay_triangles(poly).geoms:
                pts=list(orient(face,sign=1).exterior.coords)[:3]
                tri(*[(x,y,z1) for x,y in pts]);tri(*[(x,y,z0) for x,y in reversed(pts)])
            for ring in [poly.exterior,*poly.interiors]:
                points=list(ring.coords)
                for (x,y),(u,v) in zip(points,points[1:]):
                    tri((x,y,z0),(u,v,z0),(u,v,z1));tri((x,y,z0),(u,v,z1),(x,y,z1))
        colors={s:pal.get(name,{}).get('Base Color',[0.5,0.6,0.7,1]) for s,pal in palettes.items()}
        meshes.append(dict(name=name,positions=vertices,colors=colors))
    if not meshes:raise ValueError('No render geometry found in GDS')
    return dict(meshes=meshes,bounds=bounds,triangles=triangles,units='µm',notes='Simplified layer extrusion; optical materials, cap bevels and 3D cladding cutter booleans are rendered only in Blender. Depth-of-field blur preview: TODO.')
