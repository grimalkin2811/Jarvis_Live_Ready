"""Tests de la persistance JSON locale partagée (`src/json_state.py`).

Couvre les contrats sur lesquels reposent les quatre appelants (état du menu,
de l'apparence, du système et des modes) : lecture tolérante, remplacement
atomique, jamais d'exception remontée.
"""

import json
import os
import errno
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import json_state  # noqa: E402
from src.json_state import (  # noqa: E402
    atomic_replace,
    load_dict,
    open_text,
    read_text,
    save_dict,
    write_text_atomic,
)


class LoadDictTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, name: str, content: str) -> str:
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return path

    def test_missing_file_and_empty_path_return_empty_dict(self):
        self.assertEqual(load_dict(os.path.join(self.tmp.name, "absent.json")), {})
        self.assertEqual(load_dict(""), {})
        self.assertEqual(load_dict(None), {})

    def test_corrupt_file_returns_empty_dict(self):
        self.assertEqual(load_dict(self._write("corrupt.json", "{pas du json")), {})

    def test_non_object_root_returns_empty_dict(self):
        self.assertEqual(load_dict(self._write("list.json", "[1, 2, 3]")), {})
        self.assertEqual(load_dict(self._write("str.json", '"texte"')), {})

    def test_directory_instead_of_file_returns_empty_dict(self):
        self.assertEqual(load_dict(self.tmp.name), {})

    def test_valid_object_is_returned(self):
        path = self._write("ok.json", '{"a": 1, "b": "accentué"}')
        self.assertEqual(load_dict(path), {"a": 1, "b": "accentué"})


class SaveDictTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_creates_parent_directory_and_round_trips(self):
        path = os.path.join(self.tmp.name, "sous", "dossier", "state.json")
        self.assertTrue(save_dict(path, {"volume": 42, "voix": "Féminine"}))
        with open(path, "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {"volume": 42, "voix": "Féminine"})
        self.assertEqual(load_dict(path), {"volume": 42, "voix": "Féminine"})

    def test_no_temporary_file_left_behind(self):
        path = os.path.join(self.tmp.name, "state.json")
        self.assertTrue(save_dict(path, {"a": 1}))
        self.assertFalse(os.path.exists(path + ".tmp"))

    def test_existing_content_is_replaced_not_merged(self):
        path = os.path.join(self.tmp.name, "state.json")
        save_dict(path, {"a": 1, "b": 2})
        save_dict(path, {"a": 1})
        self.assertEqual(load_dict(path), {"a": 1})

    def test_unwritable_target_returns_false_without_raising(self):
        # Un dossier à la place du fichier : l'écriture doit échouer proprement.
        self.assertFalse(save_dict(self.tmp.name, {"a": 1}))
        self.assertFalse(save_dict("", {"a": 1}))

    def test_non_serialisable_payload_returns_false_without_raising(self):
        path = os.path.join(self.tmp.name, "state.json")
        self.assertFalse(save_dict(path, {"objet": object()}))


class SharedReadTests(unittest.TestCase):
    """Le helper de lecture partagé doit se comporter comme ``open`` standard."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "state.json")

    def test_read_text_round_trips_unicode(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write('{"voix": "Féminine — OK"}')
        self.assertEqual(json.loads(read_text(self.path)), {"voix": "Féminine — OK"})

    def test_read_text_missing_file_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            read_text(os.path.join(self.tmp.name, "absent.json"))

    def test_read_text_invalid_utf8_raises_unicode_decode_error(self):
        with open(self.path, "wb") as handle:
            handle.write(b'{"a": "\xff\xfe"}')
        with self.assertRaises(UnicodeDecodeError):
            read_text(self.path)

    def test_open_text_handle_does_not_block_atomic_replace(self):
        """Cœur du correctif Windows : un lecteur ouvert DOIT laisser écrire.

        Sous Windows, ``open_text`` partage ``FILE_SHARE_DELETE`` : la
        réécriture atomique pendant que le lecteur est ouvert doit réussir,
        le lecteur continue de voir l'ancien contenu et le chemin n'est
        jamais absent (sondage ``_probe12``).
        """
        self.assertTrue(save_dict(self.path, {"v": 0}))
        with open_text(self.path) as handle:
            first = handle.read()
            # Le remplacement a lieu pendant que le handle est ouvert.
            self.assertTrue(save_dict(self.path, {"v": 1}))
            self.assertIn('"v": 0', first)
            handle.seek(0)
            # L'ancien lecteur voit toujours l'ancien contenu (snapshot).
            self.assertIn('"v": 0', handle.read())
            # Et le chemin a bien été remplacé (jamais absent).
            self.assertEqual(load_dict(self.path), {"v": 1})

    def test_held_reader_never_observes_missing_file_during_writes(self):
        """Pendant 100 réécritures, le chemin existe à chaque observation."""
        self.assertTrue(save_dict(self.path, {"v": 0}))
        gaps: list[str] = []
        stop = threading.Event()
        observations = [0]

        def watcher():
            while not stop.is_set():
                try:
                    with open_text(self.path) as handle:
                        payload = json.load(handle)
                    if "v" not in payload:
                        gaps.append("payload incomplet")
                    observations[0] += 1
                except FileNotFoundError:
                    gaps.append("fichier absent")
                except (OSError, ValueError) as exc:
                    gaps.append(f"{type(exc).__name__}: {exc}")
                time.sleep(0.001)

        thread = threading.Thread(target=watcher)
        thread.start()
        try:
            for i in range(100):
                self.assertTrue(save_dict(self.path, {"v": i + 1}))
        finally:
            stop.set()
            thread.join(timeout=10)
        self.assertGreater(observations[0], 0)
        self.assertEqual(gaps, [])


class AtomicReplaceTests(unittest.TestCase):
    """Contrats de ``atomic_replace`` : atomicité, préservation, garde-fous."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dest = os.path.join(self.tmp.name, "state.json")
        self.src = os.path.join(self.tmp.name, "state.json.next")

    def _write(self, path, content):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)

    def _patch_replace(self, side_effect):
        """Couvre l'implémentation réelle (POSIX rename ou os.replace)."""
        if json_state.IS_WINDOWS:
            return mock.patch.object(json_state, "_windows_posix_replace",
                                     side_effect=side_effect)
        return mock.patch("os.replace", side_effect=side_effect)

    def test_first_save_when_destination_missing(self):
        self._write(self.src, '{"v": 1}')
        atomic_replace(self.src, self.dest)
        with open(self.dest, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), '{"v": 1}')
        self.assertFalse(os.path.exists(self.src))

    def test_replaces_existing_destination(self):
        self._write(self.dest, '{"v": 0}')
        self._write(self.src, '{"v": 1}')
        atomic_replace(self.src, self.dest)
        with open(self.dest, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), '{"v": 1}')

    def test_destination_directory_rejected_source_preserved(self):
        """Une destination-répertoire doit être refusée AVANT tout essai.

        Le renommage POSIX Windows signalerait un succès en détruisant la
        source (sondage ``_probe15`` Q2) : la garde lève sans y toucher.
        """
        directory = os.path.join(self.tmp.name, "repertoire")
        os.mkdir(directory)
        self._write(self.src, '{"v": 1}')
        with self.assertRaises(OSError):
            atomic_replace(self.src, directory)
        self.assertTrue(os.path.isdir(directory))
        self.assertTrue(os.path.exists(self.src), "la source doit être préservée")

    def test_failed_replace_preserves_destination(self):
        """Échec du remplacement → destination inchangée, source intacte."""
        self._write(self.dest, '{"v": 0}')
        self._write(self.src, '{"v": 1}')
        failure = OSError(errno.EACCES, "accès refusé")  # permanent, non réessayé
        with self._patch_replace(side_effect=failure):
            with self.assertRaises(OSError):
                atomic_replace(self.src, self.dest)
        with open(self.dest, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), '{"v": 0}')
        self.assertTrue(os.path.exists(self.src))

    def test_transient_error_is_retried_then_succeeds(self):
        """Seules les erreurs transitoires sont réessayées (backoff borné)."""
        self._write(self.dest, '{"v": 0}')
        self._write(self.src, '{"v": 1}')
        transient = OSError(errno.EBUSY, "occupé")
        if json_state.IS_WINDOWS:
            transient.winerror = 32  # ERROR_SHARING_VIOLATION
        calls = {"n": 0}

        def flaky(*args):
            calls["n"] += 1
            if calls["n"] == 1:
                raise transient
            # Deuxième essai : on délègue à l'implémentation réelle.
            if json_state.IS_WINDOWS:
                return json_state.__dict__["_windows_posix_replace_real"](*args)
            return json_state.__dict__["os_replace_real"](*args)

        if json_state.IS_WINDOWS:
            json_state.__dict__["_windows_posix_replace_real"] = \
                json_state._windows_posix_replace
            patcher = mock.patch.object(json_state, "_windows_posix_replace",
                                        side_effect=flaky)
        else:
            json_state.__dict__["os_replace_real"] = os.replace
            patcher = mock.patch("os.replace", side_effect=flaky)
        with patcher:
            atomic_replace(self.src, self.dest)
        self.assertGreaterEqual(calls["n"], 2, "la 1re erreur devait être réessayée")
        with open(self.dest, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), '{"v": 1}')

    def test_permanent_error_is_not_retried(self):
        """Une erreur non transitoire remonte immédiatement (1 seul essai)."""
        self._write(self.dest, '{"v": 0}')
        self._write(self.src, '{"v": 1}')
        permanent = OSError(errno.EACCES, "accès refusé")
        calls = {"n": 0}

        def failing(*args):
            calls["n"] += 1
            raise permanent

        with self._patch_replace(side_effect=failing):
            with self.assertRaises(OSError):
                atomic_replace(self.src, self.dest)
        self.assertEqual(calls["n"], 1, "aucune répétition pour une erreur permanente")
        with open(self.dest, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), '{"v": 0}')

    def test_retry_is_bounded(self):
        """Nombre d'essais borné : aucun blocage infini sur un verrou."""
        self._write(self.dest, '{"v": 0}')
        self._write(self.src, '{"v": 1}')
        transient = OSError(errno.EBUSY, "occupé")
        if json_state.IS_WINDOWS:
            transient.winerror = 33  # ERROR_LOCK_VIOLATION
        calls = {"n": 0}

        def always_locked(*args):
            calls["n"] += 1
            raise transient

        started = time.monotonic()
        with self._patch_replace(side_effect=always_locked):
            with self.assertRaises(OSError):
                atomic_replace(self.src, self.dest)
        elapsed = time.monotonic() - started
        self.assertEqual(calls["n"], 1 + len(json_state._REPLACE_BACKOFF))
        self.assertLess(elapsed, 5.0, "retries bornés, pas de blocage indéfini")
        with open(self.dest, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), '{"v": 0}')


