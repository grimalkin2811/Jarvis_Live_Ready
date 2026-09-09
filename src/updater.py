"""Système de mise à jour de Jarvis basé sur les GitHub Releases.

C'est le moteur utilisé par ``JarvisLauncher.exe``. Il détecte une nouvelle
version, télécharge l'archive portable publiée, vérifie son empreinte SHA-256,
puis remplace l'application de façon **atomique** en préservant les données
utilisateur.

Le module n'utilise que la bibliothèque standard (``urllib``) afin que le
launcher reste léger et sans dépendance tierce — important pour un fichier qui
doit continuer à fonctionner même quand l'application principale est
indisponible.

Conventions de nommage des artefacts (voir ``.github/workflows``) :

* ``Jarvis-v{version}-portable.zip``     — archive de l'application.
* ``Jarvis-v{version}-portable.zip.sha256`` — empreinte SHA-256 correspondante.
* ``JarvisSetup.exe``                   — installateur (Inno Setup).

Le launcher (ou le script de build) écrit un ``version.json`` à côté du
launcher : c'est la source de vérité locale pour « version installée ».
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from .version import REPO_SLUG, get_version, is_newer

#: Préfixe des artefacts d'application portable publiés dans une release.
ARTIFACT_PREFIX = "Jarvis-v"
ARTIFACT_SUFFIX = "-portable.zip"

_GITHUB_API = "https://api.github.com/repos/"


class UpdateError(RuntimeError):
    """Erreur récupérable du processus de mise à jour."""


# ---------------------------------------------------------------------------
# Version locale
# ---------------------------------------------------------------------------

def local_version(launcher_dir: str | os.PathLike | None = None) -> str:
    """Version localement installée, lue depuis ``version.json``.

    Si aucun fichier n'existe, on retombe sur la version embarquée dans
    ``src.version`` (cas d'un build frais).
    """
    base = Path(launcher_dir) if launcher_dir else Path.cwd()
    marker = base / "version.json"
    if marker.is_file():
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
            version = str(payload.get("version", "")).strip()
            if version:
                return version
        except (ValueError, OSError):
            pass
    return get_version()


def write_version_file(version: str, target_dir: str | os.PathLike, channel: str = "stable", **extra) -> Path:
    """Écrit le fichier ``version.json`` (métadonnées de l'installation)."""
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    payload = {"version": version, "channel": channel, **extra}
    marker = target / "version.json"
    marker.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return marker


# ---------------------------------------------------------------------------
# GitHub Releases
# ---------------------------------------------------------------------------

def _api_url(repo_slug: str, endpoint: str) -> str:
    return f"{_GITHUB_API}{repo_slug}/{endpoint}"


def _request_json(url: str, timeout: int = 30) -> dict:
    """GET une URL et parse le JSON avec gestion d'erreurs lisible."""
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("Release introuvable sur GitHub (404).") from exc
        raise UpdateError(f"GitHub a renvoyé une erreur HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise UpdateError(f"Impossible de joindre GitHub : {exc.reason}.") from exc
    except ValueError as exc:
        raise UpdateError("Réponse GitHub invalide (JSON illisible).") from exc


def latest_release(repo_slug: str = REPO_SLUG, prerelease: bool = False, timeout: int = 30) -> dict:
    """Retourne la dernière release (stable) publiée sur GitHub.

    Retourne un dictionnaire contenant au moins ``tag_name``, ``name``,
    ``draft``, ``prerelease`` et ``assets``.
    """
    endpoint = "releases/latest" if not prerelease else "releases"
    data = _request_json(_api_url(repo_slug, endpoint), timeout=timeout)
    if prerelease:
        # On prend la release la plus récente (par date) parmi les non-brouillons.
        releases = [r for r in data if not r.get("draft")]
        if not releases:
            raise UpdateError("Aucune release trouvée sur GitHub.")
        data = releases[0]
    return data


def release_version(release: dict) -> str:
    """Version ``MAJOR.MINOR.PATCH`` extraite du tag ``v1.2.3``."""
    tag = str(release.get("tag_name", "")).strip()
    if tag.startswith("v"):
        tag = tag[1:]
    return tag.split("+")[0].strip()


def find_asset(release: dict, version: str) -> dict | None:
    """Trouve l'artefact portable ``Jarvis-v{version}-portable.zip``."""
    wanted = f"{ARTIFACT_PREFIX}{version}{ARTIFACT_SUFFIX}"
    for asset in release.get("assets", []):
        name = str(asset.get("name", "")).strip()
        if name == wanted or name.endswith(wanted):
            return asset
    return None


def find_asset_sha256(release: dict, version: str) -> dict | None:
    """Trouve le fichier ``.sha256`` associé à un artefact."""
    wanted = f"{ARTIFACT_PREFIX}{version}{ARTIFACT_SUFFIX}.sha256"
    for asset in release.get("assets", []):
        name = str(asset.get("name", "")).strip()
        if name == wanted or name.endswith(wanted):
            return asset
    return None


# ---------------------------------------------------------------------------
# SHA-256 & téléchargement
# ---------------------------------------------------------------------------

def sha256_file(path: str | os.PathLike) -> str:
    """Calcule l'empreinte SHA-256 d'un fichier."""
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: str | os.PathLike, expected: str) -> bool:
    """Vrai si l'empreinte du fichier correspond à ``expected``."""
    expected = str(expected or "").strip().lower().split(" ")[0]
    if not expected:
        return False
    return sha256_file(path).lower() == expected


def download(
    url: str,
    dest: str | os.PathLike,
    *,
    sha256: str | None = None,
    timeout: int = 60,
    chunksize: int = 1024 * 256,
) -> Path:
    """Télécharge un fichier, vérifie son empreinte et retourne le chemin.

    Télécharge d'abord dans un fichier temporaire du répertoire cible, puis
    vérifie l'empreinte avant de déplacer vers ``dest`` : si le fichier est
    corrompu, la destination n'est jamais touchée.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".jarvis-dl-", suffix=".part", dir=str(dest.parent))
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Jarvis-Updater"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            with tmp_path.open("wb") as handle:
                while True:
                    chunk = resp.read(chunksize)
                    if not chunk:
                        break
                    handle.write(chunk)
        if sha256 and not verify_sha256(tmp_path, sha256):
            raise UpdateError("Empreinte SHA-256 invalide : fichier corrompu.")
        os.replace(tmp_path, dest)
        return dest
    except urllib.error.URLError as exc:
        raise UpdateError(f"Téléchargement interrompu : {exc.reason}.") from exc
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def download_asset(asset: dict, dest_dir: str | os.PathLike, sha256: str | None = None) -> Path:
    """Télécharge un artefact de release GitHub vers ``dest_dir``."""
    url = str(asset.get("browser_download_url", "")).strip()
    if not url:
        raise UpdateError("L'artefact ne dispose pas d'URL de téléchargement.")
    dest = Path(dest_dir) / str(asset["name"])
    return download(url, dest, sha256=sha256)


# ---------------------------------------------------------------------------
# Installation atomique
# ---------------------------------------------------------------------------

def _safe_extract(zip_path: str | os.PathLike, dest: str | os.PathLike) -> None:
    """Extrait une archive ZIP en refusant les chemins hors de ``dest``."""
    dest = Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.namelist():
            target = (dest / member).resolve()
            if not str(target).startswith(str(dest)):
                raise UpdateError(f"Chemin non autorisé dans l'archive : {member}")
        archive.extractall(dest)


def apply_update(
    zip_path: str | os.PathLike,
    install_dir: str | os.PathLike,
    *,
    version: str | None = None,
    channel: str = "stable",
    keep_backups: int = 2,
    marker_dir: str | os.PathLike | None = None,
) -> Path:
    """Remplace l'application de façon atomique.

    Procédé :
    1. l'installation courante est déplacée dans un dossier de sauvegarde ;
    2. l'archive est extraite dans un dossier temporaire ;
    3. si une étape échoue, l'ancienne installation est restaurée (rollback) ;
    4. ``version.json`` est écrit pour refléter la version installée.

    ``install_dir`` est le dossier remplaçable (ex. ``app/``). ``marker_dir``
    (défaut : ``install_dir``) indique où écrire ``version.json`` — par défaut
    sur le même dossier. Le launcher peut l'utiliser pour écrire le marqueur à
    côté de lui-même tout en remplaçant ``app/``.

    Les **données utilisateur** ne sont jamais touchées : elles vivent dans
    ``%LOCALAPPDATA%\\Jarvis``, hors du dossier d'installation.
    """
    install_dir = Path(install_dir)
    if not install_dir.is_dir():
        raise UpdateError("Dossier d'installation introuvable.")
    zip_path = Path(zip_path)
    marker_dir = Path(marker_dir) if marker_dir else install_dir

    # Dossier de sauvegarde (rollback) *à côté* de l'installation.
    backup_root = install_dir.parent / ".jarvis-backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    import time

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = backup_root / f"backup-{stamp}"
    stage_dir = backup_root / f"stage-{stamp}"

    installed = False
    try:
        # 1. Extraire l'archive dans un dossier de préparation.
        _safe_extract(zip_path, stage_dir)

        # 2. Déplacer l'installation courante vers la sauvegarde.
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        os.rename(str(install_dir), str(backup_dir))

        # 3. Déplacer le contenu préparé vers le dossier d'installation.
        #    L'archive portable est censée contenir le contenu de l'app
        #    directement à sa racine (Jarvis.exe, resources/, ...).
        contents = [p for p in stage_dir.iterdir() if p.name != "__MACOSX"]
        if len(contents) == 1 and contents[0].is_dir():
            os.replace(str(contents[0]), str(install_dir))
        else:
            shutil.copytree(str(stage_dir), str(install_dir))
        installed = True

        write_version_file(version or get_version(), str(marker_dir), channel=channel)
        return install_dir
    except Exception as exc:
        # Rollback : restaurer l'installation précédente.
        if installed and install_dir.exists() and backup_dir.is_dir():
            shutil.rmtree(install_dir)
        if backup_dir.is_dir() and not install_dir.is_dir():
            os.rename(str(backup_dir), str(install_dir))
        raise UpdateError(f"Installation de la mise à jour impossible : {exc}") from exc
    finally:
        # Nettoyer le dossier de préparation (jamais les backups : ils
        # permettent le rollback).
        if stage_dir.is_dir():
            shutil.rmtree(stage_dir, ignore_errors=True)
        # Ne garder que les derniers backups.
        backups = sorted(backup_root.glob("backup-*"), reverse=True)
        for old in backups[keep_backups:]:
            shutil.rmtree(old, ignore_errors=True)


def rollback(install_dir: str | os.PathLike) -> Path | None:
    """Restaure l'installation depuis la sauvegarde la plus récente."""
    backup_root = Path(install_dir).parent / ".jarvis-backups"
    backups = sorted(backup_root.glob("backup-*"), reverse=True) if backup_root.is_dir() else []
    if not backups:
        return None
    backup = backups[0]
    install_dir = Path(install_dir)
    if install_dir.is_dir():
        shutil.rmtree(install_dir)
    os.rename(str(backup), str(install_dir))
    return install_dir


def is_app_running(install_dir: str | os.PathLike, exe_name: str = "Jarvis.exe") -> bool:
    """Vrai si l'application est actuellement en cours d'exécution.

    Utilisé pour avertir l'utilisateur avant une mise à jour.
    """
    if os.name != "nt":
        return False
    try:
        import subprocess

        output = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        name = exe_name.lower()
        return any(name in line.lower() for line in output.splitlines())
    except Exception:
        return False


def is_connected() -> bool:
    """Vérifie simplement l'accès réseau (utile pour de bons messages d'erreur)."""
    import socket

    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3).close()
        return True
    except OSError:
        return False


def check_for_update(
    repo_slug: str = REPO_SLUG,
    *,
    current: str | None = None,
    prerelease: bool = False,
    show_prerelease: bool = False,
    timeout: int = 30,
) -> tuple[str, dict]:
    """Compare la version locale et la dernière release.

    Retourne ``(current_version, release)``. L'appelant décide ensuite
    ``is_newer(...)``. Lève ``UpdateError`` si GitHub est inaccessible.
    """
    current = current or local_version()
    release = latest_release(repo_slug, prerelease=prerelease, timeout=timeout)
    return current, release


def build_update_plan(
    repo_slug: str = REPO_SLUG, *, current: str | None = None, prerelease: bool = False
) -> dict:
    """Construit le plan de mise à jour complet (testable, sans réseau)."""
    current, release = check_for_update(repo_slug, current=current, prerelease=prerelease)
    latest = release_version(release)
    update_available = is_newer(latest, current)
    asset = find_asset(release, latest) if update_available else None
    asset_sha = find_asset_sha256(release, latest) if update_available else None
    return {
        "current": current,
        "latest": latest,
        "update_available": update_available,
        "release": release,
        "asset": asset,
        "asset_sha256": asset_sha,
        "asset_url": asset.get("browser_download_url") if asset else None,
        "asset_name": asset.get("name") if asset else None,
    }


def perform_update(
    plan: dict,
    install_dir: str | os.PathLike,
    *,
    dest_dir: str | os.PathLike | None = None,
    auto_confirm_sha: bool = True,
    marker_dir: str | os.PathLike | None = None,
) -> Path:
    """Télécharge, vérifie et installe la mise à jour décrite par ``plan``.

    Le SHA-256 est lu, si possible, depuis le fichier ``.sha256`` publié
    (téléchargé lui-même). ``auto_confirm_sha=True`` impose la vérification :
    sans empreinte publiée, la mise à jour est refusée (sécurité par défaut).

    ``marker_dir`` est transmis à ``apply_update`` pour écrire ``version.json``
    à un endroit stable (à côté du launcher) tout en remplaçant ``app/``.
    """
    asset = plan.get("asset")
    if not asset:
        raise UpdateError("Aucun artefact de mise à jour disponible.")
    dest_dir = Path(dest_dir) if dest_dir else Path(tempfile.gettempdir())
    zip_path = download_asset(asset, dest_dir)
    sha = None
    asset_sha = plan.get("asset_sha256")
    try:
        if asset_sha:
            sha_url = str(asset_sha.get("browser_download_url", "")).strip()
            if sha_url:
                sha_path = download(sha_url, dest_dir / asset_sha["name"])
                sha = sha_path.read_text(encoding="utf-8").strip().split(" ")[0]
    except UpdateError:
        sha = None

    if sha:
        if not verify_sha256(zip_path, sha):
            raise UpdateError("Empreinte SHA-256 invalide : archive corrompue.")
    elif auto_confirm_sha:
        raise UpdateError("Aucune empreinte SHA-256 publiée : mise à jour refusée.")

    return apply_update(
        zip_path,
        install_dir,
        version=plan.get("latest"),
        channel=(plan.get("release") or {}).get("name", "stable"),
        marker_dir=marker_dir,
    )
