"""Machine d'états visuelle du Desktop Mode (v1.7.0) — sans Qt.

Pourquoi une machine d'états séparée
------------------------------------
Avant la 1.7.0, le cadre Desktop se contentait de traduire trois chaînes
(« listening », « thinking », « speaking ») en trois **intensités** de halo.
Rien ne distinguait visuellement « Jarvis réfléchit » de « Jarvis exécute une
action », et le retour en veille reposait uniquement sur un minuteur.

Cette machine :

* nomme explicitement chaque état visuel ;
* n'autorise que des transitions décrites ;
* est pilotée par les **évènements réels** du backend (hotword, transcription,
  appel d'outil, début/fin de TTS, interruption, erreur) — jamais par une
  supposition ;
* reste **pure Python** : aucun import Qt, aucun minuteur. Les minuteurs sont
  la responsabilité du contrôleur Qt, mais *leurs durées* sont définies ici,
  ce qui les rend testables sans interface.

Elle est volontairement petite : c'est une table de transitions, pas un
framework.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

# ---------------------------------------------------------------------------
# États visuels
# ---------------------------------------------------------------------------


class DesktopState:
    """États visuels du Desktop Mode (constantes de chaîne).

    Des chaînes plutôt qu'un ``Enum`` : elles sont sérialisables, comparables
    et **compatibles** avec les valeurs de présence historiques utilisées par
    ``AudioIO`` et par les tests de la 1.5.3/1.6.0 (``hidden``, ``listening``,
    ``thinking``, ``speaking``).
    """

    HIDDEN = "hidden"
    LOADING = "loading"
    LISTENING = "listening"
    THINKING = "thinking"
    TOOL_USE = "tool_use"
    SPEAKING = "speaking"
    FOLLOW_UP = "follow_up"
    INTERRUPTED = "interrupted"
    ERROR = "error"


#: Ordre canonique (utilisé par l'éditeur d'apparence et la documentation).
ALL_STATES: tuple[str, ...] = (
    DesktopState.HIDDEN,
    DesktopState.LOADING,
    DesktopState.LISTENING,
    DesktopState.THINKING,
    DesktopState.TOOL_USE,
    DesktopState.SPEAKING,
    DesktopState.FOLLOW_UP,
    DesktopState.INTERRUPTED,
    DesktopState.ERROR,
)

#: États pendant lesquels quelque chose est visible à l'écran. ``HIDDEN`` est
#: le seul état réellement « coût zéro » : aucune animation, aucun widget.
VISIBLE_STATES: tuple[str, ...] = tuple(s for s in ALL_STATES if s != DesktopState.HIDDEN)

#: États personnalisables dans l'éditeur (``LOADING`` et ``INTERRUPTED`` sont
#: trop brefs pour mériter une ligne de configuration : ils héritent de
#: ``LISTENING`` / ``SPEAKING``).
CONFIGURABLE_STATES: tuple[str, ...] = (
    DesktopState.LISTENING,
    DesktopState.THINKING,
    DesktopState.TOOL_USE,
    DesktopState.SPEAKING,
    DesktopState.FOLLOW_UP,
    DesktopState.ERROR,
)

#: Libellés courts affichés par le widget « status » (français, comme le
#: reste de l'interface utilisateur de Jarvis).
STATE_LABELS: dict[str, str] = {
    DesktopState.HIDDEN: "",
    DesktopState.LOADING: "Initialisation",
    DesktopState.LISTENING: "À l'écoute",
    DesktopState.THINKING: "Réflexion",
    DesktopState.TOOL_USE: "Action",
    DesktopState.SPEAKING: "Réponse",
    DesktopState.FOLLOW_UP: "Toujours à l'écoute",
    DesktopState.INTERRUPTED: "Interrompu",
    DesktopState.ERROR: "Incident",
}


# ---------------------------------------------------------------------------
# Évènements du backend
# ---------------------------------------------------------------------------


class DesktopEvent:
    """Vocabulaire d'évènements que le backend peut émettre.

    C'est l'**abstraction propre** demandée pour ne pas deviner l'état :
    chaque entrée correspond à un fait observable dans le pipeline vocal.
    """

    LOADING = "loading"                # modèle wake word en cours de chargement
    READY = "ready"                    # backend prêt, retour en veille
    HOTWORD = "hotword"                # « Hey Jarvis » détecté
    LISTENING = "listening"            # micro ouvert (réveil ou écoute continue)
    TRANSCRIPT = "transcript"          # fragment de transcription utilisateur
    USER_TURN_END = "user_turn_end"    # la demande est complète
    THINKING = "thinking"              # traitement côté modèle
    TOOL_START = "tool_start"          # exécution d'un outil
    TOOL_END = "tool_end"              # outil terminé
    RESPONSE = "response"              # texte de réponse (transcription sortie)
    TTS_START = "tts_start"            # Jarvis commence à parler
    TTS_END = "tts_end"                # fin du tour de Jarvis
    FOLLOW_UP = "follow_up"            # fenêtre d'écoute après réponse
    INTERRUPTED = "interrupted"        # l'utilisateur a coupé la parole
    ERROR = "error"                    # incident backend
    SLEEP = "sleep"                    # retour en veille explicite
    RESET = "reset"                    # remise à zéro (changement de mode…)


#: Correspondance entre les valeurs de présence **historiques** (1.2.0 → 1.6.0,
#: ``AudioIO.presence_hook``) et le nouveau vocabulaire. Elle garantit que le
#: pipeline existant continue de piloter le Desktop Mode sans modification.
PRESENCE_TO_EVENT: dict[str, str] = {
    "loading": DesktopEvent.LOADING,
    "listening": DesktopEvent.LISTENING,
    "thinking": DesktopEvent.THINKING,
    "speaking": DesktopEvent.TTS_START,
    "hidden": DesktopEvent.SLEEP,
    "hide_overlay": DesktopEvent.SLEEP,
    "idle": DesktopEvent.SLEEP,
    "error": DesktopEvent.ERROR,
}


# ---------------------------------------------------------------------------
# Durées (secondes) — définies ici pour être testables sans Qt
# ---------------------------------------------------------------------------

#: Filet de sécurité : durée maximale pendant laquelle le halo reste en
#: ``LISTENING`` sans autre évènement. Alignée sur ``AudioIO.FOLLOW_UP_SECONDS``
#: (8 s) plus une marge, comme en 1.5.3.
LISTENING_TIMEOUT = 8.4

#: Fenêtre « Jarvis attend une nouvelle demande » après une réponse.
FOLLOW_UP_TIMEOUT = 8.4

#: ``ERROR`` et ``INTERRUPTED`` sont volontairement **brefs** : Jarvis ne doit
#: jamais rester bloqué visuellement sur un incident.
ERROR_TIMEOUT = 2.2
INTERRUPTED_TIMEOUT = 1.1

#: ``LOADING`` ne doit pas s'éterniser si le backend ne répond jamais.
LOADING_TIMEOUT = 25.0

#: Durée d'auto-effacement des états, ``None`` = pas de minuteur.
STATE_TIMEOUTS: dict[str, float | None] = {
    DesktopState.HIDDEN: None,
    DesktopState.LOADING: LOADING_TIMEOUT,
    DesktopState.LISTENING: LISTENING_TIMEOUT,
    DesktopState.THINKING: None,
    DesktopState.TOOL_USE: None,
    DesktopState.SPEAKING: None,
    DesktopState.FOLLOW_UP: FOLLOW_UP_TIMEOUT,
    DesktopState.INTERRUPTED: INTERRUPTED_TIMEOUT,
    DesktopState.ERROR: ERROR_TIMEOUT,
}

#: État atteint quand le minuteur d'un état expire.
STATE_TIMEOUT_TARGET: dict[str, str] = {
    DesktopState.LOADING: DesktopState.HIDDEN,
    DesktopState.LISTENING: DesktopState.HIDDEN,
    DesktopState.FOLLOW_UP: DesktopState.HIDDEN,
    DesktopState.INTERRUPTED: DesktopState.HIDDEN,
    DesktopState.ERROR: DesktopState.HIDDEN,
}


@dataclass(frozen=True)
class Transition:
    """Résultat d'un évènement traité par la machine."""

    previous: str
    current: str
    event: str
    payload: dict[str, Any]

    @property
    def changed(self) -> bool:
        return self.previous != self.current


