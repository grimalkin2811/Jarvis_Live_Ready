"""Launcher de Jarvis (``JarvisLauncher.exe``).

Rôle :

1. déterminer le dossier d'installation (à côté de l'exécutable) ;
2. valider l'installation (Jarvis.exe + _internal/python311.dll) ;
3. lire la version locale ;
4. interroger GitHub pour la dernière release ;
5. si une version plus récente existe : demander confirmation, télécharger,
   vérifier le SHA-256, valider la structure, installer atomiquement ;
6. lancer ``Jarvis.exe`` (mode orbe par défaut).

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

# Validation centralisée (avec fallback si module manquant)
try:
    from src.packaging_validation import (
        CRITICAL_APP_FILES,
        FORBIDDEN_FLATTENED_FILES,
        validate_app_dir,
        validate_install_dir,
    )
except ImportError:
    CRITICAL_APP_FILES = [
        "Jarvis.exe",
        "_internal/python311.dll",
        "_internal/base_library.zip",
    ]
    FORBIDDEN_FLATTENED_FILES = ["python311.dll", "base_library.zip"]

    def validate_app_dir(app_path, strict=False):
        base = Path(app_path)
        missing = []
        forbidden = []
        for rel in CRITICAL_APP_FILES:
            if not (base / rel).exists():
                missing.append(rel)
        for rel in FORBIDDEN_FLATTENED_FILES:
            if (base / rel).exists():
                forbidden.append(rel)
        is_valid = not missing and not forbidden
        return is_valid, missing, forbidden

    def validate_install_dir(install_path):
        base = Path(install_path)
        missing = []
        forbidden = []
        for rel in ["app/Jarvis.exe", "app/_internal/python311.dll", "app/_internal/base_library.zip"]:
            if not (base / rel).exists():
                missing.append(rel)
        for rel in FORBIDDEN_FLATTENED_FILES:
            if (base / "app" / rel).exists():
                forbidden.append(f"app/{rel}")
        return (not missing and not forbidden), missing, forbidden


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


def _validate_installation() -> tuple[bool, str]:
    """Valide l'installation actuelle et retourne (is_valid, message)."""
    if not getattr(sys, "frozen", False):
        # En dev, on ne valide pas
        return True, "Mode développement"

    app_dir = _app_dir()

    # Vérifie que le dossier app existe
    if not app_dir.is_dir():
        return False, (
            f"Dossier d'application introuvable: {app_dir}\n"
            f"  L'installation semble incomplète ou corrompue.\n"
            f"  Réinstallez Jarvis via JarvisSetup.exe"
        )

    # Validation via module centralisé
    is_valid, missing, forbidden = validate_app_dir(app_dir)

    if not is_valid:
        lines = [f"Installation corrompue détectée dans {app_dir}:"]
        if missing:
            lines.append("  Fichiers manquants:")
            for m in missing:
                lines.append(f"    - {m}")
        if forbidden:
            lines.append("  Fichiers aplatis détectés (bug de packaging):")
            for f in forbidden:
                lines.append(f"    - {f}")
                if f in ("python311.dll", "base_library.zip"):
                    lines.append(f"      -> {f} devrait être dans app/_internal/{f}")
                    lines.append(f"      -> Cause probable: archive ZIP aplatie ou extraction incorrecte")
        lines.append("")
        lines.append("  L'application ne peut pas démarrer.")
        lines.append("  Solutions:")
        lines.append("    1. Réinstallez Jarvis via JarvisSetup.exe")
        lines.append("    2. Ou lancez JarvisLauncher.exe --force pour forcer une réinstallation")
        lines.append(f"    3. Vérifiez que app/_internal/python311.dll existe")
        return False, "\n".join(lines)

    # Vérifie aussi Jarvis.exe
    exe = app_dir / "Jarvis.exe"
    if not exe.is_file():
        return False, (
            f"Exécutable introuvable: {exe}\n"
            f"  Réinstallez Jarvis via JarvisSetup.exe"
        )

    return True, "Installation OK"


