"""Pure stdlib semantic observations, never Blender/IK/retargeting or animation.

build_semantic_profile(candidates, profile) accepts decoded JSON objects.
Profile: source_sha256, target_template_sha256, actors mapping actor_id to
{mesh, rig, chains: [{source_start, source_end, target_component, target_control,
side_confirmation: {status: 'confirmed', evidence: ...},
pivot_calibration: {status: 'calibrated', evidence: ...}}]}.
Evidence is an external audit reference, not verification performed here.
Missing evidence blocks adaptation; even ready observations are not animation.
Names are exact official names. Hand denotes wrist, never a target palm end.
Source camera coordinates are not transformed into target coordinates.
"""

import copy
from fractions import Fraction
import math
import re


OFFICIAL_JOINT_NAMES = tuple(
    ['Hips', 'Spine1', 'Spine2', 'Chest', 'Neck1', 'Neck2', 'Head', 'HeadEnd',
     'Jaw', 'LeftEye', 'RightEye']
    + [side + suffix for side in ('Left', 'Right') for suffix in
       (['Shoulder', 'Arm', 'ForeArm', 'Hand']
        + ['Hand' + finger + part for finger in ('Thumb', 'Index', 'Middle', 'Ring', 'Pinky')
           for part in (('1', '2', '3', 'End') if finger == 'Thumb'
                        else ('1', '2', '3', '4', 'End'))])]
    + [side + part for side in ('Left', 'Right')
       for part in ('Leg', 'Shin', 'Foot', 'ToeBase', 'ToeEnd')]
)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _hash(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-fA-F]{64}', value) is not None


def _vector(value, size):
    if (isinstance(value, list) and len(value) == size
            and all(type(x) in (int, float) and math.isfinite(x) for x in value)):
        return list(value)
    return None


def _semantic(name):
    for suffix, meaning in (('Shoulder', 'clavicle'), ('ForeArm', 'elbow'),
                            ('Arm', 'upper_arm_root'), ('Hand', 'wrist')):
        if name in ('Left' + suffix, 'Right' + suffix):
            return meaning
    return name


def _proof(value, status):
    if not isinstance(value, dict) or value.get('status') != status:
        return False
    evidence = value.get('evidence')
    return _text(evidence) or (isinstance(evidence, list) and bool(evidence)
                               and all(_text(item) for item in evidence))


