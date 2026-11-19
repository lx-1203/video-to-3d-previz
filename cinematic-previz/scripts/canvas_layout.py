"""Offline Updream layout planning/readback checks. Never writes to a browser."""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def number(v):
    return type(v) in (int, float) and math.isfinite(v)


def snapshot(value):
    if 'ok' in value:
        if value['ok'] is not True:
            raise ValueError('snapshot API did not succeed')
        value = value['data']
    if value.get('truncated') is not False:
        raise ValueError('complete snapshot required; merge all pages and set truncated=false')
    nodes = value.get('nodes')
    if not isinstance(nodes, list) or not nodes:
        raise ValueError('nodes required')
    result = {}
    for n in nodes:
        i = n['id']
        if type(i) is not int or i <= 0 or i in result:
            raise ValueError('invalid/duplicate node id')
        for key in ('x', 'y'):
            if not number(n.get('position', {}).get(key)):
                raise ValueError(f'invalid position:{i}')
        for key in ('width', 'height'):
            v = n.get('size', {}).get(key)
            if not number(v) or v <= 0:
                raise ValueError(f'actual positive dimensions required:{i}')
        result[i] = n
    if not isinstance(value.get('connections'), list):
        raise ValueError('complete connections list required')
    for edge in value['connections']:
        if (not isinstance(edge, dict) or any(type(edge.get(k)) is not int or edge[k] not in result
                for k in ('fromNodeId', 'toNodeId')) or type(edge.get('isReference')) is not bool):
            raise ValueError('invalid/dangling connection')
    return value, result


def inputs(raw, spec):
    snap, nodes = snapshot(raw)
    if not spec.get('projectId') or str(snap.get('projectId')) != str(spec['projectId']):
        raise ValueError('project mismatch')
    groups = spec.get('groups')
    if not isinstance(groups, list) or not groups:
        raise ValueError('ordered groups required')
    roles, group_names = {}, set()
    for g in groups:
        if not isinstance(g.get('id'), str) or not g['id'].strip() or g['id'] in group_names:
            raise ValueError('unique group id required')
        group_names.add(g['id'])
        if not any(g.get(k) for k in ('script_ids', 'asset_ids', 'video_ids')):
            raise ValueError('empty group')
        for key in ('script_ids', 'asset_ids', 'video_ids'):
            ids = g.get(key, [])
            if not isinstance(ids, list) or any(type(i) is not int for i in ids) or len(ids) != len(set(ids)):
                raise ValueError('invalid role list')
            for i in ids:
                if i not in nodes or (i in roles and (key != 'asset_ids' or roles[i] != key)):
                    raise ValueError('missing node or duplicate non-shared role')
                if key == 'script_ids' and nodes[i]['type'] != 'text':
                    raise ValueError('script must be text')
                if key == 'video_ids' and nodes[i]['type'] != 'video':
                    raise ValueError('video must be video')
                roles[i] = key
    for pair in spec.get('expected_reference_pairs', []):
        if any(type(pair.get(k)) is not int or pair[k] not in nodes for k in ('fromNodeId', 'toNodeId')):
            raise ValueError('reference endpoints must exist')
    return snap, nodes, roles


def make_plan(raw, spec, columns=5, gap=80, title_gap=40, zone_gap=160):
    snap, nodes, roles = inputs(raw, spec)
    if type(columns) is not int or columns < 1 or columns > 20:
        raise ValueError('columns must be 1..20')
    if any(not number(v) or v <= 0 for v in (gap, title_gap, zone_gap)):
        raise ValueError('positive spacing required')
    settings = dict(columns=columns, gap=gap, title_gap=title_gap, zone_gap=zone_gap)
    foreign = [n for i, n in nodes.items() if i not in roles]
    x0 = min((nodes[i]['position']['x'] for i in roles), default=0)
    y = max([title_gap] + [n['position']['y'] + n['size']['height'] + zone_gap + title_gap for n in foreign])
    moves, bands = [], []

    def row(ids, label, count=columns):
        nonlocal y
        if not ids:
            return
        start = y
        width = max(nodes[i]['size']['width'] for i in ids)
        for offset in range(0, len(ids), count):
            chunk = ids[offset:offset + count]
            for col, i in enumerate(chunk):
                moves.append(dict(nodeId=i, x=x0 + col * (width + gap), y=y))
            y += max(nodes[i]['size']['height'] for i in chunk) + gap + title_gap
        bands.append(dict(label=label, nodeIds=ids, top=start, bottom=y - gap - title_gap))

    asset_uses = {}
    for g in spec['groups']:
        for i in g.get('asset_ids', []):
            asset_uses[i] = asset_uses.get(i, 0) + 1
    shared = [i for i, count in asset_uses.items() if count > 1]
    row(shared, 'shared_assets')
    if shared:
        y += zone_gap
    for g in spec['groups']:
        row(g.get('script_ids', []), g['id'] + ':scripts')
        row([i for i in g.get('asset_ids', []) if i not in shared], g['id'] + ':assets')
        row(g.get('video_ids', []), g['id'] + ':videos')
        y += zone_gap
    return dict(projectId=spec['projectId'], spec_sha256=digest(spec), settings=settings,
                moves=moves, bands=bands, baseline=copy.deepcopy(snap),
                status='plan_only_not_applied')


