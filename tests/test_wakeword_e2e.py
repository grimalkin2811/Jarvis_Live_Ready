"""Test de bout en bout de la chaîne wake word avec de vrais sessions ONNX.

Utilise les fixtures ``tests/fixtures/openwakeword/*.onnx`` (modèles
miniatures générés par ``tests/_make_oww_stubs.py``) avec le VRAI
``openwakeword.Model`` et le VRAI ``onnxruntime`` : chargement, prédictions
streaming sur audio synthétique, clé de score, ``reset()``.

Aucun réseau requis. Ignoré proprement si ``openwakeword``/``onnxruntime`` /
``numpy`` sont indisponibles.
"""

import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

# Aucun téléchargement de modèles pendant les tests (hors ligne).
os.environ.setdefault("JARVIS_NO_MODEL_DOWNLOAD", "1")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "openwakeword"


def _importable(name: str) -> bool:
    try:
        __import__(name)
    except Exception:
        return False
    return True


def _fixtures_available() -> bool:
    return all(
        (FIXTURES / name).is_file()
        for name in ("melspectrogram.onnx", "embedding_model.onnx", "hey_jarvis_v0.1.onnx")
    )


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


@unittest.skipUnless(_importable("openwakeword"), "openwakeword non installé")
@unittest.skipUnless(_importable("onnxruntime"), "onnxruntime non installé")
@unittest.skipUnless(_importable("numpy"), "numpy non installé")
@unittest.skipUnless(_fixtures_available(), "fixtures ONNX miniatures absentes")
class WakewordE2ETests(unittest.TestCase):
    def test_create_model_with_explicit_onnx_paths(self):
        from src import wakeword

        model, key = wakeword.create_model(
            framework="onnx", download=False, extra_dirs=[FIXTURES], verbose=False
        )
        self.assertIsNotNone(model)
        # Chargement par chemin explicite : la clé est le nom du fichier.
        self.assertEqual(key, "hey_jarvis_v0.1")
        self.assertIn("hey_jarvis_v0.1", model.models)

    def test_streaming_predict_returns_bounded_scores(self):
        import numpy as np

        from src import wakeword

        model, key = wakeword.create_model(
            framework="onnx", download=False, extra_dirs=[FIXTURES], verbose=False
        )
        self.assertIsNotNone(model)
        rng = np.random.default_rng(7)
        audio = (rng.uniform(-1.0, 1.0, 1280) * 8000).astype(np.int16)
        last: dict = {}
        for _ in range(8):
            last = model.predict(audio)
        self.assertIn(key, last)
        score = float(last[key])
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)
        # Les poids factices produisent un score non nul après amorçage.
        self.assertGreater(score, 0.0)
        model.reset()

    def test_smoke_check_passes_with_fixtures(self):
        from src import wakeword

        ok, message = wakeword.smoke_check(download=False, extra_dirs=[FIXTURES], verbose=False)
        self.assertTrue(ok, message)
        self.assertIn("hey_jarvis_v0.1", message)

    def test_frozen_bundle_layout_resolves(self):
        """Simule le bundle PyInstaller : _MEIPASS + VRAI chargement ONNX.

        Reproduit la disposition produite par ``packaging/jarvis.spec`` :
        les .onnx à la fois dans ``openwakeword/resources/models`` (copie
        1.1.1) et ``resources/openwakeword``, sous un faux ``_MEIPASS``.
        C'est la validation la plus proche du build distribué sans
        exécutable réel (celui-ci est couvert par le smoke test CI).
        """
        import sys as _sys

        from src import wakeword

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        meipass = Path(tmp.name) / "_internal"
        pkg_dir = meipass / "openwakeword" / "resources" / "models"
        res_dir = meipass / "resources" / "openwakeword"
        pkg_dir.mkdir(parents=True)
        res_dir.mkdir(parents=True)
        names = ("melspectrogram.onnx", "embedding_model.onnx", "hey_jarvis_v0.1.onnx")
        for name in names:
            shutil.copyfile(FIXTURES / name, pkg_dir / name)
            shutil.copyfile(FIXTURES / name, res_dir / name)

        had_frozen = getattr(_sys, "frozen", None)
        had_meipass = getattr(_sys, "_MEIPASS", None)
        _sys.frozen = True  # type: ignore[attr-defined]
        _sys._MEIPASS = str(meipass)  # type: ignore[attr-defined]
        try:
            resolved = wakeword.resolve_onnx_models()
            self.assertTrue(all(resolved.values()), resolved)
            # La résolution préfère l'emplacement du paquet (défauts OK).
            self.assertEqual(resolved["wakeword"], pkg_dir / "hey_jarvis_v0.1.onnx")
            ok, message = wakeword.smoke_check(download=False, verbose=False)
            self.assertTrue(ok, message)
        finally:
            if had_frozen is None:
                del _sys.frozen  # type: ignore[attr-defined]
            else:
                _sys.frozen = had_frozen  # type: ignore[attr-defined]
            if had_meipass is None:
                del _sys._MEIPASS  # type: ignore[attr-defined]
            else:
                _sys._MEIPASS = had_meipass  # type: ignore[attr-defined]

    def test_frozen_with_package_location_only(self):
        """Même sans resources/, l'emplacement paquet suffit (défauts)."""
        import sys as _sys

        from src import wakeword

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        meipass = Path(tmp.name) / "_internal"
        pkg_dir = meipass / "openwakeword" / "resources" / "models"
        pkg_dir.mkdir(parents=True)
        for name in ("melspectrogram.onnx", "embedding_model.onnx", "hey_jarvis_v0.1.onnx"):
            shutil.copyfile(FIXTURES / name, pkg_dir / name)

        # Isole des autres emplacements (dossier utilisateur du sandbox).
        with mock.patch("src.wakeword.paths.openwakeword_models_dir", return_value=Path(tmp.name) / "empty"):
            had_frozen = getattr(_sys, "frozen", None)
            had_meipass = getattr(_sys, "_MEIPASS", None)
            _sys.frozen = True  # type: ignore[attr-defined]
            _sys._MEIPASS = str(meipass)  # type: ignore[attr-defined]
            try:
                ok, message = wakeword.smoke_check(download=False, verbose=False)
                self.assertTrue(ok, message)
            finally:
                if had_frozen is None:
                    del _sys.frozen  # type: ignore[attr-defined]
                else:
                    _sys.frozen = had_frozen  # type: ignore[attr-defined]
                if had_meipass is None:
                    del _sys._MEIPASS  # type: ignore[attr-defined]
                else:
                    _sys._MEIPASS = had_meipass  # type: ignore[attr-defined]

    def test_audio_detects_with_real_model_chain(self):
        """AudioIO complet : vrai Model + clé dynamique + détection."""
        import numpy as np

        _install_fake_sounddevice()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        user_models = Path(tmp.name) / "models" / "openwakeword"
        user_models.mkdir(parents=True, exist_ok=True)
        for name in ("melspectrogram.onnx", "embedding_model.onnx", "hey_jarvis_v0.1.onnx"):
            shutil.copyfile(FIXTURES / name, user_models / name)

        old_data_dir = os.environ.get("JARVIS_DATA_DIR")
        os.environ["JARVIS_DATA_DIR"] = tmp.name
        try:
            from src.audio import AudioIO

            audio = AudioIO(lambda pcm: None)
        finally:
            if old_data_dir is None:
                os.environ.pop("JARVIS_DATA_DIR", None)
            else:
                os.environ["JARVIS_DATA_DIR"] = old_data_dir

        self.assertIsNotNone(audio.wake_model)
        self.assertEqual(audio.wake_key, "hey_jarvis_v0.1")
        rng = np.random.default_rng(3)
        detected = False
        for _ in range(8):
            chunk = (rng.uniform(-1.0, 1.0, 1280) * 8000).astype(np.int16).tobytes()
            if audio._detect_wake_word(chunk):
                detected = True
                break
        self.assertTrue(detected, "la chaîne complète devrait détecter (poids factices)")


if __name__ == "__main__":
    unittest.main()
