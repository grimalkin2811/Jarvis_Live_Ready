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

Garde-fous ajoutés (v1.0.2+) :

* Validation de la structure avant remplacement (Jarvis.exe, _internal/python311.dll, etc.)
* Rejet des archives aplaties (python311.dll à la racine)
* Validation après extraction
* Rollback automatique si la nouvelle version est invalide
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
# Validation de structure (centralisée)
# ---------------------------------------------------------------------------

# Import défensif : src.packaging_validation peut ne pas être disponible dans
# certains contextes (launcher minimal), on fournit un fallback.
try:
    from .packaging_validation import (
        CRITICAL_APP_FILES,
        FORBIDDEN_FLATTENED_FILES,
        validate_app_dir as _validate_app_dir,
        validate_zip as _validate_zip,
    )
except ImportError:
    CRITICAL_APP_FILES = [
        "Jarvis.exe",
        "_internal/python311.dll",
        "_internal/base_library.zip",
    ]
    FORBIDDEN_FLATTENED_FILES = [
        "python311.dll",
        "base_library.zip",
    ]

    def _validate_app_dir(app_path, strict=False):
        base = Path(app_path)
        missing = []
        forbidden = []
        for rel in CRITICAL_APP_FILES:
            if not (base / rel).exists():
                missing.append(rel)
        for rel in FORBIDDEN_FLATTENED_FILES:
            if (base / rel).exists():
                forbidden.append(rel)
        if not (base / "_internal").is_dir():
            missing.append("_internal/ (dossier)")
        is_valid = not missing and not forbidden
        return is_valid, missing, forbidden

    def _validate_zip(zip_path, expect_app_prefix=False):
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = [n.replace("\\", "/") for n in zf.namelist()]
                missing = []
                forbidden = []
                has_app_prefix = any(n.startswith("app/") for n in names)
                # Vérifie fichiers critiques
                if has_app_prefix:
                    for rel in CRITICAL_APP_FILES:
                        if f"app/{rel}" not in names:
                            missing.append(f"app/{rel}")
                    if not any(n.startswith("app/_internal/") for n in names):
                        missing.append("app/_internal/ (dossier)")
                    for rel in FORBIDDEN_FLATTENED_FILES:
                        if f"app/{rel}" in names:
                            forbidden.append(f"app/{rel}")
                else:
                    for rel in CRITICAL_APP_FILES:
                        if rel not in names:
                            missing.append(rel)
                    if not any(n.startswith("_internal/") for n in names):
                        missing.append("_internal/ (dossier)")
                    for rel in FORBIDDEN_FLATTENED_FILES:
                        if rel in names:
                            forbidden.append(rel)
                return (not missing and not forbidden), missing, forbidden
        except Exception as exc:
            return False, [f"Erreur ZIP: {exc}"], []


def _format_validation_error(prefix: str, missing: list[str], forbidden: list[str]) -> str:
    lines = [prefix]
    if missing:
        lines.append("  Fichiers manquants:")
        for m in missing:
            lines.append(f"    - {m}")
    if forbidden:
        lines.append("  Fichiers aplatis détectés (doivent être dans _internal/):")
        for f in forbidden:
            lines.append(f"    - {f}")
    return "\n".join(lines)


def validate_extracted_app_structure(extracted_dir: Path) -> tuple[Path, bool, list[str], list[str]]:
    """Trouve la racine de l'app dans un dossier extrait et la valide.

    Gère deux formats de ZIP :
    1. ZIP avec fichiers à la racine (Jarvis.exe, _internal/...) -> extracted_dir est la racine
    2. ZIP avec un seul dossier (app/ ou Jarvis-v1.0.0/) contenant l'app -> ce dossier est la racine

    Retourne (app_root, is_valid, missing, forbidden)
    """
    extracted_dir = Path(extracted_dir)
    # Liste le contenu
    contents = [p for p in extracted_dir.iterdir() if p.name != "__MACOSX"]

    # Cas 1 : un seul dossier, probablement l'app
    if len(contents) == 1 and contents[0].is_dir():
        candidate = contents[0]
        # Si ce dossier contient Jarvis.exe, c'est la racine de l'app
        if (candidate / "Jarvis.exe").exists() or (candidate / "_internal").is_dir():
            app_root = candidate
        else:
            # Peut-être un dossier avec sous-dossier app/
            if (candidate / "app" / "Jarvis.exe").exists():
                app_root = candidate / "app"
            else:
                app_root = candidate
    else:
        # Plusieurs fichiers à la racine, ou directement Jarvis.exe
        if (extracted_dir / "Jarvis.exe").exists():
            app_root = extracted_dir
        elif (extracted_dir / "app" / "Jarvis.exe").exists():
            app_root = extracted_dir / "app"
        else:
            # Par défaut, on considère extracted_dir comme racine
            app_root = extracted_dir

    is_valid, missing, forbidden = _validate_app_dir(app_root)
    return app_root, is_valid, missing, forbidden


def assert_valid_app_dir(app_path: Path) -> None:
    """Lève UpdateError si le dossier app est invalide."""
    is_valid, missing, forbidden = _validate_app_dir(app_path)
    if not is_valid:
        msg = _format_validation_error(f"Structure invalide pour {app_path}:", missing, forbidden)
        raise UpdateError(msg)


