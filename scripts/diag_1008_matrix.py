#!/usr/bin/env python3
"""Matrice discriminante 1008 « Requested entity was not found » (v1.9.0).

Objectif
--------
Le 1008 survient pendant S2 (session tuée par le SERVICE en pleine réponse,
de l'audio déjà reçu, puis budget de récupération épuisé). Trois familles
d'hypothèses ne sont pas distinguables par les runs actuels, qui testent
toujours la même combinaison :

* **H-history** — ``historyConfig.initialHistoryInClientContent`` est envoyé
  à un modèle audio 2.x, dont la documentation interne du projet dit qu'il y
  est « sans effet ». Un champ sans effet côté client peut être un champ
  REJETÉ côté serveur, éventuellement de façon différée.
* **H-compression** — ``ContextWindowCompressionConfig(sliding_window=
  SlidingWindow())`` est envoyé avec ``target_tokens=None`` (vérifié dans le
  SDK installé). Google documente la compression comme un facteur AGGRAVANT
  de bugs serveur sur les modèles audio natifs (googleapis/python-genai#2117).
* **H-lifecycle** — le handle de reprise ou le cycle de vie de la session.
* **H-model/protocol** — le modèle demandé, la version de protocole négociée
  ou l'authentification — famille qu'on ne peut PAS écarter en même temps que
  les trois précédentes.

Méthode
-------
Cinq sondes, chacune volontairement minimale (un tour, éventuellement une
reprise, un tour) :

* **A B C D** — plan factoriel complet 2×2 sur (history, compression), une
  seule variable changeant par ligne. Si le 1008 disparaît dans exactement une
  case, le paramètre est isolé.
* **E** — configuration de référence mais **session neuve sans aucun handle de
  reprise**. Elle répond à une question que A-D ne peuvent pas trancher :
  le 1008 apparaît-il même quand aucun handle n'est jamais réutilisé ? Si oui,
  le cycle de vie de la reprise n'est PAS la cause, et il faut regarder le
  modèle, le protocole et le code de fermeture — exactement ce que ce script
  refuse de conclure à votre place.

Le script enregistre pour chaque sonde : le modèle réellement demandé, la
configuration effective, si un handle a été reçu, où la défaillance survient
(connexion initiale / reprise / tour), le **code de fermeture** et son motif.
Il ne conclut « lifecycle » que si E est aussi en 1008 **et** que les codes de
fermeture le confirment ; sinon il énonce ce qu'il reste à examiner.

Ce plan est VOLONTAIREMENT minimal : 5 exécutions courtes, pas de rafale.

Garanties
---------
* Aucune clé n'est affichée, écrite ni journalisée (masquage ``AIza…``/``AQ.…``).
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
#: Un code de fermeture WebSocket est toujours un entier à 4 chiffres.
_CLOSE_RE = re.compile(r"\b(1\d{3})\b")

MODEL = os.getenv("JARVIS_DIAG_MODEL", "gemini-2.5-flash-native-audio-preview-12-2025")

#: Durée maximale d'attente d'un tour (s). Court : un échec doit être rapide.
TURN_TIMEOUT = 25.0

#: Sondes. ``resume=False`` (sonde E) ne réutilise jamais de handle.
PROBES: tuple[dict, ...] = (
    {"id": "A", "label": "référence (prod actuelle)", "history": True,
     "compression": True, "resume": True},
    {"id": "B", "label": "sans historyConfig", "history": False,
     "compression": True, "resume": True},
    {"id": "C", "label": "sans compression", "history": True,
     "compression": False, "resume": True},
    {"id": "D", "label": "sans les deux", "history": False,
     "compression": False, "resume": True},
    {"id": "E", "label": "session neuve, SANS reprise", "history": True,
     "compression": True, "resume": False},
)

#: Les quatre cases du plan factoriel (E en est exclue par construction).
FACTORIAL_IDS = ("A", "B", "C", "D")


def redact(text: str) -> str:
    return _KEY_RE.sub("<clé masquée>", str(text))


def find_api_key() -> str | None:
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None


def close_details(error: BaseException | None) -> tuple[int | None, str]:
    """Code et motif de fermeture RÉELS, extraits sans jamais lever.

    Le 1008 est un code de fermeture WebSocket : il faut le lire tel quel
    plutôt que de le deviner par recherche de sous-chaîne dans un message.
    """
    if error is None:
        return None, ""
    raw_code = getattr(error, "code", None)
    code: int | None = None
    if isinstance(raw_code, int):
        code = raw_code
    elif isinstance(raw_code, str) and raw_code.isdigit():
        code = int(raw_code)
    text = redact(str(error))
    if code is None:
        found = _CLOSE_RE.search(text)
        if found:
            code = int(found.group(1))
    reason = text[:200]
    return code, reason


def describe(error: BaseException | None) -> str:
    if error is None:
        return "-"
    code, reason = close_details(error)
    head = f"{type(error).__name__}(code={code})" if code is not None else type(error).__name__
    return f"{head}: {reason}"


def classify(error: BaseException | None, where: str,
             handle_seen: bool) -> tuple[str, str, int | None]:
    """Traduit l'issue d'une sonde en (code lisible, détail, close_code).

    Le code de fermeture est renvoyé séparément : c'est une donnée à conserver
    telle quelle pour le rapport, pas une conclusion.
    """
    if error is None:
        return "OK", f"réussi ({where})", None
    close_code, _reason = close_details(error)
    text = f"{error} {close_code or ''}".lower()
    if close_code == 1008 or "1008" in text or "entity was not found" in text:
        return "1008", f"1008 pendant {where} (handle_reçu={handle_seen})", close_code
    if "1007" in text or "invalid session handle" in text:
        return "1007", f"handle refusé pendant {where}", close_code
    if any(m in text for m in ("api key", "401", "403", "permission")):
        return "AUTH", f"authentification refusée pendant {where}", close_code
    if any(m in text for m in ("429", "quota", "resource_exhausted")):
        return "QUOTA", f"quota/limite pendant {where}", close_code
    return "AUTRE", f"{describe(error)} pendant {where}", close_code


def build_config(probe: dict, handle: str | None = None):
    """Configuration Live effective d'une sonde (fonction pure, testable)."""
    from google.genai import types

    extras: dict = {}
    if probe["history"]:
        extras["history_config"] = types.HistoryConfig(initial_history_in_client_content=True)
    if probe["compression"]:
        extras["context_window_compression"] = types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow()
        )
    # La sonde E ne déclare AUCUNE reprise : aucun handle ne peut être réutilisé.
    if probe["resume"]:
        extras["session_resumption"] = types.SessionResumptionConfig(handle=handle)
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction="Tu es un assistant de diagnostic. Réponds en une phrase courte.",
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        **extras,
    )


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


