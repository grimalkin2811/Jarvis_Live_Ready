import asyncio
import collections
import os
import threading
import time

from google import genai
from google.genai import types

from .conversation import (
    ConversationContext,
    get_default_conversation_context,
    is_new_conversation_command,
    to_gemini_contents,
)
from .logging_setup import get_logger
from .memory import MemoryManager
from .modes import get_default_mode_manager
from .tools import TOOL_DECLARATIONS, TOOL_FUNCTIONS
from .writing.service import WRITING_TOOL_NAMES, system_instruction as writing_system_instruction

log = get_logger("gemini")


#: Durée pendant laquelle une interruption demandée localement (« stop »)
#: reste active : le micro est réouvert vers Gemini et l'audio du modèle est
#: jeté, le temps que le serveur confirme l'interruption. Si Gemini ne
#: s'arrête pas (fausse détection), la lecture reprend au bout de ce délai
#: plutôt que de laisser Jarvis muet.
INTERRUPT_WINDOW_SECONDS = 4.0


#: Taille de l'historique de traçage (instrumentation du cycle de tour,
#: v1.7.3) : chaque envoi micro, ouverture/fermeture de tour et abandon
#: d'audio périmé doit pouvoir être expliqué a posteriori, exactement comme
#: ``AudioIO._trace`` (v1.7.2). Les deux traces partagent le même
#: interrupteur (``JARVIS_AUDIO_TRACE``) pour ne pas multiplier les
#: variables d'environnement de diagnostic.
GEMINI_TRACE_MAX_EVENTS = 512


def _trace_enabled() -> bool:
    return os.getenv("JARVIS_AUDIO_TRACE", "").strip() not in ("", "0", "false", "non")


class AuthError(RuntimeError):
    """Erreur d'authentification Gemini : inutile de réessayer en boucle."""


def _is_auth_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    markers = (
        "api key",
        "api_key",
        "permission_denied",
        "unauthenticated",
        "unauthorized",
        "401",
        "403",
    )
    return any(marker in text for marker in markers)


def _is_invalid_handle_error(exc: BaseException) -> bool:
    """Vrai si l'erreur signifie « handle de reprise refusé/expiré ».

    Constat googleapis/python-genai#2197 : un handle invalide produit une
    ``APIError 1007 … Invalid session handle`` À LA CONNEXION (pas de session
    vide silencieuse). Le repli correct est alors une session neuve + rejeu
    du contexte local, jamais une boucle de reconnexions avec le même handle.
    """
    if getattr(exc, "code", None) == 1007:
        return True
    text = str(exc).lower()
    return "1007" in text or "invalid session handle" in text


