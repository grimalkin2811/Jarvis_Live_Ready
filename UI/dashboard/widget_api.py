"""Contrat explicite des widgets du Dashboard.

Un widget est décrit par un ``WidgetSpec`` (métadonnées DÉCLARATIVES, sans
instancier quoi que ce soit) et implémenté par une sous-classe de
``DashboardWidget`` (vue Qt). Pas de découverte dynamique de plugins : les
widgets sont enregistrés explicitement dans ``registry.default_registry``.

Cycle de vie (garanti par ``frame``/``grid_view``)
--------------------------------------------------
1. ``__init__(context, settings)`` — construction LÉGÈRE : aucun minuteur,
   aucun abonnement, aucun accès disque ou réseau ;
2. ``activate()`` — le widget devient visible : il s'abonne (``self.listen``),
   démarre ses minuteurs (``self.every``) et charge ses données (de
   préférence via ``self.run_job`` hors du thread graphique) ;
3. ``deactivate()`` — page masquée, fenêtre fermée, défilement hors champ :
   abonnements et minuteurs sont **automatiquement** libérés ;
4. ``apply_settings(settings)`` — réglages d'instance modifiés ;
5. ``dispose()`` — destruction définitive (désactive puis nettoie).

États d'affichage : ``loading`` / ``ready`` / ``empty`` / ``error`` /
``unavailable`` (``set_state``). Le cadre affiche un message explicite pour
tous les états autres que ``ready``.

Les données métier ne vivent jamais dans un widget : il les lit via
``context.hub`` et ses services. Deux instances du même widget (même page ou
pages différentes) affichent donc exactement la même source.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QWidget

STATE_LOADING = "loading"
STATE_READY = "ready"
STATE_EMPTY = "empty"
STATE_ERROR = "error"
STATE_UNAVAILABLE = "unavailable"
STATES = (STATE_LOADING, STATE_READY, STATE_EMPTY, STATE_ERROR, STATE_UNAVAILABLE)

CATEGORIES = {
    "jarvis": "Jarvis",
    "conversation": "Conversation",
    "taches": "Tâches",
    "memoire": "Mémoire",
    "productivite": "Productivité",
    "systeme": "Système",
    "parametres": "Paramètres",
    "personnalise": "Personnalisé",
}

#: Capacités déclarables (documentation + filtrage de la bibliothèque).
CAPABILITIES = {
    "realtime": "Mise à jour en temps réel",
    "actions": "Propose des actions",
    "settings": "Réglages par instance",
    "multi_instance": "Plusieurs instances par page",
    "editor": "Formulaires d'édition",
    "graph": "Graphiques de données réelles",
}

#: Dépendances déclarables (services requis).
DEPENDENCIES = {
    "engine": "Moteur vocal (Gemini Live)",
    "tasks": "Task Manager",
    "memory": "Mémoire persistante",
    "psutil": "psutil (mesures système)",
    "todo": "Liste de tâches personnelles",
    "notes": "Notes",
}


@dataclass(frozen=True)
class SettingField:
    """Réglage déclaratif d'une instance de widget."""

    key: str
    label: str
    kind: str  # bool | int | choice | text
    default: Any
    choices: tuple[tuple[str, str], ...] = ()  # (valeur, libellé)
    minimum: int = 0
    maximum: int = 100
    help: str = ""

    def validate(self, value: Any) -> Any:
        if self.kind == "bool":
            return value if isinstance(value, bool) else bool(self.default)
        if self.kind == "int":
            try:
                number = int(value)
            except (TypeError, ValueError):
                return self.default
            return max(self.minimum, min(self.maximum, number))
        if self.kind == "choice":
            values = {c[0] for c in self.choices}
            return value if value in values else self.default
        if self.kind == "text":
            return str(value if value is not None else self.default)[: self.maximum or 200]
        return self.default


