from google import genai
from google.genai import types

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

        self.session = None
        self.ctx = None
        self.speaking = False
        self.tool_active = False
        self.resumption_handle = None

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

        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=(
                f"Tu es Jarvis, assistant vocal de {self.user}. "
                "Parle naturellement en français. "
                f"{response_mode}"
                "Réponds aux questions générales. "
                "Pour les actions sur le PC, utilise les outils "
                "et ne mens jamais sur leur résultat. "
                "Si un outil renvoie success=false, dis-le simplement et "
                "propose une alternative (par exemple list_applications ou "
                "list_websites pour connaître ce qui est autorisé). "
                "Avant toute action destructrice ou irréversible "
                "(shutdown_pc, restart_pc, delete_notes), demande une "
                "confirmation orale explicite puis rappelle l'outil avec "
                "confirm=true."
            ),
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