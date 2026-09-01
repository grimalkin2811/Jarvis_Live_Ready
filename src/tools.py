"""Boîte à outils de Jarvis.

Ce module regroupe **toutes** les actions que Gemini Live peut déclencher sur le
PC de l'utilisateur.

Principes :

* **Liste blanche** : applications, sites et dossiers autorisés sont déclarés
  explicitement (``APPS``, ``SITES``, ``FOLDERS``). Rien d'autre n'est ouvert.
* **Aucun crash** : chaque outil renvoie toujours un dictionnaire
  ``{"success": bool, ...}``. Les erreurs sont capturées et renvoyées à Gemini
  qui peut ainsi l'annoncer honnêtement à l'utilisateur.
* **Dépendances optionnelles** : ``pycaw`` (volume) est importé de façon
  défensive ; le reste n'utilise que la bibliothèque standard + PowerShell sur
  Windows. Le module s'importe donc sans erreur sur Linux/macOS (utile pour les
  tests), les outils spécifiques Windows renvoyant simplement une erreur.
"""

from __future__ import annotations

import ast
import datetime as dt
import functools
import json
import math
import operator
import os
import platform
import random
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import webbrowser

from .activity import get_default_activity_log, log_action
from .backup import create_backup as _create_backup
from .backup import list_backups as _list_backups
from .memory import get_default_memory_manager
from .routines import get_default_routine_manager
from .scheduler import get_default_scheduler
from .todo import get_default_todo_manager

# ---------------------------------------------------------------------------
# Dépendances optionnelles
# ---------------------------------------------------------------------------

try:  # Contrôle du volume Windows
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    from comtypes import CLSCTX_ALL
except Exception:  # pragma: no cover - dépend de la plateforme
    AudioUtilities = None
    IAudioEndpointVolume = None
    CLSCTX_ALL = None

try:  # Informations système enrichies
    import psutil
except Exception:  # pragma: no cover
    psutil = None


IS_WINDOWS = os.name == "nt"

# Dossier de données persistantes (notes, mémos...).
DATA_DIR = os.environ.get(
    "JARVIS_DATA_DIR",
    os.path.join(os.path.expanduser("~"), ".jarvis"),
)
NOTES_FILE = os.path.join(DATA_DIR, "notes.json")
#: Historique du presse-papiers (copier/coller multiples).
CLIPBOARD_HISTORY_FILE = os.path.join(DATA_DIR, "clipboard_history.json")
#: Nombre maximum d'entrées conservées dans l'historique du presse-papiers.
CLIPBOARD_HISTORY_MAX = 50
#: Extensions de fichiers lisibles à voix haute.
READABLE_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".csv", ".json", ".log", ".ini", ".cfg",
    ".yml", ".yaml", ".xml", ".html", ".htm", ".py", ".js", ".ts", ".css",
    ".bat", ".ps1", ".sql", ".rst", ".srt",
}



# ===========================================================================
# LISTES BLANCHES
# ===========================================================================

#: Applications autorisées. Clé = nom prononcé (fr/en), valeur = exécutable.
APPS = {
    # --- Système Windows ---------------------------------------------------
    "bloc-notes": "notepad.exe",
    "bloc notes": "notepad.exe",
    "notepad": "notepad.exe",
    "calculatrice": "calc.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "explorateur": "explorer.exe",
    "explorateur de fichiers": "explorer.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "paint": "mspaint.exe",
    "peinture": "mspaint.exe",
    "wordpad": "write.exe",
    "invite de commandes": "cmd.exe",
    "terminal": "wt.exe",
    "windows terminal": "wt.exe",
    "cmd": "cmd.exe",
    "powershell": "powershell.exe",
    "gestionnaire des taches": "taskmgr.exe",
    "gestionnaire de taches": "taskmgr.exe",
    "task manager": "taskmgr.exe",
    "panneau de configuration": "control.exe",
    "control panel": "control.exe",
    "parametres": "ms-settings:",
    "reglages": "ms-settings:",
    "settings": "ms-settings:",
    "outil capture": "snippingtool.exe",
    "capture d ecran": "snippingtool.exe",
    "snipping tool": "snippingtool.exe",
    "registre": "regedit.exe",
    "moniteur de ressources": "resmon.exe",
    "nettoyage de disque": "cleanmgr.exe",
    "table des caracteres": "charmap.exe",
    "magnetophone": "soundrecorder.exe",
    "clavier visuel": "osk.exe",
    "loupe": "magnify.exe",
    # --- Navigateurs -------------------------------------------------------
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "firefox": "firefox.exe",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    "brave": "brave.exe",
    "opera": "opera.exe",
    # --- Bureautique -------------------------------------------------------
    "word": "winword.exe",
    "excel": "excel.exe",
    "powerpoint": "powerpnt.exe",
    "outlook": "outlook.exe",
    "onenote": "onenote.exe",
    "acrobat": "acrobat.exe",
    "adobe reader": "acrord32.exe",
    # --- Développement -----------------------------------------------------
    "vscode": "code.exe",
    "visual studio code": "code.exe",
    "code": "code.exe",
    "notepad++": "notepad++.exe",
    "git bash": "git-bash.exe",
    "pycharm": "pycharm64.exe",
    # --- Multimédia & communication ---------------------------------------
    "spotify": "spotify.exe",
    "vlc": "vlc.exe",
    "lecteur windows media": "wmplayer.exe",
    "discord": "discord.exe",
    "teams": "teams.exe",
    "slack": "slack.exe",
    "zoom": "zoom.exe",
    "telegram": "telegram.exe",
    "whatsapp": "whatsapp.exe",
    "obs": "obs64.exe",
    "steam": "steam.exe",
}

#: Sites autorisés. Clé = nom prononcé, valeur = URL.
SITES = {
    # --- Moteurs de recherche ---------------------------------------------
    "google": "https://www.google.com",
    "bing": "https://www.bing.com",
    "duckduckgo": "https://duckduckgo.com",
    "qwant": "https://www.qwant.com",
    "ecosia": "https://www.ecosia.org",
    "yahoo": "https://fr.yahoo.com",
    "brave search": "https://search.brave.com",
    "startpage": "https://www.startpage.com",
    # --- Encyclopédies & savoir -------------------------------------------
    "wikipedia": "https://fr.wikipedia.org",
    "wikipedia anglais": "https://en.wikipedia.org",
    "wiktionnaire": "https://fr.wiktionary.org",
    "wikimedia": "https://commons.wikimedia.org",
    "larousse": "https://www.larousse.fr",
    "cnrtl": "https://www.cnrtl.fr",
    "arxiv": "https://arxiv.org",
    "google scholar": "https://scholar.google.com",
    "wolframalpha": "https://www.wolframalpha.com",
    "internet archive": "https://archive.org",
    "openstreetmap": "https://www.openstreetmap.org",
    # --- Vidéo & streaming --------------------------------------------------
    "youtube": "https://www.youtube.com",
    "youtube music": "https://music.youtube.com",
    "twitch": "https://www.twitch.tv",
    "netflix": "https://www.netflix.com",
    "prime video": "https://www.primevideo.com",
    "disney plus": "https://www.disneyplus.com",
    "dailymotion": "https://www.dailymotion.com",
    "vimeo": "https://vimeo.com",
    "arte": "https://www.arte.tv/fr",
    "france tv": "https://www.france.tv",
    "molotov": "https://www.molotov.tv",
    "crunchyroll": "https://www.crunchyroll.com",
    # --- Films & séries (quoi regarder ce soir ?) ---------------------------
    "justwatch": "https://www.justwatch.com/fr",
    "allocine": "https://www.allocine.fr",
    "imdb": "https://www.imdb.com",
    "senscritique": "https://www.senscritique.com",
    "letterboxd": "https://letterboxd.com",
    "themoviedb": "https://www.themoviedb.org",
    # --- Musique & podcasts -------------------------------------------------
    "spotify": "https://open.spotify.com",
    "deezer": "https://www.deezer.com",
    "soundcloud": "https://soundcloud.com",
    "apple music": "https://music.apple.com",
    "bandcamp": "https://bandcamp.com",
    "radio france": "https://www.radiofrance.fr",
    # --- Réseaux sociaux -----------------------------------------------------
    "twitter": "https://x.com",
    "x": "https://x.com",
    "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com",
    "linkedin": "https://www.linkedin.com",
    "reddit": "https://www.reddit.com",
    "tiktok": "https://www.tiktok.com",
    "pinterest": "https://www.pinterest.fr",
    "mastodon": "https://mastodon.social",
    "bluesky": "https://bsky.app",
    "discord": "https://discord.com/app",
    "whatsapp": "https://web.whatsapp.com",
    "telegram": "https://web.telegram.org",
    "twitch chat": "https://www.twitch.tv/directory",
    # --- Développement --------------------------------------------------------
    "github": "https://github.com",
    "gitlab": "https://gitlab.com",
    "stackoverflow": "https://stackoverflow.com",
    "stack overflow": "https://stackoverflow.com",
    "python": "https://docs.python.org/fr/3/",
    "documentation python": "https://docs.python.org/fr/3/",
    "pypi": "https://pypi.org",
    "mdn": "https://developer.mozilla.org/fr/",
    "npm": "https://www.npmjs.com",
    "docker hub": "https://hub.docker.com",
    "hugging face": "https://huggingface.co",
    "kaggle": "https://www.kaggle.com",
    "codepen": "https://codepen.io",
    "replit": "https://replit.com",
    "google ai studio": "https://aistudio.google.com",
    "openai": "https://platform.openai.com",
    "chatgpt": "https://chat.openai.com",
    "claude": "https://claude.ai",
    "gemini": "https://gemini.google.com",
    "perplexity": "https://www.perplexity.ai",
    # --- Google & productivité --------------------------------------------------
    "gmail": "https://mail.google.com",
    "google drive": "https://drive.google.com",
    "google docs": "https://docs.google.com",
    "google sheets": "https://sheets.google.com",
    "google slides": "https://slides.google.com",
    "google agenda": "https://calendar.google.com",
    "google calendar": "https://calendar.google.com",
    "google maps": "https://www.google.com/maps",
    "maps": "https://www.google.com/maps",
    "google traduction": "https://translate.google.com",
    "traduction": "https://translate.google.com",
    "deepl": "https://www.deepl.com/translator",
    "google photos": "https://photos.google.com",
    "google keep": "https://keep.google.com",
    "notion": "https://www.notion.so",
    "trello": "https://trello.com",
    "outlook": "https://outlook.live.com",
    "onedrive": "https://onedrive.live.com",
    "dropbox": "https://www.dropbox.com",
    "canva": "https://www.canva.com",
    "figma": "https://www.figma.com",
    "todoist": "https://todoist.com",
    # --- Actualités ------------------------------------------------------------
    "le monde": "https://www.lemonde.fr",
    "le figaro": "https://www.lefigaro.fr",
    "liberation": "https://www.liberation.fr",
    "france info": "https://www.francetvinfo.fr",
    "france 24": "https://www.france24.com/fr/",
    "bfm": "https://www.bfmtv.com",
    "les echos": "https://www.lesechos.fr",
    "l equipe": "https://www.lequipe.fr",
    "bbc": "https://www.bbc.com/news",
    "cnn": "https://edition.cnn.com",
    "reuters": "https://www.reuters.com",
    "hacker news": "https://news.ycombinator.com",
    "numerama": "https://www.numerama.com",
    "clubic": "https://www.clubic.com",
    "01net": "https://www.01net.com",
    "the verge": "https://www.theverge.com",
    "ars technica": "https://arstechnica.com",
    # --- Achats & services -------------------------------------------------------
    "amazon": "https://www.amazon.fr",
    "leboncoin": "https://www.leboncoin.fr",
    "cdiscount": "https://www.cdiscount.com",
    "fnac": "https://www.fnac.com",
    "aliexpress": "https://fr.aliexpress.com",
    "ebay": "https://www.ebay.fr",
    "booking": "https://www.booking.com",
    "airbnb": "https://www.airbnb.fr",
    "sncf": "https://www.sncf-connect.com",
    "ratp": "https://www.ratp.fr",
    "meteo france": "https://meteofrance.com",
    "meteo": "https://meteofrance.com",
    "impots": "https://www.impots.gouv.fr",
    "service public": "https://www.service-public.fr",
    "ameli": "https://www.ameli.fr",
    "doctolib": "https://www.doctolib.fr",
    "la poste": "https://www.laposte.fr",
    # --- Jeux & divers -------------------------------------------------------------
    "steam": "https://store.steampowered.com",
    "epic games": "https://store.epicgames.com",
    "itch io": "https://itch.io",
    "speedtest": "https://www.speedtest.net",
    "imdb": "https://www.imdb.com",
    "allocine": "https://www.allocine.fr",
    "senscritique": "https://www.senscritique.com",
    "marmiton": "https://www.marmiton.org",
    "duolingo": "https://www.duolingo.com",
    "coursera": "https://www.coursera.org",
    "openclassrooms": "https://openclassrooms.com",
    "khan academy": "https://fr.khanacademy.org",
}

