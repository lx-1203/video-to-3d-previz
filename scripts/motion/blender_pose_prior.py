"""Restricted real-component direction priors; run inside trusted Blender only.

Usage: blender --background --disable-autoexec source.blend --python
blender_pose_prior.py -- --candidates numeric77.json --profile private.json
--output NEW.blend --samples 620 699

Private profile requires source_sha256, target_template_sha256,
expected_blend_sha256, target_template_path (relative to profile or absolute),
integer frame_offset, and actors keyed by stable actor_id. Each actor requires
mesh, rig, chains. Each chain requires side_confirmation/pivot_calibration
{status: confirmed/calibrated, evidence: nonempty string or string list}, and
upper/lower objects with control, vertex_indices, proximal_cap_indices,
distal_cap_indices, source_start/source_end. Source pairs must be explicit
Arm->ForeArm and ForeArm->Hand on the same anatomical side. Hand means wrist,
not a target palm end. Cap index order/orientation is never inferred.

SVD registration and cap-based fixed-size application follow the provided
two-anchor implementation. Only source camera unit directions are transferred;
no world depth, source lengths, IK, or automatic side/pivot guessing is used.
Existing actions are copied, existing drivers are retained. Existing animation
between samples is not certified. Output is an INTERNAL candidate, never delivery.
The receipt records evaluated mesh checks at samples, not an independent reopen.
--samples is mandatory and lists Blender frame numbers, not source frames or
PTS. Every requested frame must have an existing candidate for every mapped
actor; unselected candidate rows are not applied.
"""

import argparse
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import sys


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('candidates', 'profile', 'output'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--samples', nargs='+', type=int, required=True,
                        help='Only these Blender frames; no interpolation or missing samples')
    return parser.parse_args(argv)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _proof(value, status):
    if not isinstance(value, dict) or value.get('status') != status:
        return False
    evidence = value.get('evidence')
    return _text(evidence) or (isinstance(evidence, list) and bool(evidence)
                               and all(_text(item) for item in evidence))


def validate_output(output, receipt, source):
    output, receipt, source = (Path(p).resolve() for p in (output, receipt, source))
    require(output.suffix.lower() == '.blend', 'output must be a NEW .blend')
    require(output != source and receipt != source, 'source cannot be overwritten')
    require(not output.exists() and not receipt.exists(), 'output/receipt already exists')
    require(output.parent.is_dir(), 'output parent directory must already exist')


