"""Camera-framed illustrative thin-film thickness patterns (no geometry changes)."""
import math

OWNER = 'aim_cladding_iridescence'
ACCEPTED_PATTERN = dict(center=400, span=500, angle=28, warp=25, optical_cycles=1.5)


class CladdingNotInView(ValueError):
    """The camera does not see a usable cladding top-plane extent."""


def visible_projection(scene, cladding, angle_degrees):
    """Clip the top-plane bounds to the camera frustum, in cladding coordinates.

    This is a visible plane envelope, not a mesh visibility/occlusion solver.
    Its finite clipped bounds also handle a camera near the plane horizon.
    """
    from mathutils import Vector
    camera = scene.camera
    if camera.data.type not in {'PERSP', 'ORTHO'}:
        raise ValueError('Adaptive bands require a perspective or orthographic camera')
    corners = [Vector(v) for v in cladding.bound_box]
    low = Vector(tuple(min(v[i] for v in corners) for i in range(3)))
    high = Vector(tuple(max(v[i] for v in corners) for i in range(3)))
    if high.x - low.x < 1e-8 or high.y - low.y < 1e-8:
        raise ValueError('Cladding bounds have no planar extent')
    polygon = [Vector((low.x,low.y,high.z)), Vector((high.x,low.y,high.z)),
               Vector((high.x,high.y,high.z)), Vector((low.x,high.y,high.z))]
    transform = camera.matrix_world.inverted() @ cladding.matrix_world
    frame = camera.data.view_frame(scene=scene)
    if camera.data.type == 'PERSP':
        center = sum(frame, Vector()) / len(frame)
        normals = [frame[i].cross(frame[(i + 1) % 4]).normalized() for i in range(4)]
        normals = [n if n.dot(center) >= 0 else -n for n in normals]
        planes = [(n, 0.) for n in normals]
    else:
        planes = [(Vector((1,0,0)), -min(v.x for v in frame)),
                  (Vector((-1,0,0)), max(v.x for v in frame)),
                  (Vector((0,1,0)), -min(v.y for v in frame)),
                  (Vector((0,-1,0)), max(v.y for v in frame))]
    planes += [(Vector((0,0,-1)), -camera.data.clip_start),
               (Vector((0,0,1)), camera.data.clip_end)]
    for normal, offset in planes:
        clipped = []
        if not polygon:
            break
        for a, b in zip(polygon, polygon[1:] + polygon[:1]):
            da = normal.dot(transform @ a) + offset
            db = normal.dot(transform @ b) + offset
            if da >= 0:
                clipped.append(a)
            if (da >= 0) != (db >= 0):
                clipped.append(a.lerp(b, da / (da - db)))
        polygon = clipped
    if len(polygon) < 3:
        raise CladdingNotInView('Cladding top plane does not intersect the camera frame')
    angle = math.radians(angle_degrees);c,s = math.cos(angle),math.sin(angle)
    def project(v):
        x = (v.x-low.x)/(high.x-low.x)-.5
        y = (v.y-low.y)/(high.y-low.y)-.5
        return (c*x+s*y)/(abs(c)+abs(s))
    values = [project(v) for v in polygon]
    low_p, high_p = min(values), max(values)
    if high_p-low_p < 1e-7:
        raise CladdingNotInView('Visible cladding projection is too small for adaptive bands')
    return {'projection_min':low_p, 'projection_max':high_p,
            'projection_span':high_p-low_p, 'projection_center':(low_p+high_p)/2,
            'visible_local_polygon':[list(v) for v in polygon],
            'camera_type':camera.data.type, 'lens_mm':camera.data.lens,
            'fit_scope':'camera-frustum intersection with cladding top-plane bounds'}