async def probe(client, model: str, spec: dict) -> dict:
    """Rejoue le scénario minimal avec la configuration de la sonde."""
    result: dict = {
        "id": spec["id"],
        "history": spec["history"],
        "compression": spec["compression"],
        "resume": spec["resume"],
        "model": model,
        "tour1": "-",
        "handle": False,
        "reprise": "-",
        "tour2": "-",
        "where": "connexion initiale",
        "code": "AUTRE",
        "close_code": None,
        "detail": "",
    }

    handle: str | None = None
    error: BaseException | None = None
    where = "connexion initiale"

    try:
        config = build_config(spec)
        async with client.aio.live.connect(model=model, config=config) as session:
            where = "tour 1"
            await session.send_client_content(
                turns=[{"role": "user", "parts": [{"text": "Mon prénom est Simon."}]}],
                turn_complete=True,
            )
            done, handle = await _wait_turn(session, TURN_TIMEOUT)
            result["tour1"] = "ok" if done else "timeout"
            result["handle"] = bool(handle)

        if spec["resume"]:
            where = "reprise de session"
            resume_config = build_config(spec, handle=handle)
            async with client.aio.live.connect(model=model, config=resume_config) as session:
                where = "tour 2"
                await session.send_client_content(
                    turns=[{"role": "user", "parts": [{"text": "Quel est mon prénom ?"}]}],
                    turn_complete=True,
                )
                done2, _ = await _wait_turn(session, TURN_TIMEOUT)
                result["tour2"] = "ok" if done2 else "timeout"
            result["reprise"] = "ok"
        else:
            # Sonde E : une SECONDE session neuve, aucun handle transmis.
            where = "session neuve 2 (sans reprise)"
            fresh = build_config(spec)
            async with client.aio.live.connect(model=model, config=fresh) as session:
                where = "tour 2 (session neuve)"
                await session.send_client_content(
                    turns=[{"role": "user", "parts": [{"text": "Quel est mon prénom ?"}]}],
                    turn_complete=True,
                )
                done2, _ = await _wait_turn(session, TURN_TIMEOUT)
                result["tour2"] = "ok" if done2 else "timeout"
            result["reprise"] = "sans objet (aucun handle)"
    except BaseException as exc:  # noqa: BLE001 - toute erreur est une donnée ici
        error = exc

    result["where"] = where
    code, detail, close_code = classify(error, where, bool(handle))
    result["code"] = code
    result["detail"] = detail
    result["close_code"] = close_code
    return result


