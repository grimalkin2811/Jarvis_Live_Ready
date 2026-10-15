"""Liste blanche d'outils pour les scénarios Gemini RÉELS du protocole.

Pendant une validation réelle, le modèle voit les déclarations d'outils de
production (indispensable pour juger, par exemple, qu'il n'ouvre pas le
Dashboard à tort), mais SEULS les outils sans effet sur le PC s'exécutent :
ouverture/fermeture du Dashboard, lecture d'état, heure/date, calcul, et la
mémoire — elle-même isolée dans un dossier temporaire. Tout autre outil
(applications, fichiers, système, messages, musique, alimentation…) est
remplacé par une réponse « désactivé pendant la validation » et son appel est
consigné. Aucun message n'est envoyé à un tiers.

Également utilisable comme lanceur des scripts réels officiels :

    python outils_surs.py --script scripts/validate_real_gemini.py -- --mode auto
"""

from __future__ import annotations

import argparse
import json
import runpy
import sys
from pathlib import Path

OUTILS_AUTORISES = frozenset({
    "open_dashboard", "close_dashboard", "get_ui_state", "get_conversation_state",
    "get_datetime", "get_local_time", "get_local_date", "calculate", "days_until",
    # Mémoire : base ISOLÉE (JARVIS_DATA_DIR temporaire, garde des données active).
    "recall", "remember", "list_memories", "search_memories",
    # Outil de validation des scripts officiels (déterministe, bénin).
    "validation_ping",
})

APPELS_NEUTRALISES: list[str] = []


def installer() -> list[str]:
    """Remplace en place chaque outil non autorisé ; renvoie la liste neutralisée."""
    from src import tools

    neutralises = []
    for name in list(tools.TOOL_FUNCTIONS):
        if name in OUTILS_AUTORISES:
            continue

        def stub(*_args, _name=name, **_kwargs):
            APPELS_NEUTRALISES.append(_name)
            return {
                "success": False,
                "error": (f"Outil « {_name} » désactivé pendant la validation automatique : "
                          "aucune action n'a été exécutée sur le PC."),
            }

        tools.TOOL_FUNCTIONS[name] = stub
        neutralises.append(name)
    return neutralises


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extra: list[str] = []
    if "--" in argv:
        index = argv.index("--")
        argv, extra = argv[:index], argv[index + 1:]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--script", required=True)
    args = parser.parse_args(argv)
    script = Path(args.script).resolve()
    sys.path.insert(0, str(script.parent.parent))
    neutralises = installer()
    print(f"[outils sûrs] {len(neutralises)} outils neutralisés, autorisés : {sorted(OUTILS_AUTORISES)}", flush=True)
    sys.argv = [str(script)] + extra
    code = 0
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        print("OUTILS NEUTRALISÉS APPELÉS : " + json.dumps(sorted(set(APPELS_NEUTRALISES)), ensure_ascii=False),
              flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
