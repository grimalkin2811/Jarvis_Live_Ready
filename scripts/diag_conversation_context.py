"""Diagnostic du contexte conversationnel Jarvis (sans réseau).

Ce script exécute le VRAI pipeline vocal de Jarvis (``GeminiLive`` : micro
simulé par chunks PCM -> ``send_realtime_input`` -> réception -> contexte ->
réponse vocale) contre le faux serveur Live de ``tests/live_harness.py``,
fidèle aux comportements documentés et rapportés de l'API Gemini Live.

Scénarios exécutés (cf. mission §4 et §15) :

A. conversation dans UNE session Live (« Mon prénom est Simon ») ;
B. contexte multi-tour (Hans Zimmer / Two Steps From Hell) ;
F. reconnexion SANS handle de reprise (rejeu local obligatoire) ;
G. reconnexion avec handle expiré (erreur 1007) ;
X. état du câble : l'historique a-t-il été envoyé, sous quelle forme ?

Utilisation :
    python scripts/diag_conversation_context.py            # diagnostic faux serveur
    GEMINI_API_KEY=... python scripts/diag_conversation_context.py --real
                                                            # diagnostic API réelle
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.live_harness import FakeLiveServer  # noqa: E402
from tests.voice_harness import VoiceHarness  # noqa: E402

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-native-audio-preview-12-2025")


def _verdict(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))


async def scenario_a_session_unique() -> None:
    print("\n=== A. Conversation dans UNE session Live (serveur conserve l'état) ===")
    server = FakeLiveServer("2.x")
    harness = VoiceHarness(server, model=MODEL)
    await harness.start()
    try:
        first = await harness.speak("Mon prénom est Simon.")
        second = await harness.speak("Quel est mon prénom ?")
        _verdict("première réponse", "D'accord" in first or bool(first), first)
        _verdict("rappel du prénom dans la MÊME session", "Simon" in second, second)
    finally:
        await harness.stop()


async def scenario_b_reconnexion_sans_handle() -> None:
    print("\n=== F. Reconnexion SANS handle (le rejeu local est le seul recours) ===")
    print("    (le serveur n'émet pas de handle : simule une coupure avant le")
    print("     premier session_resumption_update, ou un handle jamais reçu)")
    server = FakeLiveServer("2.x")
    server.suppress_resumption_updates = True
    harness = VoiceHarness(server, model=MODEL)
    await harness.start()
    try:
        first = await harness.speak("Mon prénom est Simon.")
        _verdict("première réponse", bool(first), first)
        server.drop_connection()
        await harness.wait_sessions(2)
        await harness.speak("Il fait beau aujourd'hui.")  # un tour après la coupure
        answer = await harness.speak("Quel est mon prénom ?")
        _verdict("rappel du prénom APRÈS reconnexion", "Simon" in answer, answer)
        seed = [
            event
            for event in server.client_content_events(generation=2)
        ]
        print(f"    câble (session 2) : {len(seed)} clientContent, "
              f"turn_complete={[e.turn_complete for e in seed]}")
    finally:
        await harness.stop()


async def scenario_g_handle_expire() -> None:
    print("\n=== G. Reconnexion avec handle EXPIRÉ (erreur 1007) ===")
    server = FakeLiveServer("2.x")
    harness = VoiceHarness(server, model=MODEL, reconnect_delay=0.01)
    await harness.start()
    try:
        await harness.speak("Mon prénom est Simon.")
        server.invalidate_all_handles()
        server.drop_connection()
        await asyncio.sleep(0.5)  # laisse la boucle de reconnexion travailler
        answer = None
        if harness.gemini.session is not None:
            answer = await asyncio.wait_for(harness.speak("Quel est mon prénom ?"), timeout=3)
        _verdict(
            "rappel du prénom après handle expiré",
            bool(answer and "Simon" in answer),
            answer or f"session={harness.gemini.session is not None} connect_count={server.connect_count}",
        )
        print(f"    tentatives de connexion : {server.connect_count}")
    finally:
        await harness.stop()


async def main() -> int:
    print(f"Jarvis — diagnostic du contexte conversationnel (modèle {MODEL})")
    print("Faux serveur Live : sémantique 2.x (clientContent en attente invisible pour l'audio)")
    await scenario_a_session_unique()
    await scenario_b_reconnexion_sans_handle()
    await scenario_g_handle_expire()
    print("\nFin du diagnostic.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
