"""Chargé automatiquement par Python (dossier placé en tête de ``PYTHONPATH``).

N'agit QUE si ``JARVIS_VALIDATION_GARDE=1`` : la garde des données réelles du
protocole de validation (``garde_donnees.py``) est alors installée avant tout
autre code du processus. Sans cette variable, ce fichier ne fait rien.
"""

import os

if os.environ.get("JARVIS_VALIDATION_GARDE") == "1":
    try:
        import garde_donnees

        if garde_donnees.install() and os.environ.get("JARVIS_VALIDATION_ACTIVATIONS"):
            import json
            import sys
            import time

            # Preuve (pour le rapport) que la garde est chargée dans CE processus.
            with garde_donnees._ORIGINALS["open"](os.environ["JARVIS_VALIDATION_ACTIVATIONS"], "a",
                                                  encoding="utf-8") as handle:
                handle.write(json.dumps({"pid": os.getpid(), "python": sys.version.split()[0],
                                         "horodatage": time.strftime("%H:%M:%S")}) + "\n")
    except Exception as exc:  # pragma: no cover - ne jamais empêcher Python de démarrer
        import sys

        sys.stderr.write(f"[garde de validation] installation impossible : {exc}\n")
