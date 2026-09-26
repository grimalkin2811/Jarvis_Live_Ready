"""Catalogue générique d'applications pour les modes focus / jeu.

Les modes focus et jeu ferment, à leur activation, les applications que
**l'utilisateur** a sélectionnées (config persistante, voir
``src/modes.py`` — clés ``focus_apps`` / ``game_apps`` de ``mode.json``).

Ce module ne code aucune règle par utilisateur : il fournit

* un **catalogue générique** d'applications courantes (navigateurs,
  messageries, multimédia, bureautique, jeux…) avec leurs images de
  processus Windows (nombres d'``.exe`` réels, indépendants du PC ou de
  l'utilisateur) ;
* la **résolution de nom** (« Opera GX », « opera gx », « op »…) ;
* les **listes par défaut** de chaque mode, dérivées des comportements
  historiques de Jarvis (les fichiers d'ancienne configuration conservent
  donc exactement le comportement d'avant) ;
* la **translation application → images de processus** utilisée pour la
  fermeture (best effort, Windows uniquement, sans jamais lever).

Une application non présente au catalogue reste utilisable : elle est
conservée « sur mesure » dans la configuration et fermée par heuristique
(image ``<nom>.exe`` puis titre de fenêtre).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

MODE_GAME = "game"
MODE_FOCUS = "focus"

#: Noms acceptés pour désigner un mode (voix, outils, JSON).
MODE_NAME_ALIASES = {
    "game": MODE_GAME,
    "jeu": MODE_GAME,
    "mode jeu": MODE_GAME,
    "gaming": MODE_GAME,
    "focus": MODE_FOCUS,
    "revision": MODE_FOCUS,
    "révision": MODE_FOCUS,
    "concentration": MODE_FOCUS,
    "mode focus": MODE_FOCUS,
    "mode revision": MODE_FOCUS,
}


@dataclass(frozen=True)
class KnownApp:
    """Application connue : identifiant stable, libellé affiché et images
    de processus Windows candidates (le premier match ferme l'application ;
    une application avec plusieurs fenêtres = un processus = tout est fermé,
    ce qui correspond au comportement historique de Jarvis)."""

    id: str
    label: str
    processes: tuple[str, ...]


def _app(id: str, label: str, *processes: str) -> KnownApp:
    return KnownApp(id=id, label=label, processes=tuple(processes))


#: Catalogue générique — aucune connaissance de l'utilisateur ici.
#: Les identifiants servent de clés stables ; les images de processus sont
#: réelles et insensibles à la casse (taskkill /IM).
KNOWN_APPS: tuple[KnownApp, ...] = (
    # Navigateurs
    _app("chrome", "Chrome", "chrome.exe"),
    _app("edge", "Edge", "msedge.exe"),
    _app("firefox", "Firefox", "firefox.exe"),
    _app("brave", "Brave", "brave.exe"),
    _app("opera", "Opera", "opera.exe"),
    _app("opera_gx", "Opera GX", "operagx.exe"),
    # Messagerie / réunions
    _app("discord", "Discord", "discord.exe", "discordptb.exe", "discordcanary.exe"),
    _app("teams", "Microsoft Teams", "teams.exe", "ms-teams.exe"),
    _app("slack", "Slack", "slack.exe"),
    _app("zoom", "Zoom", "zoom.exe"),
    _app("telegram", "Telegram", "telegram.exe"),
    _app("whatsapp", "WhatsApp", "whatsapp.exe"),
    # Multimédia
    _app("spotify", "Spotify", "spotify.exe"),
    _app("vlc", "VLC", "vlc.exe"),
    _app("windows_media", "Lecteur Windows Media", "wmplayer.exe"),
    # Bureautique
    _app("word", "Word", "winword.exe"),
    _app("excel", "Excel", "excel.exe"),
    _app("powerpoint", "PowerPoint", "powerpnt.exe"),
    _app("outlook", "Outlook", "outlook.exe"),
    _app("onenote", "OneNote", "onenote.exe"),
    # Développement
    _app("vscode", "VS Code", "code.exe"),
    _app("pycharm", "PyCharm", "pycharm64.exe", "pycharm.exe"),
    # Jeux / launchers
    _app("steam", "Steam", "steam.exe"),
    _app("epic_games", "Epic Games", "epicgameslauncher.exe"),
    _app("battle_net", "Battle.net", "battle.net.exe", "battle_net.exe"),
    _app("riot", "Riot Client", "riotclientservices.exe", "riotclient.exe"),
    _app("ubisoft", "Ubisoft Connect", "ubisoftconnect.exe"),
    _app("ea_desktop", "EA Desktop", "eadesktop.exe"),
    _app("xbox", "Xbox", "xboxpcapp.exe"),
)


def normalize_app_name(value) -> str:
    """Forme canonique d'un nom d'application (matching insensible à la
    casse, aux accents et à la ponctuation)."""
    text = unicodedata.normalize("NFD", str(value or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


_APPS_BY_ID = {app.id: app for app in KNOWN_APPS}
_APPS_BY_NORMALIZED_NAME: dict[str, KnownApp] = {}
for _app_entry in KNOWN_APPS:
    _APPS_BY_NORMALIZED_NAME.setdefault(normalize_app_name(_app_entry.id), _app_entry)
    _APPS_BY_NORMALIZED_NAME.setdefault(normalize_app_name(_app_entry.label), _app_entry)


def find_app(name) -> KnownApp | None:
    """Résout un nom donné par l'utilisateur vers le catalogue.

    Ordre : identité exacte (id ou libellé, formes canoniques), puis
    inclusion par mots entiers. « opera » → Opera, « opera gx » → Opera GX.
    Retourne ``None`` quand le nom ne correspond à aucune application connue
    (il restera traitable comme application « sur mesure »).
    """
    query = normalize_app_name(name)
    if not query:
        return None
    if query in _APPS_BY_NORMALIZED_NAME:
        return _APPS_BY_NORMALIZED_NAME[query]
    candidates = [
        app
        for app in KNOWN_APPS
        if re.search(
            rf"(?<![a-z0-9]){re.escape(normalize_app_name(app.id))}(?![a-z0-9])", query
        )
        or re.search(
            rf"(?<![a-z0-9]){re.escape(normalize_app_name(app.label))}(?![a-z0-9])", query
        )
    ]
    if not candidates:
        return None
    # Le candidat le plus court gagne : « opera » ne doit pas capturer
    # « opera gx », et une mention ambiguë choisit le nom le plus proche.
    return min(candidates, key=lambda app: len(normalize_app_name(app.label)))


def app_label(name) -> str:
    """Libellé affiché d'une entrée de configuration (catalogue ou sur mesure)."""
    known = find_app(name)
    if known is not None:
        return known.label
    text = str(name or "").strip()
    return text or str(name)


