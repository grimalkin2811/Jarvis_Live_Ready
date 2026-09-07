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

from .memory import get_default_memory_manager
from .routines import get_default_routine_manager
from .routine_actions import (
    notify_user, show_reminder_briefing, check_battery_alert, check_disk_alert,
)
from .scheduler import get_default_scheduler

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


# ===========================================================================
# PRESSE-PAPIERS
# ===========================================================================


def get_clipboard():
    """Lit le contenu texte du presse-papiers."""
    if not IS_WINDOWS:
        return _windows_only()
    ok, out = _powershell("Get-Clipboard -Raw")
    if not ok:
        return _err(out)
    return _ok(texte=out)


def set_clipboard(text):
    """Copie un texte dans le presse-papiers."""
    if not IS_WINDOWS:
        return _windows_only()
    content = str(text or "")
    if not content:
        return _err("Texte vide")
    escaped = content.replace("'", "''")
    ok, out = _powershell(f"Set-Clipboard -Value '{escaped}'")
    return _ok(longueur=len(content)) if ok else _err(out)


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

TOOL_FUNCTIONS = {
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
    # Presse-papiers
    "get_clipboard": get_clipboard,
    "set_clipboard": set_clipboard,
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
    # Notifications et routines préconfigurées
    "notify_user": notify_user,
    "show_reminder_briefing": show_reminder_briefing,
    "check_battery_alert": check_battery_alert,
    "check_disk_alert": check_disk_alert,
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
    "check_internet": check_internet,
    # Fichiers
    "open_folder": open_folder,
    "list_folder": list_folder,
    "search_files": search_files,
    # Calcul & divers
    "calculate": calculate,
    "random_number": random_number,
    "flip_coin": flip_coin,
    "roll_dice": roll_dice,
    "pick_random": pick_random,
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
    # --- Presse-papiers ---------------------------------------------------------
    _decl("get_clipboard", "Lit le texte contenu dans le presse-papiers."),
    _decl("set_clipboard", "Copie un texte dans le presse-papiers.", {"text": _STR}, ["text"]),
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
    # --- Notifications locales ------------------------------------------------------------
    _decl(
        "notify_user", "Affiche immediatement une notification locale (sans rappel differe).",
        {"message": _STR, "title": _STR}, ["message"],
    ),
    _decl(
        "show_reminder_briefing",
        "Affiche un resume des rappels Jarvis a venir (pas un agenda externe). "
        "Fonctionne meme sans rappel enregistre.",
        {"period": {**_STR, "enum": ["today", "tomorrow", "week"]}},
    ),
    _decl(
        "check_battery_alert",
        "Notifie si la batterie est a 20 % ou moins, non branchee, au plus une fois par heure. "
        "Reste silencieux sans batterie ou si elle est en charge.",
    ),
    _decl(
        "check_disk_alert",
        "Notifie si le disque du dossier utilisateur a moins de 10 % libres, au plus une fois "
        "par jour. Ne supprime aucun fichier.",
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
    _decl("list_routines", "Liste les routines personnelles et les 10 routines preconfigurees, avec leur activation et planification."),
    _decl(
        "describe_routine",
        "Detaille les etapes et la planification d'une routine.",
        {"name": _STR},
        ["name"],
    ),
    _decl(
        "update_routine",
        "Modifie une routine existante : etapes, description, planification ou activation. "
        "Pour activer/desactiver une routine preconfiguree, passe uniquement name et enabled=true/false : "
        "ses horaires et actions sont deja prets. Utilise schedule='aucune' pour retirer une planification.",
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
