"""Fournisseur Deezer pour Jarvis.

Capacités réelles (état 2025/2026) :

* **Catalogue public** (sans authentification) : recherche d'artistes, albums,
  morceaux et playlists publiques via ``https://api.deezer.com``.
* **Lecture** : l'API Deezer ne fournit plus de endpoint de lecture pour les
  applications tierces individuelles. Jarvis ouvre donc le contenu via :
  1. le protocole ``deezer://`` (application desktop Windows si installée) ;
  2. à défaut, l'URL web ``https://www.deezer.com/...`` dans le navigateur
     par défaut de l'utilisateur (pas un navigateur hardcodé).
* **Contrôles pause/suivant/précédent** : touches multimédia système Windows
  (VK_MEDIA_*), qui sont relayées à l'application au premier plan (Deezer
  desktop ou l'onglet web si le focus est correct).
* **Playlists personnelles / favoris** : nécessitent OAuth 2.0. Deezer a
  restreint la création de nouvelles applications API ; si l'utilisateur
  dispose d'un token valide (``DEEZER_ACCESS_TOKEN``), Jarvis l'utilise.
  Sinon la fonctionnalité est clairement signalée comme indisponible.
* **Morceau en cours** : l'API ne fournit pas d'endpoint de lecture temps
  réel. Jarvis conserve un **état local** de la dernière piste lancée et
  l'annonce honnêtement ; si rien n'a été lancé via Jarvis, il le dit.

Aucune simulation : si une action est impossible, le message le dit.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ... import paths
from ..models import (
    Album,
    Artist,
    Playlist,
    SearchResults,
    Track,
    album_from_deezer,
    artist_from_deezer,
    playlist_from_deezer,
    track_from_deezer,
)

log = logging.getLogger("jarvis.music.deezer")

API_BASE = "https://api.deezer.com"
CONNECT_BASE = "https://connect.deezer.com/oauth"
DEFAULT_TIMEOUT = 12.0
USER_AGENT = "Jarvis-Music/1.5.0 (+https://github.com/grimalkin2811/Jarvis_Live_Ready)"

# Touches multimédia Windows (winuser.h).
VK_MEDIA_NEXT = 0xB0
VK_MEDIA_PREV = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3

# Exécutables / processus Deezer connus sur Windows.
DEEZER_PROCESS_NAMES = (
    "deezer.exe",
    "deezer desktop.exe",
    "deezerdesktop.exe",
)

# Emplacements d'installation courants (desktop Electron / Microsoft Store).
_DEEZER_CANDIDATE_PATHS = (
    r"%LOCALAPPDATA%\Programs\Deezer\Deezer.exe",
    r"%LOCALAPPDATA%\Deezer\Deezer.exe",
    r"%PROGRAMFILES%\Deezer\Deezer.exe",
    r"%PROGRAMFILES(X86)%\Deezer\Deezer.exe",
    r"%LOCALAPPDATA%\Microsoft\WindowsApps\Deezer.exe",
)


# ---------------------------------------------------------------------------
# Helpers génériques
# ---------------------------------------------------------------------------


def _ok(**payload: Any) -> dict[str, Any]:
    result = {"success": True}
    result.update(payload)
    return result


def _err(message: Any, **payload: Any) -> dict[str, Any]:
    text = str(message)
    result = {"success": False, "error": text, "message": text}
    # ``payload`` peut surcharger message (ex. formulation plus naturelle),
    # mais ne doit pas effacer error.
    result.update(payload)
    result["error"] = str(result.get("error") or text)
    if "message" not in payload:
        result["message"] = result["error"]
    return result


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_text(value: str) -> str:
    """Normalise un libellé pour comparaisons floues (casse, accents, ponctuation)."""
    text = _strip_accents(str(value or "")).lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def similarity(a: str, b: str) -> float:
    """Score de similarité simple ∈ [0, 1] (égalité, préfixe, inclusion, tokens)."""
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    # Compacté sans espaces : « cyberpunk » ≈ « cyber punk »
    ca, cb = na.replace(" ", ""), nb.replace(" ", "")
    if ca == cb:
        return 0.96
    if na in nb or nb in na or ca in cb or cb in ca:
        shorter, longer = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
        return 0.72 + 0.25 * (len(shorter) / max(len(longer), 1))
    ta, tb = set(na.split()), set(nb.split())
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    jaccard = inter / union if union else 0.0
    # Bonus si tous les tokens de la requête sont présents.
    coverage = inter / len(ta)
    score = max(jaccard, coverage * 0.85)
    # Sous-chaîne token-niveau : « punk » dans « cyberpunk »
    if score < 0.5:
        joined_b = " ".join(tb)
        joined_a = " ".join(ta)
        token_hits = sum(1 for t in ta if t in cb or any(t in x for x in tb))
        if token_hits:
            score = max(score, 0.55 * token_hits / max(len(ta), 1))
        if ca and cb and (ca in cb or cb in ca):
            score = max(score, 0.75)
        _ = (joined_a, joined_b)  # silence linters
    return score


# ---------------------------------------------------------------------------
# Client HTTP minimal (stdlib uniquement — pas de dépendance tierce)
# ---------------------------------------------------------------------------


class DeezerAPIError(Exception):
    """Erreur renvoyée par l'API Deezer ou le transport réseau."""

    def __init__(self, message: str, *, code: int | None = None, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message


class DeezerHTTPClient:
    """Client REST Deezer (catalogue public + endpoints authentifiés optionnels)."""

    def __init__(
        self,
        *,
        access_token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.access_token = (access_token or "").strip() or None
        self.timeout = timeout
        # ``opener`` est injectable pour les tests (mock de urlopen).
        self._opener = opener or urllib.request.urlopen

    # -- bas niveau ---------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        auth: bool = False,
    ) -> dict[str, Any] | list[Any]:
        params = dict(params or {})
        if auth:
            if not self.access_token:
                raise DeezerAPIError(
                    "Authentification Deezer requise (token manquant).",
                    code=200,
                )
            params.setdefault("access_token", self.access_token)

        path = path if path.startswith("/") else f"/{path}"
        url = f"{API_BASE}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"

        body: bytes | None = None
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        request = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read()
                status = getattr(response, "status", None) or response.getcode()
        except urllib.error.HTTPError as exc:
            payload = _safe_json(exc.read() if hasattr(exc, "read") else b"")
            message = _extract_error_message(payload) or f"HTTP {exc.code}"
            raise DeezerAPIError(message, code=_extract_error_code(payload), status=exc.code) from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise DeezerAPIError(f"Réseau indisponible : {reason}", status=0) from exc
        except TimeoutError as exc:
            raise DeezerAPIError("Délai d'attente Deezer dépassé (timeout).", status=0) from exc
        except OSError as exc:
            raise DeezerAPIError(f"Connexion Deezer impossible : {exc}", status=0) from exc

        payload = _safe_json(raw)
        if isinstance(payload, dict) and "error" in payload:
            err = payload["error"] or {}
            message = err.get("message") if isinstance(err, dict) else str(err)
            code = err.get("code") if isinstance(err, dict) else None
            # Code 200 = OAuthException / token invalide ; 4 = quota ; 300 = data.
            raise DeezerAPIError(message or "Erreur API Deezer", code=code, status=status)
        return payload  # type: ignore[return-value]

    def get(self, path: str, **kwargs: Any) -> dict[str, Any] | list[Any]:
        return self.request("GET", path, **kwargs)

    # -- catalogue ----------------------------------------------------------

    def search(self, query: str, *, index_type: str = "", limit: int = 8) -> list[dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            return []
        path = f"/search/{index_type}" if index_type else "/search"
        payload = self.get(path, params={"q": q, "limit": max(1, min(int(limit), 50))})
        if not isinstance(payload, dict):
            return []
        data = payload.get("data") or []
        return [item for item in data if isinstance(item, dict)]

    def get_track(self, track_id: str | int) -> dict[str, Any]:
        payload = self.get(f"/track/{track_id}")
        return payload if isinstance(payload, dict) else {}

    def get_artist(self, artist_id: str | int) -> dict[str, Any]:
        payload = self.get(f"/artist/{artist_id}")
        return payload if isinstance(payload, dict) else {}

    def get_artist_top(self, artist_id: str | int, *, limit: int = 5) -> list[dict[str, Any]]:
        payload = self.get(f"/artist/{artist_id}/top", params={"limit": limit})
        if not isinstance(payload, dict):
            return []
        return [item for item in (payload.get("data") or []) if isinstance(item, dict)]

    def get_album(self, album_id: str | int) -> dict[str, Any]:
        payload = self.get(f"/album/{album_id}")
        return payload if isinstance(payload, dict) else {}

    def get_playlist(self, playlist_id: str | int) -> dict[str, Any]:
        payload = self.get(f"/playlist/{playlist_id}")
        return payload if isinstance(payload, dict) else {}

    def get_chart_tracks(self, *, limit: int = 10) -> list[dict[str, Any]]:
        payload = self.get("/chart/0/tracks", params={"limit": limit})
        if not isinstance(payload, dict):
            return []
        return [item for item in (payload.get("data") or []) if isinstance(item, dict)]

    # -- utilisateur (OAuth) ------------------------------------------------

    def get_me(self) -> dict[str, Any]:
        payload = self.get("/user/me", auth=True)
        return payload if isinstance(payload, dict) else {}

    def get_my_playlists(self, *, limit: int = 50) -> list[dict[str, Any]]:
        payload = self.get("/user/me/playlists", params={"limit": limit}, auth=True)
        if not isinstance(payload, dict):
            return []
        return [item for item in (payload.get("data") or []) if isinstance(item, dict)]

    def get_my_flow(self) -> dict[str, Any]:
        """Flow radio personnel — disponible uniquement authentifié."""
        payload = self.get("/user/me/flow", auth=True)
        return payload if isinstance(payload, dict) else {}


def _safe_json(raw: bytes | str | None) -> Any:
    if not raw:
        return {}
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
    else:
        text = raw
    try:
        return json.loads(text) if text else {}
    except ValueError:
        return {"raw": text}


def _extract_error_message(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    err = payload.get("error")
    if isinstance(err, dict):
        return err.get("message") or err.get("type")
    if isinstance(err, str):
        return err
    return None


def _extract_error_code(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    err = payload.get("error")
    if isinstance(err, dict):
        code = err.get("code")
        try:
            return int(code) if code is not None else None
        except (TypeError, ValueError):
            return None
    return None


# ---------------------------------------------------------------------------
# Auth / persistance du token
# ---------------------------------------------------------------------------


def _token_file() -> Path:
    try:
        return paths.deezer_auth_file()
    except Exception:
        return paths.data_dir() / "deezer_auth.json"


def load_stored_token(path: Path | None = None) -> dict[str, Any]:
    target = path or _token_file()
    if not target.is_file():
        return {}
    try:
        with target.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_stored_token(payload: dict[str, Any], path: Path | None = None) -> Path:
    target = path or _token_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Ne jamais logger le token. On écrit avec permissions utilisateur.
    clean = {
        "access_token": str(payload.get("access_token") or "").strip(),
        "expires_at": payload.get("expires_at"),
        "user_id": payload.get("user_id"),
        "user_name": payload.get("user_name"),
        "updated_at": int(time.time()),
    }
    with target.open("w", encoding="utf-8") as handle:
        json.dump(clean, handle, indent=2, ensure_ascii=False)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target


def clear_stored_token(path: Path | None = None) -> None:
    target = path or _token_file()
    try:
        if target.is_file():
            target.unlink()
    except OSError:
        pass


def resolve_access_token(
    *,
    explicit: str | None = None,
    env: dict[str, str] | None = None,
    store_path: Path | None = None,
) -> str | None:
    """Résout le token Deezer : argument > environnement > fichier local."""
    if explicit and explicit.strip():
        return explicit.strip()
    environ = env if env is not None else os.environ
    for key in ("DEEZER_ACCESS_TOKEN", "JARVIS_DEEZER_TOKEN"):
        value = (environ.get(key) or "").strip()
        if value:
            return value
    stored = load_stored_token(store_path)
    token = str(stored.get("access_token") or "").strip()
    expires_at = stored.get("expires_at")
    if token and expires_at:
        try:
            if float(expires_at) and float(expires_at) < time.time() - 30:
                return None  # expiré
        except (TypeError, ValueError):
            pass
    return token or None


# ---------------------------------------------------------------------------
# Lancement / détection de Deezer (desktop ou web)
# ---------------------------------------------------------------------------


def find_deezer_executable() -> str | None:
    """Cherche l'exécutable Deezer desktop sur la machine."""
    if os.name != "nt" and platform.system() != "Windows":
        # Sur Linux/macOS on ne force pas de chemin Windows.
        which = shutil.which("deezer")
        return which
    for candidate in _DEEZER_CANDIDATE_PATHS:
        expanded = os.path.expandvars(candidate)
        if expanded and os.path.isfile(expanded):
            return expanded
    which = shutil.which("Deezer") or shutil.which("deezer")
    return which


def is_deezer_running(
    *,
    process_checker: Callable[[], list[str]] | None = None,
) -> bool:
    """Vrai si un processus Deezer est détecté."""
    names = {n.lower() for n in DEEZER_PROCESS_NAMES}
    if process_checker is not None:
        running = [p.lower() for p in process_checker()]
        return any(any(name in proc for name in names) for proc in running)

    if os.name != "nt":
        # psutil si disponible, sinon fallback vide (non bloquant hors Windows).
        try:
            import psutil  # type: ignore

            for proc in psutil.process_iter(["name"]):
                pname = (proc.info.get("name") or "").lower()
                if any(name in pname for name in names):
                    return True
        except Exception:
            pass
        return False

    try:
        result = subprocess.run(
            ["tasklist"],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output = (result.stdout or "").lower()
        return any(name in output for name in names)
    except Exception:
        return False


def _send_media_key(vk_code: int) -> bool:
    """Envoie une touche multimédia Windows via keybd_event."""
    if os.name != "nt":
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        KEYEVENTF_EXTENDEDKEY = 0x0001
        KEYEVENTF_KEYUP = 0x0002
        user32.keybd_event(vk_code, 0, KEYEVENTF_EXTENDEDKEY, 0)
        user32.keybd_event(vk_code, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
        return True
    except Exception as exc:
        log.debug("media key failed: %s", exc)
        return False


def build_deezer_uri(resource: str, resource_id: str, *, autoplay: bool = True) -> str:
    """Construit un deep-link Deezer (protocole app)."""
    resource = resource.strip("/").lower()
    uri = f"deezer://www.deezer.com/{resource}/{resource_id}"
    if autoplay:
        uri += "?autoplay=true"
    return uri


def build_deezer_web_url(resource: str, resource_id: str, *, autoplay: bool = True) -> str:
    resource = resource.strip("/").lower()
    url = f"https://www.deezer.com/{resource}/{resource_id}"
    if autoplay:
        url += "?autoplay=true"
    return url


def open_deezer_content(
    resource: str,
    resource_id: str,
    *,
    autoplay: bool = True,
    prefer_app: bool = True,
    opener: Callable[[str], bool] | None = None,
    app_launcher: Callable[[str], bool] | None = None,
    ensure_running: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Ouvre un contenu Deezer (app desktop si possible, sinon navigateur).

    Ne hardcode **aucun** navigateur : ``webbrowser.open`` utilise le
    navigateur par défaut de l'utilisateur.
    """
    web_url = build_deezer_web_url(resource, resource_id, autoplay=autoplay)
    app_uri = build_deezer_uri(resource, resource_id, autoplay=autoplay)
    open_fn = opener or (lambda url: bool(webbrowser.open(url)))

    if prefer_app:

        def _default_app_launcher(uri: str) -> bool:
            if os.name == "nt":
                try:
                    os.startfile(uri)  # type: ignore[attr-defined]
                    return True
                except Exception:
                    exe = find_deezer_executable()
                    if exe:
                        try:
                            subprocess.Popen([exe, uri], shell=False)
                            return True
                        except Exception:
                            return False
                    return False
            # Linux/macOS : tenter xdg-open / open
            for cmd in (("xdg-open", uri), ("open", uri)):
                try:
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return True
                except Exception:
                    continue
            return False

        launcher = app_launcher or _default_app_launcher

        # Tente d'ouvrir / réveiller l'app si un helper est fourni.
        if ensure_running is not None:
            try:
                ensure_running()
            except Exception:
                pass

        if launcher(app_uri):
            return _ok(
                opened=True,
                via="app",
                uri=app_uri,
                url=web_url,
                resource=resource,
                resource_id=str(resource_id),
                message="Contenu Deezer ouvert dans l'application.",
            )

    try:
        ok = open_fn(web_url)
    except Exception as exc:
        return _err(f"Impossible d'ouvrir Deezer : {exc}", url=web_url)
    if not ok:
        return _err("Ouverture de Deezer refusée par le système.", url=web_url)
    return _ok(
        opened=True,
        via="web",
        url=web_url,
        uri=app_uri,
        resource=resource,
        resource_id=str(resource_id),
        message="Contenu Deezer ouvert dans le navigateur par défaut.",
    )


# ---------------------------------------------------------------------------
# Sélection intelligente de résultats
# ---------------------------------------------------------------------------


@dataclass
class MatchCandidate:
    kind: str  # track | artist | album | playlist
    score: float
    item: Any
    label: str


def rank_tracks(
    tracks: list[Track],
    *,
    title: str | None = None,
    artist: str | None = None,
) -> list[tuple[float, Track]]:
    ranked: list[tuple[float, Track]] = []
    for track in tracks:
        score = 0.0
        if title:
            score = max(score, similarity(title, track.title))
            # "Around the World - Daft Punk" collé dans title
            score = max(score, similarity(title, f"{track.title} {track.artist}"))
        if artist:
            artist_score = similarity(artist, track.artist)
            if title:
                score = score * 0.65 + artist_score * 0.35
            else:
                score = max(score, artist_score)
        if not title and not artist:
            score = 0.5
        ranked.append((score, track))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return ranked


def pick_best_track(
    tracks: list[Track],
    *,
    title: str | None = None,
    artist: str | None = None,
    min_score: float = 0.55,
    ambiguity_delta: float = 0.08,
) -> tuple[Track | None, list[Track], str | None]:
    """Sélectionne un morceau ou signale une ambiguïté.

    Retourne ``(best, alternatives, reason)`` :
    * ``best`` non None → choix clair ;
    * ``best`` None + alternatives → demander à l'utilisateur ;
    * les deux vides → aucun résultat.
    """
    if not tracks:
        return None, [], "empty"
    ranked = rank_tracks(tracks, title=title, artist=artist)
    best_score, best = ranked[0]
    if best_score < min_score:
        # On propose quand même le top si on a une requête libre.
        if title or artist:
            alts = [t for s, t in ranked[:5] if s >= min_score * 0.7]
            if len(alts) >= 2:
                return None, alts, "ambiguous"
            if alts:
                return alts[0], [], None
        return None, [t for _, t in ranked[:5]], "low_score"

    close = [(s, t) for s, t in ranked if s >= best_score - ambiguity_delta]
    # Ambiguïté si titres distincts OU même titre mais artistes distincts
    # (ex. plusieurs « Halo ») et qu'aucun artiste n'a été précisé pour trancher.
    distinct_titles = {normalize_text(t.title) for _, t in close}
    distinct_artists = {normalize_text(t.artist) for _, t in close}
    if len(close) > 1 and (title or artist):
        same_title_diff_artist = len(distinct_titles) == 1 and len(distinct_artists) > 1
        diff_titles = len(distinct_titles) > 1
        if same_title_diff_artist or diff_titles:
            # Si l'artiste est fourni et que le meilleur matche bien, on tranche.
            if artist and similarity(artist, best.artist) >= 0.85 and best_score >= 0.8:
                return best, [], None
            return None, [t for _, t in close[:5]], "ambiguous"
    return best, [], None


def pick_best_named(
    items: list[Any],
    query: str,
    *,
    name_attr: str = "title",
    min_score: float = 0.55,
    ambiguity_delta: float = 0.1,
) -> tuple[Any | None, list[Any], str | None]:
    if not items:
        return None, [], "empty"
    ranked: list[tuple[float, Any]] = []
    for item in items:
        name = getattr(item, name_attr, "") or ""
        ranked.append((similarity(query, name), item))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    best_score, best = ranked[0]
    if best_score < min_score:
        alts = [it for s, it in ranked[:5] if s >= min_score * 0.65]
        if len(alts) >= 2:
            return None, alts, "ambiguous"
        if ranked:
            # Dernier recours : meilleur score même bas, signalé low_score.
            return None, [it for _, it in ranked[:5]], "low_score"
        return None, [], "empty"
    close = [(s, it) for s, it in ranked if s >= best_score - ambiguity_delta]
    names = {normalize_text(getattr(it, name_attr, "")) for _, it in close}
    if len(close) > 1 and len(names) > 1:
        return None, [it for _, it in close[:5]], "ambiguous"
    return best, [], None


# ---------------------------------------------------------------------------
# DeezerProvider
# ---------------------------------------------------------------------------


@dataclass
class PlaybackState:
    """État local de lecture (Deezer ne fournit pas d'API now-playing tierce)."""

    track: Track | None = None
    context: str = ""  # track|artist|album|playlist|flow|chart
    context_id: str = ""
    context_label: str = ""
    status: str = "idle"  # idle|playing|paused|unknown
    via: str = ""
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "track": self.track.to_dict() if self.track else None,
            "context": self.context,
            "context_id": self.context_id,
            "context_label": self.context_label,
            "status": self.status,
            "via": self.via,
            "updated_at": self.updated_at,
            "source": "local",
            "note": (
                "État local maintenu par Jarvis. Deezer ne fournit plus d'API "
                "publique de lecture en temps réel pour les applications tierces."
            ),
        }


class DeezerProvider:
    """Implémentation Deezer du contrat ``MusicProvider``."""

    name = "deezer"

    def __init__(
        self,
        *,
        access_token: str | None = None,
        client: DeezerHTTPClient | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        token_path: Path | None = None,
        media_key_sender: Callable[[int], bool] | None = None,
        content_opener: Callable[..., dict[str, Any]] | None = None,
        running_checker: Callable[[], bool] | None = None,
    ) -> None:
        self._token_path = token_path
        self._lock = threading.RLock()
        self._state = PlaybackState()
        self._personal_playlists_cache: list[Playlist] | None = None
        self._personal_playlists_ts = 0.0
        self._media_key_sender = media_key_sender or _send_media_key
        self._content_opener = content_opener or open_deezer_content
        self._running_checker = running_checker or is_deezer_running

        token = resolve_access_token(explicit=access_token, store_path=token_path)
        self.client = client or DeezerHTTPClient(access_token=token, timeout=timeout)
        if token and not self.client.access_token:
            self.client.access_token = token

    # -- disponibilité ------------------------------------------------------

    def is_available(self) -> bool:
        try:
            # Ping léger : chart 1 piste.
            self.client.get_chart_tracks(limit=1)
            return True
        except DeezerAPIError:
            return False
        except Exception:
            return False

    # -- recherche ----------------------------------------------------------

    def search(self, query: str, *, limit: int = 8) -> SearchResults:
        q = (query or "").strip()
        results = SearchResults(query=q)
        if not q:
            return results
        try:
            results.tracks = self.search_tracks(q, limit=limit)
            results.artists = self.search_artists(q, limit=max(3, limit // 2))
            results.albums = self.search_albums(q, limit=max(3, limit // 2))
            results.playlists = self.search_playlists(q, limit=max(3, limit // 2))
        except DeezerAPIError:
            raise
        return results

    def search_tracks(self, query: str, *, limit: int = 8) -> list[Track]:
        raw = self.client.search(query, limit=limit)
        # /search renvoie surtout des tracks ; filtrer au cas où.
        tracks = []
        for item in raw:
            if "title" in item and ("artist" in item or item.get("type") in (None, "track")):
                if item.get("type") and item.get("type") not in ("track",):
                    continue
                tracks.append(track_from_deezer(item))
        if not tracks:
            # Endpoint dédié
            raw = self.client.search(query, index_type="track", limit=limit)
            tracks = [track_from_deezer(item) for item in raw]
        return tracks

    def search_artists(self, query: str, *, limit: int = 5) -> list[Artist]:
        raw = self.client.search(query, index_type="artist", limit=limit)
        return [artist_from_deezer(item) for item in raw]

    def search_albums(self, query: str, *, limit: int = 5) -> list[Album]:
        raw = self.client.search(query, index_type="album", limit=limit)
        return [album_from_deezer(item) for item in raw]

    def search_playlists(self, query: str, *, limit: int = 5) -> list[Playlist]:
        raw = self.client.search(query, index_type="playlist", limit=limit)
        return [playlist_from_deezer(item) for item in raw]

    # -- lecture ------------------------------------------------------------

    def _remember(
        self,
        *,
        track: Track | None,
        context: str,
        context_id: str = "",
        context_label: str = "",
        status: str = "playing",
        via: str = "",
    ) -> None:
        with self._lock:
            self._state = PlaybackState(
                track=track,
                context=context,
                context_id=str(context_id or ""),
                context_label=context_label or "",
                status=status,
                via=via,
                updated_at=time.time(),
            )

    def play_track(self, track: Track | str, **kwargs: Any) -> dict[str, Any]:
        try:
            resolved = self._resolve_track(track, artist=kwargs.get("artist"))
        except DeezerAPIError as exc:
            return self._api_error(exc)
        if isinstance(resolved, dict) and not resolved.get("success", True):
            return resolved
        assert isinstance(resolved, Track)
        opened = self._open("track", resolved.id)
        if not opened.get("success"):
            return opened
        self._remember(
            track=resolved,
            context="track",
            context_id=resolved.id,
            context_label=resolved.label(),
            via=opened.get("via", ""),
        )
        return _ok(
            action="play_track",
            track=resolved.to_dict(),
            message=f"Je lance {resolved.label()}.",
            **{k: opened[k] for k in ("via", "url", "uri") if k in opened},
        )

    def play_artist(self, artist: Artist | str, **kwargs: Any) -> dict[str, Any]:
        try:
            resolved = self._resolve_artist(artist)
        except DeezerAPIError as exc:
            return self._api_error(exc)
        if isinstance(resolved, dict) and not resolved.get("success", True):
            return resolved
        assert isinstance(resolved, Artist)

        # Préférer la radio artiste / top tracks via deep-link artiste.
        opened = self._open("artist", resolved.id)
        if not opened.get("success"):
            return opened

        top_track: Track | None = None
        try:
            tops = self.client.get_artist_top(resolved.id, limit=1)
            if tops:
                top_track = track_from_deezer(tops[0])
        except DeezerAPIError:
            top_track = None

        self._remember(
            track=top_track,
            context="artist",
            context_id=resolved.id,
            context_label=resolved.name,
            via=opened.get("via", ""),
        )
        return _ok(
            action="play_artist",
            artist=resolved.to_dict(),
            track=top_track.to_dict() if top_track else None,
            message=f"Je lance {resolved.name}.",
            **{k: opened[k] for k in ("via", "url", "uri") if k in opened},
        )

    def play_album(self, album: Album | str, **kwargs: Any) -> dict[str, Any]:
        try:
            resolved = self._resolve_album(album, artist=kwargs.get("artist"))
        except DeezerAPIError as exc:
            return self._api_error(exc)
        if isinstance(resolved, dict) and not resolved.get("success", True):
            return resolved
        assert isinstance(resolved, Album)
        opened = self._open("album", resolved.id)
        if not opened.get("success"):
            return opened
        self._remember(
            track=None,
            context="album",
            context_id=resolved.id,
            context_label=resolved.label(),
            via=opened.get("via", ""),
        )
        return _ok(
            action="play_album",
            album=resolved.to_dict(),
            message=f"Je lance l'album {resolved.label()}.",
            **{k: opened[k] for k in ("via", "url", "uri") if k in opened},
        )

    def play_playlist(self, playlist: Playlist | str, **kwargs: Any) -> dict[str, Any]:
        personal = bool(kwargs.get("personal", False))
        try:
            resolved = self._resolve_playlist(playlist, personal=personal)
        except DeezerAPIError as exc:
            return self._api_error(exc)
        if isinstance(resolved, dict) and not resolved.get("success", True):
            return resolved
        assert isinstance(resolved, Playlist)
        opened = self._open("playlist", resolved.id)
        if not opened.get("success"):
            return opened
        self._remember(
            track=None,
            context="playlist",
            context_id=resolved.id,
            context_label=resolved.title,
            via=opened.get("via", ""),
        )
        kind = "ta playlist" if resolved.personal else "la playlist"
        return _ok(
            action="play_playlist",
            playlist=resolved.to_dict(),
            message=f"Je lance {kind} {resolved.title}.",
            **{k: opened[k] for k in ("via", "url", "uri") if k in opened},
        )

    def play_flow(self) -> dict[str, Any]:
        """Lance le Flow Deezer de l'utilisateur (nécessite auth) ou le chart."""
        if self.client.access_token:
            # Deep-link flow personnel
            me = None
            try:
                me = self.client.get_me()
            except DeezerAPIError as exc:
                return self._api_error(exc)
            user_id = str((me or {}).get("id") or "")
            if user_id:
                # Deezer ne fournit plus d'endpoint de lecture Flow pour les
                # apps tierces : on ouvre la page Flow / profil via deep-link.
                try:
                    opened = open_deezer_content(
                        "page",
                        "flow",
                        autoplay=True,
                        prefer_app=True,
                    )
                except Exception:
                    opened = self._open("profile", user_id)
                self._remember(
                    track=None,
                    context="flow",
                    context_id=user_id,
                    context_label="Flow",
                    via=opened.get("via", "") if isinstance(opened, dict) else "",
                )
                if isinstance(opened, dict) and opened.get("success"):
                    return _ok(action="play_flow", message="Je lance ton Flow Deezer.", **opened)
        # Sans auth : top chart
        return self.play_chart()

    def play_chart(self) -> dict[str, Any]:
        try:
            chart = self.client.get_chart_tracks(limit=1)
        except DeezerAPIError as exc:
            return self._api_error(exc)
        if not chart:
            # Ouvrir la page charts web.
            opened = self._content_opener.__call__  # noqa — keep type checkers calm
            result = open_deezer_content("chart", "0", autoplay=True)
            if result.get("success"):
                self._remember(track=None, context="chart", context_label="Tops Deezer", via=result.get("via", ""))
                return _ok(action="play_chart", message="Je lance les tops Deezer.", **result)
            return _err("Aucun titre dans le classement Deezer.")
        track = track_from_deezer(chart[0])
        # Ouvrir le chart plutôt qu'un seul titre pour « mets de la musique ».
        result = open_deezer_content("chart", "0/tracks", autoplay=True)
        if not result.get("success"):
            # Fallback : jouer le 1er titre.
            return self.play_track(track)
        self._remember(
            track=track,
            context="chart",
            context_id="0",
            context_label="Tops Deezer",
            via=result.get("via", ""),
        )
        return _ok(
            action="play_chart",
            track=track.to_dict(),
            message="Je lance de la musique sur Deezer.",
            **{k: result[k] for k in ("via", "url", "uri") if k in result},
        )

    def _open(self, resource: str, resource_id: str) -> dict[str, Any]:
        try:
            return self._content_opener(resource, str(resource_id), autoplay=True, prefer_app=True)
        except Exception as exc:
            return _err(f"Ouverture Deezer impossible : {exc}")

    # -- contrôles ----------------------------------------------------------

    def pause(self) -> dict[str, Any]:
        return self._media_action(VK_MEDIA_PLAY_PAUSE, action="pause", status="paused", message="Lecture mise en pause.")

    def resume(self) -> dict[str, Any]:
        return self._media_action(VK_MEDIA_PLAY_PAUSE, action="resume", status="playing", message="C'est reparti.")

    def next(self) -> dict[str, Any]:
        return self._media_action(VK_MEDIA_NEXT, action="next", message="Je passe au morceau suivant.")

    def previous(self) -> dict[str, Any]:
        return self._media_action(VK_MEDIA_PREV, action="previous", message="Je reviens au morceau précédent.")

    def stop(self) -> dict[str, Any]:
        return self._media_action(VK_MEDIA_STOP, action="stop", status="idle", message="Lecture arrêtée.")

    def _media_action(
        self,
        vk: int,
        *,
        action: str,
        message: str,
        status: str | None = None,
    ) -> dict[str, Any]:
        if os.name != "nt" and self._media_key_sender is _send_media_key:
            # Hors Windows, sans sender custom : limitation honnête.
            return _err(
                "Les contrôles de lecture Deezer (pause/suivant/précédent) "
                "utilisent les touches multimédia Windows. "
                "Cette fonction n'est pas disponible sur cette plateforme.",
                action=action,
                available=False,
            )
        sent = False
        try:
            sent = bool(self._media_key_sender(vk))
        except Exception as exc:
            return _err(f"Contrôle média refusé : {exc}", action=action)
        if not sent:
            return _err(
                "Impossible d'envoyer la commande média. "
                "Vérifie que Deezer est ouvert et au premier plan.",
                action=action,
            )
        with self._lock:
            if status:
                self._state.status = status
            self._state.updated_at = time.time()
            snapshot = self._state.to_dict()
        return _ok(action=action, message=message, state=snapshot)

    # -- état ---------------------------------------------------------------

    def get_current_track(self) -> dict[str, Any]:
        with self._lock:
            state = self._state
            if state.track is not None:
                label = state.track.label()
                status = state.status
                msg = {
                    "playing": f"Tu écoutes {label}.",
                    "paused": f"En pause : {label}.",
                    "idle": f"Dernier morceau lancé : {label}.",
                }.get(status, f"Dernier morceau connu : {label}.")
                return _ok(
                    available=True,
                    track=state.track.to_dict(),
                    status=status,
                    context=state.context,
                    context_label=state.context_label,
                    message=msg,
                    source="local",
                    limitation=(
                        "Deezer ne fournit pas d'API publique now-playing pour les "
                        "applications tierces. Information basée sur la dernière "
                        "lecture lancée par Jarvis."
                    ),
                )
            if state.context and state.context_label:
                return _ok(
                    available=True,
                    track=None,
                    status=state.status,
                    context=state.context,
                    context_label=state.context_label,
                    message=f"Lecture en cours : {state.context_label}.",
                    source="local",
                    limitation=(
                        "Titre exact inconnu — Deezer ne expose pas l'état de "
                        "lecture en temps réel aux applications tierces."
                    ),
                )
        return _ok(
            available=False,
            track=None,
            message=(
                "Je ne peux pas récupérer le morceau en cours. "
                "Deezer ne fournit pas cette information aux applications tierces, "
                "et aucune lecture n'a encore été lancée via Jarvis."
            ),
            source="none",
        )

    def get_playback_state(self) -> dict[str, Any]:
        with self._lock:
            return _ok(state=self._state.to_dict())

    # -- playlists ----------------------------------------------------------

    def get_playlists(self, *, personal: bool = True, limit: int = 30) -> dict[str, Any]:
        if personal:
            if not self.client.access_token:
                return _err(
                    "Les playlists personnelles nécessitent une authentification Deezer. "
                    "Configure DEEZER_ACCESS_TOKEN (voir documentation) ou utilise "
                    "une recherche de playlist publique.",
                    auth_required=True,
                    available=False,
                    limitation="oauth_required",
                )
            try:
                playlists = self._load_personal_playlists(limit=limit)
            except DeezerAPIError as exc:
                return self._api_error(exc, auth_sensitive=True)
            return _ok(
                playlists=[p.to_dict() for p in playlists],
                count=len(playlists),
                personal=True,
                message=f"{len(playlists)} playlist(s) personnelle(s) trouvée(s).",
            )
        # Playlists publiques : on ne liste pas un catalogue entier ; demander une query.
        return _err(
            "Précise un nom pour rechercher une playlist publique "
            "(ex. « cherche la playlist Chill »).",
            hint="music_search",
        )

    def _load_personal_playlists(self, *, limit: int = 50, force: bool = False) -> list[Playlist]:
        now = time.time()
        with self._lock:
            if (
                not force
                and self._personal_playlists_cache is not None
                and now - self._personal_playlists_ts < 120
            ):
                return list(self._personal_playlists_cache)[:limit]
        raw = self.client.get_my_playlists(limit=max(limit, 50))
        playlists = [playlist_from_deezer(item, personal=True) for item in raw]
        with self._lock:
            self._personal_playlists_cache = playlists
            self._personal_playlists_ts = now
        return list(playlists)[:limit]

    # -- auth ---------------------------------------------------------------

    def auth_status(self) -> dict[str, Any]:
        token = self.client.access_token
        if not token:
            return _ok(
                authenticated=False,
                message=(
                    "Non connecté à Deezer. Le catalogue public (recherche, lecture "
                    "via app/web) fonctionne sans compte. Les playlists personnelles "
                    "nécessitent DEEZER_ACCESS_TOKEN."
                ),
                oauth_available=False,
                note=(
                    "Depuis 2025, Deezer a restreint la création de nouvelles "
                    "applications API pour les particuliers. Un token existant "
                    "peut encore fonctionner s'il est fourni."
                ),
            )
        try:
            me = self.client.get_me()
        except DeezerAPIError as exc:
            return _ok(
                authenticated=False,
                token_present=True,
                token_valid=False,
                error=str(exc.message),
                code=exc.code,
                message=(
                    "Un token Deezer est configuré mais refusé par l'API "
                    f"({exc.message}). Reconnexion nécessaire."
                ),
            )
        return _ok(
            authenticated=True,
            token_present=True,
            token_valid=True,
            user_id=str(me.get("id", "")),
            user_name=str(me.get("name") or me.get("firstname") or ""),
            message=f"Connecté à Deezer en tant que {me.get('name') or me.get('id')}.",
        )

    def connect_with_token(self, access_token: str, *, expires_at: float | None = None) -> dict[str, Any]:
        token = (access_token or "").strip()
        if not token:
            return _err("Token Deezer vide.")
        # Ne jamais logger le token.
        previous = self.client.access_token
        self.client.access_token = token
        try:
            me = self.client.get_me()
        except DeezerAPIError as exc:
            self.client.access_token = previous
            return self._api_error(exc, auth_sensitive=True)
        save_stored_token(
            {
                "access_token": token,
                "expires_at": expires_at,
                "user_id": me.get("id"),
                "user_name": me.get("name"),
            },
            self._token_path,
        )
        with self._lock:
            self._personal_playlists_cache = None
        return _ok(
            authenticated=True,
            user_id=str(me.get("id", "")),
            user_name=str(me.get("name") or ""),
            message=f"Compte Deezer connecté ({me.get('name') or me.get('id')}).",
        )

    def disconnect(self) -> dict[str, Any]:
        self.client.access_token = None
        clear_stored_token(self._token_path)
        with self._lock:
            self._personal_playlists_cache = None
        return _ok(authenticated=False, message="Déconnecté de Deezer.")

    # -- résolution d'entités -----------------------------------------------

    def _resolve_track(self, track: Track | str, *, artist: str | None = None) -> Track | dict[str, Any]:
        if isinstance(track, Track):
            return track
        text = str(track or "").strip()
        if not text:
            return _err("Aucun morceau précisé.")
        # ID numérique direct
        if text.isdigit():
            payload = self.client.get_track(text)
            if not payload or payload.get("error"):
                return _err(f"Morceau introuvable (id {text}).")
            return track_from_deezer(payload)

        # Patterns "titre de artiste" / "titre - artiste"
        title, parsed_artist = split_title_artist(text)
        artist_name = artist or parsed_artist
        query = text
        if artist_name and title:
            query = f"{title} {artist_name}"
        tracks = self.search_tracks(query, limit=10)
        if artist_name and not tracks:
            tracks = self.search_tracks(title or text, limit=10)

        best, alts, reason = pick_best_track(tracks, title=title or text, artist=artist_name)
        if best is not None:
            return best
        if reason == "ambiguous" and alts:
            return _err(
                _format_track_ambiguity(alts),
                ambiguous=True,
                candidates=[t.to_dict() for t in alts],
            )
        if alts:
            # low_score : on prend le premier si unique, sinon ambiguïté.
            if len(alts) == 1:
                return alts[0]
            return _err(
                _format_track_ambiguity(alts),
                ambiguous=True,
                candidates=[t.to_dict() for t in alts],
            )
        return _err("Je n'ai trouvé aucun morceau correspondant.", query=text)

    def _resolve_artist(self, artist: Artist | str) -> Artist | dict[str, Any]:
        if isinstance(artist, Artist):
            return artist
        text = str(artist or "").strip()
        if not text:
            return _err("Aucun artiste précisé.")
        if text.isdigit():
            payload = self.client.get_artist(text)
            if not payload or payload.get("error"):
                return _err(f"Artiste introuvable (id {text}).")
            return artist_from_deezer(payload)
        artists = self.search_artists(text, limit=8)
        best, alts, reason = pick_best_named(artists, text, name_attr="name")
        if best is not None:
            return best
        if alts:
            if reason == "ambiguous" or len(alts) > 1:
                return _err(
                    _format_named_ambiguity("artistes", alts, "name"),
                    ambiguous=True,
                    candidates=[a.to_dict() for a in alts],
                )
            return alts[0]
        return _err("Je n'ai trouvé aucun artiste correspondant.", query=text)

    def _resolve_album(self, album: Album | str, *, artist: str | None = None) -> Album | dict[str, Any]:
        if isinstance(album, Album):
            return album
        text = str(album or "").strip()
        if not text:
            return _err("Aucun album précisé.")
        if text.isdigit():
            payload = self.client.get_album(text)
            if not payload or payload.get("error"):
                return _err(f"Album introuvable (id {text}).")
            return album_from_deezer(payload)
        title, parsed_artist = split_title_artist(text)
        artist_name = artist or parsed_artist
        query = f"{title} {artist_name}".strip() if artist_name else text
        albums = self.search_albums(query, limit=8)
        # Filtrer par artiste si fourni
        if artist_name:
            filtered = [a for a in albums if similarity(artist_name, a.artist) >= 0.5]
            if filtered:
                albums = filtered
        best, alts, reason = pick_best_named(albums, title or text, name_attr="title")
        if best is not None:
            return best
        if alts:
            if reason == "ambiguous" or len(alts) > 1:
                return _err(
                    _format_named_ambiguity("albums", alts, "title", secondary="artist"),
                    ambiguous=True,
                    candidates=[a.to_dict() for a in alts],
                )
            return alts[0]
        return _err("Je n'ai trouvé aucun album correspondant.", query=text)

    def _resolve_playlist(
        self,
        playlist: Playlist | str,
        *,
        personal: bool = False,
    ) -> Playlist | dict[str, Any]:
        if isinstance(playlist, Playlist):
            return playlist
        text = str(playlist or "").strip()
        if not text:
            return _err("Aucune playlist précisée.")
        if text.isdigit():
            payload = self.client.get_playlist(text)
            if not payload or payload.get("error"):
                return _err(f"Playlist introuvable (id {text}).")
            return playlist_from_deezer(payload, personal=personal)

        candidates: list[Playlist] = []
        # Priorité aux playlists personnelles si demandées / si auth dispo.
        if personal or self.client.access_token:
            if self.client.access_token:
                try:
                    personal_list = self._load_personal_playlists(limit=50)
                    candidates.extend(personal_list)
                except DeezerAPIError as exc:
                    if personal:
                        return self._api_error(exc, auth_sensitive=True)
            elif personal:
                return _err(
                    "Les playlists personnelles nécessitent une authentification Deezer "
                    "(DEEZER_ACCESS_TOKEN). Cette fonction n'est pas disponible sans compte lié.",
                    auth_required=True,
                    available=False,
                )

        # Compléter avec recherche publique
        try:
            public = self.search_playlists(text, limit=8)
            # Éviter les doublons d'id
            seen = {p.id for p in candidates}
            for p in public:
                if p.id not in seen:
                    candidates.append(p)
        except DeezerAPIError as exc:
            if not candidates:
                return self._api_error(exc)

        # Si personal=True, restreindre aux personnelles d'abord.
        pool = [p for p in candidates if p.personal] if personal else candidates
        if personal and not pool:
            pool = candidates  # fallback public si aucune perso ne matche plus bas

        best, alts, reason = pick_best_named(pool or candidates, text, name_attr="title", min_score=0.5)
        if best is not None:
            return best
        if alts:
            if reason == "ambiguous" or len(alts) > 1:
                return _err(
                    _format_named_ambiguity("playlists", alts, "title"),
                    ambiguous=True,
                    candidates=[p.to_dict() for p in alts],
                )
            return alts[0]
        return _err("Je n'ai trouvé aucune playlist correspondant.", query=text)

    # -- erreurs API --------------------------------------------------------

    def _api_error(self, exc: DeezerAPIError, *, auth_sensitive: bool = False) -> dict[str, Any]:
        code = exc.code
        message = exc.message
        # Token expiré / invalide
        if code in {200, 300} or (auth_sensitive and code in {200, 4, 50}):
            return _err(
                f"Authentification Deezer refusée : {message}. "
                "Le token est peut-être expiré ou révoqué.",
                code=code,
                auth_required=True,
                token_expired=True,
            )
        if exc.status == 0:
            return _err(
                f"API Deezer indisponible ({message}).",
                network_error=True,
                code=code,
            )
        return _err(f"Erreur Deezer : {message}", code=code, status=exc.status)


# ---------------------------------------------------------------------------
# Parsing d'intentions textuelles « titre de artiste »
# ---------------------------------------------------------------------------

_SPLIT_PATTERNS = [
    re.compile(r"^(?P<title>.+?)\s+(?:de|d'|par|by|-|—|–)\s+(?P<artist>.+)$", re.IGNORECASE),
]


def split_title_artist(text: str) -> tuple[str, str | None]:
    """Extrait (titre, artiste) depuis une requête libre."""
    raw = (text or "").strip()
    if not raw:
        return "", None
    for pattern in _SPLIT_PATTERNS:
        match = pattern.match(raw)
        if match:
            title = match.group("title").strip(" \t-—–")
            artist = match.group("artist").strip(" \t-—–")
            if title and artist and len(artist) >= 2:
                return title, artist
    return raw, None


def _format_track_ambiguity(tracks: list[Track]) -> str:
    labels = [t.label() for t in tracks[:4]]
    joined = ", ".join(labels)
    return f"J'ai trouvé plusieurs morceaux : {joined}. Tu veux lequel ?"


def _format_named_ambiguity(
    kind: str,
    items: list[Any],
    attr: str,
    *,
    secondary: str | None = None,
) -> str:
    labels = []
    for item in items[:4]:
        primary = getattr(item, attr, "")
        if secondary:
            extra = getattr(item, secondary, "")
            labels.append(f"{primary} ({extra})" if extra else str(primary))
        else:
            labels.append(str(primary))
    joined = ", ".join(labels)
    return f"J'ai trouvé plusieurs {kind} : {joined}. Tu veux laquelle ?"
