"""Modes de protection de Jarvis (focus / jeu).

Ces modes ne sont pas de simples macros visuelles : ils modifient la politique
locale d'exécution des outils. Même si le modèle tente une action interdite, le
backend la refuse avant d'agir sur le PC.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import threading
import unicodedata
import urllib.parse

try:  # Informations/processus système enrichis, optionnel.
    import psutil
except Exception:  # pragma: no cover - dépend de l'environnement
    psutil = None


DEFAULT_DATA_DIR = os.environ.get(
    "JARVIS_DATA_DIR",
    os.path.join(os.path.expanduser("~"), ".jarvis"),
)
DEFAULT_MODES_PATH = os.path.join(DEFAULT_DATA_DIR, "mode.json")

MODE_NORMAL = "normal"
MODE_FOCUS = "focus"
MODE_GAME = "game"
MODES = {MODE_NORMAL, MODE_FOCUS, MODE_GAME}

_ISO = "%Y-%m-%dT%H:%M:%S"
_IS_WINDOWS = os.name == "nt"


# Outils qui doivent rester disponibles pour activer/désactiver un mode ou
# demander l'état courant, quel que soit le mode actif.
MODE_CONTROL_TOOLS = {
    "activate_focus_mode",
    "activate_game_mode",
    "disable_jarvis_mode",
    "get_jarvis_mode",
}

AUDIO_TOOLS = {
    "set_volume",
    "volume_up",
    "volume_down",
    "get_volume",
    "mute_audio",
    "unmute_audio",
    "toggle_mute",
}

# En jeu, Jarvis doit rester invisible et ne pas toucher à l'écran. On garde
# uniquement les commandes de volume et quelques lectures locales inoffensives.
GAME_ALLOWED_TOOLS = MODE_CONTROL_TOOLS | AUDIO_TOOLS | {
    "get_local_time",
    "get_local_date",
    "get_datetime",
    "get_battery_status",
}

# Pendant les révisions : pas de jeux, streaming, réseaux sociaux, achats,
# hasard ou commandes multimédia qui relancent une distraction.
FOCUS_ALWAYS_BLOCKED_TOOLS = {
    "search_youtube",
    "media_play_pause",
    "media_next",
    "media_previous",
    "media_stop",
    "random_number",
    "flip_coin",
    "roll_dice",
    "pick_random",
}

FOCUS_BLOCKED_APPS = {
    "discord",
    "spotify",
    "vlc",
    "lecteur windows media",
    "steam",
    "telegram",
    "whatsapp",
    "teams",
    "slack",
    "zoom",
    "obs",
}

FOCUS_BLOCKED_SITES = {
    # Vidéo/streaming/musique
    "youtube",
    "youtube music",
    "twitch",
    "netflix",
    "prime video",
    "disney plus",
    "dailymotion",
    "vimeo",
    "arte",
    "france tv",
    "molotov",
    "crunchyroll",
    "spotify",
    "deezer",
    "soundcloud",
    "apple music",
    "bandcamp",
    "radio france",
    # Réseaux et messageries
    "twitter",
    "x",
    "facebook",
    "instagram",
    "linkedin",
    "reddit",
    "tiktok",
    "pinterest",
    "mastodon",
    "bluesky",
    "discord",
    "whatsapp",
    "telegram",
    "twitch chat",
    # Achats/loisirs/jeux
    "amazon",
    "leboncoin",
    "cdiscount",
    "fnac",
    "aliexpress",
    "ebay",
    "booking",
    "airbnb",
    "steam",
    "epic games",
    "itch io",
    "imdb",
    "allocine",
    "senscritique",
    # Actualités très chronophages pendant une session de révision.
    "le monde",
    "le figaro",
    "liberation",
    "france info",
    "france 24",
    "bfm",
    "les echos",
    "l equipe",
    "bbc",
    "cnn",
    "reuters",
    "hacker news",
    "numerama",
    "clubic",
    "01net",
    "the verge",
    "ars technica",
    # Alias courants de SITE_ALIASES (sans importer src.tools pour éviter un cycle).
    "yt",
    "ytb",
    "you tube",
    "youtub",
    "insta",
    "fb",
    "prime",
    "disney",
    "actualites",
    "info",
    "les infos",
    "journal",
    "hn",
}

FOCUS_BLOCKED_DOMAINS = {
    "youtube.com",
    "youtu.be",
    "music.youtube.com",
    "twitch.tv",
    "netflix.com",
    "primevideo.com",
    "disneyplus.com",
    "dailymotion.com",
    "vimeo.com",
    "arte.tv",
    "france.tv",
    "molotov.tv",
    "crunchyroll.com",
    "spotify.com",
    "open.spotify.com",
    "deezer.com",
    "soundcloud.com",
    "music.apple.com",
    "bandcamp.com",
    "radiofrance.fr",
    "x.com",
    "twitter.com",
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "reddit.com",
    "tiktok.com",
    "pinterest.fr",
    "pinterest.com",
    "mastodon.social",
    "bsky.app",
    "discord.com",
    "web.whatsapp.com",
    "web.telegram.org",
    "amazon.fr",
    "amazon.com",
    "leboncoin.fr",
    "cdiscount.com",
    "fnac.com",
    "aliexpress.com",
    "ebay.fr",
    "booking.com",
    "airbnb.fr",
    "steampowered.com",
    "store.steampowered.com",
    "epicgames.com",
    "store.epicgames.com",
    "itch.io",
    "imdb.com",
    "allocine.fr",
    "senscritique.com",
    "lemonde.fr",
    "lefigaro.fr",
    "liberation.fr",
    "francetvinfo.fr",
    "france24.com",
    "bfmtv.com",
    "lesechos.fr",
    "lequipe.fr",
    "bbc.com",
    "cnn.com",
    "reuters.com",
    "news.ycombinator.com",
    "numerama.com",
    "clubic.com",
    "01net.com",
    "theverge.com",
    "arstechnica.com",
}

FOCUS_DISTRACTION_KEYWORDS = {
    "youtube",
    "twitch",
    "netflix",
    "tiktok",
    "instagram",
    "reddit",
    "discord",
    "steam",
    "fortnite",
    "minecraft",
    "roblox",
    "jeu",
    "jeux",
    "gaming",
    "gameplay",
    "film",
    "serie",
    "série",
    "anime",
    "meme",
    "mème",
    "memes",
    "mèmes",
    "shopping",
    "amazon",
    "insta",
    "reels",
    "shorts",
}

FOCUS_BLOCKED_PROCESS_IMAGES = {
    "Discord.exe",
    "Spotify.exe",
    "vlc.exe",
    "wmplayer.exe",
    "steam.exe",
    "Telegram.exe",
    "WhatsApp.exe",
    "Teams.exe",
    "ms-teams.exe",
    "Slack.exe",
    "Zoom.exe",
    "EpicGamesLauncher.exe",
    "Battle.net.exe",
    "RiotClientServices.exe",
    "UbisoftConnect.exe",
    "EADesktop.exe",
    "XboxPcApp.exe",
}

GAME_BACKGROUND_PROCESS_IMAGES = {
    # Navigateurs
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "brave.exe",
    "opera.exe",
    # Messagerie / réunions
    "Discord.exe",
    "Teams.exe",
    "ms-teams.exe",
    "Slack.exe",
    "Zoom.exe",
    "Telegram.exe",
    "WhatsApp.exe",
    # Multimédia
    "Spotify.exe",
    "vlc.exe",
    "wmplayer.exe",
    # Bureautique/dev souvent lourds ; on ne touche pas aux launchers de jeux.
    "WINWORD.EXE",
    "EXCEL.EXE",
    "POWERPNT.EXE",
    "OUTLOOK.EXE",
    "ONENOTE.EXE",
    "Code.exe",
    "pycharm64.exe",
}


_MODE_LABELS = {
    MODE_NORMAL: "normal",
    MODE_FOCUS: "focus",
    MODE_GAME: "jeu",
}


def _ok(**payload) -> dict:
    result = {"success": True}
    result.update(payload)
    return result


def _err(message: str, **payload) -> dict:
    result = {"success": False, "error": message}
    result.update(payload)
    return result


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def normalize(value) -> str:
    text = _strip_accents(str(value or "")).lower().strip()
    text = text.replace("'", " ").replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", text)


def _host_matches(host: str, domains: set[str]) -> bool:
    host = (host or "").lower().strip()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return False
    for domain in domains:
        domain = domain.lower()
        if host == domain or host.endswith("." + domain):
            return True
    return False


def _parse_duration_minutes(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        minutes = int(float(value))
    except (TypeError, ValueError):
        return None
    return max(1, min(24 * 60, minutes))


def _safe_load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


class JarvisModeManager:
    """État et garde-fous locaux des modes focus/jeu."""

    def __init__(self, path: str | os.PathLike | None = None, enabled: bool = True) -> None:
        self.path = str(path or DEFAULT_MODES_PATH)
        self.enabled = bool(enabled)
        self._lock = threading.RLock()
        self._state = self._load()

    # ------------------------------------------------------------------
    # Persistance
    # ------------------------------------------------------------------
    def _empty(self) -> dict:
        return {
            "version": 1,
            "active_mode": MODE_NORMAL,
            "activated_at": None,
            "expires_at": None,
        }

    def _load(self) -> dict:
        payload = _safe_load_json(self.path)
        mode = str(payload.get("active_mode") or MODE_NORMAL).lower()
        if mode not in MODES:
            mode = MODE_NORMAL
        state = self._empty()
        state.update(payload)
        state["active_mode"] = mode
        return self._normalize_expiration(state)

    def _save(self) -> None:
        if not self.enabled:
            return
        try:
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            temp = f"{self.path}.tmp"
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump(self._state, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temp, self.path)
        except Exception:
            # Les modes restent valables en mémoire ; on ne doit pas faire
            # échouer Jarvis pour une erreur d'écriture locale.
            pass

    def _normalize_expiration(self, state: dict) -> dict:
        expires = state.get("expires_at")
        if state.get("active_mode") == MODE_NORMAL or not expires:
            if state.get("active_mode") == MODE_NORMAL:
                state["expires_at"] = None
            return state
        try:
            moment = dt.datetime.strptime(str(expires), _ISO)
        except Exception:
            state["expires_at"] = None
            return state
        if moment <= dt.datetime.now():
            state["active_mode"] = MODE_NORMAL
            state["activated_at"] = None
            state["expires_at"] = None
        return state

    def _refresh_locked(self) -> None:
        previous = dict(self._state)
        self._state = self._normalize_expiration(self._state)
        if self._state != previous:
            self._save()

    # ------------------------------------------------------------------
    # État public
    # ------------------------------------------------------------------
    def current_mode(self) -> str:
        if not self.enabled:
            return MODE_NORMAL
        with self._lock:
            self._refresh_locked()
            return str(self._state.get("active_mode") or MODE_NORMAL)

    def status(self) -> dict:
        with self._lock:
            self._refresh_locked()
            mode = str(self._state.get("active_mode") or MODE_NORMAL)
            return _ok(
                mode=mode,
                libelle=_MODE_LABELS.get(mode, mode),
                actif=mode != MODE_NORMAL,
                active_mode=mode,
                activated_at=self._state.get("activated_at"),
                expires_at=self._state.get("expires_at"),
                notifications_silencieuses=self.should_suppress_notifications(),
                affichage_bloque=self.should_suppress_visuals(),
            )

    def _set_mode(self, mode: str, duration_minutes=None) -> dict:
        if not self.enabled:
            return _err("Modes Jarvis désactivés.")
        mode = str(mode or MODE_NORMAL).lower()
        if mode not in MODES:
            return _err(f"Mode inconnu : {mode}.")
        minutes = _parse_duration_minutes(duration_minutes)
        now = dt.datetime.now()
        expires_at = None
        if minutes is not None:
            expires_at = (now + dt.timedelta(minutes=minutes)).strftime(_ISO)
        with self._lock:
            self._state.update(
                active_mode=mode,
                activated_at=now.strftime(_ISO) if mode != MODE_NORMAL else None,
                expires_at=expires_at if mode != MODE_NORMAL else None,
            )
            self._save()
        return _ok(
            mode=mode,
            libelle=_MODE_LABELS.get(mode, mode),
            actif=mode != MODE_NORMAL,
            expires_at=expires_at,
        )

    def activate_focus_mode(self, duration_minutes=None, close_distractions=True) -> dict:
        """Active le mode révision et ferme les distractions connues."""
        result = self._set_mode(MODE_FOCUS, duration_minutes=duration_minutes)
        if not result.get("success"):
            return result
        cleanup = None
        if bool(close_distractions):
            cleanup = self._close_processes(FOCUS_BLOCKED_PROCESS_IMAGES)
        result.update(
            description=(
                "Mode focus actif : jeux, streaming, réseaux sociaux, achats, "
                "hasard et discussions hors révision sont refusés. Les outils "
                "utiles aux révisions restent disponibles."
            ),
            sites_bloques=sorted(FOCUS_BLOCKED_SITES),
            applications_bloquees=sorted(FOCUS_BLOCKED_APPS),
            nettoyage=cleanup,
        )
        return result

    def activate_game_mode(self, duration_minutes=None, close_background=True) -> dict:
        """Active le mode jeu : performances + zéro interaction visuelle."""
        result = self._set_mode(MODE_GAME, duration_minutes=duration_minutes)
        if not result.get("success"):
            return result
        cleanup = None
        if bool(close_background):
            cleanup = self._close_processes(GAME_BACKGROUND_PROCESS_IMAGES)
        protocol = self._cancel_active_protocol()
        priority = self._set_jarvis_low_priority()
        result.update(
            description=(
                "Mode jeu actif : Jarvis reste discret, n'ouvre rien, ne ferme "
                "rien, ne lit pas l'écran, n'affiche pas de notification/overlay "
                "et n'interagit pas avec le jeu. Les commandes de volume et la "
                "désactivation du mode restent autorisées."
            ),
            affichage_bloque=True,
            interactions_ecran_bloquees=True,
            outils_autorises=sorted(GAME_ALLOWED_TOOLS),
            nettoyage=cleanup,
            protocole=protocol,
            priorite_jarvis=priority,
        )
        return result

    def disable_mode(self) -> dict:
        previous = self.current_mode()
        result = self._set_mode(MODE_NORMAL)
        if result.get("success"):
            result.update(
                mode_precedent=previous,
                description="Mode spécial désactivé : Jarvis revient au fonctionnement normal.",
                priorite_jarvis=self._restore_jarvis_priority(),
            )
        return result

    # ------------------------------------------------------------------
    # Politique d'outils
    # ------------------------------------------------------------------
    def block_for_tool(self, tool_name: str, args: dict | None = None) -> dict | None:
        """Renvoie un résultat d'erreur si l'outil est interdit par le mode.

        ``None`` signifie que l'appel peut continuer.
        """
        mode = self.current_mode()
        if mode == MODE_NORMAL:
            return None
        args = args or {}
        tool_name = str(tool_name or "")

        if tool_name in MODE_CONTROL_TOOLS:
            return None

        if mode == MODE_GAME:
            if tool_name in GAME_ALLOWED_TOOLS:
                return None
            return _err(
                "Mode jeu actif : action bloquée pour éviter toute interaction "
                "avec l'écran/le jeu ou tout affichage. Le volume reste autorisé ; "
                "désactive le mode jeu pour le reste.",
                blocked_by_mode=True,
                mode=MODE_GAME,
                outils_autorises=sorted(GAME_ALLOWED_TOOLS),
            )

        if mode == MODE_FOCUS:
            return self._focus_block_for_tool(tool_name, args)

        return None

    def _focus_block_for_tool(self, tool_name: str, args: dict) -> dict | None:
        if tool_name in FOCUS_ALWAYS_BLOCKED_TOOLS:
            return self._focus_error("outil de loisir/hasard")

        if tool_name == "open_application":
            requested = normalize(args.get("application"))
            if requested in FOCUS_BLOCKED_APPS:
                return self._focus_error(f"application bloquée : {requested}")
            # Correspondance tolérante : « ouvre Discord s'il te plaît ».
            for app in FOCUS_BLOCKED_APPS:
                if app and re.search(rf"(?<![a-z0-9]){re.escape(app)}(?![a-z0-9])", requested):
                    return self._focus_error(f"application bloquée : {app}")

        if tool_name == "open_website":
            requested = normalize(args.get("site"))
            if requested in FOCUS_BLOCKED_SITES:
                return self._focus_error(f"site bloqué : {requested}")
            for site in FOCUS_BLOCKED_SITES:
                if site and re.search(rf"(?<![a-z0-9]){re.escape(site)}(?![a-z0-9])", requested):
                    return self._focus_error(f"site bloqué : {site}")

        if tool_name == "open_url":
            raw = str(args.get("url") or "").strip()
            if raw and "://" not in raw:
                raw = "https://" + raw
            host = urllib.parse.urlparse(raw).hostname or ""
            if _host_matches(host, FOCUS_BLOCKED_DOMAINS):
                return self._focus_error(f"domaine bloqué : {host}")

        if tool_name == "web_search":
            engine = normalize(args.get("engine") or "google")
            query = normalize(args.get("query"))
            if engine in {"youtube", "images", "amazon"}:
                return self._focus_error(f"moteur de distraction : {engine}")
            if any(
                re.search(rf"(?<![a-z0-9]){re.escape(normalize(keyword))}(?![a-z0-9])", query)
                for keyword in FOCUS_DISTRACTION_KEYWORDS
            ):
                return self._focus_error("recherche orientée distraction")

        return None

    def _focus_error(self, reason: str) -> dict:
        return _err(
            "Mode focus actif : cette action ressemble à une distraction. "
            "Je la bloque pour protéger la session de révision.",
            blocked_by_mode=True,
            mode=MODE_FOCUS,
            raison=reason,
        )

    # ------------------------------------------------------------------
    # Instructions prompt / affichage
    # ------------------------------------------------------------------
    def system_instruction(self) -> str:
        mode = self.current_mode()
        if mode == MODE_FOCUS:
            return (
                "MODE FOCUS ACTIF. L'utilisateur est en révision : refuse les "
                "discussions hors travail, jeux, réseaux sociaux, streaming, "
                "achats, hasard et autres distractions. Redirige brièvement vers "
                "la tâche de révision. Tu peux aider à apprendre, expliquer, "
                "quizzer, résumer, chronométrer une session ou ouvrir des ressources "
                "éducatives autorisées. Pour sortir du mode, utilise disable_jarvis_mode "
                "seulement si l'utilisateur le demande clairement."
            )
        if mode == MODE_GAME:
            return (
                "MODE JEU ACTIF. Priorité absolue au jeu : réponses très courtes, "
                "aucune action visuelle, aucune ouverture/fermeture de fenêtre, "
                "aucune capture ou interaction écran, aucune notification. N'utilise "
                "que les outils de volume, get_jarvis_mode ou disable_jarvis_mode "
                "si demandé explicitement."
            )
        return ""

    def should_suppress_notifications(self) -> bool:
        return self.current_mode() == MODE_GAME

    def should_suppress_visuals(self) -> bool:
        return self.current_mode() == MODE_GAME

    # ------------------------------------------------------------------
    # Nettoyage / priorité, best effort et non destructif.
    # ------------------------------------------------------------------
    def _close_processes(self, images: set[str]) -> dict:
        requested = sorted(images, key=str.lower)
        if not requested:
            return _ok(tentes=[], fermes=[], ignores=[])
        if not _IS_WINDOWS:
            return _ok(
                tente=False,
                fermes=[],
                ignores=requested,
                raison="Fermeture automatique des processus disponible uniquement sous Windows.",
            )

        closed: list[str] = []
        ignored: list[str] = []
        errors: list[dict] = []
        for image in requested:
            try:
                result = subprocess.run(
                    ["taskkill", "/IM", image, "/F"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except Exception as exc:  # pragma: no cover - dépend de Windows
                errors.append({"processus": image, "erreur": str(exc)})
                continue
            output = (result.stdout or result.stderr or "").strip()
            if result.returncode == 0:
                closed.append(image)
            elif "not found" in output.lower() or "introuvable" in output.lower():
                ignored.append(image)
            else:
                errors.append({"processus": image, "erreur": output or "taskkill a échoué"})
        return {
            "success": not errors,
            "tente": True,
            "fermes": closed,
            "ignores": ignored,
            "erreurs": errors,
        }

    def _cancel_active_protocol(self) -> dict:
        try:
            from . import protocols

            cancelled = bool(protocols.cancel_active())
            return _ok(annule=cancelled)
        except Exception as exc:
            return _ok(annule=False, raison=str(exc))

    def _set_jarvis_low_priority(self) -> dict:
        if psutil is None:
            return _ok(applique=False, raison="psutil indisponible")
        try:
            process = psutil.Process(os.getpid())
            if _IS_WINDOWS and hasattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS"):
                process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
                return _ok(applique=True, priorite="below_normal")
            # Sur POSIX, augmenter nice réduit la priorité. On évite de toucher
            # aux priorités si l'environnement refuse l'opération.
            if not _IS_WINDOWS:
                return _ok(applique=False, raison="priorité conservée hors Windows")
        except Exception as exc:  # pragma: no cover - dépend OS/droits
            return _ok(applique=False, raison=str(exc))
        return _ok(applique=False, raison="priorité non modifiée")

    def _restore_jarvis_priority(self) -> dict:
        if psutil is None:
            return _ok(applique=False, raison="psutil indisponible")
        try:
            process = psutil.Process(os.getpid())
            if _IS_WINDOWS and hasattr(psutil, "NORMAL_PRIORITY_CLASS"):
                process.nice(psutil.NORMAL_PRIORITY_CLASS)
                return _ok(applique=True, priorite="normal")
        except Exception as exc:  # pragma: no cover
            return _ok(applique=False, raison=str(exc))
        return _ok(applique=False, raison="priorité non modifiée")


_DEFAULT_MANAGER: JarvisModeManager | None = None
_DEFAULT_LOCK = threading.Lock()


def get_default_mode_manager() -> JarvisModeManager:
    global _DEFAULT_MANAGER
    with _DEFAULT_LOCK:
        if _DEFAULT_MANAGER is None:
            enabled = os.environ.get("JARVIS_MODES_ENABLED", "1").strip().lower() not in {
                "0",
                "false",
                "no",
                "off",
            }
            path = os.environ.get("JARVIS_MODES_PATH", DEFAULT_MODES_PATH)
            _DEFAULT_MANAGER = JarvisModeManager(path, enabled=enabled)
        return _DEFAULT_MANAGER


def set_default_mode_manager(manager: JarvisModeManager | None) -> None:
    global _DEFAULT_MANAGER
    with _DEFAULT_LOCK:
        _DEFAULT_MANAGER = manager