class DesktopStateMachine:
    """Traduit les évènements du backend en états visuels.

    La machine est **tolérante** : un évènement inconnu ou hors séquence ne
    lève jamais, il est simplement ignoré (le pipeline vocal ne doit jamais
    tomber à cause de l'interface). Elle est aussi **déterministe** : la même
    séquence d'évènements produit toujours la même séquence d'états, ce que
    les tests vérifient.
    """

    #: Nombre de transitions conservées pour le diagnostic.
    HISTORY = 24

    def __init__(self, on_transition: Callable[[Transition], None] | None = None) -> None:
        self._state = DesktopState.HIDDEN
        self._previous = DesktopState.HIDDEN
        self._on_transition = on_transition
        self._history: list[Transition] = []
        #: Nom du dernier outil signalé (affiché par le widget « tool »).
        self.tool_name = ""
        #: Compteur d'outils actifs : un tour peut en enchaîner plusieurs.
        self._tool_depth = 0

    # -- lecture ------------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def previous(self) -> str:
        return self._previous

    def history(self) -> tuple[Transition, ...]:
        return tuple(self._history)

    def is_visible(self) -> bool:
        return self._state != DesktopState.HIDDEN

    # -- écriture -----------------------------------------------------------

    def reset(self) -> Transition:
        """Remet la machine en veille (changement de mode, fermeture…)."""
        self.tool_name = ""
        self._tool_depth = 0
        return self._apply(DesktopState.HIDDEN, DesktopEvent.RESET, {})

    def handle(self, event: str, payload: dict[str, Any] | None = None) -> Transition | None:
        """Traite un évènement. Retourne la transition, ou ``None`` si ignoré."""
        data = dict(payload or {})
        target = self._target_for(str(event or ""), data)
        if target is None:
            return None
        return self._apply(target, str(event), data)

    def handle_presence(self, presence: str) -> Transition | None:
        """Entrée de compatibilité : valeurs de ``AudioIO.presence_hook``."""
        event = PRESENCE_TO_EVENT.get(str(presence or "").strip().lower())
        if event is None:
            return None
        return self.handle(event)

    def timeout(self) -> Transition | None:
        """Le minuteur de l'état courant a expiré."""
        target = STATE_TIMEOUT_TARGET.get(self._state)
        if target is None:
            return None
        return self._apply(target, "timeout", {})

    def timeout_seconds(self) -> float | None:
        """Durée du minuteur de l'état courant (``None`` = aucun)."""
        return STATE_TIMEOUTS.get(self._state)

    # -- interne ------------------------------------------------------------

    def _target_for(self, event: str, payload: dict[str, Any]) -> str | None:
        state = self._state

        if event == DesktopEvent.RESET:
            self.tool_name = ""
            self._tool_depth = 0
            return DesktopState.HIDDEN

        if event == DesktopEvent.LOADING:
            return DesktopState.LOADING if state == DesktopState.HIDDEN else None

        if event in (DesktopEvent.READY, DesktopEvent.SLEEP):
            self.tool_name = ""
            self._tool_depth = 0
            return DesktopState.HIDDEN

        if event == DesktopEvent.HOTWORD:
            # Feedback IMMÉDIAT : le hotword prime sur tout état en cours.
            self.tool_name = ""
            self._tool_depth = 0
            return DesktopState.LISTENING

        if event == DesktopEvent.LISTENING:
            self._tool_depth = 0
            self.tool_name = ""
            # Après une réponse, « listening » signifie « j'attends la suite » :
            # c'est FOLLOW_UP, un état distinct et explicite pour l'utilisateur.
            if state in (DesktopState.SPEAKING, DesktopState.THINKING,
                         DesktopState.TOOL_USE, DesktopState.INTERRUPTED):
                return DesktopState.FOLLOW_UP
            if state == DesktopState.FOLLOW_UP:
                # Relance de la fenêtre : on reste en FOLLOW_UP mais le
                # contrôleur redémarre son minuteur (transition « identique »).
                return DesktopState.FOLLOW_UP
            return DesktopState.LISTENING

        if event == DesktopEvent.TRANSCRIPT:
            # La transcription confirme que l'utilisateur parle : on ne quitte
            # pas l'écoute, mais on relance sa fenêtre.
            if state in (DesktopState.LISTENING, DesktopState.FOLLOW_UP):
                return state
            if state in (DesktopState.HIDDEN, DesktopState.LOADING):
                return DesktopState.LISTENING
            return None

        if event == DesktopEvent.USER_TURN_END:
            if state in (DesktopState.LISTENING, DesktopState.FOLLOW_UP):
                return DesktopState.THINKING
            return None

        if event == DesktopEvent.THINKING:
            if state in (DesktopState.TOOL_USE,):
                return None
            return DesktopState.THINKING

        if event == DesktopEvent.TOOL_START:
            name = str(payload.get("name") or "").strip()
            if name:
                self.tool_name = name
            self._tool_depth += 1
            return DesktopState.TOOL_USE

        if event == DesktopEvent.TOOL_END:
            self._tool_depth = max(0, self._tool_depth - 1)
            if self._tool_depth:
                return None
            self.tool_name = ""
            # L'outil est fini mais Jarvis n'a pas encore parlé : il « réfléchit »
            # à sa réponse. On ne repasse jamais directement en HIDDEN ici.
            if state == DesktopState.TOOL_USE:
                return DesktopState.THINKING
            return None

        if event == DesktopEvent.RESPONSE:
            # Texte de réponse disponible : si Jarvis ne parle pas encore, on
            # anticipe l'état SPEAKING pour que l'affichage soit immédiat.
            if state in (DesktopState.THINKING, DesktopState.TOOL_USE):
                return DesktopState.SPEAKING
            return None

        if event == DesktopEvent.TTS_START:
            self._tool_depth = 0
            self.tool_name = ""
            return DesktopState.SPEAKING

        if event == DesktopEvent.TTS_END:
            if state in (DesktopState.SPEAKING, DesktopState.THINKING, DesktopState.TOOL_USE):
                return DesktopState.FOLLOW_UP
            return None

        if event == DesktopEvent.FOLLOW_UP:
            return DesktopState.FOLLOW_UP

        if event == DesktopEvent.INTERRUPTED:
            if state == DesktopState.HIDDEN:
                return None
            return DesktopState.INTERRUPTED

        if event == DesktopEvent.ERROR:
            return DesktopState.ERROR

        return None

    def _apply(self, target: str, event: str, payload: dict[str, Any]) -> Transition:
        previous = self._state
        self._previous = previous
        self._state = target
        transition = Transition(previous=previous, current=target, event=event, payload=payload)
        self._history.append(transition)
        if len(self._history) > self.HISTORY:
            del self._history[: len(self._history) - self.HISTORY]
        if self._on_transition is not None:
            try:
                self._on_transition(transition)
            except Exception:
                # L'interface ne doit jamais faire tomber la machine d'états.
                pass
        return transition


def normalize_state(value: Any, default: str = DesktopState.HIDDEN) -> str:
    """Normalise une chaîne d'état (tolérante aux alias historiques)."""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in ALL_STATES:
        return text
    aliases = {
        "tool": DesktopState.TOOL_USE,
        "tooluse": DesktopState.TOOL_USE,
        "listening_after_reply": DesktopState.FOLLOW_UP,
        "followup": DesktopState.FOLLOW_UP,
        "idle": DesktopState.HIDDEN,
        "hide_overlay": DesktopState.HIDDEN,
    }
    return aliases.get(text, default)
