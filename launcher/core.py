"""Logique métier du launcher Jarvis, sans aucune dépendance graphique.

Ce module est partagé par l'interface console (``launcher/main.py``) et
l'interface graphique (``launcher/gui.py``) afin d'éviter toute duplication :

* résolution des dossiers (installation / application) ;
* validation de l'installation (structure PyInstaller) ;
* lecture de la version locale ;
* vérification et installation des mises à jour (via ``src.updater``) ;
* lancement de Jarvis.

Bibliothèque standard uniquement (+ ``src.updater`` / ``src.version``), comme
le launcher historique : ce module doit rester importable dans le bundle
``JarvisLauncher.exe`` le plus minimal possible.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# Les modules src sont réutilisés (stdlib uniquement pour le launcher).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import updater  # noqa: E402
from src.version import CHANNEL, get_version  # noqa: E402

# Validation centralisée (avec fallback si module manquant)
try:
    from src.packaging_validation import (
        validate_app_dir,
        validate_install_dir,  # noqa: F401 (ré-exporté pour les tests)
    )
except ImportError:  # pragma: no cover - sécurité distribution minimale
    def validate_app_dir(app_path, strict=False):  # type: ignore[no-redef]
        base = Path(app_path)
        missing = []
        forbidden = []
        for rel in ("Jarvis.exe", "_internal/python311.dll", "_internal/base_library.zip"):
            if not (base / rel).exists():
                missing.append(rel)
        for rel in ("python311.dll", "base_library.zip"):
            if (base / rel).exists():
                forbidden.append(rel)
        return (not missing and not forbidden), missing, forbidden

    def validate_install_dir(install_path):  # type: ignore[no-redef]
        base = Path(install_path)
        missing = []
        forbidden = []
        for rel in ("app/Jarvis.exe", "app/_internal/python311.dll", "app/_internal/base_library.zip"):
            if not (base / rel).exists():
                missing.append(rel)
        for rel in ("python311.dll", "base_library.zip"):
            if (base / "app" / rel).exists():
                forbidden.append(f"app/{rel}")
        return (not missing and not forbidden), missing, forbidden


# ---------------------------------------------------------------------------
# Dossiers
# ---------------------------------------------------------------------------

def is_frozen() -> bool:
    """Vrai quand le launcher tourne en bundle PyInstaller (``.exe``)."""
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path:
    """Dossier d'installation (celui qui contient le launcher)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def app_dir() -> Path:
    """Dossier remplaçable de l'application (``app/`` sous le launcher).

    En développement, l'application est la racine du dépôt (pas de ``app/``).
    """
    if is_frozen():
        return install_dir() / "app"
    return Path(__file__).resolve().parents[1]


def jarvis_executable() -> Path:
    """Chemin de l'exécutable Jarvis (frozen) ou chaîne vide en dev."""
    return app_dir() / "Jarvis.exe"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    message: str
    missing: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()


def validate_installation() -> ValidationResult:
    """Valide l'installation actuelle (fichiers critiques, aplatissement)."""
    if not is_frozen():
        return ValidationResult(True, "Mode développement")

    directory = app_dir()
    if not directory.is_dir():
        return ValidationResult(
            False,
            f"Dossier d'application introuvable : {directory}\n"
            "L'installation semble incomplète ou corrompue.\n"
            "Réinstallez Jarvis via JarvisSetup.exe",
        )

    is_valid, missing, forbidden = validate_app_dir(directory)
    if is_valid:
        exe = directory / "Jarvis.exe"
        if not exe.is_file():
            return ValidationResult(
                False,
                f"Exécutable introuvable : {exe}\nRéinstallez Jarvis via JarvisSetup.exe",
                missing=("Jarvis.exe",),
            )
        return ValidationResult(True, "Installation OK")

    lines = [f"Installation corrompue détectée dans {directory} :"]
    if missing:
        lines.append("Fichiers manquants :")
        lines.extend(f"  - {item}" for item in missing)
    if forbidden:
        lines.append("Fichiers aplatis détectés (bug de packaging) :")
        lines.extend(f"  - {item}" for item in forbidden)
    lines += [
        "",
        "L'application ne peut pas démarrer. Solutions :",
        "  1. Réinstallez Jarvis via JarvisSetup.exe",
        "  2. Ou forcez une réinstallation (mise à jour vers la même version)",
        "  3. Vérifiez que app/_internal/python311.dll existe",
    ]
    return ValidationResult(False, "\n".join(lines), tuple(missing), tuple(forbidden))