def validate_contract(candidates, profile, samples=None):
    """Stdlib-only validation; returns explicit sample plan, never interpolation."""
    require(isinstance(candidates, dict) and isinstance(profile, dict), 'JSON objects required')
    for field in ('source_sha256', 'target_template_sha256', 'expected_blend_sha256'):
        require(isinstance(profile.get(field), str) and
                re.fullmatch('[0-9a-fA-F]{64}', profile[field]), 'invalid ' + field)
    require(isinstance(candidates.get('source_sha256'), str) and
            candidates['source_sha256'].lower() == profile['source_sha256'].lower(),
            'source_sha256 mismatch')
    require(_text(profile.get('target_template_path')), 'target_template_path required')
    require(type(profile.get('frame_offset')) is int, 'integer frame_offset required')
    if samples is not None:
        require(isinstance(samples, list) and bool(samples) and
                all(type(f) is int for f in samples) and len(set(samples)) == len(samples),
                'samples must be unique integer Blender frames')
    names = candidates.get('joint_names')
    require(isinstance(names, list) and len(names) == 77 and
            all(_text(n) for n in names) and len(set(names)) == 77, '77 unique joint names required')
    actors = profile.get('actors')
    require(isinstance(actors, dict) and bool(actors), 'actors mapping required')
    objects = set()
    for aid, actor in actors.items():
        require(_text(aid) and isinstance(actor, dict), 'invalid actor mapping')
        require(all(_text(actor.get(k)) for k in ('mesh', 'rig')), 'explicit mesh/rig required')
        require(actor['mesh'] not in objects and actor['rig'] not in objects and
                actor['mesh'] != actor['rig'], 'actor mesh/rig must be independent')
        objects.update((actor['mesh'], actor['rig']))
        chains = actor.get('chains')
        require(isinstance(chains, list) and bool(chains), 'explicit chains required')
        controls, vertices = set(), set()
        for chain in chains:
            require(isinstance(chain, dict), 'chain object required')
            for field, status in (('side_confirmation', 'confirmed'),
                                  ('pivot_calibration', 'calibrated')):
                require(_proof(chain.get(field), status), field + ' unproven')
            for part in ('upper', 'lower'):
                segment = chain.get(part)
                require(isinstance(segment, dict) and _text(segment.get('control')),
                        'upper/lower control required')
                require(segment['control'] not in controls, 'control reused')
                controls.add(segment['control'])
                for key in ('vertex_indices', 'proximal_cap_indices', 'distal_cap_indices'):
                    ids = segment.get(key)
                    require(isinstance(ids, list) and bool(ids) and
                            all(type(i) is int and i >= 0 for i in ids) and
                            len(set(ids)) == len(ids), 'invalid ' + key)
                ids = set(segment['vertex_indices'])
                proximal, distal = (set(segment[k]) for k in
                                    ('proximal_cap_indices', 'distal_cap_indices'))
                require(len(ids) >= 3 and proximal <= ids and distal <= ids and
                        not proximal & distal, 'caps must be disjoint component subsets')
                require(not ids & vertices, 'component vertices overlap')
                vertices.update(ids)
            upper, lower = chain['upper'], chain['lower']
            require(any((upper.get('source_start'), upper.get('source_end'),
                         lower.get('source_start'), lower.get('source_end')) ==
                        (side + 'Arm', side + 'ForeArm', side + 'ForeArm', side + 'Hand')
                        for side in ('Left', 'Right')), 'explicit same-side formal arm joints required')
            require(all(segment[key] in names for segment in (upper, lower)
                        for key in ('source_start', 'source_end')), 'source joint absent')
    rows = candidates.get('rows')
    require(isinstance(rows, list) and bool(rows), 'candidate rows required')
    plan, seen, covered = [], set(), set()
    for row in rows:
        require(isinstance(row, dict) and _text(row.get('actor_id')), 'row actor_id required')
        if row['actor_id'] not in actors:
            continue
        require(type(row.get('source_frame')) is int and row['source_frame'] >= 0
                and type(row.get('pts')) is int, 'integer source_frame and PTS required')
        frame = row['source_frame'] + profile['frame_offset']
        if samples is not None and frame not in samples:
            continue
        require(isinstance(row.get('time_base'), str), 'rational time_base required')
        try:
            valid_time_base = Fraction(row['time_base']) > 0
        except (ValueError, ZeroDivisionError):
            valid_time_base = False
        require(valid_time_base, 'positive rational time_base required')
        key = row['actor_id'], row['source_frame']
        require(key not in seen, 'duplicate actor/source frame')
        seen.add(key)
        covered.add(row['actor_id'])
        joints = row.get('joints_camera')
        require(isinstance(joints, list) and len(joints) == 77, 'numeric77 joints required')
        require(all(isinstance(v, list) and len(v) == 3 and
                    all(type(x) in (int, float) and math.isfinite(x) for x in v)
                    for v in joints), 'finite numeric77 vectors required')
        for chain in actors[row['actor_id']]['chains']:
            for segment in (chain['upper'], chain['lower']):
                a, b = (joints[names.index(segment[k])] for k in ('source_start', 'source_end'))
                require(any(x != y for x, y in zip(a, b)), 'degenerate source direction')
        plan.append({'row': row, 'blender_frame': row['source_frame'] + profile['frame_offset']})
    require(covered == set(actors), 'each profile actor must have existing candidate samples')
    if samples is not None:
        require(seen == {(aid, frame - profile['frame_offset'])
                         for aid in actors for frame in samples},
                'each requested Blender frame requires an existing candidate for every actor')
    return sorted(plan, key=lambda sample: (sample['blender_frame'], sample['row']['actor_id']))


