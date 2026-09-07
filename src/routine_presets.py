"""Catalogue livré avec Jarvis : dix routines locales, prêtes à activer.

Aucun compte, chemin personnel ou logiciel tiers à renseigner. Les identifiants
stables servent à la migration ; les noms restent libres et prononçables.
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
)


def builtin_routines() -> list[dict]:
    """Une copie indépendante, toujours désactivée à l'installation."""
    return [
        dict(deepcopy(preset), enabled=False, last_run=None, run_count=0)
        for preset in _PRESETS
    ]
