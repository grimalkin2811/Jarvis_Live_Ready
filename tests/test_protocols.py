"""Tests du moteur de protocoles et des déclencheurs cachés.

Aucun de ces tests n'a besoin de Qt, de micro ni de clé API : le moteur est
volontairement découplé de l'interface.
"""

import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import _ci_diag  # noqa: E402,F401  (diagnostic CI temporaire)

from src import protocols  # noqa: E402
from src.secret_codes import (  # noqa: E402
    SecretCodeDetector,
    SecretGestureDetector,
)


class ProtocolCatalogTests(unittest.TestCase):
    def test_catalogue_non_vide_et_coherent(self):
        catalogue = protocols.list_protocols()
        self.assertTrue(catalogue)
        ids = {item["id"] for item in catalogue}
        self.assertIn("wake_up", ids)
        for item in catalogue:
            self.assertGreater(item["etapes"], 0)
            self.assertGreater(item["duree_estimee_s"], 0)

    def test_recherche_tolerante(self):
        for name in ("wake_up", "wake up", "réveil", "reveil", "WAKE-UP",
                     "jarvis wake up"):
            self.assertIsNotNone(protocols.find_protocol(name), name)
        self.assertIsNone(protocols.find_protocol("protocole inexistant"))
        self.assertIsNone(protocols.find_protocol(""))

    def test_recherche_dans_une_phrase(self):
        found = protocols.find_protocol("lance le protocole diagnostic maintenant")
        self.assertIsNotNone(found)
        self.assertEqual(found.protocol_id, "diagnostic")

    def test_aucun_protocole_n_appelle_un_outil_interdit(self):
        forbidden = protocols._forbidden_tools()
        for protocol in protocols.PROTOCOLS:
            for beat in protocol.beats:
                if beat.tool:
                    self.assertNotIn(beat.tool, forbidden, beat.tool)

    def test_les_outils_utilises_existent(self):
        from src.tools import TOOL_FUNCTIONS

        for protocol in protocols.PROTOCOLS:
            for beat in protocol.beats:
                if beat.tool:
                    self.assertIn(beat.tool, TOOL_FUNCTIONS, beat.tool)

    def test_les_sondes_utilisees_existent(self):
        for protocol in protocols.PROTOCOLS:
            for beat in protocol.beats:
                if beat.probe:
                    self.assertIn(beat.probe, protocols.PROBES, beat.probe)