def main(argv=None):
    # Check execution environment before opening any supplied asset or JSON.
    import bpy
    blender_args = sys.argv[:sys.argv.index('--')] if '--' in sys.argv else sys.argv
    autoexec_flags = [arg for arg in blender_args
                      if arg in ('--disable-autoexec', '--enable-autoexec', '-Y', '-y')]
    require(bpy.app.background and autoexec_flags and autoexec_flags[-1] in ('--disable-autoexec', '-Y'),
            'requires Blender --background --disable-autoexec')
    bpy.context.preferences.filepaths.use_scripts_auto_execute = False
    require(bool(bpy.data.filepath), 'a saved source blend must already be loaded')
    import numpy as np
    from mathutils import Matrix, Vector

    args = parse_args(sys.argv[sys.argv.index('--') + 1:] if argv is None else argv)
    source = Path(bpy.data.filepath).resolve()
    output = Path(args.output).resolve()
    receipt_path = output.with_suffix('.receipt.json')
    validate_output(output, receipt_path, source)
    profile_path, candidate_path = Path(args.profile).resolve(), Path(args.candidates).resolve()
    profile = json.loads(profile_path.read_text(encoding='utf-8'))
    candidates = json.loads(candidate_path.read_text(encoding='utf-8'))
    plan = validate_contract(candidates, profile, args.samples)
    input_hashes = {'candidates': sha256(candidate_path), 'profile': sha256(profile_path)}
    source_hash = sha256(source)
    require(source_hash == profile['expected_blend_sha256'].lower(), 'loaded blend SHA mismatch')
    template = Path(profile['target_template_path'])
    if not template.is_absolute():
        template = profile_path.parent / template
    require(sha256(template) == profile['target_template_sha256'].lower(), 'template SHA mismatch')
    require(output != template.resolve() and output not in (profile_path, candidate_path),
            'output collides with an input')
    # Fail on missing external dependencies; never enable embedded scripts.
    dependencies = []
    for collection in ('libraries', 'images', 'sounds', 'movieclips', 'fonts', 'cache_files'):
        for asset in getattr(bpy.data, collection, ()):
            path = getattr(asset, 'filepath', '')
            if (not path or path == '<builtin>' or getattr(asset, 'packed_file', None)
                    or getattr(asset, 'packed_files', None)
                    or getattr(asset, 'source', '') in ('GENERATED', 'VIEWER')):
                continue
            resolved = Path(bpy.path.abspath(path, library=getattr(asset, 'library', None)))
            require(resolved.is_file(), 'missing/unsupported dependency: ' + str(resolved))
            dependencies.append({'path': str(resolved), 'sha256': sha256(resolved)})
    scene = bpy.context.scene
    actors = profile['actors']
    names = candidates['joint_names']
    tol, unchanged_tol = 2e-5, 1e-6

    def evaluated(ob):
        ev = ob.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = ev.to_mesh()
        try:
            require(ob.name not in {a['mesh'] for a in actors.values()} or
                    len(mesh.vertices) == len(ob.data.vertices), 'mapped evaluated topology changed')
            return [ev.matrix_world @ v.co for v in mesh.vertices]
        finally:
            ev.to_mesh_clear()

    def mean(points, ids):
        return sum((points[i] for i in ids), Vector()) / len(ids)

    def rigid(ob, points, segment):
        ids = segment['vertex_indices']
        p = np.array([list(ob.data.vertices[i].co) for i in ids])
        q = np.array([list(points[i]) for i in ids])
        pc, qc = p.mean(0), q.mean(0)
        require(np.linalg.matrix_rank(p - pc, tol=1e-10) >= 2, 'degenerate component SVD')
        u, _, vt = np.linalg.svd((p - pc).T @ (q - qc))
        rr = vt.T @ u.T
        if np.linalg.det(rr) < 0:
            vt[-1] *= -1
            rr = vt.T @ u.T
        transform = Matrix.Identity(4)
        for i in range(3):
            for j in range(3):
                transform[i][j] = rr[i, j]
        transform.translation = Vector(qc - rr @ pc)
        error = max((transform @ ob.data.vertices[i].co - points[i]).length for i in ids)
        require(error < 1e-5, 'component is not rigid at sample')
        return transform, error

    bindings = {}
    for aid, actor in actors.items():
        ob, rig = (bpy.data.objects.get(actor[k]) for k in ('mesh', 'rig'))
        require(ob is not None and ob.type == 'MESH' and rig is not None and rig.type == 'ARMATURE',
                'mesh/rig binding missing: ' + aid)
        require(ob.library is None and rig.library is None, 'linked objects cannot be edited')
        require(rig.animation_data is not None and rig.animation_data.action is not None
                and not rig.animation_data.nla_tracks, 'requires existing action without NLA')
        ids = set()
        for chain in actor['chains']:
            for segment in (chain['upper'], chain['lower']):
                require(max(segment['vertex_indices']) < len(ob.data.vertices), 'component index out of range')
                bone = rig.pose.bones.get(segment['control'])
                require(bone is not None and not bone.constraints, 'control absent or constrained')
                require(not any(d.data_path.startswith(bone.path_from_id() + '.')
                                for d in rig.animation_data.drivers), 'control has a driver')
                ids.update(segment['vertex_indices'])
        bindings[aid] = ob, rig, ids
    # Baseline every mesh at every sampled frame, including unmodified actors/props.
    baseline, mesh_checks = {}, []
    meshes = [ob for ob in scene.objects if ob.type == 'MESH']
    frame_actors = {}
    for sample in plan:
        frame_actors.setdefault(sample['blender_frame'], set()).add(sample['row']['actor_id'])
    for frame in frame_actors:
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        require(scene.camera is not None, 'sample has no active camera')
        for ob in meshes:
            baseline[frame, ob.name] = evaluated(ob)
    copied_actions = []
    for aid, (_, rig, _) in bindings.items():
        ad = rig.animation_data
        old = ad.action
        slot = getattr(ad, 'action_slot', None)
        identifier = slot.identifier if slot else None
        driver_pointers = [d.as_pointer() for d in ad.drivers]
        ad.action = old.copy()
        if identifier is not None:
            ad.action_slot = next(s for s in ad.action.slots if s.identifier == identifier)
        require([d.as_pointer() for d in ad.drivers] == driver_pointers, 'drivers changed')
        copied_actions.append({'actor_id': aid, 'original': old.name, 'copy': ad.action.name,
                               'drivers_preserved': True})
    records = []
    for sample in plan:
        row, frame = sample['row'], sample['blender_frame']
        aid = row['actor_id']
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        rotation = scene.camera.matrix_world.to_quaternion().to_matrix()
        ob, rig, _ = bindings[aid]
        raw = [v.co.copy() for v in ob.data.vertices]

        def direction(segment):
            a, b = (Vector(row['joints_camera'][names.index(segment[k])])
                    for k in ('source_start', 'source_end'))
            v = (b - a).normalized()
            require(v.length > 1e-5, 'invalid source direction')
            return (rotation @ Vector((v.x, -v.y, -v.z))).normalized()

        for chain in actors[aid]['chains']:
            upper, lower = chain['upper'], chain['lower']
            pts = evaluated(ob)
            tu, eu = rigid(ob, pts, upper)
            tl, el = rigid(ob, pts, lower)
            h, e = (mean(pts, upper[k]) for k in ('proximal_cap_indices', 'distal_cap_indices'))
            l, w = (mean(pts, lower[k]) for k in ('proximal_cap_indices', 'distal_cap_indices'))
            require(min((e - h).length, (w - l).length) > 1e-5, 'degenerate component caps')
            qu = (e - h).normalized().rotation_difference(direction(upper))
            new_upper = Matrix.Translation(h) @ qu.to_matrix().to_4x4() @ Matrix.Translation(-h) @ tu
            new_elbow = new_upper @ mean(raw, lower['proximal_cap_indices'])
            ql = (w - l).normalized().rotation_difference(direction(lower))
            new_lower = Matrix.Translation(new_elbow) @ ql.to_matrix().to_4x4() @ Matrix.Translation(-l) @ tl
            for segment, transform in ((upper, new_upper), (lower, new_lower)):
                pb = rig.pose.bones[segment['control']]
                pb.rotation_mode = 'QUATERNION'
                pb.matrix = rig.matrix_world.inverted() @ transform @ pb.bone.matrix_local
                require(max(abs(x - 1) for x in pb.scale) < 1e-5, 'application would change control scale')
                pb.keyframe_insert('location', frame=frame)
                pb.keyframe_insert('rotation_quaternion', frame=frame)
                bpy.context.view_layer.update()
            actual = evaluated(ob)
            error = max((actual[i] - transform @ raw[i]).length
                        for segment, transform in ((upper, new_upper), (lower, new_lower))
                        for i in segment['vertex_indices'])
            require(error < tol, 'actual visible components did not follow requested map')
            cap_error, lengths = 0.0, []
            for segment, transform in ((upper, new_upper), (lower, new_lower)):
                proximal, distal = segment['proximal_cap_indices'], segment['distal_cap_indices']
                old_length = (mean(pts, distal) - mean(pts, proximal)).length
                new_length = (mean(actual, distal) - mean(actual, proximal)).length
                require(abs(old_length - new_length) < tol, 'component length changed')
                for ids in (proximal, distal):
                    cap_error = max(cap_error, (mean(actual, ids) - transform @ mean(raw, ids)).length)
                lengths.append({'control': segment['control'], 'before': old_length, 'after': new_length})
            require(cap_error < tol, 'cap residual exceeded tolerance')
            records.append({'actor_id': aid, 'source_frame': row['source_frame'], 'pts': row['pts'],
                            'time_base': row['time_base'], 'blender_frame': frame,
                            'source_joint_visibility': row.get('source_joint_visibility', 'unknown'),
                            'side_confirmation': chain['side_confirmation'],
                            'pivot_calibration': chain['pivot_calibration'],
                            'upper_control': upper['control'], 'lower_control': lower['control'],
                            'source_upper_joints': [upper['source_start'], upper['source_end']],
                            'source_lower_joints': [lower['source_start'], lower['source_end']],
                            'registration_residual_m': max(eu, el), 'component_residual_m': error,
                            'cap_residual_m': cap_error, 'lengths': lengths,
                            'source_world_depth_used': False})
    for frame, active in frame_actors.items():
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        allowed = {bindings[aid][0].name: bindings[aid][2] for aid in active}
        for ob in meshes:
            actual, before = evaluated(ob), baseline[frame, ob.name]
            require(len(actual) == len(before), 'evaluated topology changed during application: ' + ob.name)
            ids = allowed.get(ob.name, set())
            nonarm = max(((p - q).length for i, (p, q) in enumerate(zip(actual, before))
                          if i not in ids), default=0.0)
            changed = max(((actual[i] - before[i]).length for i in ids), default=0.0)
            require(nonarm < unchanged_tol, 'non-arm or other mesh changed: ' + ob.name)
            require(not ids or changed > 1e-4, 'mapped actual mesh did not change: ' + ob.name)
            mesh_checks.append({'blender_frame': frame, 'mesh': ob.name,
                                'arm_vertex_change_m': changed, 'nonarm_change_m': nonarm})
    require(sha256(source) == source_hash, 'source file changed during application')
    require(sha256(candidate_path) == input_hashes['candidates'] and
            sha256(profile_path) == input_hashes['profile'], 'JSON inputs changed during application')
    validate_output(output, receipt_path, source)
    scene.frame_set(plan[0]['blender_frame'])
    bpy.context.preferences.filepaths.save_version = 0
    # Reserve both names exclusively so normal invocations cannot overwrite outputs.
    with output.open('xb'):
        pass
    with receipt_path.open('x', encoding='utf-8') as receipt:
        audit = {'status': 'blocked_until_save', 'delivery_allowed': False}
        receipt.write(json.dumps(audit))
    saved = bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)
    require('FINISHED' in saved and output.stat().st_size > 0, 'Blender save failed')
    audit = {'status': 'internal_sample_candidate', 'source_blend_sha256': source_hash,
             'source_sha256': profile['source_sha256'],
             'target_template_sha256': profile['target_template_sha256'],
             'candidates_sha256': input_hashes['candidates'], 'profile_sha256': input_hashes['profile'],
             'output': str(output), 'output_sha256': sha256(output), 'dependencies': dependencies,
             'sample_frames_only': sorted(frame_actors), 'frame_offset': profile['frame_offset'],
             'records': records, 'actual_mesh_checks': mesh_checks, 'copied_actions': copied_actions,
             'per_frame_stretch': False, 'source_world_depth_used': False,
             'temporal_animation_passed': False, 'independent_reopen_passed': False,
             'contacts_passed': False, 'skeleton_gate_passed': False, 'delivery_allowed': False}
    receipt_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    print('BLENDER_POSE_PRIOR', output, audit['output_sha256'], flush=True)


if __name__ == '__main__':
    main()
