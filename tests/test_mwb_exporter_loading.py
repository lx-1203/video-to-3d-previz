"""Regression coverage for real GEM video metadata and restricted loading."""
import importlib.util
from pathlib import Path
import pickle
import tempfile
import unittest

import numpy as np
import torch


ROOT = Path(__file__).resolve().parents[1]
EXPORTER = ROOT / "scripts/motion/export_gem_bvh.py"


class ExporterLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("exporter_loading_test", EXPORTER)
        cls.exporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.exporter)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / "motion.pt"

    def export_invalid_schema(self, value):
        torch.save(value, self.cache)
        # A schema error proves deserialization succeeded. No geometry assets
        # are needed, because this invalid cache must fail before SOMA runs.
        try:
            with self.assertRaisesRegex(ValueError, "pred_soma_params_global"):
                self.exporter.export(self.cache, self.root, self.root / "out.bvh", 30)
        except pickle.UnpicklingError as error:
            self.fail(f"Numeric GEM metadata was rejected before schema validation: {error}")

    def test_real_video_numeric_tracking_metadata_passes_restricted_loader(self):
        before = torch.serialization.get_safe_globals().copy()
        self.export_invalid_schema({
            "length": torch.tensor([3]),
            "tracking": {
                "frame_ids": np.arange(3, dtype=np.int64),
                "boxes_xyxy": np.zeros((3, 4), dtype=np.float32),
                "track_ids": np.ones(3, dtype=np.int32),
                "observed_primary": np.ones(3, dtype=np.bool_),
            },
        })
        # PyTorch returns a list constructed from a set; membership must be
        # restored, but set iteration order can change after a scoped load.
        self.assertCountEqual(torch.serialization.get_safe_globals(), before)

    def test_tensor_only_cache_keeps_schema_validation(self):
        self.export_invalid_schema({"length": torch.tensor([3])})

    def test_empty_numeric_tracking_buffers_pass_restricted_loader(self):
        self.export_invalid_schema({"tracking": {
            "frame_ids": np.empty(0, dtype=np.int64),
            "boxes_xyxy": np.empty((0, 4), dtype=np.float32),
            "observed_primary": np.empty(0, dtype=np.bool_),
        }})

    def test_preexisting_safe_globals_survive_scoped_export(self):
        with torch.serialization.safe_globals([bytes, np.dtype]):
            before = torch.serialization.get_safe_globals().copy()
            self.export_invalid_schema({"tracking": np.zeros(3, dtype=np.float32)})
            self.assertCountEqual(torch.serialization.get_safe_globals(), before)

    def test_unapproved_pickle_global_is_rejected_without_execution(self):
        marker = self.root / "must_not_exist.txt"

        class UnapprovedPayload:
            def __reduce__(self):
                expression = f"__import__('pathlib').Path({str(marker)!r}).write_text('executed')"
                return eval, (expression,)

        torch.save({"tracking": UnapprovedPayload()}, self.cache)
        before = torch.serialization.get_safe_globals().copy()
        with self.assertRaises(pickle.UnpicklingError):
            self.exporter.export(self.cache, self.root, self.root / "out.bvh", 30)
        self.assertFalse(marker.exists())
        self.assertCountEqual(torch.serialization.get_safe_globals(), before)

    def test_numpy_object_arrays_are_not_allowlisted(self):
        torch.save({"tracking": np.array(["unapproved"], dtype=object)}, self.cache)
        with self.assertRaises(pickle.UnpicklingError):
            self.exporter.export(self.cache, self.root, self.root / "out.bvh", 30)


if __name__ == "__main__":
    unittest.main()