class ProtocolPlaybackTests(unittest.TestCase):
    def test_lecture_complete_emet_tous_les_evenements(self):
        events = []
        summary = protocols.play_protocol(
            protocols.DIAGNOSTIC, events.append, fast=True
        )

        self.assertTrue(summary["success"])
        self.assertFalse(summary["interrompu"])
        self.assertEqual(len(summary["etapes"]), len(protocols.DIAGNOSTIC.beats))

        kinds = [event["type"] for event in events]
        self.assertEqual(kinds[0], protocols.EVENT_START)
        self.assertEqual(kinds[-1], protocols.EVENT_DONE)
        beats = [e for e in events if e["type"] == protocols.EVENT_BEAT]
        self.assertEqual(len(beats), len(protocols.DIAGNOSTIC.beats))

    def test_progression_croissante_et_bornee(self):
        events = []
        protocols.play_protocol(protocols.WAKE_UP, events.append, fast=True)
        progress = [e["progress"] for e in events if e["type"] == protocols.EVENT_BEAT]
        self.assertEqual(progress, sorted(progress))
        self.assertAlmostEqual(progress[-1], 1.0, places=5)
        self.assertTrue(all(0.0 < p <= 1.0 for p in progress))

    def test_annulation_immediate(self):
        cancel = threading.Event()
        cancel.set()
        summary = protocols.play_protocol(
            protocols.WAKE_UP, cancel_event=cancel, fast=True
        )
        self.assertFalse(summary["success"])
        self.assertTrue(summary["interrompu"])
        self.assertEqual(summary["conclusion"], "Protocole interrompu.")

    def test_une_sonde_qui_echoue_ne_casse_pas_la_sequence(self):
        original = protocols.PROBES["cpu"]

        def _boom():
            raise RuntimeError("capteur grillé")

        protocols.PROBES["cpu"] = _boom
        try:
            summary = protocols.play_protocol(protocols.DIAGNOSTIC, fast=True)
        finally:
            protocols.PROBES["cpu"] = original

        self.assertTrue(summary["success"])
        self.assertTrue(any("indisponible" in line for line in summary["etapes"]))

    def test_un_outil_absent_degrade_le_beat_sans_echouer(self):
        protocol = protocols.Protocol(
            protocol_id="test_tool",
            name="Test",
            title="T",
            subtitle="",
            closing="fin",
            beats=(protocols.Beat("Action", 0.0, tool="outil_qui_n_existe_pas"),),
        )
        events = []
        summary = protocols.play_protocol(protocol, events.append, fast=True)
        self.assertTrue(summary["success"])
        beat = [e for e in events if e["type"] == protocols.EVENT_BEAT][0]
        self.assertEqual(beat["status"], "warn")

    def test_un_outil_interdit_est_refuse(self):
        protocol = protocols.Protocol(
            protocol_id="test_danger",
            name="Danger",
            title="T",
            subtitle="",
            closing="fin",
            beats=(protocols.Beat("Extinction", 0.0, tool="shutdown_pc",
                                  args={"confirm": True}),),
        )
        events = []
        protocols.play_protocol(protocol, events.append, fast=True)
        beat = [e for e in events if e["type"] == protocols.EVENT_BEAT][0]
        self.assertEqual(beat["status"], "warn")
        self.assertIn("refusée", beat["detail"])

    def test_listeners_globaux(self):
        received = []
        protocols.add_listener(received.append)
        try:
            protocols.play_protocol(protocols.DIAGNOSTIC, fast=True)
        finally:
            protocols.remove_listener(received.append)
        self.assertTrue(received)

        # Après désabonnement, plus rien n'arrive.
        count = len(received)
        protocols.play_protocol(protocols.DIAGNOSTIC, fast=True)
        self.assertEqual(len(received), count)

    def test_un_listener_qui_plante_n_interrompt_pas(self):
        def _bad(_event):
            raise RuntimeError("listener cassé")

        protocols.add_listener(_bad)
        try:
            summary = protocols.play_protocol(protocols.DIAGNOSTIC, fast=True)
        finally:
            protocols.remove_listener(_bad)
        self.assertTrue(summary["success"])

    def test_start_protocol_en_arriere_plan(self):
        run = protocols.start_protocol("diagnostic", fast=True)
        self.assertIsNotNone(run)
        run.join(timeout=10.0)
        self.assertFalse(run.running)
        self.assertIsNotNone(run.result)
        self.assertTrue(run.result["success"])

    def test_start_protocol_nom_inconnu(self):
        self.assertIsNone(protocols.start_protocol("n'existe pas"))

    def test_rendu_console_ne_plante_pas(self):
        renderer = protocols.render_console()
        summary = protocols.play_protocol(protocols.FOCUS, renderer, fast=True)
        self.assertTrue(summary["success"])


class SecretCodeTests(unittest.TestCase):
    def test_code_complet_declenche(self):
        detector = SecretCodeDetector()
        result = [detector.feed(ch, now=index * 0.1)
                  for index, ch in enumerate("wakeup")]
        self.assertEqual(result[-1], "wake_up")
        self.assertTrue(all(value is None for value in result[:-1]))

    def test_accents_et_majuscules_acceptes(self):
        detector = SecretCodeDetector()
        for index, ch in enumerate("RÉVEI"):
            detector.feed(ch, now=index * 0.1)
        self.assertEqual(detector.feed("L", now=1.0), "wake_up")

    def test_saisie_trop_lente_oubliee(self):
        detector = SecretCodeDetector()
        for index, ch in enumerate("wake"):
            detector.feed(ch, now=index * 0.1)
        # Dix secondes plus tard, les lettres précédentes ne comptent plus.
        self.assertIsNone(detector.feed("u", now=10.0))
        self.assertIsNone(detector.feed("p", now=10.1))

    def test_touche_non_alphabetique_reinitialise(self):
        detector = SecretCodeDetector()
        for index, ch in enumerate("wake"):
            detector.feed(ch, now=index * 0.1)
        detector.feed(" ", now=0.5)
        self.assertEqual(detector.buffer, "")

    def test_lettres_parasites_avant_le_code(self):
        detector = SecretCodeDetector()
        for index, ch in enumerate("xyzwakeup"):
            triggered = detector.feed(ch, now=index * 0.1)
        self.assertEqual(triggered, "wake_up")

    def test_le_tampon_est_vide_apres_succes(self):
        detector = SecretCodeDetector()
        for index, ch in enumerate("focus"):
            detector.feed(ch, now=index * 0.1)
        self.assertEqual(detector.buffer, "")

    def test_tous_les_codes_pointent_vers_un_protocole_reel(self):
        from src.secret_codes import SECRET_CODES

        for code, protocol_id in SECRET_CODES.items():
            self.assertIsNotNone(protocols.find_protocol(protocol_id), code)

    def test_aucun_code_ne_commence_par_un_raccourci_existant(self):
        # « m » (micro), « s » (stop) et « d » (debug) doivent rester
        # instantanés : aucun code secret ne peut commencer par ces lettres.
        from src.secret_codes import SECRET_CODES

        for code in SECRET_CODES:
            self.assertNotIn(code[0], {"m", "s", "d"}, code)

    def test_is_partial(self):
        detector = SecretCodeDetector()
        self.assertFalse(detector.is_partial())
        detector.feed("w", now=0.0)
        self.assertTrue(detector.is_partial())
        detector.reset()
        detector.feed("q", now=1.0)
        self.assertFalse(detector.is_partial())


