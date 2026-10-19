"""Scénarios RÉELS Dashboard + Gemini Live (E1 à E10) — « mini-Jarvis ».

Reproduit la composition de production (``src/ui.py``) :

* thread Qt : Dashboard réel (``DashboardController`` relié au pont vocal),
  widget de conversation réel pour les messages écrits, bouton Stop branché
  sur l'interruption manuelle de production ;
* thread moteur : boucle asyncio dédiée, VRAIE session ``GeminiLive`` et VRAIE
  ``AudioIO`` (``tests/real_gemini_harness.RealVoiceLab``, déjà utilisé par le
  script officiel ``validate_reconnect_v174_real.py``), contexte de
  conversation PARTAGÉ par défaut, ``RUNTIME.register_text_handler`` et états
  du moteur publiés comme en production ;
* entrée vocale : pont micro de production alimenté par une fausse carte son
  qui capture EN CONTINU (bruit de fond + énoncé réel), exactement comme un
  vrai micro. Énoncés synthétisés localement (voix française SAPI via le
  script officiel v174) ou, à défaut, par une session Live dédiée. Le micro
  de l'utilisateur n'est jamais ouvert, rien n'est joué sur ses haut-parleurs ;
* interruption : vrai barge-in d'``AudioIO`` (l'énoncé « Stop » est capté
  pendant que la réponse est lue) → ``gemini.request_interrupt()``.

Pourquoi ce chemin (correctif PR #50, validation Windows du 2026-10-16) : la
version précédente envoyait chaque énoncé puis COUPAIT le flux audio, sans
``audio_stream_end``. La VAD automatique de Gemini Live suppose un flux
continu (documentation Google : un flux interrompu doit être clos par
``audioStreamEnd``) ; en production, ``AudioIO`` transmet le micro en continu
tant que la conversation est ouverte. Le harnais n'exerçait donc pas le
chemin de production, et son auto-test (faux serveur sans VAD) ne pouvait pas
le révéler. L'auto-test utilise désormais un faux serveur à VAD par énergie
qui exige, comme le vrai, de l'audio APRÈS l'énoncé pour clore le tour.

Chaque échec joint l'état du moteur (``engine_snapshot``), la raison de la
porte micro, l'état audio, la fin des traces et les erreurs de session
(classe, code et statut d'API) — masqués.

Outils : déclarations de production, exécution limitée à la liste blanche de
``outils_surs.py`` (aucune action sur le PC, aucun envoi à un tiers).

Clé : variable ``GEMINI_API_KEY`` posée par l'orchestrateur pour CE processus
seulement ; jamais affichée, jamais écrite. Aucun enregistrement audio n'est
conservé.

``--selftest`` : même harnais contre un faux serveur (aucune clé, aucun
réseau) — prouve que le harnais fonctionne sur ce PC, ne prouve RIEN sur
Gemini réel.

Sortie : ``<out>/gemini_reel.json``.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
import traceback
from pathlib import Path

#: Code de Jarvis testé : la copie isolée de la PR (posée par l'orchestrateur), jamais la copie de
#: travail de l'utilisateur ; à défaut (exécution manuelle), le dépôt qui contient ce script.
ROOT = Path(os.environ.get("JARVIS_VALIDATION_RACINE") or Path(__file__).resolve().parents[2]).resolve()
ICI = Path(__file__).resolve().parent
REUSSI, ECHEC, NON_TESTE = "RÉUSSI", "ÉCHEC", "NON TESTÉ"

PROMPTS_VOIX = {
    "V1": "Bonjour Jarvis, je m'appelle Ada. Réponds en une phrase courte.",
    "V2": "Ouvre le tableau de bord sur la page mémoire.",
    "V3": "Quel code est-ce que je t'ai donné par écrit tout à l'heure ?",
    "L1": "Raconte-moi en détail l'histoire de la tour Eiffel, en au moins huit phrases.",
    "L2": "Explique-moi en détail comment fonctionne un moteur de voiture, en au moins huit phrases.",
    "L3": "Décris-moi en détail le cycle de l'eau, en au moins huit phrases.",
    "STOP": "Stop, arrête-toi s'il te plaît.",
    "V6": "Quel est mon prénom ?",
    "V7": "Le tableau de bord de ma voiture affiche un voyant orange. Qu'est-ce que ça peut vouloir dire ? "
          "Réponds en deux phrases.",
    "V8": "Merci, c'est tout pour le moment.",
    "VA": "Quelle est la capitale de l'Espagne ? Réponds en un mot.",
}
TEXTES = {
    "T1": "Retiens ce code pour la suite de notre conversation : ORION-4. Réponds juste « noté ».",
    "T2": "Comment je m'appelle ?",
    "T3": "Mon tableau de bord Excel est trop lent, tu as une idée rapide ? Réponds en une phrase.",
    "T4": "Message écrit envoyé pendant que tu parles.",
    "T4B": "D'accord, merci.",
    "T5": "Rappelle-moi le code que je t'ai donné par écrit.",
}

#: Délais (s). Réel : un tour vocal = énoncé (~5 s) + VAD + réponse ; le chien
#: de garde de Jarvis reprend un serveur muet en 30 s, ces bornes ne masquent
#: donc aucun blocage. Auto-test : faux serveur local, réponses de 1 à 6 s.
DELAIS_REELS = {"tour": 90.0, "disponible": 75.0, "parole": 60.0, "longue": 180.0}
DELAIS_SELFTEST = {"tour": 25.0, "disponible": 25.0, "parole": 20.0, "longue": 40.0}
#: Niveau minimal (RMS int16) de l'énoncé « Stop » : voix claire près du micro,
#: au-dessus du seuil de barge-in d'AudioIO (900).
NIVEAU_STOP = 2500.0

RESULTATS: list[dict] = []
EVENEMENTS: dict[str, list] = {"exceptions": []}
_CLE_RE = re.compile(r"AIza[A-Za-z0-9_\-]{10,}|AQ\.[A-Za-z0-9_\-]+")


def masquer(texte) -> str:
    return _CLE_RE.sub("<clé masquée>", str(texte))


def _excepthook(exc_type, exc, tb):
    EVENEMENTS["exceptions"].append(masquer("".join(traceback.format_exception(exc_type, exc, tb))[-1500:]))
    sys.__excepthook__(exc_type, exc, tb)


def charger(nom: str, chemin: Path):
    spec = importlib.util.spec_from_file_location(nom, chemin)
    module = importlib.util.module_from_spec(spec)
    sys.modules[nom] = module
    spec.loader.exec_module(module)
    return module


def extrait(texte: str, n: int = 140) -> str:
    texte = " ".join(str(texte or "").split())
    return texte if len(texte) <= n else texte[: n - 1] + "…"


def noter(cid: str, titre: str, statut: str, resultat: str, preuve: str = "") -> None:
    RESULTATS.append({"id": cid, "titre": titre, "statut": statut, "resultat": masquer(resultat),
                      "preuve": masquer(preuve)})
    print(f"[{cid}] {statut} — {titre} : {masquer(resultat)}", flush=True)
    if statut == ECHEC and preuve:
        print(f"[{cid}] diagnostic : {masquer(preuve)}", flush=True)


def decrire_exception(exc: BaseException) -> str:
    """Classe, message, code/statut d'API et causes chaînées — masqués, bornés."""
    morceaux, vus, courant = [], set(), exc
    while courant is not None and id(courant) not in vus and len(morceaux) < 4:
        vus.add(id(courant))
        details = []
        for attr in ("code", "status"):
            valeur = getattr(courant, attr, None)
            if valeur not in (None, ""):
                details.append(f"{attr}={valeur}")
        recu = getattr(courant, "rcvd", None)  # websockets.ConnectionClosed : code/raison reçus
        if recu is not None:
            details.append(f"ws={getattr(recu, 'code', '')} {getattr(recu, 'reason', '')}".strip())
        message = " ".join(str(courant).split())[:240]
        morceaux.append(f"{type(courant).__name__}: {message}" + (f" [{', '.join(details)}]" if details else ""))
        courant = courant.__cause__ or courant.__context__
    return masquer(" <- ".join(morceaux))


