"""Loader strict des fixtures TTS (scripts/validation/tts_fixtures.py).

Garanties testées : correspondance par texte EXACT uniquement, sha256 +
format + durées vérifiés avant tout usage, problèmes retournés (jamais
avalés), refus de remplacement silencieux dans l'outil de régénération.
Ces tests ne contactent AUCUNE API (R : tests unitaires sans clé).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO = Path(__file__).resolve().parents[1]
from scripts.validation import tts_fixtures as fx  # noqa: E402


def _all_manifest_texts() -> list[str]:
    manifest = json.loads(fx.MANIFEST_PATH.read_text(encoding="utf-8"))
    return [entry["text"] for entry in manifest["fixtures"]]


class TtsFixturesLoaderTests(unittest.TestCase):
    def test_toutes_les_fixtures_versionnees_sont_valides(self) -> None:
        prompts = _all_manifest_texts()
        self.assertGreaterEqual(len(prompts), 10, "le manifeste doit rester complet")
        cache, problems = fx.load_fixtures(prompts)
        self.assertEqual(problems, [], "aucune fixture versionnée ne doit être douteuse")
        self.assertEqual(len(cache), len(prompts))
        for prompt, pcm in cache.items():
            self.assertIsInstance(pcm, bytes)
            self.assertGreater(len(pcm), 1600, f"PCM trop court pour {prompt!r}")
            self.assertEqual(len(pcm) % 2, 0, "PCM s16le : nombre d'octets pair")

    def test_prompt_inconnu_refuse_sans_approximation(self) -> None:
        cache, problems = fx.load_fixtures(["Phrase qui n'existe nulle part."])
        self.assertEqual(cache, {})
        self.assertEqual(len(problems), 1)
        self.assertIn("aucune fixture", problems[0])

    def test_sha256_corrompu_detecte(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._isolate_fixtures(Path(td))
            manifest = json.loads(fx.MANIFEST_PATH.read_text(encoding="utf-8"))
            # Fausser UNIQUEMENT le sha256 (le fichier reste en place).
            manifest["fixtures"][0]["sha256"] = "0" * 64
            fx.MANIFEST_PATH.write_text(json.dumps(manifest), encoding="utf-8")
            cache, problems = fx.load_fixtures([manifest["fixtures"][0]["text"]])
            self.assertEqual(cache, {}, "une fixture corrompue ne doit jamais être renvoyée")
            self.assertEqual(len(problems), 1)
            self.assertIn("corrompue", problems[0])

    def test_format_invalide_detecte(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            self._isolate_fixtures(td_path)
            # Fabriquer un WAV valide mais en 8 kHz (format interdit).
            wrong = td_path / "tts_fixtures" / "mauvais_format.wav"
            with wave.open(str(wrong), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(8000)
                wf.writeframes(b"\x00\x00" * 16000)  # 2 s de silence 8 kHz
            text = "Fixture de mauvais format pour le test."
            manifest = {
                "fixtures": [
                    {
                        "file": "mauvais_format.wav",
                        "text": text,
                        "sha256": __import__("hashlib").sha256(wrong.read_bytes()).hexdigest(),
                    }
                ]
            }
            fx.MANIFEST_PATH.write_text(json.dumps(manifest), encoding="utf-8")
            cache, problems = fx.load_fixtures([text])
            self.assertEqual(cache, {})
            self.assertEqual(len(problems), 1)
            self.assertIn("format", problems[0])

    def test_fixture_tronquee_detectee(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            self._isolate_fixtures(td_path)
            # Un WAV 16 kHz valide mais bien trop court pour sa phrase.
            wrong = td_path / "tts_fixtures" / "trop_court.wav"
            with wave.open(str(wrong), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(b"\x00\x10" * 800)  # 0,05 s
            text = "Phrase longue exigée par le manifeste pour ce test de troncature."
            manifest = {
                "fixtures": [
                    {
                        "file": "trop_court.wav",
                        "text": text,
                        "sha256": __import__("hashlib").sha256(wrong.read_bytes()).hexdigest(),
                    }
                ]
            }
            fx.MANIFEST_PATH.write_text(json.dumps(manifest), encoding="utf-8")
            cache, problems = fx.load_fixtures([text])
            self.assertEqual(cache, {})
            self.assertIn("durée", problems[0])

    def _isolate_fixtures(self, td: Path) -> None:
        """Copie les vraies fixtures dans un dossier temporaire isolé."""
        shutil.copytree(fx.FIXTURES_DIR, td / "tts_fixtures")
        self._old = (fx.FIXTURES_DIR, fx.MANIFEST_PATH)
        fx.FIXTURES_DIR = td / "tts_fixtures"
        fx.MANIFEST_PATH = td / "tts_fixtures" / "manifest.json"
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        fx.FIXTURES_DIR, fx.MANIFEST_PATH = self._old


class MakeTtsFixturesToolTests(unittest.TestCase):
    def test_remplacement_refuse_sans_force(self) -> None:
        """Remplacer une fixture existante exige --force explicite : les
        entrées des validations réelles ne changent jamais silencieusement."""
        existing = json.loads(fx.MANIFEST_PATH.read_text(encoding="utf-8"))["fixtures"][0]
        source = fx.FIXTURES_DIR / existing["file"]
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "validation" / "make_tts_fixtures.py"),
             "add", "--in", str(source), "--texte", existing["text"],
             "--out", Path(existing["file"]).stem],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0, "le remplacement doit être refusé")
        self.assertIn("REFUSÉ", result.stderr + result.stdout)
        self.assertIn("--force", result.stderr + result.stdout)

    def test_check_reussit_sur_le_depot(self) -> None:
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "validation" / "make_tts_fixtures.py"),
             "check"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("valides", result.stdout)


if __name__ == "__main__":
    unittest.main()