#: Alias supplémentaires (prononciations alternatives) -> clé canonique de SITES.
SITE_ALIASES = {
    "wiki": "wikipedia",
    "wikipedia francais": "wikipedia",
    "youtub": "youtube",
    "you tube": "youtube",
    "yt": "youtube",
    "ytb": "youtube",
    "gogle": "google",
    "gogol": "google",
    "ddg": "duckduckgo",
    "insta": "instagram",
    "fb": "facebook",
    "so": "stackoverflow",
    "git hub": "github",
    "hf": "hugging face",
    "gpt": "chatgpt",
    "chat gpt": "chatgpt",
    "drive": "google drive",
    "agenda": "google agenda",
    "calendrier": "google agenda",
    "mail": "gmail",
    "boite mail": "gmail",
    "courriel": "gmail",
    "carte": "google maps",
    "cartes": "google maps",
    "plan": "google maps",
    "traducteur": "traduction",
    "google translate": "traduction",
    "actualites": "france info",
    "info": "france info",
    "les infos": "france info",
    "journal": "le monde",
    "hn": "hacker news",
    "prime": "prime video",
    "disney": "disney plus",
    "apple": "apple music",
    "sncf connect": "sncf",
    "la meteo": "meteo",
    "docs python": "documentation python",
}

#: Dossiers autorisés pour ``open_folder``.
FOLDERS = {
    "documents": ("USERPROFILE", "Documents"),
    "telechargements": ("USERPROFILE", "Downloads"),
    "downloads": ("USERPROFILE", "Downloads"),
    "bureau": ("USERPROFILE", "Desktop"),
    "desktop": ("USERPROFILE", "Desktop"),
    "images": ("USERPROFILE", "Pictures"),
    "pictures": ("USERPROFILE", "Pictures"),
    "musique": ("USERPROFILE", "Music"),
    "music": ("USERPROFILE", "Music"),
    "videos": ("USERPROFILE", "Videos"),
    "personnel": ("USERPROFILE", ""),
    "accueil": ("USERPROFILE", ""),
    "home": ("USERPROFILE", ""),
}

#: Moteurs de recherche utilisables par ``web_search``.
SEARCH_ENGINES = {
    "google": "https://www.google.com/search?q={q}",
    "bing": "https://www.bing.com/search?q={q}",
    "duckduckgo": "https://duckduckgo.com/?q={q}",
    "qwant": "https://www.qwant.com/?q={q}",
    "ecosia": "https://www.ecosia.org/search?q={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "wikipedia": "https://fr.wikipedia.org/w/index.php?search={q}",
    "github": "https://github.com/search?q={q}",
    "images": "https://www.google.com/search?tbm=isch&q={q}",
    "maps": "https://www.google.com/maps/search/{q}",
    "amazon": "https://www.amazon.fr/s?k={q}",
    "stackoverflow": "https://stackoverflow.com/search?q={q}",
}

#: Domaines autorisés pour ``open_url`` (déduits de SITES + quelques extras).
EXTRA_ALLOWED_DOMAINS = {
    "google.com",
    "youtu.be",
    "wikipedia.org",
    "wikimedia.org",
    "github.io",
    "githubusercontent.com",
    "readthedocs.io",
    "gouv.fr",
    "openai.com",
    "anthropic.com",
}


def _domains_from_sites():
    domains = set(EXTRA_ALLOWED_DOMAINS)
    for url in SITES.values():
        host = urllib.parse.urlparse(url).hostname or ""
        host = host.lower()
        if host.startswith("www."):
            host = host[4:]
        parts = host.split(".")
        if len(parts) >= 2:
            # On autorise le domaine enregistrable et ses sous-domaines.
            domains.add(".".join(parts[-2:]) if len(parts) == 2 else host)
        if host:
            domains.add(host)
    return domains


#: Ensemble final des domaines autorisés.
ALLOWED_DOMAINS = _domains_from_sites()


# ===========================================================================
# HELPERS
# ===========================================================================


def _ok(**payload):
    result = {"success": True}
    result.update(payload)
    return result


def _err(message, **payload):
    result = {"success": False, "error": str(message)}
    result.update(payload)
    return result


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def _normalize_name(value) -> str:
    """Normalise un nom prononcé : minuscules, sans accent, espaces compactés."""
    text = _strip_accents(str(value or "")).lower().strip()
    text = text.replace("'", " ").replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text)
    return text


def _lookup(mapping, name, aliases=None):
    """Recherche tolérante dans une liste blanche (exact, alias, préfixe)."""
    key = _normalize_name(name)
    if not key:
        return None, None

    normalized = {_normalize_name(k): k for k in mapping}

    if key in normalized:
        original = normalized[key]
        return original, mapping[original]

    if aliases:
        alias_map = {_normalize_name(k): v for k, v in aliases.items()}
        if key in alias_map:
            target = _normalize_name(alias_map[key])
            if target in normalized:
                original = normalized[target]
                return original, mapping[original]

    # Correspondance partielle : "ouvre le site wikipedia s'il te plaît"
    def _contains_word(haystack, needle):
        return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None

    candidates = [
        k
        for k in normalized
        if len(k) >= 3 and (_contains_word(key, k) or _contains_word(k, key))
    ]
    if aliases:
        for alias, target in aliases.items():
            alias_key = _normalize_name(alias)
            target_key = _normalize_name(target)
            if len(alias_key) >= 3 and _contains_word(key, alias_key) and target_key in normalized:
                candidates.append(target_key)
    if len(candidates) == 1:
        original = normalized[candidates[0]]
        return original, mapping[original]
    if candidates:
        best = sorted(candidates, key=len, reverse=True)[0]
        original = normalized[best]
        return original, mapping[original]

    return None, None


def _windows_only():
    return _err("Cette action n'est disponible que sous Windows.")


def _run(args, timeout=15):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _powershell(script, timeout=20):
    """Exécute un script PowerShell et renvoie (ok, sortie)."""
    if not IS_WINDOWS:
        return False, "PowerShell indisponible."
    try:
        result = _run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            timeout=timeout,
        )
    except Exception as exc:  # pragma: no cover
        return False, str(exc)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "Echec PowerShell").strip()
    return True, (result.stdout or "").strip()


def _send_key(vk_code):
    """Envoie une touche virtuelle Windows (touches multimédia notamment)."""
    if not IS_WINDOWS:
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        user32.keybd_event(vk_code, 0, 0, 0)
        user32.keybd_event(vk_code, 0, 2, 0)  # KEYEVENTF_KEYUP
        return True
    except Exception:
        return False


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


# ===========================================================================
# AUDIO / VOLUME
# ===========================================================================


def endpoint():
    if AudioUtilities is None or IAudioEndpointVolume is None or CLSCTX_ALL is None:
        raise RuntimeError("Contrôle audio indisponible sur cet environnement.")

    device = AudioUtilities.GetSpeakers()
    interface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return interface.QueryInterface(IAudioEndpointVolume)


def _safe_volume_level():
    try:
        current = endpoint().GetMasterVolumeLevelScalar()
        return int(round(current * 100))
    except Exception:
        return 0


def set_volume(volume):
    """Règle le volume principal (0-100)."""
    try:
        value = max(0, min(100, int(volume)))
        audio = endpoint()
        audio.SetMute(0, None)
        audio.SetMasterVolumeLevelScalar(value / 100, None)
        return _ok(volume=value)
    except Exception as exc:
        return _err(exc)


def volume_up(amount=10):
    try:
        return set_volume(_safe_volume_level() + int(amount))
    except Exception as exc:
        return _err(exc)


def volume_down(amount=10):
    try:
        return set_volume(_safe_volume_level() - int(amount))
    except Exception as exc:
        return _err(exc)


def get_volume():
    """Renvoie le volume courant et l'état muet."""
    try:
        audio = endpoint()
        level = int(round(audio.GetMasterVolumeLevelScalar() * 100))
        muted = bool(audio.GetMute())
        return _ok(volume=level, muted=muted)
    except Exception as exc:
        return _err(exc)


def mute_audio():
    try:
        endpoint().SetMute(1, None)
        return _ok(muted=True)
    except Exception as exc:
        return _err(exc)


def unmute_audio():
    try:
        endpoint().SetMute(0, None)
        return _ok(muted=False)
    except Exception as exc:
        return _err(exc)


def toggle_mute():
    """Inverse l'état muet."""
    try:
        audio = endpoint()
        new_state = 0 if audio.GetMute() else 1
        audio.SetMute(new_state, None)
        return _ok(muted=bool(new_state))
    except Exception as exc:
        return _err(exc)


# ===========================================================================
# MULTIMÉDIA (touches système)
# ===========================================================================

VK_MEDIA_NEXT = 0xB0
VK_MEDIA_PREV = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3


def media_play_pause():
    """Lecture / pause du média en cours."""
    if not IS_WINDOWS:
        return _windows_only()
    return _ok(action="play_pause") if _send_key(VK_MEDIA_PLAY_PAUSE) else _err("Touche média refusée.")


def media_next():
    """Piste suivante."""
    if not IS_WINDOWS:
        return _windows_only()
    return _ok(action="next") if _send_key(VK_MEDIA_NEXT) else _err("Touche média refusée.")


def media_previous():
    """Piste précédente."""
    if not IS_WINDOWS:
        return _windows_only()
    return _ok(action="previous") if _send_key(VK_MEDIA_PREV) else _err("Touche média refusée.")


def media_stop():
    """Arrête la lecture."""
    if not IS_WINDOWS:
        return _windows_only()
    return _ok(action="stop") if _send_key(VK_MEDIA_STOP) else _err("Touche média refusée.")


# ===========================================================================
# APPLICATIONS
# ===========================================================================


def list_applications():
    """Liste les applications de la liste blanche."""
    names = sorted({_normalize_name(k) for k in APPS})
    return _ok(applications=names, count=len(names))


def open_application(application):
    """Ouvre une application autorisée."""
    name, executable = _lookup(APPS, application)
    if not executable:
        return _err(
            "Application non autorisee",
            hint="Utilise list_applications pour connaitre les applications disponibles.",
        )

    if not IS_WINDOWS:
        return _windows_only()

    try:
        if executable.endswith(":"):  # URI type ms-settings:
            os.startfile(executable)  # type: ignore[attr-defined]
        else:
            subprocess.Popen([executable], shell=False)
        return _ok(application=name, executable=executable)
    except FileNotFoundError:
        # Deuxième chance : laisser le shell Windows résoudre (raccourcis, PATH App).
        try:
            os.startfile(executable)  # type: ignore[attr-defined]
            return _ok(application=name, executable=executable)
        except Exception as exc:
            return _err(f"Application introuvable sur ce PC : {exc}")
    except Exception as exc:
        return _err(exc)


def close_application(application):
    """Ferme une application autorisée."""
    name, executable = _lookup(APPS, application)
    if not executable:
        return _err("Application non autorisee")

    if not IS_WINDOWS:
        return _windows_only()

    image = os.path.basename(executable)
    if not image.lower().endswith(".exe"):
        return _err("Cette application ne peut pas etre fermee automatiquement.")

    try:
        result = _run(["taskkill", "/IM", image, "/F"])
    except Exception as exc:
        return _err(exc)

    message = (result.stdout or result.stderr or "").strip()
    if result.returncode == 0:
        return _ok(application=name, message=message)
    return _err(message or "Application non lancee", application=name)


def is_application_running(application):
    """Indique si une application autorisée est en cours d'exécution."""
    name, executable = _lookup(APPS, application)
    if not executable:
        return _err("Application non autorisee")
    if not IS_WINDOWS:
        return _windows_only()

    image = os.path.basename(executable)
    try:
        result = _run(["tasklist", "/FI", f"IMAGENAME eq {image}"])
    except Exception as exc:
        return _err(exc)
    running = image.lower() in (result.stdout or "").lower()
    return _ok(application=name, running=running)


def list_running_applications(limit=15):
    """Liste les principales applications actuellement ouvertes (fenêtres visibles)."""
    if not IS_WINDOWS:
        return _windows_only()
    ok, out = _powershell(
        "Get-Process | Where-Object {$_.MainWindowTitle -ne ''} | "
        "Select-Object -ExpandProperty MainWindowTitle"
    )
    if not ok:
        return _err(out)
    titles = [line.strip() for line in out.splitlines() if line.strip()]
    try:
        limit = max(1, min(50, int(limit)))
    except Exception:
        limit = 15
    return _ok(windows=titles[:limit], count=len(titles))


# ===========================================================================
# SYSTÈME
# ===========================================================================


def get_system_info():
    """Informations générales sur la machine."""
    info = {
        "systeme": f"{platform.system()} {platform.release()}",
        "version": platform.version(),
        "machine": platform.machine(),
        "processeur": platform.processor() or platform.machine(),
        "hote": socket.gethostname(),
        "utilisateur": os.environ.get("USERNAME") or os.environ.get("USER") or "inconnu",
        "python": sys.version.split()[0],
    }
    if psutil is not None:
        try:
            memory = psutil.virtual_memory()
            info["ram_totale_go"] = round(memory.total / (1024 ** 3), 1)
            info["ram_utilisee_pourcent"] = memory.percent
            info["cpu_pourcent"] = psutil.cpu_percent(interval=0.2)
        except Exception:
            pass
    try:
        usage = shutil.disk_usage(os.path.expanduser("~"))
        info["disque_libre_go"] = round(usage.free / (1024 ** 3), 1)
        info["disque_total_go"] = round(usage.total / (1024 ** 3), 1)
    except Exception:
        pass
    return _ok(**info)


