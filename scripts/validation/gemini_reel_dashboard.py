"""Scénarios RÉELS Dashboard + Gemini Live (E1 à E10) — « mini-Jarvis ».

Reproduit la composition de production (``src/ui.py``) :

* thread Qt : Dashboard réel (``DashboardController`` relié au pont vocal),
  widget de conversation réel pour les messages écrits ;
* thread moteur : boucle asyncio dédiée, VRAIE session ``GeminiLive`` (le
  ``RealRunner`` du script officiel ``scripts/validate_real_gemini.py``,
  réutilisé tel quel), contexte de conversation PARTAGÉ par défaut,
  ``RUNTIME.register_text_handler(gemini.submit_text_threadsafe)`` et états du
  moteur publiés comme en production ;
* entrée vocale : AUDIO RÉEL envoyé par ``send_realtime_input`` (VAD serveur,
  transcription), synthétisé localement (voix française SAPI via le script
  officiel ``validate_reconnect_v174_real.py``) ou, à défaut, par une session
  Live dédiée. Le micro de l'utilisateur n'est jamais ouvert ici.

Outils : déclarations de production, exécution limitée à la liste blanche de
``outils_surs.py`` (aucune action sur le PC, aucun envoi à un tiers).

Clé : variable ``GEMINI_API_KEY`` posée par l'orchestrateur pour CE processus
seulement ; jamais affichée, jamais écrite. Aucun enregistrement audio n'est
conservé.

``--selftest`` : même harnais contre le faux serveur (aucune clé, aucun
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
import sys
import tempfile
import threading
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

RESULTATS: list[dict] = []
EVENEMENTS: dict[str, list] = {"exceptions": []}


def _excepthook(exc_type, exc, tb):
    EVENEMENTS["exceptions"].append("".join(traceback.format_exception(exc_type, exc, tb))[-1500:])
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
    RESULTATS.append({"id": cid, "titre": titre, "statut": statut, "resultat": resultat, "preuve": preuve})
    print(f"[{cid}] {statut} — {titre} : {resultat}", flush=True)


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

    vrg = sys.modules["validate_real_gemini"]
    client = genai.Client(api_key=api_key)
    return await vrg.synthesize_all_via_live(client, model, prompts)


# ---------------------------------------------------------------------------
# Moteur : boucle asyncio dédiée (comme le thread vocal de production)
# ---------------------------------------------------------------------------


class Moteur:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._main, name="validation-moteur", daemon=True)
        self.runner = None

    def _main(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def demarrer(self) -> None:
        self.thread.start()

    def soumettre(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def arreter(self, app) -> None:
        if self.runner is not None:
            try:
                attendre_futur(app, self.soumettre(self.runner.stop()), 20)
            except Exception:
                pass
            try:
                from src.runtime_status import RUNTIME

                RUNTIME.unregister_text_handler()
            except Exception:
                pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


def attendre_futur(app, future, timeout: float):
    deadline = time.monotonic() + timeout
    while not future.done():
        app.processEvents()
        time.sleep(0.01)
        if time.monotonic() > deadline:
            future.cancel()
            raise TimeoutError(f"pas de résultat après {timeout:.0f} s")
    return future.result()


def attendre(app, predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(predicate())


def fabriquer_runner(api_key: str, model: str):
    """Construit (sur la boucle du moteur) le RealRunner câblé comme en production."""
    vrg = sys.modules["validate_real_gemini"]
    from src.conversation import set_default_conversation_context
    from src.runtime_status import RUNTIME, EngineState

    class RunnerDashboard(vrg.RealRunner):
        def __init__(self) -> None:
            super().__init__(api_key, model, None)
            # Contexte PARTAGÉ (production : get_default_conversation_context()).
            set_default_conversation_context(self.conversation)
            RUNTIME.register_text_handler(self.gemini.submit_text_threadsafe)

        async def _run(self) -> None:  # même boucle que src/ui.py, états publiés
            gemini = self.gemini
            while not self._stop.is_set():
                gemini.reconnect_requested = False
                try:
                    RUNTIME.set_engine_state(EngineState.CONNECTING, "ouverture de la session Live")
                    await gemini.connect()
                    RUNTIME.set_engine_state(EngineState.READY, "", model=str(gemini.model or ""),
                                             session_generation=int(getattr(gemini, "session_generation", 0) or 0))
                    self._connected.set()
                    await gemini.receive_loop()
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    vrg.safe_print(f"   [session] erreur : {type(exc).__name__}")
                    RUNTIME.set_engine_state(EngineState.RECONNECTING, "nouvelle tentative")
                    if not gemini.reconnect_requested:
                        await asyncio.sleep(1.0)
                finally:
                    try:
                        await gemini.close()
                    except Exception:
                        pass

    async def build():
        return RunnerDashboard()

    return build


# ---------------------------------------------------------------------------
# Scénarios
# ---------------------------------------------------------------------------


class Scenarios:
    def __init__(self, app, moteur: Moteur, controller, pcm: dict[str, bytes], selftest: bool, server=None) -> None:
        self.app = app
        self.moteur = moteur
        self.runner = moteur.runner
        self.controller = controller
        self.pcm = pcm
        self.selftest = selftest
        self.server = server
        self.widget = None

    # -- primitives ----------------------------------------------------------------
    def parler(self, cle: str, timeout: float = 120.0) -> dict:
        if self.server is not None:
            self.server.current.queue_utterance(PROMPTS_VOIX[cle], 3)
        turn = attendre_futur(self.app, self.moteur.soumettre(self.runner.speak_pcm(self.pcm[cle])), timeout)
        return dict(turn or {})

    def parler_sans_attendre(self, cle: str):
        if self.server is not None:
            self.server.current.queue_utterance(PROMPTS_VOIX[cle], 3)
        return self.moteur.soumettre(self.runner.speak_pcm(self.pcm[cle]))

    def ouvrir_conversation(self) -> bool:
        from controle_dashboard_reel import page_contenant, widget_par_type

        page = page_contenant(self.controller, "jarvis.conversation")
        if page is None:
            return False
        self.controller.open(page)
        attendre(self.app, self.controller.is_open, 5)
        _page, self.widget = widget_par_type(self.controller.window, "jarvis.conversation")
        return self.widget is not None

    def ecrire(self, cle: str, attendre_reponse: bool = True, timeout: float = 120.0) -> dict:
        if self.widget is None or self.controller.window is None or not self.controller.is_open():
            self.ouvrir_conversation()
        widget = self.widget
        attendre(self.app, lambda: widget.send_button.isEnabled(), 30)
        before = len(self.runner.turn_log)
        widget.input.setPlainText(TEXTES[cle])
        widget.send()
        attendre(self.app, lambda: not widget._pending, 20)
        banner = widget.banner.text() if widget.banner.isVisible() else ""
        result = {"accepte": not banner and widget.input.toPlainText() == "", "banniere": banner,
                  "texte_conserve": widget.input.toPlainText() == TEXTES[cle]}
        if result["accepte"] and attendre_reponse:
            attendre(self.app, lambda: len(self.runner.turn_log) > before, timeout)
            if len(self.runner.turn_log) > before:
                result.update(self.runner.turn_log[-1])
        return result

    def attendre_parole(self, timeout: float = 30.0) -> bool:
        return attendre(self.app, lambda: bool(self.runner.gemini.speaking) or bool(self.runner._audio_chunks), timeout)

    def message_dans_contexte(self, texte: str) -> bool:
        from src.conversation import get_default_conversation_context

        return any(texte in (m.text or "") for m in get_default_conversation_context().get_messages())

    # -- E1 à E10 -------------------------------------------------------------------
    def e01(self):
        turn = self.parler("V1")
        ok = bool(turn.get("assistant")) and not self.controller.is_open()
        noter("E01", "Conversation vocale normale avant ouverture du Dashboard", REUSSI if ok else ECHEC,
              f"transcription utilisateur « {extrait(turn.get('user'), 80)} », réponse « {extrait(turn.get('assistant'))} », "
              f"Dashboard fermé={not self.controller.is_open()}")

    def e02(self):
        turn = self.parler("V2")
        opened = attendre(self.app, lambda: self.controller.is_open(), 10)
        page = self.controller.window.current_page() if self.controller.window is not None else None
        ok = "open_dashboard" in turn.get("tools", []) and opened and page == "memoire"
        noter("E02", "Ouverture du Dashboard pendant une conversation (commande vocale)", REUSSI if ok else ECHEC,
              f"outils={turn.get('tools')}, ouvert={opened}, page={page}, réponse « {extrait(turn.get('assistant'))} »")

    def e03(self):
        if not self.ouvrir_conversation():
            noter("E03", "Message écrit depuis le Dashboard et réponse", NON_TESTE, "widget Conversation absent")
            return
        result = self.ecrire("T1")
        ok = result["accepte"] and bool(result.get("assistant"))
        noter("E03", "Message écrit depuis le Dashboard et réponse", REUSSI if ok else ECHEC,
              f"accepté={result['accepte']}, bannière « {extrait(result['banniere'], 80)} », "
              f"réponse « {extrait(result.get('assistant'))} »")

    def e04_e05(self):
        text = self.ecrire("T2")
        name_ok = text["accepte"] and "ada" in (text.get("assistant") or "").lower()
        reopened = attendre(self.app, self.runner.gemini.can_send, 10)
        voice = self.parler("V3")
        code_ok = "orion" in (voice.get("assistant") or "").lower()
        noter("E04", "Continuité du contexte voix → texte → voix", REUSSI if (name_ok and code_ok) else ECHEC,
              f"prénom dit à la voix retrouvé par écrit={name_ok} (« {extrait(text.get('assistant'), 80)} ») ; "
              f"code donné par écrit retrouvé à la voix={code_ok} (« {extrait(voice.get('assistant'), 80)} »)")
        ok5 = reopened and bool(voice.get("user")) and bool(voice.get("assistant"))
        noter("E05", "Reprise de la voix après une réponse écrite", REUSSI if ok5 else ECHEC,
              f"micro rouvert après la réponse écrite={reopened}, tour vocal transcrit={bool(voice.get('user'))}, "
              f"réponse={bool(voice.get('assistant'))}")

    def e06(self):
        if self.selftest:
            noter("E06", "Message écrit pendant que Jarvis parle ou travaille", NON_TESTE,
                  "faux serveur : réponse instantanée, pas de fenêtre « occupé » reproductible")
            return
        future = self.parler_sans_attendre("L1")
        speaking = self.attendre_parole(40)
        refused = self.ecrire("T4", attendre_reponse=False) if speaking else {"accepte": None, "banniere": ""}
        try:
            attendre_futur(self.app, future, 150)
        except Exception:
            pass
        absent = not self.message_dans_contexte(TEXTES["T4"])
        after = self.ecrire("T4B")
        ok = (speaking and refused.get("accepte") is False and "occupé" in refused.get("banniere", "")
              and refused.get("texte_conserve") and absent and after["accepte"])
        noter("E06", "Message écrit pendant que Jarvis parle ou travaille", REUSSI if ok else ECHEC,
              f"Jarvis parlait={speaking}, refus explicite « {extrait(refused.get('banniere'), 90)} », texte conservé="
              f"{refused.get('texte_conserve')}, absent du contexte={absent}, message suivant accepté={after['accepte']}")

    def e07(self):
        future = self.parler_sans_attendre("L2")
        speaking = self.attendre_parole(40) if not self.selftest else True
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
        try:
            turn = dict(attendre_futur(self.app, future, 150) or {})
        except Exception as exc:
            turn = {"erreur": f"{type(exc).__name__}"}
        after = self.parler("VA")
        new_exc = EVENEMENTS["exceptions"][exc_before:]
        ok = speaking and reopened and not new_exc and bool(turn.get("assistant")) and "madrid" in (after.get("assistant") or "").lower()
        noter("E07", "Fermeture et réouverture du Dashboard pendant une réponse", REUSSI if ok else ECHEC,
              f"fermé/libéré puis rouvert pendant la réponse={reopened}, exceptions={len(new_exc)}, réponse complète="
              f"{bool(turn.get('assistant'))}, tour vocal suivant correct={'madrid' in (after.get('assistant') or '').lower()}",
              new_exc[0][-300:] if new_exc else "")

    def e08(self):
        details, ok = [], True
        if self.selftest:
            details.append("interruption : non reproductible sur le faux serveur")
        else:
            before = len(self.runner.turn_log)
            future = self.parler_sans_attendre("L3")
            speaking = self.attendre_parole(40)
            interrupted = False
            if speaking:
                self.runner.gemini.request_interrupt()  # même appel que l'interruption manuelle de production
                try:
                    attendre_futur(self.app, self.moteur.soumettre(self.runner.speak_pcm(self.pcm["STOP"])), 120)
                except Exception:
                    pass
                try:
                    attendre_futur(self.app, future, 60)
                except Exception:
                    pass
                interrupted = any(t.get("interrupted") for t in self.runner.turn_log[before:])
            ok &= speaking and interrupted
            details.append(f"interruption pendant la réponse : parlait={speaking}, interrompue={interrupted}")
        self.runner.connected.clear()
        attendre_futur(self.app, self.moteur.soumettre(self.runner.drop_connection(keep_handle=False)), 20)
        reconnected = attendre(self.app, lambda: self.runner.connected.is_set(), 60)
        text = self.ecrire("T5")
        code_ok = text["accepte"] and "orion" in (text.get("assistant") or "").lower()
        voice = self.parler("V6")
        name_ok = "ada" in (voice.get("assistant") or "").lower()
        ok &= reconnected and code_ok and name_ok
        details.append(f"coupure réseau simulée (session fermée brutalement, sans reprise) : reconnecté={reconnected}, "
                       f"contexte retrouvé par écrit={code_ok}, puis à la voix={name_ok}")
        noter("E08", "Interruption, coupure réseau et reconnexion", REUSSI if ok else ECHEC, " ; ".join(details))

    def e09(self):
        self.controller.hide_window()
        attendre(self.app, lambda: not self.controller.is_open(), 3)
        turns = [self.parler("VA"), self.parler("V6"), self.parler("V8")]
        answered = [bool(t.get("assistant")) for t in turns]
        ok = all(answered) and "madrid" in (turns[0].get("assistant") or "").lower() and "ada" in (turns[1].get("assistant") or "").lower()
        noter("E09", "Non-régression d'une conversation purement vocale (Dashboard fermé)", REUSSI if ok else ECHEC,
              f"3 tours vocaux consécutifs, réponses={answered}, exactitude capitale/prénom="
              f"{'madrid' in (turns[0].get('assistant') or '').lower()}/{'ada' in (turns[1].get('assistant') or '').lower()}")

    def e10(self):
        self.controller.hide_window()
        attendre(self.app, lambda: not self.controller.is_open(), 3)
        voice = self.parler("V7")
        opened_voice = attendre(self.app, self.controller.is_open, 3)
        text = self.ecrire("T3")
        tools = list(voice.get("tools", [])) + list(text.get("tools", []))
        ok = not opened_voice and "open_dashboard" not in tools and text["accepte"]
        noter("E10", "Aucune ouverture intempestive quand le Dashboard est seulement mentionné", REUSSI if ok else ECHEC,
              f"« tableau de bord de ma voiture » (voix) → ouvert={opened_voice} ; « tableau de bord Excel » (écrit) ; "
              f"outils appelés={tools}")

    def tout(self):
        for step in (self.e01, self.e02, self.e03, self.e04_e05, self.e06, self.e07, self.e08, self.e09, self.e10):
            try:
                step()
            except Exception as exc:
                name = step.__name__.upper().replace("_", "/")
                noter(name[:3], f"Scénario {name}", ECHEC, f"exception : {type(exc).__name__}: {exc}",
                      traceback.format_exc(limit=4)[-600:])


# ---------------------------------------------------------------------------


def installer_faux_serveur():
    from tests.live_harness import FakeClient, FakeLiveServer

    server = FakeLiveServer("2.x")
    server.text_turns_use_oracle = True
    server.oracle.register(r"je m'appelle ada", lambda pairs: "Enchanté Ada.")
    server.oracle.register(r"ORION-4", lambda pairs: "Noté.")
    server.oracle.register(r"comment je m'appelle|mon prénom", lambda pairs: "Tu t'appelles Ada.")
    server.oracle.register(r"code", lambda pairs: "Le code est ORION-4.")
    server.oracle.register(r"capitale de l'Espagne", lambda pairs: "Madrid.")
    server.oracle.register(r"tableau de bord sur la page", lambda pairs: "C'est ouvert.")
    server.oracle.register(r".", lambda pairs: "Réponse du faux serveur.")
    server.next_tool_script = (r"tableau de bord sur la page", "open_dashboard", {"page": "memoire"})
    return server, FakeClient


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
    charger("validate_real_gemini", ROOT / "scripts" / "validate_real_gemini.py")  # module partagé (RealRunner…)
    from src.settings import DEFAULT_MODEL

    model = os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_MODEL
    api_key = "" if args.selftest else (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    rapport = {"mode": "selftest (faux serveur)" if args.selftest else "réel", "modele": model, "entree": "",
               "debut": time.strftime("%Y-%m-%dT%H:%M:%S"), "erreur": "", "scenarios": RESULTATS}

    def ecrire_rapport():
        rapport["fin"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        rapport["exceptions_python"] = len(EVENEMENTS["exceptions"])
        try:
            import outils_surs

            rapport["outils_neutralises_appeles"] = sorted(set(outils_surs.APPELS_NEUTRALISES))
        except Exception:
            pass
        (out / "gemini_reel.json").write_text(json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.selftest and not api_key:
        rapport["erreur"] = "clé indisponible"
        ecrire_rapport()
        return 2

    prompts = list(PROMPTS_VOIX.values())
    pcm: dict[str, bytes] = {}
    server = None
    try:
        if args.selftest:
            chunk = 16000 * 2 * 100 // 1000
            pcm = {k: b"\x01\x02" * (chunk * 3 // 2) for k in PROMPTS_VOIX}
            rapport["entree"] = "faux serveur (aucun audio réel)"
        else:
            cache, why = synthese_locale(prompts)
            if cache is None:
                print(f"[entrée] {why} — synthèse via une session Live dédiée.", flush=True)
                cache = asyncio.run(synthese_live(api_key, model, prompts))
                why = "synthèse par une session Gemini Live dédiée (voix réelle, pas une tonalité)"
            pcm = {k: cache[v] for k, v in PROMPTS_VOIX.items()}
            rapport["entree"] = why
    except Exception as exc:
        rapport["erreur"] = f"synthèse des énoncés impossible : {type(exc).__name__}"
        ecrire_rapport()
        return 3

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    sys.excepthook = _excepthook
    import outils_surs

    from controle_dashboard_reel import Contexte, fabrique
    from tests.dashboard_support import isolated_jarvis

    moteur = Moteur()
    code = 1
    with isolated_jarvis(prefix="jarvis-validation-gemini-") as env:
        ctx = Contexte(out, natif=bool(args.natif), notification_native=False)
        ctx.app, ctx.env = app, env
        controller = fabrique(ctx, attach_bridge=True)
        try:
            outils_surs.installer()
            moteur.demarrer()
            if args.selftest:
                server, fake_client = installer_faux_serveur()
            moteur.runner = attendre_futur(app, moteur.soumettre(fabriquer_runner(api_key or "cle-factice-selftest", model)()), 30)
            if server is not None:
                moteur.runner.gemini.client = fake_client(server)
            attendre_futur(app, moteur.soumettre(moteur.runner.start()), 90)
            Scenarios(app, moteur, controller, pcm, args.selftest, server).tout()
            code = 0 if all(r["statut"] != ECHEC for r in RESULTATS) else 1
        except Exception as exc:
            rapport["erreur"] = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
            code = 3
        finally:
            moteur.arreter(app)
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