def check(raw, spec, plan):
    actual, nodes, roles = inputs(raw, spec)
    before, old = snapshot(plan['baseline'])
    if str(before.get('projectId')) != str(spec['projectId']) or plan['spec_sha256'] != digest(spec):
        raise ValueError('plan belongs to another project/spec')
    moves = plan['moves']
    if len(moves) != len(roles) or {m['nodeId'] for m in moves} != set(roles):
        raise ValueError('plan scope mismatch')
    errors = []
    for m in moves:
        if any(abs(nodes[m['nodeId']]['position'][k] - m[k]) > 0.001 for k in ('x', 'y')):
            errors.append(f"position_mismatch:{m['nodeId']}")
    for i, prior in old.items():
        now = nodes.get(i)
        if now is None:
            errors.append(f'missing_original_node:{i}')
            continue
        if i not in roles and prior['position'] != now['position']:
            errors.append(f'outside_scope_changed:{i}')
        # All other fields were read with the same snapshot schema before/after.
        state = ('status', 'generating', 'failed')
        if any(prior.get(k) != now.get(k) for k in state):
            errors.append(f'generation_state_changed:{i}')
        ignored = {'position', *state}
        if {k:v for k,v in prior.items() if k not in ignored} != {k:v for k,v in now.items() if k not in ignored}:
            errors.append(f'content_changed:{i}')
    margin = plan['settings']['title_gap']
    uses = {}
    for g in spec['groups']:
        for i in g.get('asset_ids', []):
            uses[i] = uses.get(i, 0) + 1
    shared = {i for i, count in uses.items() if count > 1}
    zones = []
    for g in spec['groups']:
        videos = g.get('video_ids', [])
        order = [(nodes[i]['position']['y'], nodes[i]['position']['x']) for i in videos]
        if order != sorted(order):
            errors.append('video_order:' + g['id'])
        layers = [g.get('script_ids', []), [i for i in g.get('asset_ids', []) if i not in shared], videos]
        occupied = [ids for ids in layers if ids]
        for upper, lower in zip(occupied, occupied[1:]):
            if max(nodes[i]['position']['y'] + nodes[i]['size']['height'] for i in upper) > min(nodes[i]['position']['y'] - margin for i in lower):
                errors.append('layer_order:' + g['id'])
        ids = [i for layer in layers for i in layer]
        if ids:
            zones.append((min(nodes[i]['position']['y'] - margin for i in ids),
                          max(nodes[i]['position']['y'] + nodes[i]['size']['height'] for i in ids)))
    if any(a[1] > b[0] for a, b in zip(zones, zones[1:])):
        errors.append('group_order_or_separation')
    if shared and zones and max(nodes[i]['position']['y'] + nodes[i]['size']['height'] for i in shared) > zones[0][0]:
        errors.append('shared_assets_not_before_groups')
    values = list(nodes.values())
    for index, a in enumerate(values):
        for b in values[index + 1:]:
            if a['id'] not in roles and b['id'] not in roles:
                continue
            ax, ay = a['position']['x'], a['position']['y']
            bx, by = b['position']['x'], b['position']['y']
            if (ax < bx + b['size']['width'] and bx < ax + a['size']['width']
                    and ay - margin < by + b['size']['height'] and by - margin < ay + a['size']['height']):
                errors.append(f"overlap:{a['id']}:{b['id']}")
    for p in spec.get('expected_reference_pairs', []):
        if not any(c.get('fromNodeId') == p['fromNodeId'] and c.get('toNodeId') == p['toNodeId']
                   and c.get('isReference') is True for c in actual.get('connections', [])):
            errors.append(f"missing_reference:{p['fromNodeId']}->{p['toNodeId']}")
    old_edges = {(c['fromNodeId'], c['toNodeId'], c.get('isReference')) for c in before.get('connections', [])}
    new_edges = {(c['fromNodeId'], c['toNodeId'], c.get('isReference')) for c in actual.get('connections', [])}
    if not old_edges.issubset(new_edges):
        errors.append('original_connections_removed_or_changed')
    return dict(status='failed' if errors else 'structure_verified_visual_pending', errors=errors,
                moved_nodes=len(moves), expected_references=len(spec.get('expected_reference_pairs', [])),
                visual_verified=False, generation_readiness_verified=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['plan', 'check'])
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('spec', type=Path)
    parser.add_argument('--plan', type=Path, help='required for check')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        load = lambda p: json.loads(p.read_text(encoding='utf-8-sig'))
        result = make_plan(load(args.snapshot), load(args.spec)) if args.mode == 'plan' else check(load(args.snapshot), load(args.spec), load(args.plan))
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k in ('status', 'errors', 'moved_nodes')}, ensure_ascii=False))
        return 1 if result.get('errors') else 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(json.dumps(dict(status='invalid_input', error=str(exc)), ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
