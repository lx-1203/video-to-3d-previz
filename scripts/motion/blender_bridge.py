"""Deterministic BVH -> original client FK controls, with save/reopen verification.

Run inside Blender with --disable-autoexec --python-exit-code 1.
No embedded source text or user-provided Python is executed.
The parser/time helpers intentionally use only the Python standard library.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys
import traceback


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parse_bvh(text):
    tokens = re.findall(r'[{}]|[^\s{}]+', text)
    cursor = 0
    joints = []
    channel_count = 0

    def take(expected=None):
        nonlocal cursor
        if cursor >= len(tokens):
            raise ValueError('Truncated BVH')
        token = tokens[cursor]
        cursor += 1
        if expected is not None and token != expected:
            raise ValueError('Expected %r, got %r' % (expected, token))
        return token

    def number():
        value = float(take())
        if not math.isfinite(value):
            raise ValueError('Non-finite BVH number')
        return value

    def joint(parent=None, is_root=False):
        nonlocal channel_count
        take('ROOT' if is_root else 'JOINT')
        name = take()
        if any(j['name'] == name for j in joints):
            raise ValueError('Duplicate BVH joint: '+name)
        index = len(joints)
        item = {'name': name, 'parent': parent, 'children': [], 'end_sites': []}
        joints.append(item)
        take('{'); take('OFFSET')
        item['offset'] = [number() for _ in range(3)]
        take('CHANNELS')
        n = int(take())
        if n < 0 or n > 6:
            raise ValueError('Invalid BVH channel count')
        item['channels'] = [take() for _ in range(n)]
        if len(set(item['channels'])) != n or any(c not in {
                'Xposition','Yposition','Zposition','Xrotation','Yrotation','Zrotation'} for c in item['channels']):
            raise ValueError('Invalid BVH channels')
        item['channel_start'] = channel_count
        channel_count += n
        while cursor < len(tokens) and tokens[cursor] != '}':
            if tokens[cursor] == 'JOINT':
                child = joint(index)
                item['children'].append(child)
            elif tokens[cursor] == 'End':
                take('End'); take('Site'); take('{'); take('OFFSET')
                item['end_sites'].append([number() for _ in range(3)])
                take('}')
            else:
                raise ValueError('Unexpected BVH hierarchy token: '+tokens[cursor])
        take('}')
        return index

    take('HIERARCHY')
    joint(is_root=True)
    take('MOTION'); take('Frames:')
    count = int(take())
    take('Frame'); take('Time:')
    frame_time = number()
    if count < 1 or frame_time <= 0:
        raise ValueError('BVH needs frames and positive frame time')
    if len(tokens) - cursor != count * channel_count:
        raise ValueError('BVH frame/channel count does not match its motion values')
    frames = [[number() for _ in range(channel_count)] for _ in range(count)]
    return {'joints': joints, 'frames': frames, 'frame_time': frame_time,
            'channel_count': channel_count}


def blender_fps(fps):
    num, den = fps['num'], fps['den']
    if isinstance(num, bool) or isinstance(den, bool) or int(num) != num or int(den) != den or num <= 0 or den <= 0:
        raise ValueError('source_fps requires positive integer num/den')
    integer_fps = math.ceil(num/den)
    if not 1 <= integer_fps <= 32767:
        raise ValueError('Effective fps exceeds Blender integer fps range')
    return integer_fps,integer_fps*den/num


def sample_times(frame_count, frame_time, fps, start, duration):
    blender_fps(fps)
    num,den = fps['num'],fps['den']
    start, duration = float(start), float(duration)
    if not all(math.isfinite(v) for v in (start, duration)) or start < 0 or duration <= 0:
        raise ValueError('Invalid start_seconds/duration')
    count = max(1, math.ceil(duration * num / den - 1e-9))
    times = [start + i * den / num for i in range(count)]
    # A BVH sample is valid until the next source sample. Hold its last pose
    # for the remaining fraction of that interval, without extending clip time.
    if start + duration > frame_count * frame_time + 1e-6 or times[-1] >= frame_count * frame_time + 1e-6:
        raise ValueError('Requested time span exceeds BVH sampled coverage')
    return times


def resolve_joint(names, aliases):
    for alias in aliases:
        exact = [n for n in names if n == alias]
        if exact:
            return exact[0]
    for alias in aliases:
        found = [n for n in names if n.casefold() == alias.casefold()]
        if len(found) > 1:
            raise ValueError('Ambiguous BVH joint alias: '+alias)
        if found:
            return found[0]
    return None


def validate_mapping_unique(active):
    for key in ('source','target'):
        values = [row[key] for row in active]
        if len(set(values)) != len(values):
            raise ValueError('Duplicate '+key+' bone mapping; explicit profile correction required')


def local_translation(joint, values, unit_scale, convention):
    if convention not in ('offset_plus_position','absolute_parent_local_blender_bvh'):
        raise ValueError('Unknown BVH position channel convention')
    result = [float(v) for v in joint['offset']]
    for channel, value in zip(joint['channels'],values[joint['channel_start']:]):
        if channel.endswith('position'):
            axis = 'XYZ'.index(channel[0])
            result[axis] = value if convention == 'absolute_parent_local_blender_bvh' else result[axis]+value
    return [v*unit_scale for v in result]


_AUTO_METADATA = object()


def read_motion_metadata(motion_path, registration=_AUTO_METADATA):
    """Return one verified byte snapshot; explicit None forbids a new sidecar."""
    sidecar = Path(motion_path).with_suffix(Path(motion_path).suffix+'.json').resolve()
    if registration is None:
        if sidecar.exists():
            raise ValueError('Unregistered BVH sidecar exists despite motion_metadata=None')
        return None
    if registration is _AUTO_METADATA:
        return sidecar.read_bytes() if sidecar.exists() else None
    if not isinstance(registration,dict) or not all(key in registration for key in ('path','sha256','bytes')):
        raise ValueError('motion_metadata requires path, sha256 and bytes')
    if Path(registration['path']).resolve() != sidecar:
        raise ValueError('Registered motion_metadata path must be motion.bvh.json')
    if isinstance(registration['bytes'],bool) or not isinstance(registration['bytes'],int) or registration['bytes'] < 0:
        raise ValueError('Registered motion_metadata bytes must be a nonnegative integer')
    try:
        payload = sidecar.read_bytes()
    except OSError as exc:
        raise ValueError('Registered motion_metadata is missing or unreadable') from exc
    if len(payload) != registration['bytes'] or hashlib.sha256(payload).hexdigest() != registration['sha256']:
        raise ValueError('Registered motion_metadata size or SHA-256 mismatch')
    return payload


def motion_convention(motion_path, profile_source, registration=_AUTO_METADATA):
    path = Path(motion_path)
    result = {key:profile_source[key] for key in ('unit_scale_m','axis_up','axis_forward','position_channel_convention') if key in profile_source}
    result.setdefault('position_channel_convention','offset_plus_position')
    sidecar = path.with_suffix(path.suffix+'.json')
    result['sidecar'] = None
    payload = read_motion_metadata(path,registration)
    if payload is not None:
        metadata = json.loads(payload.decode('utf-8-sig'))
        if metadata.get('format') != 'motion_workbench_soma_bvh_v1':
            raise ValueError('Unsupported BVH sidecar format')
        if metadata.get('bvh_sha256') != sha256(path):
            raise ValueError('BVH sidecar SHA-256 does not match motion file')
        if metadata.get('position_channel_convention') != 'absolute_parent_local_blender_bvh' or metadata.get('handedness') != 'right':
            raise ValueError('Unsupported SOMA BVH position/handedness convention')
        rest_rotations = metadata.get('source_rest_world_rotations')
        if not isinstance(rest_rotations,dict) or not rest_rotations:
            raise ValueError('SOMA sidecar requires source_rest_world_rotations')
        for name,matrix in rest_rotations.items():
            if not isinstance(matrix,list) or len(matrix) != 3 or any(not isinstance(row,list) or len(row) != 3 for row in matrix):
                raise ValueError('Invalid source rest rotation shape: '+name)
            if not all(math.isfinite(float(value)) for row in matrix for value in row):
                raise ValueError('Non-finite source rest rotation: '+name)
            if max(abs(sum(matrix[k][i]*matrix[k][j] for k in range(3))-(1 if i==j else 0)) for i in range(3) for j in range(3)) > 1e-3:
                raise ValueError('Non-rigid source rest rotation: '+name)
            determinant = sum(matrix[0][i] * (matrix[1][(i+1)%3]*matrix[2][(i+2)%3]
                              - matrix[1][(i+2)%3]*matrix[2][(i+1)%3]) for i in range(3))
            if abs(determinant-1) > 1e-3:
                raise ValueError('Source rest rotation must be right-handed: '+name)
        units = {'centimeters':0.01,'meters':1.0,'millimeters':0.001}
        if metadata.get('unit') not in units or not math.isclose(float(metadata['unit_scale_m']),units[metadata['unit']],abs_tol=1e-10):
            raise ValueError('BVH sidecar unit and unit_scale_m disagree')
        result.update(unit_scale_m=float(metadata['unit_scale_m']), axis_up=metadata['up_axis'],
            axis_forward=metadata['forward_axis'], position_channel_convention=metadata['position_channel_convention'],
            sidecar=str(sidecar.resolve()), sidecar_sha256=hashlib.sha256(payload).hexdigest(), bvh_sha256=metadata['bvh_sha256'],
            source_rest_world_rotations=rest_rotations)
    allowed = {'X','Y','Z','-X','-Y','-Z'}
    if result.get('axis_up') not in allowed or result.get('axis_forward') not in allowed or result['axis_up'][-1] == result['axis_forward'][-1]:
        raise ValueError('Invalid BVH up/forward axis convention')
    return result


def action_curves(action):
    if action is None:
        return []
    if hasattr(action, 'fcurves'):
        return list(action.fcurves)
    return [fc for layer in action.layers for strip in layer.strips
            for bag in getattr(strip, 'channelbags', []) for fc in bag.fcurves]


def signature(rig, mesh):
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    geometry = {
        'vertices': [list(v.co) for v in mesh.data.vertices],
        'polygons': [(list(p.vertices), p.material_index) for p in mesh.data.polygons],
        'weights': [[(mesh.vertex_groups[g.group].name, g.weight) for g in v.groups] for v in mesh.data.vertices],
        'parent': mesh.parent.name if mesh.parent else None,
        'modifiers': [(m.type, m.object.name if m.type == 'ARMATURE' and m.object else None) for m in mesh.modifiers],
    }
    bones = [(b.name, b.parent.name if b.parent else None, list(b.head_local), list(b.tail_local),
              [list(r) for r in b.matrix_local], b.use_deform) for b in rig.data.bones]
    drivers = []
    for fc in rig.animation_data.drivers:
        drivers.append((fc.data_path, fc.array_index, fc.driver.type, fc.driver.expression,
            [(v.name, v.type, [(t.id.name if t.id else None, t.data_path, t.bone_target) for t in v.targets]) for v in fc.driver.variables],
            [(m.type, list(m.coefficients) if m.type == 'GENERATOR' else None) for m in fc.modifiers]))
    return {'geometry': digest(geometry), 'rest_bones': digest(bones), 'drivers': digest(drivers)}


def transfer(request):
    import bpy
    from mathutils import Matrix, Vector
    from bpy_extras.io_utils import axis_conversion

    out = Path(request['output_dir']).resolve()
    out.mkdir(parents=True, exist_ok=True)
    for name in ('candidate.blend', 'report.json', 'verification.json', 'preview.png'):
        if (out/name).exists():
            raise ValueError('Refusing to overwrite existing output: '+str(out/name))
    template = Path(request['template']).resolve()
    motion_path = Path(request['motion']).resolve()
    if sha256(template) != request['template_sha256'] or sha256(motion_path) != request['motion_sha256']:
        raise ValueError('Template or motion SHA-256 mismatch')
    profile = json.loads(Path(request['profile']).read_text(encoding='utf-8-sig'))
    if profile.get('schema_version') != 1 or profile['source']['format'] != 'bvh':
        raise ValueError('Unsupported bridge profile')
    bvh = parse_bvh(motion_path.read_text(encoding='utf-8-sig'))
    convention = motion_convention(motion_path,profile['source'],request.get('motion_metadata',_AUTO_METADATA))
    times = sample_times(len(bvh['frames']), bvh['frame_time'], request['source_fps'],
                         request['start_seconds'], request['duration'])
    bpy.ops.wm.open_mainfile(filepath=str(template), load_ui=False, use_scripts=False)
    rig = bpy.data.objects.get(profile['rig_object'])
    mesh = bpy.data.objects.get(profile['mesh_object'])
    if not rig or rig.type != 'ARMATURE' or not mesh or mesh.type != 'MESH':
        raise ValueError('Profile rig/mesh not found')
    expected = profile['expected']
    counts = {'vertices': len(mesh.data.vertices), 'polygons': len(mesh.data.polygons),
              'bones': len(rig.data.bones), 'drivers': len(rig.animation_data.drivers)}
    if counts != expected:
        raise ValueError('Template counts disagree with profile: '+repr(counts))
    original = signature(rig, mesh)
    names = [j['name'] for j in bvh['joints']]
    by_name = {j['name']: j for j in bvh['joints']}
    active, skipped = [], []
    for row in profile['mapping']:
        if row['target'].startswith(('DEF-', 'ORG-', 'MCH-')):
            raise ValueError('Profile attempts to animate a generated/deformation bone')
        source = resolve_joint(names, row['source_aliases'])
        if source is None or row['target'] not in rig.pose.bones:
            if row.get('required'):
                raise ValueError('Required mapping missing: '+row['role'])
            skipped.append(row['role'])
        else:
            active.append(dict(row, source=source))
    validate_mapping_unique(active)
    source_root = resolve_joint(names, profile['source']['root_aliases'])
    if source_root != names[0]:
        raise ValueError('BVH root must match profile root aliases')
    unit = float(convention['unit_scale_m'])
    if not math.isfinite(unit) or unit <= 0:
        raise ValueError('Invalid BVH unit scale')
    A = axis_conversion(from_forward=convention['axis_forward'], from_up=convention['axis_up'],
        to_forward=profile['target']['forward'], to_up=profile['target']['up']).to_3x3()
    A_inv = A.inverted()
    def axis_vector(s):
        return Vector(((-1 if s.startswith('-') else 1) if 'XYZ'[i] == s[-1] else 0 for i in range(3)))
    source_forward = axis_vector(convention['axis_forward'])
    target_forward = axis_vector(profile['target']['forward'])

    def bone_axis(j):
        candidates = [by_name[names[i]]['offset'] for i in j['children']] + j['end_sites']
        for value in candidates:
            if Vector(value).length > 1e-8:
                return Vector(value)
        raise ValueError('BVH joint has no nonzero child/end-site axis: '+j['name'])

    def frame_y(direction, hint):
        y = direction.normalized()
        z = hint - y * hint.dot(y)
        if z.length < 1e-5:
            hint = Vector((1, 0, 0)) if abs(y.x) < 0.9 else Vector((0, 0, 1))
            z = hint - y * hint.dot(y)
        z.normalize()
        x = y.cross(z).normalized()
        return Matrix((x, y, x.cross(y))).transposed()

    role_map = {row['role']: row for row in active}
    ratios = []
    for chain in profile['scale_calibration']['chains']:
        if all(role in role_map for role in chain):
            target_length = sum((rig.matrix_world.to_3x3() @ (rig.data.bones[role_map[role]['target']].tail_local - rig.data.bones[role_map[role]['target']].head_local)).length for role in chain)
            source_length = sum(bone_axis(by_name[role_map[role]['source']]).length * unit for role in chain)
            ratios.append(target_length / source_length)
    if not ratios:
        raise ValueError('No complete calibration leg chain')
    root_scale = statistics.median(ratios)
    if not 0.05 <= root_scale <= 20:
        raise ValueError('Implausible root calibration scale; check BVH units/profile')

    rest_world = {row['target']: rig.matrix_world @ rig.data.bones[row['target']].matrix_local for row in active}
    for row in active:
        if convention['sidecar']:
            if row['source'] not in convention['source_rest_world_rotations']:
                raise ValueError('SOMA sidecar lacks mapped rest rotation: '+row['source'])
            source_bind = Matrix(convention['source_rest_world_rotations'][row['source']])
        else:
            source_bind = Matrix.Identity(3)
        row['source_bind_inverse'] = source_bind.inverted()
        target_rest = rest_world[row['target']].to_3x3().normalized()
        row['target_rest_rotation'] = target_rest
        if row.get('rotation_mode') == 'aim':
            # Neutral roll uses global front; physical Y still equals the
            # posed source rotation times its parent-local child offset.
            source_frame = frame_y(source_bind @ bone_axis(by_name[row['source']]), source_forward)
            target_direction = rig.matrix_world.to_3x3() @ (rig.data.bones[row['target']].tail_local - rig.data.bones[row['target']].head_local)
            target_frame = frame_y(target_direction, target_forward)
            row['source_rest_frame'] = source_frame
            row['target_roll'] = target_frame.inverted() @ target_rest
    def depth(pb):
        return len(pb.parent_recursive)
    active.sort(key=lambda row: depth(rig.pose.bones[row['target']]))
    rig.animation_data.action = None
    for track in rig.animation_data.nla_tracks:
        track.mute = True
    init_names = set(r['target'] for r in active) | {'root', 'torso', 'hips', 'chest', 'neck', 'head'}
    for name in init_names:
        if name in rig.pose.bones:
            rig.pose.bones[name].matrix_basis = Matrix.Identity(4)
    for name in profile['target']['fk_switch_controls']:
        rig.pose.bones[name]['IK_FK'] = profile['target']['fk_value']
        rig.pose.bones[name]['FK_limb_follow'] = 0.0
    for prop in ('head_follow', 'neck_follow'):
        if prop in rig.pose.bones['torso']:
            rig.pose.bones['torso'][prop] = 1.0
    rig.update_tag()
    bpy.context.view_layer.update()
    root_control = profile['target']['root_control']
    base_root = (rig.matrix_world @ rig.pose.bones[root_control].matrix).translation.copy()
    scene = bpy.context.scene
    scene.render.fps,scene.render.fps_base = blender_fps(request['source_fps'])
    scene.frame_start = int(request.get('frame_start', 1))
    scene.frame_end = scene.frame_start + len(times) - 1

    def local_sample(index):
        values = bvh['frames'][index]
        result = []
        for j in bvh['joints']:
            position = Vector(local_translation(j,values,unit,convention['position_channel_convention']))
            local = Matrix.Translation(position)
            for channel, value in zip(j['channels'], values[j['channel_start']:]):
                axis = channel[0]
                if channel.endswith('rotation'):
                    local = local @ Matrix.Rotation(math.radians(value), 4, axis)
            result.append(local)
        return result

    cache = {}
    def world_sample(seconds):
        position = seconds / bvh['frame_time']
        lo = min(int(math.floor(position)), len(bvh['frames'])-1)
        hi = min(lo+1, len(bvh['frames'])-1)
        alpha = max(0, min(1, position-lo))
        for index in {lo, hi}:
            if index not in cache:
                cache[index] = local_sample(index)
        worlds = []
        for i, j in enumerate(bvh['joints']):
            lhs, rhs = cache[lo][i], cache[hi][i]
            q = lhs.to_quaternion().slerp(rhs.to_quaternion(), alpha)
            local = q.to_matrix().to_4x4()
            local.translation = lhs.translation.lerp(rhs.translation, alpha)
            worlds.append(local if j['parent'] is None else worlds[j['parent']] @ local)
        return {j['name']: worlds[i] for i,j in enumerate(bvh['joints'])}

    first_source = world_sample(times[0])
    first_root = first_source[source_root].translation
    # Quaternion continuity is handled explicitly; Blender channels remain linear.
    previous_quaternions = {}
    location_max_error = 0.0
    rotation_max_error = 0.0
    for offset, seconds in enumerate(times):
        frame = scene.frame_start + offset
        scene.frame_set(frame)
        source_world = world_sample(seconds)
        for row in active:
            pb = rig.pose.bones[row['target']]
            source_rotation = source_world[row['source']].to_3x3().normalized()
            source_delta = source_rotation @ row['source_bind_inverse']
            if row.get('rotation_mode') == 'aim':
                wanted = A @ source_delta @ row['source_rest_frame'] @ row['target_roll']
            else:
                wanted = A @ source_delta @ A_inv @ row['target_rest_rotation']
            current = rig.evaluated_get(bpy.context.evaluated_depsgraph_get()).pose.bones[pb.name].matrix
            world = wanted.to_4x4()
            world.translation = (rig.matrix_world @ current).translation
            if pb.name == root_control:
                world.translation = base_root + (A @ (source_world[source_root].translation-first_root)) * root_scale
            pb.rotation_mode = 'QUATERNION'
            pb.matrix = rig.matrix_world.inverted() @ world
            bpy.context.view_layer.update()
            # Non-root locations are not animation channels: keep every joint's attachment fixed.
            if pb.name != root_control:
                pb.location = (0,0,0)
                location_max_error = max(location_max_error, pb.location.length)
            pb.scale = (1,1,1)
            q = pb.rotation_quaternion.copy()
            if pb.name in previous_quaternions and q.dot(previous_quaternions[pb.name]) < 0:
                q.negate(); pb.rotation_quaternion = q
            previous_quaternions[pb.name] = q.copy()
            pb.keyframe_insert('rotation_quaternion', frame=frame, group=pb.name)
            if pb.name == root_control:
                pb.keyframe_insert('location', frame=frame, group=pb.name)
            bpy.context.view_layer.update()
            evaluated_pb = rig.evaluated_get(bpy.context.evaluated_depsgraph_get()).pose.bones[pb.name]
            actual = (rig.matrix_world @ evaluated_pb.matrix).to_quaternion()
            error_angle = actual.rotation_difference(wanted.to_quaternion()).angle
            rotation_max_error = max(rotation_max_error,min(error_angle,abs(2*math.pi-error_angle)))
    for name in profile['target']['fk_switch_controls']:
        rig.pose.bones[name].keyframe_insert(data_path='["IK_FK"]', frame=scene.frame_start, group='FK mode')
    rig.animation_data.action.name = 'VideoMotion_BVH_FK_Candidate'
    for fc in action_curves(rig.animation_data.action):
        for key in fc.keyframe_points:
            key.interpolation = 'LINEAR'
    scene.frame_set(scene.frame_start)
    bpy.context.view_layer.update()

    # Add a preview camera only for a character-only template that has none.
    if scene.camera is None:
        coords = [mesh.matrix_world @ v.co for v in mesh.data.vertices]
        lower = Vector(tuple(min(v[i] for v in coords) for i in range(3)))
        upper = Vector(tuple(max(v[i] for v in coords) for i in range(3)))
        center = (lower + upper) * 0.5
        camera_data = bpy.data.cameras.new('MotionPreviewCamera')
        camera = bpy.data.objects.new('MotionPreviewCamera', camera_data)
        scene.collection.objects.link(camera)
        camera.location = center + Vector((3,-6,1.3))
        camera.rotation_euler = (center-camera.location).to_track_quat('-Z','Y').to_euler()
        camera_data.type = 'ORTHO'; camera_data.ortho_scale = max(3.6, (upper-lower).length*1.35)
        scene.camera = camera
        for name, offset, power in [('MotionPreviewKey',(2,-3,5),900), ('MotionPreviewFill',(-3,-2,2),500)]:
            data = bpy.data.lights.new(name, 'AREA'); data.energy = power; data.shape = 'DISK'; data.size = 5
            lamp = bpy.data.objects.new(name, data); scene.collection.objects.link(lamp)
            lamp.location = center + Vector(offset)
            lamp.rotation_euler = (center-lamp.location).to_track_quat('-Z','Y').to_euler()
    # Blender 5 renamed EEVEE NEXT back to BLENDER_EEVEE.
    scene.render.engine = 'BLENDER_EEVEE' if bpy.app.version >= (5,0,0) else 'BLENDER_EEVEE_NEXT'
    scene.render.resolution_x = 512; scene.render.resolution_y = 512
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    blend = out/'candidate.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(blend), check_existing=False)
    bpy.ops.wm.open_mainfile(filepath=str(blend), load_ui=False, use_scripts=False)
    rig = bpy.data.objects[profile['rig_object']]
    mesh = bpy.data.objects[profile['mesh_object']]
    scene = bpy.context.scene
    after = signature(rig, mesh)
    curves = action_curves(rig.animation_data.action)
    root_samples = []
    control_samples = []
    for frame in (scene.frame_start, scene.frame_end):
        scene.frame_set(frame); bpy.context.view_layer.update()
        evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
        root_samples.append(rig.matrix_world @ evaluated.pose.bones[root_control].matrix)
        control_samples.append({row['target']:rig.pose.bones[row['target']].rotation_quaternion.copy() for row in active})
    switches = {name: float(rig.pose.bones[name]['IK_FK']) for name in profile['target']['fk_switch_controls']}
    expected_curve_frames = {float(f) for f in range(scene.frame_start,scene.frame_end+1)}
    covered = all({float(k.co.x) for k in fc.keyframe_points} == expected_curve_frames
                  for fc in curves if fc.data_path.endswith(('rotation_quaternion', 'location')))
    bad_generated_keys = [fc.data_path for fc in curves if any('pose.bones["'+prefix in fc.data_path for prefix in ('DEF-','ORG-','MCH-'))]
    verification = {
        'geometry_unchanged': original['geometry'] == after['geometry'],
        'rest_bones_unchanged': original['rest_bones'] == after['rest_bones'],
        'drivers_unchanged': original['drivers'] == after['drivers'],
        'vertex_count': len(mesh.data.vertices), 'polygon_count': len(mesh.data.polygons),
        'bone_count': len(rig.data.bones), 'driver_count': len(rig.animation_data.drivers),
        'fk_switches': switches, 'fk_value': profile['target']['fk_value'],
        'mapped_controls': [{'source':row['source'],'target':row['target'],'role':row['role']} for row in active],
        'skipped_optional_roles': skipped, 'root_scale_calibrated_once': root_scale,
        'source_unit_scale_m': unit, 'bvh_frame_time': bvh['frame_time'],
        'motion_convention': convention,
        'source_first_foot_height_m': min((A @ first_source[role_map[role]['source']].translation).z for role in ('left_foot','right_foot') if role in role_map) if any(role in role_map for role in ('left_foot','right_foot')) else None,
        'tail_hold_frame_count': sum(t > (len(bvh['frames'])-1)*bvh['frame_time']+1e-8 for t in times),
        'tail_hold_seconds': max(0,times[-1]-(len(bvh['frames'])-1)*bvh['frame_time']),
        'tail_hold_source_frame_periods': max(0,times[-1]/bvh['frame_time']-(len(bvh['frames'])-1)),
        'source_interval_start_seconds': request.get('source_interval_start_seconds', 0),
        'frame_count': len(times), 'fps': scene.render.fps, 'fps_base': scene.render.fps_base,
        'frame_start': scene.frame_start, 'frame_end': scene.frame_end,
        'curves_cover_all_frames': covered, 'action_curve_count': len(curves),
        'generated_bones_keyed': bad_generated_keys,
        'root_displacement_m': (root_samples[1].translation-root_samples[0].translation).length,
        'root_rotation_change_radians': root_samples[0].to_quaternion().rotation_difference(root_samples[1].to_quaternion()).angle,
        'control_location_max_error': location_max_error,
        'control_rotation_change_radians': {name:control_samples[0][name].rotation_difference(control_samples[1][name]).angle for name in control_samples[0]},
        'max_control_rotation_error_radians': rotation_max_error,
        'template_sha256_after': sha256(template),
        'visual_review_passed': False, 'status': 'candidate_requires_visual_acceptance',
        'limitations': profile.get('limitations', []),
    }
    ok = (all(verification[k] for k in ('geometry_unchanged','rest_bones_unchanged','drivers_unchanged','curves_cover_all_frames'))
          and bool(curves) and not bad_generated_keys and all(v == profile['target']['fk_value'] for v in switches.values())
          and rotation_max_error < 1e-3
          and verification['template_sha256_after'] == request['template_sha256'])
    verification_path = out/'verification.json'
    verification_path.write_text(json.dumps(verification,ensure_ascii=False,indent=2),encoding='utf-8')
    if not ok:
        raise ValueError('Saved candidate failed technical verification; see verification.json')
    scene.frame_set(scene.frame_start)
    scene.render.filepath = str(out/'preview.png')
    bpy.ops.render.render(write_still=True)
    report = {'ok': True, 'blend': str(blend), 'preview': str(out/'preview.png'),
        'verification': str(verification_path), 'frame_start': scene.frame_start, 'frame_end': scene.frame_end,
        'source_fps': request['source_fps'], 'template_sha256': request['template_sha256'],
        'motion_sha256': request['motion_sha256'], 'visual_review_passed': False}
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('BLENDER_BRIDGE_REPORT='+json.dumps(report,ensure_ascii=False))
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True)
    argv = sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else sys.argv[1:]
    args = parser.parse_args(argv)
    request = json.loads(Path(args.request).read_text(encoding='utf-8-sig'))
    try:
        transfer(request)
    except Exception as exc:
        out = Path(request['output_dir']).resolve()
        out.mkdir(parents=True,exist_ok=True)
        failure = {'ok': False, 'error': str(exc), 'traceback': traceback.format_exc()}
        # Preserve any successful prior run rather than relabelling it.
        if not (out/'report.json').exists():
            (out/'report.json').write_text(json.dumps(failure,ensure_ascii=False,indent=2),encoding='utf-8')
        print('BLENDER_BRIDGE_ERROR='+str(exc))
        raise


if __name__ == '__main__':
    main()
