"""Optional cladding thin-film reflection, applied after preset camera/visibility.

A presentation approximation: neutral transmission and real opening-wall paths
are retained. The camera-adaptive thickness pattern is illustrative, not measured data.
"""
from __future__ import annotations
import math
from aim_cladding_pattern import OWNER, ACCEPTED_PATTERN, CladdingNotInView, adapt_pattern, pattern

DEFAULT_STRENGTH = 0.85
SUN_BASELINE = OWNER + '_sun_baseline'
SHEEN_BASELINE = OWNER + '_sheen_baseline'


def normalize_appearance(value=None):
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - {'cladding_iridescence'}:
        raise ValueError('appearance only supports cladding_iridescence')
    config = value.get('cladding_iridescence', {})
    if not isinstance(config, dict) or set(config) - {'enabled', 'strength'}:
        raise ValueError('cladding_iridescence requires enabled and/or strength')
    enabled = config.get('enabled', False)
    strength = config.get('strength', DEFAULT_STRENGTH)
    if not isinstance(enabled, bool):
        raise ValueError('cladding_iridescence.enabled must be boolean')
    if isinstance(strength, bool) or not isinstance(strength, (int, float)) or not math.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError('cladding_iridescence.strength must be finite in [0, 1]')
    return {'cladding_iridescence': {'enabled': enabled, 'strength': float(strength)}}


def reset_iridescence(scene):
    """Restore owned Sun/sheen changes and remove only effect-owned resources."""
    import bpy
    materials = {slot.material for obj in scene.objects if obj.type == 'MESH'
                 for slot in obj.material_slots if slot.material}
    for material in materials:
        if not material.use_nodes:
            continue
        tree = material.node_tree
        if SHEEN_BASELINE in material:
            sheen = tree.nodes.get('AIM_Glass_Camera-only sheen')
            if sheen:
                sheen.inputs[1].default_value = material[SHEEN_BASELINE]
            del material[SHEEN_BASELINE]
        owned = [n for n in tree.nodes if n.get(OWNER)]
        if not owned:
            continue
        shell = tree.nodes.get('AIM_Glass_Refractive opening walls')
        cap = tree.nodes.get('AIM_Glass_Glass cap')
        if shell and cap:
            tree.links.new(cap.outputs[0], shell.inputs[1])
        for node in owned:
            tree.nodes.remove(node)
    for obj in list(scene.objects):
        if obj.type == 'LIGHT' and SUN_BASELINE in obj.data:
            obj.data.energy = obj.data[SUN_BASELINE]
            del obj.data[SUN_BASELINE]
        if obj.type == 'LIGHT' and obj.get(OWNER):
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if data.users == 0:
                bpy.data.lights.remove(data)
    for collection in list(bpy.data.collections):
        if collection.get(OWNER) and collection.name in scene.collection.children:
            bpy.data.collections.remove(collection)
    if OWNER in scene:
        del scene[OWNER]


