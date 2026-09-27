"""Configuration de l'apparence Desktop (v1.7.0) : défauts, persistance, migration.

Le bloc ``desktop`` vit dans le fichier d'apparence EXISTANT
(``appearance_state.json``). Ces tests garantissent notamment qu'un fichier
écrit par la 1.6.0 continue de fonctionner sans perdre aucun réglage.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from UI.desktop import config as cfg  # noqa: E402
from UI.desktop.state import CONFIGURABLE_STATES, DesktopState  # noqa: E402


class DefaultsTests(unittest.TestCase):
    """Les défauts doivent être excellents sans ouvrir l'éditeur."""

    def setUp(self) -> None:
        self.config = cfg.DesktopAppearanceConfig()

    def test_default_interaction_is_non_intrusive(self) -> None:
        self.assertEqual(self.config.interaction, cfg.INTERACTION_WIDGETS)
        self.assertTrue(self.config.click_through())

    def test_no_widget_intercepts_clicks_by_default(self) -> None:
        # Tant que les contrôles sont éteints, RIEN ne capte la souris.
        self.assertFalse(self.config.has_interactive_widgets())

    def test_essential_widgets_are_enabled(self) -> None:
        enabled = set(self.config.enabled_widgets())
        self.assertIn(cfg.HALO, enabled)
        self.assertIn(cfg.STATUS, enabled)
        self.assertIn(cfg.TRANSCRIPT, enabled)
        self.assertIn(cfg.AUDIO, enabled)
        self.assertIn(cfg.TOOL, enabled)

    def test_chatbot_like_widgets_are_off_by_default(self) -> None:
        # Choix assumé : Jarvis parle, il n'écrit pas un fil de discussion.
        self.assertFalse(self.config.slot(cfg.RESPONSE).enabled)
        self.assertFalse(self.config.slot(cfg.CONTROLS).enabled)

    def test_hidden_state_shows_nothing(self) -> None:
        for name in cfg.WIDGET_ORDER:
            self.assertFalse(self.config.is_visible(name, DesktopState.HIDDEN))

    def test_tool_widget_only_in_tool_state(self) -> None:
        self.assertTrue(self.config.is_visible(cfg.TOOL, DesktopState.TOOL_USE))
        self.assertFalse(self.config.is_visible(cfg.TOOL, DesktopState.LISTENING))

    def test_reduced_motion_is_off_but_available(self) -> None:
        self.assertFalse(self.config.reduced_motion)
        self.assertEqual(self.config.motion_scale(), 1.0)
        self.config.reduced_motion = True
        self.assertLess(self.config.motion_scale(), 1.0)