def build_semantic_profile(candidates, profile):
    """Return per-row observations and fail-closed adaptation reasons.

    Structural errors raise ValueError. Incomplete private mappings and numeric
    observations remain in output as blocked records, with no guessed aliases.
    Rows retain input order and exact PTS/time_base (no interpolation/alignment).
    Projection validity is geometric only; visibility is independently carried
    from source_joint_visibility (scalar, joint list or exact-name mapping).
    """
    if not isinstance(candidates, dict) or not isinstance(profile, dict):
        raise ValueError('candidates and profile must be JSON objects')
    names = candidates.get('joint_names')
    if (not isinstance(names, list) or len(names) != 77
            or any(not isinstance(n, str) for n in names)
            or set(names) != set(OFFICIAL_JOINT_NAMES)):
        raise ValueError('joint_names must contain the 77 unique official names')
    rows = candidates.get('rows')
    actors = profile.get('actors', {})
    if not isinstance(rows, list) or not isinstance(actors, dict):
        raise ValueError('rows must be a list and profile.actors an object')
    global_reasons = []
    for field in ('source_sha256', 'target_template_sha256'):
        if not _hash(profile.get(field)):
            global_reasons.append('invalid_profile_' + field)
    if (not _hash(candidates.get('source_sha256'))
            or str(candidates.get('source_sha256')).lower()
            != str(profile.get('source_sha256')).lower()):
        global_reasons.append('source_sha256_mismatch')
    index = {name: i for i, name in enumerate(names)}
    output_rows = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('each row must be an object')
        actor_id = row.get('actor_id')
        if (not _text(actor_id) or type(row.get('pts')) is not int
                or type(row.get('source_frame')) is not int):
            raise ValueError('row requires actor_id and integer pts/source_frame')
        try:
            if not isinstance(row.get('time_base'), str) or Fraction(row['time_base']) <= 0:
                raise ValueError()
        except (ValueError, ZeroDivisionError):
            raise ValueError('time_base must be a positive rational string') from None
        for field in ('joints_camera', 'keypoints_2d', 'projection_valid'):
            if not isinstance(row.get(field), list) or len(row[field]) != 77:
                raise ValueError(field + ' must have 77 entries')
        reasons = list(global_reasons)
        actor = actors.get(actor_id)
        if not isinstance(actor, dict):
            actor = {}
            reasons.append('actor_mapping_missing')
        if not all(_text(actor.get(field)) for field in ('mesh', 'rig')):
            reasons.append('mesh_rig_mapping_missing')
        chains = actor.get('chains', [])
        if not isinstance(chains, list):
            raise ValueError('actor.chains must be a list')
        if not chains:
            reasons.append('explicit_chains_missing')
        result = {k: row[k] for k in ('actor_id', 'source_frame', 'pts', 'time_base')}
        result.update(mesh=actor.get('mesh'), rig=actor.get('rig'), chains=[], reasons=reasons)

        def endpoint(name):
            if name not in index:
                return None
            i = index[name]
            visibility = row.get('source_joint_visibility', 'unknown')
            if isinstance(visibility, list):
                visibility = visibility[i] if len(visibility) == 77 else 'unknown'
            elif isinstance(visibility, dict):
                visibility = visibility.get(name, 'unknown')
            if visibility is None:
                visibility = 'unknown'
            return {'joint': name, 'semantic': _semantic(name),
                    'camera': _vector(row['joints_camera'][i], 3),
                    'keypoint_2d': _vector(row['keypoints_2d'][i], 2),
                    'projection_valid': row['projection_valid'][i] is True,
                    'visibility': copy.deepcopy(visibility)}

        for chain in chains:
            if not isinstance(chain, dict):
                raise ValueError('each chain must be an object')
            blocked = list(reasons)
            for field, status in (('side_confirmation', 'confirmed'),
                                  ('pivot_calibration', 'calibrated')):
                if not _proof(chain.get(field), status):
                    blocked.append(field + '_unproven')
            for field in ('target_component', 'target_control'):
                if not _text(chain.get(field)):
                    blocked.append(field + '_missing')
            endpoints = []
            for field in ('source_start', 'source_end'):
                name = chain.get(field)
                point = endpoint(name) if isinstance(name, str) else None
                endpoints.append(point)
                if point is None:
                    blocked.append(field + '_not_official')
            start, end = endpoints
            direction = None
            direction_2d = None
            if start and end:
                if start['camera'] is not None and end['camera'] is not None:
                    direction = [b - a for a, b in zip(start['camera'], end['camera'])]
                    if not all(math.isfinite(v) for v in direction) or not any(direction):
                        direction = None
                if direction is None:
                    blocked.append('invalid_or_degenerate_camera_chain')
                if (start['projection_valid'] and end['projection_valid']
                        and start['keypoint_2d'] is not None and end['keypoint_2d'] is not None):
                    direction_2d = [b - a for a, b in zip(start['keypoint_2d'], end['keypoint_2d'])]
                    if not all(math.isfinite(v) for v in direction_2d):
                        direction_2d = None
            item = copy.deepcopy(chain)
            item.update(start=start, end=end, direction_camera=direction,
                        direction_2d=direction_2d, reasons=blocked,
                        status='blocked' if blocked else 'ready_for_semantic_adaptation')
            result['chains'].append(item)
        result['status'] = ('blocked' if reasons or any(c['reasons'] for c in result['chains'])
                            else 'ready_for_semantic_adaptation')
        output_rows.append(result)
    return {'source_sha256': candidates.get('source_sha256'),
            'target_template_sha256': profile.get('target_template_sha256'),
            'status': 'blocked' if global_reasons or not output_rows or any(
                r['status'] == 'blocked' for r in output_rows) else 'ready_for_semantic_adaptation',
            'reasons': global_reasons, 'rows': output_rows,
            'executable_animation': False, 'delivery_allowed': False,
            'operations_performed': ['semantic_observations_only']}
