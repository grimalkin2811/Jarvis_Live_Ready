"""Tests de la résolution et du chargement OpenWakeWord (src/wakeword.py).

Hermétiques : aucun réseau, aucun modèle réel. Le module ``openwakeword`` est
simulé (faux ``Model`` enregistrant ses arguments) sauf dans
``test_wakeword_e2e.py`` qui utilise les fixtures ONNX miniatures avec le vrai
onnxruntime.
"""

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

# Aucun téléchargement de modèles pendant les tests (hors ligne).
os.environ.setdefault("JARVIS_NO_MODEL_DOWNLOAD", "1")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from src import wakeword  # noqa: E402


def _install_fake_sounddevice() -> None:
    if "sounddevice" in sys.modules:
        return
    sd = types.ModuleType("sounddevice")

    class _FakeStream:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    sd.RawInputStream = _FakeStream
    sd.RawOutputStream = _FakeStream
    sys.modules["sounddevice"] = sd


class FakeModel:
    """Imite openwakeword.model.Model (validation framework/extension incluse)."""

    instances: list["FakeModel"] = []
    next_scores: dict | None = None

    def __init__(self, wakeword_models=None, inference_framework="onnx", **kwargs):
        self.wakeword_models = list(wakeword_models or [])
        self.inference_framework = inference_framework
        self.kwargs = kwargs
        # Même validation que le vrai Model (mélange framework/extension).
        for path in self.wakeword_models:
            if inference_framework == "onnx" and ".tflite" in path:
                raise ValueError("The onnx inference framework is selected, but tflite models were provided!")
            if inference_framework == "tflite" and ".onnx" in path:
                raise ValueError("The tflite inference framework is selected, but onnx models were provided!")
        for key in ("melspec_model_path", "embedding_model_path"):
            value = str(kwargs.get(key, ""))
            if inference_framework == "onnx" and ".tflite" in value:
                raise ValueError("The onnx inference framework is selected, but tflite models were provided!")
            if inference_framework == "tflite" and ".onnx" in value:
                raise ValueError("The tflite inference framework is selected, but onnx models were provided!")
        # Clés = nom du fichier sans extension (comportement réel).
        self.models = {Path(p).stem: object() for p in self.wakeword_models}
        FakeModel.instances.append(self)

    def predict(self, samples):
        if FakeModel.next_scores is not None:
            return dict(FakeModel.next_scores)
        return {key: 0.0 for key in self.models}

    def reset(self):
        pass


def _install_fake_openwakeword(model_cls=FakeModel):
    """Injecte un faux paquet openwakeword + onnxruntime. Retourne un undo."""
    saved = {}
    for name in ("openwakeword", "openwakeword.model", "openwakeword.utils", "onnxruntime"):
        saved[name] = sys.modules.get(name, mock.sentinel.missing)

    pkg = types.ModuleType("openwakeword")
    pkg.MODELS = {
        "hey_jarvis": {"model_path": "/pkg/openwakeword/resources/models/hey_jarvis_v0.1.tflite"}
    }
    pkg.__file__ = "/pkg/openwakeword/__init__.py"
    mod_model = types.ModuleType("openwakeword.model")
    mod_model.Model = model_cls
    mod_utils = types.ModuleType("openwakeword.utils")
    mod_utils.download_models = mock.Mock()
    ort = types.ModuleType("onnxruntime")

    sys.modules["openwakeword"] = pkg
    sys.modules["openwakeword.model"] = mod_model
    sys.modules["openwakeword.utils"] = mod_utils
    sys.modules["onnxruntime"] = ort

    def undo():
        for name, module in saved.items():
            if module is mock.sentinel.missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    return undo


def _make_onnx_files(directory: Path) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    out = {}
    for name in wakeword.REQUIRED_ONNX_MODELS:
        path = directory / name
        path.write_bytes(b"fake-onnx")
        out[name] = path
    return out


def _make_tflite_files(directory: Path) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    out = {}
    for name in wakeword.REQUIRED_TFLITE_MODELS:
        path = directory / name
        path.write_bytes(b"fake-tflite")
        out[name] = path
    return out


class DownloadAllowedTests(unittest.TestCase):
    def test_explicit_overrides_env(self):
        with mock.patch.dict(os.environ, {"JARVIS_NO_MODEL_DOWNLOAD": "1"}):
            self.assertTrue(wakeword.download_allowed(True))
            self.assertFalse(wakeword.download_allowed(False))

    def test_env_disables_by_default(self):
        with mock.patch.dict(os.environ, {"JARVIS_NO_MODEL_DOWNLOAD": "1"}):
            self.assertFalse(wakeword.download_allowed(None))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JARVIS_NO_MODEL_DOWNLOAD", None)
            try:
                self.assertTrue(wakeword.download_allowed(None))
            finally:
                os.environ["JARVIS_NO_MODEL_DOWNLOAD"] = "1"

    def test_zero_value_keeps_download(self):
        with mock.patch.dict(os.environ, {"JARVIS_NO_MODEL_DOWNLOAD": "0"}):
            self.assertTrue(wakeword.download_allowed(None))