class WriteTextAtomicTests(unittest.TestCase):
    """``write_text_atomic`` : temporaire unique, nettoyage garanti."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "state.json")

    def _temps(self):
        return [n for n in os.listdir(self.tmp.name) if n.endswith(".tmp")]

    def test_success_leaves_no_temporary_file(self):
        write_text_atomic(self.path, '{"a": 1}\n')
        self.assertEqual(self._temps(), [])

    def test_creates_missing_parent_directory(self):
        nested = os.path.join(self.tmp.name, "sous", "dossier", "state.json")
        write_text_atomic(nested, '{"a": 1}')
        self.assertTrue(os.path.exists(nested))

    def test_failure_cleans_temporary_and_preserves_destination(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write('{"v": 0}')
        failure = OSError(errno.EACCES, "accès refusé")
        with mock.patch.object(json_state, "atomic_replace", side_effect=failure):
            with self.assertRaises(OSError):
                write_text_atomic(self.path, '{"v": 1}\n')
        with open(self.path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), '{"v": 0}')
        self.assertEqual(self._temps(), [], "le temporaire doit être nettoyé")

    def test_failure_without_destination_leaves_no_temporary(self):
        failure = OSError(errno.EACCES, "accès refusé")
        with mock.patch.object(json_state, "atomic_replace", side_effect=failure):
            with self.assertRaises(OSError):
                write_text_atomic(self.path, '{"v": 1}\n')
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(self._temps(), [])


class ConcurrentSaveLoadTests(unittest.TestCase):
    """Écritures et lectures simultanées : jamais de trou dans le fichier."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "state.json")

    def test_concurrent_saves_never_leave_a_gap_for_readers(self):
        self.assertTrue(save_dict(self.path, {"seq": 0}))
        stop = threading.Event()
        gaps: list[str] = []
        observations = [0]
        writer_failures: list[int] = []

        def reader():
            while not stop.is_set():
                payload = load_dict(self.path)
                observations[0] += 1
                if "seq" not in payload:
                    gaps.append("payload incomplet ou fichier absent")
                time.sleep(0.001)

        thread = threading.Thread(target=reader)
        thread.start()
        try:
            for i in range(60):
                if not save_dict(self.path, {"seq": i + 1}):
                    writer_failures.append(i)
        finally:
            stop.set()
            thread.join(timeout=10)

        self.assertEqual(writer_failures, [], "aucune écriture ne doit échouer")
        self.assertEqual(gaps, [], "le lecteur ne voit jamais le fichier absent/tronqué")
        self.assertGreater(observations[0], 0)
        self.assertEqual(load_dict(self.path), {"seq": 60})

    def test_concurrent_writers_to_same_file_all_succeed(self):
        """Deux écriveurs simultanés : temporaires uniques, aucun écrasement."""
        self.assertTrue(save_dict(self.path, {"w": 0}))
        failures: list[str] = []
        barrier = threading.Barrier(2)

        def writer(tag: str):
            barrier.wait()
            for i in range(25):
                if not save_dict(self.path, {"w": f"{tag}-{i}"}):
                    failures.append(f"{tag}-{i}")

        threads = [threading.Thread(target=writer, args=(t,)) for t in "ab"]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(failures, [])
        payload = load_dict(self.path)
        self.assertIn("w", payload, "le fichier reste un JSON valide à la fin")