class ValidationTests(unittest.TestCase):
    def test_values_are_clamped(self) -> None:
        config = cfg.DesktopAppearanceConfig.from_dict(
            {
                "intensity": 99.0,
                "thickness": -5,
                "text_scale": "abc",
                "transcript_seconds": 1000,
                "widgets": {cfg.STATUS: {"x": 12.0, "y": -3.0, "scale": 50}},
            }
        )
        self.assertLessEqual(config.intensity, 1.6)
        self.assertGreaterEqual(config.thickness, 0.5)
        self.assertEqual(config.text_scale, 1.0)  # valeur illisible → défaut
        self.assertLessEqual(config.transcript_seconds, 30.0)
        slot = config.slot(cfg.STATUS)
        self.assertLessEqual(slot.x, 0.98)
        self.assertGreaterEqual(slot.y, 0.02)
        self.assertLessEqual(slot.scale, 2.0)

    def test_unknown_states_are_dropped(self) -> None:
        config = cfg.DesktopAppearanceConfig.from_dict(
            {"widgets": {cfg.STATUS: {"states": ["listening", "licorne", "hidden"]}}}
        )
        self.assertEqual(config.slot(cfg.STATUS).states, (DesktopState.LISTENING,))

    def test_interaction_aliases(self) -> None:
        self.assertEqual(cfg.normalize_interaction("click_through"), cfg.INTERACTION_ALWAYS)
        self.assertEqual(cfg.normalize_interaction("full"), cfg.INTERACTION_FULL)
        self.assertEqual(cfg.normalize_interaction("zzz"), cfg.INTERACTION_WIDGETS)

    def test_set_state_visibility_keeps_canonical_order(self) -> None:
        config = cfg.DesktopAppearanceConfig()
        config.set_slot(cfg.STATUS, states=[])
        config.set_state_visibility(cfg.STATUS, DesktopState.SPEAKING, True)
        config.set_state_visibility(cfg.STATUS, DesktopState.LISTENING, True)
        self.assertEqual(
            config.slot(cfg.STATUS).states,
            (DesktopState.LISTENING, DesktopState.SPEAKING),
        )

    def test_set_state_visibility_ignores_hidden(self) -> None:
        config = cfg.DesktopAppearanceConfig()
        config.set_state_visibility(cfg.STATUS, DesktopState.HIDDEN, True)
        self.assertNotIn(DesktopState.HIDDEN, config.slot(cfg.STATUS).states)

    def test_reset_restores_defaults(self) -> None:
        config = cfg.DesktopAppearanceConfig()
        config.interaction = cfg.INTERACTION_FULL
        config.reduced_motion = True
        config.set_slot(cfg.HALO, enabled=False, x=0.1)
        config.reset()
        self.assertEqual(config.interaction, cfg.INTERACTION_WIDGETS)
        self.assertFalse(config.reduced_motion)
        self.assertTrue(config.slot(cfg.HALO).enabled)
        self.assertEqual(config.slot(cfg.HALO).x, 0.5)


class RoundTripTests(unittest.TestCase):
    def test_serialization_is_stable(self) -> None:
        config = cfg.DesktopAppearanceConfig()
        config.interaction = cfg.INTERACTION_ALWAYS
        config.set_slot(cfg.STATUS, x=0.21, y=0.07, scale=1.35)
        config.set_state_visibility(cfg.TRANSCRIPT, DesktopState.SPEAKING, True)
        restored = cfg.DesktopAppearanceConfig.from_dict(config.to_dict())
        self.assertEqual(restored.to_dict(), config.to_dict())
        self.assertEqual(restored.interaction, cfg.INTERACTION_ALWAYS)
        self.assertAlmostEqual(restored.slot(cfg.STATUS).x, 0.21)
        self.assertIn(DesktopState.SPEAKING, restored.slot(cfg.TRANSCRIPT).states)

    def test_json_serializable(self) -> None:
        payload = cfg.DesktopAppearanceConfig().to_dict()
        json.loads(json.dumps(payload))

    def test_schema_version_is_written(self) -> None:
        self.assertEqual(
            cfg.DesktopAppearanceConfig().to_dict()["schema_version"], cfg.SCHEMA_VERSION
        )

    def test_future_schema_is_read_best_effort(self) -> None:
        payload = cfg.DesktopAppearanceConfig().to_dict()
        payload["schema_version"] = 99
        payload["interaction"] = cfg.INTERACTION_ALWAYS
        restored = cfg.DesktopAppearanceConfig.from_dict(payload)
        self.assertEqual(restored.interaction, cfg.INTERACTION_ALWAYS)
        self.assertEqual(restored.schema_version, cfg.SCHEMA_VERSION)

    def test_garbage_payload_falls_back_to_defaults(self) -> None:
        for payload in (None, [], "texte", 42):
            config = cfg.DesktopAppearanceConfig.from_dict(payload)
            self.assertEqual(config.interaction, cfg.INTERACTION_WIDGETS)