def get_battery_status():
    """État de la batterie (niveau, branchement)."""
    if psutil is not None:
        try:
            battery = psutil.sensors_battery()
            if battery is not None:
                payload = {
                    "pourcentage": int(battery.percent),
                    "branche": bool(battery.power_plugged),
                }
                if battery.secsleft and battery.secsleft > 0:
                    payload["autonomie_minutes"] = int(battery.secsleft // 60)
                return _ok(**payload)
        except Exception:
            pass

    if IS_WINDOWS:
        ok, out = _powershell(
            "(Get-CimInstance Win32_Battery | Select-Object -First 1 "
            "-ExpandProperty EstimatedChargeRemaining)"
        )
        if ok and out:
            try:
                return _ok(pourcentage=int(out.split()[0]))
            except Exception:
                pass
    return _err("Aucune batterie detectee sur cette machine.")


def get_disk_usage(drive=None):
    """Espace disque disponible."""
    target = drive or ("C:\\" if IS_WINDOWS else "/")
    try:
        usage = shutil.disk_usage(target)
    except Exception as exc:
        return _err(exc)
    return _ok(
        disque=target,
        total_go=round(usage.total / (1024 ** 3), 1),
        utilise_go=round(usage.used / (1024 ** 3), 1),
        libre_go=round(usage.free / (1024 ** 3), 1),
        libre_pourcent=round(usage.free / usage.total * 100, 1) if usage.total else 0,
    )


def take_screenshot(folder=None):
    """Capture l'écran et enregistre l'image dans Images (ou un dossier fourni)."""
    if not IS_WINDOWS:
        return _windows_only()

    target_dir = folder or os.path.join(os.path.expanduser("~"), "Pictures")
    try:
        os.makedirs(target_dir, exist_ok=True)
    except Exception as exc:
        return _err(exc)

    filename = dt.datetime.now().strftime("jarvis_%Y%m%d_%H%M%S.png")
    path = os.path.join(target_dir, filename)
    script = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
        "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen; "
        "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height; "
        "$g=[System.Drawing.Graphics]::FromImage($bmp); "
        "$g.CopyFromScreen($b.Left,$b.Top,0,0,$bmp.Size); "
        f"$bmp.Save('{path}'); $g.Dispose(); $bmp.Dispose();"
    )
    ok, out = _powershell(script, timeout=30)
    if not ok:
        return _err(out)
    return _ok(fichier=path)


def lock_workstation():
    """Verrouille la session Windows."""
    if not IS_WINDOWS:
        return _windows_only()
    try:
        import ctypes

        ctypes.windll.user32.LockWorkStation()
        return _ok(action="lock")
    except Exception as exc:
        return _err(exc)


def show_desktop():
    """Réduit toutes les fenêtres (affiche le bureau)."""
    if not IS_WINDOWS:
        return _windows_only()
    ok, out = _powershell(
        "$s = New-Object -ComObject Shell.Application; $s.MinimizeAll()"
    )
    return _ok(action="show_desktop") if ok else _err(out)


def shutdown_pc(delay_seconds=30, confirm=False):
    """Éteint le PC (nécessite confirm=True)."""
    if not IS_WINDOWS:
        return _windows_only()
    if not confirm:
        return _err(
            "Confirmation requise : rappelle l'outil avec confirm=true apres accord explicite."
        )
    try:
        delay = max(0, min(3600, int(delay_seconds)))
        _run(["shutdown", "/s", "/t", str(delay)])
        return _ok(action="shutdown", delai_secondes=delay)
    except Exception as exc:
        return _err(exc)


def restart_pc(delay_seconds=30, confirm=False):
    """Redémarre le PC (nécessite confirm=True)."""
    if not IS_WINDOWS:
        return _windows_only()
    if not confirm:
        return _err(
            "Confirmation requise : rappelle l'outil avec confirm=true apres accord explicite."
        )
    try:
        delay = max(0, min(3600, int(delay_seconds)))
        _run(["shutdown", "/r", "/t", str(delay)])
        return _ok(action="restart", delai_secondes=delay)
    except Exception as exc:
        return _err(exc)


def cancel_shutdown():
    """Annule un arrêt ou redémarrage programmé."""
    if not IS_WINDOWS:
        return _windows_only()
    try:
        result = _run(["shutdown", "/a"])
    except Exception as exc:
        return _err(exc)
    if result.returncode == 0:
        return _ok(action="cancel_shutdown")
    return _err((result.stderr or result.stdout or "Aucun arret programme").strip())


def set_brightness(level):
    """Règle la luminosité de l'écran principal (0-100)."""
    if not IS_WINDOWS:
        return _windows_only()
    try:
        value = max(0, min(100, int(level)))
    except Exception as exc:
        return _err(exc)
    ok, out = _powershell(
        "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods)"
        f".WmiSetBrightness(1,{value})"
    )
    return _ok(luminosite=value) if ok else _err(out or "Luminosite non pilotable.")


def sleep_pc():
    """Met le PC en veille (l'écran s'éteint, la session reste ouverte)."""
    if not IS_WINDOWS:
        return _windows_only()
    try:
        import ctypes

        # SetSuspendState(Hibernate=0, Force=1, WakeupEventsDisabled=0)
        if ctypes.windll.powrprof.SetSuspendState(0, 1, 0):
            return _ok(action="veille")
    except Exception:
        pass
    try:
        _run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
        return _ok(action="veille")
    except Exception as exc:
        return _err(exc)


def hibernate_pc():
    """Met le PC en hibernation (état enregistré sur le disque, extinction complète)."""
    if not IS_WINDOWS:
        return _windows_only()
    try:
        result = _run(["shutdown", "/h"])
    except Exception as exc:
        return _err(exc)
    if result.returncode == 0:
        return _ok(action="hibernation")
    message = (result.stderr or result.stdout or "").strip()
    try:
        import ctypes

        if ctypes.windll.powrprof.SetSuspendState(1, 1, 0):
            return _ok(action="hibernation")
    except Exception:
        pass
    return _err(message or "Hibernation indisponible (peut-etre desactivee sur ce PC).")


def turn_off_screen():
    """Éteint l'écran sans verrouiller la session (bouger la souris le rallume)."""
    if not IS_WINDOWS:
        return _windows_only()
    try:
        import ctypes

        # HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2 = éteint
        ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
        return _ok(action="ecran_eteint")
    except Exception as exc:
        return _err(exc)


def empty_recycle_bin(confirm=False):
    """Vide la corbeille Windows (nécessite confirm=True : action irréversible)."""
    if not IS_WINDOWS:
        return _windows_only()
    if not confirm:
        return _err(
            "Confirmation requise : vider la corbeille est irreversible. "
            "Rappelle l'outil avec confirm=true apres accord explicite."
        )
    ok, out = _powershell("Clear-RecycleBin -Force -Confirm:$false -ErrorAction Stop", timeout=60)
    if ok:
        return _ok(action="corbeille_videe")
    try:
        import ctypes

        # SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
        code = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x07)
        if code == 0:
            return _ok(action="corbeille_videe")
    except Exception:
        pass
    if "vide" in (out or "").lower() or "empty" in (out or "").lower():
        return _ok(action="corbeille_deja_vide")
    return _err(out or "Impossible de vider la corbeille.")


def get_folder_size(folder="telechargements"):
    """Mesure le poids d'un dossier autorisé et ses plus gros éléments."""
    name, path = _resolve_folder(folder)
    if not path:
        return _err(
            "Dossier non autorise",
            hint="Dossiers possibles : " + ", ".join(sorted(FOLDERS)),
        )
    if not os.path.isdir(path):
        return _err(f"Dossier introuvable : {path}")

    total = 0
    files = 0
    per_entry = {}
    try:
        for root, dirs, names in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for filename in names:
                full = os.path.join(root, filename)
                try:
                    size = os.path.getsize(full)
                except Exception:
                    continue
                total += size
                files += 1
                relative = os.path.relpath(full, path)
                top = relative.split(os.sep)[0]
                per_entry[top] = per_entry.get(top, 0) + size
                if files > 200000:  # garde-fou sur les arborescences géantes
                    raise StopIteration
    except StopIteration:
        pass
    except Exception as exc:
        return _err(exc)

    biggest = sorted(per_entry.items(), key=lambda item: item[1], reverse=True)[:5]
    return _ok(
        dossier=name,
        chemin=path,
        taille_mo=round(total / (1024 ** 2), 1),
        taille_go=round(total / (1024 ** 3), 2),
        fichiers=files,
        plus_gros=[
            {"nom": entry, "taille_mo": round(size / (1024 ** 2), 1)} for entry, size in biggest
        ],
    )


def show_notification(title="Jarvis", message="", duration=5):
    """Affiche une notification Windows (toast) en plus de la réponse vocale."""
    if not IS_WINDOWS:
        return _windows_only()
    heading = str(title or "Jarvis").strip()[:64] or "Jarvis"
    body = str(message or "").strip()[:256]
    if not body:
        return _err("Message de notification vide")

    safe_title = heading.replace("'", "''")
    safe_body = body.replace("'", "''")

    toast_script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType = WindowsRuntime] > $null; "
        "$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        "$texts = $template.GetElementsByTagName('text'); "
        f"$texts.Item(0).AppendChild($template.CreateTextNode('{safe_title}')) > $null; "
        f"$texts.Item(1).AppendChild($template.CreateTextNode('{safe_body}')) > $null; "
        "$toast = New-Object Windows.UI.Notifications.ToastNotification $template; "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        "'Jarvis').Show($toast)"
    )
    ok, out = _powershell(toast_script, timeout=25)
    if ok:
        return _ok(titre=heading, message=body, style="toast")

    try:
        seconds = max(1, min(30, int(duration)))
    except Exception:
        seconds = 5
    balloon_script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$icon = New-Object System.Windows.Forms.NotifyIcon; "
        "$icon.Icon = [System.Drawing.SystemIcons]::Information; "
        f"$icon.BalloonTipTitle = '{safe_title}'; "
        f"$icon.BalloonTipText = '{safe_body}'; "
        "$icon.Visible = $true; "
        f"$icon.ShowBalloonTip({seconds * 1000}); "
        f"Start-Sleep -Seconds {min(seconds, 10)}; $icon.Dispose()"
    )
    ok2, out2 = _powershell(balloon_script, timeout=40)
    if ok2:
        return _ok(titre=heading, message=body, style="infobulle")
    return _err(out2 or out or "Notification impossible.")


def wake_on_lan(mac, broadcast="255.255.255.255", port=9):
    """Réveille un autre PC du réseau local via un paquet magique Wake-on-LAN."""
    raw = re.sub(r"[^0-9a-fA-F]", "", str(mac or ""))
    if len(raw) != 12:
        return _err(
            "Adresse MAC invalide",
            hint="Format attendu : AA:BB:CC:DD:EE:FF.",
        )
    try:
        payload = b"\xff" * 6 + bytes.fromhex(raw) * 16
    except Exception as exc:
        return _err(exc)

    target = str(broadcast or "255.255.255.255").strip() or "255.255.255.255"
    try:
        port = max(1, min(65535, int(port)))
    except Exception:
        port = 9

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(3)
            sock.sendto(payload, (target, port))
            # Un second envoi sur le port 7, très courant lui aussi.
            if port == 9:
                sock.sendto(payload, (target, 7))
    except Exception as exc:
        return _err(f"Envoi du paquet magique impossible : {exc}")

    formatted = ":".join(raw[index : index + 2].upper() for index in range(0, 12, 2))
    return _ok(mac=formatted, diffusion=target, port=port, action="wake_on_lan")


# ===========================================================================
# CLAVIER : SAISIE DE TEXTE & TOUCHES
# ===========================================================================

#: Touches nommées autorisées pour ``press_key`` (liste blanche).
KEYS = {
    "entree": 0x0D, "enter": 0x0D, "retour": 0x0D, "return": 0x0D,
    "tabulation": 0x09, "tab": 0x09,
    "espace": 0x20, "space": 0x20,
    "echap": 0x1B, "escape": 0x1B, "esc": 0x1B,
    "retour arriere": 0x08, "backspace": 0x08, "effacer": 0x08,
    "suppr": 0x2E, "supprimer": 0x2E, "delete": 0x2E, "del": 0x2E,
    "inser": 0x2D, "insert": 0x2D,
    "haut": 0x26, "up": 0x26,
    "bas": 0x28, "down": 0x28,
    "gauche": 0x25, "left": 0x25,
    "droite": 0x27, "right": 0x27,
    "debut": 0x24, "home": 0x24,
    "fin": 0x23, "end": 0x23,
    "page precedente": 0x21, "page haut": 0x21, "pageup": 0x21,
    "page suivante": 0x22, "page bas": 0x22, "pagedown": 0x22,
    "impression ecran": 0x2C, "impr ecran": 0x2C, "printscreen": 0x2C,
    "verr maj": 0x14, "capslock": 0x14,
    "windows": 0x5B, "win": 0x5B,
    "menu": 0x5D,
}
KEYS.update({f"f{index}": 0x6F + index for index in range(1, 13)})
KEYS.update({chr(code): code for code in range(ord("A"), ord("Z") + 1)})
KEYS.update({chr(code).lower(): code for code in range(ord("A"), ord("Z") + 1)})
KEYS.update({str(digit): 0x30 + digit for digit in range(10)})

