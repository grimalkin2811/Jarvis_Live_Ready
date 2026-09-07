import asyncio
import time

from google import genai
from google.genai import types

from .memory import MemoryManager
from .tools import TOOL_DECLARATIONS, TOOL_FUNCTIONS


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
        response_mode_provider=None,
        voice_provider=None,
        voice_version_provider=None,
        speech_pace_provider=None,
        memory_manager: MemoryManager | None = None,
    ):
        self.client = genai.Client(api_key=key)
        self.model = model
        self.user = user

        self.on_audio = on_audio
        self.on_turn_complete = on_turn_complete
        self.on_interrupted = on_interrupted
        self.on_speaking = on_speaking
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

        self.session = None
        self.ctx = None
        self.speaking = False
        self.tool_active = False
        self.resumption_handle = None
        self._turn_user_text = []
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
            "Dix routines préconfigurées sont déjà disponibles et désactivées par défaut. "
            "Pour « active/désactive la routine hydratation », utilise update_routine avec name et enabled=true/false uniquement : "
            "ne la recrée pas, ne demande aucun horaire ni paramètre supplémentaire. En cas de nom ambigu, liste les routines. "
            "Une routine désactivée ne peut pas être lancée ; son activation ne lance pas immédiatement ses étapes, "
            "elle autorise ses prochains déclenchements tant que Jarvis reste ouvert. "
            "Les étapes d'une routine s'écrivent comme des appels séparés par des points-virgules, par exemple "
            "open_application(vscode); wait(2); set_volume(30). En cas de doute sur les outils autorisés, appelle list_routine_tools. "
            "Pour une échéance qui doit survivre au redémarrage du PC (« rappelle-moi demain à 9h », « chaque lundi à 8h »), utilise set_reminder ; "
            "garde set_timer pour les simples comptes à rebours de la session en cours. "
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
            "confirm=true."
        )
        if memory_context:
            system_instruction += f"\n\n{memory_context}"

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

        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=system_instruction,
            tools=[
                types.Tool(
                    function_declarations=decl
                )
            ],
            speech_config=speech_config,
            session_resumption=resumption_config
        )

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

            # Certaines versions de Gemini Live peuvent fournir une
            # transcription de l'audio utilisateur. Si elle existe, on la
            # collecte pour une extraction mémoire conservatrice en fin de tour.
            if server_content:
                try:
                    transcript = getattr(server_content, "input_transcription", None)
                    text = getattr(transcript, "text", None) if transcript else None
                    if text:
                        self._turn_user_text.append(str(text))
                except Exception:
                    pass

            # =================================================
            # AUDIO GEMINI
            # =================================================

            if (
                server_content
                and server_content.model_turn
            ):
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
                if self.on_turn_complete:
                    self.on_turn_complete()
                if self.memory_manager is not None and self._turn_user_text:
                    try:
                        text = " ".join(self._turn_user_text).strip()
                        if text:
                            self.memory_manager.remember_from_text(text, source="live_transcript")
                    except Exception as exc:
                        print(f"[Memory] Extraction ignorée : {exc}")
                    finally:
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
                self.tool_active = True
                responses = []

                # try/finally : même si un outil ou l'envoi échoue, le micro
                # doit être réactivé (tool_active = False), sinon Jarvis
                # deviendrait muet jusqu'au redémarrage.
                try:
                    for c in tc.function_calls:

                        fn = TOOL_FUNCTIONS.get(c.name)

                        if fn is None:
                            result = {
                                "success": False,
                                "error": "Outil inconnu"
                            }
                        else:
                            try:
                                # Exécution dans un thread : un outil lent
                                # (attente, réseau, PowerShell) ne bloque
                                # plus la réception audio — l'utilisateur
                                # peut continuer à parler et interrompre.
                                result = await asyncio.to_thread(
                                    fn,
                                    **dict(c.args or {})
                                )
                            except Exception as e:
                                result = {
                                    "success": False,
                                    "error": str(e)
                                }

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
        await self._shutdown_session()