def film_cap(material, thickness, film_ior, strength, thickness_span=0):
    """Blend a reflection-only dielectric film into the existing clear-cap model.

    The black diffuse lobe absorbs its share and adds no reflected diffuse color.
    Neutral cap transmission, white studio sheen, and the real opening walls stay
    on their existing paths. Transmission is deliberately not recomputed from the
    film reflectance: this is a controlled presentation approximation, not a full
    energy-conserving optical multilayer simulation.
    """
    t = material.node_tree
    n = t.nodes
    def new(kind):
        node = n.new(kind)
        node[OWNER] = True
        return node

    cap = n['AIM_Glass_Glass cap']
    shell = n['AIM_Glass_Refractive opening walls']
    fresnel = n['AIM_Glass_Glass Fresnel']
    transparent = n['AIM_Glass_Clear transmission']
    studio = n['AIM_Glass_Neutral studio reflection']
    wall = n['Principled BSDF']
    film = new('ShaderNodeBsdfPrincipled')
    film.name = 'AIM_Iridescence_ThinFilmReflection'
    film.inputs['Base Color'].default_value = (0, 0, 0, 1)
    film.inputs['Metallic'].default_value = 0
    film.inputs['IOR'].default_value = wall.inputs['IOR'].default_value
    film.inputs['Roughness'].default_value = wall.inputs['Roughness'].default_value
    film.inputs['Transmission Weight'].default_value = 0
    film.inputs['Specular IOR Level'].default_value = 0.5
    film.inputs['Thin Film Thickness'].default_value = thickness
    film.inputs['Thin Film IOR'].default_value = film_ior
    if thickness > 0 and thickness_span > 0:
        # A labeled smooth thickness-variation hypothesis, not measured chip data.
        # Generated coordinates use the same normalized rule for every layout.
        coordinates = new('ShaderNodeTexCoord')
        separate = new('ShaderNodeSeparateXYZ')
        scale = new('ShaderNodeMath')
        scale.operation = 'MULTIPLY_ADD'
        scale.inputs[1].default_value = thickness_span
        scale.inputs[2].default_value = thickness - thickness_span / 2
        t.links.new(coordinates.outputs['Generated'], separate.inputs[0])
        t.links.new(separate.outputs['X'], scale.inputs[0])
        t.links.new(scale.outputs[0], film.inputs['Thin Film Thickness'])
    t.links.new(n['AIM_Glass_Geometry'].outputs['True Normal'], film.inputs['Normal'])
    clear = new('ShaderNodeMixShader')
    clear.name = 'AIM_Iridescence_UnchangedClearAndStudio'
    t.links.new(fresnel.outputs[0], clear.inputs[0])
    t.links.new(transparent.outputs[0], clear.inputs[1])
    t.links.new(studio.outputs[0], clear.inputs[2])
    coated = new('ShaderNodeAddShader')
    coated.name = 'AIM_Iridescence_CoatedCap'
    t.links.new(clear.outputs[0], coated.inputs[0])
    t.links.new(film.outputs[0], coated.inputs[1])
    mix = new('ShaderNodeMixShader')
    mix.name = 'AIM_Iridescence_ReflectionStrength'
    mix.inputs[0].default_value = strength
    t.links.new(cap.outputs[0], mix.inputs[1])
    t.links.new(coated.outputs[0], mix.inputs[2])
    t.links.new(mix.outputs[0], shell.inputs[1])
    assert shell.inputs[2].links[0].from_node == wall
    assert wall.inputs['Thin Film Thickness'].default_value == 0
    assert tuple(transparent.inputs['Color'].default_value) == (1, 1, 1, 1)
    return {'film_ior': film_ior, 'substrate_ior': film.inputs['IOR'].default_value,
            'thickness_nm': thickness, 'thickness_span_nm': thickness_span, 'reflection_mix': strength,
            'neutral_transmission_preserved': True, 'wall_branch_preserved': True}

def softbox(scene, energy_scale, cladding=None):
    import bpy
    from mathutils import Vector
    camera = scene.camera
    cladding = cladding or bpy.data.objects['LCLADDING_RENDER']
    normal = (cladding.matrix_world.to_3x3().inverted().transposed() @ Vector((0, 0, 1))).normalized()
    view = (camera.matrix_world.to_quaternion() @ Vector((0, 0, 1))).normalized()
    corners = [Vector(v) for v in cladding.bound_box]
    top = max(v.z for v in corners)
    point = cladding.matrix_world @ Vector((0, 0, top))
    denominator = view.dot(normal)
    distance = (camera.location - point).dot(normal) / denominator if abs(denominator) > 1e-4 else -1
    if distance > 0 and distance < 1e7:
        target = camera.location - view * distance
    else:
        target = cladding.matrix_world @ (sum(corners, Vector()) / 8)
    distance_to_camera = (camera.location - target).length
    if camera.data.type == 'ORTHO':
        frame = camera.data.view_frame(scene=scene)
        span = max(v.x for v in frame) - min(v.x for v in frame)
    else:
        span = 2 * max(distance_to_camera, 1e-3) * math.tan(camera.data.angle_x / 2)
    distance = span * 2
    light_direction = 2 * normal.dot(view) * normal - view
    data = bpy.data.lights.new('AIM_Iridescence_WhiteSoftbox', 'AREA')
    data.shape = 'DISK'
    data.size = span * 2
    data.color = (1, 1, 1)
    data.energy = energy_scale * distance ** 2
    obj = bpy.data.objects.new(data.name, data)
    obj[OWNER] = True
    scene.collection.objects.link(obj)
    obj.location = target + light_direction * distance
    obj.rotation_euler = (target - obj.location).to_track_quat('-Z', 'Y').to_euler()
    return {'target': list(target), 'location': list(obj.location), 'size': data.size,
            'energy': data.energy, 'energy_scale': energy_scale}