#: Modificateurs autorisés.
MODIFIERS = {
    "ctrl": 0x11, "control": 0x11, "controle": 0x11,
    "alt": 0x12,
    "maj": 0x10, "shift": 0x10,
    "win": 0x5B, "windows": 0x5B,
}

#: Longueur maximale d'un texte tapé d'un coup (garde-fou).
MAX_TYPE_LENGTH = 2000


def _key_down_up(vk_code, down=True):
    """Presse ou relâche une touche virtuelle Windows."""
    try:
        import ctypes

        flags = 0 if down else 2  # KEYEVENTF_KEYUP
        ctypes.windll.user32.keybd_event(vk_code, 0, flags, 0)
        return True
    except Exception:
        return False


def _send_unicode_text(text):
    """Tape un texte Unicode via ``keybd_event`` (KEYEVENTF_UNICODE)."""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        for char in text:
            if char == "\n":
                user32.keybd_event(0x0D, 0, 0, 0)
                user32.keybd_event(0x0D, 0, 2, 0)
                continue
            code = ord(char)
            # 0 + KEYEVENTF_UNICODE (0x4) : le scan code porte le caractère.
            user32.keybd_event(0, code, 0x4, 0)
            user32.keybd_event(0, code, 0x4 | 0x2, 0)
            time.sleep(0.002)
        return True
    except Exception:
        return False


def _parse_key_combo(key, modifiers=""):
    """Analyse « ctrl+s », key='s' + modifiers='ctrl' → (mods, touche)."""
    raw = _normalize_name(key).replace(" plus ", "+")
    tokens = [token.strip() for token in re.split(r"[+,]| et ", raw) if token.strip()]
    extra = [
        token.strip()
        for token in re.split(r"[+,]| et ", _normalize_name(modifiers))
        if token.strip()
    ]

    mods = []
    main = None
    for token in extra + tokens:
        if token in MODIFIERS:
            if MODIFIERS[token] not in mods:
                mods.append(MODIFIERS[token])
        elif token in KEYS:
            main = KEYS[token]
        else:
            return None, None, token
    return mods, main, None


def type_text(text):
    """Tape un texte au clavier dans la fenêtre active (comme si l'utilisateur l'écrivait)."""
    if not IS_WINDOWS:
        return _windows_only()
    content = str(text or "")
    if not content.strip():
        return _err("Texte vide")
    if len(content) > MAX_TYPE_LENGTH:
        return _err(f"Texte trop long (max {MAX_TYPE_LENGTH} caracteres).")
    if not _send_unicode_text(content):
        return _err("Saisie clavier refusee par le systeme.")
    return _ok(caracteres=len(content), texte=content[:80])


def press_key(key, modifiers="", repeat=1):
    """Appuie sur une touche autorisée, éventuellement avec des modificateurs (« ctrl+s »)."""
    if not IS_WINDOWS:
        return _windows_only()

    mods, main, unknown = _parse_key_combo(key, modifiers)
    if unknown is not None:
        return _err(
            f"Touche non autorisee : {unknown}",
            hint="Touches possibles : lettres, chiffres, F1-F12, entree, tab, espace, echap, "
            "suppr, fleches, debut, fin, page haut/bas ; modificateurs : ctrl, alt, maj, win.",
        )
    if main is None:
        return _err("Aucune touche principale indiquee.")

    try:
        count = max(1, min(20, int(repeat)))
    except Exception:
        count = 1

    for _ in range(count):
        for modifier in mods:
            _key_down_up(modifier, True)
        pressed = _key_down_up(main, True) and _key_down_up(main, False)
        for modifier in reversed(mods):
            _key_down_up(modifier, False)
        if not pressed:
            return _err("Touche refusee par le systeme.")
        time.sleep(0.02)

    return _ok(touche=_normalize_name(key), modificateurs=_normalize_name(modifiers), repetitions=count)


# ===========================================================================
# PRESSE-PAPIERS (+ HISTORIQUE)
# ===========================================================================


def _read_clipboard_native():
    """Lecture rapide du presse-papiers texte via l'API Windows (sans PowerShell)."""
    if not IS_WINDOWS:
        return None
    try:
        import ctypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not user32.OpenClipboard(0):
            return None
        try:
            handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
            if not handle:
                return None
            kernel32.GlobalLock.restype = ctypes.c_void_p
            pointer = kernel32.GlobalLock(ctypes.c_void_p(handle))
            if not pointer:
                return None
            try:
                return ctypes.c_wchar_p(pointer).value
            finally:
                kernel32.GlobalUnlock(ctypes.c_void_p(handle))
        finally:
            user32.CloseClipboard()
    except Exception:
        return None


_CLIPBOARD_LOCK = threading.RLock()
_CLIPBOARD_WATCHER = None


def _record_clipboard(text):
    """Ajoute une entrée à l'historique du presse-papiers (déduplique la dernière)."""
    content = str(text or "")
    if not content.strip():
        return None
    if len(content) > 5000:
        content = content[:5000]
    with _CLIPBOARD_LOCK:
        history = _read_json(CLIPBOARD_HISTORY_FILE, [])
        if not isinstance(history, list):
            history = []
        if history and history[0].get("texte") == content:
            return history[0]
        entry = {
            "texte": content,
            "date": dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
        }
        history.insert(0, entry)
        del history[CLIPBOARD_HISTORY_MAX:]
        try:
            _write_json(CLIPBOARD_HISTORY_FILE, history)
        except Exception:
            return None
        return entry


def _clipboard_watch_loop(interval):
    last = None
    while True:
        try:
            current = _read_clipboard_native()
            if current and current != last:
                last = current
                _record_clipboard(current)
        except Exception:
            pass
        time.sleep(interval)


def start_clipboard_watcher(interval=1.5):
    """Démarre (une seule fois) la surveillance du presse-papiers en tâche de fond."""
    global _CLIPBOARD_WATCHER
    if not IS_WINDOWS:
        return False
    with _CLIPBOARD_LOCK:
        if _CLIPBOARD_WATCHER is not None and _CLIPBOARD_WATCHER.is_alive():
            return True
        thread = threading.Thread(
            target=_clipboard_watch_loop,
            args=(max(0.5, float(interval)),),
            daemon=True,
            name="jarvis-clipboard",
        )
        _CLIPBOARD_WATCHER = thread
    thread.start()
    return True


def get_clipboard():
    """Lit le contenu texte du presse-papiers."""
    if not IS_WINDOWS:
        return _windows_only()
    content = _read_clipboard_native()
    if content is None:
        ok, out = _powershell("Get-Clipboard -Raw")
        if not ok:
            return _err(out)
        content = out
    _record_clipboard(content)
    start_clipboard_watcher()
    return _ok(texte=content)


def set_clipboard(text):
    """Copie un texte dans le presse-papiers."""
    if not IS_WINDOWS:
        return _windows_only()
    content = str(text or "")
    if not content:
        return _err("Texte vide")
    escaped = content.replace("'", "''")
    ok, out = _powershell(f"Set-Clipboard -Value '{escaped}'")
    if not ok:
        return _err(out)
    _record_clipboard(content)
    start_clipboard_watcher()
    return _ok(longueur=len(content))


def get_clipboard_history(limit=10):
    """Liste les derniers textes copiés (« recolle ce que j'ai copié avant »)."""
    start_clipboard_watcher()
    with _CLIPBOARD_LOCK:
        history = _read_json(CLIPBOARD_HISTORY_FILE, [])
    if not isinstance(history, list):
        history = []
    try:
        limit = max(1, min(50, int(limit)))
    except Exception:
        limit = 10

    entries = []
    for index, item in enumerate(history[:limit], start=1):
        texte = str(item.get("texte", ""))
        entries.append(
            {
                "index": index,
                "apercu": texte[:120] + ("..." if len(texte) > 120 else ""),
                "longueur": len(texte),
                "date": item.get("date", ""),
            }
        )
    return _ok(
        historique=entries,
        count=len(entries),
        total=len(history),
        message="Historique du presse-papiers vide." if not entries else None,
    )


def paste_from_history(index=1, paste=False):
    """Remet une entrée de l'historique dans le presse-papiers (index 1 = la plus récente)."""
    if not IS_WINDOWS:
        return _windows_only()
    with _CLIPBOARD_LOCK:
        history = _read_json(CLIPBOARD_HISTORY_FILE, [])
    if not isinstance(history, list) or not history:
        return _err("Historique du presse-papiers vide.")
    try:
        position = max(1, int(index))
    except Exception:
        position = 1
    if position > len(history):
        return _err(f"Seulement {len(history)} entrees dans l'historique.")

    texte = str(history[position - 1].get("texte", ""))
    result = set_clipboard(texte)
    if not result.get("success"):
        return result

    colle = False
    if paste:
        colle = bool(press_key("v", modifiers="ctrl").get("success"))

    return _ok(
        index=position,
        apercu=texte[:120] + ("..." if len(texte) > 120 else ""),
        colle=colle,
    )


def clear_clipboard_history(confirm=False):
    """Efface l'historique du presse-papiers (nécessite confirm=True)."""
    if not confirm:
        return _err("Confirmation requise pour effacer l'historique du presse-papiers.")
    with _CLIPBOARD_LOCK:
        try:
            if os.path.exists(CLIPBOARD_HISTORY_FILE):
                os.remove(CLIPBOARD_HISTORY_FILE)
        except Exception as exc:
            return _err(exc)
    return _ok(action="historique_efface")


# ===========================================================================
# DATE / HEURE
# ===========================================================================

_JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def get_local_time():
    """Heure locale."""
    now = dt.datetime.now()
    return _ok(time=now.strftime("%H:%M"), heure=now.hour, minute=now.minute)


def get_local_date():
    """Date locale."""
    now = dt.datetime.now()
    return _ok(
        date=now.strftime("%d/%m/%Y"),
        jour_semaine=_JOURS[now.weekday()],
        date_longue=f"{_JOURS[now.weekday()]} {now.day} {_MOIS[now.month - 1]} {now.year}",
    )


def get_datetime():
    """Date et heure complètes."""
    now = dt.datetime.now()
    return _ok(
        date=now.strftime("%d/%m/%Y"),
        heure=now.strftime("%H:%M:%S"),
        jour_semaine=_JOURS[now.weekday()],
        semaine_iso=now.isocalendar()[1],
        fuseau=time.tzname[0] if time.tzname else "local",
        iso=now.isoformat(timespec="seconds"),
    )


def days_until(date):
    """Nombre de jours entre aujourd'hui et une date (JJ/MM/AAAA ou AAAA-MM-JJ)."""
    raw = str(date or "").strip()
    parsed = None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y"):
        try:
            parsed = dt.datetime.strptime(raw, fmt).date()
            break
        except ValueError:
            continue
    if parsed is None:
        return _err("Date illisible. Utilise JJ/MM/AAAA.")
    delta = (parsed - dt.date.today()).days
    return _ok(
        date=parsed.strftime("%d/%m/%Y"),
        jours=delta,
        passe=delta < 0,
        jour_semaine=_JOURS[parsed.weekday()],
    )


# ===========================================================================
# MINUTEURS & RAPPELS
# ===========================================================================

_TIMERS = {}
_TIMERS_LOCK = threading.Lock()
_TIMER_SEQ = [0]


def _timer_finished(timer_id, label):
    with _TIMERS_LOCK:
        entry = _TIMERS.get(timer_id)
        if entry:
            entry["done"] = True
    print(f"\n[Jarvis] ⏰ Minuteur terminé : {label}")
    # Notification visuelle en plus du bip et de la voix.
    try:
        show_notification("Minuteur terminé", str(label))
    except Exception:
        pass
    if IS_WINDOWS:
        try:
            import winsound

            for _ in range(3):
                winsound.Beep(880, 250)
        except Exception:
            pass


def set_timer(seconds=None, minutes=None, label="minuteur"):
    """Programme un minuteur (en secondes et/ou minutes)."""
    try:
        total = int(seconds or 0) + int(minutes or 0) * 60
    except Exception:
        return _err("Duree invalide")
    if total <= 0:
        return _err("Duree invalide : indique au moins une seconde.")
    if total > 24 * 3600:
        return _err("Duree trop longue (max 24 heures).")

    with _TIMERS_LOCK:
        _TIMER_SEQ[0] += 1
        timer_id = _TIMER_SEQ[0]

    name = str(label or "minuteur").strip() or "minuteur"
    thread = threading.Timer(total, _timer_finished, args=(timer_id, name))
    thread.daemon = True
    thread.start()

    with _TIMERS_LOCK:
        _TIMERS[timer_id] = {
            "id": timer_id,
            "label": name,
            "fin": (dt.datetime.now() + dt.timedelta(seconds=total)).strftime("%H:%M:%S"),
            "timer": thread,
            "done": False,
        }

    return _ok(id=timer_id, label=name, duree_secondes=total, fin=_TIMERS[timer_id]["fin"])


def list_timers():
    """Liste les minuteurs actifs."""
    with _TIMERS_LOCK:
        active = [
            {"id": e["id"], "label": e["label"], "fin": e["fin"]}
            for e in _TIMERS.values()
            if not e["done"]
        ]
    return _ok(minuteurs=active, count=len(active))