def assert_valid_zip(zip_path: Path) -> None:
    """Lève UpdateError si le ZIP est invalide."""
    is_valid, missing, forbidden = _validate_zip(zip_path)
    if not is_valid:
        msg = _format_validation_error(f"Archive invalide {zip_path}:", missing, forbidden)
        raise UpdateError(msg)


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
# Installation atomique avec validation
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
    """Remplace l'application de façon atomique avec validation.

    Procédé :
    1. Valide l'archive ZIP (structure _internal, pas d'aplatissement)
    2. Extrait l'archive dans un dossier temporaire
    3. Valide la structure extraite (Jarvis.exe, _internal/python311.dll, etc.)
    4. Déplace l'installation courante vers un backup
    5. Installe la nouvelle version
    6. Valide la nouvelle installation
    7. Si une étape échoue, rollback vers l'ancienne version

    ``install_dir`` est le dossier remplaçable (ex. ``app/``). ``marker_dir``
    (défaut : ``install_dir``) indique où écrire ``version.json``.

    Les **données utilisateur** ne sont jamais touchées.
    """
    install_dir = Path(install_dir)
    if not install_dir.is_dir():
        raise UpdateError(f"Dossier d'installation introuvable: {install_dir}")
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise UpdateError(f"Archive introuvable: {zip_path}")
    marker_dir = Path(marker_dir) if marker_dir else install_dir

    # 0. Validation préalable du ZIP
    print(f"[Updater] Validation de l'archive {zip_path}...")
    is_valid, missing, forbidden = _validate_zip(zip_path)
    if not is_valid:
        msg = _format_validation_error(f"Archive invalide {zip_path}:", missing, forbidden)
        print(f"[Updater] {msg}")
        raise UpdateError(f"Archive invalide, mise à jour refusée:\n{msg}")

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
        print(f"[Updater] Extraction vers {stage_dir}...")
        _safe_extract(zip_path, stage_dir)

        # 2. Valider la structure extraite
        print(f"[Updater] Validation de la structure extraite...")
        app_root, is_valid, missing, forbidden = validate_extracted_app_structure(stage_dir)
        if not is_valid:
            msg = _format_validation_error(f"Structure extraite invalide (racine détectée: {app_root}):", missing, forbidden)
            print(f"[Updater] {msg}")
            raise UpdateError(f"Archive extraite invalide, mise à jour refusée:\n{msg}")

        print(f"[Updater] Structure extraite valide: {app_root}")

        # 3. Déplacer l'installation courante vers la sauvegarde.
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        print(f"[Updater] Sauvegarde de l'ancienne version vers {backup_dir}...")
        os.rename(str(install_dir), str(backup_dir))

        # 4. Déplacer le contenu préparé vers le dossier d'installation.
        print(f"[Updater] Installation de la nouvelle version vers {install_dir}...")
        try:
            # Si l'app_root est un sous-dossier de stage_dir, on le déplace
            # Sinon, on copie tout le stage_dir
            if app_root.resolve() != stage_dir.resolve():
                # app_root est un sous-dossier (ex: stage/app ou stage/Jarvis-v1.0.0)
                os.replace(str(app_root), str(install_dir))
            else:
                # app_root == stage_dir, contient directement les fichiers
                # On doit copier le contenu
                shutil.copytree(str(stage_dir), str(install_dir))
        except Exception as exc:
            # Si l'installation échoue, on tente de restaurer immédiatement
            print(f"[Updater] Échec installation: {exc}, rollback...")
            if install_dir.exists():
                shutil.rmtree(install_dir, ignore_errors=True)
            if backup_dir.is_dir() and not install_dir.exists():
                os.rename(str(backup_dir), str(install_dir))
            raise UpdateError(f"Installation impossible: {exc}") from exc

        installed = True

        # 5. Valider la nouvelle installation
        print(f"[Updater] Validation de la nouvelle installation...")
        is_valid, missing, forbidden = _validate_app_dir(install_dir)
        if not is_valid:
            msg = _format_validation_error(f"Nouvelle installation invalide {install_dir}:", missing, forbidden)
            print(f"[Updater] {msg}")
            # Rollback
            if install_dir.exists():
                shutil.rmtree(install_dir)
            if backup_dir.is_dir():
                os.rename(str(backup_dir), str(install_dir))
            raise UpdateError(f"Installation invalide après extraction, rollback effectué:\n{msg}")

        # 6. Écrire version.json
        write_version_file(version or get_version(), str(marker_dir), channel=channel)
        print(f"[Updater] Mise à jour réussie vers {version or get_version()}")
        return install_dir

    except UpdateError:
        # Déjà une UpdateError avec message clair, on la propage
        raise
    except Exception as exc:
        # Rollback pour toute autre erreur
        print(f"[Updater] Erreur inattendue: {exc}, rollback...")
        if installed and install_dir.exists() and backup_dir.is_dir():
            shutil.rmtree(install_dir, ignore_errors=True)
        if backup_dir.is_dir() and not install_dir.is_dir():
            try:
                os.rename(str(backup_dir), str(install_dir))
                print(f"[Updater] Rollback réussi")
            except Exception as rollback_exc:
                print(f"[Updater] Rollback échoué: {rollback_exc}")
        raise UpdateError(f"Installation de la mise à jour impossible : {exc}") from exc
    finally:
        # Nettoyer le dossier de préparation
        if stage_dir.is_dir():
            shutil.rmtree(stage_dir, ignore_errors=True)
        # Ne garder que les derniers backups
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
    print(f"[Updater] Téléchargement de {asset.get('name')}...")
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
        print(f"[Updater] Vérification SHA-256...")
        if not verify_sha256(zip_path, sha):
            raise UpdateError("Empreinte SHA-256 invalide : archive corrompue.")
        print(f"[Updater] SHA-256 OK")
    elif auto_confirm_sha:
        raise UpdateError("Aucune empreinte SHA-256 publiée : mise à jour refusée.")

    # Validation du ZIP avant installation
    print(f"[Updater] Validation du ZIP téléchargé...")
    assert_valid_zip(zip_path)

    return apply_update(
        zip_path,
        install_dir,
        version=plan.get("latest"),
        channel=(plan.get("release") or {}).get("name", "stable"),
        marker_dir=marker_dir,
    )
