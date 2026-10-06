"""Mesures de performance de la chaîne de contexte (mission §17).

Toutes les mesures ci-dessous sont exécutées contre le FAUX serveur Live
(``tests/live_harness.py``) : elles mesurent le coût propre du code Jarvis
(construction du contexte, rejeu, clôture du seed, latence observée par le
pipeline) SANS la latence réseau réelle de l'API Gemini. Les chiffres
« vraie API » (aller-retour WebSocket, temps de setup du serveur) ne sont PAS
mesurables dans cet environnement — cf. rapport final, section H.

Mesures :
  1. construction du contexte + ``to_gemini_contents`` + sérialisation JSON
     (le « rejeu » pur, hors réseau) pour des conversations de taille croissante ;
  2. création de session + rejeu complet via le pipeline réel (``GeminiLive``) ;
  3. latence de la première réponse après reconnexion (avec rejeu) vs tour
     en session établie (sans rejeu) ;
  4. mémoire résidente (RSS) et temps CPU consommés par 200 reconstructions.

Utilisation :
    python scripts/perf_context_replay.py
"""

from __future__ import annotations

import asyncio
import json
import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.conversation import (  # noqa: E402
    ConversationContext,
    estimate_tokens,
    to_gemini_contents,
)
from tests.live_harness import FakeLiveServer  # noqa: E402
from tests.voice_harness import VoiceHarness  # noqa: E402


def _rss_kb() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def build_context(n_turns: int) -> ConversationContext:
    """Contexte réaliste : alternance user/assistant + un tour avec outil."""
    ctx = ConversationContext()
    for i in range(n_turns):
        ctx.add_user_message(f"Dis-moi tout sur le sujet numéro {i}, s'il te plaît.")
        if i % 5 == 3:  # un tour sur cinq contient un appel d'outil
            ctx.add_tool_interaction(
                "music_search",
                {"query": f"artiste {i}"},
                {"success": True, "results": [f"artiste {i} — titre A", f"artiste {i} — titre B"]},
            )
        ctx.add_assistant_message(
            f"Voici ce que je sais du sujet numéro {i} : c'est un sujet fascinant, "
            "avec plusieurs aspects que je peux détailler si tu veux."
        )
        ctx.close_turn(reason="perf")
    return ctx


def measure_replay_build() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for n in (2, 5, 10, 20, 40):
        ctx = build_context(n)
        t0 = time.perf_counter()
        for _ in range(100):
            contents = to_gemini_contents(ctx.get_messages())
            payload = json.dumps(contents, ensure_ascii=False)
        build_ms = (time.perf_counter() - t0) * 1000 / 100
        tokens = sum(
            estimate_tokens(part.get("text", ""))
            for content in contents
            for part in content.get("parts", [])
        )
        rows.append(
            {
                "tours": n,
                "messages_contexte": len(ctx.get_messages()),
                "messages_rejetes": len(contents),
                "tokens_estimes": tokens,
                "payload_ko": len(payload.encode("utf-8")) / 1024,
                "build_ms": round(build_ms, 3),
            }
        )
    return rows


async def measure_pipeline() -> dict[str, object]:
    """Session + rejeu + première réponse, sur le pipeline vocal réel (faux câble)."""
    results: dict[str, object] = {}

    # -- reconnexion avec rejeu d'un contexte de 20 tours --------------------
    # Aucun handle n'est jamais délivré (suppress_resumption_updates dès le
    # départ) : la reconnexion DOIT donc passer par le rejeu complet (_seed).
    server = FakeLiveServer("2.x")
    server.suppress_resumption_updates = True
    harness = VoiceHarness(server)
    await harness.start()
    try:
        await harness.speak("Mon prénom est Simon.")
        for i in range(19):
            await harness.speak(f"Dis-moi en plus du sujet numéro {i}, s'il te plaît.")
        server.drop_connection()
        t0 = time.perf_counter()
        await harness.wait_sessions(2, timeout=5)
        connect_s = time.perf_counter() - t0
        seed = server.client_content_events(generation=2)
        seed_parts = sum(len(e.contents) for e in seed)
        seed_payload_ko = sum(
            len(json.dumps(e.contents, ensure_ascii=False).encode("utf-8")) for e in seed
        ) / 1024
        t1 = time.perf_counter()
        answer = await harness.speak("Quel est mon prénom ?", timeout=5)
        first_response_s = time.perf_counter() - t1
        results["rejeu_20_tours"] = {
            "connect_et_setup_s": round(connect_s, 4),
            "seed_events": len(seed),
            "roles_rejetees": seed_parts,
            "payload_rejeu_ko": round(seed_payload_ko, 2),
            "latence_premiere_reponse_s": round(first_response_s, 4),
            "reponse_contient_sujet": "Simon" in answer,
        }

        # -- tour en session établie (sans rejeu) ------------------------------
        t2 = time.perf_counter()
        answer2 = await harness.speak("Comment je m'appelle ?", timeout=5)
        results["tour_session_etablie"] = {
            "latence_reponse_s": round(time.perf_counter() - t2, 4),
            "reponse_contient_sujet": "Simon" in answer2,
        }
    finally:
        await harness.stop()
    return results


def measure_memory_cpu() -> dict[str, object]:
    ctx = build_context(20)
    rss_before = _rss_kb()
    cpu0 = time.process_time()
    for _ in range(200):
        payload = json.dumps(to_gemini_contents(ctx.get_messages()), ensure_ascii=False)
    cpu_s = time.process_time() - cpu0
    return {
        "rss_avant_kb": rss_before,
        "rss_apres_kb": _rss_kb(),
        "cpu_s_pour_200_builds": round(cpu_s, 4),
        "payload_unique_ko": round(len(payload.encode("utf-8")) / 1024, 1),
    }


def _print_table(title: str, rows: list[dict[str, object]]) -> None:
    print(f"\n## {title}\n")
    if not rows:
        return
    headers = list(rows[0].keys())
    print("| " + " | ".join(headers) + " |")
    print("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        print("| " + " | ".join(str(row[h]) for h in headers) + " |")


async def main() -> int:
    print("# Mesures de performance — chaîne de contexte v1.7.1 (faux serveur, hors latence réseau)")
    _print_table("1. Construction du rejeu (to_gemini_contents + JSON)", measure_replay_build())
    _print_table("2/3. Pipeline réel (connect + rejeu + première réponse)", [await measure_pipeline()])
    _print_table("4. Mémoire / CPU (200 reconstructions d'un contexte 20 tours)", [measure_memory_cpu()])
    print(
        "\nNOTE : mesures sur faux serveur — elles chiffrent le coût PROPRE du code Jarvis."
        " La latence réseau réelle de l'API Gemini (setup, aller-retour du seed)"
        " s'ajoute en production ; non mesurable ici sans clé API."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
