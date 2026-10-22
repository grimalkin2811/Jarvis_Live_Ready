"""Smoke Windows : renommages POSIX sous tas souillÃ© (cause racine historique).

Le bug du terminateur nul se dÃ©clenchait seulement quand les octets APRÃˆS le
tampon contenaient des dÃ©chets de tas (dÃ©pend de l'historique d'allocation du
processus). Ce smoke sale le tas diffÃ©remment Ã  chaque exÃ©cution puis effectue
des centaines de remplacements atomiques concurrents Ã  une lectrice partagÃ©e :
le nom de destination doit toujours Ãªtre exact, jamais de fichier corrompu.
"""
import os
import random
import string
import sys
import tempfile
import threading
import time

# Racine du depot : scripts/windows_json_state_probes/ -> scripts/ -> racine.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src import json_state  # noqa: E402


def dirty_heap(rng: random.Random) -> None:
    """Remplit le tas avec des chaÃ®nes de longueurs variÃ©es (dÃ©lits UTF-16)."""
    keep = []
    for _ in range(400):
        size = rng.randint(3, 90)
        keep.append("".join(rng.choice(string.printable) for _ in range(size)))
    rng.shuffle(keep)


def main() -> int:
    rng = random.Random(int(time.time() * 1000) % (2**32))
    dirty_heap(rng)
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "state.json")
        if not json_state.save_dict(path, {"seq": 0}):
            failures.append("seed save")

        stop = threading.Event()
        gaps: list[str] = []

        def reader():
            while not stop.is_set():
                payload = json_state.load_dict(path)
                if "seq" not in payload:
                    gaps.append("trou/payload invalide")
                time.sleep(0.0005)

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()

        # Ã‰critures en vol avec tas souillÃ© entre chaque appel.
        for i in range(120):
            dirty_heap(rng)
            if not json_state.save_dict(path, {"seq": i + 1}):
                failures.append(f"save #{i + 1}")

        # Lectrice partagÃ©e maintenue ouverte pendant une rÃ©Ã©criture.
        with json_state.open_text(path) as held:
            held.read()
            if not json_state.save_dict(path, {"seq": 999}):
                failures.append("save with held reader")

        stop.set()
        thread.join(timeout=5)

        # Aucun fichier parasite dans le dossier (nom de destination corrompu,
        # temporaire rÃ©siduel...).
        entries = sorted(os.listdir(tmp))
        if entries != ["state.json"]:
            failures.append(f"fichiers parasites: {entries!r}")

        final = json_state.load_dict(path)
        if final.get("seq") != 999:
            failures.append(f"contenu final: {final!r}")
        if gaps:
            failures.append(f"{len(gaps)} trous lecteur")

    if failures:
        print("SMOKE KO:", "; ".join(failures))
        return 1
    print("SMOKE OK: 120 remplacements sous tas souillÃ©, 0 trou, 0 parasite")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