def ajuster_niveau(pcm: bytes, rms_min: float) -> bytes:
    """Remonte le niveau d'un énoncé trop faible, mesuré comme le barge-in d'AudioIO.

    AudioIO compare le RMS de blocs de 80 ms (1280 échantillons) à son seuil :
    le niveau retenu est la médiane des blocs actifs (silences exclus).
    """
    import numpy as np

    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
    usable = samples[: samples.size // 1280 * 1280]
    if usable.size == 0:
        return pcm
    blocs = np.sqrt(np.mean(np.square(usable.reshape(-1, 1280)), axis=1))
    actifs = blocs[blocs > 200.0]
    if actifs.size == 0:
        return pcm
    niveau = float(np.median(actifs))
    if niveau >= rms_min:
        return pcm
    return np.clip(samples * (rms_min / niveau), -32768, 32767).astype(np.int16).tobytes()


def phrases(texte: str) -> list[str]:
    """Découpe un énoncé en phrases (ponctuation finale suivie d'une espace)."""
    return [morceau for morceau in re.split(r"(?<=[.?!])\s+", texte.strip()) if morceau]


def enonce_synthetique(texte: str) -> bytes:
    """Énoncé de l'auto-test : une salve par phrase, pauses de 0,9 s entre phrases.

    Une pause plus longue que la fin de parole de la VAD (8 blocs de 80 ms) :
    comme le vrai service, le faux serveur clôt la parole à la pause et commence
    une génération que la phrase suivante annule (motif réel E01/E07/E09/E10).
    """
    from tests.echo_loop_harness import _tone_pcm

    morceaux = phrases(texte)
    if len(morceaux) <= 1:
        return _tone_pcm(1.2, 2600.0, 16000) + b"\x00\x00" * 1600
    pause = b"\x00\x00" * int(16000 * 0.9)
    return pause.join(_tone_pcm(0.7, 2600.0, 16000) for _ in morceaux) + b"\x00\x00" * 1600


# ---------------------------------------------------------------------------
# Synthèse des énoncés (AVANT toute initialisation Qt : processus SAPI isolés)
# ---------------------------------------------------------------------------


def synthese_locale(prompts: list[str]) -> tuple[dict[str, bytes] | None, str]:
    """Voix française locale (SAPI) via le script officiel, sinon None."""
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        v174 = charger("validate_reconnect_v174_real", ROOT / "scripts" / "validate_reconnect_v174_real.py")
    except Exception as exc:
        return None, f"script v174 inchargeable ({type(exc).__name__})"
    try:
        import pyttsx3  # noqa: F401
    except Exception:
        return None, "pyttsx3 indisponible"
    with tempfile.TemporaryDirectory(prefix="jarvis-validation-tts-") as tmp:
        try:
            voice = v174._synthesize_one_local(prompts[0], Path(tmp) / "sonde.wav")
        except Exception as exc:
            return None, f"synthèse locale impossible ({type(exc).__name__})"
    nom = str(voice or "").lower()
    if not any(mark in nom for mark in ("fr", "french", "français", "hortense", "julie", "paul", "denise")):
        return None, f"aucune voix française locale (voix : {voice!r})"
    cache = v174.synthesize_all_local(prompts)
    if not cache or any(p not in cache for p in prompts):
        return None, "synthèse locale incomplète"
    return cache, f"voix locale française ({voice})"


async def synthese_live(api_key: str, model: str, prompts: list[str]) -> dict[str, bytes]:
    import google.genai as genai

    vrg = charger("validate_real_gemini", ROOT / "scripts" / "validate_real_gemini.py")
    client = genai.Client(api_key=api_key)
    return await vrg.synthesize_all_via_live(client, model, prompts)


# ---------------------------------------------------------------------------
# Attentes (le thread Qt continue de traiter ses évènements)
# ---------------------------------------------------------------------------


def attendre(app, predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    app.processEvents()
    return bool(predicate())


def pause(app, secondes: float) -> None:
    attendre(app, lambda: False, secondes)


# ---------------------------------------------------------------------------
# Labo : AudioIO + GeminiLive réelles, câblées comme src/ui.py
# ---------------------------------------------------------------------------


def construire_labo(api_key: str, model: str, client=None):
    from src.conversation import set_default_conversation_context
    from src.runtime_status import RUNTIME, EngineState
    from src.ui import _manual_stop
    from tests.real_gemini_harness import RealVoiceLab
    from UI import menu_state

    class LaboDashboard(RealVoiceLab):
        """``RealVoiceLab`` + compléments de câblage de production (src/ui.py)."""

        def __init__(self) -> None:
            super().__init__(api_key, model, user="Validation")
            gemini = self.gemini
            if client is not None:
                gemini.client = client
            self.erreurs: list[tuple[float, str]] = []
            self.interruptions: list[float] = []
            self._current.update(tools=[], interrupted=False)
            gemini.on_tool_start = lambda name: self._current.setdefault("tools", []).append(name)

            def on_interrupted() -> None:  # production : desktop("interrupted") puis clear_output()
                self._current["interrupted"] = True
                self.interruptions.append(time.monotonic())
                self.audio.clear_output()

            gemini.on_interrupted = on_interrupted
            # Production : AudioIO(..., on_barge_in=on_barge_in) -> gemini.request_interrupt().
            self.audio.on_barge_in = gemini.request_interrupt
            # Bouton Stop du Dashboard / menu radial : gestionnaire de production (src/ui.py).
            menu_state.LIVE.set_stop_speaking_handler(lambda: _manual_stop(self.audio, gemini))
            # Contexte PARTAGÉ (production : get_default_conversation_context()).
            set_default_conversation_context(self.conversation)
            RUNTIME.register_text_handler(gemini.submit_text_threadsafe)

        def _on_turn_complete(self) -> None:
            current = self._current
            super()._on_turn_complete()
            self.turns[-1]["tools"] = list(current.get("tools", []))
            self.turns[-1]["interrupted"] = bool(current.get("interrupted"))
            self._current.update(tools=[], interrupted=False)

        async def _voice_main(self) -> None:
            """Boucle de src/ui.py (connect -> receive_loop -> close), états publiés."""
            gemini = self.gemini
            while not self._stop.is_set():
                gemini.reconnect_requested = False
                try:
                    RUNTIME.set_engine_state(EngineState.CONNECTING, "ouverture de la session Live")
                    await gemini.connect()
                    RUNTIME.set_engine_state(EngineState.READY, "", model=str(gemini.model or ""),
                                             session_generation=int(gemini.session_generation or 0))
                    self.connected.set()
                    await gemini.receive_loop()
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    if self._stop.is_set():
                        break
                    description = decrire_exception(exc)
                    self.erreurs.append((time.monotonic(), description))
                    self.errors.append(description)
                    print(f"   [session] erreur : {description}", flush=True)
                    if not gemini.reconnect_requested:
                        RUNTIME.set_engine_state(EngineState.RECONNECTING, "nouvelle tentative")
                        await asyncio.sleep(1.0)
                else:
                    await asyncio.sleep(0.1)
                finally:
                    try:
                        await gemini.close()
                    except Exception:
                        pass

        def arreter(self) -> None:
            try:
                self.stop()
            finally:
                try:
                    RUNTIME.unregister_text_handler()
                except Exception:
                    pass
                try:
                    menu_state.LIVE.set_stop_speaking_handler(None)
                except Exception:
                    pass

    return LaboDashboard()


# ---------------------------------------------------------------------------
# Faux serveur de l'auto-test : VAD par énergie, réponses diffusées, interruption
# ---------------------------------------------------------------------------


def installer_faux_serveur(path):
    """Faux serveur FIDÈLE sur les points qui ont échoué en réel.

    * VAD par énergie : la fin de parole exige ``end_blocks`` blocs REÇUS sous
      le seuil — un harnais qui coupe le flux après l'énoncé ne voit jamais la
      fin du tour (défaut de la version précédente) ;
    * réponses diffusées dans le temps (fenêtre « Jarvis parle » réelle) ;
    * interruption : de la parole pendant une génération coupe celle-ci
      (``interrupted`` puis ``turn_complete``), comme le service réel.
    """
    from src.audio import AudioIO
    from tests.echo_loop_harness import VadLiveServer, _tone_pcm
    from tests.live_harness import (
        FakeClient,
        _Message,
        _ResumptionUpdate,
        msg_interrupted,
        msg_model_transcript,
        msg_turn_complete,
        msg_user_transcript,
    )

    voix_minimale = _tone_pcm(0.3, 3000.0, AudioIO.OUTPUT_RATE)

    class ServeurSelftest(VadLiveServer):
        #: Latence avant le premier audio d'une réponse (le vrai service met
        #: 0,5 à 1,5 s) : une phrase qui suit la pause annule une génération
        #: encore MUETTE — motif réel E01/E07/E09/E10.
        LATENCE_REFLEXION_S = 0.9
        #: Réponse au rejeu de contexte après une coupure, plus lente que
        #: ``SEED_COMMIT_TIMEOUT_SECONDS`` (historique long) — motif réel E08.
        DELAI_REPONSE_REJEU_S = 6.0

        def __init__(self) -> None:
            super().__init__(path, end_blocks=8, threshold_rms=350.0)
            self.text_turns_use_oracle = True
            self._diffusions: dict[str, asyncio.Task] = {}
            self.interruptions_par_client = 0
            self.contenus_differes = 0

        def _register(self, session) -> None:
            super()._register(session)
            sid = session.session_id
            generer = session._generate
            envoyer_contenu = session.send_client_content
            session.__dict__["_differe"] = None

            async def generer_tardif(recap: bool = False, answer_override=None) -> None:
                # Comme le vrai service, une réponse (texte, suite d'outil, rejeu)
                # prend du temps : pendant ce temps, un ``clientContent`` peut
                # l'interrompre (cf. ``contenu_client``).
                delai = self.DELAI_REPONSE_REJEU_S if recap else self.LATENCE_REFLEXION_S

                async def plus_tard() -> None:
                    await asyncio.sleep(delai)
                    await generer(recap=recap, answer_override=answer_override)

                self._diffusions[sid] = asyncio.ensure_future(plus_tard())

            async def contenu_client(turns=None, turn_complete: bool = True) -> None:
                tache = self._diffusions.get(sid)
                if tache is not None and not tache.done():
                    # « A message here will interrupt any current model generation. »
                    tache.cancel()
                    self.interruptions_par_client += 1
                    await session._emit(msg_interrupted())
                    await session._emit(msg_turn_complete())
                if session.__dict__.get("_vad", {}).get("in_speech") and turn_complete:
                    # Parole en cours : la VAD du serveur attend la fin de
                    # l'activité avant de générer. Si le micro se tait sans
                    # ``audio_stream_end``, cette fin n'arrive jamais (constat
                    # réel : RESPONSE_STALLED après la relance).
                    self.contenus_differes += 1
                    session.__dict__["_differe"] = (turns, turn_complete)
                    return
                await envoyer_contenu(turns=turns, turn_complete=turn_complete)

            session._generate = generer_tardif
            session.send_client_content = contenu_client
            emettre = session._emit

            async def emettre_realiste(message) -> None:
                # Réalisme : le faux serveur générique joint 64 octets d'audio
                # (1 ms) à ses réponses texte et à son récapitulatif après
                # reconnexion, ce qu'aucune vraie réponse ne fait. Après une
                # interruption (``clear_output``), AudioIO attend 100 ms de voix
                # avant de lancer la lecture : ces réponses factices restaient
                # « à jouer » pour toujours (artefact d'auto-test, sans
                # équivalent réel). Limité aux sessions de CE serveur.
                turn = getattr(getattr(message, "server_content", None), "model_turn", None)
                for part in getattr(turn, "parts", None) or []:
                    inline = getattr(part, "inline_data", None)
                    if inline is not None and len(inline.data or b"") < len(voix_minimale):
                        inline.data = voix_minimale
                await emettre(message)

            session._emit = emettre_realiste

        async def _vad_send_realtime_input(self, session, audio=None, audio_stream_end=None, **kwargs):
            etat = session.__dict__.setdefault(
                "_vad", {"in_speech": False, "below": 0, "started": 0.0, "peak": 0.0})
            if audio_stream_end:
                # « audioStreamEnd » : l'audio en cache est vidé, la parole en
                # cours est close (documentation Live API).
                if etat.get("in_speech"):
                    etat.update(in_speech=False, below=0)
                    texte = self.next_user_transcripts.pop(0) if self.next_user_transcripts else "je vous écoute"
                    await self._commit_turn(session, texte, peak=etat.get("peak", 0.0), started=etat.get("started", 0.0))
                await self._contenu_differe(session)
                return
            avant = bool(etat.get("in_speech"))
            await super()._vad_send_realtime_input(session, audio=audio, **kwargs)
            pendant = bool(session.__dict__.get("_vad", {}).get("in_speech"))
            if not avant and pendant:
                tache = self._diffusions.get(session.session_id)
                if tache is not None and not tache.done():
                    tache.cancel()
                    await session._emit(msg_interrupted())
                    await session._emit(msg_turn_complete())
            if avant and not pendant:
                await self._contenu_differe(session)

        async def _contenu_differe(self, session) -> None:
            differe = session.__dict__.get("_differe")
            if differe is not None:
                session.__dict__["_differe"] = None
                await session.send_client_content(turns=differe[0], turn_complete=differe[1])

        async def _commit_turn(self, session, text: str, *, peak: float, started: float) -> None:
            session.committed.append({"role": "user", "parts": [{"text": text}]})
            await session._emit(msg_user_transcript(text))
            # Le modèle répond à TOUT ce qui a été dit depuis sa dernière réponse
            # (une génération annulée par la suite de la phrase ne perd rien).
            demande = session.__dict__.setdefault("_demande", [])
            demande.append(text)
            complete = " ".join(demande)
            script = self.next_tool_script
            if script is not None and re.search(script[0], complete, re.IGNORECASE):
                demande.clear()
                await session._generate()  # appel d'outil ; la suite arrive avec le tool_response
                return
            answer, _ok = self.oracle.answer(complete, session.visible())
            seconds = 6.0 if re.search(r"en détail", complete, re.IGNORECASE) else 1.0
            self._diffusions[session.session_id] = asyncio.create_task(self._diffuser(session, answer, seconds))

        async def _diffuser(self, session, answer: str, seconds: float) -> None:
            await asyncio.sleep(self.LATENCE_REFLEXION_S)
            session.__dict__.get("_demande", []).clear()
            audio = _tone_pcm(seconds, 3000.0, AudioIO.OUTPUT_RATE)
            parts = 8 if seconds > 2 else 2
            piece = (len(audio) // parts) & ~1
            for index in range(parts):
                chunk = audio[index * piece:(index + 1) * piece] if index < parts - 1 else audio[index * piece:]
                await session._emit(msg_model_transcript(answer if index == 0 else "", audio=chunk))
                await asyncio.sleep(seconds / parts / 2)  # génération deux fois plus rapide que la lecture
            await session._emit(msg_turn_complete())
            session.committed.append({"role": "model", "parts": [{"text": answer}]})
            handle = f"{session.session_id}-{len(session.committed)}"
            self.handles[handle] = [dict(c) for c in session.committed]
            session.last_handle = handle
            await session._emit(_Message(session_resumption_update=_ResumptionUpdate(handle, True)))

    server = ServeurSelftest()
    oracle = server.oracle
    oracle.register(r"je m'appelle ada", lambda pairs: "Enchanté Ada.")
    oracle.register(r"ORION-4", lambda pairs: "Noté.")
    oracle.register(r"comment je m'appelle|mon prénom",
                    lambda pairs: "Tu t'appelles Ada." if any("Ada" in t for _r, t in pairs) else "Je ne sais pas.")
    oracle.register(r"code", lambda pairs: "Le code est ORION-4." if any("ORION-4" in t for _r, t in pairs)
                    else "Je ne connais aucun code.")
    oracle.register(r"capitale de l'Espagne", lambda pairs: "Madrid.")
    oracle.register(r"tableau de bord sur la page", lambda pairs: "C'est ouvert.")
    oracle.register(r".", lambda pairs: "Réponse du faux serveur.")
    server.next_tool_script = (r"tableau de bord sur la page", "open_dashboard", {"page": "memoire"})
    return server, FakeClient(server)


# ---------------------------------------------------------------------------
# Scénarios
# ---------------------------------------------------------------------------


class Scenarios:
    def __init__(self, app, labo, controller, pcm: dict[str, bytes], selftest: bool, server=None) -> None:
        self.app = app
        self.labo = labo
        self.gemini = labo.gemini
        self.controller = controller
        self.pcm = pcm
        self.selftest = selftest
        self.server = server
        self.delais = DELAIS_SELFTEST if selftest else DELAIS_REELS
        self.widget = None
        self.debut = time.monotonic()

    # -- observation -----------------------------------------------------------------
    def audible(self) -> bool:
        audio = self.labo.audio
        return bool(audio.speaking) or audio.output_pending_bytes() > 0

    def disponible(self) -> bool:
        """Jarvis peut entendre une nouvelle demande : session prête, rien en cours, voix terminée."""
        return bool(self.gemini.can_send()) and not self.gemini.speaking and not self.audible()

    def diagnostic(self, depuis: float | None = None) -> str:
        gemini, audio = self.gemini, self.labo.audio
        try:
            porte = gemini._can_send_now()
        except Exception as exc:  # pragma: no cover - diagnostic seulement
            porte = (None, type(exc).__name__)
        bruit = {"AUDIO_SENT_TO_GEMINI", "GEMINI_USER_TRANSCRIPT", "GEMINI_ASSISTANT_TRANSCRIPT",
                 "LIVE_DIAGNOSTIC_FIELDS"}
        traces = []
        for event in gemini.trace_events()[-80:]:
            if event.get("kind") in bruit:
                continue
            champs = {k: v for k, v in event.items()
                      if k not in ("ts", "kind", "thread", "turn_id") and isinstance(v, (str, int, float, bool))}
            age = time.monotonic() - float(event.get("ts", 0.0))
            traces.append(f"-{age:.1f}s {event.get('kind')} {json.dumps(champs, ensure_ascii=False)[:120]}")
        audio_traces = [f"{e.get('kind')}" for e in audio.trace_events()[-8:]]
        erreurs = [d for t, d in self.labo.erreurs if depuis is None or t >= depuis][-3:]
        etat = {
            "moteur": gemini.engine_snapshot(),
            "porte_micro": {"ouverte": porte[0], "raison": porte[1]},
            "audio": {"eveille": bool(audio.awake), "voix_jarvis": bool(audio.speaking),
                      "lecture_en_attente_octets": int(audio.output_pending_bytes())},
            "tours_observes": len(self.labo.turns),
            "erreurs_session": erreurs,
            "traces_gemini": traces[-12:],
            "traces_audio": audio_traces,
        }
        return masquer(json.dumps(etat, ensure_ascii=False))[:3500]

    # -- primitives ----------------------------------------------------------------
    def attendre_disponible(self, contexte: str) -> None:
        if not attendre(self.app, self.disponible, self.delais["disponible"]):
            raise TimeoutError(f"Jarvis indisponible {self.delais['disponible']:.0f} s avant « {contexte} »")
        # Traîne acoustique (anti-écho) écoulée : l'énoncé est entendu en entier.
        pause(self.app, self.labo.audio.MIC_ECHO_GUARD_SECONDS + 0.35)

    def _dire(self, cle: str) -> None:
        if self.server is not None:
            # Une transcription par phrase prononcée (jamais de file décalée).
            self.server.next_user_transcripts[:] = phrases(PROMPTS_VOIX[cle])
        self.labo.wake()
        self.labo.speak_pcm(self.pcm[cle])

    def parler_sans_attendre(self, cle: str) -> int:
        self.attendre_disponible(cle)
        avant = len(self.labo.turns)
        self._dire(cle)
        return avant

    def reponse_vocale(self, avant: int) -> dict | None:
        """Premier tour terminé depuis ``avant`` qui contient une transcription
        utilisateur : la réponse du modèle au rejeu de contexte (tour SANS
        demande, après une reconnexion) n'est la réponse à aucun énoncé."""
        return next((t for t in self.labo.turns[avant:] if str(t.get("user") or "").strip()), None)

    def attendre_tour(self, avant: int, cle: str, timeout: float | None = None) -> dict:
        timeout = timeout or self.delais["tour"]
        if not attendre(self.app, lambda: self.reponse_vocale(avant) is not None, timeout):
            raise TimeoutError(f"aucune fin de tour {timeout:.0f} s après l'énoncé {cle}")
        return dict(self.reponse_vocale(avant))

    def parler(self, cle: str) -> dict:
        avant = self.parler_sans_attendre(cle)
        return self.attendre_tour(avant, cle)

    def ouvrir_conversation(self) -> bool:
        from controle_dashboard_reel import page_contenant, widget_par_type

        page = page_contenant(self.controller, "jarvis.conversation")
        if page is None:
            return False
        self.controller.open(page)
        attendre(self.app, self.controller.is_open, 5)
        _page, self.widget = widget_par_type(self.controller.window, "jarvis.conversation")
        return self.widget is not None

    def ecrire(self, cle: str, attendre_reponse: bool = True) -> dict:
        if self.widget is None or self.controller.window is None or not self.controller.is_open():
            self.ouvrir_conversation()
        widget = self.widget
        attendre(self.app, lambda: widget.send_button.isEnabled(), 30)
        avant = len(self.labo.turns)
        widget.input.setPlainText(TEXTES[cle])
        widget.send()
        attendre(self.app, lambda: not widget._pending, 20)
        banner = widget.banner.text() if widget.banner.isVisible() else ""
        result = {"accepte": not banner and widget.input.toPlainText() == "", "banniere": banner,
                  "texte_conserve": widget.input.toPlainText() == TEXTES[cle]}
        if result["accepte"] and attendre_reponse:
            # La réponse au message est le tour qui CONTIENT ce message (même
            # règle que ``reponse_vocale``) : jamais un tour sans demande.
            def reponse():
                return next((t for t in self.labo.turns[avant:] if t.get("user") == TEXTES[cle]), None)

            if attendre(self.app, lambda: reponse() is not None, self.delais["tour"]):
                result.update(reponse())
            else:
                result["sans_reponse"] = True
        return result

    def message_dans_contexte(self, texte: str) -> bool:
        from src.conversation import get_default_conversation_context

        return any(texte in (m.text or "") for m in get_default_conversation_context().get_messages())

    def evenements(self, liste, depuis: float) -> int:
        return sum(1 for t in liste if t >= depuis)

    def traces_depuis(self, source, kind: str, depuis: float) -> list[dict]:
        return [e for e in source() if e.get("kind") == kind and float(e.get("ts", 0.0)) >= depuis]

    # -- E1 à E10 -------------------------------------------------------------------
    def e01(self):
        turn = self.parler("V1")
        ok = bool(turn.get("assistant")) and not self.controller.is_open()
        noter("E01", "Conversation vocale normale avant ouverture du Dashboard", REUSSI if ok else ECHEC,
              f"transcription utilisateur « {extrait(turn.get('user'), 80)} », réponse « {extrait(turn.get('assistant'))} », "
              f"Dashboard fermé={not self.controller.is_open()}", "" if ok else self.diagnostic())

    def e02(self):
        turn = self.parler("V2")
        opened = attendre(self.app, lambda: self.controller.is_open(), 10)
        page = self.controller.window.current_page() if self.controller.window is not None else None
        ok = "open_dashboard" in turn.get("tools", []) and opened and page == "memoire"
        noter("E02", "Ouverture du Dashboard pendant une conversation (commande vocale)", REUSSI if ok else ECHEC,
              f"outils={turn.get('tools')}, ouvert={opened}, page={page}, réponse « {extrait(turn.get('assistant'))} »",
              "" if ok else self.diagnostic())

    def e03(self):
        if not self.ouvrir_conversation():
            noter("E03", "Message écrit depuis le Dashboard et réponse", NON_TESTE, "widget Conversation absent")
            return
        result = self.ecrire("T1")
        ok = result["accepte"] and bool(result.get("assistant"))
        noter("E03", "Message écrit depuis le Dashboard et réponse", REUSSI if ok else ECHEC,
              f"accepté={result['accepte']}, bannière « {extrait(result['banniere'], 80)} », "
              f"réponse « {extrait(result.get('assistant'))} »", "" if ok else self.diagnostic())

    def e04_e05(self):
        text = self.ecrire("T2")
        name_ok = text["accepte"] and "ada" in (text.get("assistant") or "").lower()
        reopened = attendre(self.app, self.gemini.can_send, 10)
        voice = self.parler("V3")
        code_ok = "orion" in (voice.get("assistant") or "").lower()
        ok4 = name_ok and code_ok
        noter("E04", "Continuité du contexte voix → texte → voix", REUSSI if ok4 else ECHEC,
              f"prénom dit à la voix retrouvé par écrit={name_ok} (« {extrait(text.get('assistant'), 80)} ») ; "
              f"code donné par écrit retrouvé à la voix={code_ok} (« {extrait(voice.get('assistant'), 80)} »)",
              "" if ok4 else self.diagnostic())
        ok5 = reopened and bool(voice.get("user")) and bool(voice.get("assistant"))
        noter("E05", "Reprise de la voix après une réponse écrite", REUSSI if ok5 else ECHEC,
              f"micro rouvert après la réponse écrite={reopened}, tour vocal transcrit={bool(voice.get('user'))}, "
              f"réponse={bool(voice.get('assistant'))}", "" if ok5 else self.diagnostic())

    def e06(self):
        """Politique : pendant une réponse, le message écrit est refusé explicitement et conservé."""
        avant = self.parler_sans_attendre("L1")
        speaking = attendre(self.app, lambda: bool(self.gemini.speaking), self.delais["parole"])
        refused = self.ecrire("T4", attendre_reponse=False) if speaking else {"accepte": None, "banniere": ""}
        fin = attendre(self.app, lambda: self.reponse_vocale(avant) is not None, self.delais["longue"])
        attendre(self.app, self.disponible, self.delais["longue"])
        absent = not self.message_dans_contexte(TEXTES["T4"])
        after = self.ecrire("T4B")
        ok = (speaking and refused.get("accepte") is False and "occupé" in refused.get("banniere", "")
              and bool(refused.get("texte_conserve")) and absent and fin and after["accepte"]
              and bool(after.get("assistant")))
        noter("E06", "Message écrit pendant que Jarvis parle ou travaille", REUSSI if ok else ECHEC,
              f"Jarvis parlait={speaking}, refus explicite « {extrait(refused.get('banniere'), 90)} », texte conservé="
              f"{refused.get('texte_conserve')}, absent du contexte={absent}, réponse en cours terminée={fin}, "
              f"message suivant accepté et répondu={after['accepte'] and bool(after.get('assistant'))}",
              "" if ok else self.diagnostic())

    def e07(self):
        avant = self.parler_sans_attendre("L2")
        speaking = attendre(self.app, self.audible, self.delais["parole"])
        exc_before = len(EVENEMENTS["exceptions"])
        window = self.controller.window
        if window is not None:
            window.close()
        attendre(self.app, lambda: not self.controller.is_open(), 3)
        self.controller.open()
        attendre(self.app, self.controller.is_open, 3)
        self.controller.store.config.close.mode = "interactive"
        self.controller.store.config.close.interactive["release_window"] = True
        self.controller.store.changed("close")
        self.controller.window.close()
        attendre(self.app, lambda: self.controller.window is None, 3)
        self.widget = None
        self.controller.store.config.close.mode = "hide"
        self.controller.store.changed("close")
        reopened = self.ouvrir_conversation()
        complete = attendre(self.app, lambda: self.reponse_vocale(avant) is not None, self.delais["longue"])
        turn = dict(self.reponse_vocale(avant)) if complete else {}
        after = self.parler("VA")
        new_exc = EVENEMENTS["exceptions"][exc_before:]
        madrid = "madrid" in (after.get("assistant") or "").lower()
        ok = speaking and reopened and not new_exc and bool(turn.get("assistant")) and madrid
        noter("E07", "Fermeture et réouverture du Dashboard pendant une réponse", REUSSI if ok else ECHEC,
              f"Jarvis parlait={speaking}, fermé/libéré puis rouvert pendant la réponse={reopened}, exceptions="
              f"{len(new_exc)}, réponse complète={bool(turn.get('assistant'))}, tour vocal suivant correct={madrid}",
              (new_exc[0][-300:] if new_exc else "") or ("" if ok else self.diagnostic()))

    def e08(self):
        details, ok = [], True
        # 1) Interruption VOCALE réelle (barge-in d'AudioIO) pendant la lecture d'une longue réponse.
        avant = self.parler_sans_attendre("L3")
        speaking = attendre(self.app, self.audible, self.delais["parole"])
        barge = coupe = confirme = repris = tour_stop = False
        generation_finie = None
        if speaking:
            pause(self.app, 1.5)  # Jarvis parle depuis un moment (au-delà de la grâce anti-écho)
            t_stop = time.monotonic()
            generation_finie = not self.gemini.speaking  # le serveur avait-il déjà tout envoyé ?
            if self.server is not None:
                self.server.next_user_transcripts[:] = [PROMPTS_VOIX["STOP"]]
            self.labo.speak_pcm(self.pcm["STOP"])
            barge = attendre(self.app, lambda: bool(self.traces_depuis(self.labo.audio.trace_events, "barge_in",
                                                                        t_stop)), 10)
            coupe = barge and attendre(self.app, lambda: self.labo.audio.output_pending_bytes() == 0, 3)
            confirme = attendre(self.app, lambda: bool(self.traces_depuis(self.gemini.trace_events, "INTERRUPTION",
                                                                           t_stop)), 10)

            def stop_traite() -> bool:
                return any(re.search(r"stop|arr[êe]te", str(t.get("user") or ""), re.IGNORECASE)
                           for t in self.labo.turns[avant:])

            # Le tour « Stop » doit être traité AVANT la coupure réseau (sinon il
            # serait perdu avec la session). Un serveur qui ne lui répond pas
            # n'invalide pas l'interruption : c'est seulement noté.
            tour_stop = attendre(self.app, lambda: stop_traite() and self.disponible(), self.delais["tour"])
            repris = tour_stop or attendre(self.app, self.disponible, 5)
        interruption_ok = speaking and barge and coupe and (confirme or bool(generation_finie)) and repris
        ok &= interruption_ok
        details.append(f"interruption vocale pendant la réponse : parlait={speaking}, barge-in détecté={barge}, "
                       f"lecture coupée={coupe}, interruption confirmée par le serveur={confirme} "
                       f"(génération déjà terminée={generation_finie}), tour « Stop » traité={tour_stop}, "
                       f"Jarvis de nouveau disponible={repris}")
        # 2) Coupure réseau simulée (session fermée brutalement, SANS reprise) puis reconnexion.
        t_cut = time.monotonic()
        generation = self.gemini.session_generation
        self.labo.connected.clear()
        self.labo.force_disconnect(drop_handle=True)
        reconnected = attendre(self.app, lambda: self.labo.connected.is_set()
                               and self.gemini.session_generation > generation and self.gemini.can_send(), 60)
        text = self.ecrire("T5")
        code_ok = text["accepte"] and "orion" in (text.get("assistant") or "").lower()
        voice = self.parler("V6")
        name_ok = "ada" in (voice.get("assistant") or "").lower()
        ok &= reconnected and code_ok and name_ok
        details.append(f"coupure réseau simulée : reconnecté={reconnected} (session {generation}→"
                       f"{self.gemini.session_generation}), contexte retrouvé par écrit={code_ok} "
                       f"(« {extrait(text.get('assistant') or text.get('banniere'), 70)} »), puis à la voix={name_ok} "
                       f"(« {extrait(voice.get('assistant'), 70)} »)")
        noter("E08", "Interruption, coupure réseau et reconnexion", REUSSI if ok else ECHEC, " ; ".join(details),
              "" if ok else self.diagnostic(depuis=t_cut - 120))

    def e09(self):
        self.controller.hide_window()
        attendre(self.app, lambda: not self.controller.is_open(), 3)
        turns = [self.parler("VA"), self.parler("V6"), self.parler("V8")]
        answered = [bool(t.get("assistant")) for t in turns]
        madrid = "madrid" in (turns[0].get("assistant") or "").lower()
        ada = "ada" in (turns[1].get("assistant") or "").lower()
        ok = all(answered) and madrid and ada
        noter("E09", "Non-régression d'une conversation purement vocale (Dashboard fermé)", REUSSI if ok else ECHEC,
              f"3 tours vocaux consécutifs, réponses={answered}, exactitude capitale/prénom={madrid}/{ada}",
              "" if ok else self.diagnostic())

    def e10(self):
        self.controller.hide_window()
        attendre(self.app, lambda: not self.controller.is_open(), 3)
        voice = self.parler("V7")
        opened_voice = attendre(self.app, self.controller.is_open, 3)
        text = self.ecrire("T3")
        tools = list(voice.get("tools", [])) + list(text.get("tools", []))
        ok = not opened_voice and "open_dashboard" not in tools and text["accepte"] and bool(text.get("assistant"))
        noter("E10", "Aucune ouverture intempestive quand le Dashboard est seulement mentionné", REUSSI if ok else ECHEC,
              f"« tableau de bord de ma voiture » (voix) → ouvert={opened_voice} ; « tableau de bord Excel » (écrit) "
              f"accepté et répondu={text['accepte'] and bool(text.get('assistant'))} ; outils appelés={tools}",
              "" if ok else self.diagnostic())

    def tout(self):
        etapes = (("E01", self.e01), ("E02", self.e02), ("E03", self.e03), ("E04", self.e04_e05),
                  ("E06", self.e06), ("E07", self.e07), ("E08", self.e08), ("E09", self.e09), ("E10", self.e10))
        for cid, step in etapes:
            debut = time.monotonic()
            print(f"--- {cid} ---", flush=True)
            try:
                step()
            except Exception as exc:
                ids = ("E04", "E05") if cid == "E04" else (cid,)
                for each in ids:
                    noter(each, f"Scénario {each}", ECHEC, f"exception : {decrire_exception(exc)}",
                          self.diagnostic(depuis=debut) + " | " + traceback.format_exc(limit=3)[-500:])
            print(f"    ({time.monotonic() - debut:.1f} s)", flush=True)


# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--natif", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.natif and sys.platform == "win32":
        os.environ["QT_QPA_PLATFORM"] = "windows"
    else:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ICI))
    from src.settings import DEFAULT_MODEL

    model = os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_MODEL
    api_key = "" if args.selftest else (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    rapport = {"mode": "selftest (faux serveur)" if args.selftest else "réel", "modele": model, "entree": "",
               "chemin_audio": "AudioIO réelle + pont micro de production, capture continue (fausse carte son)",
               "debut": time.strftime("%Y-%m-%dT%H:%M:%S"), "erreur": "", "scenarios": RESULTATS}
    labo = None

    def ecrire_rapport():
        rapport["fin"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        rapport["exceptions_python"] = len(EVENEMENTS["exceptions"])
        if labo is not None:
            rapport["sessions"] = {
                "generation_finale": int(labo.gemini.session_generation or 0),
                "reprises_serveur_muet": int(getattr(labo.gemini, "stalls_recovered", 0)),
                "erreurs": [d for _t, d in labo.erreurs][-10:],
                "interruptions_signalees": len(labo.interruptions),
            }
        try:
            import outils_surs

            rapport["outils_neutralises_appeles"] = sorted(set(outils_surs.APPELS_NEUTRALISES))
        except Exception:
            pass
        (out / "gemini_reel.json").write_text(masquer(json.dumps(rapport, ensure_ascii=False, indent=2)),
                                              encoding="utf-8")

    if not args.selftest and not api_key:
        rapport["erreur"] = "clé indisponible"
        ecrire_rapport()
        return 2

    prompts = list(PROMPTS_VOIX.values())
    pcm: dict[str, bytes] = {}
    try:
        if args.selftest:
            pcm = {k: enonce_synthetique(v) for k, v in PROMPTS_VOIX.items()}
            rapport["entree"] = ("faux serveur (énoncés synthétiques, une salve par phrase, pauses de 0,9 s ; "
                                 "VAD par énergie)")
        else:
            cache, why = synthese_locale(prompts)
            if cache is None:
                print(f"[entrée] {why} — synthèse via une session Live dédiée.", flush=True)
                cache = asyncio.run(synthese_live(api_key, model, prompts))
                why = "synthèse par une session Gemini Live dédiée (voix réelle, pas une tonalité)"
            pcm = {k: cache[v] for k, v in PROMPTS_VOIX.items()}
            pcm["STOP"] = ajuster_niveau(pcm["STOP"], NIVEAU_STOP)
            rapport["entree"] = why
    except Exception as exc:
        rapport["erreur"] = f"synthèse des énoncés impossible : {decrire_exception(exc)}"
        ecrire_rapport()
        return 3

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    sys.excepthook = _excepthook
    import outils_surs

    from controle_dashboard_reel import Contexte, fabrique
    from tests.dashboard_support import isolated_jarvis

    code = 1
    with isolated_jarvis(prefix="jarvis-validation-gemini-") as env:
        ctx = Contexte(out, natif=bool(args.natif), notification_native=False)
        ctx.app, ctx.env = app, env
        controller = fabrique(ctx, attach_bridge=True)
        try:
            outils_surs.installer()
            server = client = None
            if args.selftest:
                from tests.real_gemini_harness import RealAcousticPath

                server, client = installer_faux_serveur(RealAcousticPath())
            labo = construire_labo(api_key or "cle-factice-selftest", model, client)
            if server is not None:
                server.path = labo.path  # la VAD du faux serveur « entend » le même micro simulé
            labo.start(timeout=90)
            Scenarios(app, labo, controller, pcm, args.selftest, server).tout()
            code = 0 if all(r["statut"] != ECHEC for r in RESULTATS) else 1
        except Exception as exc:
            rapport["erreur"] = decrire_exception(exc)
            traceback.print_exc()
            code = 3
        finally:
            if labo is not None:
                try:
                    labo.arreter()
                except Exception:
                    pass
            try:
                controller.shutdown()
            except Exception:
                pass
            ecrire_rapport()
            pcm.clear()
    print("GEMINI DASHBOARD : " + ("SELFTEST " if args.selftest else "")
          + ", ".join(f"{r['id']}={r['statut']}" for r in RESULTATS), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
