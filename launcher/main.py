"""Launcher de Jarvis (``JarvisLauncher.exe``).

Rôle :

1. déterminer le dossier d'installation (à côté de l'exécutable) ;
2. valider l'installation (Jarvis.exe + _internal/python311.dll) ;
3. lire la version locale ;
4. interroger GitHub pour la dernière release ;
5. si une version plus récente existe : demander confirmation, télécharger,
   vérifier le SHA-256, valider la structure, installer atomiquement ;
6. lancer ``Jarvis.exe`` (mode orbe par défaut).

Deux interfaces (depuis la 1.1.1) :

* **graphique** (PySide6, défaut sans arguments) : fenêtre du launcher avec
  version, état, vérification et installation des mises à jour, lancement ;
* **console** (toute option d'action, ou ``--no-gui``) : comportement
  historique pour scripts, diagnostic et CI.

Usage :

* ``JarvisLauncher.exe``                → interface graphique.
* ``JarvisLauncher.exe --gui``         → interface graphique (forcée).
* ``JarvisLauncher.exe --no-gui``      → vérifie les mises à jour puis lance
  Jarvis en console (comportement historique).
* ``JarvisLauncher.exe --check``       → vérifie sans télécharger ni lancer.
* ``JarvisLauncher.exe --no-update``   → ne vérifie pas, lance directement.
* ``JarvisLauncher.exe --force``       → force la réinstallation de la même version.
* ``JarvisLauncher.exe --console``     → lance Jarvis en mode console (headless).
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

# Les modules src/launcher sont réutilisés (stdlib uniquement pour le launcher).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from launcher import core  # noqa: E402


# ---------------------------------------------------------------------------
# Console / flux standard (exécutable "windowed")
# ---------------------------------------------------------------------------

def _setup_stdio(cli_mode: bool) -> None:
    """Prépare stdout/stderr pour un exécutable compilé sans console.

    Avec ``console=False``, PyInstaller démarre sans flux standard
    (``sys.stdout is None``) : tout ``print()`` lèverait une exception.
    En mode console, on rattache le terminal parent (ou on alloue une
    console) ; en mode graphique, on neutralise les flux (le journal de
    l'interface affiche ses propres messages).
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    if cli_mode and os.name == "nt":
        if _attach_windows_console():
            return
    try:
        devnull = open(os.devnull, "w", encoding="utf-8")
    except OSError:
        return
    if sys.stdout is None:
        sys.stdout = devnull  # type: ignore[assignment]
    if sys.stderr is None:
        sys.stderr = devnull  # type: ignore[assignment]


def _attach_windows_console() -> bool:
    """Rattache la console parente (ou en alloue une) sous Windows."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        attached = kernel32.AttachConsole(0xFFFFFFFF)  # ATTACH_PARENT_PROCESS
        if not attached:
            attached = kernel32.AllocConsole()
        if not attached:
            return False
        # Rebranche les flux standard sur la console nouvellement attachée.
        try:
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")  # type: ignore[assignment]
        except OSError:
            pass
        try:
            sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace")  # type: ignore[assignment]
        except OSError:
            pass
        try:
            sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")  # type: ignore[assignment]
        except OSError:
            pass
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Mode console (comportement historique)
# ---------------------------------------------------------------------------

def _launch(args: argparse.Namespace) -> int:
    if getattr(args, "console", False):
        mode, wait = "console", True
    elif getattr(args, "desktop", False):
        mode, wait = "desktop", False
    else:
        mode, wait = "ui", False
    validation = core.validate_installation()
    if core.is_frozen() and not validation.ok:
        print(f"[Launcher] ERREUR : {validation.message}", file=sys.stderr)
        # Affiche aussi via input pour que l'utilisateur voie le message
        # même si la console se ferme rapidement.
        try:
            print(f"\n[Launcher] {validation.message}")
            print("\n[Launcher] Appuyez sur Entrée pour quitter...")
            input()
        except Exception:
            pass
        return 1
    if core.is_frozen():
        print(f"[Launcher] {validation.message}")
    if core.is_frozen():
        print(f"[Launcher] Lancement de Jarvis (mode {mode})...")
    else:
        print(f"[Launcher] Lancement de Jarvis en développement (mode {mode})...")
    result = core.launch_jarvis(mode, wait=wait)
    if result.ok:
        print(f"[Launcher] {result.message}")
        return result.returncode
    print(f"[Launcher] ERREUR : {result.message}", file=sys.stderr)
    try:
        traceback.print_exc()
    except Exception:
        pass
    return 1


def _maybe_update(args: argparse.Namespace) -> None:
    """Vérifie les mises à jour, demande confirmation et installe le cas échéant."""
    status = core.get_status()
    print(f"[Launcher] Version installée : {status.local_version}")
    print(f"[Launcher] Dossier installation : {status.install_path}")
    print(f"[Launcher] Dossier app : {status.app_path}")

    if core.is_frozen() and not status.install_ok:
        print("[Launcher] ATTENTION : installation actuelle invalide, mise à jour recommandée")
        print(f"[Launcher] {status.install_message}")

    if getattr(args, "no_update", False):
        print("[Launcher] Vérification des mises à jour désactivée (--no-update).")
        return

    check = core.check_update(
        prerelease=getattr(args, "prerelease", False),
        force=getattr(args, "force", False),
    )
    if not check.ok:
        print(f"[Launcher] {check.message}")
        print("[Launcher] On lance la version locale.")
        return

    assert check.plan is not None
    if not check.update_available:
        print(f"[Launcher] {check.message}")
        return

    if getattr(args, "check", False):
        print(f"[Launcher] Mise à jour disponible : {status.local_version} -> {check.latest}")
        return

    print(f"[Launcher] Nouvelle version disponible : {status.local_version} -> {check.latest}")
    if not _confirm(f"Mettre à jour Jarvis vers {check.latest} ? [o/N] : "):
        print("[Launcher] Mise à jour annulée.")
        return

    print("[Launcher] Téléchargement de la mise à jour...")
    result = core.install_update(check.plan)
    if result.ok:
        print(f"[Launcher] {result.message}")
        print(f"[Launcher] Nouvelle installation validée : OK (version {check.latest})")
    else:
        print(f"[Launcher] {result.message}")
        print("[Launcher] L'ancienne version reste utilisable.")


def _confirm(prompt: str) -> bool:
    try:
        value = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return value in {"o", "oui", "y", "yes", "1"}


def _run_cli(args: argparse.Namespace) -> int:
    # Mode validation seule
    if getattr(args, "validate", False):
        validation = core.validate_installation()
        print(validation.message)
        return 0 if validation.ok else 1

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


# ---------------------------------------------------------------------------
# Sélection de l'interface
# ---------------------------------------------------------------------------

#: Options qui forcent le mode console (actions scriptables / diagnostic).
_CLI_ACTION_OPTIONS = (
    "check",
    "no_update",
    "update_only",
    "force",
    "prerelease",
    "console",
    "desktop",
    "validate",
    "no_gui",
)


def _wants_gui(args: argparse.Namespace, argv: list[str] | None) -> bool:
    if getattr(args, "gui", False):
        return True
    if getattr(args, "no_gui", False):
        return False
    # Sans aucun argument : interface graphique. Toute option d'action
    # conserve le comportement console historique (compatibilité scripts/CI).
    if argv is not None and len(argv) == 0:
        return True
    return not any(getattr(args, name, False) for name in _CLI_ACTION_OPTIONS)


def _run_gui(*, auto_check: bool) -> int:
    try:
        from launcher import gui
    except ImportError as exc:
        print(f"[Launcher] Interface graphique indisponible ({exc}).", file=sys.stderr)
        print("[Launcher] Bascule en mode console.", file=sys.stderr)
        return -1
    try:
        return int(gui.run_gui(auto_check=auto_check))
    except Exception as exc:
        print(f"[Launcher] Échec de l'interface graphique : {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="JarvisLauncher", description="Launcher de Jarvis")
    parser.add_argument("--gui", action="store_true", help="Ouvre l'interface graphique (défaut sans arguments).")
    parser.add_argument("--no-gui", action="store_true", help="Force le mode console historique.")
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
    raw = list(argv) if argv is not None else sys.argv[1:]
    gui_mode = _wants_gui(args, raw)
    _setup_stdio(cli_mode=not gui_mode)

    if gui_mode:
        # En GUI, --no-update désactive la vérification automatique.
        rc = _run_gui(auto_check=not getattr(args, "no_update", False))
        if rc == -1:
            # PySide6 absent : repli console (lance directement Jarvis).
            return _launch(args)
        return rc
    return _run_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