def adapt_pattern(scene, cladding, config):
    if not config.get('adaptive_sweeps') and not config.get('optical_cycles'):
        return config
    result = dict(config)
    bounds = visible_projection(scene, cladding, config['angle'])
    if config.get('optical_cycles'):
        from mathutils import Vector
        polygon = [Vector(v) for v in bounds['visible_local_polygon']]
        center = cladding.matrix_world @ (sum(polygon, Vector()) / len(polygon))
        normal = (cladding.matrix_world.to_3x3().inverted().transposed() @ Vector((0,0,1))).normalized()
        if scene.camera.data.type == 'ORTHO':
            view = scene.camera.matrix_world.to_quaternion() @ Vector((0,0,1))
        else:
            view = (scene.camera.matrix_world.translation-center).normalized()
        cosine = max(0., min(1., abs(normal.dot(view))))
        film_ior = 2.
        film_cosine = math.sqrt(1-(1-cosine*cosine)/(film_ior*film_ior))
        reference_nm = 550.
        thickness_period = reference_nm/(2*film_ior*film_cosine)
        visible_span_nm = config['optical_cycles']*thickness_period
        result['span'] = visible_span_nm/bounds['projection_span']
        result['phase_center'] = bounds['projection_center']
        result['adaptive'] = dict(bounds, reference_wavelength_nm=reference_nm,
            target_optical_cycles=config['optical_cycles'], incidence_cosine=cosine,
            film_ior=film_ior, thickness_period_nm=thickness_period,
            visible_thickness_span_nm=visible_span_nm,
            note='Linear optical phase at a representative angle; approximate visual color count, not a spectral calibration')
        return result
    # One sine period traverses the useful thickness/color range twice.
    # The visible_sweeps target counts these broad color runs, not RGB extrema.
    target_cycles = config['adaptive_sweeps'] / 2
    result['cycles'] = target_cycles / bounds['projection_span']
    result['phase_center'] = bounds['projection_center']
    result['adaptive'] = dict(bounds, target_sweeps=config['adaptive_sweeps'],
                              visible_thickness_cycles=target_cycles,
                              full_bounds_cycles=result['cycles'])
    return result


def pattern(material, config):
    tree = material.node_tree
    def node(kind, name):
        value = tree.nodes.new(kind)
        value.name = 'Pattern_' + name
        value[OWNER] = True
        return value
    def math_node(op, name, a=None, b=None):
        n = node('ShaderNodeMath', name);n.operation = op
        for index, value in enumerate((a, b)):
            if value is not None:
                if isinstance(value, (int, float)):
                    n.inputs[index].default_value = value
                else:
                    tree.links.new(value, n.inputs[index])
        return n.outputs[0]
    coords = node('ShaderNodeTexCoord', 'Coordinates')
    xy = node('ShaderNodeSeparateXYZ', 'XY')
    tree.links.new(coords.outputs['Generated'], xy.inputs[0])
    angle = math.radians(config['angle'])
    # Normalize the diagonal projection so the nominal range stays fixed.
    c, s = math.cos(angle), math.sin(angle)
    scale = config['span'] / (abs(c) + abs(s))
    x = math_node('SUBTRACT', 'Center X', xy.outputs['X'], .5)
    y = math_node('SUBTRACT', 'Center Y', xy.outputs['Y'], .5)
    x = math_node('MULTIPLY', 'X gradient', x, scale * c)
    y = math_node('MULTIPLY', 'Y gradient', y, scale * s)
    projection = math_node('ADD', 'Projection', x, y)
    if 'phase_center' in config:
        projection = math_node('SUBTRACT', 'Visible phase center', projection, config['phase_center'] * config['span'])
    if config.get('cycles'):
        phase = math_node('MULTIPLY', 'Ripple phase', projection, 2 * math.pi * config['cycles'] / config['span'])
        projection = math_node('MULTIPLY', 'Bounded smooth ripple', math_node('SINE', 'Ripple', phase), config['span'] / 2)
    thickness = math_node('ADD', 'Gradient', projection, config['center'])
    if config['warp']:
        noise = node('ShaderNodeTexNoise', 'Smooth thickness variation')
        noise.noise_dimensions = '3D'
        noise.inputs['Scale'].default_value = 3
        noise.inputs['Detail'].default_value = 1
        noise.inputs['Roughness'].default_value = .45
        tree.links.new(coords.outputs['Generated'], noise.inputs['Vector'])
        variation = math_node('MULTIPLY', 'Warp nm', math_node('SUBTRACT', 'Zero mean noise', noise.outputs['Fac'], .5), config['warp'] * 2)
        thickness = math_node('ADD', 'Nonuniform thickness', thickness, variation)
    thickness = math_node('MAXIMUM', 'Positive thickness', thickness, 1)
    if 'AIM_Iridescence_ThinFilmReflection' in tree.nodes:
        tree.links.new(thickness, tree.nodes['AIM_Iridescence_ThinFilmReflection'].inputs['Thin Film Thickness'])
    return thickness, dict(cycles=config.get('cycles',0),angle_degrees=config['angle'],center_nm=config['center'],span_nm=config['span'],warp_amplitude_nm=config['warp'],noise_scale=3,noise_detail=1)