class AppearanceStateIntegrationTests(unittest.TestCase):
    """Le bloc Desktop voyage dans le fichier d'apparence existant."""

    def setUp(self) -> None:
        from UI import appearance_actions

        self.actions = appearance_actions
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "appearance_state.json")

    def test_no_second_config_file_is_created(self) -> None:
        state = self.actions.AppearanceState()
        self.actions.save_state(state, self.path)
        self.assertEqual(os.listdir(self.tmp.name), ["appearance_state.json"])

    def test_desktop_block_is_persisted_and_restored(self) -> None:
        state = self.actions.AppearanceState()
        config = self.actions.desktop_config(state)
        config.interaction = cfg.INTERACTION_ALWAYS
        config.reduced_motion = True
        config.set_slot(cfg.STATUS, x=0.12, y=0.08, scale=1.4)
        config.set_slot(cfg.CONTROLS, enabled=True)
        self.actions.save_state(state, self.path)

        restored = self.actions.load_state(self.path)
        restored_config = self.actions.desktop_config(restored)
        self.assertEqual(restored_config.interaction, cfg.INTERACTION_ALWAYS)
        self.assertTrue(restored_config.reduced_motion)
        self.assertAlmostEqual(restored_config.slot(cfg.STATUS).x, 0.12)
        self.assertTrue(restored_config.slot(cfg.CONTROLS).enabled)

    def test_v160_file_still_loads_with_defaults(self) -> None:
        """Migration : un fichier 1.6.0 n'a pas de bloc ``desktop``."""
        legacy = {
            "theme_name": "purple",
            "glow_intensity": 1.2,
            "blob_scale": 1.1,
            "time_scale": 1.25,
            "minimal_mode": False,
            "cinematic_mode": False,
            "item_bg_opacity": 0.42,
        }
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(legacy, handle)

        state = self.actions.load_state(self.path)
        # Les réglages 1.6.0 sont intacts…
        self.assertEqual(state.theme_name, "purple")
        self.assertAlmostEqual(state.item_bg_opacity, 0.42)
        # …et le Desktop Mode démarre sur ses défauts.
        config = self.actions.desktop_config(state)
        self.assertEqual(config.interaction, cfg.INTERACTION_WIDGETS)
        self.assertTrue(config.slot(cfg.HALO).enabled)

    def test_saving_a_v160_state_adds_the_block(self) -> None:
        legacy = {"theme_name": "green"}
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(legacy, handle)
        state = self.actions.load_state(self.path)
        self.actions.save_state(state, self.path)
        with open(self.path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertIn("desktop", payload)
        self.assertEqual(payload["theme_name"], "green")

    def test_unknown_future_keys_do_not_break_loading(self) -> None:
        payload = {
            "theme_name": "blue",
            "desktop": {
                "schema_version": cfg.SCHEMA_VERSION,
                "interaction": "always",
                "widgets": {"halo": {"enabled": True}},
                "clé_du_futur": {"a": 1},
            },
            "autre_clé_inconnue": True,
        }
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        state = self.actions.load_state(self.path)
        self.assertEqual(
            self.actions.desktop_config(state).interaction, cfg.INTERACTION_ALWAYS
        )

    def test_survives_multiple_save_load_cycles(self) -> None:
        state = self.actions.AppearanceState()
        self.actions.desktop_config(state).set_slot(cfg.AUDIO, x=0.8, y=0.2)
        for _ in range(5):
            self.actions.save_state(state, self.path)
            state = self.actions.load_state(self.path)
        slot = self.actions.desktop_config(state).slot(cfg.AUDIO)
        self.assertAlmostEqual(slot.x, 0.8)
        self.assertAlmostEqual(slot.y, 0.2)


class SummaryTests(unittest.TestCase):
    def test_states_summary_is_readable(self) -> None:
        self.assertEqual(cfg.states_summary([]), "jamais")
        self.assertEqual(cfg.states_summary(CONFIGURABLE_STATES), "tous les états")
        self.assertIn("À l'écoute", cfg.states_summary([DesktopState.LISTENING]))


if __name__ == "__main__":
    unittest.main()
