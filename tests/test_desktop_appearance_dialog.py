"""Éditeur d'apparence du Desktop Mode (v1.7.0).

Couvre le contrat complet demandé pour la personnalisation : activer,
désactiver, déplacer (glisser-déposer), redimensionner, choisir les états,
prévisualiser, réinitialiser — et **persister** le tout dans le fichier
d'apparence existant.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

try:
    from PySide6.QtCore import QPointF, Qt  # noqa: E402
    from PySide6.QtGui import QMouseEvent  # noqa: E402
    from PySide6.QtWidgets import QApplication  # noqa: E402

    from UI import appearance_actions  # noqa: E402
    from UI.desktop import config as cfg  # noqa: E402
    from UI.desktop.state import DesktopState  # noqa: E402
    from UI.desktop_appearance_dialog import (  # noqa: E402
        DesktopAppearanceDialog,
        register_apply_hook,
        show_desktop_appearance_dialog,
        unregister_apply_hook,
    )

    HAS_QT = True
except Exception:  # pragma: no cover
    HAS_QT = False
    QApplication = None  # type: ignore[assignment]


def _qt_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _drag(widget, start: QPointF, end: QPointF) -> None:
    """Simule un glisser-déposer complet sur un widget."""
    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier
    )
    move = QMouseEvent(
        QMouseEvent.Type.MouseMove, end, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier
    )
    release = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton, Qt.NoModifier
    )
    widget.mousePressEvent(press)
    widget.mouseMoveEvent(move)
    widget.mouseReleaseEvent(release)


@unittest.skipUnless(HAS_QT, "PySide6 indisponible")
class DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _qt_app()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "appearance_state.json")
        self.state = appearance_actions.AppearanceState()
        self.dialog = DesktopAppearanceDialog(state=self.state, path=self.path)
        self.dialog.resize(760, 760)
        self.addCleanup(self.dialog.deleteLater)
        self.addCleanup(self.dialog.close)

    def _saved(self) -> dict:
        with open(self.path, "r", encoding="utf-8") as handle:
            return json.load(handle)["desktop"]

    # -- structure -----------------------------------------------------------

    def test_every_widget_has_a_row(self) -> None:
        for name in cfg.WIDGET_ORDER:
            self.assertIn(name, self.dialog.rows)

    def test_every_configurable_state_is_offered(self) -> None:
        row = self.dialog.rows[cfg.STATUS]
        from UI.desktop.state import CONFIGURABLE_STATES

        for state in CONFIGURABLE_STATES:
            self.assertIn(state, row.state_boxes)

    def test_dialog_renders(self) -> None:
        self.dialog.show()
        QApplication.processEvents()
        self.assertFalse(self.dialog.grab().isNull())

    # -- activation ----------------------------------------------------------

    def test_enabling_a_widget_persists_immediately(self) -> None:
        self.dialog.rows[cfg.RESPONSE].enabled.setChecked(True)
        self.assertTrue(self._saved()["widgets"][cfg.RESPONSE]["enabled"])

    def test_disabling_a_widget_persists_immediately(self) -> None:
        self.dialog.rows[cfg.STATUS].enabled.setChecked(False)
        self.assertFalse(self._saved()["widgets"][cfg.STATUS]["enabled"])

    def test_disabled_widget_greys_out_its_states(self) -> None:
        row = self.dialog.rows[cfg.TOOL]
        row.enabled.setChecked(False)
        for box in row.state_boxes.values():
            self.assertFalse(box.isEnabled())

    # -- taille --------------------------------------------------------------

    def test_scale_slider_persists(self) -> None:
        self.dialog.rows[cfg.TRANSCRIPT].scale.setValue(150)
        self.assertAlmostEqual(self._saved()["widgets"][cfg.TRANSCRIPT]["scale"], 1.5)

    def test_halo_has_no_scale(self) -> None:
        self.assertFalse(self.dialog.rows[cfg.HALO].scale.isEnabled())

    # -- visibilité par état -------------------------------------------------

    def test_state_checkbox_adds_and_removes(self) -> None:
        row = self.dialog.rows[cfg.TRANSCRIPT]
        row.state_boxes[DesktopState.SPEAKING].setChecked(True)
        self.assertIn(DesktopState.SPEAKING, self._saved()["widgets"][cfg.TRANSCRIPT]["states"])
        row.state_boxes[DesktopState.SPEAKING].setChecked(False)
        self.assertNotIn(
            DesktopState.SPEAKING, self._saved()["widgets"][cfg.TRANSCRIPT]["states"]
        )

    # -- déplacement ---------------------------------------------------------

    def test_drag_and_drop_moves_a_widget(self) -> None:
        preview = self.dialog.preview
        preview.resize(600, 320)
        preview.preview_state = DesktopState.LISTENING
        before = preview._config().slot(cfg.STATUS)
        start = preview.chip_rect(cfg.STATUS).center()
        screen = preview.screen_rect()
        end = QPointF(screen.left() + screen.width() * 0.25, screen.top() + screen.height() * 0.30)
        _drag(preview, start, end)
        after = preview._config().slot(cfg.STATUS)
        self.assertNotAlmostEqual(before.x, after.x, places=2)
        self.assertNotAlmostEqual(before.y, after.y, places=2)
        self.assertAlmostEqual(after.x, 0.25, places=1)
        self.assertAlmostEqual(after.y, 0.30, places=1)

    def test_drag_is_persisted_on_release(self) -> None:
        preview = self.dialog.preview
        preview.resize(600, 320)
        start = preview.chip_rect(cfg.STATUS).center()
        screen = preview.screen_rect()
        end = QPointF(screen.left() + screen.width() * 0.2, screen.top() + screen.height() * 0.2)
        _drag(preview, start, end)
        self.assertLess(self._saved()["widgets"][cfg.STATUS]["x"], 0.4)

    def test_drag_stays_inside_the_screen(self) -> None:
        preview = self.dialog.preview
        preview.resize(600, 320)
        start = preview.chip_rect(cfg.STATUS).center()
        _drag(preview, start, QPointF(-4000.0, -4000.0))
        slot = preview._config().slot(cfg.STATUS)
        self.assertGreaterEqual(slot.x, 0.0)
        self.assertGreaterEqual(slot.y, 0.0)
        self.assertLessEqual(slot.x, 1.0)
        self.assertLessEqual(slot.y, 1.0)

    def test_drag_snaps_to_the_grid(self) -> None:
        preview = self.dialog.preview
        preview.resize(600, 320)
        screen = preview.screen_rect()
        start = preview.chip_rect(cfg.STATUS).center()
        # Presque au centre : l'aimantation doit finir le travail.
        end = QPointF(
            screen.left() + screen.width() * 0.515,
            screen.top() + screen.height() * 0.489,
        )
        _drag(preview, start, end)
        slot = preview._config().slot(cfg.STATUS)
        self.assertAlmostEqual(slot.x, 0.5, places=3)
        self.assertAlmostEqual(slot.y, 0.5, places=3)

    def test_disabled_widgets_are_not_draggable(self) -> None:
        preview = self.dialog.preview
        preview.resize(600, 320)
        self.assertNotIn(cfg.RESPONSE, preview.visible_chips())
        self.assertNotIn(cfg.HALO, preview.visible_chips())

    # -- réglages globaux ----------------------------------------------------

    def test_interaction_combo_persists(self) -> None:
        index = self.dialog.interaction.findData(cfg.INTERACTION_ALWAYS)
        self.dialog.interaction.setCurrentIndex(index)
        self.assertEqual(self._saved()["interaction"], cfg.INTERACTION_ALWAYS)

    def test_reduced_motion_persists(self) -> None:
        self.dialog.reduced_motion.setChecked(True)
        self.assertTrue(self._saved()["reduced_motion"])

    def test_audio_reactive_persists(self) -> None:
        self.dialog.audio_reactive.setChecked(False)
        self.assertFalse(self._saved()["audio_reactive"])

    def test_global_sliders_persist(self) -> None:
        self.dialog.intensity.setValue(130)
        self.dialog.thickness.setValue(70)
        self.dialog.text_scale.setValue(140)
        saved = self._saved()
        self.assertAlmostEqual(saved["intensity"], 1.3)
        self.assertAlmostEqual(saved["thickness"], 0.7)
        self.assertAlmostEqual(saved["text_scale"], 1.4)

    # -- aperçu --------------------------------------------------------------

    def test_preview_follows_the_selected_state(self) -> None:
        index = next(
            i
            for i in range(self.dialog.state_combo.count())
            if self.dialog.state_combo.itemData(i) == DesktopState.SPEAKING
        )
        self.dialog.state_combo.setCurrentIndex(index)
        self.assertEqual(self.dialog.preview.preview_state, DesktopState.SPEAKING)

    def test_preview_renders_every_state(self) -> None:
        self.dialog.preview.resize(600, 320)
        for index in range(self.dialog.state_combo.count()):
            self.dialog.state_combo.setCurrentIndex(index)
            self.assertFalse(self.dialog.preview.grab().isNull())

    # -- réinitialisation ----------------------------------------------------

    def test_reset_restores_defaults_and_saves(self) -> None:
        self.dialog.rows[cfg.RESPONSE].enabled.setChecked(True)
        self.dialog.rows[cfg.STATUS].enabled.setChecked(False)
        self.dialog.intensity.setValue(150)
        self.dialog._reset_defaults()
        saved = self._saved()
        self.assertFalse(saved["widgets"][cfg.RESPONSE]["enabled"])
        self.assertTrue(saved["widgets"][cfg.STATUS]["enabled"])
        self.assertAlmostEqual(saved["intensity"], 1.0)
        self.assertTrue(self.dialog.rows[cfg.STATUS].enabled.isChecked())

    # -- persistance / rechargement ------------------------------------------

    def test_settings_survive_a_reload(self) -> None:
        self.dialog.rows[cfg.CONTROLS].enabled.setChecked(True)
        self.dialog.rows[cfg.TRANSCRIPT].scale.setValue(120)
        reloaded = appearance_actions.load_state(self.path)
        config = appearance_actions.desktop_config(reloaded)
        self.assertTrue(config.slot(cfg.CONTROLS).enabled)
        self.assertAlmostEqual(config.slot(cfg.TRANSCRIPT).scale, 1.2)

    def test_refresh_all_reads_back_external_changes(self) -> None:
        appearance_actions.desktop_config(self.state).set_slot(cfg.AUDIO, enabled=False)
        self.dialog.refresh_all()
        self.assertFalse(self.dialog.rows[cfg.AUDIO].enabled.isChecked())

    def test_summary_mentions_click_policy(self) -> None:
        self.dialog.refresh_all()
        self.assertIn("Souris", self.dialog.summary.text())
        self.dialog.rows[cfg.CONTROLS].enabled.setChecked(True)
        self.assertIn("cliquables", self.dialog.summary.text())

    # -- application à chaud --------------------------------------------------

    def test_apply_hook_is_called(self) -> None:
        calls: list[int] = []
        hook = lambda: calls.append(1)  # noqa: E731
        register_apply_hook(hook)
        self.addCleanup(unregister_apply_hook, hook)
        self.dialog.rows[cfg.RESPONSE].enabled.setChecked(True)
        self.assertTrue(calls)

    def test_failing_hook_does_not_break_saving(self) -> None:
        def _boom():
            raise RuntimeError("overlay mort")

        register_apply_hook(_boom)
        self.addCleanup(unregister_apply_hook, _boom)
        self.dialog.rows[cfg.RESPONSE].enabled.setChecked(True)
        self.assertTrue(self._saved()["widgets"][cfg.RESPONSE]["enabled"])


@unittest.skipUnless(HAS_QT, "PySide6 indisponible")
class SingletonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _qt_app()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "appearance_state.json")

    def test_single_shared_window(self) -> None:
        first = show_desktop_appearance_dialog(path=self.path)
        second = show_desktop_appearance_dialog(path=self.path)
        self.addCleanup(first.close)
        self.assertIs(first, second)

    def test_reopening_after_close_creates_a_fresh_window(self) -> None:
        first = show_desktop_appearance_dialog(path=self.path)
        first.close()
        QApplication.processEvents()
        second = show_desktop_appearance_dialog(path=self.path)
        self.addCleanup(second.close)
        self.assertIsNotNone(second)


if __name__ == "__main__":
    unittest.main()
