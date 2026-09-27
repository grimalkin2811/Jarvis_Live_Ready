"""Configuration du Desktop Mode (v1.7.0) — pure donnée, sans Qt.

Où c'est persisté
-----------------
**Nulle part ailleurs que dans le fichier d'apparence existant.** Le bloc
``desktop`` est ajouté à ``UI/appearance_state.json``
(``%LOCALAPPDATA%\\Jarvis\\ui\\appearance_state.json``), à côté du thème, du
glow et de l'opacité des fonds d'items. Aucun nouveau fichier n'est créé :
l'apparence du Desktop Mode *est* de l'apparence.

Compatibilité
-------------
* Un fichier écrit par la 1.6.0 n'a pas de clé ``desktop`` : les défauts
  s'appliquent, et l'utilisateur obtient directement une interface soignée.
* Une clé inconnue d'une version future est ignorée sans erreur.
* ``schema_version`` permet une migration explicite (``migrate_payload``).

Modèle de position
------------------
Chaque widget est placé par un **centre normalisé** ``(x, y)`` dans ``[0, 1]``
de l'écran, plus une échelle. C'est indépendant de la résolution, du DPI et du
nombre d'écrans : déplacer Jarvis d'un 1080p vers un 4K conserve la
composition. L'ancrage 3×3 n'est qu'une aide de l'éditeur (aimantation), pas
une donnée séparée à maintenir.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterable

from .state import CONFIGURABLE_STATES, DesktopState, normalize_state

#: Version du schéma sérialisé. À incrémenter uniquement si une migration
#: devient nécessaire (voir ``migrate_payload``).
SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Widgets disponibles
# ---------------------------------------------------------------------------

HALO = "halo"
STATUS = "status"
TRANSCRIPT = "transcript"
RESPONSE = "response"
TOOL = "tool"
AUDIO = "audio"
CONTROLS = "controls"

#: Ordre d'affichage dans l'éditeur.
WIDGET_ORDER: tuple[str, ...] = (HALO, STATUS, TRANSCRIPT, RESPONSE, TOOL, AUDIO, CONTROLS)

#: Libellé + description courte (éditeur d'apparence).
WIDGET_LABELS: dict[str, tuple[str, str]] = {
    HALO: ("Halo", "Lumière périphérique : la présence de Jarvis."),
    STATUS: ("État", "Pastille indiquant ce que Jarvis est en train de faire."),
    TRANSCRIPT: ("Transcription", "Ce que Jarvis a compris de votre demande."),
    RESPONSE: ("Réponse", "Résumé court de la réponse de Jarvis."),
    TOOL: ("Outil", "Nom de l'action en cours d'exécution."),
    AUDIO: ("Visualiseur", "Petit indicateur de niveau audio."),
    CONTROLS: ("Contrôles", "Boutons Stop / Micro / Masquer (widget cliquable)."),
}

#: Widgets qui reçoivent des clics quand ils sont activés. Le halo et les
#: indicateurs restent toujours traversants.
INTERACTIVE_WIDGETS: frozenset[str] = frozenset({CONTROLS})

# ---------------------------------------------------------------------------
# Politique d'interaction
# ---------------------------------------------------------------------------

INTERACTION_ALWAYS = "always"          # tout traverse, même les contrôles
INTERACTION_WIDGETS = "widgets"        # défaut : seuls les widgets interactifs captent
INTERACTION_FULL = "interactive"       # l'overlay entier capte la souris
INTERACTION_MODES: tuple[str, ...] = (INTERACTION_ALWAYS, INTERACTION_WIDGETS, INTERACTION_FULL)

INTERACTION_LABELS: dict[str, str] = {
    INTERACTION_ALWAYS: "Toujours traversable",
    INTERACTION_WIDGETS: "Widgets interactifs uniquement",
    INTERACTION_FULL: "Overlay interactif",
}


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(low, min(high, number))


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", "non", ""}


def normalize_interaction(value: Any, default: str = INTERACTION_WIDGETS) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "always": INTERACTION_ALWAYS,
        "click_through": INTERACTION_ALWAYS,
        "clickthrough": INTERACTION_ALWAYS,
        "passthrough": INTERACTION_ALWAYS,
        "widgets": INTERACTION_WIDGETS,
        "widgets_only": INTERACTION_WIDGETS,
        "interactive": INTERACTION_FULL,
        "full": INTERACTION_FULL,
    }
    return aliases.get(text, default if default in INTERACTION_MODES else INTERACTION_WIDGETS)


@dataclass
class WidgetSlot:
    """Réglages d'un élément de l'overlay."""

    enabled: bool = True
    #: Centre du widget, normalisé sur l'écran (0..1).
    x: float = 0.5
    y: float = 0.5
    #: Facteur d'échelle appliqué à la taille de base.
    scale: float = 1.0
    #: États dans lesquels l'élément est visible.
    states: tuple[str, ...] = field(default_factory=tuple)

    def visible_in(self, state: str) -> bool:
        return self.enabled and normalize_state(state) in self.states

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "x": round(float(self.x), 4),
            "y": round(float(self.y), 4),
            "scale": round(float(self.scale), 3),
            "states": list(self.states),
        }

    @classmethod
    def from_dict(cls, payload: Any, default: "WidgetSlot") -> "WidgetSlot":
        if not isinstance(payload, dict):
            return replace(default)
        states = payload.get("states")
        if isinstance(states, (list, tuple)):
            cleaned = tuple(
                dict.fromkeys(
                    normalize_state(item)
                    for item in states
                    if normalize_state(item, "") in CONFIGURABLE_STATES
                )
            )
        else:
            cleaned = default.states
        return cls(
            enabled=_bool(payload.get("enabled"), default.enabled),
            x=_clamp(payload.get("x"), 0.02, 0.98, default.x),
            y=_clamp(payload.get("y"), 0.02, 0.98, default.y),
            scale=_clamp(payload.get("scale"), 0.6, 2.0, default.scale),
            states=cleaned,
        )


def _default_slots() -> dict[str, WidgetSlot]:
    """Défauts « premium » : beau sans ouvrir l'éditeur, jamais envahissant.

    Décisions assumées :

    * **Réponse désactivée** par défaut — Jarvis parle ; afficher en plus tout
      ce qu'il dit transformerait l'overlay en chatbot flottant.
    * **Contrôles désactivés** par défaut — tant qu'ils sont éteints, l'overlay
      est intégralement traversant : aucune surprise possible sur le bureau.
    * Tout le reste est allumé : halo, état, transcription, outil, visualiseur.
    """
    active = (
        DesktopState.LISTENING,
        DesktopState.THINKING,
        DesktopState.TOOL_USE,
        DesktopState.SPEAKING,
        DesktopState.FOLLOW_UP,
        DesktopState.ERROR,
    )
    return {
        HALO: WidgetSlot(enabled=True, x=0.5, y=0.5, scale=1.0, states=active),
        STATUS: WidgetSlot(enabled=True, x=0.5, y=0.952, scale=1.0, states=active),
        TRANSCRIPT: WidgetSlot(
            enabled=True,
            x=0.5,
            y=0.838,
            scale=1.0,
            states=(DesktopState.LISTENING, DesktopState.FOLLOW_UP, DesktopState.THINKING),
        ),
        RESPONSE: WidgetSlot(
            enabled=False,
            x=0.5,
            y=0.838,
            scale=1.0,
            states=(DesktopState.SPEAKING,),
        ),
        TOOL: WidgetSlot(
            enabled=True,
            x=0.5,
            y=0.838,
            scale=1.0,
            states=(DesktopState.TOOL_USE,),
        ),
        AUDIO: WidgetSlot(
            enabled=True,
            x=0.5,
            y=0.888,
            scale=1.0,
            states=(DesktopState.LISTENING, DesktopState.SPEAKING, DesktopState.FOLLOW_UP),
        ),
        CONTROLS: WidgetSlot(
            enabled=False,
            x=0.93,
            y=0.94,
            scale=1.0,
            states=(DesktopState.SPEAKING, DesktopState.THINKING, DesktopState.TOOL_USE),
        ),
    }


@dataclass
class DesktopAppearanceConfig:
    """Tout ce que l'utilisateur peut régler pour le Desktop Mode."""

    schema_version: int = SCHEMA_VERSION
    #: Politique d'interception de la souris.
    interaction: str = INTERACTION_WIDGETS
    #: Réduit les animations sans casser les états (accessibilité).
    reduced_motion: bool = False
    #: Force globale du halo.
    intensity: float = 1.0
    #: Épaisseur de la lumière périphérique.
    thickness: float = 1.0
    #: Le halo et le visualiseur réagissent au niveau audio réel.
    audio_reactive: bool = True
    #: Taille du texte des widgets (accessibilité / écrans 4K).
    text_scale: float = 1.0
    #: Durées d'affichage des cartes temporaires (secondes).
    transcript_seconds: float = 6.0
    response_seconds: float = 7.0
    slots: dict[str, WidgetSlot] = field(default_factory=_default_slots)

    # -- lecture ------------------------------------------------------------

    def slot(self, name: str) -> WidgetSlot:
        return self.slots.get(name) or WidgetSlot(enabled=False, states=())

    def is_visible(self, name: str, state: str) -> bool:
        """Le widget ``name`` doit-il être visible dans l'état ``state`` ?"""
        return self.slot(name).visible_in(state)

    def enabled_widgets(self) -> tuple[str, ...]:
        return tuple(name for name in WIDGET_ORDER if self.slot(name).enabled)

    def has_interactive_widgets(self) -> bool:
        """Vrai si au moins un widget cliquable est activé."""
        if self.interaction == INTERACTION_ALWAYS:
            return False
        if self.interaction == INTERACTION_FULL:
            return True
        return any(self.slot(name).enabled for name in INTERACTIVE_WIDGETS)

    def click_through(self) -> bool:
        """Vrai si l'overlay principal doit laisser passer tous les clics."""
        return self.interaction != INTERACTION_FULL

    def motion_scale(self) -> float:
        """Facteur appliqué aux amplitudes d'animation (1.0 = normal)."""
        return 0.35 if self.reduced_motion else 1.0

    # -- écriture -----------------------------------------------------------

    def set_slot(self, name: str, **changes: Any) -> WidgetSlot:
        """Met à jour un slot et retourne sa nouvelle valeur (validée)."""
        current = self.slots.get(name)
        if current is None:
            current = WidgetSlot(enabled=False, states=())
        payload = current.to_dict()
        payload.update(changes)
        updated = WidgetSlot.from_dict(payload, current)
        self.slots[name] = updated
        return updated

    def set_state_visibility(self, name: str, state: str, visible: bool) -> tuple[str, ...]:
        """Active/désactive un widget pour un état donné."""
        target = normalize_state(state, "")
        if target not in CONFIGURABLE_STATES:
            return self.slot(name).states
        current = list(self.slot(name).states)
        if visible and target not in current:
            current.append(target)
        elif not visible and target in current:
            current.remove(target)
        ordered = tuple(s for s in CONFIGURABLE_STATES if s in current)
        self.set_slot(name, states=list(ordered))
        return ordered

    def reset(self) -> None:
        """Rétablit intégralement les valeurs d'origine."""
        fresh = DesktopAppearanceConfig()
        self.schema_version = fresh.schema_version
        self.interaction = fresh.interaction
        self.reduced_motion = fresh.reduced_motion
        self.intensity = fresh.intensity
        self.thickness = fresh.thickness
        self.audio_reactive = fresh.audio_reactive
        self.text_scale = fresh.text_scale
        self.transcript_seconds = fresh.transcript_seconds
        self.response_seconds = fresh.response_seconds
        self.slots = fresh.slots

    # -- sérialisation ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "interaction": self.interaction,
            "reduced_motion": bool(self.reduced_motion),
            "intensity": round(float(self.intensity), 3),
            "thickness": round(float(self.thickness), 3),
            "audio_reactive": bool(self.audio_reactive),
            "text_scale": round(float(self.text_scale), 3),
            "transcript_seconds": round(float(self.transcript_seconds), 2),
            "response_seconds": round(float(self.response_seconds), 2),
            "widgets": {name: self.slots[name].to_dict() for name in WIDGET_ORDER if name in self.slots},
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "DesktopAppearanceConfig":
        config = cls()
        if not isinstance(payload, dict):
            return config
        data = migrate_payload(payload)
        config.interaction = normalize_interaction(data.get("interaction"), config.interaction)
        config.reduced_motion = _bool(data.get("reduced_motion"), config.reduced_motion)
        config.intensity = _clamp(data.get("intensity"), 0.3, 1.6, config.intensity)
        config.thickness = _clamp(data.get("thickness"), 0.5, 1.8, config.thickness)
        config.audio_reactive = _bool(data.get("audio_reactive"), config.audio_reactive)
        config.text_scale = _clamp(data.get("text_scale"), 0.8, 1.8, config.text_scale)
        config.transcript_seconds = _clamp(data.get("transcript_seconds"), 2.0, 30.0, config.transcript_seconds)
        config.response_seconds = _clamp(data.get("response_seconds"), 2.0, 30.0, config.response_seconds)
        widgets = data.get("widgets")
        if isinstance(widgets, dict):
            defaults = _default_slots()
            for name in WIDGET_ORDER:
                config.slots[name] = WidgetSlot.from_dict(widgets.get(name), defaults[name])
        return config


def migrate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Amène un bloc ``desktop`` sérialisé au schéma courant.

    La 1.7.0 est le premier schéma : la seule « migration » possible est
    l'absence de bloc (fichier 1.6.0 ou antérieur), traitée par les défauts.
    La fonction existe pour que l'ajout d'une v2 ne se fasse pas à coups de
    ``if`` dispersés dans ``from_dict``.
    """
    data = dict(payload)
    try:
        version = int(data.get("schema_version", 0))
    except (TypeError, ValueError):
        version = 0
    if version > SCHEMA_VERSION:
        # Fichier écrit par une version plus récente : on garde ce qu'on sait
        # lire plutôt que de tout jeter.
        pass
    data["schema_version"] = SCHEMA_VERSION
    return data


def states_summary(states: Iterable[str]) -> str:
    """Résumé lisible des états d'un widget (utilisé par l'éditeur)."""
    from .state import STATE_LABELS

    names = [STATE_LABELS.get(state, state) for state in states]
    if not names:
        return "jamais"
    if len(names) >= len(CONFIGURABLE_STATES):
        return "tous les états"
    return ", ".join(names)