def cancel_timer(timer_id=None):
    """Annule un minuteur (ou tous si aucun identifiant)."""
    with _TIMERS_LOCK:
        if timer_id is None:
            count = 0
            for entry in _TIMERS.values():
                if not entry["done"]:
                    entry["timer"].cancel()
                    entry["done"] = True
                    count += 1
            return _ok(annules=count)

        try:
            key = int(timer_id)
        except Exception:
            return _err("Identifiant invalide")
        entry = _TIMERS.get(key)
        if not entry or entry["done"]:
            return _err("Minuteur introuvable")
        entry["timer"].cancel()
        entry["done"] = True
        return _ok(id=key, label=entry["label"])


# ===========================================================================
# NOTES
# ===========================================================================


def take_note(text):
    """Enregistre une note datée."""
    content = str(text or "").strip()
    if not content:
        return _err("Note vide")
    notes = _read_json(NOTES_FILE, [])
    if not isinstance(notes, list):
        notes = []
    entry = {"date": dt.datetime.now().strftime("%d/%m/%Y %H:%M"), "texte": content}
    notes.append(entry)
    try:
        _write_json(NOTES_FILE, notes)
    except Exception as exc:
        return _err(exc)
    return _ok(note=entry, total=len(notes))


def read_notes(limit=5):
    """Relit les dernières notes enregistrées."""
    notes = _read_json(NOTES_FILE, [])
    if not isinstance(notes, list) or not notes:
        return _ok(notes=[], count=0, message="Aucune note enregistree.")
    try:
        limit = max(1, min(50, int(limit)))
    except Exception:
        limit = 5
    return _ok(notes=notes[-limit:], count=len(notes))


def delete_notes(confirm=False):
    """Efface toutes les notes (nécessite confirm=True)."""
    if not confirm:
        return _err("Confirmation requise pour effacer les notes.")
    try:
        if os.path.exists(NOTES_FILE):
            os.remove(NOTES_FILE)
    except Exception as exc:
        return _err(exc)
    return _ok(action="notes_effacees")


# ===========================================================================
# LISTE DE TÂCHES (TODO)
# ===========================================================================


def add_todo(text, due="", priority="normale"):
    """Ajoute une tâche à la liste de choses à faire."""
    return get_default_todo_manager().add_task(text, due=due, priority=priority)


def list_todos(status="pending", limit=20):
    """Liste les tâches (par défaut celles qui restent à faire)."""
    return get_default_todo_manager().list_tasks(status=status, limit=limit)


def complete_todo(todo_id=None, text=""):
    """Marque une tâche comme faite (par identifiant ou par libellé)."""
    return get_default_todo_manager().complete_task(task_id=todo_id, label=text)


def reopen_todo(todo_id=None, text=""):
    """Remet une tâche terminée dans les choses à faire."""
    return get_default_todo_manager().reopen_task(task_id=todo_id, label=text)


def delete_todo(todo_id=None, text=""):
    """Supprime définitivement une tâche."""
    return get_default_todo_manager().delete_task(task_id=todo_id, label=text)


def clear_todos(confirm=False, only_done=False):
    """Vide la liste de tâches (only_done=true pour ne retirer que celles déjà faites)."""
    return get_default_todo_manager().clear_tasks(confirm=confirm, only_done=only_done)


# ===========================================================================
# JOURNAL D'ACTIVITÉ & SAUVEGARDE
# ===========================================================================


def get_activity_log(day="aujourd'hui", limit=30):
    """Raconte ce que Jarvis a fait (« qu'as-tu fait aujourd'hui ? »)."""
    return get_default_activity_log().query(day=day, limit=limit)


def clear_activity_log(confirm=False):
    """Efface le journal d'activité local (nécessite confirm=True)."""
    return get_default_activity_log().clear(confirm=confirm)


def backup_data(destination=""):
    """Exporte mémoire, routines, tâches, rappels et notes dans un fichier JSON local."""
    return _create_backup(destination)


def list_backups(folder=""):
    """Liste les sauvegardes déjà réalisées."""
    return _list_backups(folder)


# ===========================================================================
# WEB
# ===========================================================================


def list_websites():
    """Liste les sites de la liste blanche."""
    names = sorted(SITES)
    return _ok(sites=names, count=len(names))


def open_website(site):
    """Ouvre un site autorisé dans le navigateur."""
    name, url = _lookup(SITES, site, SITE_ALIASES)
    if not url:
        return _err(
            "Site non autorise",
            hint="Utilise list_websites pour connaitre les sites disponibles.",
        )
    try:
        webbrowser.open(url)
    except Exception as exc:
        return _err(exc)
    return _ok(site=name, url=url)


def _is_domain_allowed(host):
    host = (host or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return False
    for allowed in ALLOWED_DOMAINS:
        if host == allowed or host.endswith("." + allowed):
            return True
    return False


def open_url(url):
    """Ouvre une URL précise si son domaine appartient à la liste blanche."""
    raw = str(url or "").strip()
    if not raw:
        return _err("URL vide")
    if "://" not in raw:
        raw = "https://" + raw

    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        return _err("Seuls http et https sont autorises.")
    if not _is_domain_allowed(parsed.hostname):
        return _err(f"Domaine non autorise : {parsed.hostname}")

    try:
        webbrowser.open(raw)
    except Exception as exc:
        return _err(exc)
    return _ok(url=raw, domaine=parsed.hostname)


def web_search(query, engine="google"):
    """Recherche sur le Web avec le moteur choisi."""
    q = str(query or "").strip()
    if not q:
        return _err("Requete vide")

    key = _normalize_name(engine) or "google"
    template = SEARCH_ENGINES.get(key)
    if template is None:
        _, template = _lookup(SEARCH_ENGINES, key)
    if template is None:
        template = SEARCH_ENGINES["google"]
        key = "google"

    url = template.format(q=urllib.parse.quote(q))
    try:
        webbrowser.open(url)
    except Exception as exc:
        return _err(exc)
    return _ok(query=q, moteur=key, url=url)


def search_youtube(query):
    """Recherche une vidéo sur YouTube."""
    return web_search(query, engine="youtube")


def search_wikipedia(query):
    """Recherche un article sur Wikipédia."""
    return web_search(query, engine="wikipedia")


def open_maps(place):
    """Ouvre un lieu dans Google Maps."""
    location = str(place or "").strip()
    if not location:
        return _err("Lieu vide")
    url = f"https://www.google.com/maps/search/{urllib.parse.quote(location)}"
    try:
        webbrowser.open(url)
    except Exception as exc:
        return _err(exc)
    return _ok(lieu=location, url=url)


def get_directions(destination, origin=None):
    """Ouvre un itinéraire Google Maps."""
    dest = str(destination or "").strip()
    if not dest:
        return _err("Destination vide")
    start = urllib.parse.quote(str(origin or "").strip())
    url = (
        "https://www.google.com/maps/dir/?api=1"
        f"&destination={urllib.parse.quote(dest)}"
    )
    if start:
        url += f"&origin={start}"
    try:
        webbrowser.open(url)
    except Exception as exc:
        return _err(exc)
    return _ok(destination=dest, origine=origin or "position actuelle", url=url)


def translate_text(text, target_language="en", source_language="auto"):
    """Ouvre Google Traduction avec le texte à traduire."""
    content = str(text or "").strip()
    if not content:
        return _err("Texte vide")
    url = (
        "https://translate.google.com/?sl="
        f"{urllib.parse.quote(str(source_language or 'auto'))}"
        f"&tl={urllib.parse.quote(str(target_language or 'en'))}"
        f"&text={urllib.parse.quote(content)}&op=translate"
    )
    try:
        webbrowser.open(url)
    except Exception as exc:
        return _err(exc)
    return _ok(texte=content, langue_cible=target_language, url=url)


def get_weather(city="Paris"):
    """Météo actuelle via wttr.in (aucune clé API requise)."""
    location = str(city or "Paris").strip() or "Paris"
    url = f"https://wttr.in/{urllib.parse.quote(location)}?format=j1&lang=fr"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:
        return _err(f"Meteo indisponible : {exc}")

    try:
        current = payload["current_condition"][0]
        today = payload["weather"][0]
        description = current.get("lang_fr", [{}])[0].get("value") or current.get(
            "weatherDesc", [{}]
        )[0].get("value", "")
        return _ok(
            ville=location,
            description=description,
            temperature_c=int(current.get("temp_C", 0)),
            ressenti_c=int(current.get("FeelsLikeC", 0)),
            humidite=int(current.get("humidity", 0)),
            vent_kmh=int(current.get("windspeedKmph", 0)),
            min_c=int(today.get("mintempC", 0)),
            max_c=int(today.get("maxtempC", 0)),
        )
    except Exception as exc:
        return _err(f"Reponse meteo illisible : {exc}")


#: Codes météo WMO (open-meteo) traduits en français.
WMO_CODES = {
    0: "ciel dégagé", 1: "plutôt dégagé", 2: "partiellement nuageux", 3: "couvert",
    45: "brouillard", 48: "brouillard givrant",
    51: "bruine légère", 53: "bruine", 55: "bruine dense",
    56: "bruine verglaçante", 57: "bruine verglaçante dense",
    61: "pluie faible", 63: "pluie", 65: "pluie forte",
    66: "pluie verglaçante", 67: "pluie verglaçante forte",
    71: "neige faible", 73: "neige", 75: "neige forte", 77: "grains de neige",
    80: "averses faibles", 81: "averses", 82: "fortes averses",
    85: "averses de neige", 86: "fortes averses de neige",
    95: "orage", 96: "orage avec grêle", 99: "orage violent avec grêle",
}


def _fetch_json(url, timeout=10):
    request = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _forecast_from_wttr(location, days):
    """Repli : wttr.in fournit jusqu'à 3 jours de prévision."""
    url = f"https://wttr.in/{urllib.parse.quote(location)}?format=j1&lang=fr"
    payload = _fetch_json(url)
    entries = []
    for item in payload.get("weather", [])[:days]:
        date = dt.datetime.strptime(item["date"], "%Y-%m-%d")
        midday = (item.get("hourly") or [{}])[len(item.get("hourly") or [{}]) // 2]
        description = (midday.get("lang_fr", [{}]) or [{}])[0].get("value") or (
            midday.get("weatherDesc", [{}]) or [{}]
        )[0].get("value", "")
        entries.append(
            {
                "jour": _JOURS[date.weekday()],
                "date": date.strftime("%d/%m"),
                "description": description,
                "min_c": int(item.get("mintempC", 0)),
                "max_c": int(item.get("maxtempC", 0)),
            }
        )
    return entries


def get_forecast(city="Paris", days=7):
    """Prévisions météo jusqu'à 7 jours (open-meteo, sans clé API)."""
    location = str(city or "Paris").strip() or "Paris"
    try:
        days = max(1, min(7, int(days)))
    except Exception:
        days = 7

    try:
        geo = _fetch_json(
            "https://geocoding-api.open-meteo.com/v1/search?"
            + urllib.parse.urlencode({"name": location, "count": 1, "language": "fr", "format": "json"})
        )
        results = geo.get("results") or []
        if not results:
            raise ValueError(f"ville inconnue : {location}")
        place = results[0]
        payload = _fetch_json(
            "https://api.open-meteo.com/v1/forecast?"
            + urllib.parse.urlencode(
                {
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "timezone": "auto",
                    "forecast_days": days,
                }
            )
        )
        daily = payload.get("daily") or {}
        entries = []
        for index, iso_date in enumerate(daily.get("time", [])[:days]):
            date = dt.datetime.strptime(iso_date, "%Y-%m-%d")
            code = (daily.get("weather_code") or [None])[index]
            entries.append(
                {
                    "jour": _JOURS[date.weekday()],
                    "date": date.strftime("%d/%m"),
                    "description": WMO_CODES.get(code, "temps variable"),
                    "min_c": round(float((daily.get("temperature_2m_min") or [0])[index])),
                    "max_c": round(float((daily.get("temperature_2m_max") or [0])[index])),
                    "pluie_pourcent": (daily.get("precipitation_probability_max") or [None])[index],
                }
            )
        if not entries:
            raise ValueError("aucune prevision recue")
        return _ok(
            ville=place.get("name", location),
            pays=place.get("country", ""),
            jours=entries,
            count=len(entries),
            source="open-meteo",
        )
    except Exception as exc:
        try:
            entries = _forecast_from_wttr(location, min(days, 3))
            if entries:
                return _ok(ville=location, jours=entries, count=len(entries), source="wttr.in")
        except Exception:
            pass
        return _err(f"Previsions indisponibles : {exc}")


#: Plateformes reconnues par ``find_something_to_watch`` (pages JustWatch).
WATCH_PROVIDERS = {
    "netflix": "nfx",
    "prime video": "amp",
    "amazon": "amp",
    "disney plus": "dnp",
    "disney": "dnp",
    "canal": "cpd",
    "apple tv": "atp",
    "ocs": "ocs",
    "arte": "arte",
    "crunchyroll": "cru",
    "paramount": "pmp",
}


def find_something_to_watch(query="", genre="", service=""):
    """Propose quoi regarder ce soir : ouvre la fiche ou le catalogue correspondant."""
    title = str(query or "").strip()
    genre_text = str(genre or "").strip()
    provider_key, provider_code = _lookup(WATCH_PROVIDERS, service) if service else (None, None)

    if title:
        url = "https://www.justwatch.com/fr/recherche?" + urllib.parse.urlencode({"q": title})
        intention = "fiche"
    elif provider_code:
        url = "https://www.justwatch.com/fr/fournisseur/" + urllib.parse.quote(
            provider_key.replace(" ", "-")
        )
        intention = "catalogue"
    elif genre_text:
        url = "https://www.justwatch.com/fr/recherche?" + urllib.parse.urlencode(
            {"q": f"que regarder {genre_text}"}
        )
        intention = "catalogue"
    else:
        url = "https://www.justwatch.com/fr/nouveautes"
        intention = "nouveautes"

    try:
        webbrowser.open(url)
    except Exception as exc:
        return _err(exc)
    return _ok(
        url=url,
        recherche=title,
        genre=genre_text,
        plateforme=provider_key or "",
        intention=intention,
    )


def draft_email(to="", subject="", body=""):
    """Prépare un brouillon d'email et l'ouvre dans le client de messagerie (mailto:)."""
    recipients = [
        address.strip()
        for address in re.split(r"[;,\s]+", str(to or ""))
        if address.strip()
    ]
    for address in recipients:
        if not re.match(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$", address):
            return _err(f"Adresse email invalide : {address}")

    subject_text = str(subject or "").strip()[:200]
    body_text = str(body or "").strip()[:5000]
    if not body_text and not subject_text:
        return _err("Rien a envoyer : precise au moins un objet ou un contenu.")

    params = {}
    if subject_text:
        params["subject"] = subject_text
    if body_text:
        params["body"] = body_text
    url = "mailto:" + urllib.parse.quote(",".join(recipients), safe="@,.")
    if params:
        url += "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)

    try:
        webbrowser.open(url)
    except Exception as exc:
        return _err(exc)
    return _ok(
        destinataires=recipients,
        objet=subject_text,
        corps=body_text[:200] + ("..." if len(body_text) > 200 else ""),
        caracteres=len(body_text),
    )


def check_internet():
    """Vérifie la connectivité Internet."""
    start = time.time()
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=4):
            pass
    except Exception as exc:
        return _ok(connecte=False, detail=str(exc))
    return _ok(connecte=True, latence_ms=int((time.time() - start) * 1000))


# ===========================================================================
# FICHIERS
# ===========================================================================


def _resolve_folder(name):
    key, spec = _lookup(FOLDERS, name)
    if not spec:
        return None, None
    base = os.path.expanduser("~")
    path = os.path.join(base, spec[1]) if spec[1] else base
    return key, path


def open_folder(folder):
    """Ouvre un dossier utilisateur autorisé."""
    name, path = _resolve_folder(folder)
    if not path:
        return _err(
            "Dossier non autorise",
            hint="Dossiers possibles : " + ", ".join(sorted(FOLDERS)),
        )
    if not os.path.isdir(path):
        return _err(f"Dossier introuvable : {path}")
    try:
        if IS_WINDOWS:
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as exc:
        return _err(exc)
    return _ok(dossier=name, chemin=path)


def list_folder(folder, limit=20):
    """Liste le contenu d'un dossier utilisateur autorisé."""
    name, path = _resolve_folder(folder)
    if not path:
        return _err("Dossier non autorise")
    try:
        entries = sorted(os.listdir(path))
    except Exception as exc:
        return _err(exc)
    try:
        limit = max(1, min(100, int(limit)))
    except Exception:
        limit = 20
    return _ok(dossier=name, chemin=path, elements=entries[:limit], total=len(entries))


def search_files(pattern, folder="documents", limit=15):
    """Cherche des fichiers par nom dans un dossier autorisé."""
    needle = _normalize_name(pattern)
    if not needle:
        return _err("Motif de recherche vide")
    name, path = _resolve_folder(folder)
    if not path:
        return _err("Dossier non autorise")

    try:
        limit = max(1, min(50, int(limit)))
    except Exception:
        limit = 15

    matches = []
    try:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith(".")][:50]
            for filename in files:
                if needle in _normalize_name(filename):
                    matches.append(os.path.join(root, filename))
                    if len(matches) >= limit:
                        raise StopIteration
    except StopIteration:
        pass
    except Exception as exc:
        return _err(exc)

    return _ok(dossier=name, motif=pattern, fichiers=matches, count=len(matches))


def _allowed_roots():
    """Racines autorisées pour la lecture de fichiers."""
    roots = []
    for key in FOLDERS:
        _, path = _resolve_folder(key)
        if path:
            roots.append(os.path.realpath(path))
    return roots


def _resolve_readable_file(name, folder="documents"):
    """Trouve un fichier lisible : chemin complet autorisé, ou recherche par nom."""
    raw = str(name or "").strip().strip('"')
    if not raw:
        return None, "Aucun fichier indique."

    candidate = os.path.realpath(os.path.expanduser(raw))
    if os.path.isfile(candidate):
        roots = _allowed_roots()
        if any(candidate.startswith(root + os.sep) or candidate == root for root in roots):
            return candidate, None
        return None, "Fichier hors des dossiers autorises."

    found = search_files(raw, folder=folder, limit=5)
    if not found.get("success"):
        return None, found.get("error", "Recherche impossible.")
    matches = found.get("fichiers") or []
    if not matches:
        return None, f"Aucun fichier nomme '{raw}' dans {folder}."
    return matches[0], None


def read_file_aloud(name, folder="documents", max_chars=4000):
    """Lit le contenu d'un fichier texte pour que Jarvis le lise ou le résume à voix haute."""
    path, error = _resolve_readable_file(name, folder)
    if error:
        return _err(error)

    extension = os.path.splitext(path)[1].lower()
    if extension not in READABLE_EXTENSIONS:
        return _err(
            f"Format non lisible : {extension or 'inconnu'}",
            hint="Formats acceptes : " + ", ".join(sorted(READABLE_EXTENSIONS)),
        )

    try:
        max_chars = max(200, min(20000, int(max_chars)))
    except Exception:
        max_chars = 4000

    try:
        size = os.path.getsize(path)
        if size > 5 * 1024 * 1024:
            return _err("Fichier trop volumineux (plus de 5 Mo).")
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            content = handle.read(max_chars + 1)
    except Exception as exc:
        return _err(exc)

    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars]

    words = len(content.split())
    return _ok(
        fichier=os.path.basename(path),
        chemin=path,
        contenu=content,
        mots=words,
        tronque=truncated,
        consigne=(
            "Lis ce contenu a voix haute de facon naturelle. S'il est long ou tronque, "
            "resume-le d'abord en quelques phrases puis propose de lire la suite."
        ),
    )