def print_plan(model: str | None = None) -> None:
    """Affiche le plan. ``model`` reflète TOUJOURS ce qui serait réellement sondé."""
    print("Matrice discriminante 1008 — plan (aucun appel réseau)\n")
    print(f"modèle : {model or MODEL}")
    print(f"sondes : {len(PROBES)} (A-D = plan factoriel 2×2 ; E = sans reprise)\n")
    header = (f"{'sonde':<7} {'hypothèse testée':<28} {'history':<8} "
              f"{'compress':<9} {'reprise':<8}")
    print(header)
    print("-" * len(header))
    for spec in PROBES:
        print(f"{spec['id']:<7} {spec['label']:<28} {str(spec['history']):<8} "
              f"{str(spec['compression']):<9} {str(spec['resume']):<8}")
    print("\nLecture des résultats (A-D) :")
    print("  - 1008 uniquement en A        -> H-history ou H-compression")
    print("  - 1008 en A et C, jamais B/D  -> H-history isolée")
    print("  - 1008 en A et B, jamais C/D  -> H-compression isolée")
    print("  - 1008 dans les 4 cases       -> configuration NON discriminante :")
    print("                                   lire la sonde E avant toute conclusion")
    print("\nLecture de la sonde E (aucun handle réutilisé) :")
    print("  - E en 1008                   -> la reprise n'est pas la cause :")
    print("                                   regarder modèle, protocole, close code")
    print("  - E saine, A-D en 1008        -> le cycle de vie de la reprise est")
    print("                                   impliqué (à confirmer par close_code)")
    print("  - E saine, A seul en 1008     -> paramètre de configuration isolé")
    print("\nEn cas d'échec des 5 sondes, ne pas conclure : examiner le close code,")
    print("le modèle réellement demandé et les traces de session.")


def _verdict_factorial(rows: list[dict]) -> str:
    """Texte d'analyse du plan factoriel — jamais une conclusion hâtive."""
    by_id = {row["id"]: row for row in rows}
    failed = [pid for pid in FACTORIAL_IDS if by_id[pid]["code"] == "1008"]
    if not failed:
        return "aucun 1008 dans les quatre cases"
    if len(failed) == 4:
        return ("1008 dans les QUATRE cases : la configuration n'est pas "
                "discriminante (ni history ni compression ne l'expliquent à elles seules)")
    return f"1008 seulement en {', '.join(failed)} : paramètre discriminant isolé"


def _verdict_lifecycle(rows: list[dict]) -> str:
    """Décide — ou refuse de décider — sur la base de la sonde E."""
    by_id = {row["id"]: row for row in rows}
    e_row = by_id.get("E")
    factorial_1008 = [pid for pid in FACTORIAL_IDS if by_id[pid]["code"] == "1008"]
    if e_row is None:
        return "sonde E absente : cycle de vie non testé"
    if e_row["code"] == "1008":
        return ("sonde E (AUCUN handle réutilisé) également en 1008 -> la reprise "
                "n'est PAS la cause. Examiner modèle demandé, protocole et close code.")
    if len(factorial_1008) == len(FACTORIAL_IDS):
        return ("sonde E saine alors que A-D échouent -> le cycle de vie de la "
                "reprise est IMPLIQUÉ ; à confirmer avec les close_code des cas A-D")
    return "sonde E saine : cohérent avec un paramètre de configuration isolé"