@dataclass(frozen=True)
class WidgetSpec:
    type_id: str
    title: str
    description: str
    category: str
    factory: Callable[["WidgetContext", dict[str, Any]], "DashboardWidget"]
    min_size: tuple[int, int] = (3, 3)
    default_size: tuple[int, int] = (4, 4)
    max_size: tuple[int, int] | None = None
    capabilities: frozenset[str] = frozenset()
    dependencies: tuple[str, ...] = ()
    settings: tuple[SettingField, ...] = ()
    max_instances_per_page: int = 1
    icon: str = "◇"
    #: Identifiants de politiques de sécurité des actions proposées.
    actions: tuple[str, ...] = ()

    def validate_settings(self, raw: dict[str, Any] | None) -> dict[str, Any]:
        """Réglages validés ; les clés inconnues sont CONSERVÉES (compatibilité)."""
        data = dict(raw or {})
        for field_spec in self.settings:
            data[field_spec.key] = field_spec.validate(data.get(field_spec.key, field_spec.default))
        return data

    def contract_errors(self) -> list[str]:
        """Vérifications structurelles (utilisées par les tests du registre)."""
        errors: list[str] = []
        if not self.type_id or "." not in self.type_id:
            errors.append("type_id doit être de la forme 'domaine.nom'")
        if self.category not in CATEGORIES:
            errors.append(f"catégorie inconnue : {self.category}")
        if not self.title or not self.description:
            errors.append("titre et description obligatoires")
        mw, mh = self.min_size
        dw, dh = self.default_size
        if not (1 <= mw <= dw <= 12 and 1 <= mh <= dh <= 24):
            errors.append("tailles incohérentes (1 ≤ min ≤ défaut ≤ 12×24)")
        if self.max_size is not None and (self.max_size[0] < dw or self.max_size[1] < dh):
            errors.append("taille maximale inférieure à la taille par défaut")
        for cap in self.capabilities:
            if cap not in CAPABILITIES:
                errors.append(f"capacité inconnue : {cap}")
        for dep in self.dependencies:
            if dep not in DEPENDENCIES:
                errors.append(f"dépendance inconnue : {dep}")
        if "multi_instance" in self.capabilities and self.max_instances_per_page < 2:
            errors.append("multi_instance exige max_instances_per_page ≥ 2")
        keys = [f.key for f in self.settings]
        if len(keys) != len(set(keys)):
            errors.append("clés de réglages dupliquées")
        if self.settings and "settings" not in self.capabilities:
            errors.append("réglages déclarés sans la capacité 'settings'")
        return errors


@dataclass
class WidgetAction:
    """Action proposée par un widget (menu « ⋯ » du cadre)."""

    key: str
    label: str
    handler: Callable[[], None]
    enabled: bool = True
    reason: str = ""


@dataclass
class WidgetContext:
    """Accès d'un widget au reste du Dashboard (injecté, jamais global)."""

    hub: Any
    theme_manager: Any
    instance_id: str
    page_id: str = ""
    #: confirm(action_id, résumé, détails) -> bool (applique la politique).
    confirm: Callable[[str, str, str], bool] = lambda action_id, summary, details="": True
    #: toast(message, niveau) : retour visuel non bloquant.
    toast: Callable[[str, str], None] = lambda message, level="info": None
    open_page: Callable[[str], None] = lambda page_id: None
    open_path: Callable[[str], bool] = lambda path: False
    controller: Any = None
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def theme(self):
        return self.theme_manager.theme