class TransientClassificationTests(unittest.TestCase):
    """``_is_transient`` : réessayer seulement ce qui peut changer d'avis."""

    def test_windows_lock_errors_are_transient(self):
        # ACCESS_DENIED(5), SHARING_VIOLATION(32), LOCK_VIOLATION(33)
        for code in (5, 32, 33):
            exc = OSError(errno.EACCES, "verrou")
            exc.winerror = code
            self.assertTrue(json_state._is_transient(exc), f"winerror {code}")

    def test_windows_permanent_errors_are_not_transient(self):
        exc = OSError(errno.ENOENT, "absent")
        exc.winerror = 2  # ERROR_FILE_NOT_FOUND
        self.assertFalse(json_state._is_transient(exc))

    def test_posix_busy_errors_are_transient(self):
        self.assertTrue(json_state._is_transient(OSError(errno.EBUSY, "occupé")))
        self.assertTrue(json_state._is_transient(OSError(errno.ETXTBSY, "occupé")))

    def test_posix_permission_error_is_not_transient(self):
        self.assertFalse(json_state._is_transient(OSError(errno.EACCES, "refusé")))


class SaveDictFailureContractTests(unittest.TestCase):
    """``save_dict`` : ni exception, ni destination perdue, ni temporaire."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "state.json")

    def test_failed_save_returns_false_and_preserves_old_state(self):
        self.assertTrue(save_dict(self.path, {"v": 0}))
        failure = OSError(errno.EACCES, "accès refusé")
        with mock.patch.object(json_state, "atomic_replace", side_effect=failure):
            self.assertFalse(save_dict(self.path, {"v": 1}))
        self.assertEqual(load_dict(self.path), {"v": 0})
        leftovers = [n for n in os.listdir(self.tmp.name) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_save_over_directory_still_returns_false(self):
        directory = os.path.join(self.tmp.name, "dossier")
        os.mkdir(directory)
        self.assertFalse(save_dict(directory, {"a": 1}))
        self.assertTrue(os.path.isdir(directory))


if __name__ == "__main__":
    unittest.main()
