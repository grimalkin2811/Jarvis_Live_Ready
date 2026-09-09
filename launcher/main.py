"""Launcher de Jarvis (``JarvisLauncher.exe``).

Rôle :

1. déterminer le dossier d'installation (à côté de l'exécutable) ;
2. lire la version locale ;
3. interroger GitHub pour la dernière release ;
4. si une version plus récente existe : demander confirmation, télécharger,
   vérifier le SHA-256, installer atomiquement, puis préserver les données ;
5. lancer ``Jarvis.exe`` (mode orbe par défaut).

Usage :

* ``JarvisLauncher.exe``                → vérifie les mises à jour puis lance Jarvis.
* ``JarvisLauncher.exe --check``        → vérifie sans télécharger ni lancer.
* ``JarvisLauncher.exe --no-update``    → ne vérifie pas, lance directement.
* ``JarvisLauncher.exe --force``        → force la réinstallation de la même version.
* ``JarvisLauncher.exe --console``      → lance Jarvis en mode console (headless).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

# Les modules src sont réutilisés (stdlib uniquement pour le launcher).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import updater  # noqa: E402


def _install_dir() -> Path:
    """Dossier d'installation (celui qui contient le launcher)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _app_dir() -> Path:
    """Dossier remplaçable de l'application (``app/`` sous le launcher).

    En développement, l'application est la racine du dépôt (pas de ``app/``).
    """
    if getattr(sys, "frozen", False):
        return _install_dir() / "app"
    return Path(__file__).resolve().parents[1]


def _launch(args: argparse.Namespace) -> None:
    if getattr(sys, "frozen", False):
        exe = _app_dir() / "Jarvis.exe"
        cmd = [str(exe)]
        if getattr(args, "console", False):
            pass  # Jarvis.exe lance la console par défaut
        elif getattr(args, "desktop", False):
            cmd.append("--desktop")
        else:
            cmd.append("--ui")
    else:
        python = sys.executable
        cmd = [python, "-m", "src.main"]
        if getattr(args, "console", False):
            pass
        elif getattr(args, "desktop", False):
            cmd.append("--desktop")
        else:
            cmd.append("--ui")

    print(f"[Launcher] Lancement de Jarvis ({' '.join(cmd)})...")
    # subprocess avec création de nouveau groupe de processus : si Jarvis
    # échoue, le launcher ne meurt pas avec lui.
    try:
        if getattr(args, "console", False):
            return subprocess.call(cmd)
        return subprocess.Popen(cmd)
    except FileNotFoundError:
        print(f"[Launcher] Démarrage impossible : {cmd[0]} introuvable.")
        print("[Launcher] Réinstallez Jarvis ou lancez Jarvis.exe directement.")
        return 1
    except Exception as exc:
        print(f"[Launcher] Erreur au démarrage : {exc}")
        return 1


def _maybe_update(args: argparse.Namespace) -> None:
    """Vérifie les mises à jour, demande confirmation et installe le cas échéant."""
    launcher_dir = _install_dir()
    app_dir = _app_dir()
    current = updater.local_version(launcher_dir)
    repo = os.getenv("JARVIS_REPO", updater.REPO_SLUG)

    print(f"[Launcher] Version installée : {current}")

    if getattr(args, "no_update", False):
        print("[Launcher] Vérification des mises à jour désactivée (--no-update).")
        return

    if not updater.is_connected():
        print("[Launcher] Pas de connexion Internet : lancement de la version locale.")
        return

    try:
        plan = updater.build_update_plan(repo, current=current, prerelease=getattr(args, "prerelease", False))
    except updater.UpdateError as exc:
        print(f"[Launcher] Vérification impossible : {exc}")
        print("[Launcher] On lance la version locale.")
        return

    latest = plan["latest"]
    if not plan["update_available"] and not getattr(args, "force", False):
        print(f"[Launcher] Vous avez déjà la dernière version ({latest}).")
        return

    if getattr(args, "check", False):
        print(f"[Launcher] Mise à jour disponible : {current} -> {latest}")
        return

    print(f"[Launcher] Nouvelle version disponible : {current} -> {latest}")
    if updater.is_app_running(str(app_dir / "Jarvis.exe")):
        print("[Launcher] Jarvis est actuellement ouvert : fermez-le puis relancez.")
        return

    if not _confirm(f"Mettre à jour Jarvis vers {latest} ? [o/N] : "):
        print("[Launcher] Mise à jour annulée.")
        if not getattr(args, "update_only", False):
            _launch(args)
        return

    try:
        tmp = tempfile.mkdtemp(prefix="jarvis-update-")
        print("[Launcher] Téléchargement de la mise à jour...")
        updater.perform_update(
            plan,
            app_dir,
            dest_dir=tmp,
            marker_dir=launcher_dir,
        )
        print(f"[Launcher] Mise à jour vers {latest} terminée.")
        shutil.rmtree(tmp, ignore_errors=True)
    except updater.UpdateError as exc:
        print(f"[Launcher] Échec de la mise à jour : {exc}")
        print("[Launcher] L'ancienne version reste utilisable.")
        if not getattr(args, "update_only", False):
            _launch(args)
        return
    except Exception as exc:
        traceback.print_exc()
        print(f"[Launcher] Échec inattendu de la mise à jour : {exc}")
        if not getattr(args, "update_only", False):
            _launch(args)
        return


def _confirm(prompt: str) -> bool:
    try:
        value = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return value in {"o", "oui", "y", "yes", "1"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="JarvisLauncher", description="Launcher de Jarvis")
    parser.add_argument("--check", action="store_true", help="Vérifie les mises à jour sans rien télécharger.")
    parser.add_argument("--no-update", action="store_true", help="Ne vérifie pas les mises à jour.")
    parser.add_argument("--update-only", action="store_true", help="Met à jour puis quitte (sans lancer Jarvis).")
    parser.add_argument("--force", action="store_true", help="Force l'installation de la même version.")
    parser.add_argument("--prerelease", action="store_true", help="Considère aussi les pre-releases.")
    parser.add_argument("--console", action="store_true", help="Lance Jarvis en mode console.")
    parser.add_argument("--desktop", action="store_true", help="Lance Jarvis en mode overlay bureau.")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not getattr(args, "no_update", False):
            _maybe_update(args)
        if getattr(args, "update_only", False):
            return 0
        if getattr(args, "check", False):
            return 0
    except KeyboardInterrupt:
        print("\n[Launcher] Interrompu.")
        return 1
    return _launch(args)


if __name__ == "__main__":
    raise SystemExit(main())