def process_images_for(name) -> list[str]:
    """Images de processus à fermer pour une entrée de configuration.

    Catalogue : les images réelles de l'application. Sur mesure :
    ``<nom canonique>.exe`` (best effort ; la fermeture essaie ensuite le
    titre de fenêtre).
    """
    known = find_app(name)
    if known is not None:
        return list(known.processes)
    base = normalize_app_name(name)
    if base:
        return [base.replace(" ", "") + ".exe"]
    return []


def is_known_app(name) -> bool:
    return find_app(name) is not None


def normalize_mode_name(mode) -> str | None:
    """« jeu », « mode jeu », « game »… → ``game`` / ``focus`` (sinon None)."""
    key = normalize_app_name(mode)
    if not key:
        return None
    if key in MODE_NAME_ALIASES:
        return MODE_NAME_ALIASES[key]
    return None


def valid_mode_name(mode) -> str | None:
    """Valide un mode : ``game``/``focus`` directs ou alias français/anglais."""
    mode_key = str(mode or "").strip().lower()
    if mode_key in (MODE_GAME, MODE_FOCUS):
        return mode_key
    return normalize_mode_name(mode)


# ---------------------------------------------------------------------------
# Comportements historiques (v1.x) — valeurs par défaut de la nouvelle
# configuration : une installation existante conserve exactement ce que
# Jarvis fermait avant que le réglage ne devienne personnalisable.
# ---------------------------------------------------------------------------

#: Images fermées par le mode jeu avant personnalisation (v1.x).
LEGACY_GAME_IMAGES: frozenset[str] = frozenset(
    image.lower()
    for image in (
        "chrome.exe",
        "msedge.exe",
        "firefox.exe",
        "brave.exe",
        "opera.exe",
        "discord.exe",
        "teams.exe",
        "ms-teams.exe",
        "slack.exe",
        "zoom.exe",
        "telegram.exe",
        "whatsapp.exe",
        "spotify.exe",
        "vlc.exe",
        "wmplayer.exe",
        "winword.exe",
        "excel.exe",
        "powerpnt.exe",
        "outlook.exe",
        "onenote.exe",
        "code.exe",
        "pycharm64.exe",
    )
)

#: Images fermées par le mode focus avant personnalisation (v1.x).
LEGACY_FOCUS_IMAGES: frozenset[str] = frozenset(
    image.lower()
    for image in (
        "discord.exe",
        "spotify.exe",
        "vlc.exe",
        "wmplayer.exe",
        "steam.exe",
        "telegram.exe",
        "whatsapp.exe",
        "teams.exe",
        "ms-teams.exe",
        "slack.exe",
        "zoom.exe",
        "epicgameslauncher.exe",
        "battle.net.exe",
        "riotclientservices.exe",
        "ubisoftconnect.exe",
        "eadesktop.exe",
        "xboxpcapp.exe",
    )
)


def default_apps_for(mode: str) -> list[str]:
    """Liste par défaut (libellés) d'un mode : les applications du catalogue
    dont au moins une image de processus figurait dans le comportement
    historique. Garantit la compatibilité avec les configurations v1.x."""
    legacy = LEGACY_GAME_IMAGES if mode == MODE_GAME else LEGACY_FOCUS_IMAGES
    labels = [
        app.label
        for app in KNOWN_APPS
        if any(process.lower() in legacy for process in app.processes)
    ]
    return labels


def sanitize_app_list(raw) -> list[str]:
    """Nettoie une liste lue du JSON : strings non vides, sans doublons
    (comparaison canonique), dans l'ordre d'apparition. Aucune exception."""
    seen: set[str] = set()
    result: list[str] = []
    if not isinstance(raw, (list, tuple)):
        return result
    for entry in raw:
        if not isinstance(entry, (str, int, float)):
            continue
        text = str(entry).strip()
        if not text:
            continue
        key = normalize_app_name(text)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result
