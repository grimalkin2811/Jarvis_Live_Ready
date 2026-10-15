"""Widgets « Paramètres » : apparence, mise en page, notifications,
fermeture & surveillance, sécurité. Chaque modification est validée,
persistée (écriture différée atomique) et appliquée à chaud."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QGridLayout,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QRadioButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config import (
    ANIMATION_LABELS,
    CLOSE_MODE_LABELS,
    CLOSE_MODES,
    INTERACTIVE_OPTIONS,
    NOTIFICATION_CATEGORIES,
)
from ..presets import PRESET_DESCRIPTIONS, PRESET_LABELS, available_presets
from ..security import LEVEL_LABELS
from ..theme import THEMES, resolve
from ..widget_api import STATE_READY, DashboardWidget
from .common import button, hrow, label


class ThemeCard(QWidget):
    clicked = Signal(str)

    def __init__(self, theme_id: str, parent=None) -> None:
        super().__init__(parent)
        self.theme_id = theme_id
        self.selected = False
        self.setMinimumSize(120, 74)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(THEMES[theme_id].tagline)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit(self.theme_id)

    def paintEvent(self, event) -> None:  # noqa: N802
        from ..config import AppearancePrefs
        from ..painting import paint_panel

        spec = THEMES[self.theme_id]
        theme = resolve(AppearancePrefs(theme=self.theme_id), reduced_motion=True)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect())
        p.fillRect(rect, QColor(spec.bg_bottom))
        paint_panel(p, rect.adjusted(4, 4, -4, -4), theme, active=self.selected)
        p.setPen(theme.accent)
        font = p.font()
        font.setBold(True)
        p.setFont(font)
        p.drawText(rect.adjusted(12, 8, -8, -8), Qt.AlignLeft | Qt.AlignTop, spec.name)
        p.setPen(theme.text_dim)
        font.setBold(False)
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.5))
        p.setFont(font)
        p.drawText(rect.adjusted(12, 28, -8, -8), Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, spec.tagline)
        if self.selected:
            p.setPen(theme.accent)
            p.drawText(rect.adjusted(8, 8, -12, -8), Qt.AlignRight | Qt.AlignBottom, "✓")
        p.end()


class AppearanceSettingsWidget(DashboardWidget):
    def __init__(self, context, settings=None) -> None:
        super().__init__(context, settings)
        self._updating = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        cards = QGridLayout()
        self.cards: dict[str, ThemeCard] = {}
        for index, theme_id in enumerate(THEMES):
            card = ThemeCard(theme_id)
            card.clicked.connect(lambda tid: self._update(theme=tid))
            cards.addWidget(card, 0, index)
            self.cards[theme_id] = card
        layout.addLayout(cards)
        grid = QGridLayout()
        grid.setVerticalSpacing(6)
        self.accent_button = button("Accent…")
        self.accent_button.clicked.connect(self._pick_accent)
        self.accent_reset = button("Accent du thème")
        self.accent_reset.clicked.connect(lambda: self._update(accent=None))
        grid.addWidget(label("Couleur d'accent", "dim"), 0, 0)
        grid.addLayout(hrow(self.accent_button, self.accent_reset, None), 0, 1)
        self.glow = QSlider(Qt.Horizontal)
        self.glow.setRange(0, 100)
        self.glow.sliderReleased.connect(lambda: self._update(glow=self.glow.value()))
        self.glow.valueChanged.connect(lambda v: None if self.glow.isSliderDown() else self._update(glow=v))
        grid.addWidget(label("Intensité de la lueur", "dim"), 1, 0)
        grid.addWidget(self.glow, 1, 1)
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(40, 100)
        self.opacity.sliderReleased.connect(lambda: self._update(panel_opacity=self.opacity.value()))
        self.opacity.valueChanged.connect(lambda v: None if self.opacity.isSliderDown() else self._update(panel_opacity=v))
        grid.addWidget(label("Opacité des panneaux", "dim"), 2, 0)
        grid.addWidget(self.opacity, 2, 1)
        self.density = QComboBox()
        for key, text in (("compact", "Compacte"), ("normal", "Normale"), ("confortable", "Confortable")):
            self.density.addItem(text, key)
        self.density.currentIndexChanged.connect(lambda _i: self._update(density=self.density.currentData()))
        grid.addWidget(label("Densité", "dim"), 3, 0)
        grid.addWidget(self.density, 3, 1)
        self.animation = QComboBox()
        for key, text in ANIMATION_LABELS.items():
            self.animation.addItem(text, key)
        self.animation.currentIndexChanged.connect(lambda _i: self._update(animation_level=self.animation.currentData()))
        grid.addWidget(label("Animations", "dim"), 4, 0)
        grid.addWidget(self.animation, 4, 1)
        self.motion = QCheckBox("Respecter les préférences d'accessibilité du système")
        self.motion.toggled.connect(lambda checked: self._update(respect_system_motion=checked))
        grid.addWidget(self.motion, 5, 0, 1, 2)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        self.note = label("", "dim", wrap=True)
        layout.addWidget(self.note)
        layout.addStretch(1)

    def on_activate(self) -> None:
        self.listen(self.context.theme_manager.changed, lambda _t: self._load())
        self._load()

    def refresh(self) -> None:
        self._load()

    def _load(self) -> None:
        self._updating = True
        prefs = self.context.hub.store.config.appearance
        theme = self.context.theme
        for theme_id, card in self.cards.items():
            card.selected = theme_id == prefs.theme
            card.update()
        self.glow.setValue(prefs.glow)
        self.opacity.setValue(prefs.panel_opacity)
        self.density.setCurrentIndex(max(0, self.density.findData(prefs.density)))
        self.animation.setCurrentIndex(max(0, self.animation.findData(prefs.animation_level)))
        self.motion.setChecked(prefs.respect_system_motion)
        self.accent_button.setText(f"Accent… ({theme.accent.name()})")
        self.accent_reset.setEnabled(prefs.accent is not None)
        self.note.setText(
            "Animations réduites : préférence d'accessibilité du système détectée." if theme.reduced_by_system
            else f"Niveau effectif : {ANIMATION_LABELS.get(theme.animation_level)} · {theme.fps or 0} images/s max."
        )
        self._updating = False
        self.set_state(STATE_READY)

    def _update(self, **changes) -> None:
        if self._updating:
            return
        self.context.theme_manager.update(**changes)

    def _pick_accent(self) -> None:
        color = QColorDialog.getColor(self.context.theme.accent, self, "Couleur d'accent")
        if color.isValid():
            self._update(accent=color.name())


class LayoutSettingsWidget(DashboardWidget):
    def __init__(self, context, settings=None) -> None:
        super().__init__(context, settings)
        self._updating = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.multipage = QRadioButton("Plusieurs pages (navigation latérale)")
        self.onepage = QRadioButton("OnePage (tout sur une page, diagnostics en bas)")
        group = QButtonGroup(self)
        group.addButton(self.multipage)
        group.addButton(self.onepage)
        self.multipage.toggled.connect(lambda checked: self._view("multipage") if checked else None)
        self.onepage.toggled.connect(lambda checked: self._view("onepage") if checked else None)
        layout.addWidget(label("Affichage", "dim"))
        layout.addWidget(self.multipage)
        layout.addWidget(self.onepage)
        layout.addWidget(label("Préréglages", "dim"))
        self.preset = QComboBox()
        for key in available_presets():
            self.preset.addItem(PRESET_LABELS[key], key)
        self.preset.currentIndexChanged.connect(self._preset_hint)
        apply = button("Appliquer")
        apply.clicked.connect(self._apply_preset)
        save = button("Enregistrer comme « Personnalisée »")
        save.clicked.connect(self._save_custom)
        layout.addLayout(hrow(self.preset, apply, None))
        self.preset_hint = label("", "dim", wrap=True)
        layout.addWidget(self.preset_hint)
        layout.addWidget(save)
        layout.addWidget(label("Pages", "dim"))
        self.pages = QListWidget()
        self.pages.setMaximumHeight(170)
        layout.addWidget(self.pages, 1)
        add = button("+ Page")
        add.clicked.connect(self._add_page)
        rename = button("Renommer")
        rename.clicked.connect(self._rename_page)
        up = button("↑")
        up.clicked.connect(lambda: self._move(-1))
        down = button("↓")
        down.clicked.connect(lambda: self._move(1))
        delete = button("Supprimer", "danger")
        delete.clicked.connect(self._delete_page)
        layout.addLayout(hrow(add, rename, up, down, None, delete))
        self.current_preset = label("", "dim", wrap=True)
        layout.addWidget(self.current_preset)

    def on_activate(self) -> None:
        controller = self.context.controller
        if controller is not None:
            self.listen(controller.layout_changed, self._load)
        self._load()

    def refresh(self) -> None:
        self._load()

    def _load(self) -> None:
        self._updating = True
        layout = self.context.hub.store.config.layout
        (self.onepage if layout.view_mode == "onepage" else self.multipage).setChecked(True)
        self.preset.setCurrentIndex(max(0, self.preset.findData(layout.preset)))
        self.current_preset.setText(f"Préréglage actif : {PRESET_LABELS.get(layout.preset, layout.preset)}"
                                    + (" (une disposition personnalisée est enregistrée)" if layout.custom_snapshot else ""))
        selected = self.pages.currentItem().data(Qt.UserRole) if self.pages.currentItem() else None
        self.pages.clear()
        for page in layout.pages:
            if page.extras.get("_hidden_overflow"):
                continue
            item = QListWidgetItem(f"{page.icon}  {page.title}" + ("" if page.builtin else "  (personnalisée)"))
            item.setData(Qt.UserRole, page.id)
            self.pages.addItem(item)
            if page.id == selected:
                self.pages.setCurrentItem(item)
        self._preset_hint()
        self._updating = False
        self.set_state(STATE_READY)

    def _preset_hint(self) -> None:
        self.preset_hint.setText(PRESET_DESCRIPTIONS.get(self.preset.currentData(), ""))

    def _view(self, mode: str) -> None:
        if not self._updating and self.context.controller is not None:
            self.context.controller.set_view_mode(mode)

    def _apply_preset(self) -> None:
        preset = self.preset.currentData()
        if not self.context.confirm("layout.preset", f"Appliquer le préréglage « {PRESET_LABELS[preset]} » ?",
                                    "La disposition des pages intégrées est remplacée (vos pages personnalisées et "
                                    "aucune donnée ne sont touchées). « Personnalisée » restaure votre disposition "
                                    "enregistrée."):
            return
        ok, message = self.context.controller.apply_preset(preset)
        self.context.toast("Préréglage appliqué." if ok else message, "success" if ok else "warning")

    def _save_custom(self) -> None:
        self.context.controller.save_custom_layout()
        self.context.toast("Disposition enregistrée comme « Personnalisée ».", "success")

    def _selected(self) -> str | None:
        item = self.pages.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _add_page(self) -> None:
        title, ok = QInputDialog.getText(self, "Nouvelle page", "Nom de la page :")
        if not ok:
            return
        page, message = self.context.controller.add_page(title)
        self.context.toast(f"Page « {page.title} » créée." if page else message, "success" if page else "warning")

    def _rename_page(self) -> None:
        page_id = self._selected()
        if page_id is None:
            return
        page = self.context.hub.store.config.layout.page(page_id)
        title, ok = QInputDialog.getText(self, "Renommer la page", "Nouveau nom :", text=page.title if page else "")
        if not ok:
            return
        done, message = self.context.controller.rename_page(page_id, title)
        self.context.toast("Page renommée." if done else message, "success" if done else "warning")

    def _move(self, delta: int) -> None:
        page_id = self._selected()
        if page_id is not None:
            self.context.controller.move_page(page_id, delta)

    def _delete_page(self) -> None:
        page_id = self._selected()
        if page_id is None:
            return
        page = self.context.hub.store.config.layout.page(page_id)
        if page is None or page.builtin:
            self.context.toast("Les pages intégrées ne peuvent pas être supprimées.", "warning")
            return
        if not self.context.confirm("page.delete", f"Supprimer la page « {page.title} » ?",
                                    "Seule la page et sa disposition disparaissent ; aucune donnée métier."):
            return
        done, message = self.context.controller.delete_page(page_id)
        self.context.toast("Page supprimée." if done else message, "success" if done else "warning")


class NotificationSettingsWidget(DashboardWidget):
    def __init__(self, context, settings=None) -> None:
        super().__init__(context, settings)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        grid = QGridLayout()
        grid.addWidget(label("Catégorie", "dim"), 0, 0)
        grid.addWidget(label("Dashboard", "dim"), 0, 1)
        grid.addWidget(label("Windows (natif)", "dim"), 0, 2)
        self.boxes: dict[tuple[str, str], QCheckBox] = {}
        for row, (key, (title, _d, _n)) in enumerate(NOTIFICATION_CATEGORIES.items(), start=1):
            grid.addWidget(label(title, wrap=True), row, 0)
            for column, channel in ((1, "dashboard"), (2, "native")):
                box = QCheckBox()
                box.toggled.connect(lambda checked, k=key, c=channel: self._set(k, c, checked))
                grid.addWidget(box, row, column, alignment=Qt.AlignCenter)
                self.boxes[(key, channel)] = box
        grid.setColumnStretch(0, 1)
        layout.addLayout(grid)
        layout.addWidget(label("Dashboard fermé, les notifications continuent tant que Jarvis tourne (orbe ou "
                               "Desktop) ; les bulles natives sont alors préfixées « Dashboard — ». Une même "
                               "information n'est jamais notifiée deux fois.", "dim", wrap=True))
        layout.addStretch(1)
        self._loading = False

    def on_activate(self) -> None:
        self._load()

    def refresh(self) -> None:
        self._load()

    def _load(self) -> None:
        self._loading = True
        prefs = self.context.hub.store.config.notifications
        for (key, channel), box in self.boxes.items():
            box.setChecked(prefs.wants(key, channel))
        self._loading = False
        self.set_state(STATE_READY)

    def _set(self, key: str, channel: str, value: bool) -> None:
        if self._loading:
            return
        prefs = self.context.hub.store.config.notifications
        prefs.categories.setdefault(key, {})[channel] = bool(value)
        self.context.hub.store.changed("notifications")


class BehaviorSettingsWidget(DashboardWidget):
    def __init__(self, context, settings=None) -> None:
        super().__init__(context, settings)
        self._loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label("À la fermeture de la fenêtre", "dim"))
        self.modes: dict[str, QRadioButton] = {}
        group = QButtonGroup(self)
        for mode in CLOSE_MODES:
            radio = QRadioButton(CLOSE_MODE_LABELS[mode])
            radio.toggled.connect(lambda checked, m=mode: self._set_close(m) if checked else None)
            group.addButton(radio)
            layout.addWidget(radio)
            self.modes[mode] = radio
        self.options: dict[str, QCheckBox] = {}
        for key, (title, _default) in INTERACTIVE_OPTIONS.items():
            box = QCheckBox(title)
            box.toggled.connect(lambda checked, k=key: self._set_option(k, checked))
            layout.addWidget(box)
            self.options[key] = box
        layout.addWidget(label("Le moteur vocal, la conversation et les tâches actives ne sont jamais arrêtés par "
                               "les modes persistant et interactif.", "dim", wrap=True))
        layout.addWidget(label("Surveillance des ressources", "dim"))
        self.monitor = QCheckBox("Activer la surveillance (CPU, RAM, disque, réseau)")
        self.monitor.toggled.connect(lambda v: self._set_monitoring(enabled=v))
        self.background = QCheckBox("Continuer en arrière-plan pour les alertes de seuil")
        self.background.toggled.connect(lambda v: self._set_monitoring(background=v))
        self.interval = QSpinBox()
        self.interval.setRange(1, 60)
        self.interval.setSuffix(" s")
        self.interval.valueChanged.connect(lambda v: self._set_monitoring(interval_s=v))
        self.cpu = QSpinBox()
        self.ram = QSpinBox()
        self.disk = QSpinBox()
        for box, key in ((self.cpu, "cpu_threshold"), (self.ram, "ram_threshold"), (self.disk, "disk_threshold")):
            box.setRange(50, 100)
            box.setSuffix(" %")
            box.valueChanged.connect(lambda v, k=key: self._set_monitoring(**{k: v}))
        layout.addWidget(self.monitor)
        layout.addWidget(self.background)
        layout.addLayout(hrow(label("Intervalle", "dim"), self.interval, label("Seuils CPU", "dim"), self.cpu,
                              label("RAM", "dim"), self.ram, label("Disque", "dim"), self.disk, None))
        layout.addWidget(label("Général", "dim"))
        self.open_on_launch = QCheckBox("Ouvrir le Dashboard au démarrage de Jarvis")
        self.open_on_launch.toggled.connect(lambda v: self._set_general(open_on_launch=v))
        self.auto_update = QCheckBox("Vérifier les mises à jour une fois par jour (API GitHub publique)")
        self.auto_update.toggled.connect(lambda v: self._set_general(auto_update_check=v))
        layout.addWidget(self.open_on_launch)
        layout.addWidget(self.auto_update)
        layout.addStretch(1)

    def on_activate(self) -> None:
        self._load()

    def refresh(self) -> None:
        self._load()

    def _load(self) -> None:
        self._loading = True
        config = self.context.hub.store.config
        self.modes[config.close.mode].setChecked(True)
        for key, box in self.options.items():
            box.setChecked(bool(config.close.interactive.get(key)))
            box.setEnabled(config.close.mode == "interactive")
        m = config.monitoring
        self.monitor.setChecked(m.enabled)
        self.background.setChecked(m.background)
        self.interval.setValue(m.interval_s)
        self.cpu.setValue(m.cpu_threshold)
        self.ram.setValue(m.ram_threshold)
        self.disk.setValue(m.disk_threshold)
        self.open_on_launch.setChecked(config.general.open_on_launch)
        self.auto_update.setChecked(config.general.auto_update_check)
        self._loading = False
        self.set_state(STATE_READY)

    def _set_close(self, mode: str) -> None:
        if self._loading:
            return
        self.context.hub.store.config.close.mode = mode
        for box in self.options.values():
            box.setEnabled(mode == "interactive")
        self.context.hub.store.changed("close")

    def _set_option(self, key: str, value: bool) -> None:
        if self._loading:
            return
        self.context.hub.store.config.close.interactive[key] = bool(value)
        self.context.hub.store.changed("close")

    def _set_monitoring(self, **changes) -> None:
        if self._loading:
            return
        prefs = self.context.hub.store.config.monitoring
        for key, value in changes.items():
            setattr(prefs, key, value)
        self.context.hub.store.changed("monitoring")
        self.context.hub.apply_monitoring_prefs()

    def _set_general(self, **changes) -> None:
        if self._loading:
            return
        prefs = self.context.hub.store.config.general
        for key, value in changes.items():
            setattr(prefs, key, bool(value))
        self.context.hub.store.changed("general")


class SecuritySettingsWidget(DashboardWidget):
    def __init__(self, context, settings=None) -> None:
        super().__init__(context, settings)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label("Niveau 1 : immédiat · Niveau 2 : confirmation (désactivable ci-dessous si "
                               "autorisé) · Niveau 3 : destructif, confirmation renforcée non désactivable.",
                               "dim", wrap=True))
        self.grid = QGridLayout()
        self.grid.setColumnStretch(0, 1)
        layout.addLayout(self.grid)
        layout.addStretch(1)
        self._boxes: dict[str, QCheckBox] = {}
        self._loading = False

    def on_activate(self) -> None:
        self._load()

    def refresh(self) -> None:
        self._load()

    def _load(self) -> None:
        self._loading = True
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        security = self.context.hub.security
        row = 0
        for policy in sorted(security.lowerable_policies(), key=lambda p: p.label):
            box = QCheckBox("Demander confirmation")
            box.setChecked(security.effective_level(policy.action_id) >= 2)
            box.toggled.connect(lambda checked, aid=policy.action_id: self._set(aid, checked))
            self.grid.addWidget(label(policy.label + (f" — {policy.description}" if policy.description else ""),
                                      wrap=True), row, 0)
            self.grid.addWidget(box, row, 1)
            self._boxes[policy.action_id] = box
            row += 1
        for policy in sorted(security.locked_policies(), key=lambda p: (-p.level, p.label)):
            self.grid.addWidget(label(f"⊘ {policy.label}", wrap=True), row, 0)
            self.grid.addWidget(label(LEVEL_LABELS[policy.level].split(" — ")[0] + " — verrouillé", "dim"), row, 1)
            row += 1
        self._loading = False
        self.set_state(STATE_READY)

    def _set(self, action_id: str, confirm: bool) -> None:
        if self._loading:
            return
        security = self.context.hub.security
        if security.set_confirmation(action_id, confirm):
            self.context.hub.store.config.security.confirm = dict(security.overrides)
            self.context.hub.store.changed("security")