class SecretGestureTests(unittest.TestCase):
    def test_trois_clics_rapides_au_centre(self):
        gesture = SecretGestureDetector()
        self.assertFalse(gesture.feed(5.0, 100.0, now=0.0))
        self.assertFalse(gesture.feed(5.0, 100.0, now=0.2))
        self.assertTrue(gesture.feed(5.0, 100.0, now=0.4))

    def test_clics_trop_lents_ne_declenchent_pas(self):
        gesture = SecretGestureDetector()
        self.assertFalse(gesture.feed(5.0, 100.0, now=0.0))
        self.assertFalse(gesture.feed(5.0, 100.0, now=5.0))
        self.assertFalse(gesture.feed(5.0, 100.0, now=10.0))

    def test_clic_hors_du_coeur_reinitialise(self):
        gesture = SecretGestureDetector()
        gesture.feed(5.0, 100.0, now=0.0)
        gesture.feed(5.0, 100.0, now=0.1)
        # Clic loin du centre : la série est annulée.
        self.assertFalse(gesture.feed(95.0, 100.0, now=0.2))
        self.assertFalse(gesture.feed(5.0, 100.0, now=0.3))

    def test_rayon_nul_ne_plante_pas(self):
        gesture = SecretGestureDetector()
        self.assertFalse(gesture.feed(0.0, 0.0, now=0.0))


class ProtocolToolTests(unittest.TestCase):
    """Les outils exposés à Gemini."""

    def test_outils_declares_et_implementes(self):
        from src.tools import TOOL_DECLARATIONS, TOOL_FUNCTIONS

        declared = {d["name"] for d in TOOL_DECLARATIONS}
        for name in ("run_protocol", "list_protocols", "cancel_protocol"):
            self.assertIn(name, declared)
            self.assertIn(name, TOOL_FUNCTIONS)

    def test_list_protocols_tool(self):
        from src import tools

        result = tools.list_protocols()
        self.assertTrue(result["success"])
        self.assertTrue(result["protocoles"])

    def test_run_protocol_nom_inconnu(self):
        from src import tools

        result = tools.run_protocol("bidon")
        self.assertFalse(result["success"])
        self.assertIn("protocoles", result)

    def test_run_protocol_rend_la_main_immediatement(self):
        from src import tools

        result = tools.run_protocol("diagnostic")
        self.assertTrue(result["success"])
        self.assertEqual(result["protocole"], "diagnostic")
        # La séquence tourne en fond : on l'annule pour ne pas polluer
        # les autres tests.
        tools.cancel_protocol()
        run = protocols.active_run()
        if run is not None:
            run.join(timeout=5.0)

    def test_cancel_protocol_sans_protocole(self):
        from src import tools

        run = protocols.active_run()
        if run is not None:
            run.cancel()
            run.join(timeout=5.0)
        result = tools.cancel_protocol()
        self.assertTrue(result["success"])


if __name__ == "__main__":
    unittest.main()
