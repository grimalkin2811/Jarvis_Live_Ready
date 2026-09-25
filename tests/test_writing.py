"""Tests du Writing System (Jarvis 1.4.0).

Aucune frappe réelle, aucun presse-papiers système : l'insertion est simulée.
Les fichiers sont créés dans un dossier temporaire, jamais dans le dépôt.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import paths, tools  # noqa: E402
from src.version import get_version  # noqa: E402
from src.writing import (  # noqa: E402
    SPOKEN_FILE_CREATED,
    SPOKEN_FILE_FAILED,
    SPOKEN_WRITE_FAILED,
    SPOKEN_WRITTEN,
    classify_writing_intent,
    create_text_file,
    derive_filename,
    handle_command,
    sanitize_filename,
    system_instruction,
    write_to_active_field,
)
from src.writing.active_field import (  # noqa: E402
    VK_CONTROL,
    VK_RETURN,
    ActiveFieldInserter,
    ExternalFocus,
    MemoryClipboard,
    RecordingKeyboard,
    apply_at_cursor,
    encode_ctrl_v,
    encode_plan,
    events_to_text,
    insert_via_clipboard,
    plan_insertion,
)
from src.writing.filenames import (  # noqa: E402
    explicit_filename_is_invalid,
    generate_unique_filename,
)
from src.writing.service import set_active_field_writer  # noqa: E402
from src.writing.text import finalize_text  # noqa: E402
from UI import menu_state  # noqa: E402
from src.writing.settings import (  # noqa: E402
    ACTIVE_FIELD_DESCRIPTION,
    TEXT_FILES_DESCRIPTION,
)


SECRET = "XYLOPHONE_SECRET_7741"


def _writer(fail=False, partial=0, clipboard=None, focus=None, platform="nt", limit=4000):
    keyboard = RecordingKeyboard(fail=fail, partial=partial)
    return ActiveFieldInserter(
        platform=platform,
        keyboard=keyboard,
        clipboard=clipboard,
        focus=focus or ExternalFocus(),
        sleep=lambda _seconds: None,
        unicode_limit=limit,
    ), keyboard


class IntentTests(unittest.TestCase):
    def test_spec_examples(self):
        cases = {
            "Explique-moi ce qu'est un trou noir.": "conversation",
            "Qu'est-ce que tu écrirais dans une lettre de motivation ?": "conversation",
            "Comment rédiger une lettre de motivation ?": "conversation",
            "Que mettrais-tu dans un mail au professeur ?": "conversation",
            "Jarvis, écris-moi un mail pour demander un rendez-vous au professeur.": "write_active_field",
            "Écris-moi un mail à mon professeur pour lui demander un rendez-vous.": "write_active_field",
            "Rédige une lettre de motivation pour un stage.": "write_active_field",
            "Compose un message pour Marie.": "write_active_field",
            "Crée-moi un fichier texte avec une présentation de mon projet.": "create_text_file",
            "Jarvis, crée-moi un texte de présentation de mon projet.": "create_text_file",
            "Sauvegarde un texte sur les trous noirs.": "create_text_file",
            "Enregistre ça dans un fichier.": "create_text_file",
        }
        for utterance, expected in cases.items():
            with self.subTest(utterance=utterance):
                intent = classify_writing_intent(utterance)
                self.assertEqual(intent.action, expected, intent.reason)

    def test_hypothetical_is_explicit_non_action(self):
        intent = classify_writing_intent("Qu'est-ce que tu écrirais dans une lettre de motivation ?")
        self.assertTrue(intent.explicit_non_action)
        self.assertFalse(intent.is_action)

    def test_negation_and_narration_are_not_actions(self):
        self.assertEqual(classify_writing_intent("N'écris pas de mail.").action, "conversation")
        self.assertTrue(classify_writing_intent("Ne m'écris rien dans le champ.").explicit_non_action)
        self.assertEqual(classify_writing_intent("Je t'écris pour te dire bonjour.").action, "conversation")
        self.assertEqual(classify_writing_intent("J'ai écrit un mail hier.").action, "conversation")

    def test_polite_request_is_an_action(self):
        intent = classify_writing_intent("Est-ce que tu peux m'écrire un mail au professeur ?")
        self.assertEqual(intent.action, "write_active_field")
        self.assertFalse(intent.explicit_non_action)

    def test_conditional_polite_stays_conversation(self):
        intent = classify_writing_intent("Est-ce que tu écrirais un mail au professeur ?")
        self.assertEqual(intent.action, "conversation")
        self.assertTrue(intent.explicit_non_action)

    def test_both_modes_when_explicitly_combined(self):
        intent = classify_writing_intent(
            "Écris le mail dans le champ et crée aussi un fichier."
        )
        self.assertEqual(intent.action, "both")

    def test_file_object_wins_over_write_verb(self):
        intent = classify_writing_intent("Rédige un fichier texte avec une présentation.")
        self.assertEqual(intent.action, "create_text_file")

    def test_bare_ecris_is_not_an_action(self):
        intent = classify_writing_intent("écris")
        self.assertEqual(intent.action, "conversation")
        self.assertFalse(intent.explicit_non_action)


class FilenameTests(unittest.TestCase):
    def test_spec_names(self):
        self.assertEqual(derive_filename("écris un texte sur les trous noirs"), "trous_noirs.txt")
        self.assertEqual(derive_filename("crée mon mail au professeur"), "mail_professeur.txt")
        self.assertEqual(
            derive_filename("fais-moi une présentation de Jarvis"),
            "presentation_jarvis.txt",
        )
        self.assertEqual(
            derive_filename("Jarvis, crée-moi un texte de présentation de mon projet."),
            "presentation_projet.txt",
        )

    def test_explicit_name(self):
        self.assertEqual(derive_filename("appelle-le brouillon du cours"), "brouillon.txt")
        self.assertEqual(derive_filename("nomme-le rapport final sur les ventes"), "rapport_final.txt")

    def test_fallback_document(self):
        self.assertEqual(derive_filename("crée-moi un texte"), "document.txt")
        self.assertEqual(derive_filename(""), "document.txt")

    def test_forbidden_characters_and_path(self):
        self.assertEqual(sanitize_filename(r"..\..\secret.txt"), "secret.txt")
        self.assertEqual(sanitize_filename("rapport:final*.txt"), "rapport_final.txt")
        self.assertEqual(sanitize_filename('note?"<>|.txt'), "note.txt")
        self.assertNotIn("\\", sanitize_filename("a/b\\c"))
        self.assertTrue(sanitize_filename("Présentation été").endswith(".txt"))
        self.assertNotIn("é", sanitize_filename("Présentation été"))

    def test_reserved_windows_name(self):
        name = sanitize_filename("CON")
        self.assertNotEqual(name.lower(), "con.txt")
        self.assertTrue(name.endswith(".txt"))

    def test_invalid_explicit_name(self):
        self.assertTrue(explicit_filename_is_invalid("???"))
        self.assertTrue(explicit_filename_is_invalid("***"))
        self.assertFalse(explicit_filename_is_invalid("rapport?.txt"))
        self.assertFalse(explicit_filename_is_invalid(""))

    def test_collision_suffixes(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "document.txt").write_text("ancien", encoding="utf-8")
            (directory / "document_1.txt").write_text("ancien", encoding="utf-8")
            self.assertEqual(generate_unique_filename(directory, "document.txt"), "document_2.txt")
            self.assertEqual(generate_unique_filename(directory, "libre"), "libre.txt")


class TextFinalizeTests(unittest.TestCase):
    def test_keeps_accents_and_paragraphs(self):
        raw = "Élève à l'école.\n\nÇa va ?"
        self.assertEqual(finalize_text(raw), raw)

    def test_strips_fence_and_preamble(self):
        self.assertEqual(finalize_text("```\nBonjour Madame,\n```"), "Bonjour Madame,")
        self.assertEqual(
            finalize_text("Voici le texte :\n\nBonjour Madame,"),
            "Bonjour Madame,",
        )

    def test_drops_destructive_controls(self):
        self.assertEqual(finalize_text("a\x08b\x7fc"), "abc")

    def test_empty(self):
        self.assertEqual(finalize_text("   "), "")
        self.assertEqual(finalize_text(None), "")


class ActiveFieldPlanTests(unittest.TestCase):
    def test_simple_accents_paragraphs_and_long_text(self):
        samples = [
            "Bonjour.",
            "Élève à l'école, ça va — déjà.",
            "Bonjour Monsieur,\n\nJe souhaiterais un rendez-vous.\n\nCordialement,\nAlice",
            ("Paragraphe accentué élève. " * 200).strip(),
        ]
        for sample in samples:
            with self.subTest(length=len(sample)):
                events = plan_insertion(sample)
                self.assertEqual(events_to_text(events), sample.replace("\r\n", "\n"))
                self.assertTrue(all(event.kind in {"unicode", "key"} for event in events))
                self.assertFalse(any(event.value in {"backspace", "delete", "select_all"} for event in events))

    def test_crlf_is_one_enter(self):
        events = plan_insertion("un\r\ndeux")
        self.assertEqual(events_to_text(events), "un\ndeux")
        self.assertEqual(sum(1 for event in events if event.value == "enter"), 1)

    def test_insertion_does_not_replace_existing_text(self):
        existing = "Bonjour le monde"
        events = plan_insertion("à toi, ")
        self.assertEqual(apply_at_cursor(existing, 0, events), "à toi, Bonjour le monde")
        self.assertEqual(apply_at_cursor(existing, 8, events), "Bonjour à toi, le monde")
        self.assertEqual(apply_at_cursor(existing, len(existing), events), "Bonjour le mondeà toi, ")

    def test_backspace_in_source_is_not_a_key(self):
        events = plan_insertion("a\x08b")
        self.assertEqual(events_to_text(events), "ab")
        self.assertFalse(any(event.value == "backspace" for event in events))

    def test_windows_encoding_uses_enter_and_unicode_accents(self):
        units = encode_plan(plan_insertion("é\n"))
        self.assertEqual(units[0].kind, "unicode")
        self.assertEqual(units[0].value, ord("é"))
        self.assertEqual(units[-2].kind, "vk_down")
        self.assertEqual(units[-2].value, VK_RETURN)
        self.assertNotIn(VK_CONTROL, [unit.value for unit in units])

    def test_emoji_is_a_surrogate_pair(self):
        units = [unit for unit in encode_plan(plan_insertion("😀")) if unit.kind == "unicode"]
        self.assertEqual([unit.value for unit in units], [0xD83D, 0xDE00])

    def test_ctrl_v_is_pressed_and_released(self):
        units = encode_ctrl_v()
        self.assertEqual(units[0].value, VK_CONTROL)
        self.assertEqual(units[0].kind, "vk_down")
        self.assertEqual(units[-1].value, VK_CONTROL)
        self.assertEqual(units[-1].kind, "vk_up")


class ClipboardTests(unittest.TestCase):
    def test_paste_restores_previous_clipboard(self):
        clipboard = MemoryClipboard("COPIE UTILISATEUR")
        seen = []

        def send_paste():
            seen.append(clipboard.value)

        result = insert_via_clipboard("Élève\n\nça va", clipboard, send_paste, sleep=lambda _s: None)
        self.assertTrue(result["success"])
        self.assertEqual(clipboard.value, "COPIE UTILISATEUR")
        self.assertTrue(result["clipboard_restored"])
        self.assertIn("Élève", seen[0])
        self.assertIn("\r\n\r\n", seen[0])

    def test_restore_even_if_paste_fails(self):
        clipboard = MemoryClipboard("secret")

        def send_paste():
            raise OSError("fenêtre refusée")

        result = insert_via_clipboard("bonjour", clipboard, send_paste, sleep=lambda _s: None)
        self.assertFalse(result["success"])
        self.assertEqual(clipboard.value, "secret")

    def test_capture_failure_does_not_touch_clipboard(self):
        clipboard = MemoryClipboard("secret")
        clipboard.fail_capture = True
        result = insert_via_clipboard("bonjour", clipboard, lambda: None, sleep=lambda _s: None)
        self.assertFalse(result["success"])
        self.assertEqual(clipboard.value, "secret")


class InserterTests(unittest.TestCase):
    def test_unicode_path_does_not_touch_clipboard(self):
        clipboard = MemoryClipboard("secret")
        inserter, keyboard = _writer(clipboard=clipboard)
        result = inserter.write("Élève à l'école.\n\nSuite.")
        self.assertTrue(result["success"])
        self.assertEqual(result["method"], "unicode")
        self.assertFalse(result["clipboard_used"])
        self.assertEqual(clipboard.value, "secret")
        self.assertEqual(keyboard.pastes, 0)
        self.assertEqual(events_to_text(plan_insertion("Élève à l'école.\n\nSuite.")), "Élève à l'école.\n\nSuite.")

    def test_long_text_uses_clipboard_and_restores_it(self):
        clipboard = MemoryClipboard("secret")
        text = "Accent été. " * 80
        inserter, keyboard = _writer(clipboard=clipboard, limit=100)
        result = inserter.write(text)
        self.assertTrue(result["success"])
        self.assertEqual(result["method"], "clipboard")
        self.assertEqual(keyboard.pastes, 1)
        self.assertEqual(clipboard.value, "secret")

    def test_unicode_failure_falls_back_to_clipboard(self):
        clipboard = MemoryClipboard("secret")
        inserter, keyboard = _writer(fail=True, clipboard=clipboard)
        result = inserter.write("Bonjour élève")
        self.assertTrue(result["success"])
        self.assertEqual(result["method"], "clipboard")
        self.assertEqual(clipboard.value, "secret")
        self.assertEqual(keyboard.pastes, 1)

    def test_partial_insertion_does_not_paste_the_whole_text(self):
        clipboard = MemoryClipboard("secret")
        inserter, keyboard = _writer(partial=4, clipboard=clipboard)
        result = inserter.write("Bonjour")
        self.assertFalse(result["success"])
        self.assertTrue(result.get("partial"))
        self.assertEqual(keyboard.pastes, 0)
        self.assertEqual(clipboard.value, "secret")

    def test_missing_foreground_and_own_process(self):
        inserter, keyboard = _writer(focus=ExternalFocus(foreground=False))
        result = inserter.write("Bonjour")
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "inaccessible")
        self.assertEqual(keyboard.units, [])

        inserter, keyboard = _writer(focus=ExternalFocus(own=True))
        result = inserter.write("Bonjour")
        self.assertEqual(result["reason"], "own_process")
        self.assertEqual(keyboard.units, [])

    def test_non_windows_does_not_type(self):
        inserter, keyboard = _writer(platform="posix")
        result = inserter.write("Bonjour")
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "unsupported_platform")
        self.assertEqual(keyboard.units, [])


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        set_active_field_writer(None)

    def test_active_field_success_does_not_echo_text(self):
        inserter, _keyboard = _writer()
        text = f"Bonjour {SECRET}, élève à l'école.\n\nCordialement."
        result = write_to_active_field(text, writer=inserter, enabled=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["message"], SPOKEN_WRITTEN)
        blob = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(SECRET, blob)
        self.assertNotIn("élève", blob)

    def test_hypothetical_does_not_write_or_create(self):
        inserter, keyboard = _writer()
        question = "Qu'est-ce que tu écrirais dans une lettre de motivation ?"
        result = write_to_active_field("Une lettre.", request=question, writer=inserter, enabled=True)
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "not_explicit")
        self.assertEqual(keyboard.units, [])
        created = create_text_file(
            "Une lettre.",
            request=question,
            directory=self.directory,
            enabled=True,
        )
        self.assertFalse(created["success"])
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_empty_text(self):
        inserter, keyboard = _writer()
        result = write_to_active_field("   ", writer=inserter, enabled=True)
        self.assertFalse(result["success"])
        self.assertEqual(result["message"], SPOKEN_WRITE_FAILED)
        self.assertEqual(keyboard.units, [])
        created = create_text_file("", directory=self.directory, enabled=True)
        self.assertFalse(created["success"])
        self.assertEqual(created["message"], SPOKEN_FILE_FAILED)

    def test_disabled_modes_are_independent(self):
        inserter, keyboard = _writer()
        text = "Bonjour élève."
        blocked_field = write_to_active_field(text, writer=inserter, enabled=False)
        self.assertFalse(blocked_field["success"])
        self.assertEqual(blocked_field["reason"], "disabled")
        self.assertEqual(keyboard.units, [])
        allowed_file = create_text_file(text, filename="note", directory=self.directory, enabled=True)
        self.assertTrue(allowed_file["success"])
        self.assertTrue((self.directory / "note.txt").is_file())

        blocked_file = create_text_file(text, filename="autre", directory=self.directory, enabled=False)
        self.assertFalse(blocked_file["success"])
        self.assertEqual(blocked_file["reason"], "disabled")
        self.assertFalse((self.directory / "autre.txt").exists())
        allowed_field = write_to_active_field(text, writer=inserter, enabled=True)
        self.assertTrue(allowed_field["success"])
        self.assertEqual(allowed_field["message"], SPOKEN_WRITTEN)

    def test_file_creation_naming_collision_and_directory(self):
        missing = self.directory / "user_content"
        self.assertFalse(missing.exists())
        first = create_text_file(
            "Élève à l'école.\n\nDeuxième paragraphe.",
            request="crée-moi un texte de présentation de mon projet.",
            directory=missing,
            enabled=True,
        )
        self.assertTrue(first["success"])
        self.assertEqual(first["message"], SPOKEN_FILE_CREATED)
        self.assertEqual(first["fichier"], "presentation_projet.txt")
        self.assertTrue(missing.is_dir())
        self.assertEqual(
            (missing / "presentation_projet.txt").read_text(encoding="utf-8"),
            "Élève à l'école.\n\nDeuxième paragraphe.",
        )
        self.assertNotIn(SECRET, json.dumps(first))

        names = []
        for _ in range(3):
            created = create_text_file("suite", filename="document", directory=missing, enabled=True)
            self.assertTrue(created["success"])
            names.append(created["fichier"])
        self.assertEqual(names, ["document.txt", "document_1.txt", "document_2.txt"])
        self.assertEqual((missing / "document.txt").read_text(encoding="utf-8"), "suite")

    def test_custom_filename_forbidden_chars_and_traversal(self):
        created = create_text_file("ok", filename="brouillon", directory=self.directory, enabled=True)
        self.assertEqual(created["fichier"], "brouillon.txt")
        dirty = create_text_file("ok", filename="rapport:final*", directory=self.directory, enabled=True)
        self.assertEqual(dirty["fichier"], "rapport_final.txt")
        outside = self.directory.parent / "outside.txt"
        traversed = create_text_file(
            "ok",
            filename="../../outside.txt",
            directory=self.directory,
            enabled=True,
        )
        self.assertTrue(traversed["success"])
        self.assertTrue(str(Path(traversed["chemin"]).resolve()).startswith(str(self.directory.resolve())))
        self.assertFalse(outside.exists())

    def test_invalid_filename_and_permission(self):
        invalid = create_text_file("ok", filename="???", directory=self.directory, enabled=True)
        self.assertFalse(invalid["success"])
        self.assertEqual(invalid["reason"], "invalid_filename")
        self.assertEqual(invalid["message"], SPOKEN_FILE_FAILED)
        self.assertEqual(list(self.directory.iterdir()), [])

        with patch("src.writing.files.Path.mkdir", side_effect=PermissionError("denied")):
            denied = create_text_file("ok", filename="note", directory=self.directory, enabled=True)
        self.assertFalse(denied["success"])
        self.assertEqual(denied["reason"], "permission")

    def test_existing_file_is_not_overwritten(self):
        target = self.directory / "document.txt"
        target.write_text("original", encoding="utf-8")
        created = create_text_file("nouveau", filename="document", directory=self.directory, enabled=True)
        self.assertEqual(created["fichier"], "document_1.txt")
        self.assertEqual(target.read_text(encoding="utf-8"), "original")

    def test_handle_command_routes_spec_phrases(self):
        inserter, keyboard = _writer()
        conversation = handle_command(
            "Explique-moi ce qu'est un trou noir.",
            "Un trou noir est...",
            writer=inserter,
            directory=self.directory,
        )
        self.assertFalse(conversation["handled"])
        self.assertEqual(keyboard.units, [])

        question = handle_command(
            "Qu'est-ce que tu écrirais dans une lettre de motivation ?",
            "Madame, Monsieur,",
            writer=inserter,
            directory=self.directory,
        )
        self.assertFalse(question["handled"])
        self.assertEqual(list(self.directory.iterdir()), [])

        written = handle_command(
            "Écris-moi un mail à mon professeur pour lui demander un rendez-vous.",
            f"Bonjour {SECRET}",
            writer=inserter,
            directory=self.directory,
            field_enabled=True,
        )
        self.assertTrue(written["handled"])
        self.assertEqual(written["message"], SPOKEN_WRITTEN)
        self.assertNotIn(SECRET, json.dumps(written))

        created = handle_command(
            "Crée-moi un fichier texte avec une présentation de mon projet.",
            "Voici le projet.",
            directory=self.directory,
            files_enabled=True,
        )
        self.assertTrue(created["success"])
        self.assertEqual(created["message"], SPOKEN_FILE_CREATED)
        self.assertTrue(any(path.suffix == ".txt" for path in self.directory.iterdir()))

    def test_prompt_forbids_spontaneous_writing(self):
        prompt = system_instruction()
        self.assertIn("write_to_active_field", prompt)
        self.assertIn("create_text_file", prompt)
        self.assertIn("qu'est-ce que tu écrirais", prompt)
        self.assertIn(SPOKEN_WRITTEN, prompt)
        self.assertIn("user_content", prompt)
        self.assertIn("ne lis jamais", prompt)


class SettingsPersistenceTests(unittest.TestCase):
    def setUp(self):
        self._field = menu_state.LIVE.get_writing_active_field()
        self._files = menu_state.LIVE.get_writing_text_files()

    def tearDown(self):
        menu_state.LIVE.set_writing_active_field(self._field)
        menu_state.LIVE.set_writing_text_files(self._files)

    def test_defaults_are_independent_and_on(self):
        state = menu_state.MenuState()
        self.assertTrue(state.writing_active_field)
        self.assertTrue(state.writing_text_files)
        self.assertIn(ACTIVE_FIELD_DESCRIPTION, ACTIVE_FIELD_DESCRIPTION)
        self.assertIn("user_content", TEXT_FILES_DESCRIPTION)

    def test_roundtrip_survives_restart(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.addCleanup(os.remove, path)
        state = menu_state.MenuState(writing_active_field=False, writing_text_files=True)
        menu_state.save_state(state, path)
        # Redémarrage simulé : nouvel objet chargé depuis le disque.
        loaded = menu_state.load_state(path)
        self.assertFalse(loaded.writing_active_field)
        self.assertTrue(loaded.writing_text_files)
        self.assertFalse(menu_state.LIVE.get_writing_active_field())
        self.assertTrue(menu_state.LIVE.get_writing_text_files())

        again = menu_state.load_state(path)
        self.assertFalse(again.writing_active_field)
        self.assertTrue(again.writing_text_files)

    def test_legacy_file_keeps_new_defaults(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.addCleanup(os.remove, path)
        Path(path).write_text(json.dumps({"mic_enabled": False}), encoding="utf-8")
        loaded = menu_state.load_state(path)
        self.assertTrue(loaded.writing_active_field)
        self.assertTrue(loaded.writing_text_files)
        self.assertFalse(loaded.mic_enabled)

    def test_live_flags_block_the_service(self):
        menu_state.LIVE.set_writing_active_field(False)
        menu_state.LIVE.set_writing_text_files(True)
        inserter, keyboard = _writer()
        blocked = write_to_active_field("Bonjour", writer=inserter)
        self.assertEqual(blocked["reason"], "disabled")
        self.assertEqual(keyboard.units, [])
        with tempfile.TemporaryDirectory() as tmp:
            created = create_text_file("Bonjour", filename="ok", directory=tmp)
        self.assertTrue(created["success"])

        menu_state.LIVE.set_writing_active_field(True)
        menu_state.LIVE.set_writing_text_files(False)
        allowed = write_to_active_field("Bonjour", writer=inserter)
        self.assertTrue(allowed["success"])
        with tempfile.TemporaryDirectory() as tmp:
            blocked_file = create_text_file("Bonjour", filename="ok", directory=tmp)
            self.assertEqual(blocked_file["reason"], "disabled")
            self.assertEqual(list(Path(tmp).iterdir()), [])


class PathTests(unittest.TestCase):
    def test_dev_path_is_relative_to_the_repository(self):
        with patch.dict(os.environ, {"JARVIS_USER_CONTENT_DIR": ""}, clear=False):
            os.environ.pop("JARVIS_USER_CONTENT_DIR", None)
            with patch.object(paths, "is_frozen", return_value=False):
                resolved = paths.user_content_dir()
                self.assertEqual(resolved, paths.app_dir() / "user_content")
                # Jamais un chemin d'installation figé, quel que soit le lecteur.
                self.assertNotIn("Jarvis_Live_Ready\\user_content", str(resolved).replace("/", "\\")[3:])

    def test_frozen_path_stays_in_user_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"JARVIS_DATA_DIR": tmp}, clear=False):
                with patch.object(paths, "is_frozen", return_value=True):
                    self.assertEqual(paths.user_content_dir(), Path(tmp).resolve() / "user_content")

    def test_override_and_ensure(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "custom"
            with patch.dict(os.environ, {"JARVIS_USER_CONTENT_DIR": str(target)}, clear=False):
                self.assertEqual(paths.user_content_dir(), target)
                created = paths.ensure_user_content_dir()
                self.assertTrue(created.is_dir())


class ToolIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._writer = None
        set_active_field_writer(None)
        self.addCleanup(set_active_field_writer, None)

    def test_tools_are_declared(self):
        names = {item["name"] for item in tools.TOOL_DECLARATIONS}
        self.assertIn("write_to_active_field", names)
        self.assertIn("create_text_file", names)
        self.assertEqual(names, set(tools.TOOL_FUNCTIONS))
        self.assertEqual(get_version(), "1.4.0")

    def test_active_field_is_forbidden_in_routines(self):
        from src import routines

        self.assertIn("write_to_active_field", routines.FORBIDDEN_TOOLS)
        self.assertNotIn("create_text_file", routines.FORBIDDEN_TOOLS)
        self.assertNotIn("write_to_active_field", routines.available_tools())
        self.assertIn("create_text_file", routines.available_tools())

    def test_tool_wrapper_uses_writer_and_hides_text(self):
        inserter, _keyboard = _writer()
        set_active_field_writer(inserter)
        result = tools.write_to_active_field(f"Secret {SECRET}", request="")
        self.assertTrue(result["success"])
        self.assertEqual(result["message"], SPOKEN_WRITTEN)
        self.assertNotIn(SECRET, json.dumps(result))

    def test_tool_does_not_crash_on_linux_default_writer(self):
        result = tools.write_to_active_field("Bonjour")
        self.assertIsInstance(result, dict)
        self.assertIn("success", result)
        self.assertIn("message", result)

    def test_game_mode_blocks_writing_tools(self):
        from src import modes

        previous = modes._DEFAULT_MANAGER
        with tempfile.TemporaryDirectory() as tmp:
            manager = modes.JarvisModeManager(Path(tmp) / "mode.json")
            modes.set_default_mode_manager(manager)
            self.addCleanup(modes.set_default_mode_manager, previous)
            with patch.object(modes.JarvisModeManager, "_set_jarvis_low_priority", return_value={"success": True}):
                self.assertTrue(manager.activate_game_mode(close_background=False)["success"])
            blocked = tools.TOOL_FUNCTIONS["write_to_active_field"](text="Bonjour")
            self.assertFalse(blocked["success"])
            self.assertTrue(blocked.get("blocked_by_mode"))
            blocked_file = tools.TOOL_FUNCTIONS["create_text_file"](text="Bonjour", filename="x")
            self.assertFalse(blocked_file["success"])
            self.assertTrue(blocked_file.get("blocked_by_mode"))

    def test_prompt_is_wired_into_gemini_live(self):
        from src import gemini_live

        self.assertIn("write_to_active_field", gemini_live.writing_system_instruction())
        self.assertIn("write_to_active_field", gemini_live.WRITING_TOOL_NAMES)


class MenuWritingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            from PySide6.QtWidgets import QApplication
            import UI.jarvis_menu as jm
        except Exception as exc:  # pragma: no cover - environnement sans Qt
            raise unittest.SkipTest(f"Qt indisponible : {exc}")
        cls.jm = jm
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.widget = jm.MorphingOrbWidget()

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "widget", None) is not None:
            cls.widget.close()
            cls.widget.deleteLater()

    def setUp(self):
        self._field = menu_state.LIVE.get_writing_active_field()
        self._files = menu_state.LIVE.get_writing_text_files()
        self.widget.menu_state = menu_state.MenuState()
        self.widget._menu_action_flash = ""
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.widget._menu_state_path = self.path
        self.addCleanup(os.remove, self.path)
        self.addCleanup(menu_state.LIVE.set_writing_active_field, self._field)
        self.addCleanup(menu_state.LIVE.set_writing_text_files, self._files)

    def _voice(self):
        for spec in self.jm.MENU_SPECS:
            if spec.name == "Voice":
                return spec
        raise AssertionError("menu Voice introuvable")

    def _item(self, label):
        for item in self._voice().items:
            if item.label == label:
                return item
        raise AssertionError(label)

    def test_toggles_exist_with_descriptions_and_on_off(self):
        spec = self._voice()
        field = self._item("Active Field")
        files = self._item("Text Files")
        self.assertEqual(field.description, ACTIVE_FIELD_DESCRIPTION)
        self.assertEqual(files.description, TEXT_FILES_DESCRIPTION)
        self.assertEqual(self.widget._voice_value("Active Field"), "On")
        self.assertEqual(self.widget._voice_value("Text Files"), "On")
        self.assertTrue(self.widget._menu_toggle_value(spec, field))

    def test_toggle_persists_and_flashes_description(self):
        spec = self._voice()
        field = self._item("Active Field")
        self.widget.time = 10.0
        self.widget._menu_state_last_save = -10.0
        self.widget._menu_set_toggle(spec, field, True)
        self.assertIn(ACTIVE_FIELD_DESCRIPTION, self.widget._menu_action_flash)
        self.assertIn("On", self.widget._menu_action_flash)
        self.widget.time = 11.0
        self.widget._menu_set_toggle(spec, field, False)
        self.assertIn("Off", self.widget._menu_action_flash)
        self.assertFalse(self.widget.menu_state.writing_active_field)
        self.assertFalse(menu_state.LIVE.get_writing_active_field())
        saved = json.loads(Path(self.path).read_text(encoding="utf-8"))
        self.assertFalse(saved["writing_active_field"])
        self.assertTrue(saved["writing_text_files"])

        files = self._item("Text Files")
        self.widget.time = 12.0
        self.widget._menu_set_toggle(spec, files, False)
        self.assertIn("Off", self.widget._menu_action_flash)
        self.widget.time = 13.0
        self.widget._menu_set_toggle(spec, files, True)
        self.assertIn(TEXT_FILES_DESCRIPTION, self.widget._menu_action_flash)
        self.assertIn("user_content", self.widget._menu_action_flash)


if __name__ == "__main__":
    unittest.main()
