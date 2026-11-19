"""Cross-project geometry, scope and readback acceptance tests (no network)."""
import copy
import unittest
import canvas_layout as layout


def node(i, kind='video', w=356, h=200, x=0, y=0):
    return dict(id=i, type=kind, name=f'Named {i}', position=dict(x=x, y=y),
                size=dict(width=w, height=h), generating=False)


def fixture():
    nodes = [node(1, 'text', 280, 900), node(2, 'image', 700, 240),
             node(3, 'image'), node(4, 'text'), node(99, x=-80, y=5000)]
    nodes += [node(i, w=280 + i * 7, h=180 + i * 3) for i in range(10, 27)]
    snap = dict(projectId='new-project', nodes=nodes, connections=[], truncated=False)
    spec = dict(projectId='new-project', groups=[
        dict(id='A', script_ids=[1], asset_ids=[2, 3], video_ids=list(range(10, 16))),
        dict(id='B', script_ids=[4], asset_ids=[2, 3], video_ids=list(range(16, 22))),
        dict(id='C', script_ids=[], asset_ids=[], video_ids=list(range(22, 27)))],
        expected_reference_pairs=[dict(fromNodeId=2, toNodeId=10)])
    return snap, spec


def moved(snap, plan):
    result = copy.deepcopy(snap)
    for m in plan['moves']:
        next(n for n in result['nodes'] if n['id'] == m['nodeId'])['position'] = dict(x=m['x'], y=m['y'])
    result['connections'] = [dict(fromNodeId=2, toNodeId=10, isReference=True)]
    return result


class LayoutTests(unittest.TestCase):
    def test_baseline_is_not_mutated_with_callers_snapshot(self):
        snap, spec = fixture(); plan = layout.make_plan(snap, spec)
        snap['nodes'] = moved(snap, plan)['nodes']
        next(n for n in snap['nodes'] if n['id'] == 99)['position']['x'] += 100
        snap['connections'] = [dict(fromNodeId=2, toNodeId=10, isReference=True)]
        self.assertIn('outside_scope_changed:99', layout.check(snap, spec, plan)['errors'])

    def test_other_project_sizes_shared_assets_and_seventeen_packages(self):
        snap, spec = fixture()
        plan = layout.make_plan(snap, spec)
        ids = [m['nodeId'] for m in plan['moves']]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids.count(2), 1)
        self.assertEqual(ids.count(3), 1)
        self.assertNotIn(99, ids)
        result = layout.check(moved(snap, plan), spec, plan)
        self.assertEqual(result['status'], 'structure_verified_visual_pending', result)
        self.assertEqual(result['errors'], [])

    def test_partial_move_is_not_accepted(self):
        snap, spec = fixture(); plan = layout.make_plan(snap, spec)
        actual = moved(snap, plan)
        actual['nodes'][0]['position'] = snap['nodes'][0]['position']
        self.assertIn('position_mismatch:1', layout.check(actual, spec, plan)['errors'])

    def test_unrelated_node_move_detected(self):
        snap, spec = fixture(); plan = layout.make_plan(snap, spec)
        actual = moved(snap, plan)
        next(n for n in actual['nodes'] if n['id'] == 99)['position']['x'] += 1
        self.assertIn('outside_scope_changed:99', layout.check(actual, spec, plan)['errors'])

    def test_title_area_collision_detected(self):
        snap, spec = fixture(); plan = layout.make_plan(snap, spec)
        actual = moved(snap, plan)
        a, b = actual['nodes'][0:2]
        a['position'] = dict(x=0, y=100)
        b['position'] = dict(x=0, y=100 + a['size']['height'] + 1)
        self.assertTrue(any(e.startswith('overlap:') for e in layout.check(actual, spec, plan)['errors']))

    def test_missing_reference_and_false_reference_detected(self):
        snap, spec = fixture(); plan = layout.make_plan(snap, spec)
        actual = moved(snap, plan); actual['connections'][0]['isReference'] = False
        self.assertIn('missing_reference:2->10', layout.check(actual, spec, plan)['errors'])

    def test_new_nodes_only_keeps_approved_layout(self):
        snap, spec = fixture()
        spec['groups'] = [dict(id='new', video_ids=[26])]
        plan = layout.make_plan(snap, spec)
        self.assertEqual([m['nodeId'] for m in plan['moves']], [26])
        self.assertEqual(layout.check(moved(snap, plan), spec, plan)['errors'], [])

    def test_incomplete_snapshot_and_unknown_dimensions_fail_closed(self):
        for change in ('truncated', 'size'):
            snap, spec = fixture()
            if change == 'truncated': snap['truncated'] = True
            else: snap['nodes'][0]['size']['width'] = 0
            with self.assertRaises(ValueError): layout.make_plan(snap, spec)

    def test_project_identity_and_duplicate_role_rejected(self):
        snap, spec = fixture(); spec['projectId'] = 'wrong'
        with self.assertRaises(ValueError): layout.make_plan(snap, spec)

    def test_incomplete_or_dangling_connections_rejected(self):
        for bad in (None, 'missing', [dict(fromNodeId=999, toNodeId=10, isReference=True)]):
            snap, spec = fixture()
            if bad == 'missing': del snap['connections']
            else: snap['connections'] = bad
            with self.assertRaises(ValueError): layout.make_plan(snap, spec)
        snap, spec = fixture(); spec['groups'][1]['video_ids'].append(10)
        with self.assertRaises(ValueError): layout.make_plan(snap, spec)

    def test_rename_and_generation_are_not_layout_acceptance(self):
        snap, spec = fixture(); plan = layout.make_plan(snap, spec)
        actual = moved(snap, plan); actual['nodes'][0]['name'] = 'wrong'
        actual['nodes'][1]['generating'] = True
        errors = layout.check(actual, spec, plan)['errors']
        self.assertIn('content_changed:1', errors)
        self.assertIn('generation_state_changed:2', errors)

    def test_final_plan_cannot_hide_reversed_package_order(self):
        snap, spec = fixture(); plan = layout.make_plan(snap, spec)
        a = next(m for m in plan['moves'] if m['nodeId'] == 10)
        b = next(m for m in plan['moves'] if m['nodeId'] == 11)
        a['x'], b['x'] = b['x'], a['x']
        self.assertIn('video_order:A', layout.check(moved(snap, plan), spec, plan)['errors'])

    def test_final_plan_cannot_put_material_below_video(self):
        snap, spec = fixture(); spec['groups'][0]['asset_ids'].append(27)
        snap['nodes'].append(node(27, 'image'))
        plan = layout.make_plan(snap, spec)
        a = next(m for m in plan['moves'] if m['nodeId'] == 27)
        a['y'] = max(m['y'] for m in plan['moves']) + 1000
        self.assertIn('layer_order:A', layout.check(moved(snap, plan), spec, plan)['errors'])


if __name__ == '__main__': unittest.main()