# ===========================================================================
# CALCUL & DIVERS
# ===========================================================================

_MATH_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_MATH_NAMES = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "sqrt": math.sqrt,
    "racine": math.sqrt,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
    "fact": math.factorial,
    "factorielle": math.factorial,
}


def _eval_node(node):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("Valeur non numerique")
    if isinstance(node, ast.BinOp) and type(node.op) in _MATH_OPS:
        return _MATH_OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _MATH_OPS:
        return _MATH_OPS[type(node.op)](_eval_node(node.operand))
    if isinstance(node, ast.Name) and node.id in _MATH_NAMES:
        value = _MATH_NAMES[node.id]
        if callable(value):
            raise ValueError("Fonction utilisee sans parenthese")
        return value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        func = _MATH_NAMES.get(node.func.id)
        if not callable(func):
            raise ValueError(f"Fonction inconnue : {node.func.id}")
        return func(*[_eval_node(arg) for arg in node.args])
    raise ValueError("Expression non autorisee")


def calculate(expression):
    """Évalue une expression mathématique de façon sécurisée."""
    raw = str(expression or "").strip()
    if not raw:
        return _err("Expression vide")

    cleaned = _strip_accents(raw.lower())
    for word, symbol in (
        ("divise par", "/"),
        ("multiplie par", "*"),
        ("puissance", "**"),
        ("plus", "+"),
        ("moins", "-"),
        ("fois", "*"),
    ):
        cleaned = re.sub(rf"\b{word}\b", symbol, cleaned)
    cleaned = (
        cleaned.replace("×", "*")
        .replace("÷", "/")
        .replace("^", "**")
    )
    # "3 x 4" -> "3 * 4" (mais on preserve exp, max, log10...)
    cleaned = re.sub(r"(?<=[\d\s)])\s*x\s*(?=[\d(])", "*", cleaned)
    # Virgule decimale : 3,5 -> 3.5 (sauf si l'expression utilise des fonctions
    # a plusieurs arguments comme max(3,9)).
    if "(" not in cleaned:
        cleaned = re.sub(r"(?<=\d),(?=\d)", ".", cleaned)
    if len(cleaned) > 200:
        return _err("Expression trop longue")

    try:
        tree = ast.parse(cleaned, mode="eval")
        value = _eval_node(tree)
    except Exception as exc:
        return _err(f"Calcul impossible : {exc}")

    if isinstance(value, float) and value.is_integer():
        value = int(value)
    elif isinstance(value, float):
        value = round(value, 10)
    return _ok(expression=raw, resultat=value)


def random_number(minimum=1, maximum=100):
    """Tire un nombre aléatoire entre deux bornes."""
    try:
        low, high = int(minimum), int(maximum)
    except Exception:
        return _err("Bornes invalides")
    if low > high:
        low, high = high, low
    return _ok(nombre=random.randint(low, high), min=low, max=high)


def flip_coin():
    """Pile ou face."""
    return _ok(resultat=random.choice(["pile", "face"]))


def roll_dice(sides=6, count=1):
    """Lance un ou plusieurs dés."""
    try:
        sides = max(2, min(1000, int(sides)))
        count = max(1, min(20, int(count)))
    except Exception:
        return _err("Parametres invalides")
    rolls = [random.randint(1, sides) for _ in range(count)]
    return _ok(des=rolls, total=sum(rolls), faces=sides)


def pick_random(options):
    """Choisit un élément au hasard dans une liste."""
    if isinstance(options, str):
        items = [o.strip() for o in re.split(r"[,;]| ou ", options) if o.strip()]
    elif isinstance(options, (list, tuple)):
        items = [str(o).strip() for o in options if str(o).strip()]
    else:
        items = []
    if not items:
        return _err("Aucune option fournie")
    return _ok(choix=random.choice(items), options=items)


# ===========================================================================
# MÉMOIRE PERSISTANTE
# ===========================================================================


def remember(content, category="other", importance=3):
    """Ajoute ou met à jour un souvenir durable dans la mémoire locale."""
    return get_default_memory_manager().add_memory(
        content,
        category=category,
        importance=importance,
        source="tool:remember",
    )


def recall(query="", limit=5):
    """Recherche les souvenirs pertinents pour une requête."""
    return get_default_memory_manager().search_memories(query, limit=limit)


def list_memories(limit=50, category=None):
    """Liste les souvenirs enregistrés, pour inspection par l'utilisateur."""
    return get_default_memory_manager().list_memories(limit=limit, category=category)


def search_memories(query, limit=10):
    """Recherche explicite dans la mémoire locale."""
    return get_default_memory_manager().search_memories(query, limit=limit)


def update_memory(memory_id, content=None, category=None, importance=None):
    """Modifie un souvenir existant."""
    return get_default_memory_manager().update_memory(
        memory_id,
        content=content,
        category=category,
        importance=importance,
    )


def delete_memory(memory_id):
    """Supprime logiquement un souvenir précis."""
    return get_default_memory_manager().delete_memory(memory_id)


def forget(query, confirm=False, limit=10):
    """Oublie les souvenirs liés à une requête après confirmation."""
    return get_default_memory_manager().forget(query, limit=limit, confirm=confirm)


def clear_memory(confirm=False):
    """Efface toute la mémoire durable après confirmation."""
    return get_default_memory_manager().clear_memory(confirm=confirm)


# ===========================================================================
# ROUTINES (macros vocales)
# ===========================================================================


def create_routine(name, steps, description="", schedule=""):
    """Crée une routine : un enchaînement d'outils nommé et rejouable."""
    return get_default_routine_manager().create_routine(
        name,
        steps,
        description=description,
        schedule=schedule or None,
    )


def run_routine(name):
    """Exécute une routine existante."""
    return get_default_routine_manager().run_routine(name)


def list_routines():
    """Liste les routines enregistrées."""
    return get_default_routine_manager().list_routines()


def describe_routine(name):
    """Détaille les étapes et la planification d'une routine."""
    return get_default_routine_manager().describe_routine(name)


def update_routine(name, steps=None, description=None, schedule=None, enabled=None):
    """Modifie une routine : étapes, description, planification ou activation."""
    return get_default_routine_manager().update_routine(
        name,
        steps=steps,
        description=description,
        schedule=schedule,
        enabled=enabled,
    )


def delete_routine(name, confirm=False):
    """Supprime une routine après confirmation."""
    return get_default_routine_manager().delete_routine(name, confirm=confirm)


def list_routine_tools():
    """Liste les outils utilisables comme étape d'une routine."""
    from .routines import FORBIDDEN_TOOLS, available_tools

    return _ok(outils=available_tools(), interdits=sorted(FORBIDDEN_TOOLS))


# ===========================================================================
# RAPPELS PERSISTANTS
# ===========================================================================


def set_reminder(text, when="", recurrence="", routine=""):
    """Programme un rappel durable, conservé après un redémarrage."""
    return get_default_scheduler().add_reminder(
        text,
        when=when or None,
        recurrence=recurrence,
        routine=routine or None,
    )


def list_reminders(limit=20):
    """Liste les rappels en attente."""
    return get_default_scheduler().list_reminders(limit=limit)


