"""Catalogue livré avec Jarvis : routines locales, prêtes à activer.

Aucun chemin personnel à renseigner. Les macros manuelles indiquent leurs
prérequis (Windows, VS Code, navigateur) et leurs effets dans leur description.
Les identifiants stables servent à la migration ; les noms restent libres et prononçables.
Les plages horaires suivent l'heure locale du PC, borne de fin incluse.
"""

from __future__ import annotations

from copy import deepcopy


def _schedule(time: str, *, weekdays=False, end=None, interval=None) -> dict:
    schedule = {"time": time, "days": list(range(5 if weekdays else 7))}
    if interval is not None:
        schedule.update(end_time=end, interval_minutes=interval)
    return schedule


def _notification(title: str, message: str) -> list[dict]:
    return [{"tool": "notify_user", "args": {"title": title, "message": message}}]


_PRESETS = (
    {
        "preset_id": "morning_briefing",
        "name": "Briefing du matin",
        "description": "Affiche la date et les prochains rappels Jarvis de la journée. "
        "Indique aussi quand aucun rappel n'est prévu ; aucun agenda externe requis.",
        "schedule": _schedule("09:00", weekdays=True),
        "steps": [{"tool": "show_reminder_briefing", "args": {"period": "today"}}],
    },
    {
        "preset_id": "hydration",
        "name": "Hydratation",
        "description": "Une notification pour penser à boire un verre d'eau, "
        "toutes les deux heures de 10 h à 18 h.",
        "schedule": _schedule("10:00", end="18:00", interval=120),
        "steps": _notification(
            "Hydratation",
            "Pensez à boire un verre d'eau. Une petite pause avant de reprendre ?",
        ),
    },
    {
        "preset_id": "eye_break",
        "name": "Pause visuelle",
        "description": "Invite à regarder au loin pendant 20 secondes, toutes les "
        "20 minutes en semaine, de 9 h 20 à 17 h 40.",
        "schedule": _schedule("09:20", weekdays=True, end="17:40", interval=20),
        "steps": _notification(
            "Pause visuelle",
            "Reposez vos yeux : regardez au loin "
            "pendant 20 secondes et clignez doucement des yeux.",
        ),
    },
    {
        "preset_id": "movement_break",
        "name": "Pause active",
        "description": "Rappelle de se lever ou de changer de position une fois par "
        "heure en semaine, de 9 h 55 à 17 h 55. Ne verrouille pas le PC.",
        "schedule": _schedule("09:55", weekdays=True, end="17:55", interval=60),
        "steps": _notification(
            "Pause active",
            "Changez de position, détendez vos épaules "
            "ou marchez un peu si vous le pouvez.",
        ),
    },
    {
        "preset_id": "lunch_break",
        "name": "Pause déjeuner",
        "description": "À 12 h 30 en semaine, propose de prendre une pause loin de "
        "l'écran. Aucune fenêtre n'est fermée ou réduite.",
        "schedule": _schedule("12:30", weekdays=True),
        "steps": _notification(
            "Pause déjeuner",
            "C'est l'heure de la pause déjeuner. "
            "Si possible, quittez l'écran et prenez le temps de souffler.",
        ),
    },
    {
        "preset_id": "end_of_day",
        "name": "Fin de journée",
        "description": "À 18 h en semaine, affiche une checklist : enregistrer son "
        "travail, noter la prochaine étape, déconnecter. N'éteint pas le PC.",
        "schedule": _schedule("18:00", weekdays=True),
        "steps": _notification(
            "Fin de journée",
            "Avant de terminer : enregistrez vos "
            "documents, notez votre prochaine étape et prenez une vraie pause. "
            "Aucune application ne sera fermée automatiquement.",
        ),
    },
    {
        "preset_id": "tomorrow_briefing",
        "name": "Préparer demain",
        "description": "À 20 h 30, affiche les rappels Jarvis prévus pour demain "
        "afin d'anticiper la journée. Fonctionne aussi avec une liste vide.",
        "schedule": _schedule("20:30"),
        "steps": [{"tool": "show_reminder_briefing", "args": {"period": "tomorrow"}}],
    },
    {
        "preset_id": "weekly_review",
        "name": "Revue hebdomadaire",
        "description": "Le vendredi à 17 h, résume les rappels Jarvis à venir "
        "sur les sept prochains jours (aujourd'hui inclus).",
        "schedule": {"time": "17:00", "days": [4]},
        "steps": [{"tool": "show_reminder_briefing", "args": {"period": "week"}}],
    },
    {
        "preset_id": "low_battery",
        "name": "Batterie faible",
        "description": "Vérifie la batterie toutes les 5 minutes. Alerte à 20 % ou "
        "moins uniquement sur batterie, au plus une fois par heure. "
        "Reste silencieuse sur un PC sans batterie.",
        "schedule": _schedule("00:00", end="23:55", interval=5),
        "steps": [{"tool": "check_battery_alert", "args": {}}],
    },
    {
        "preset_id": "low_disk_space",
        "name": "Espace disque",
        "description": "Vérifie chaque heure le disque du dossier utilisateur. "
        "Alerte sous 10 % d'espace libre, au plus une fois par jour. "
        "Ne supprime aucun fichier.",
        "schedule": _schedule("00:00", end="23:00", interval=60),
        "steps": [{"tool": "check_disk_alert", "args": {}}],
    },
    {
        "preset_id": "focus_mode",
        "name": "Mode focus",
        "description": "Session de révision protégée : bloque les jeux, le streaming, "
        "les réseaux sociaux, les achats, les outils de hasard et les bavardages "
        "hors travail. Ferme aussi les distractions connues quand Windows le permet.",
        "schedule": None,
        "steps": [{"tool": "activate_focus_mode", "args": {}}],
    },
    {
        "preset_id": "game_mode",
        "name": "Mode jeu",
        "description": "Optimise Jarvis pour jouer : ferme des processus lourds non essentiels, "
        "bloque toute ouverture, fermeture, capture, notification ou interaction "
        "à l'écran. Les commandes de volume et la sortie du mode restent possibles.",
        "schedule": None,
        "steps": [{"tool": "activate_game_mode", "args": {}}],
    },
    # Catalogues historiques : macros manuelles, désactivées comme tous les presets.
    {
        "preset_id": "work_setup",
        "name": "Poste de travail",
        "description": "Ouvre VS Code et GitHub, puis règle le volume à 30 %. VS Code doit être installé.",
        "schedule": None,
        "steps": [
            {
                "tool": "open_application",
                "args": {
                    "application": "vscode"
                }
            },
            {
                "tool": "open_website",
                "args": {
                    "site": "github"
                }
            },
            {
                "tool": "set_volume",
                "args": {
                    "volume": 30
                }
            }
        ]
    },
    {
        "preset_id": "morning_dashboard",
        "name": "Ouverture du matin",
        "description": "Ouvre Google Agenda, Gmail et France Info. Aucun accès aux comptes : connexion éventuelle dans le navigateur.",
        "schedule": None,
        "steps": [
            {
                "tool": "open_website",
                "args": {
                    "site": "google agenda"
                }
            },
            {
                "tool": "open_website",
                "args": {
                    "site": "gmail"
                }
            },
            {
                "tool": "open_website",
                "args": {
                    "site": "france info"
                }
            }
        ]
    },
    {
        "preset_id": "pomodoro_timer",
        "name": "Pomodoro 25 minutes",
        "description": "Coupe le son et lance un minuteur de session de 25 minutes. Ne rétablit pas le volume automatiquement.",
        "schedule": None,
        "steps": [
            {
                "tool": "mute_audio",
                "args": {}
            },
            {
                "tool": "set_timer",
                "args": {
                    "minutes": 25,
                    "label": "Fin du Pomodoro"
                }
            }
        ]
    },
    {
        "preset_id": "meeting_timer",
        "name": "Réunion 45 minutes",
        "description": "Coupe le son, réduit les fenêtres et lance un minuteur de session de 45 minutes.",
        "schedule": None,
        "steps": [
            {
                "tool": "mute_audio",
                "args": {}
            },
            {
                "tool": "show_desktop",
                "args": {}
            },
            {
                "tool": "set_timer",
                "args": {
                    "minutes": 45,
                    "label": "Fin de réunion"
                }
            }
        ]
    },
    {
        "preset_id": "coffee_timer",
        "name": "Pause café 5 minutes",
        "description": "Rétablit le son et lance un minuteur de session de 5 minutes.",
        "schedule": None,
        "steps": [
            {
                "tool": "unmute_audio",
                "args": {}
            },
            {
                "tool": "set_timer",
                "args": {
                    "minutes": 5,
                    "label": "Fin de la pause café"
                }
            }
        ]
    },
    {
        "preset_id": "music_break",
        "name": "Pause musicale",
        "description": "Ouvre Spotify dans le navigateur et règle le volume à 40 %. Ne démarre pas automatiquement une lecture.",
        "schedule": None,
        "steps": [
            {
                "tool": "open_website",
                "args": {
                    "site": "spotify"
                }
            },
            {
                "tool": "set_volume",
                "args": {
                    "volume": 40
                }
            }
        ]
    },
    {
        "preset_id": "news_reading",
        "name": "Lire les actualités",
        "description": "Ouvre France Info puis Le Monde. Ne lit ni ne résume automatiquement les pages.",
        "schedule": None,
        "steps": [
            {
                "tool": "open_website",
                "args": {
                    "site": "france info"
                }
            },
            {
                "tool": "open_website",
                "args": {
                    "site": "le monde"
                }
            }
        ]
    },
    {
        "preset_id": "system_review",
        "name": "Bilan du PC",
        "description": "Retourne connexion, batterie, disque et informations système. Le résultat est disponible à la voix ; sans batterie, une erreur est annoncée.",
        "schedule": None,
        "steps": [
            {
                "tool": "check_internet",
                "args": {}
            },
            {
                "tool": "get_battery_status",
                "args": {}
            },
            {
                "tool": "get_disk_usage",
                "args": {}
            },
            {
                "tool": "get_system_info",
                "args": {}
            }
        ]
    },
    {
        "preset_id": "leaving_desk",
        "name": "Je pars",
        "description": "Arrête le média, coupe le son et verrouille la session Windows. Enregistrez votre travail avant de lancer cette routine.",
        "schedule": None,
        "steps": [
            {
                "tool": "media_stop",
                "args": {}
            },
            {
                "tool": "mute_audio",
                "args": {}
            },
            {
                "tool": "lock_workstation",
                "args": {}
            }
        ]
    },
    {
        "preset_id": "cinema_setup",
        "name": "Séance cinéma",
        "description": "Réduit les fenêtres et règle le volume à 40 %, sans ouvrir de vidéo.",
        "schedule": None,
        "steps": [
            {
                "tool": "show_desktop",
                "args": {}
            },
            {
                "tool": "set_volume",
                "args": {
                    "volume": 40
                }
            }
        ]
    },
    {
        "preset_id": "quiet_night",
        "name": "Nuit calme",
        "description": "Arrête le média et abaisse le volume à 10 %. Aucun verrouillage, changement de luminosité ou arrêt automatique.",
        "schedule": None,
        "steps": [
            {
                "tool": "media_stop",
                "args": {}
            },
            {
                "tool": "set_volume",
                "args": {
                    "volume": 10
                }
            }
        ]
    },
    {
        "preset_id": "guided_maintenance",
        "name": "Entretien guidé du PC",
        "description": "Ouvre Téléchargements puis le nettoyage de disque Windows. Aucune suppression exécutée par Jarvis : vous choisissez dans Windows.",
        "schedule": None,
        "steps": [
            {
                "tool": "open_folder",
                "args": {
                    "folder": "telechargements"
                }
            },
            {
                "tool": "open_application",
                "args": {
                    "application": "nettoyage de disque"
                }
            }
        ]
    },

)


def builtin_routines() -> list[dict]:
    """Une copie indépendante, toujours désactivée à l'installation."""
    return [
        dict(deepcopy(preset), enabled=False, last_run=None, run_count=0)
        for preset in _PRESETS
    ]