def apply_iridescence(scene, appearance):
    import bpy
    import json
    config = normalize_appearance(appearance)['cladding_iridescence']
    reset_iridescence(scene)
    if not config['enabled'] or config['strength'] == 0:
        return {'enabled': False}
    objects = sorted((obj for obj in scene.objects if obj.type == 'MESH' and not obj.hide_render
                      and any(slot.material and slot.material.name.split('.')[0] == 'Mat_CLADDING_RENDER'
                              for slot in obj.material_slots)), key=lambda obj: obj.name)
    if not objects:
        return {'enabled': False, 'reason': 'No visible cladding'}
    if scene.camera is None:
        raise ValueError('Iridescence requires the preset camera')
    bpy.context.view_layer.update()
    # Validate and fit before changing any material or illumination.
    fits = {}
    for obj in objects:
        try:
            fitted = adapt_pattern(scene, obj, ACCEPTED_PATTERN)
        except CladdingNotInView:
            continue
        for slot in obj.material_slots:
            material = slot.material
            if material and material.name.split('.')[0] == 'Mat_CLADDING_RENDER':
                fits.setdefault(material, (obj, fitted))
    if not fits:
        return {'enabled': False, 'reason': 'Cladding outside camera frame'}
    required = ('AIM_Glass_Glass cap', 'AIM_Glass_Refractive opening walls',
                'AIM_Glass_Glass Fresnel', 'AIM_Glass_Clear transmission',
                'AIM_Glass_Neutral studio reflection', 'AIM_Glass_Geometry',
                'AIM_Glass_Camera-only sheen', 'Principled BSDF')
    for material in fits:
        if not material.use_nodes or any(name not in material.node_tree.nodes for name in required):
            raise ValueError('Iridescence requires current presentation glass; rebuild this scene.')
        if 'Thin Film Thickness' not in material.node_tree.nodes['Principled BSDF'].inputs:
            raise ValueError('This Blender version does not support Principled thin film.')
    result = {'enabled': True, 'strength': config['strength'], 'patterns': {}, 'sun': {}}
    try:
        for material, (obj, fitted) in fits.items():
            sheen = material.node_tree.nodes['AIM_Glass_Camera-only sheen'].inputs[1]
            material[SHEEN_BASELINE] = sheen.default_value
            sheen.default_value *= .25
            film_cap(material, 400, 2.0, config['strength'])
            pattern(material, fitted)
            result['patterns'][obj.name] = fitted
        # Preserve each source Sun energy so disable/save/reload and repeated apply
        # restore exactly. The preset caller resets before applying a new Sun value.
        for light in scene.objects:
            if light.type == 'LIGHT' and light.data.type == 'SUN':
                if SUN_BASELINE not in light.data:
                    light.data[SUN_BASELINE] = light.data.energy
                    light.data.energy *= .9
                result['sun'][light.name] = light.data.energy
        result['white_softbox'] = softbox(scene, .4, next(iter(fits.values()))[0])
        receivers = bpy.data.collections.new('AIM_Iridescence_Receivers')
        receivers[OWNER] = True
        scene.collection.children.link(receivers)
        for obj in objects:
            receivers.objects.link(obj)
        light = next(obj for obj in scene.objects if obj.type == 'LIGHT' and obj.get(OWNER))
        light.light_linking.receiver_collection = receivers
        scene[OWNER] = json.dumps(result, sort_keys=True)
    except Exception:
        reset_iridescence(scene)
        raise
    return result
