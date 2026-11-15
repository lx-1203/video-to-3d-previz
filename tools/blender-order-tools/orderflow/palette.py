"""Explicit actor-slot palettes. Importing this module does not require Blender."""
import colorsys
import copy
import json
import math
from pathlib import Path

# Gray/black/white have no meaningful hue. Use one stable, visibly colored
# orange fallback in display sRGB, rather than amplifying tiny RGB noise.
LOW_SATURATION = 0.05
NEUTRAL_MARKER_SRGB = (1.0, 0.45, 0.08)


def _rgba(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4 or any(isinstance(c, bool) or not isinstance(c, (int, float)) or not math.isfinite(c) or not 0 <= c <= 1 for c in value):
        raise ValueError('Colors must have four finite RGBA values between 0 and 1')
    return list(value)


def _linear_to_srgb(channel):
    return 12.92*channel if channel <= 0.0031308 else 1.055*channel**(1/2.4)-0.055


def _srgb_to_linear(channel):
    return channel/12.92 if channel <= 0.04045 else ((channel+0.055)/1.055)**2.4


def complement(body):
    """Linear RGBA in/out; complementary hue is computed in display sRGB.

    Low display saturation (<5%) uses NEUTRAL_MARKER_SRGB. This is a stable
    colored fallback, not a claim about rendered contrast or accessibility.
    """
    r, g, b = [_linear_to_srgb(channel) for channel in _rgba(body)[:3]]
    hue, saturation, value = colorsys.rgb_to_hsv(r, g, b)
    display = NEUTRAL_MARKER_SRGB if saturation < LOW_SATURATION else colorsys.hsv_to_rgb((hue+0.5)%1, max(saturation,0.7), max(value,0.9))
    return [*[max(0.0,min(1.0,_srgb_to_linear(channel))) for channel in display], 1.0]


def validate(document):
    if not isinstance(document, dict) or type(document.get('schema_version')) is not int or document['schema_version'] != 1:
        raise ValueError('Palette schema_version must be 1')
    actors = document.get('actors')
    if not isinstance(actors, dict) or not actors:
        raise ValueError('Palette actors must be a nonempty object')
    result = copy.deepcopy(document)
    names = set()
    for actor_id, actor in result['actors'].items():
        if not isinstance(actor_id, str) or not actor_id.strip() or not isinstance(actor, dict):
            raise ValueError('Each actor needs a stable nonempty actor_id and mapping')
        actor['body_color'] = _rgba(actor.get('body_color'))
        if actor.get('marker_color') != 'complement':
            actor['marker_color'] = _rgba(actor.get('marker_color'))
        objects = actor.get('objects')
        if not isinstance(objects, list) or not objects:
            raise ValueError(f'{actor_id}: objects must be a nonempty list')
        for item in objects:
            if not isinstance(item, dict) or not isinstance(item.get('name'), str) or not item['name'].strip():
                raise ValueError(f'{actor_id}: each object needs an explicit name')
            if item['name'] in names:
                raise ValueError(f'Duplicate object mapping: {item["name"]}')
            names.add(item['name'])
            used = set()
            for key in ('body_slots', 'marker_slots'):
                slots = item.get(key)
                if not isinstance(slots, list) or any(type(i) is not int or i < 0 for i in slots):
                    raise ValueError(f'{item["name"]}: {key} must contain nonnegative integer indices')
                if any(i in used for i in slots) or len(set(slots)) != len(slots):
                    raise ValueError(f'{item["name"]}: overlapping or duplicate slots')
                used.update(slots)
            if not used:
                raise ValueError(f'{item["name"]}: no slots selected')
    return result


def _base_sockets(material):
    if not material.use_nodes:
        return []
    sockets = [node.inputs['Base Color'] for node in material.node_tree.nodes if node.type == 'BSDF_PRINCIPLED']
    outputs = [node for node in material.node_tree.nodes if node.type == 'OUTPUT_MATERIAL' and node.is_active_output]
    if not outputs or any(len(node.inputs['Surface'].links) != 1 or node.inputs['Surface'].links[0].from_node.type != 'BSDF_PRINCIPLED' for node in outputs):
        raise ValueError(f'{material.name}: unsupported surface graph; expected a direct Principled shader')
    if not sockets or any(socket.is_linked for socket in sockets):
        raise ValueError(f'{material.name}: unsupported linked/textured Base Color or no Principled shader')
    return sockets


def _curves(action):
    if hasattr(action, 'fcurves'):
        yield action.fcurves
    else:
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    yield bag.fcurves


def _fix_animation(block, paths):
    animation = block.animation_data
    if animation is None:
        return
    # The new material must not edit an action shared with the source material.
    if animation.action:
        animation.action = animation.action.copy()
        for curves in _curves(animation.action):
            for curve in list(curves):
                if curve.data_path in paths:
                    curves.remove(curve)
    for track in animation.nla_tracks:
        for strip in track.strips:
            if strip.action:
                strip.action = strip.action.copy()
                for curves in _curves(strip.action):
                    for curve in list(curves):
                        if curve.data_path in paths:
                            curves.remove(curve)
    for driver in list(animation.drivers):
        if driver.data_path in paths:
            animation.drivers.remove(driver)


def _set_material(material, color):
    _fix_animation(material, {'diffuse_color'})
    sockets = _base_sockets(material)
    if sockets:
        _fix_animation(material.node_tree, {socket.path_from_id('default_value') for socket in sockets})
    material.diffuse_color = color
    for socket in sockets:
        socket.default_value = color


def apply(context, path):
    import bpy
    document = validate(json.loads(Path(path).read_text(encoding='utf-8')))
    plan = []
    for actor_id, actor in document['actors'].items():
        body = actor['body_color']
        marker = complement(body) if actor['marker_color'] == 'complement' else actor['marker_color']
        for item in actor['objects']:
            obj = context.scene.objects.get(item['name'])
            if obj is None or obj.type != 'MESH' or obj.library:
                raise ValueError(f'{item["name"]}: expected a local mesh in the current scene')
            slots = {slot: color for key, color in (('body_slots', body), ('marker_slots', marker)) for slot in item[key]}
            for slot in slots:
                if slot >= len(obj.material_slots) or obj.material_slots[slot].material is None:
                    raise ValueError(f'{obj.name}: missing material at slot {slot}')
                _base_sockets(obj.material_slots[slot].material)
            plan.append((actor_id, obj, slots))
    staged, meshes, materials, cache = [], [], [], {}
    originals = []
    try:
        # Stage every copy and color before changing any scene object.
        for actor_id, obj, slots in plan:
            mesh = obj.data.copy()
            meshes.append(mesh)
            replacements = {}
            for slot, color in slots.items():
                source = obj.material_slots[slot].material
                key = (actor_id, source.as_pointer(), tuple(color))
                if key not in cache:
                    material = source.copy()
                    materials.append(material)
                    material.name = f'{actor_id}_{source.name}'
                    _set_material(material, color)
                    cache[key] = material
                replacements[slot] = cache[key]
            staged.append((actor_id, obj, mesh, replacements))
        for actor_id, obj, mesh, replacements in staged:
            originals.append((obj, obj.data, {slot: obj.material_slots[slot].material for slot in replacements}, obj.get('orderflow_actor_id')))
            obj.data = mesh
            for slot, material in replacements.items():
                obj.material_slots[slot].material = material
            obj['orderflow_actor_id'] = actor_id
    except Exception:
        for obj, mesh, slots, actor_id in reversed(originals):
            obj.data = mesh
            for slot, material in slots.items():
                obj.material_slots[slot].material = material
            if actor_id is None:
                if 'orderflow_actor_id' in obj: del obj['orderflow_actor_id']
            else:
                obj['orderflow_actor_id'] = actor_id
        for mesh in meshes:
            if mesh.users == 0: bpy.data.meshes.remove(mesh)
        for material in materials:
            if material.users == 0: bpy.data.materials.remove(material)
        raise
    return {'actors': len(document['actors']), 'objects': len(plan), 'materials': len(materials)}