def cancel_reminder(reminder_id=None, confirm=False):
    """Annule un rappel (ou tous, avec confirmation)."""
    return get_default_scheduler().cancel_reminder(reminder_id=reminder_id, confirm=confirm)



# ===========================================================================
# ENREGISTREMENT DES OUTILS
# ===========================================================================

_TOOL_IMPLEMENTATIONS = {
    # Applications
    "open_application": open_application,
    "close_application": close_application,
    "is_application_running": is_application_running,
    "list_applications": list_applications,
    "list_running_applications": list_running_applications,
    # Audio
    "set_volume": set_volume,
    "volume_up": volume_up,
    "volume_down": volume_down,
    "get_volume": get_volume,
    "mute_audio": mute_audio,
    "unmute_audio": unmute_audio,
    "toggle_mute": toggle_mute,
    # Multimédia
    "media_play_pause": media_play_pause,
    "media_next": media_next,
    "media_previous": media_previous,
    "media_stop": media_stop,
    # Système
    "get_system_info": get_system_info,
    "get_battery_status": get_battery_status,
    "get_disk_usage": get_disk_usage,
    "take_screenshot": take_screenshot,
    "lock_workstation": lock_workstation,
    "show_desktop": show_desktop,
    "shutdown_pc": shutdown_pc,
    "restart_pc": restart_pc,
    "cancel_shutdown": cancel_shutdown,
    "set_brightness": set_brightness,
    "sleep_pc": sleep_pc,
    "hibernate_pc": hibernate_pc,
    "turn_off_screen": turn_off_screen,
    "empty_recycle_bin": empty_recycle_bin,
    "get_folder_size": get_folder_size,
    "show_notification": show_notification,
    "wake_on_lan": wake_on_lan,
    # Clavier
    "type_text": type_text,
    "press_key": press_key,
    # Presse-papiers
    "get_clipboard": get_clipboard,
    "set_clipboard": set_clipboard,
    "get_clipboard_history": get_clipboard_history,
    "paste_from_history": paste_from_history,
    "clear_clipboard_history": clear_clipboard_history,
    # Date / heure
    "get_local_time": get_local_time,
    "get_local_date": get_local_date,
    "get_datetime": get_datetime,
    "days_until": days_until,
    # Minuteurs
    "set_timer": set_timer,
    "list_timers": list_timers,
    "cancel_timer": cancel_timer,
    # Notes
    "take_note": take_note,
    "read_notes": read_notes,
    "delete_notes": delete_notes,
    # Liste de taches
    "add_todo": add_todo,
    "list_todos": list_todos,
    "complete_todo": complete_todo,
    "reopen_todo": reopen_todo,
    "delete_todo": delete_todo,
    "clear_todos": clear_todos,
    # Journal d'activite & sauvegarde
    "get_activity_log": get_activity_log,
    "clear_activity_log": clear_activity_log,
    "backup_data": backup_data,
    "list_backups": list_backups,
    # Mémoire persistante
    "remember": remember,
    "recall": recall,
    "list_memories": list_memories,
    "search_memories": search_memories,
    "update_memory": update_memory,
    "delete_memory": delete_memory,
    "forget": forget,
    "clear_memory": clear_memory,
    # Routines
    "create_routine": create_routine,
    "run_routine": run_routine,
    "list_routines": list_routines,
    "describe_routine": describe_routine,
    "update_routine": update_routine,
    "delete_routine": delete_routine,
    "list_routine_tools": list_routine_tools,
    # Rappels persistants
    "set_reminder": set_reminder,
    "list_reminders": list_reminders,
    "cancel_reminder": cancel_reminder,
    # Web
    "open_website": open_website,
    "list_websites": list_websites,
    "open_url": open_url,
    "web_search": web_search,
    "search_youtube": search_youtube,
    "search_wikipedia": search_wikipedia,
    "open_maps": open_maps,
    "get_directions": get_directions,
    "translate_text": translate_text,
    "get_weather": get_weather,
    "get_forecast": get_forecast,
    "find_something_to_watch": find_something_to_watch,
    "draft_email": draft_email,
    "check_internet": check_internet,
    # Fichiers
    "open_folder": open_folder,
    "list_folder": list_folder,
    "search_files": search_files,
    "read_file_aloud": read_file_aloud,
    # Calcul & divers
    "calculate": calculate,
    "random_number": random_number,
    "flip_coin": flip_coin,
    "roll_dice": roll_dice,
    "pick_random": pick_random,
}


# ---------------------------------------------------------------------------
# Journal d'activité : chaque outil appelé est consigné localement.
# ---------------------------------------------------------------------------


def _with_activity_log(name, function):
    """Enveloppe un outil pour consigner son appel dans le journal local."""

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        try:
            result = function(*args, **kwargs)
        except Exception as exc:
            log_action(name, kwargs, success=False, error=str(exc))
            raise
        try:
            if isinstance(result, dict):
                log_action(
                    name,
                    kwargs,
                    success=bool(result.get("success", True)),
                    error=str(result.get("error", "")),
                )
            else:  # pragma: no cover - tous les outils renvoient un dict
                log_action(name, kwargs, success=True)
        except Exception:
            pass
        return result

    return wrapper


#: Outils exposés à Gemini (identiques aux implémentations, mais journalisés).
TOOL_FUNCTIONS = {
    name: _with_activity_log(name, function)
    for name, function in _TOOL_IMPLEMENTATIONS.items()
}


def _decl(name, description, properties=None, required=None):
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties or {},
            "required": required or [],
        },
    }


_STR = {"type": "string"}
_INT = {"type": "integer"}
_BOOL = {"type": "boolean"}