class CandidateDirsTests(unittest.TestCase):
    def test_user_dir_first_and_respects_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Résoudre PENDANT que le dossier existe : sur Windows, resolve()
            # étend les noms courts (8.3, "...RUNNER~1...") vers le nom long,
            # mais seulement en ouvrant le chemin ; après suppression, la
            # forme courte est conservée et la comparaison échoue.
            expected = Path(tmp).resolve() / "models" / "openwakeword"
            with mock.patch.dict(os.environ, {"JARVIS_DATA_DIR": tmp}):
                dirs = wakeword.candidate_dirs()
        self.assertTrue(dirs)
        # paths._user_root() canonise l'override via .resolve() : comparer
        # des chemins canoniques (cf. commentaire ci-dessus).
        self.assertEqual(dirs[0], expected)


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Chemin canonique : src/paths.py applique .resolve() à JARVIS_DATA_DIR
        # et les comparaisons doivent utiliser la même forme (noms courts 8.3
        # sous Windows, cf. test_user_dir_first_and_respects_override).
        self.root = Path(self.tmp.name).resolve()
        patcher = mock.patch.dict(os.environ, {"JARVIS_DATA_DIR": str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user_models = self.root / "models" / "openwakeword"

    def test_resolve_finds_user_files(self):
        _make_onnx_files(self.user_models)
        resolved = wakeword.resolve_onnx_models()
        self.assertTrue(all(resolved.values()))
        self.assertEqual(resolved["wakeword"], self.user_models / "hey_jarvis_v0.1.onnx")

    def test_resolve_reports_missing(self):
        resolved = wakeword.resolve_onnx_models()
        # Environnement de test : aucun vrai modèle présent.
        if all(resolved.values()):
            self.skipTest("de vrais modèles sont présents dans cet environnement")
        self.assertIn(None, resolved.values())

    def test_find_wakeword_accepts_future_versions(self):
        self.user_models.mkdir(parents=True, exist_ok=True)
        future = self.user_models / "hey_jarvis_v0.9.onnx"
        future.write_bytes(b"fake")
        found = wakeword.find_wakeword_file(".onnx")
        self.assertEqual(found, future)

    def test_extra_dirs_take_priority(self):
        _make_onnx_files(self.user_models)
        priority = self.root / "priority"
        _make_onnx_files(priority)
        resolved = wakeword.resolve_onnx_models(extra_dirs=[priority])
        self.assertEqual(resolved["melspectrogram"], priority / "melspectrogram.onnx")

    def test_status_and_format_ascii(self):
        _make_onnx_files(self.user_models)
        status = wakeword.models_status()
        self.assertTrue(status["onnx_complete"])
        text = wakeword.format_status(status)
        text.encode("cp1252")
        self.assertTrue(text.isascii())


class DetectScoreKeyTests(unittest.TestCase):
    def test_exact_match_preferred(self):
        model = mock.Mock()
        model.models = {"hey_jarvis": object(), "other": object()}
        self.assertEqual(wakeword.detect_score_key(model), "hey_jarvis")

    def test_filename_stem_matched(self):
        # Cas réel du chargement par chemin explicite (bug 1.1.0 bis).
        model = mock.Mock()
        model.models = {"hey_jarvis_v0.1": object()}
        self.assertEqual(wakeword.detect_score_key(model), "hey_jarvis_v0.1")

    def test_fallback_first_key(self):
        model = mock.Mock()
        model.models = {"custom": object()}
        self.assertEqual(wakeword.detect_score_key(model), "custom")

    def test_fallback_default_when_empty(self):
        model = mock.Mock()
        model.models = {}
        self.assertEqual(wakeword.detect_score_key(model), "hey_jarvis")


class CreateModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.models_dir = self.root / "models"
        FakeModel.instances.clear()
        FakeModel.next_scores = None
        self.addCleanup(lambda: FakeModel.instances.clear())
        self.addCleanup(lambda: setattr(FakeModel, "next_scores", None))
        undo = _install_fake_openwakeword()
        self.addCleanup(undo)

    def test_create_onnx_passes_explicit_paths(self):
        _make_onnx_files(self.models_dir)
        model, key = wakeword.create_model(
            framework="onnx", download=False, extra_dirs=[self.models_dir], verbose=False
        )
        self.assertIsNotNone(model)
        self.assertEqual(key, "hey_jarvis_v0.1")
        self.assertEqual(len(FakeModel.instances), 1)
        instance = FakeModel.instances[0]
        self.assertEqual(instance.inference_framework, "onnx")
        self.assertEqual(instance.wakeword_models, [str(self.models_dir / "hey_jarvis_v0.1.onnx")])
        # Les chemins de pré-traitement sont EXPLICITES (cœur du correctif).
        self.assertEqual(instance.kwargs["melspec_model_path"], str(self.models_dir / "melspectrogram.onnx"))
        self.assertEqual(instance.kwargs["embedding_model_path"], str(self.models_dir / "embedding_model.onnx"))

    def test_create_onnx_missing_files_returns_none(self):
        model, key = wakeword.create_model(
            framework="onnx", download=False, extra_dirs=[self.root / "empty"], verbose=False
        )
        # Aucun fichier dans extra_dirs ni (normalement) ailleurs.
        if model is not None:
            self.skipTest("de vrais modèles sont résolus dans cet environnement")
        self.assertIsNone(model)
        self.assertEqual(key, "")

    def test_create_rejects_unknown_framework(self):
        with self.assertRaises(ValueError):
            wakeword.create_model(framework="tensorrt", download=False, verbose=False)

    def test_create_without_openwakeword(self):
        with mock.patch.dict(sys.modules, {"openwakeword": None, "openwakeword.model": None}):
            # Force l'échec d'import même si le paquet est installé.
            with mock.patch.object(wakeword, "openwakeword_available", return_value=False):
                model, key = wakeword.create_model(download=False, verbose=False)
        self.assertIsNone(model)
        self.assertEqual(key, "")

    def test_load_best_falls_back_to_tflite(self):
        # ONNX incomplet mais TFLite complet + runtime présent.
        tflite_dir = self.root / "tflite"
        _make_tflite_files(tflite_dir)
        fake_tflite = types.ModuleType("tflite_runtime")
        fake_interp = types.ModuleType("tflite_runtime.interpreter")
        with mock.patch.dict(
            sys.modules, {"tflite_runtime": fake_tflite, "tflite_runtime.interpreter": fake_interp}
        ):
            with mock.patch.object(wakeword, "resolve_onnx_models", return_value={
                "melspectrogram": None, "embedding": None, "wakeword": None,
            }):
                model, key, framework = wakeword.load_best_model(
                    download=False, extra_dirs=[tflite_dir], verbose=False
                )
        self.assertIsNotNone(model)
        self.assertEqual(framework, "tflite")
        self.assertEqual(key, "hey_jarvis_v0.1")

    def test_load_best_returns_empty_when_nothing_available(self):
        with mock.patch.object(wakeword, "resolve_onnx_models", return_value={
            "melspectrogram": None, "embedding": None, "wakeword": None,
        }):
            with mock.patch.object(wakeword, "resolve_tflite_models", return_value={
                "melspectrogram": None, "embedding": None, "wakeword": None,
            }):
                model, key, framework = wakeword.load_best_model(download=False, verbose=False)
        self.assertIsNone(model)
        self.assertEqual((key, framework), ("", ""))


class AudioIOIntegrationTests(unittest.TestCase):
    """AudioIO utilise la clé de score détectée (pas la clé codée en dur)."""

    def setUp(self):
        _install_fake_sounddevice()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        user_models = self.root / "models" / "openwakeword"
        _make_onnx_files(user_models)
        patcher = mock.patch.dict(os.environ, {"JARVIS_DATA_DIR": str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)
        FakeModel.instances.clear()
        FakeModel.next_scores = {"hey_jarvis_v0.1": 0.92}
        self.addCleanup(lambda: FakeModel.instances.clear())
        self.addCleanup(lambda: setattr(FakeModel, "next_scores", None))
        undo = _install_fake_openwakeword()
        self.addCleanup(undo)

    def test_audio_uses_detected_key(self):
        from src.audio import AudioIO

        audio = AudioIO(lambda pcm: None)
        self.assertIsNotNone(audio.wake_model)
        self.assertEqual(audio.wake_key, "hey_jarvis_v0.1")
        self.assertEqual(audio.wake_framework, "onnx")
        import numpy as np

        pcm = np.zeros(1280, dtype=np.int16).tobytes()
        # Avec l'ancien code (clé "hey_jarvis" codée en dur), ceci valait False.
        self.assertTrue(audio._detect_wake_word(pcm))

    def test_audio_degraded_without_models(self):
        from src.audio import AudioIO

        for path in (self.root / "models" / "openwakeword").glob("*"):
            path.unlink()
        with mock.patch.object(wakeword, "candidate_dirs", return_value=[self.root / "nowhere"]):
            audio = AudioIO(lambda pcm: None)
        self.assertIsNone(audio.wake_model)


if __name__ == "__main__":
    unittest.main()