def print_matrix(rows: list[dict], model: str) -> None:
    print("\n" + "=" * 92)
    print("MATRICE 1008 — résultats réels")
    print(f"modèle demandé : {model}")
    print("=" * 92)
    header = (f"{'sonde':<7} {'hist':<6} {'comp':<6} {'reprise':<9} {'tour1':<8} "
              f"{'handle':<8} {'tour2':<8} {'issue':<8} {'close':<7}")
    print(header)
    print("-" * len(header))
    for row in rows:
        print(f"{row['id']:<7} {str(row['history']):<6} {str(row['compression']):<6} "
              f"{str(row['resume']):<9} {row['tour1']:<8} {str(row['handle']):<8} "
              f"{row['tour2']:<8} {row['code']:<8} {str(row['close_code'] or '-'):<7}")
    print("-" * len(header))
    for row in rows:
        print(f"{row['id']} [{row['where']}] {row['detail']}")

    codes = {row["code"] for row in rows}
    print("\n" + "-" * 92)
    if "QUOTA" in codes or "AUTH" in codes:
        print("VERDICT : NON TESTABLE — authentification ou quota en défaut.")
        print("          Aucune conclusion sur le 1008 n'est possible.")
        print("=" * 92)
        return

    print(f"Plan factoriel A-D : {_verdict_factorial(rows)}")
    print(f"Cycle de vie (E)   : {_verdict_lifecycle(rows)}")
    print()
    if "1008" not in codes:
        print("VERDICT : 1008 NON REPRODUIT sur ce modèle à cet instant.")
        print("          Ce n'est pas une preuve d'innocuité : relancer sous charge")
        print("          (S1->S6 complet) avant de conclure.")
    elif codes == {"1008"}:
        print("VERDICT : 1008 dans LES CINQ sondes. Ce n'est PAS une preuve que le")
        print("          cycle de vie est fautif — la reprise est écartée (E).")
        print("          À examiner dans cet ordre :")
        print("            1. le modèle réellement demandé (ci-dessus) et sa version")
        print("               de protocole — essayer --model gemini-3.8-live ;")
        print("            2. le close code exact de chaque sonde (colonne 'close') ;")
        print("            3. les traces de session et l'authentification du projet.")
    else:
        print("VERDICT : issues hétérogènes — lire le détail par sonde ci-dessus")
        print("          avant toute modification du code.")
    print("=" * 92)


async def run_matrix(api_key: str, model: str) -> int:
    from google import genai

    client = genai.Client(api_key=api_key)
    rows: list[dict] = []
    for spec in PROBES:
        print(f"\n[sonde {spec['id']}] {spec['label']} "
              f"(history={spec['history']}, compression={spec['compression']}, "
              f"reprise={spec['resume']})")
        row = await probe(client, model, spec)
        print(f"    -> {row['code']} : {row['detail']}")
        rows.append(row)
    print_matrix(rows, model)
    return 0 if all(row["code"] == "OK" for row in rows) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Matrice discriminante 1008")
    parser.add_argument("--dry-run", action="store_true",
                        help="affiche le plan sans aucun appel réseau")
    parser.add_argument("--model", default=MODEL,
                        help="modèle Live à sonder (permet de tester gemini-3.8-live)")
    args = parser.parse_args(argv)

    if args.dry_run:
        print_plan(args.model)
        return 0

    api_key = find_api_key()
    if not api_key:
        print_plan(args.model)
        print("\nAUCUNE CLÉ TROUVÉE (GEMINI_API_KEY / GOOGLE_API_KEY).")
        print("Matrice 1008 : NON TESTABLE dans cet environnement.")
        return 2

    return asyncio.run(run_matrix(api_key, args.model))


if __name__ == "__main__":
    sys.exit(main())
