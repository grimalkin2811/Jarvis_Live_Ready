"""Tests du script de build scripts/download_models.py (gate ONNX 1.1.1).

Le build doit échouer plutôt que publier un bundle sans les trois modèles
ONNX requis (wake word + pré-traitement).
"""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

REQUIRED = ("melspectrogram.onnx", "embedding_model.onnx", "hey_jarvis_v0.1.onnx")


def _load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "download_models.py"
    spec = importlib.util.spec_from_file_location("jarvis_download_models", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["jarvis_download_models"] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop("jarvis_download_models", None)
        raise
    return module


class DownloadModelsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = _load_script()

    def _run_main(self, writer):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        fake_utils = mock.Mock()
        fake_utils.download_models = mock.Mock(side_effect=writer)
        with mock.patch.dict(
            sys.modules, {"openwakeword": mock.Mock(), "openwakeword.utils": fake_utils}
        ):
            old_root = self.script.ROOT
            self.script.ROOT = root
            try:
                return self.script.main()
            finally:
                self.script.ROOT = old_root

    def test_complete_download_passes(self):
        def writer(models, target_directory):
            target = Path(target_directory)
            target.mkdir(parents=True, exist_ok=True)
            for name in REQUIRED:
                (target / name).write_bytes(b"x" * 2048)

        self.assertEqual(self._run_main(writer), 0)

    def test_partial_download_fails(self):
        def writer(models, target_directory):
            target = Path(target_directory)
            target.mkdir(parents=True, exist_ok=True)
            (target / "hey_jarvis_v0.1.onnx").write_bytes(b"x" * 2048)

        self.assertEqual(self._run_main(writer), 1)

    def test_truncated_file_fails(self):
        def writer(models, target_directory):
            target = Path(target_directory)
            target.mkdir(parents=True, exist_ok=True)
            for name in REQUIRED:
                (target / name).write_bytes(b"tiny")

        self.assertEqual(self._run_main(writer), 1)

    def test_failed_download_without_files_fails(self):
        def writer(models, target_directory):
            raise RuntimeError("réseau coupé")

        self.assertEqual(self._run_main(writer), 1)


if __name__ == "__main__":
    unittest.main()
