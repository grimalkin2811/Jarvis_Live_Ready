from google import genai
from google.genai import types

from .memory import MemoryManager
from .tools import TOOL_DECLARATIONS, TOOL_FUNCTIONS


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
        self.memory_manager = memory_manager

        self.session = None
        self.ctx = None
        self.speaking = False
        self.tool_active = False
        self.resumption_handle = None
        self._turn_user_text = []

    def can_send(self):
        return self.session is not None and not self.speaking and not self.tool_active

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
            "Réponds aux questions générales. "
            "Tu disposes d'une mémoire locale persistante, contrôlée par l'utilisateur. "
            "Utilise recall pour rechercher des souvenirs pertinents lorsque la question dépend du profil, des préférences, projets ou décisions passées. "
            "Utilise remember quand l'utilisateur dit explicitement de retenir quelque chose, ou pour une information clairement durable (identité, préférence, personne importante, projet, configuration, décision). "
            "Ne mémorise pas les banalités ni chaque phrase de la conversation. "
            "Pour oublier ou effacer la mémoire, utilise forget/delete_memory/clear_memory et demande confirmation pour les suppressions larges. "
            "Tu sais aussi enchaîner des actions grâce aux routines : run_routine pour lancer une routine existante (« lance le mode travail »), "
            "list_routines pour savoir ce qui existe, create_routine/update_routine pour en créer ou en modifier une. "
            "Les étapes d'une routine s'écrivent comme des appels séparés par des points-virgules, par exemple "
            "open_application(vscode); wait(2); set_volume(30). En cas de doute sur les outils autorisés, appelle list_routine_tools. "
            "Pour une échéance qui doit survivre au redémarrage du PC (« rappelle-moi demain à 9h », « chaque lundi à 8h »), utilise set_reminder ; "
            "garde set_timer pour les simples comptes à rebours de la session en cours. "
            "Pour les actions sur le PC, utilise les outils "
            "et ne mens jamais sur leur résultat. "
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

        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=system_instruction,
            tools=[
                types.Tool(
                    function_declarations=decl
                )
            ],
            session_resumption=resumption_config
        )

        self.ctx = self.client.aio.live.connect(
            model=self.model,
            config=config
        )

        self.session = await self.ctx.__aenter__()

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
                if not self.speaking and self.on_speaking is not None:
                    self.on_speaking()
                self.speaking = True
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

                for c in tc.function_calls:

                    fn = TOOL_FUNCTIONS.get(c.name)

                    try:

                        if fn:

                            result = fn(
                                **dict(c.args or {})
                            )

                        else:

                            result = {
                                "success": False,
                                "error": "Outil inconnu"
                            }

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
                self.tool_active = False

    async def close(self):
        self.speaking = False
        self.tool_active = False

        if self.ctx:

            await self.ctx.__aexit__(
                None,
                None,
                None
            )

            self.ctx = None
            self.session = None