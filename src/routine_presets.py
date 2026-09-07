"""Routines préconfigurées livrées avec Jarvis.

Une routine préconfigurée (ou *preset*) est une routine ordinaire, écrite à
l'avance avec les seuls outils de la boîte à outils Jarvis : elle ne peut donc
rien faire de plus que ce que Jarvis sait déjà faire. Elles sont **désactivées
par défaut** : rien ne se déclenche tant que l'utilisateur ne les a pas
activées, et l'activation se fait en un geste (clic sur l'orbe, ou
« Jarvis, active la routine réveil »).

Cycle de vie :

* au premier lancement, les presets manquants sont ajoutés à
  ``~/.jarvis/routines.json`` ;
* un preset déjà présent n'est **jamais** réécrit : l'utilisateur peut le
  renommer, changer ses étapes ou son heure sans rien perdre ;
* un preset supprimé par l'utilisateur n'est pas réinstallé (il est
  mémorisé dans la section ``presets`` du fichier) ;
* l'action « Presets » du menu radial (ou ``install_default_presets(restore=True)``)
  réinstalle les presets manquants, y compris ceux qui avaient été supprimés.

Ce module ne dépend pas de ``src.routines`` (pas d'import circulaire) : il ne
fait que décrire des données et calculer ce qu'il manque.
"""

from __future__ import annotations

from .timeparse import normalize

#: Version du catalogue : incrémentée si la liste des presets change
#: sensiblement (elle est recopiée dans ``routines.json``).
PRESET_VERSION = 1

_EVERY_DAY = list(range(7))  # lundi=0 … dimanche=6
_WEEK_DAYS = list(range(5))  # lundi → vendredi
_FRIDAY_SATURDAY = [4, 5]
_SATURDAY = [5]
_SUNDAY = [6]


def _schedule(time: str, days) -> dict:
    return {"time": time, "days": list(days)}


def _preset(name: str, description: str, time: str, days, steps) -> dict:
    """Décrit un preset. ``steps`` est une liste de ``(outil, arguments)``."""
    return {
        "name": name,
        "description": description,
        "schedule": _schedule(time, days),
        "steps": [{"tool": tool, "args": dict(args or {})} for tool, args in steps],
        "enabled": False,  # rien ne se déclenche sans l'accord de l'utilisateur
    }


#: Catalogue des routines préconfigurées. Chaque entrée est conçue pour être
#: utile telle quelle : il suffit de l'activer.
PRESETS: list[dict] = [
    _preset(
        "réveil",
        "Remet le son et lance une radio pour démarrer la journée.",
        "07:30",
        _EVERY_DAY,
        [
            ("unmute_audio", {}),
            ("set_volume", {"volume": 45}),
            ("open_website", {"site": "radio france"}),
        ],
    ),
    _preset(
        "journal du matin",
        "Ouvre agenda, boîte mail et actualités pour faire le point.",
        "08:00",
        _WEEK_DAYS,
        [
            ("open_website", {"site": "google agenda"}),
            ("wait", {"seconds": 2}),
            ("open_website", {"site": "gmail"}),
            ("wait", {"seconds": 2}),
            ("open_website", {"site": "france info"}),
        ],
    ),
    _preset(
        "mode travail",
        "Poste de code : VS Code, GitHub et un volume calme.",
        "09:00",
        _WEEK_DAYS,
        [
            ("open_application", {"application": "vscode"}),
            ("wait", {"seconds": 3}),
            ("open_website", {"site": "github"}),
            ("set_volume", {"volume": 30}),
        ],
    ),
    _preset(
        "pomodoro",
        "Session de concentration : bureau dégagé, son coupé, minuteur de 50 min.",
        "09:30",
        _WEEK_DAYS,
        [
            ("mute_audio", {}),
            ("show_desktop", {}),
            ("set_timer", {"minutes": 50, "label": "Pomodoro : 50 minutes de focus"}),
        ],
    ),
    _preset(
        "pause café",
        "Coupure courte : son rétabli, bureau dégagé, minuteur de 5 min.",
        "11:00",
        _WEEK_DAYS,
        [
            ("unmute_audio", {}),
            ("show_desktop", {}),
            ("set_timer", {"minutes": 5, "label": "Pause café"}),
        ],
    ),
    _preset(
        "déjeuner",
        "Pause déjeuner : son coupé, bureau dégagé, minuteur de 45 min.",
        "12:30",
        _WEEK_DAYS,
        [
            ("mute_audio", {}),
            ("show_desktop", {}),
            ("set_timer", {"minutes": 45, "label": "Déjeuner"}),
        ],
    ),
    _preset(
        "reprise d'après-midi",
        "Retour de pause : son rétabli et VS Code rouvert.",
        "13:30",
        _WEEK_DAYS,
        [
            ("unmute_audio", {}),
            ("set_volume", {"volume": 35}),
            ("open_application", {"application": "vscode"}),
        ],
    ),
    _preset(
        "bilan du soir",
        "Fin de journée : note les priorités du lendemain et ouvre le tableau de tâches.",
        "17:45",
        _WEEK_DAYS,
        [
            ("show_desktop", {}),
            ("take_note", {"text": "Bilan du soir : noter les 3 priorités de demain"}),
            ("open_website", {"site": "trello"}),
        ],
    ),
    _preset(
        "mode détente",
        "Soirée : ferme la messagerie de travail et ouvre YouTube.",
        "19:30",
        _EVERY_DAY,
        [
            ("close_application", {"application": "teams"}),
            ("close_application", {"application": "slack"}),
            ("wait", {"seconds": 2}),
            ("set_volume", {"volume": 55}),
            ("open_website", {"site": "youtube"}),
        ],
    ),
    _preset(
        "nuit calme",
        "Fin de soirée : lecture arrêtée, volume bas, bureau dégagé.",
        "23:00",
        _EVERY_DAY,
        [
            ("media_stop", {}),
            ("set_volume", {"volume": 10}),
            ("show_desktop", {}),
        ],
    ),
    _preset(
        "mode gaming",
        "Session de jeu : Steam lancé et volume relevé.",
        "21:00",
        _FRIDAY_SATURDAY,
        [
            ("open_application", {"application": "steam"}),
            ("wait", {"seconds": 3}),
            ("set_volume", {"volume": 70}),
        ],
    ),
    _preset(
        "entretien du PC",
        "Ménage du week-end : dossier Téléchargements puis nettoyage de disque.",
        "10:00",
        _SATURDAY,
        [
            ("open_folder", {"folder": "telechargements"}),
            ("wait", {"seconds": 1}),
            ("open_application", {"application": "nettoyage de disque"}),
        ],
    ),
    _preset(
        "préparation de la semaine",
        "Dimanche soir : agenda et tâches pour préparer la semaine.",
        "18:00",
        _SUNDAY,
        [
            ("open_website", {"site": "google agenda"}),
            ("wait", {"seconds": 2}),
            ("open_website", {"site": "trello"}),
            ("take_note", {"text": "Objectifs de la semaine à venir"}),
        ],
    ),
]


