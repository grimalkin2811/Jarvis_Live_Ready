import asyncio
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
        self._turn_user_text = []
        # Transcription de la réponse de Jarvis pour le tour en cours, et
        # portion de la demande utilisateur déjà versée au contexte (la
        # transcription arrive par fragments pendant que l'utilisateur parle).
        self._turn_model_text = []
        self._user_text_committed = ""
        # Vrai quand le tour courant est clos côté contexte : la prochaine
        # transcription entrante ouvre un nouveau tour (et ne recopie pas la
        # demande précédente, cf. interruption suivie de turn_complete).
        self._turn_closed = False
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

    def _finish_turn(self) -> None:
        """Clôt proprement le tour courant (fin de tour ou interruption).

        Le tampon de transcription utilisateur n'est PAS vidé ici : la
        mémoire persistante l'exploite encore juste après ``turn_complete``.
        Il est remis à zéro à l'ouverture du tour suivant.
        """
        self._commit_user_text()
        self._commit_assistant_text()
        self.conversation.close_turn("fin de tour")
        self._turn_closed = True

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
        if self.session is None and self.ctx is None:
            return
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

    async def _replay_context(self) -> None:
        """Rejoue le contexte local dans une session Gemini neuve.

        Si un handle de reprise existe, le serveur restaure lui-même la
        conversation : rejouer ferait doublon. Sinon (démarrage à froid,
        reprise expirée, reset), Jarvis réinjecte SON contexte — c'est ce qui
        rend la continuité indépendante d'un état implicite côté serveur.
        """
        if self.session is None:
            return
        if self.resumption_handle:
            log.debug("contexte : reprise de session serveur (pas de rejeu local)")
            return
        messages = self.conversation.get_messages()
        if not messages:
            return
        turns = to_gemini_contents(messages)
        if not turns:
            return
        try:
            await self.session.send_client_content(turns=turns, turn_complete=False)
        except Exception as exc:
            log.warning("rejeu du contexte conversationnel impossible : %s", exc)
            return
        log.debug(
            "contexte rejoué id=%s messages=%s contents=%s",
            self.conversation.conversation_id, len(messages), len(turns),
        )

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

    def can_send(self):
        # Pendant une interruption, le micro doit passer : c'est ainsi que
        # Gemini entend « stop » et arrête réellement son tour.
        if self.session is None or self.tool_active:
            return False
        return not self.speaking or self.interrupt_active()

    async def connect(self):
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
        # littéralement pas ce qui avait été dit au tour précédent). L'option
        # est appliquée de façon défensive : une version plus ancienne du SDK
        # ne doit pas empêcher Jarvis de démarrer.
        try:
            transcription = types.AudioTranscriptionConfig()
            config = types.LiveConnectConfig(
                input_audio_transcription=transcription,
                output_audio_transcription=transcription,
                **config_kwargs,
            )
        except Exception as exc:  # pragma: no cover - dépend de la version du SDK
            log.warning("transcriptions Live indisponibles (%s) : contexte limité", exc)
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
        log.debug(
            "session Gemini ouverte — %s (reprise=%s)",
            self.conversation.describe(), bool(self.resumption_handle),
        )
        await self._replay_context()
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
                    self.reconnect_requested = True
                    await self._shutdown_session()
                    return
        except asyncio.CancelledError:
            raise

    async def send_audio(self, pcm):
        if not self.session:
            return

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

        async for r in self.session.receive():

            sru = getattr(
                r,
                "session_resumption_update",
                None
            )
            if sru and sru.new_handle:
                self.resumption_handle = sru.new_handle

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

    async def _shutdown_session(self) -> None:
        self.speaking = False
        self.tool_active = False
        self._clear_interrupt()
        self._interrupt_was_active = False
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
