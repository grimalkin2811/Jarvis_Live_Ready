#!/usr/bin/env python3
"""Matrice discriminante 1008 « Requested entity was not found » (v1.9.0).

Objectif
--------
Le 1008 survient pendant S2 (session tuée par le SERVICE en pleine réponse,
audio déjà reçu, puis budget de récupération épuisé). Trois hypothèses de
CONFIGURATION sont sous notre contrôle et ne sont pas distinguables par les
runs actuels, parce qu'ils testent toujours la même combinaison :

* **H-history** — ``historyConfig.initialHistoryInClientContent`` est envoyé
  à un modèle audio 2.x, dont la documentation interne du projet dit qu'il y
  est « sans effet ». Un champ sans effet côté client peut être un champ
  REJETÉ côté serveur, éventuellement de façon différée.
* **H-compression** — ``ContextWindowCompressionConfig(sliding_window=
  SlidingWindow())`` est envoyé avec ``target_tokens=None`` (vérifié dans le
  SDK installé). Une fenêtre glissante sans cible est au mieux inerte, au
  pire une compaction par tour. Google documente la compression comme un
  facteur AGGRAVANT de bugs serveur sur les modèles audio natifs
  (googleapis/python-genai#2117).
* **H-lifecycle** — ni l'une ni l'autre : c'est le cycle de vie (handle de
  reprise, durée de session) qui est en cause.

Méthode
-------
Le MÊME scénario minimal (un tour, reprise de session, un tour) est rejoué
quatre fois en ne changeant QU'UNE variable à la fois : un plan factoriel
complet 2×2 sur (history, compression). Si le 1008 disparaît dans exactement
une des quatre cases, le paramètre est isolé. S'il persiste partout, seule
H-lifecycle survit — et il faut alors regarder les handles et les codes de
fermeture, pas la configuration.

Ce plan est VOLONTAIREMENT minimal : 4 exécutions courtes, pas de rafale.

Garanties
---------
* Aucune clé n'est affichée, écrite ni journalisée (masquage des motifs
  ``AIza…`` / ``AQ.…`` identique au reste du projet).
* Sans ``GEMINI_API_KEY``, rien n'est exécuté et le script sort en
  ``NON TESTABLE`` (code 2) : jamais de faux succès.
* ``--dry-run`` affiche le plan sans aucun appel réseau (testable hors ligne).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys

_KEY_RE = re.compile(r"(AIza[0-9A-Za-z_\-]{8,}|AQ\.[0-9A-Za-z_\-]{8,})")

MODEL = os.getenv("JARVIS_DIAG_MODEL", "gemini-2.5-flash-native-audio-preview-12-2025")

#: Durée maximale d'attente d'un tour (s). Court : un échec doit être rapide.
TURN_TIMEOUT = 25.0

#: Plan factoriel 2×2 : une seule variable change par ligne.
COMBOS = (
    ("A", "référence (prod actuelle)", True, True),
    ("B", "sans historyConfig", False, True),
    ("C", "sans compression", True, False),
    ("D", "sans les deux", False, False),
)


def redact(text: str) -> str:
    return _KEY_RE.sub("<clé masquée>", str(text))


def find_api_key() -> str | None:
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None


def describe(error: BaseException) -> str:
    """Classe + code, toujours masqué et borné."""
    code = getattr(error, "code", None)
    label = type(error).__name__
    head = f"{label}(code={code})" if code is not None else label
    return redact(f"{head}: {str(error)[:200]}")


def classify(error: BaseException | None, where: str, handle_seen: bool) -> tuple[str, str]:
    """Traduit l'issue d'une sonde en (code lisible, détail)."""
    if error is None:
        return "OK", f"reprise réussie ({where})"
    text = f"{error} {getattr(error, 'code', '')}".lower()
    if "1008" in text or "entity was not found" in text:
        return "1008", f"1008 pendant {where} (handle_reçu={handle_seen})"
    if "1007" in text or "invalid session handle" in text:
        return "1007", f"handle refusé pendant {where}"
    if any(marker in text for marker in ("api key", "401", "403", "permission")):
        return "AUTH", f"authentification refusée pendant {where}"
    if any(marker in text for marker in ("429", "quota", "resource_exhausted")):
        return "QUOTA", f"quota/limite pendant {where}"
    return "AUTRE", f"{describe(error)} pendant {where}"


async def _wait_turn(session, timeout: float) -> tuple[bool, str | None]:
    """Consomme un tour ; retourne (a_terminé, handle_de_reprise_observé)."""
    handle: str | None = None

    async def consume() -> bool:
        nonlocal handle
        async for message in session.receive():
            update = getattr(message, "session_resumption_update", None)
            if update is not None and getattr(update, "new_handle", None):
                handle = update.new_handle
            content = getattr(message, "server_content", None)
            if content is not None and getattr(content, "turn_complete", False):
                return True
        return False

    try:
        done = await asyncio.wait_for(consume(), timeout)
    except asyncio.TimeoutError:
        return False, handle
    return done, handle


async def probe(client, model: str, history: bool, compression: bool) -> dict:
    """Rejoue le scénario S2 minimal avec la configuration demandée."""
    from google.genai import types

    extras: dict = {}
    if history:
        extras["history_config"] = types.HistoryConfig(initial_history_in_client_content=True)
    if compression:
        extras["context_window_compression"] = types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow()
        )

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction="Tu es un assistant de diagnostic. Réponds en une phrase courte.",
        session_resumption=types.SessionResumptionConfig(),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        **extras,
    )

    result: dict = {
        "history": history,
        "compression": compression,
        "tour1": "-",
        "handle": False,
        "reprise": "-",
        "tour2": "-",
        "code": "AUTRE",
        "detail": "",
    }

    handle: str | None = None
    error: BaseException | None = None
    where = "connexion initiale"

    try:
        async with client.aio.live.connect(model=model, config=config) as session:
            where = "tour 1"
            await session.send_client_content(
                turns=[{"role": "user", "parts": [{"text": "Mon prénom est Simon."}]}],
                turn_complete=True,
            )
            done, handle = await _wait_turn(session, TURN_TIMEOUT)
            result["tour1"] = "ok" if done else "timeout"
            result["handle"] = bool(handle)

        where = "reprise de session"
        resume_config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=config.system_instruction,
            session_resumption=types.SessionResumptionConfig(handle=handle),
            **extras,
        )
        async with client.aio.live.connect(model=model, config=resume_config) as session:
            where = "tour 2"
            await session.send_client_content(
                turns=[{"role": "user", "parts": [{"text": "Quel est mon prénom ?"}]}],
                turn_complete=True,
            )
            done2, _ = await _wait_turn(session, TURN_TIMEOUT)
            result["tour2"] = "ok" if done2 else "timeout"
        result["reprise"] = "ok"
    except BaseException as exc:  # noqa: BLE001 - toute erreur est une donnée ici
        error = exc
        result["reprise"] = "échec" if where == "reprise de session" else result["reprise"]

    code, detail = classify(error, where, bool(handle))
    result["code"] = code
    result["detail"] = detail
    return result


def print_plan() -> None:
    print("Matrice discriminante 1008 — plan (aucun appel réseau)\n")
    print(f"modèle : {MODEL}")
    print(f"combinaisons : {len(COMBOS)} × 2 sessions courtes (un tour chacune)\n")
    header = f"{'case':<5} {'hypothèse testée':<26} {'history':<8} {'compression':<12}"
    print(header)
    print("-" * len(header))
    for key, label, history, compression in COMBOS:
        print(f"{key:<5} {label:<26} {str(history):<8} {str(compression):<12}")
    print("\nLecture des résultats :")
    print("  - 1008 uniquement en A        -> hypothèse H-history OU H-compression")
    print("  - 1008 en A et C, jamais B/D  -> H-history isolée")
    print("  - 1008 en A et B, jamais C/D  -> H-compression isolée")
    print("  - 1008 dans les 4 cases       -> H-lifecycle (handle/cycle de vie)")
    print("  - aucun 1008                  -> le défaut ne se reproduit pas ici :")
    print("                                   ne rien conclure sans traces.")


def print_matrix(rows: list[dict]) -> None:
    print("\n" + "=" * 78)
    print("MATRICE 1008 — résultats réels")
    print("=" * 78)
    header = f"{'case':<5} {'history':<8} {'compress':<9} {'tour1':<8} {'handle':<7} {'tour2':<8} {'issue':<8}"
    print(header)
    print("-" * len(header))
    for key, row in zip([c[0] for c in COMBOS], rows):
        print(
            f"{key:<5} {str(row['history']):<8} {str(row['compression']):<9} "
            f"{row['tour1']:<8} {str(row['handle']):<7} {row['tour2']:<8} {row['code']:<8}"
        )
    print("-" * len(header))
    for key, row in zip([c[0] for c in COMBOS], rows):
        print(f"{key} : {row['detail']}")

    codes = {row["code"] for row in rows}
    print()
    if "QUOTA" in codes or "AUTH" in codes:
        print("VERDICT : NON TESTABLE — authentification ou quota en défaut.")
        print("          Aucune conclusion sur le 1008 n'est possible.")
        return
    if "1008" in codes:
        cases = [k for k, row in zip([c[0] for c in COMBOS], rows) if row["code"] == "1008"]
        if len(cases) == 4:
            print("VERDICT : hypothèse H-lifecycle — le 1008 apparaît dans les")
            print("          quatre combinaisons. La configuration n'est pas le")
            print("          facteur : examiner handles et codes de fermeture.")
        else:
            print(f"VERDICT : le 1008 n'apparaît qu'en {', '.join(cases)} — un")
            print("          paramètre discriminant est isolé (cf. lignes ci-dessus).")
    elif codes == {"OK"}:
        print("VERDICT : 1008 NON REPRODUIT sur ce modèle à cet instant.")
        print("          Ce n'est pas une preuve d'innocuité : relancer sous")
        print("          charge (S1->S6 complet) avant de conclure.")
    else:
        print(f"VERDICT : issues hétérogènes ({', '.join(sorted(codes))}) —")
        print("          lire le détail par case avant toute conclusion.")
    print("=" * 78)


async def run_matrix(api_key: str) -> int:
    from google import genai

    client = genai.Client(api_key=api_key)
    rows: list[dict] = []
    for key, label, history, compression in COMBOS:
        print(f"\n[case {key}] {label} (history={history}, compression={compression})")
        row = await probe(client, MODEL, history, compression)
        print(f"    -> {row['code']} : {row['detail']}")
        rows.append(row)
    print_matrix(rows)
    return 0 if all(row["code"] == "OK" for row in rows) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                       help="affiche le plan sans aucun appel réseau")
    args = parser.parse_args(argv)

    if args.dry_run:
        print_plan()
        return 0

    api_key = find_api_key()
    if not api_key:
        print_plan()
        print("\nAUCUNE CLÉ TROUVÉE (GEMINI_API_KEY / GOOGLE_API_KEY).")
        print("Matrice 1008 : NON TESTABLE dans cet environnement.")
        return 2

    return asyncio.run(run_matrix(api_key))


if __name__ == "__main__":
    sys.exit(main())