def preset_names() -> list[str]:
    """Noms des routines préconfigurées, dans l'ordre du catalogue."""
    return [preset["name"] for preset in PRESETS]


def find_preset(name: str) -> dict | None:
    """Retrouve un preset par son nom (recherche tolérante)."""
    target = normalize(name)
    if not target:
        return None
    for preset in PRESETS:
        if normalize(preset["name"]) == target:
            return preset
    for preset in PRESETS:
        candidate = normalize(preset["name"])
        if candidate and (candidate in target or target in candidate):
            return preset
    return None


def as_routine(preset: dict) -> dict:
    """Convertit un preset en routine prête à être stockée dans le fichier."""
    return {
        "name": preset["name"],
        "description": preset["description"],
        "enabled": False,
        "schedule": dict(preset["schedule"]),
        "steps": [{"tool": step["tool"], "args": dict(step["args"])} for step in preset["steps"]],
        "last_run": None,
        "run_count": 0,
    }


def sync_presets(payload: dict, restore: bool = False) -> tuple[list[dict], dict]:
    """Calcule les presets à ajouter à ``payload``.

    Renvoie ``(routines_a_ajouter, meta_presets)``. La fonction est pure : elle
    ne modifie pas ``payload``.

    Règles : un preset déjà présent dans les routines est laissé tranquille ;
    un preset supprimé par l'utilisateur n'est pas réinstallé, sauf si
    ``restore`` est vrai.
    """
    routines = payload.get("routines")
    existing = {
        normalize(str(item.get("name") or ""))
        for item in (routines or [])
        if isinstance(item, dict)
    }
    existing.discard("")

    meta = payload.get("presets") if isinstance(payload.get("presets"), dict) else {}
    installed = {normalize(str(name)) for name in (meta.get("installed") or [])}
    dismissed = {normalize(str(name)) for name in (meta.get("dismissed") or [])}
    installed.discard("")
    dismissed.discard("")

    to_add: list[dict] = []
    for preset in PRESETS:
        key = normalize(preset["name"])
        if key in existing:
            # Déjà là (éventuellement modifié par l'utilisateur) : on ne touche à rien.
            installed.add(key)
            dismissed.discard(key)
            continue
        if key in dismissed and not restore:
            continue
        if key in installed and not restore:
            # Installé puis supprimé : l'utilisateur l'a fait exprès.
            installed.discard(key)
            dismissed.add(key)
            continue
        to_add.append(as_routine(preset))
        installed.add(key)
        dismissed.discard(key)

    new_meta = {
        "version": PRESET_VERSION,
        "installed": sorted(installed),
        "dismissed": sorted(dismissed),
    }
    return to_add, new_meta