def _env_flag(name: str, default: bool = True) -> bool:
    """Lit un drapeau booléen d'environnement (kill-switch de secours)."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", "non"}


#: Mode de rejeu du contexte dans une session Gemini neuve.
#: * ``commit`` (défaut) : l'historique est envoyé en ``clientContent``
#:   clôturé (``turn_complete=True``) et se termine par un tour user —
#:   protocole validé en production (pipecat) : sur les modèles audio 2.x le
#:   modèle produit une brève reprise puis RAPPELLE l'historique aux tours
#:   audio suivants ; sur 3.x avec ``historyConfig`` le commit est silencieux.
#: * ``pending`` : ancien comportage v1.6/1.7 (``turn_complete=False``),
#:   conservé uniquement pour comparaison — le premier tour audio suivant
#:   « ignore » l'historique (régression documentée).
#: * ``off`` : aucun rejeu (le contexte ne survit qu'à la reprise de session).
SEED_MODE = os.getenv("JARVIS_LIVE_SEED_MODE", "commit").strip().lower() or "commit"

#: ``historyConfig.initialHistoryInClientContent`` : protocole documenté de
#: l'historique initial (requis pour seeder les modèles 3.x, inoffensif sur
#: 2.x où le commit explicite reste nécessaire). Kill-switch :
#: ``JARVIS_LIVE_HISTORY_CONFIG=0``.
HISTORY_CONFIG_ENABLED = _env_flag("JARVIS_LIVE_HISTORY_CONFIG", True)

#: Compression de fenêtre de contexte (sliding window) : sans elle, une
#: session AUDIO est terminée au bout de ~15 minutes (documentation Live API),
#: ce qui détruit le contexte serveur. Kill-switch : ``JARVIS_LIVE_COMPRESSION=0``.
CONTEXT_COMPRESSION_ENABLED = _env_flag("JARVIS_LIVE_COMPRESSION", True)

#: Délai maximal pour attendre que le serveur confirme avoir réellement
#: TRAITÉ le rejeu de contexte (son propre tour de clôture, observé via
#: ``session.receive()``) avant d'autoriser le micro à parler du tour
#: suivant.
#:
#: Cause racine (audit v1.7.5, échec du scénario S6 contre l'API réelle) :
#: ``send_client_content(turn_complete=True)`` est un simple envoi réseau —
#: il revient dès que le client a ÉCRIT sur le WebSocket, PAS quand le
#: serveur a fini de traiter ce tour. Sur les modèles audio 2.x, ce tour
#: clôturé déclenche une inférence (le modèle produit une brève reprise,
#: cf. ``_seed_context``) : tant que cette inférence n'est pas terminée
#: (son propre ``turn_complete`` reçu), le serveur est encore « occupé » par
#: le tour de rejeu. Avant ce correctif, Jarvis ouvrait la porte audio dès
#: l'ENVOI du rejeu (``context_seeded=True``) sans jamais vérifier qu'il
#: avait été COMMITÉ : le micro pouvait alors livrer la question réelle
#: suivante PENDANT que le serveur générait encore sa reprise, produisant un
#: tour corrompu (réponse vide ou hors-sujet) — exactement le symptôme
#: observé en validation réelle (scénario S6 : « Quel est mon prénom ? » ->
#: réponse=''), alors que le faux serveur de test ne pouvait PAS reproduire
#: la course (il génère sa réponse de façon synchrone à l'intérieur même de
#: l'appel ``send_client_content``, ce qu'un vrai WebSocket ne fait jamais).
#:
#: Sur les modèles 3.x avec ``historyConfig`` (commit silencieux, aucune
#: inférence déclenchée par le rejeu), cette attente expire normalement
#: SANS réponse : c'est le comportement attendu, pas une erreur — la porte
#: s'ouvre quand même après le délai (dégradation contrôlée), et
#: ``context_seed_confirmed`` reste simplement False pour dire honnêtement
#: qu'aucune confirmation explicite n'a été observée.
SEED_COMMIT_TIMEOUT_SECONDS = float(os.getenv("JARVIS_LIVE_SEED_COMMIT_TIMEOUT", "5") or 5)


class GeminiLive:
    def __init__(
        self,
        key,
        model,
        user,
        on_audio,
        on_turn_complete=None,
        on_interrupted=None,
        on_speaking=None,
        on_thinking=None,
        on_tool_start=None,
        on_tool_end=None,
        on_user_transcript=None,
        on_assistant_transcript=None,
        response_mode_provider=None,
        voice_provider=None,
        voice_version_provider=None,
        speech_pace_provider=None,
        memory_manager: MemoryManager | None = None,
        conversation: ConversationContext | None = None,
    ):
        self.client = genai.Client(api_key=key)
        self.model = model
        self.user = user

        self.on_audio = on_audio
        self.on_turn_complete = on_turn_complete
        self.on_interrupted = on_interrupted
        self.on_speaking = on_speaking
        self.on_thinking = on_thinking
        # v1.7.0 — évènements fins destinés au retour visuel (Desktop Mode).
        # Tous optionnels : sans eux, le comportement est **identique** à la
        # 1.6.0. Ils exposent des faits déjà connus du pipeline (un outil
        # démarre, la transcription a avancé) plutôt que d'obliger l'interface
        # à les deviner avec des minuteurs.
        self.on_tool_start = on_tool_start
        self.on_tool_end = on_tool_end
        self.on_user_transcript = on_user_transcript
        self.on_assistant_transcript = on_assistant_transcript
        # Fournit le mode de réponse courant (menu radial) pour le prompt système.
        self.response_mode_provider = response_mode_provider
        # Fournit la voix prébuilt Gemini (menu radial). La voix ne peut pas
        # changer en cours de session : le watcher ci-dessous déclenche une
        # reconnexion douce quand l'utilisateur en choisit une autre.
        self.voice_provider = voice_provider
        self.voice_version_provider = voice_version_provider
        # Fournit la consigne de débit ("posé" / "normal" / "vif").
        self.speech_pace_provider = speech_pace_provider
        self.memory_manager = memory_manager

        # Contexte conversationnel multi-tour (v1.6.0). Il appartient à Jarvis,
        # pas au fournisseur : la session Gemini n'en est qu'un consommateur.
        self.conversation = (
            conversation if conversation is not None else get_default_conversation_context()
        )

        self.session = None
        self.ctx = None
        self.speaking = False
        self.tool_active = False
        self.resumption_handle = None
        # Traçabilité de session (instrumentation du contexte conversationnel) :
        # ``session_generation`` distingue chaque session Gemini successive,
        # ``session_id`` est l'identifiant serveur (setupComplete), et
        # ``context_seeded`` dit si le contexte local a été rejoué dans la
        # session courante (faux quand le serveur a repris la session lui-même).
        self.session_id: str | None = None
        self.session_generation = 0
        self.context_seeded = False
        # Diagnostic (v1.7.5 — audit S6) : ``context_seeded`` dit seulement
        # qu'un rejeu a été ENVOYÉ, pas que le serveur l'a réellement COMMITÉ
        # avant l'arrivée du tour suivant. ``context_seed_confirmed`` est le
        # signal fort : vrai seulement si le tour de clôture du serveur
        # (son propre ``turn_complete`` en réponse au rejeu) a été OBSERVÉ
        # avant l'ouverture de la porte audio. Ne jamais confondre les deux :
        # c'est précisément cette confusion qui laissait passer un tour
        # suivant corrompu contre l'API réelle (réponse vide ou hors-sujet)
        # alors que tout semblait correct côté client.
        self.context_seed_confirmed = False
        # Diagnostic (v1.7.4 — correctif reconnexion-par-tour) : pourquoi le
        # PROCHAIN appel à ``connect()`` a été jugé nécessaire. Positionné
        # explicitement à chaque déclencheur légitime (GoAway, reset de
        # contexte, changement de voix) ; laissé à "startup"/"error" sinon.
        # Sert à distinguer dans les traces une reconnexion volontaire d'une
        # reconnexion technique — et à détecter, en test, toute reconnexion
        # qui ne serait due à AUCUNE de ces raisons connues (régression).
        self._reconnect_reason: str = "startup"
        # =====================================================
        # INSTRUMENTATION DU CYCLE DE TOUR (v1.7.3)
        # -----------------------------------------------------
        # ``turn_counter`` numérote chaque tour UTILISATEUR logique ouvert
        # dans la session courante (remis à zéro à chaque nouvelle session) ;
        # ``turn_id`` combine génération de session + numéro de tour pour un
        # identifiant global unique, exigé par le diagnostic double-cycle
        # (« pourquoi une seule phrase produit deux tours ? »). Le trace est
        # partagé avec ``AudioIO`` via le même interrupteur d'environnement
        # (``JARVIS_AUDIO_TRACE``) : une seule chronologie, deux sources.
        self.turn_counter = 0
        # ``turn_epoch`` compte les FERMETURES de tour (pas les ouvertures) :
        # il n'avance qu'au moment précis où ``_finish_turn`` clôt un tour
        # réellement ouvert. C'est le garde-fou qui détecte un bloc micro
        # capturé PENDANT le tour N, mais exécuté APRÈS que le tour N a été
        # clos — même si, entre-temps, ``speaking`` est redevenu False (ex.
        # Jarvis a fini de répondre et attend un suivi) : un simple contrôle
        # de ``speaking``/``session_generation`` ne peut pas voir cette
        # course précise (cf. Test E, §10) puisque l'état « idle » ressemble
        # exactement à une fenêtre d'écoute de suivi légitime. Comparer
        # l'epoch capturé à l'epoch courant lève l'ambiguïté : un suivi réel
        # est capturé APRÈS la fermeture (même epoch que l'état courant),
        # un bloc périmé a été capturé AVANT (epoch inférieur à l'état
        # courant).
        self.turn_epoch = 0
        self._trace = collections.deque(maxlen=GEMINI_TRACE_MAX_EVENTS)
        self._trace_lock = threading.Lock()
        #: Nombre de blocs micro rejetés car périmés (capturés alors que
        #: ``can_send()`` valait True, exécutés après que l'état a changé) —
        #: métrique directe de la course thread-audio / boucle-asyncio.
        self.stale_audio_dropped = 0
        # Porte d'entrée audio : aucune donnée temps réel n'est envoyée avant
        # que la session soit prête (reprise confirmée OU rejeu du contexte
        # terminé). Vraie par défaut pour une session branchée directement
        # (tests, outillage) ; ``connect()`` la ferme le temps du setup puis
        # la rouvre — ça supprime la course « audio envoyé pendant le rejeu »,
        # c'est-à-dire l'audio arrivant AVANT l'historique sur le WebSocket.
        self._session_ready = True
        self._resumable = False
        self._turn_user_text = []
        # Transcription de la réponse de Jarvis pour le tour en cours, et
        # portion de la demande utilisateur déjà versée au contexte (la
        # transcription arrive par fragments pendant que l'utilisateur parle).
        self._turn_model_text = []
        self._user_text_committed = ""
        # Vrai quand le tour courant est clos côté contexte : la prochaine
        # transcription entrante ouvre un nouveau tour (et ne recopie pas la
        # demande précédente, cf. interruption suivie de turn_complete).
        # Vrai à la construction : aucun tour n'est encore ouvert, la
        # première transcription doit donc en ouvrir un explicitement (et
        # être comptée par ``turn_counter``/tracée en ``TURN_OPEN``).
        self._turn_closed = True
        self._loop = None
        self._watcher_task = None
        self._voice_version_used = None
        # Vrai quand la session a été fermée volontairement pour appliquer un
        # réglage (changement de voix) : la boucle externe reconnecte
        # immédiatement, sans message d'erreur ni délai.
        self.reconnect_requested = False

        # Interruption locale (« stop » détecté par le micro ou bouton Stop).
        # Pendant la fenêtre d'interruption : l'audio du modèle est jeté et
        # le micro est réautorisé pour que Gemini entende l'utilisateur et
        # coupe son tour côté serveur.
        self.interrupt_requested = False
        self._interrupt_until = 0.0
        self._interrupt_was_active = False

    # ------------------------------------------------------------------
    # Interruption de la réponse en cours
    # ------------------------------------------------------------------

    def request_interrupt(self) -> None:
        """Demande l'arrêt de la réponse en cours (appelable depuis un
        autre thread : n'écrit que des attributs simples)."""
        self._interrupt_until = time.monotonic() + INTERRUPT_WINDOW_SECONDS
        self.interrupt_requested = True

    def interrupt_active(self) -> bool:
        """Vrai tant que l'interruption locale est en cours (fenêtre)."""
        if not self.interrupt_requested:
            return False
        if time.monotonic() >= self._interrupt_until:
            # Gemini n'a pas confirmé : on reprend le cours normal plutôt
            # que de rester bloqué.
            self.interrupt_requested = False
            return False
        return True

    def _clear_interrupt(self) -> None:
        self.interrupt_requested = False
        self._interrupt_until = 0.0

    # ------------------------------------------------------------------
    # Évènements d'interface (v1.7.0)
    # ------------------------------------------------------------------

    @staticmethod
    def _notify(hook, *args) -> None:
        """Appelle un callback d'interface sans jamais casser la boucle vocale."""
        if hook is None:
            return
        try:
            hook(*args)
        except Exception as exc:  # pragma: no cover - défensif
            log.debug("callback d'interface ignoré : %s", exc)

    # ------------------------------------------------------------------
    # Traçage du cycle de tour (v1.7.3 — diagnostic du double-cycle)
    # ------------------------------------------------------------------

    @property
    def turn_id(self) -> str:
        """Identifiant global du tour courant : ``g<session>-t<tour>``."""
        return f"g{self.session_generation}-t{self.turn_counter}"

    def _record_trace(self, kind: str, **fields) -> None:
        """Enregistre un événement du cycle de tour (borné, thread-safe).

        Miroir de ``AudioIO._record_trace`` : ne doit jamais masquer une
        erreur du pipeline vocal, et reste consultable après coup via
        ``trace_events()`` même quand ``JARVIS_AUDIO_TRACE`` est désactivé
        (l'affichage console est optionnel, la collecte ne l'est pas).
        """
        event = {
            "ts": time.monotonic(),
            "kind": kind,
            "thread": threading.current_thread().name,
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "session_generation": self.session_generation,
            "speaking": self.speaking,
            "tool_active": self.tool_active,
            **fields,
        }
        with self._trace_lock:
            self._trace.append(event)
        if _trace_enabled():
            details = " ".join(
                f"{key}={value}" for key, value in event.items()
                if key not in ("ts", "kind")
            )
            print(f"[GEMINI] {kind} {details}", flush=True)

    def trace_events(self) -> list[dict]:
        """Copie des événements du cycle de tour (tests et diagnostics)."""
        with self._trace_lock:
            return list(self._trace)

    # ------------------------------------------------------------------
    # Contexte conversationnel (v1.6.0)
    # ------------------------------------------------------------------

    def _commit_user_text(self) -> None:
        """Verse la demande utilisateur du tour courant dans le contexte.

        Appelée dès que le tour utilisateur produit un effet (appel d'outil ou
        début de réponse) afin que l'ordre USER -> OUTILS -> ASSISTANT soit
        toujours respecté, puis complétée si la transcription continue
        d'arriver.
        """
        full = " ".join(self._turn_user_text).strip()
        if not full:
            return
        committed = self._user_text_committed
        if not committed:
            self.conversation.add_user_message(full)
            log.debug(
                "INPUT FINALIZED gen=%s turn=%s (versé au contexte)",
                self.session_generation, full and len(self.conversation.get_messages()),
            )
        elif full == committed:
            return
        elif full.startswith(committed):
            self.conversation.extend_user_message(full[len(committed):])
        else:
            # Transcription révisée par le serveur : on complète plutôt que de
            # réécrire, pour ne jamais perdre ce qui a déjà servi de référence.
            self.conversation.extend_user_message(full)
        self._user_text_committed = full

    def _commit_assistant_text(self) -> None:
        text = " ".join(self._turn_model_text).strip()
        self._turn_model_text.clear()
        if text:
            self.conversation.add_assistant_message(text)

    def _begin_turn_if_needed(self) -> None:
        """Ouvre un nouveau tour si le précédent est déjà clos.

        La transcription entrante du tour suivant ne doit jamais être
        interprétée comme la suite de la demande précédente : on repart d'un
        tampon vide dès que le tour d'avant a été versé au contexte.
        """
        if not self._turn_closed:
            return
        self._turn_closed = False
        self._turn_user_text.clear()
        self._turn_model_text.clear()
        self._user_text_committed = ""
        self.turn_counter += 1
        log.debug(
            "TURN START gen=%s conversation=%s",
            self.session_generation, self.conversation.conversation_id,
        )
        self._record_trace("TURN_OPEN", source="input_transcription")

    def _finish_turn(self) -> None:
        """Clôt proprement le tour courant (fin de tour ou interruption).

        Le tampon de transcription utilisateur n'est PAS vidé ici : la
        mémoire persistante l'exploite encore juste après ``turn_complete``.
        Il est remis à zéro à l'ouverture du tour suivant.
        """
        self._commit_user_text()
        self._commit_assistant_text()
        self.conversation.close_turn("fin de tour")
        already_closed = self._turn_closed
        self._turn_closed = True
        log.debug(
            "TURN COMPLETE gen=%s conversation=%s messages=%s",
            self.session_generation, self.conversation.conversation_id,
            self.conversation.size(),
        )
        if not already_closed:
            self.turn_epoch += 1
            self._record_trace("TURN_CLOSE")

    def _handle_context_reset(self, info) -> None:
        """Le contexte a été réinitialisé : la session Gemini doit repartir.

        Appelé depuis n'importe quel thread (outil ``reset_conversation``) ou
        depuis la boucle vocale (commande « nouvelle conversation »). On coupe
        la session en cours pour que le serveur n'ait plus, lui non plus, la
        conversation précédente : la boucle externe reconnecte aussitôt.
        """
        self.resumption_handle = None
        self._turn_user_text.clear()
        self._turn_model_text.clear()
        self._user_text_committed = ""
        self._turn_closed = False
        self._session_ready = False
        if self.session is None and self.ctx is None:
            return
        self._reconnect_reason = "context_reset"
        self.reconnect_requested = True
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._schedule_session_restart)
        except RuntimeError:  # pragma: no cover - boucle déjà arrêtée
            log.debug("reset contexte : boucle asyncio indisponible")

    def _schedule_session_restart(self) -> None:
        try:
            asyncio.ensure_future(self._shutdown_session())
        except Exception as exc:  # pragma: no cover - défensif
            log.debug("redémarrage de session impossible : %s", exc)

    async def _seed_context(self) -> bool:
        """Rejoue le contexte local dans une session Gemini neuve.

        Si un handle de reprise existe, le serveur restaure lui-même la
        conversation : rejouer ferait doublon. Sinon (démarrage à froid,
        reprise expirée ou refusée, handle jamais reçu), Jarvis réinjecte SON
        contexte — c'est ce qui rend la continuité indépendante d'un état
        implicite côté serveur.

        Protocole (v1.7.1 — correctif de la régression « le modèle ne se
        souvient de rien après reconnexion ») :

        * l'historique est envoyé en UN ``clientContent`` **clôturé**
          (``turn_complete=True``) : un envoi non clôturé reste « en attente »
          et, sur les modèles audio 2.x, N'EST PAS rappelé par le tour audio
          suivant (thread Google AI 111617 ; pipecat : « *without this, the
          model ignores the context it's been seeded with* ») ;
        * le seed se termine par un tour **user** (tour vide ajouté si
          l'historique se termine par une réponse assistant) : exigence des
          modèles 2.x documentée par pipecat (« *we append a blank user turn
          to satisfy the server* ») ;
        * sur les modèles 2.x, la clôture déclenche une brève inférence de
          reprise (le modèle confirme qu'il a le contexte) — comportement de
          production assumé ; sur 3.x avec ``historyConfig``, le commit est
          silencieux (aucun appel modèle).

        Retourne True si un rejeu a effectivement été envoyé.
        """
        if self.session is None:
            return False
        if self.resumption_handle:
            # Le serveur a restauré la conversation lui-même : rejouer
            # créerait un doublon. C'est le chemin nominal de reconnexion.
            log.debug(
                "SESSION RESUMED gen=%s handle=present conversation=%s (pas de rejeu)",
                self.session_generation, self.conversation.conversation_id,
            )
            return False
        if SEED_MODE == "off":
            return False
        messages = self.conversation.get_messages()
        if not messages:
            return False
        turns = to_gemini_contents(messages)
        if not turns:
            return False
        # Exigence 2.x : le seed doit se terminer par un tour user. L'historique
        # de Jarvis se termine normalement par une réponse assistant.
        if turns and turns[-1].get("role") != "user":
            turns = [*turns, {"role": "user", "parts": [{"text": " "}]}]
        turn_complete = SEED_MODE != "pending"
        log.info(
            "CONTEXT REPLAY START gen=%s conversation=%s messages=%s contents=%s "
            "turn_complete=%s mode=%s",
            self.session_generation, self.conversation.conversation_id,
            len(messages), len(turns), turn_complete, SEED_MODE,
        )
        self.context_seed_confirmed = False
        try:
            await self.session.send_client_content(turns=turns, turn_complete=turn_complete)
        except Exception as exc:
            log.warning(
                "CONTEXT REPLAY END gen=%s resultat=echec erreur=%s", self.session_generation, exc
            )
            return False
        roles = "/".join(str(turn.get("role", "?")) for turn in turns)
        log.info(
            "CONTEXT REPLAY END gen=%s resultat=ok roles=%s contents=%s turn_complete=%s",
            self.session_generation, roles, len(turns), turn_complete,
        )
        # CORRECTIF v1.7.5 (cause racine de l'échec S6 en validation réelle) :
        # l'envoi ci-dessus n'est qu'une écriture réseau — il ne dit RIEN sur
        # le moment où le serveur a fini de TRAITER ce tour. Si le rejeu a été
        # clôturé (turn_complete=True), le serveur va généralement répondre
        # (reprise 2.x) ; tant que cette réponse n'est pas observée, le micro
        # ne doit PAS être autorisé à parler du tour suivant (cf.
        # ``SEED_COMMIT_TIMEOUT_SECONDS``). Sans ce correctif,
        # ``context_seeded=True`` était pris à tort pour une preuve que le
        # contexte était déjà exploitable par le modèle.
        if turn_complete:
            self.context_seed_confirmed = await self._await_seed_commit()
        return True

    async def _await_seed_commit(self) -> bool:
        """Consomme le tour de confirmation serveur du rejeu de contexte.

        Appelle ``_receive_one_turn_cycle()`` — EXACTEMENT le même mécanisme
        qui traite normalement un tour réel — pour absorber la réponse du
        serveur au rejeu (reprise 2.x : transcription + audio + son propre
        ``turn_complete``) AVANT que la porte audio ne s'ouvre. Borné par
        ``SEED_COMMIT_TIMEOUT_SECONDS`` : sur les modèles 3.x/historyConfig
        (commit silencieux, aucune inférence déclenchée), rien n'arrivera
        jamais sur ce tour — le délai expire alors normalement et la porte
        s'ouvre quand même (dégradation contrôlée, jamais un blocage
        permanent du micro).

        Retourne True si une confirmation serveur a réellement été observée
        (son propre ``TURN_COMPLETE`` sur CE tour précis), False si le délai
        a expiré sans réponse. Une erreur réseau/protocole pendant l'attente
        est propagée normalement (comme toute autre erreur de session) : la
        boucle externe (``main.py``/``ui.py``) reconnectera.
        """
        try:
            received = await asyncio.wait_for(
                self._receive_one_turn_cycle(), timeout=SEED_COMMIT_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            log.warning(
                "SEED COMMIT TIMEOUT gen=%s : aucune confirmation serveur sous %.1fs "
                "(normal sur un modèle à commit silencieux ; sinon, latence réseau "
                "anormale) — ouverture du micro en dégradé, sans confirmation",
                self.session_generation, SEED_COMMIT_TIMEOUT_SECONDS,
            )
            self._record_trace("SEED_COMMIT_TIMEOUT", session_id=self.session_id)
            return False
        log.info(
            "SEED COMMIT CONFIRMED gen=%s : réponse serveur au rejeu reçue avant "
            "l'ouverture du micro",
            self.session_generation,
        )
        self._record_trace(
            "SEED_COMMIT_CONFIRMED", session_id=self.session_id, received=received
        )
        return bool(received)

    #: Compat v1.7.0 : l'ancien nom reste disponible pour les diagnostics.
    _replay_context = _seed_context

    def _maybe_handle_reset_command(self) -> bool:
        """Commande vocale « nouvelle conversation », traitée localement.

        Aucun appel LLM supplémentaire : le contexte est vidé, la session est
        recréée vierge et l'utilisateur reçoit un retour par le bus de
        notifications existant.
        """
        text = " ".join(self._turn_user_text).strip()
        if not text or not is_new_conversation_command(text):
            return False
        self._turn_user_text.clear()
        self._turn_model_text.clear()
        self._user_text_committed = ""
        self._turn_closed = False
        self.request_interrupt()
        info = self.conversation.start_new_conversation(reason="commande vocale")
        self._notify_reset(info)
        return True

    @staticmethod
    def _notify_reset(info) -> None:
        message = "Conversation réinitialisée. La mémoire persistante est conservée."
        try:
            from .routine_actions import notify_user

            notify_user(message)
        except Exception:  # pragma: no cover - la notification ne doit rien casser
            print(f"[Jarvis] {message}")
        log.info("conversation réinitialisée -> %s", info.get("conversation_id", "?"))

    def _can_send_now(self) -> tuple[bool, str]:
        """Condition d'envoi, évaluée à l'instant de l'appel.

        Logique UNIQUE partagée par ``can_send()`` (pré-vérification rapide,
        utilisée par le pont micro sur le thread audio pour éviter de
        planifier une coroutine pour rien) et ``send_audio()`` (vérification
        AUTORITAIRE, ré-exécutée sur la boucle asyncio juste avant l'envoi
        réseau). Retourne ``(autorisé, raison_du_refus)``.
        """
        if self.session is None:
            return False, "pas de session"
        if self.tool_active:
            return False, "outil en cours"
        if not self._session_ready:
            return False, "session pas prete"
        # Pendant une interruption, le micro doit passer : c'est ainsi que
        # Gemini entend « stop » et arrête réellement son tour.
        if self.speaking and not self.interrupt_active():
            return False, "jarvis parle"
        return True, ""

    def can_send(self):
        allowed, _reason = self._can_send_now()
        return allowed

    async def connect(self):
        """Ouvre la session Live, avec repli automatique sur handle refusé.

        Un handle de reprise peut être refusé par le serveur (expiration
        ~2 h, session terminée…) : l'erreur est levée À LA CONNEXION
        (APIError 1007). On efface alors le handle et on retente UNE fois en
        session neuve — le contexte local est rejoué par ``_seed_context``.
        Sans ce repli, Jarvis bouclerait indéfiniment sur le handle mort.
        """
        attempts = 2
        while True:
            resumed = bool(self.resumption_handle)
            try:
                await self._connect_once()
                return
            except AuthError:
                raise
            except Exception as exc:
                if attempts > 1 and self.resumption_handle and _is_invalid_handle_error(exc):
                    log.warning(
                        "handle de reprise refusé par le serveur (%s) : "
                        "nouvelle session vierge + rejeu du contexte local",
                        exc,
                    )
                    self.resumption_handle = None
                    attempts -= 1
                    continue
                if resumed:
                    log.warning("reprise de session impossible (%s) : tentative abandonnée", exc)
                raise

    async def _connect_once(self):
        decl = [
            types.FunctionDeclaration(
                name=t["name"],
                description=t["description"],
                parameters=t["parameters"]
            )
            for t in TOOL_DECLARATIONS
        ]

        resumption_config = types.SessionResumptionConfig(
            handle=self.resumption_handle
        )

        response_mode = ""
        if self.response_mode_provider is not None:
            try:
                mode = self.response_mode_provider()
                if mode:
                    response_mode = f"Mode de réponse : {mode}. "
            except Exception:
                response_mode = ""

        pace_instruction = ""
        if self.speech_pace_provider is not None:
            try:
                pace = self.speech_pace_provider()
                if pace == "posé":
                    pace_instruction = (
                        "Parle de façon posée et très claire, sans précipitation. "
                    )
                elif pace == "vif":
                    pace_instruction = (
                        "Parle de manière vive avec des phrases courtes. "
                    )
            except Exception:
                pace_instruction = ""

        memory_context = ""
        if self.memory_manager is not None and self.memory_manager.enabled:
            try:
                # Au démarrage d'une session audio Live, Jarvis ne dispose pas
                # encore d'une transcription de la requête. On injecte donc un
                # petit noyau de souvenirs importants/récents, puis le modèle
                # peut appeler recall(query=...) pour une recherche ciblée.
                memory_context = self.memory_manager.relevant_memories_for_prompt(
                    query=self.user,
                    limit=self.memory_manager.max_results,
                )
            except Exception as exc:
                print(f"[Memory] Contexte indisponible : {exc}")
                memory_context = ""

        mode_context = ""
        try:
            mode_context = get_default_mode_manager().system_instruction()
        except Exception:
            mode_context = ""

        system_instruction = (
            f"Tu es Jarvis, assistant vocal de {self.user}. "
            "Parle naturellement en français. "
            f"{response_mode}"
            f"{pace_instruction}"
            "Réponds aux questions générales. "
            "Tu disposes d'une mémoire locale persistante, contrôlée par l'utilisateur. "
            "Utilise recall pour rechercher des souvenirs pertinents lorsque la question dépend du profil, des préférences, projets ou décisions passées. "
            "Utilise remember quand l'utilisateur dit explicitement de retenir quelque chose, ou pour une information clairement durable (identité, préférence, personne importante, projet, configuration, décision). "
            "Ne mémorise pas les banalités ni chaque phrase de la conversation. "
            "Pour oublier ou effacer la mémoire, utilise forget/delete_memory/clear_memory et demande confirmation pour les suppressions larges. "
            "Tu sais aussi enchaîner des actions grâce aux routines : run_routine pour lancer une routine existante (« lance le mode travail »), "
            "list_routines pour savoir ce qui existe, create_routine/update_routine pour en créer ou en modifier une. "
            "Douze routines préconfigurées sont déjà disponibles et désactivées par défaut, dont Mode focus et Mode jeu. "
            "Pour activer immédiatement le mode focus ou le mode jeu, utilise activate_focus_mode ou activate_game_mode ; "
            "pour revenir au comportement normal, utilise disable_jarvis_mode seulement si l'utilisateur le demande clairement. "
            "Chaque mode ferme à son activation les applications que l'UTILISATEUR a choisies (deux listes indépendantes, persistées) : "
            "ne préjuge jamais de leur contenu. Pour les consulter, list_mode_applications(mode) ; "
            "pour les modifier, toggle_mode_application / set_mode_applications / reset_mode_applications. "
            "Exemple : « dans le mode jeu, ne ferme pas Opera GX » → toggle_mode_application(mode='jeu', application='Opera GX', enabled=False) "
            "puis confirme ce que la liste du mode jeu contient maintenant. Modifier le mode jeu ne modifie jamais le mode focus, et inversement. "
            "« Affiche le blob », « masque le blob », « affiche le menu », « masque le menu » sont des commandes d'affichage explicites : "
            "appelle show_blob / hide_blob / show_menu / hide_menu. « Affiche le blob » et « affiche le menu » sont des ACTIONS : "
            "ils (ré)affichent l'élément demandé dans TOUS les cas, y compris si un mode jeu/focus ou un réglage le masque actuellement. "
            "Ne te contente jamais de dire que c'est caché ou refusé : exécute l'outil. "
            "Pour savoir ce qui est réellement affiché, get_ui_state. "
            "Pour « passe en mode Desktop » ou « passe en mode Blob », appelle set_interface_mode avec mode='desktop' ou mode='blob' : "
            "le changement est immédiat et persistant, sans redémarrage. "
            "Pour « active/désactive la routine hydratation », utilise update_routine avec name et enabled=true/false uniquement : "
            "ne la recrée pas, ne demande aucun horaire ni paramètre supplémentaire. En cas de nom ambigu, liste les routines. "
            "Une routine désactivée ne peut pas être lancée ; son activation ne lance pas immédiatement ses étapes, "
            "elle autorise ses prochains déclenchements tant que Jarvis reste ouvert. "
            "Les étapes d'une routine s'écrivent comme des appels séparés par des points-virgules, par exemple "
            "open_application(vscode); wait(2); set_volume(30). En cas de doute sur les outils autorisés, appelle list_routine_tools. "
            "Pour une échéance qui doit survivre au redémarrage du PC (« rappelle-moi demain à 9h », « chaque lundi à 8h »), utilise set_reminder ; "
            "garde set_timer pour les simples comptes à rebours de la session en cours. "
            "Tu disposes aussi de protocoles cinématiques (run_protocol) : une séquence plein écran "
            "avec diagnostic réel de la machine. Utilise run_protocol(protocol='wake_up') quand l'utilisateur dit "
            "« réveille-toi », « wake up » ou « lance la séquence d'allumage » ; 'diagnostic' pour un bilan système complet ; "
            "'focus' pour une session de concentration ; 'stand_down' pour la mise en veille. "
            "Le protocole se joue en arrière-plan : annonce-le brièvement (« Séquence d'allumage engagée ») "
            "puis commente sobrement le résultat, sans réciter toutes les étapes. "
            f"{writing_system_instruction()} "
            "Tu contrôles aussi la musique via Deezer (intégration native v1.5.2). "
            "Pour toute demande musicale (« mets de la musique », « joue Daft Punk », "
            "« Around the World de Daft Punk », « ma playlist Chill », pause, suivant…), "
            "utilise les outils music_* : music_play pour lancer, music_search pour chercher, "
            "music_pause / music_resume / music_next / music_previous pour contrôler, "
            "music_current pour le morceau en cours, music_list_playlists pour les playlists personnelles OAuth. "
            "Une demande « joue ma playlist X » doit utiliser music_play avec playlist=X et personal=true : "
            "Jarvis vérifie d'abord ses associations locales et peut donc lancer la playlist sans OAuth. "
            "Pour « enregistre ma playlist X » avec un lien ou un ID, utilise music_playlist_save ; "
            "sans lien/ID, utilise music_playlist_import seulement si un token OAuth valide permet la découverte. "
            "Pour « quelles sont mes playlists enregistrées », utilise music_playlist_list. "
            "Pour « supprime ma playlist X de Jarvis », utilise music_playlist_remove et précise que "
            "cela retire seulement l'association locale, sans supprimer la playlist Deezer. "
            "Réponds de façon très courte après une action musicale "
            "(« Je lance Daft Punk. », « Lecture mise en pause. », « C'est reparti. »). "
            "Si success=false avec ambiguous=true et candidates, demande laquelle choisir "
            "sans inventer. Si auth_required=true pour une playlist perso, explique clairement "
            "qu'il faut soit un token OAuth, soit enregistrer la playlist avec son lien Deezer. "
            "Ne prétends jamais qu'une playlist personnelle peut être découverte automatiquement sans OAuth. "
            "Ne prétends jamais qu'un morceau joue si l'outil ne l'a pas confirmé. "
            "Pour les actions sur le PC, utilise les outils "
            "et ne mens jamais sur leur résultat. "
            "Si l'utilisateur te coupe la parole (« stop », « attends », « ça suffit »), "
            "arrête-toi immédiatement : ne reprends pas la phrase interrompue, "
            "réponds au plus par un mot très bref et attends sa consigne suivante. "
            "Si un outil renvoie success=false, dis-le simplement et "
            "propose une alternative (par exemple list_applications ou "
            "list_websites pour connaître ce qui est autorisé). "
            "Avant toute action destructrice ou irréversible "
            "(shutdown_pc, restart_pc, delete_notes, clear_memory, delete_routine), demande une "
            "confirmation orale explicite puis rappelle l'outil avec "
            "confirm=true. "
            "Tu disposes du contexte de la conversation en cours : les tours précédents te sont "
            "fournis. Résous donc les références (« lui », « celui-là », « le deuxième », « l'autre », "
            "« oui », « mets-la en pause », « reprends », « plus court ») à partir de ces tours, "
            "et ne redemande pas une information déjà donnée juste avant. "
            "Ce contexte est distinct de la mémoire persistante : ne mémorise rien simplement "
            "parce que c'est dans la conversation. "
            "Si l'utilisateur demande de repartir de zéro (« nouvelle conversation », "
            "« efface le contexte », « réinitialise la conversation »), appelle reset_conversation : "
            "le contexte conversationnel est vidé, la mémoire persistante et les réglages restent intacts."
        )
        if memory_context:
            system_instruction += f"\n\n{memory_context}"
        if mode_context:
            system_instruction += f"\n\n{mode_context}"

        voice_name = None
        if self.voice_provider is not None:
            try:
                voice_name = self.voice_provider() or None
            except Exception:
                voice_name = None

        speech_config = None
        if voice_name:
            try:
                speech_config = types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=voice_name
                        )
                    )
                )
            except Exception as exc:
                print(f"[Voix] Configuration refusée ({exc}), voix par défaut.")
                speech_config = None

        config_kwargs = dict(
            response_modalities=["AUDIO"],
            system_instruction=system_instruction,
            tools=[
                types.Tool(
                    function_declarations=decl
                )
            ],
            speech_config=speech_config,
            session_resumption=resumption_config,
        )

        # Transcriptions d'entrée et de sortie : c'est la matière première du
        # contexte conversationnel local (avant la v1.6.0, Jarvis ne savait
        # littéralement pas ce qui avait été dit au tour précédent).
        #
        # Compression de fenêtre de contexte : sans elle, une session AUDIO est
        # terminée au bout de ~15 minutes (documentation Live API) et le
        # contexte serveur disparaît avec elle.
        #
        # historyConfig.initialHistoryInClientContent : protocole documenté de
        # l'historique initial (sur 3.x, le clientContent de seed est committé
        # sans appel modèle ; sur 2.x le champ est sans effet et le commit
        # explicite turn_complete=True reste nécessaire).
        #
        # Ces options sont appliquées de façon défensive : une version plus
        # ancienne du SDK ne doit pas empêcher Jarvis de démarrer.
        config = None
        try:
            transcription = types.AudioTranscriptionConfig()
        except Exception:  # pragma: no cover - dépend de la version du SDK
            transcription = None
        compression = None
        if CONTEXT_COMPRESSION_ENABLED:
            try:
                compression = types.ContextWindowCompressionConfig(
                    sliding_window=types.SlidingWindow()
                )
            except Exception:  # pragma: no cover - SDK sans compression
                compression = None
        history = None
        if HISTORY_CONFIG_ENABLED:
            try:
                history = types.HistoryConfig(initial_history_in_client_content=True)
            except Exception:  # pragma: no cover - SDK sans historyConfig
                history = None
        # Dégradation progressive : transcriptions+compression+history, puis
        # transcriptions seules, puis strict minimum.
        layers = (
            ("input_audio_transcription", transcription),
            ("output_audio_transcription", transcription),
            ("context_window_compression", compression),
            ("history_config", history),
        )
        for depth in (len(layers), 2, 0):
            extras = {name: value for name, value in layers[:depth]}
            try:
                config = types.LiveConnectConfig(**extras, **config_kwargs)
                break
            except Exception as exc:  # pragma: no cover - dépend de la version du SDK
                log.warning("options Live indisponibles (%s) : configuration réduite", exc)
                config = None
        if config is None:  # pragma: no cover - tous les niveaux ont échoué
            config = types.LiveConnectConfig(**config_kwargs)

        try:
            self.ctx = self.client.aio.live.connect(
                model=self.model,
                config=config
            )
            self.session = await self.ctx.__aenter__()
        except Exception as exc:
            self.ctx = None
            self.session = None
            if _is_auth_error(exc):
                raise AuthError(
                    "Clé Gemini API refusée. Vérifie GEMINI_API_KEY dans le "
                    "fichier .env (ou relance setup.bat)."
                ) from exc
            raise

        if self.voice_version_provider is not None:
            try:
                self._voice_version_used = int(self.voice_version_provider())
            except Exception:
                self._voice_version_used = None
        self.reconnect_requested = False
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - hors boucle asyncio
            self._loop = None
        self.conversation.bind_session(self._handle_context_reset)
        self.conversation.set_provider("gemini")

        # Comptabilité de session : distingue chaque session Gemini et dit si
        # le contexte a été repris par le serveur ou rejoué par Jarvis.
        resumed = bool(self.resumption_handle)
        self.session_generation += 1
        self.session_id = getattr(
            getattr(self.session, "setup_complete", None), "session_id", None
        ) or getattr(self.session, "session_id", None)
        self.context_seeded = False
        self.context_seed_confirmed = False
        self._session_ready = False
        # Diagnostic (v1.7.4) : pourquoi CETTE reconnexion a eu lieu. Toute
        # valeur autre que "startup"/"goaway"/"context_reset"/"voice_change"/
        # "error" signale une reconnexion non attribuée à une cause connue —
        # exactement la classe de bug corrigée ici (reconnexion après un
        # simple tour normal). Réinitialisé après lecture pour ne pas
        # « hériter » de la raison d'une reconnexion précédente.
        reconnect_reason = self._reconnect_reason
        self._reconnect_reason = "unexpected_after_normal_turn"
        if resumed:
            log.info(
                "SESSION RESUMED gen=%s session_id=%s reason=%s — %s (contexte restauré par le serveur)",
                self.session_generation, self.session_id or "?", reconnect_reason,
                self.conversation.describe(),
            )
            self._record_trace(
                "SESSION_RESUME", session_id=self.session_id, reason=reconnect_reason
            )
        else:
            log.info(
                "SESSION CREATED gen=%s session_id=%s reason=%s — %s",
                self.session_generation, self.session_id or "?", reconnect_reason,
                self.conversation.describe(),
            )
            self._record_trace(
                "SESSION_CONNECT", session_id=self.session_id, reason=reconnect_reason
            )
        # Rejeu du contexte local UNIQUEMENT si le serveur n'a pas repris la
        # session. La porte audio reste fermée jusqu'à la fin du rejeu ET,
        # si ce rejeu a été clôturé, jusqu'à ce que le serveur ait lui-même
        # confirmé l'avoir traité (``_await_seed_commit``, v1.7.5) : aucun
        # chunk ne peut précéder l'historique sur le WebSocket, NI arriver
        # pendant que le serveur génère encore sa réponse à ce rejeu.
        self.context_seeded = await self._seed_context()
        self._session_ready = True
        # Diagnostic (v1.7.4 — validation réelle, item #7 de l'audit) :
        # marqueur explicite de l'instant où le micro est de nouveau autorisé
        # à écrire sur CETTE session (``_can_send_now`` refuse tout envoi
        # tant que ``_session_ready`` est faux — cf. « session pas prete »).
        # Sert à prouver, trace à l'appui, qu'aucun bloc audio n'a pu
        # atteindre une session neuve avant la fin de son rejeu de contexte.
        # ``context_seed_confirmed`` (v1.7.5) distingue « rejeu envoyé » de
        # « rejeu réellement traité par le serveur avant ce marqueur » — ne
        # jamais déduire la seconde de la première (cause racine de
        # l'échec du scénario S6 contre l'API réelle, cf. audit v1.7.5).
        self._record_trace(
            "SESSION_READY",
            session_id=self.session_id,
            context_seeded=self.context_seeded,
            context_seed_confirmed=self.context_seed_confirmed,
        )
        self._start_voice_watcher()

    def _start_voice_watcher(self) -> None:
        """Surveille les changements de voix pour reconnecter proprement."""
        if self._watcher_task is not None and not self._watcher_task.done():
            return
        if self.voice_version_provider is None:
            return
        self._watcher_task = asyncio.create_task(self._watch_voice_changes())

    async def _watch_voice_changes(self) -> None:
        try:
            while True:
                await asyncio.sleep(0.5)
                if self.voice_version_provider is None:
                    return
                try:
                    version = int(self.voice_version_provider())
                except Exception:
                    continue
                if (
                    self._voice_version_used is not None
                    and version != self._voice_version_used
                ):
                    print("[Voix] Changement de voix : reconnexion de la session…")
                    # La reprise de session conserve la configuration d'ORIGINE
                    # (voix comprise) : pour appliquer la nouvelle voix, il
                    # faut une session NEUVE. On abandonne donc le handle —
                    # le contexte conversationnel est restauré par le rejeu
                    # local (_seed_context), il n'est pas perdu.
                    self.resumption_handle = None
                    self._reconnect_reason = "voice_change"
                    self.reconnect_requested = True
                    await self._shutdown_session()
                    return
        except asyncio.CancelledError:
            raise

    async def send_audio(self, pcm, *, capture_generation: int | None = None,
                          capture_turn_epoch: int | None = None):
        """Envoie un bloc micro à Gemini — SEUL point d'entrée autoritaire.

        RACE CORRIGÉE (v1.7.3 — double-cycle / « Dis-moi tout » superposé) :
        le pont micro (``mic()`` dans ``src/main.py``/``src/ui.py``) tourne
        sur le THREAD AUDIO temps réel. Il vérifie ``can_send()`` puis
        planifie cette coroutine via ``run_coroutine_threadsafe`` — mais il
        ne l'attend jamais et ne la revérifie jamais. Entre cette
        vérification et l'exécution RÉELLE de la coroutine sur la boucle
        asyncio (qui peut être occupée par autre chose : traitement d'un
        message serveur, callback audio synchrone), Gemini peut avoir
        commencé sa réponse — le bloc capturé pendant que c'était encore
        permis devient un bloc PÉRIMÉ. Le forwarder créerait un second tour
        « utilisateur » fantôme dans la session déjà en train de répondre au
        premier (démontré par ``tests/test_turn_race.py`` : capture avant
        ``speaking=True``, exécution après, sur le vrai pont micro + la
        vraie ``GeminiLive``).

        La correction architecturale : la décision « puis-je envoyer ? »
        n'est plus prise une fois pour toutes sur le thread audio — elle est
        RE-VALIDÉE ICI, sur la boucle asyncio, juste avant l'écriture
        réseau. Comme asyncio est mono-thread, aucune autre coroutine ne
        peut modifier ``self.speaking``/``self.session`` entre cette
        vérification et l'appel à ``send_realtime_input`` : c'est le SEUL
        endroit où la fraîcheur de l'état est garantie.

        ``capture_generation`` (optionnel, fourni par le pont micro) ajoute
        une seconde ligne de défense : si une reconnexion a eu lieu entre la
        capture et l'exécution (nouvelle session, VAD serveur vierge), le
        bloc appartient à une conversation qui n'existe plus côté serveur —
        il est abandonné même si, par coïncidence, ``speaking`` est de
        nouveau False au même instant.

        ``capture_turn_epoch`` (optionnel, fourni par le pont micro) est la
        troisième ligne de défense, et la plus fine : elle détecte un bloc
        capturé PENDANT un tour, mais exécuté APRÈS que ce tour a été clos
        (``turn_complete``/interruption), y compris quand ``speaking`` est
        redevenu False entre-temps — ce qui rend ce cas indiscernable d'un
        suivi légitime pour un simple contrôle de ``speaking`` (Test E,
        §10). ``turn_epoch`` n'avance qu'aux fermetures de tour : un bloc
        dont l'epoch capturé est en retard sur l'epoch courant appartenait
        forcément à un tour déjà clos avant même son exécution.
        """
        if (
            capture_generation is not None
            and capture_generation != self.session_generation
        ):
            self.stale_audio_dropped += 1
            self._record_trace(
                "AUDIO_SEND_DROPPED_STALE",
                reason="generation perimee (reconnexion pendant le trajet)",
                capture_generation=capture_generation,
                bytes=len(pcm),
            )
            return

        if (
            capture_turn_epoch is not None
            and capture_turn_epoch != self.turn_epoch
        ):
            self.stale_audio_dropped += 1
            self._record_trace(
                "AUDIO_SEND_DROPPED_STALE",
                reason="tour deja clos entre capture et execution",
                capture_turn_epoch=capture_turn_epoch,
                current_turn_epoch=self.turn_epoch,
                bytes=len(pcm),
            )
            return

        allowed, reason = self._can_send_now()
        if not allowed:
            if reason in ("jarvis parle", "outil en cours"):
                # Ces deux raisons signifient que l'état a changé APRÈS la
                # capture (sinon le pont micro n'aurait pas planifié cet
                # envoi) : c'est la course décrite ci-dessus, pas un
                # fonctionnement normal — on la compte et on la trace.
                self.stale_audio_dropped += 1
                self._record_trace(
                    "AUDIO_SEND_DROPPED_STALE",
                    reason=reason,
                    capture_generation=capture_generation,
                    bytes=len(pcm),
                )
            # Session absente/pas prête : chemin normal de reconnexion, déjà
            # journalisé ailleurs (SESSION CREATED/RESUMED) — pas de bruit.
            return

        self._record_trace("AUDIO_SENT_TO_GEMINI", bytes=len(pcm))
        await self.session.send_realtime_input(
            audio=types.Blob(
                data=pcm,
                mime_type="audio/pcm;rate=16000"
            )
        )

    async def receive_loop(self):
        try:
            await self._receive_loop()
        except BaseException:
            # Tour interrompu par une erreur réseau/serveur : le message
            # utilisateur reste dans le contexte mais le tour est marqué en
            # échec (comportement déterministe, testé).
            if not self.reconnect_requested:
                self._reconnect_reason = "error"
            self._fail_open_turn("erreur de session")
            raise
        else:
            self._fail_open_turn("session fermée avant la réponse")

    def _fail_open_turn(self, reason: str) -> None:
        """Ferme le tour resté ouvert quand la session s'arrête.

        Déterministe : la demande de l'utilisateur est conservée (elle doit
        rester référençable après une coupure), la réponse partielle éventuelle
        est conservée elle aussi ; sinon le tour est marqué ``failed``.
        """
        self._commit_user_text()
        if self._turn_model_text:
            self._commit_assistant_text()
        elif self.conversation.has_open_turn():
            self.conversation.fail_open_turn(reason)
        self._turn_user_text.clear()
        self._turn_model_text.clear()
        self._user_text_committed = ""
        self._turn_closed = False

    async def _receive_loop(self):
        """Reçoit les messages Gemini pour TOUS les tours de la session.

        CORRECTIF v1.7.4 (reconnexion-par-tour / « Je vous écoute »
        périodique) : ``session.receive()`` est conçu par le SDK Gemini
        Live pour se terminer naturellement dès qu'un tour est complet
        (``turn_complete``) — ce n'est PAS un signal que la connexion
        WebSocket est morte. Confirmé par Google (issue
        googleapis/python-genai#1224, résolue) : « the receive() method
        throws you out of the loop if turn is complete. To keep receiving
        messages from the following turns you need to put this part of the
        code under the while loop. »

        Avant ce correctif, ``_receive_loop`` ne consommait qu'UN tour puis
        rendait la main à l'appelant (``main.py``/``ui.py``), qui
        interprétait ce retour normal comme « la session est terminée » et
        rouvrait un WebSocket neuf (``connect()``) après CHAQUE tour — y
        compris pendant la fenêtre de conversation de 8 secondes, sans
        aucune parole de l'utilisateur. Chaque reconnexion à tort déclenchait
        ``_seed_context`` (pas de handle de reprise encore reçu à ce
        rythme), qui rejoue l'historique et clôt le rejeu par un tour
        « user » synthétique (vide) — cela provoque une brève réponse du
        modèle (« Je t'écoute. », « Je suis prêt... ») persistée comme un
        message assistant orphelin, et RÉARME la fenêtre de 8 secondes via
        ``on_turn_complete``. La boucle se perpétuait ainsi indéfiniment.

        On boucle donc ICI, sur LA MÊME session, tant qu'aucune vraie raison
        de reconnecter n'est apparue : GoAway serveur, erreur de session,
        changement de voix ou reset explicite du contexte — toutes ces
        conditions positionnent ``reconnect_requested`` à True et/ou libèrent
        ``self.session`` (cf. ``_handle_context_reset``,
        ``_watch_voice_changes``, le bloc GoAway ci-dessous). Un vrai suivi
        de conversation (nouvelle parole réelle avant expiration du délai)
        est ainsi traité par un simple tour supplémentaire sur le MÊME
        WebSocket, sans aucune reconnexion ni rejeu de contexte.
        """
        turn_cycle = 0
        while self.session is not None and not self.reconnect_requested:
            if turn_cycle > 0:
                # Tour suivant sur la session déjà ouverte : AUCUNE
                # reconnexion, AUCUN rejeu de contexte. C'est la preuve
                # instrumentée qu'un vrai enchaînement (ou un simple silence
                # suivi d'expiration) ne recrée pas de session.
                self._record_trace(
                    "SESSION_REUSED_NEXT_TURN",
                    turn_cycle=turn_cycle,
                )
            turn_cycle += 1
            received_any = await self._receive_one_turn_cycle()
            if not received_any:
                # Un appel à ``receive()`` qui ne délivre STRICTEMENT rien
                # n'arrive jamais sur une session réelle en vie (le SDK ne
                # rend la main qu'après un tour complet — cf. docstring
                # ci-dessus) : soit la connexion est réellement terminée sans
                # que ``self.session``/``reconnect_requested`` l'aient encore
                # reflété, soit un faux serveur de test n'a plus rien à
                # fournir. Dans les deux cas, continuer à rappeler receive()
                # en boucle serrée serait une attente active infinie : on
                # arrête ici, sans lever d'erreur (comportement déterministe
                # et sans risque de masquer un vrai échec, qui lève toujours
                # une exception propagée normalement par le SDK).
                return

    async def _receive_one_turn_cycle(self) -> bool:
        """Traite les messages d'UN appel à ``session.receive()``.

        Retourne ``True`` si au moins un message a été reçu (cas normal :
        une session réellement vivante représente toujours un tour complet),
        ``False`` si l'appel n'a livré strictement rien.
        """
        received_any = False
        async for r in self.session.receive():
            received_any = True

            sru = getattr(
                r,
                "session_resumption_update",
                None
            )
            if sru is not None:
                if sru.new_handle:
                    self.resumption_handle = sru.new_handle
                    self._resumable = True
                elif getattr(sru, "resumable", None) is False:
                    # Le serveur indique que la session n'est PAS reprenable à
                    # cet instant (exécution d'outil en cours, génération…).
                    # On garde le dernier handle valide (reprendre un état
                    # légèrement antérieur vaut mieux que tout perdre), mais
                    # l'état est tracé pour le diagnostic.
                    self._resumable = False
                    log.debug(
                        "session gen=%s momentanément non reprenable (handle conservé)",
                        self.session_generation,
                    )

            # GoAway : le serveur annoncera la fin de la connexion sous peu
            # (ABORTED). On la devance proprement — reconnexion immédiate et
            # silencieuse avec le handle de reprise, SANS passer par le chemin
            # d'erreur (traceback + 5 secondes d'attente).
            goaway = getattr(r, "go_away", None)
            if goaway is not None:
                log.info(
                    "serveur : GoAway (fin de connexion dans %s) — reconnexion propre",
                    getattr(goaway, "time_left", "?"),
                )
                self._reconnect_reason = "goaway"
                self.reconnect_requested = True

            server_content = getattr(
                r,
                "server_content",
                None
            )

            # Transcription de l'audio utilisateur (activée dans connect()).
            # Elle alimente le contexte conversationnel, la détection locale
            # de « nouvelle conversation » et l'extraction mémoire
            # conservatrice existante.
            if server_content:
                try:
                    transcript = getattr(server_content, "input_transcription", None)
                    text = getattr(transcript, "text", None) if transcript else None
                    if text:
                        self._begin_turn_if_needed()
                        self._turn_user_text.append(str(text))
                        self._record_trace("GEMINI_USER_TRANSCRIPT", text=str(text))
                        # v1.7.0 : la MÊME transcription alimente l'interface.
                        # Aucun second système de transcription n'est créé.
                        self._notify(
                            self.on_user_transcript,
                            " ".join(self._turn_user_text).strip(),
                            False,
                        )
                        if self._maybe_handle_reset_command():
                            continue
                except Exception:
                    pass

                # Transcription de la réponse de Jarvis : c'est elle qui rend
                # « plus court », « et sa population ? » possibles au tour
                # suivant.
                try:
                    out = getattr(server_content, "output_transcription", None)
                    out_text = getattr(out, "text", None) if out else None
                    if out_text:
                        self._commit_user_text()
                        self._turn_model_text.append(str(out_text))
                        self._record_trace("GEMINI_ASSISTANT_TRANSCRIPT", text=str(out_text))
                        self._notify(
                            self.on_assistant_transcript,
                            " ".join(self._turn_model_text).strip(),
                        )
                except Exception:
                    pass

            # =================================================
            # AUDIO GEMINI
            # =================================================

            if (
                server_content
                and server_content.model_turn
            ):
                # Le modèle répond : la demande de l'utilisateur est complète,
                # on la fige dans le contexte avant tout message assistant.
                self._commit_user_text()
                # Interruption locale en cours : l'audio déjà généré par
                # Gemini est jeté (Jarvis se tait) jusqu'à ce que le serveur
                # confirme l'interruption ou que la fenêtre expire.
                interrupting = self.interrupt_active()

                if interrupting:
                    self._interrupt_was_active = True
                else:
                    if (
                        not self.speaking
                        or self._interrupt_was_active
                    ) and self.on_speaking is not None:
                        # Reprise après une fausse détection : l'UI doit
                        # repasser en « réponse en cours ».
                        self.on_speaking()
                        self._record_trace("TTS_START")
                    self._interrupt_was_active = False

                self.speaking = True

                if not interrupting:
                    for part in server_content.model_turn.parts:

                        if (
                            part.inline_data
                            and part.inline_data.data
                        ):

                            self.on_audio(
                                part.inline_data.data
                            )

            # =================================================
            # INTERRUPTION
            # =================================================

            if (
                server_content
                and server_content.interrupted
            ):
                self.speaking = False
                self._clear_interrupt()
                self._interrupt_was_active = False
                self._record_trace("INTERRUPTION", source="serveur")
                # Ce que Jarvis avait commencé à dire reste référencable
                # (« non, l'autre », « redis ça plus court »).
                self._finish_turn()
                if self.on_interrupted:
                    self.on_interrupted()

            # =================================================
            # FIN DE TOUR
            # =================================================

            if (
                server_content
                and server_content.turn_complete
            ):
                self.speaking = False
                self._clear_interrupt()
                self._interrupt_was_active = False
                self._record_trace("TURN_COMPLETE", source="serveur")
                # Contexte conversationnel d'abord : le tour est clos avec
                # l'ordre USER -> OUTILS -> ASSISTANT.
                self._finish_turn()
                if self.on_turn_complete:
                    self.on_turn_complete()
                # Mémoire persistante : inchangée, volontairement séparée du
                # contexte. Seule l'extraction conservatrice existante peut
                # écrire un souvenir (« souviens-toi que… »).
                if self.memory_manager is not None and self._turn_user_text:
                    try:
                        text = " ".join(self._turn_user_text).strip()
                        if text:
                            self.memory_manager.remember_from_text(text, source="live_transcript")
                    except Exception as exc:
                        print(f"[Memory] Extraction ignorée : {exc}")
                    finally:
                        self._turn_user_text.clear()
                else:
                    self._turn_user_text.clear()

            # =================================================
            # OUTILS
            # =================================================

            tc = getattr(
                r,
                "tool_call",
                None
            )

            if tc and tc.function_calls:
                if self.on_thinking is not None:
                    try:
                        self.on_thinking()
                    except Exception:
                        pass
                self.tool_active = True
                responses = []
                # Un appel d'outil est une conséquence de la demande en cours :
                # elle doit être dans le contexte AVANT l'appel.
                self._commit_user_text()

                # try/finally : même si un outil ou l'envoi échoue, le micro
                # doit être réactivé (tool_active = False), sinon Jarvis
                # deviendrait muet jusqu'au redémarrage.
                try:
                    for c in tc.function_calls:

                        fn = TOOL_FUNCTIONS.get(c.name)
                        args = dict(c.args or {})
                        # Filet d'intention : si la transcription de la demande
                        # est déjà là, le système d'écriture peut refuser une
                        # hypothèse (« qu'est-ce que tu écrirais ») même si le
                        # modèle a appelé l'outil par erreur.
                        if c.name in WRITING_TOOL_NAMES and not str(args.get("request") or "").strip():
                            transcript = " ".join(self._turn_user_text).strip()
                            if transcript:
                                args["request"] = transcript

                        if fn is None:
                            result = {
                                "success": False,
                                "error": "Outil inconnu"
                            }
                        else:
                            # v1.7.0 : début/fin d'outil explicites pour le
                            # retour visuel (« Action : music_play »).
                            self._notify(self.on_tool_start, c.name)
                            try:
                                # Exécution dans un thread : un outil lent
                                # (attente, réseau, PowerShell) ne bloque
                                # plus la réception audio — l'utilisateur
                                # peut continuer à parler et interrompre.
                                result = await asyncio.to_thread(
                                    fn,
                                    **args
                                )
                            except Exception as e:
                                result = {
                                    "success": False,
                                    "error": str(e)
                                }
                            # ``result`` est toujours défini ici (le except
                            # ci-dessus en fabrique un), donc pas de finally.
                            self._notify(
                                self.on_tool_end,
                                c.name,
                                bool(result.get("success", True)) if isinstance(result, dict) else True,
                            )

                        # Trace compacte dans le contexte : l'appel et son
                        # résultat (jamais le payload JSON complet) pour que
                        # « lance le deuxième » reste résoluble au tour suivant.
                        self.conversation.add_tool_interaction(c.name, args, result)

                        responses.append(
                            types.FunctionResponse(
                                name=c.name,
                                id=c.id,
                                response=result
                            )
                        )

                    await self.session.send_tool_response(
                        function_responses=responses
                    )
                finally:
                    self.tool_active = False
        return received_any

    async def _shutdown_session(self) -> None:
        self.speaking = False
        self.tool_active = False
        self._clear_interrupt()
        self._interrupt_was_active = False
        self._session_ready = False
        ctx, self.ctx = self.ctx, None
        self.session = None
        if ctx is not None:
            try:
                await ctx.__aexit__(
                    None,
                    None,
                    None
                )
            except Exception:
                pass

    async def close(self):
        task = self._watcher_task
        self._watcher_task = None
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
        # Le contexte survit à la session (reconnexion, changement de voix,
        # changement d'interface) : seul le pont de reset est détaché.
        self.conversation.unbind_session(self._handle_context_reset)
        await self._shutdown_session()