class DashboardWidget(QWidget):
    """Classe de base de tous les widgets (vue Qt + cycle de vie)."""

    state_changed = Signal(str, str)
    title_changed = Signal(str)
    actions_changed = Signal()
    #: Demande de surbrillance du cadre (activité récente).
    pulse = Signal()

    spec: WidgetSpec | None = None

    def __init__(self, context: WidgetContext, settings: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.context = context
        self.settings = dict(settings or {})
        self._state = STATE_LOADING
        self._state_message = ""
        self._active = False
        self._disposed = False
        self._connections: list[tuple[Any, Callable]] = []
        self._timers: list[QTimer] = []
        self._subtitle = ""
        self.setObjectName(f"widget-{context.instance_id}")

    # -- états --------------------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def state_message(self) -> str:
        return self._state_message

    def set_state(self, state: str, message: str = "") -> None:
        if state not in STATES:
            state = STATE_ERROR
        if state == self._state and message == self._state_message:
            return
        self._state = state
        self._state_message = str(message or "")
        self.state_changed.emit(state, self._state_message)

    # -- cycle de vie ---------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        return self._active

    def activate(self) -> None:
        if self._active or self._disposed:
            return
        self._active = True
        try:
            self.on_activate()
        except Exception as exc:  # un widget défaillant n'emporte pas le Dashboard
            self.set_state(STATE_ERROR, f"Erreur d'activation : {exc}")

    def deactivate(self) -> None:
        if not self._active:
            return
        self._active = False
        try:
            self.on_deactivate()
        except Exception:
            pass
        self._release()

    def dispose(self) -> None:
        if self._disposed:
            return
        self.deactivate()
        try:
            self.on_dispose()
        except Exception:
            pass
        self._disposed = True

    def apply_settings(self, settings: dict[str, Any]) -> None:
        self.settings = dict(settings or {})
        try:
            self.on_settings_changed()
        except Exception as exc:
            self.set_state(STATE_ERROR, f"Réglages non appliqués : {exc}")

    # -- points d'extension (à surcharger) ----------------------------------------

    def on_activate(self) -> None:
        """Abonnements + premier chargement. Appeler ``self.refresh()`` si utile."""

    def on_deactivate(self) -> None:
        """Arrêts spécifiques (les abonnements/minuteurs sont libérés ensuite)."""

    def on_dispose(self) -> None:
        """Nettoyage final."""

    def on_settings_changed(self) -> None:
        if self._active:
            self.refresh()

    def on_theme_changed(self, theme) -> None:
        """Le thème a changé (les feuilles de style sont déjà appliquées)."""
        self.update()

    def refresh(self) -> None:
        """Recharge les données affichées."""

    def actions(self) -> list[WidgetAction]:
        return []

    def subtitle(self) -> str:
        return self._subtitle

    def set_subtitle(self, text: str) -> None:
        if text != self._subtitle:
            self._subtitle = text
            self.title_changed.emit(text)

    # -- utilitaires garantissant la libération -------------------------------------

    def listen(self, signal: Any, slot: Callable) -> None:
        """Connexion libérée automatiquement à la désactivation."""
        signal.connect(slot)
        self._connections.append((signal, slot))

    def every(self, interval_ms: int, callback: Callable[[], None], *, immediate: bool = False) -> QTimer:
        """Minuteur arrêté et détruit automatiquement à la désactivation."""
        timer = QTimer(self)
        timer.setInterval(max(16, int(interval_ms)))
        timer.timeout.connect(callback)
        timer.start()
        self._timers.append(timer)
        if immediate:
            callback()
        return timer

    def run_job(self, fn: Callable[..., Any], *args: Any, on_done: Callable[[Any], None] | None = None,
                on_error: Callable[[BaseException], None] | None = None, **kwargs: Any) -> int:
        """Exécute ``fn`` hors du thread graphique ; rappels ignorés si le widget
        a été détruit entre-temps."""
        return self.context.hub.jobs.submit(fn, *args, on_done=on_done, on_error=on_error, owner=self, **kwargs)

    def active_timer_count(self) -> int:
        return sum(1 for t in self._timers if t.isActive())

    def connection_count(self) -> int:
        return len(self._connections)

    def _release(self) -> None:
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._connections.clear()
        for timer in self._timers:
            timer.stop()
            timer.deleteLater()
        self._timers.clear()
