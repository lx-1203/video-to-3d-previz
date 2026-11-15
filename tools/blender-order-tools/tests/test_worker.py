import sys
import unittest
from types import SimpleNamespace
from unittest import mock
from orderflow import core, worker
import test_core


class WorkerTests(unittest.TestCase):
    setUp = test_core.CoreTests.setUp
    tearDown = test_core.CoreTests.tearDown

    def test_nested_compositor_file_output_and_cycle_are_rejected(self):
        inner = SimpleNamespace(name='Inner', nodes=[])
        outer = SimpleNamespace(name='Outer', nodes=[])
        inner.nodes = [SimpleNamespace(type='GROUP', node_tree=outer), SimpleNamespace(type='OUTPUT_FILE', name='ExternalWriter', mute=True)]
        outer.nodes = [SimpleNamespace(type='GROUP', node_tree=inner)]
        scene = SimpleNamespace(name='RenderScene', render=SimpleNamespace(use_compositing=True), compositing_node_group=outer)
        errors = worker.unsupported_compositor_outputs(scene)
        self.assertEqual(len(errors), 1)
        self.assertIn('ExternalWriter', errors[0])
        scene.render.use_compositing = False
        self.assertEqual(worker.unsupported_compositor_outputs(scene), [])

    def test_unrelated_scene_groups_and_material_nodes_are_not_inspected(self):
        scene = SimpleNamespace(name='SafeScene', render=SimpleNamespace(use_compositing=True), compositing_node_group=None, use_nodes=False)
        self.assertEqual(worker.unsupported_compositor_outputs(scene), [])

    def test_environment_failure_stops_before_opening_or_rendering_snapshot(self):
        job_path = core.write_job(self.job, self.spec)
        bpy = SimpleNamespace(ops=mock.Mock())
        with mock.patch.dict(sys.modules, {'bpy': bpy}), mock.patch.object(worker.media, 'run_process', side_effect=RuntimeError('missing av')) as process:
            with self.assertRaisesRegex(RuntimeError, 'missing av'):
                worker.run_job(job_path)
        bpy.ops.wm.open_mainfile.assert_not_called()
        self.assertFalse((self.job / 'scene.blend').exists())
        self.assertIn('--check-env', process.call_args.args[0])
