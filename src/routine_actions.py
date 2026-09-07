"""Outils locaux utilisés par les routines prêtes à l'emploi.

Ils sont déclarés dans src.tools, comme les autres actions autorisées. Les
imports d'outils système sont tardifs pour ne pas créer de cycle d'import.
"""

from __future__ import annotations

import datetime as dt
import os

from .scheduler import get_default_scheduler


def notify_user(message, title="Jarvis") -> dict:
    """Affiche une notification locale immédiatement, sans appel à Gemini."""
    return get_default_scheduler().notify(title, message)


def show_reminder_briefing(period="today") -> dict:
    """Résume les vrais rappels Jarvis, sans agenda ou compte supplémentaire."""
    now = dt.datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "today":
        start, end = now, midnight + dt.timedelta(days=1)
        title, label = "Briefing du matin", "aujourd'hui"
    elif period == "tomorrow":
        start = midnight + dt.timedelta(days=1)
        end = start + dt.timedelta(days=1)
        title, label = "Préparer demain", "demain"
    elif period == "week":
        start, end = now, midnight + dt.timedelta(days=7)
        title, label = "Revue hebdomadaire", "sur les 7 prochains jours"
    else:
        return {
            "success": False,
            "error": "Période inconnue : today, tomorrow ou week.",
        }

    scheduler = get_default_scheduler()
    result = scheduler.reminder_occurrences(start, end)
    if not result.get("success"):
        return result
    reminders = result["rappels"]
    count = len(reminders)
    if not count:
        message = f"{start:%d/%m/%Y} — Aucun rappel Jarvis prévu {label}."
    else:
        lines = [f"{start:%d/%m} — {count} rappel(s) {label} :"]
        for reminder in reminders[:3]:
            due = dt.datetime.fromisoformat(reminder["echeance_iso"])
            date = due.strftime("%d/%m %H:%M" if period == "week" else "%H:%M")
            text = " ".join(str(reminder["texte"]).split())
            if len(text) > 42:
                text = text[:41] + "…"
            lines.append(f"{date} : {text}")
        if count > 3:
            lines.append(f"+ {count - 3} autre(s). Demandez la liste à Jarvis.")
        message = "\n".join(lines)
    outcome = scheduler.notify(title, message)
    outcome.update(nombre_rappels=count, periode=period)
    return outcome


def check_battery_alert() -> dict:
    """Alerte à 20 % ou moins, débranché ; au plus une alerte par heure."""
    from .tools import get_battery_status

    status = get_battery_status()
    if not status.get("success") or "branche" not in status:
        # Pas une panne de routine sur un PC fixe. Sans état d'alimentation
        # fiable, ne pas alerter à tort pendant la charge.
        return {
            "success": True,
            "notification": False,
            "raison": "Batterie ou état de charge indisponible sur ce PC.",
        }
    level = status["pourcentage"]
    if status["branche"] or level > 20:
        return {
            "success": True,
            "notification": False,
            "raison": "Batterie OK ou PC branché.",
        }
    return get_default_scheduler().notify(
        "Batterie faible",
        f"Batterie à {level} %. Pensez à brancher le chargeur. "
        "Jarvis ne modifie pas vos réglages d'alimentation.",
        cooldown_key="preset:low_battery",
        cooldown_seconds=3600,
    )


def check_disk_alert() -> dict:
    """Alerte sous 10 % libres sur le disque utilisateur ; une fois par jour."""
    from .tools import get_disk_usage

    status = get_disk_usage(drive=os.path.expanduser("~"))
    if not status.get("success"):
        return status
    if status["libre_pourcent"] >= 10:
        return {
            "success": True,
            "notification": False,
            "raison": "Espace disque suffisant.",
        }
    return get_default_scheduler().notify(
        "Espace disque",
        f"Le disque de votre dossier utilisateur n'a plus que "
        f"{status['libre_go']} Go libres ({status['libre_pourcent']} %). "
        "Pensez à faire de la place. Aucun fichier n'a été supprimé.",
        cooldown_key="preset:low_disk_space",
        cooldown_seconds=86400,
    )