# ---------------------------------------------------------------------------
# Version / statut
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LauncherStatus:
    """État instantané affiché par le launcher (console ou graphique)."""

    local_version: str
    bundled_version: str = field(default_factory=get_version)
    channel: str = CHANNEL
    install_ok: bool = True
    install_message: str = "Installation OK"
    jarvis_running: bool = False
    install_path: str = ""
    app_path: str = ""


def local_version() -> str:
    """Version installée (``version.json`` ou version embarquée)."""
    return updater.local_version(install_dir())


def repo_slug() -> str:
    """Dépôt GitHub utilisé pour les mises à jour (surchargé par JARVIS_REPO)."""
    return os.getenv("JARVIS_REPO", updater.REPO_SLUG)


def get_status() -> LauncherStatus:
    """Construit l'état instantané du launcher."""
    validation = validate_installation()
    running = False
    try:
        running = updater.is_app_running(str(jarvis_executable()))
    except Exception:
        running = False
    return LauncherStatus(
        local_version=local_version(),
        install_ok=validation.ok,
        install_message=validation.message,
        jarvis_running=running,
        install_path=str(install_dir()),
        app_path=str(app_dir()),
    )


# ---------------------------------------------------------------------------
# Mise à jour
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UpdateCheck:
    ok: bool
    message: str
    plan: dict | None = None

    @property
    def update_available(self) -> bool:
        return bool(self.plan and self.plan.get("update_available"))

    @property
    def latest(self) -> str:
        if self.plan:
            return str(self.plan.get("latest", ""))
        return ""


def check_update(*, prerelease: bool = False, force: bool = False) -> UpdateCheck:
    """Interroge GitHub et construit le plan de mise à jour (sans télécharger).

    ``force=True`` prépare une réinstallation de la version courante même si
    aucune mise à jour n'est disponible (réparation d'installation corrompue).

    Ne lève jamais : les erreurs réseau/GitHub sont retournées dans le
    résultat pour un affichage clair (console ou QMessageBox).
    """
    current = local_version()
    repo = repo_slug()
    if not updater.is_connected():
        return UpdateCheck(False, "Pas de connexion Internet : lancement de la version locale.")
    try:
        plan = updater.build_update_plan(repo, current=current, prerelease=prerelease)
    except updater.UpdateError as exc:
        return UpdateCheck(False, f"Vérification impossible : {exc}")
    except Exception as exc:  # pragma: no cover - garde-fou réseau
        return UpdateCheck(False, f"Vérification impossible (erreur inattendue) : {exc}")
    latest = str(plan.get("latest", ""))
    if plan.get("update_available"):
        return UpdateCheck(True, f"Mise à jour disponible : {current} -> {latest}", plan=plan)
    if force:
        # Réinstallation forcée : recherche l'artefact de la version courante
        # dans la dernière release (qui est cette même version).
        release = plan.get("release") or {}
        asset = updater.find_asset(release, current)
        if asset is None:
            return UpdateCheck(
                False,
                f"Réinstallation impossible : aucun artefact {current} dans la release.",
            )
        plan["asset"] = asset
        plan["asset_sha256"] = updater.find_asset_sha256(release, current)
        plan["update_available"] = True
        return UpdateCheck(True, f"Réinstallation forcée de la version {current}.", plan=plan)
    return UpdateCheck(True, f"Vous avez déjà la dernière version ({latest}).", plan=plan)


@dataclass(frozen=True)
class UpdateResult:
    ok: bool
    message: str