TOOL_DECLARATIONS = [
    # --- Applications ---------------------------------------------------
    _decl(
        "open_application",
        "Ouvre une application autorisee du PC (bloc-notes, calculatrice, chrome, spotify, vscode...).",
        {"application": {**_STR, "description": "Nom de l'application a ouvrir."}},
        ["application"],
    ),
    _decl(
        "close_application",
        "Ferme une application autorisee du PC.",
        {"application": {**_STR, "description": "Nom de l'application a fermer."}},
        ["application"],
    ),
    _decl(
        "is_application_running",
        "Indique si une application autorisee est actuellement lancee.",
        {"application": _STR},
        ["application"],
    ),
    _decl("list_applications", "Liste les applications autorisees."),
    _decl(
        "list_running_applications",
        "Liste les fenetres/applications actuellement ouvertes.",
        {"limit": {**_INT, "description": "Nombre maximum de resultats."}},
    ),
    # --- Audio -------------------------------------------------------------
    _decl("set_volume", "Regle le volume principal de 0 a 100.", {"volume": _INT}, ["volume"]),
    _decl("volume_up", "Augmente le volume.", {"amount": _INT}),
    _decl("volume_down", "Baisse le volume.", {"amount": _INT}),
    _decl("get_volume", "Donne le volume actuel et l'etat muet."),
    _decl("mute_audio", "Coupe le son."),
    _decl("unmute_audio", "Retablit le son."),
    _decl("toggle_mute", "Inverse l'etat muet du son."),
    # --- Multimédia ---------------------------------------------------------
    _decl("media_play_pause", "Lance ou met en pause la lecture multimedia en cours."),
    _decl("media_next", "Passe a la piste suivante."),
    _decl("media_previous", "Revient a la piste precedente."),
    _decl("media_stop", "Arrete la lecture multimedia."),
    # --- Système -------------------------------------------------------------
    _decl("get_system_info", "Donne les informations systeme (OS, CPU, RAM, disque)."),
    _decl("get_battery_status", "Donne le niveau de batterie et l'etat de charge."),
    _decl(
        "get_disk_usage",
        "Donne l'espace disque total, utilise et libre.",
        {"drive": {**_STR, "description": "Lecteur, par exemple C:\\"}},
    ),
    _decl(
        "take_screenshot",
        "Capture l'ecran et enregistre l'image dans le dossier Images.",
        {"folder": {**_STR, "description": "Dossier de destination optionnel."}},
    ),
    _decl("lock_workstation", "Verrouille la session Windows."),
    _decl("show_desktop", "Reduit toutes les fenetres pour afficher le bureau."),
    _decl(
        "shutdown_pc",
        "Eteint le PC. Demande TOUJOURS une confirmation orale avant d'appeler avec confirm=true.",
        {"delay_seconds": _INT, "confirm": _BOOL},
    ),
    _decl(
        "restart_pc",
        "Redemarre le PC. Demande TOUJOURS une confirmation orale avant d'appeler avec confirm=true.",
        {"delay_seconds": _INT, "confirm": _BOOL},
    ),
    _decl("cancel_shutdown", "Annule un arret ou un redemarrage programme."),
    _decl(
        "set_brightness",
        "Regle la luminosite de l'ecran de 0 a 100.",
        {"level": _INT},
        ["level"],
    ),
    _decl(
        "sleep_pc",
        "Met le PC en veille ('mets le PC en veille'). La session reste ouverte.",
    ),
    _decl(
        "hibernate_pc",
        "Met le PC en hibernation : l'etat est enregistre sur le disque puis le PC s'eteint.",
    ),
    _decl(
        "turn_off_screen",
        "Eteint l'ecran sans verrouiller la session ('eteins l'ecran'). Un mouvement de souris le rallume.",
    ),
    _decl(
        "empty_recycle_bin",
        "Vide la corbeille Windows. Action irreversible : demande TOUJOURS une confirmation orale avant confirm=true.",
        {"confirm": _BOOL},
    ),
    _decl(
        "get_folder_size",
        "Indique le poids d'un dossier autorise et ses plus gros elements ('combien pese Telechargements ?').",
        {"folder": {**_STR, "description": "documents, telechargements, bureau, images, musique, videos."}},
    ),
    _decl(
        "show_notification",
        "Affiche une notification Windows (toast) en complement de la voix, utile pour un rappel ou la fin d'un minuteur.",
        {"title": _STR, "message": _STR, "duration": _INT},
        ["message"],
    ),
    _decl(
        "wake_on_lan",
        "Reveille un autre ordinateur du reseau local via son adresse MAC (Wake-on-LAN).",
        {
            "mac": {**_STR, "description": "Adresse MAC, par exemple AA:BB:CC:DD:EE:FF."},
            "broadcast": _STR,
            "port": _INT,
        },
        ["mac"],
    ),
    # --- Clavier -----------------------------------------------------------------
    _decl(
        "type_text",
        "Tape un texte au clavier dans la fenetre active, comme si l'utilisateur l'ecrivait "
        "('ecris bonjour dans le champ').",
        {"text": _STR},
        ["text"],
    ),
    _decl(
        "press_key",
        "Appuie sur une touche du clavier, avec modificateurs optionnels ('appuie sur entree', 'fais ctrl+s').",
        {
            "key": {**_STR, "description": "Touche : lettre, chiffre, F1-F12, entree, tab, espace, echap, suppr, fleches... Accepte aussi 'ctrl+s'."},
            "modifiers": {**_STR, "description": "Modificateurs separes par + : ctrl, alt, maj, win."},
            "repeat": _INT,
        },
        ["key"],
    ),
    # --- Presse-papiers ---------------------------------------------------------
    _decl("get_clipboard", "Lit le texte contenu dans le presse-papiers."),
    _decl("set_clipboard", "Copie un texte dans le presse-papiers.", {"text": _STR}, ["text"]),
    _decl(
        "get_clipboard_history",
        "Liste les derniers textes copies dans le presse-papiers ('qu'est-ce que j'ai copie avant ?').",
        {"limit": _INT},
    ),
    _decl(
        "paste_from_history",
        "Remet une entree de l'historique du presse-papiers ('recolle ce que j'avais copie avant'). index=1 est la plus recente.",
        {"index": _INT, "paste": {**_BOOL, "description": "true pour coller directement avec ctrl+v."}},
    ),
    _decl(
        "clear_clipboard_history",
        "Efface l'historique du presse-papiers. Demande une confirmation orale avant confirm=true.",
        {"confirm": _BOOL},
    ),
    # --- Date / heure ------------------------------------------------------------
    _decl("get_local_time", "Donne l'heure locale."),
    _decl("get_local_date", "Donne la date locale et le jour de la semaine."),
    _decl("get_datetime", "Donne la date et l'heure completes."),
    _decl(
        "days_until",
        "Calcule le nombre de jours jusqu'a une date donnee (JJ/MM/AAAA).",
        {"date": _STR},
        ["date"],
    ),
    # --- Minuteurs ------------------------------------------------------------------
    _decl(
        "set_timer",
        "Programme un minuteur qui sonne a la fin du delai.",
        {"seconds": _INT, "minutes": _INT, "label": _STR},
    ),
    _decl("list_timers", "Liste les minuteurs en cours."),
    _decl("cancel_timer", "Annule un minuteur (tous si aucun id).", {"timer_id": _INT}),
    # --- Notes --------------------------------------------------------------------------
    _decl("take_note", "Enregistre une note datee pour l'utilisateur.", {"text": _STR}, ["text"]),
    _decl("read_notes", "Relit les dernieres notes enregistrees.", {"limit": _INT}),
    _decl("delete_notes", "Efface toutes les notes (confirmation requise).", {"confirm": _BOOL}),
    # --- Liste de taches (todo) ------------------------------------------------------------
    _decl(
        "add_todo",
        "Ajoute une tache a la liste de choses a faire ('ajoute reviser la presentation a ma todo'). "
        "Contrairement a une note, une tache a un etat fait / a faire.",
        {
            "text": {**_STR, "description": "Libelle de la tache."},
            "due": {**_STR, "description": "Echeance optionnelle : 'demain a 9h', 'vendredi', '12/03/2026'."},
            "priority": {**_STR, "description": "haute, normale ou basse."},
        },
        ["text"],
    ),
    _decl(
        "list_todos",
        "Liste les taches ('qu'est-ce qu'il me reste a faire ?').",
        {
            "status": {**_STR, "description": "pending (par defaut), done ou all."},
            "limit": _INT,
        },
    ),
    _decl(
        "complete_todo",
        "Marque une tache comme faite, par identifiant ou par libelle ('marque la presentation comme faite').",
        {"todo_id": _INT, "text": _STR},
    ),
    _decl(
        "reopen_todo",
        "Remet une tache terminee dans les choses a faire.",
        {"todo_id": _INT, "text": _STR},
    ),
    _decl(
        "delete_todo",
        "Supprime definitivement une tache de la liste.",
        {"todo_id": _INT, "text": _STR},
    ),
    _decl(
        "clear_todos",
        "Vide la liste de taches. Utilise only_done=true pour ne retirer que les taches faites, "
        "sinon demande une confirmation orale avant confirm=true.",
        {"confirm": _BOOL, "only_done": _BOOL},
    ),
    # --- Journal d'activite & sauvegarde ----------------------------------------------------
    _decl(
        "get_activity_log",
        "Raconte les actions realisees par Jarvis ('qu'as-tu fait aujourd'hui ?').",
        {
            "day": {**_STR, "description": "aujourd'hui (par defaut), hier, avant-hier ou une date JJ/MM/AAAA."},
            "limit": _INT,
        },
    ),
    _decl(
        "clear_activity_log",
        "Efface le journal d'activite local. Demande une confirmation orale avant confirm=true.",
        {"confirm": _BOOL},
    ),
    _decl(
        "backup_data",
        "Sauvegarde memoire, routines, taches, rappels et notes dans un fichier JSON local "
        "('sauvegarde ta memoire').",
        {"destination": {**_STR, "description": "Dossier ou fichier de destination, optionnel."}},
    ),
    _decl("list_backups", "Liste les sauvegardes deja realisees.", {"folder": _STR}),
    # --- Mémoire persistante ---------------------------------------------------------------
    _decl(
        "remember",
        "Enregistre une information durable importante sur l'utilisateur dans la memoire locale. A utiliser pour 'souviens-toi', preferences, identite, projets, decisions ou configurations importantes.",
        {
            "content": {**_STR, "description": "Souvenir clair et autonome, reformule sans bruit."},
            "category": {"type": "string", "description": "identity, preference, person, project, configuration, fact, habit, decision ou other."},
            "importance": {**_INT, "description": "Importance de 1 a 5."},
        },
        ["content"],
    ),
    _decl(
        "recall",
        "Recherche quelques souvenirs pertinents dans la memoire locale avant de repondre a une question personnelle ou contextuelle.",
        {"query": _STR, "limit": _INT},
    ),
    _decl(
        "list_memories",
        "Liste les souvenirs en memoire quand l'utilisateur demande ce que Jarvis sait de lui.",
        {"limit": _INT, "category": _STR},
    ),
    _decl(
        "search_memories",
        "Recherche explicitement dans la memoire locale.",
        {"query": _STR, "limit": _INT},
        ["query"],
    ),
    _decl(
        "update_memory",
        "Modifie un souvenir existant par identifiant.",
        {"memory_id": _INT, "content": _STR, "category": _STR, "importance": _INT},
        ["memory_id"],
    ),
    _decl(
        "delete_memory",
        "Supprime un souvenir precis par identifiant.",
        {"memory_id": _INT},
        ["memory_id"],
    ),
    _decl(
        "forget",
        "Oublie les souvenirs correspondant a une requete. Demande une confirmation orale avant confirm=true.",
        {"query": _STR, "confirm": _BOOL, "limit": _INT},
        ["query"],
    ),
    _decl(
        "clear_memory",
        "Efface toute la memoire durable. Demande TOUJOURS une confirmation orale avant confirm=true.",
        {"confirm": _BOOL},
    ),
    # --- Routines --------------------------------------------------------------------------
    _decl(
        "create_routine",
        "Cree une routine : un enchainement d'outils nomme et rejouable (par exemple 'mode travail'). "
        "Les etapes s'ecrivent sous forme d'appels separes par des points-virgules, "
        "par exemple : open_application(vscode); wait(2); set_volume(30); open_website(spotify). "
        "Seuls les outils autorises sont acceptes ; appelle list_routine_tools en cas de doute.",
        {
            "name": {**_STR, "description": "Nom parle de la routine, par exemple 'mode travail'."},
            "steps": {
                **_STR,
                "description": "Etapes separees par des points-virgules : outil(argument) ; outil(cle=valeur).",
            },
            "description": {**_STR, "description": "Courte description optionnelle."},
            "schedule": {
                **_STR,
                "description": "Declenchement automatique optionnel : 'tous les jours a 9h', 'en semaine a 8h30', 'lundi et vendredi a 18h'.",
            },
        },
        ["name", "steps"],
    ),
    _decl(
        "run_routine",
        "Execute une routine existante quand l'utilisateur la demande ('lance le mode travail').",
        {"name": _STR},
        ["name"],
    ),
    _decl("list_routines", "Liste les routines enregistrees et leur planification."),
    _decl(
        "describe_routine",
        "Detaille les etapes et la planification d'une routine.",
        {"name": _STR},
        ["name"],
    ),
    _decl(
        "update_routine",
        "Modifie une routine existante : etapes, description, planification ou activation. "
        "Utilise schedule='aucune' pour retirer un declenchement automatique.",
        {
            "name": _STR,
            "steps": _STR,
            "description": _STR,
            "schedule": _STR,
            "enabled": _BOOL,
        },
        ["name"],
    ),
    _decl(
        "delete_routine",
        "Supprime une routine. Demande une confirmation orale avant d'appeler avec confirm=true.",
        {"name": _STR, "confirm": _BOOL},
        ["name"],
    ),
    _decl(
        "list_routine_tools",
        "Liste les outils utilisables comme etape d'une routine, et ceux qui sont interdits.",
    ),
    # --- Rappels persistants ---------------------------------------------------------------
    _decl(
        "set_reminder",
        "Programme un rappel durable qui survit au redemarrage du PC, contrairement a set_timer. "
        "A utiliser pour 'rappelle-moi demain a 9h', 'chaque lundi a 8h'. "
        "Pour un simple compte a rebours de quelques minutes, prefere set_timer.",
        {
            "text": {**_STR, "description": "Contenu du rappel, par exemple 'appeler Paul'."},
            "when": {
                **_STR,
                "description": "Echeance : 'demain a 9h', 'dans 20 minutes', 'lundi a 8h30', '12/03/2026 a 14h' ou une date ISO.",
            },
            "recurrence": {
                **_STR,
                "description": "Optionnel : daily, weekdays, weekends, weekly, monthly, hourly.",
            },
            "routine": {
                **_STR,
                "description": "Optionnel : nom d'une routine a executer a l'echeance au lieu d'annoncer un texte.",
            },
        },
        ["text"],
    ),
    _decl("list_reminders", "Liste les rappels en attente.", {"limit": _INT}),
    _decl(
        "cancel_reminder",
        "Annule un rappel par identifiant, ou tous les rappels avec confirm=true apres confirmation orale.",
        {"reminder_id": _INT, "confirm": _BOOL},
    ),
    # --- Web ------------------------------------------------------------------------------
    _decl(
        "open_website",
        "Ouvre un site de la liste blanche (youtube, wikipedia, gmail, github, netflix...).",
        {"site": {**_STR, "description": "Nom du site a ouvrir."}},
        ["site"],
    ),
    _decl("list_websites", "Liste les sites autorises."),
    _decl(
        "open_url",
        "Ouvre une URL precise si son domaine est autorise.",
        {"url": _STR},
        ["url"],
    ),
    _decl(
        "web_search",
        "Recherche sur le Web dans le navigateur.",
        {
            "query": _STR,
            "engine": {
                "type": "string",
                "description": "Moteur : google, bing, duckduckgo, qwant, ecosia, youtube, wikipedia, github, images, maps, amazon, stackoverflow.",
            },
        },
        ["query"],
    ),
    _decl("search_youtube", "Recherche une video sur YouTube.", {"query": _STR}, ["query"]),
    _decl("search_wikipedia", "Recherche un article sur Wikipedia.", {"query": _STR}, ["query"]),
    _decl("open_maps", "Affiche un lieu sur Google Maps.", {"place": _STR}, ["place"]),
    _decl(
        "get_directions",
        "Affiche un itineraire vers une destination.",
        {"destination": _STR, "origin": _STR},
        ["destination"],
    ),
    _decl(
        "translate_text",
        "Ouvre Google Traduction avec un texte a traduire.",
        {"text": _STR, "target_language": _STR, "source_language": _STR},
        ["text"],
    ),
    _decl(
        "get_weather",
        "Donne la meteo actuelle d'une ville.",
        {"city": {**_STR, "description": "Ville, Paris par defaut."}},
    ),
    _decl(
        "get_forecast",
        "Donne les previsions meteo des prochains jours (jusqu'a 7) pour une ville.",
        {"city": {**_STR, "description": "Ville, Paris par defaut."}, "days": _INT},
    ),
    _decl(
        "find_something_to_watch",
        "Aide a choisir un film ou une serie et ouvre la fiche ou le catalogue correspondant "
        "('que regarder ce soir ?').",
        {
            "query": {**_STR, "description": "Titre recherche, optionnel."},
            "genre": {**_STR, "description": "Genre souhaite : comedie, thriller, science-fiction..."},
            "service": {**_STR, "description": "Plateforme : netflix, prime video, disney plus, canal, apple tv..."},
        },
    ),
    _decl(
        "draft_email",
        "Redige un brouillon d'email et l'ouvre pre-rempli dans le client de messagerie "
        "('redige un email a Paul pour annuler la reunion'). N'envoie jamais l'email lui-meme.",
        {
            "to": {**_STR, "description": "Adresse(s) du destinataire."},
            "subject": _STR,
            "body": {**_STR, "description": "Corps du message, redige par Jarvis."},
        },
    ),
    _decl("check_internet", "Verifie que la connexion Internet fonctionne."),
    # --- Fichiers ------------------------------------------------------------------------------
    _decl(
        "open_folder",
        "Ouvre un dossier utilisateur autorise (documents, telechargements, bureau, images, musique, videos).",
        {"folder": _STR},
        ["folder"],
    ),
    _decl(
        "list_folder",
        "Liste le contenu d'un dossier utilisateur autorise.",
        {"folder": _STR, "limit": _INT},
        ["folder"],
    ),
    _decl(
        "search_files",
        "Cherche des fichiers par nom dans un dossier autorise.",
        {"pattern": _STR, "folder": _STR, "limit": _INT},
        ["pattern"],
    ),
    _decl(
        "read_file_aloud",
        "Lit le contenu d'un fichier texte d'un dossier autorise pour pouvoir le lire a voix haute "
        "ou le resumer ('lis-moi ce fichier').",
        {
            "name": {**_STR, "description": "Nom du fichier ou chemin complet dans un dossier autorise."},
            "folder": {**_STR, "description": "Dossier ou chercher : documents par defaut."},
            "max_chars": _INT,
        },
        ["name"],
    ),
    # --- Calcul & divers --------------------------------------------------------------------------
    _decl(
        "calculate",
        "Calcule une expression mathematique (operations, racine, puissance, trigonometrie).",
        {"expression": _STR},
        ["expression"],
    ),
    _decl("random_number", "Tire un nombre au hasard entre deux bornes.", {"minimum": _INT, "maximum": _INT}),
    _decl("flip_coin", "Tire a pile ou face."),
    _decl("roll_dice", "Lance un ou plusieurs des.", {"sides": _INT, "count": _INT}),
    _decl(
        "pick_random",
        "Choisit une option au hasard dans une liste.",
        {"options": {"type": "string", "description": "Options separees par des virgules."}},
        ["options"],
    ),
]


def _check_registry():
    """Garde-fou : declarations et implementations doivent correspondre."""
    declared = {d["name"] for d in TOOL_DECLARATIONS}
    implemented = set(TOOL_FUNCTIONS)
    missing = declared - implemented
    extra = implemented - declared
    return missing, extra


_MISSING, _EXTRA = _check_registry()
if _MISSING or _EXTRA:  # pragma: no cover - erreur de developpement
    raise RuntimeError(
        f"Incoherence des outils Jarvis. Non implementes: {sorted(_MISSING)} / "
        f"Non declares: {sorted(_EXTRA)}"
    )


# Les routines reutilisent la boite a outils : on l'enregistre ici plutot que
# de laisser src.routines importer src.tools (dependance circulaire).
try:
    from .routines import set_tool_registry as _set_routine_tool_registry

    _set_routine_tool_registry(TOOL_FUNCTIONS, TOOL_DECLARATIONS)
except Exception:  # pragma: no cover - ne doit jamais bloquer Jarvis
    pass