def _launch(args: argparse.Namespace) -> int:
    # Validation préalable en mode frozen
    if getattr(sys, "frozen", False):
        is_valid, message = _validate_installation()
        if not is_valid:
            print(f"[Launcher] ERREUR: {message}", file=sys.stderr)
            # Affiche aussi via input pour que l'utilisateur voie le message
            # même si la console se ferme rapidement
            try:
                print(f"\n[Launcher] {message}")
                print("\n[Launcher] Appuyez sur Entrée pour quitter...")
                input()
            except Exception:
                pass
            return 1
        else:
            print(f"[Launcher] {message}")

    if getattr(sys, "frozen", False):
        exe = _app_dir() / "Jarvis.exe"
        # Vérification explicite avant lancement
        if not exe.is_file():
            print(f"[Launcher] ERREUR: Exécutable introuvable: {exe}")
            print(f"[Launcher] Réinstallez Jarvis.")
            return 1

        # Vérifie _internal/python311.dll avant de lancer
        internal_dll = _app_dir() / "_internal" / "python311.dll"
        if not internal_dll.is_file():
            print(f"[Launcher] ERREUR: DLL critique manquante: {internal_dll}")
            print(f"[Launcher] Structure PyInstaller invalide (flattening détecté).")
            print(f"[Launcher] Vérifiez que app/_internal/ existe et contient python311.dll")
            print(f"[Launcher] Si python311.dll est à la racine de app/, l'installation est corrompue.")
            print(f"[Launcher] Réinstallez Jarvis via JarvisSetup.exe")
            return 1

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
    # Jarvis tourne dans un processus séparé ; le launcher ne doit pas
    # s'attacher à lui (sinon il resterait ouvert tant que Jarvis est ouvert).
    try:
        if getattr(args, "console", False):
            result = subprocess.call(cmd)
            return result
        else:
            subprocess.Popen(cmd)
        return 0
    except FileNotFoundError:
        print(f"[Launcher] Démarrage impossible : {cmd[0]} introuvable.")
        print(f"[Launcher] Réinstallez Jarvis ou lancez Jarvis.exe directement.")
        return 1
    except Exception as exc:
        print(f"[Launcher] Erreur au démarrage : {exc}")
        traceback.print_exc()
        return 1


def _maybe_update(args: argparse.Namespace) -> None:
    """Vérifie les mises à jour, demande confirmation et installe le cas échéant."""
    launcher_dir = _install_dir()
    app_dir = _app_dir()
    current = updater.local_version(launcher_dir)
    repo = os.getenv("JARVIS_REPO", updater.REPO_SLUG)

    print(f"[Launcher] Version installée : {current}")
    print(f"[Launcher] Dossier installation : {launcher_dir}")
    print(f"[Launcher] Dossier app : {app_dir}")

    # Valide l'installation actuelle avant de vérifier les updates
    if getattr(sys, "frozen", False):
        is_valid, msg = _validate_installation()
        if not is_valid:
            print(f"[Launcher] ATTENTION: Installation actuelle invalide, mise à jour recommandée")
            print(f"[Launcher] {msg}")

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
        # Valide la nouvelle installation
        if getattr(sys, "frozen", False):
            is_valid, msg = _validate_installation()
            if not is_valid:
                print(f"[Launcher] ERREUR: Nouvelle installation invalide après update!")
                print(f"[Launcher] {msg}")
                print(f"[Launcher] Tentative de rollback...")
                restored = updater.rollback(app_dir)
                if restored:
                    print(f"[Launcher] Rollback réussi vers ancienne version")
                else:
                    print(f"[Launcher] Rollback impossible, réinstallation manuelle requise")
            else:
                print(f"[Launcher] Nouvelle installation validée: OK")
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
    parser.add_argument("--validate", action="store_true", help="Valide l'installation et quitte.")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    # Mode validation seule
    if getattr(args, "validate", False):
        is_valid, msg = _validate_installation()
        print(msg)
        return 0 if is_valid else 1

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