def install_update(plan: dict, *, progress=None) -> UpdateResult:
    """Télécharge, vérifie et installe la mise à jour décrite par ``plan``.

    ``progress`` reçoit ``(octets_recus, total_ou_None)`` pendant le
    téléchargement (barre de progression du launcher graphique).
    Ne lève jamais : le résultat porte le diagnostic.
    """
    directory = app_dir()
    launcher_directory = install_dir()
    if updater.is_app_running(str(directory / "Jarvis.exe")):
        return UpdateResult(False, "Jarvis est actuellement ouvert : fermez-le puis relancez.")
    tmp = tempfile.mkdtemp(prefix="jarvis-update-")
    try:
        updater.perform_update(
            plan,
            directory,
            dest_dir=tmp,
            marker_dir=launcher_directory,
            progress=progress,
        )
    except updater.UpdateError as exc:
        return UpdateResult(False, f"Échec de la mise à jour : {exc}")
    except Exception as exc:  # pragma: no cover - garde-fou
        return UpdateResult(False, f"Échec inattendu de la mise à jour : {exc}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    latest = str((plan or {}).get("latest", local_version()))
    if is_frozen():
        validation = validate_installation()
        if not validation.ok:
            restored = updater.rollback(directory)
            suffix = "Rollback réussi vers l'ancienne version." if restored else (
                "Rollback impossible, réinstallation manuelle requise."
            )
            return UpdateResult(
                False,
                f"Nouvelle installation invalide après mise à jour :\n"
                f"{validation.message}\n{suffix}",
            )
    return UpdateResult(True, f"Mise à jour vers {latest} terminée.")


def rollback() -> UpdateResult:
    """Restaure la sauvegarde la plus récente (après un échec)."""
    try:
        restored = updater.rollback(app_dir())
    except Exception as exc:
        return UpdateResult(False, f"Rollback impossible : {exc}")
    if restored:
        return UpdateResult(True, f"Ancienne version restaurée ({restored}).")
    return UpdateResult(False, "Aucune sauvegarde disponible pour un rollback.")


# ---------------------------------------------------------------------------
# Lancement de Jarvis
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LaunchResult:
    ok: bool
    message: str
    returncode: int = 0


def launch_jarvis(mode: str = "ui", *, wait: bool = False) -> LaunchResult:
    """Lance Jarvis.

    Args:
        mode: ``"ui"`` (orbe, défaut), ``"desktop"`` (overlay) ou
            ``"console"`` (headless, sortie visible).
        wait: si Vrai, attend la fin du processus et retourne son code
            (mode console) ; sinon, Jarvis tourne en processus détaché et le
            launcher peut se fermer.
    """
    if mode not in ("ui", "desktop", "console"):
        return LaunchResult(False, f"Mode de lancement inconnu : {mode!r}")
    validation = validate_installation()
    if not validation.ok:
        return LaunchResult(False, validation.message)
    if is_frozen():
        exe = jarvis_executable()
        if not exe.is_file():
            return LaunchResult(False, f"Exécutable introuvable : {exe}\nRéinstallez Jarvis.")
        internal_dll = app_dir() / "_internal" / "python311.dll"
        if not internal_dll.is_file():
            return LaunchResult(
                False,
                f"DLL critique manquante : {internal_dll}\n"
                "Structure PyInstaller invalide (flattening détecté).\n"
                "Réinstallez Jarvis via JarvisSetup.exe",
            )
        cmd = [str(exe)]
        if mode == "desktop":
            cmd.append("--desktop")
        elif mode == "ui":
            cmd.append("--ui")
        # mode console : Jarvis.exe sans argument (headless).
    else:
        cmd = [sys.executable, "-m", "src.main"]
        if mode == "desktop":
            cmd.append("--desktop")
        elif mode == "ui":
            cmd.append("--ui")
    try:
        if wait:
            completed = subprocess.run(cmd)
            if completed.returncode == 0:
                return LaunchResult(True, "Jarvis s'est terminé.", returncode=0)
            return LaunchResult(
                False,
                f"Jarvis s'est terminé avec le code {completed.returncode}.",
                returncode=completed.returncode,
            )
        subprocess.Popen(cmd)
        return LaunchResult(True, "Jarvis lancé.")
    except FileNotFoundError:
        return LaunchResult(
            False,
            f"Démarrage impossible : {cmd[0]} introuvable.\n"
            "Réinstallez Jarvis ou lancez Jarvis.exe directement.",
        )
    except Exception as exc:
        return LaunchResult(False, f"Erreur au démarrage : {exc}")
