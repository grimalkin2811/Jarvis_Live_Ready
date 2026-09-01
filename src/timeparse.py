"""Analyse d'expressions temporelles françaises pour Jarvis.

Ce module est volontairement autonome (aucune dépendance externe, aucun import
du reste de l'application) afin de pouvoir être testé seul et réutilisé aussi
bien par les rappels (``src.scheduler``) que par les routines
(``src.routines``).

Deux fonctions publiques principales :

* :func:`parse_when` — « demain à 9h », « dans 20 minutes », « lundi à 8h30 »,
  « 12/03/2026 à 14h », « 2026-03-12T14:00 » → ``datetime`` local naïf.
* :func:`parse_schedule` — « tous les jours à 9h », « en semaine à 8h30 »,
  « lundi et vendredi à 18h » → ``{"time": "HH:MM", "days": [0, 4]}``.

Le parsing est **conservateur** : en cas de doute, il renvoie ``None`` plutôt
que de deviner. Jarvis peut alors demander une précision à l'utilisateur.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

#: Jours de la semaine, indexés comme ``datetime.weekday()`` (lundi = 0).
WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

WEEKDAY_INDEX = {name: index for index, name in enumerate(WEEKDAYS)}
WEEKDAY_INDEX.update(
    {
        "lun": 0,
        "mar": 1,
        "mer": 2,
        "jeu": 3,
        "ven": 4,
        "sam": 5,
        "dim": 6,
        # Tolérance sur les pluriels (« tous les lundis »).
        **{f"{name}s": index for index, name in enumerate(WEEKDAYS)},
    }
)

MONTHS = {
    "janvier": 1,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
}

NUMBER_WORDS = {
    "un": 1,
    "une": 1,
    "deux": 2,
    "trois": 3,
    "quatre": 4,
    "cinq": 5,
    "six": 6,
    "sept": 7,
    "huit": 8,
    "neuf": 9,
    "dix": 10,
    "onze": 11,
    "douze": 12,
    "quinze": 15,
    "vingt": 20,
    "trente": 30,
    "quarante": 40,
    "quarante cinq": 45,
    "soixante": 60,
}

#: Heure utilisée quand une date est donnée sans heure précise.
DEFAULT_HOUR = (9, 0)

RECURRENCES = {"", "daily", "weekly", "weekdays", "weekends", "monthly", "hourly"}


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------


def normalize(value) -> str:
    """Minuscule, sans accents, ponctuation réduite à des espaces."""
    text = unicodedata.normalize("NFD", str(value or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("'", " ").replace("’", " ").replace("-", " ")
    text = re.sub(r"[^a-z0-9:/\.\-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _word_number(token: str) -> int | None:
    token = token.strip()
    if not token:
        return None
    if token.isdigit():
        try:
            return int(token)
        except ValueError:
            return None
    return NUMBER_WORDS.get(token)


def format_datetime(moment: dt.datetime) -> str:
    """Formatage lisible et prononçable d'une échéance."""
    today = dt.date.today()
    delta_days = (moment.date() - today).days
    heure = moment.strftime("%H:%M")
    if delta_days == 0:
        return f"aujourd'hui à {heure}"
    if delta_days == 1:
        return f"demain à {heure}"
    if delta_days == 2:
        return f"après-demain à {heure}"
    if 0 < delta_days < 7:
        return f"{WEEKDAYS[moment.weekday()]} à {heure}"
    return moment.strftime("%d/%m/%Y à %H:%M")


# ---------------------------------------------------------------------------
# Heures
# ---------------------------------------------------------------------------

_TIME_PATTERNS = [
    # 9h, 9 h, 9h30, 9 h 30, 9h05
    re.compile(r"\b(?P<h>\d{1,2})\s*h(?:eures?)?\s*(?P<m>\d{1,2})?\b"),
    # 09:30, 9:30
    re.compile(r"\b(?P<h>\d{1,2})\s*:\s*(?P<m>\d{2})\b"),
    # à 9 (uniquement précédé de « a »/« vers », sinon trop ambigu)
    re.compile(r"\b(?:a|vers)\s+(?P<h>\d{1,2})(?!\s*[:h/\d])\b"),
]


def extract_time(text: str) -> tuple[tuple[int, int] | None, str]:
    """Extrait une heure du texte et renvoie ``((h, m), reste_du_texte)``."""
    normalized = normalize(text)

    if re.search(r"\bmidi\b", normalized):
        return (12, 0), re.sub(r"\b(a|vers)?\s*midi\b", " ", normalized).strip()
    if re.search(r"\bminuit\b", normalized):
        return (0, 0), re.sub(r"\b(a|vers)?\s*minuit\b", " ", normalized).strip()

    for pattern in _TIME_PATTERNS:
        match = pattern.search(normalized)
        if not match:
            continue
        hour = int(match.group("h"))
        minute = int(match.groupdict().get("m") or 0)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            continue

        rest = normalized[: match.start()] + " " + normalized[match.end() :]
        rest = re.sub(r"\s+", " ", rest).strip()

        # « et demie », « et quart », « moins le quart »
        if re.search(r"\bet demie?\b", rest):
            minute = 30
            rest = re.sub(r"\bet demie?\b", " ", rest)
        elif re.search(r"\bet quart\b", rest):
            minute = 15
            rest = re.sub(r"\bet quart\b", " ", rest)
        elif re.search(r"\bmoins le quart\b", rest):
            minute = 45
            hour = (hour - 1) % 24
            rest = re.sub(r"\bmoins le quart\b", " ", rest)

        # « 9h du soir » / « 9h du matin »
        if re.search(r"\b(du soir|de l apres midi|ce soir)\b", rest) and hour < 12:
            hour += 12
        if re.search(r"\bdu matin\b", rest) and hour == 12:
            hour = 0

        return (hour, minute), re.sub(r"\s+", " ", rest).strip()

    return None, normalized


# ---------------------------------------------------------------------------
# Durées relatives
# ---------------------------------------------------------------------------

_DELAY_UNITS = {
    "seconde": 1,
    "secondes": 1,
    "sec": 1,
    "minute": 60,
    "minutes": 60,
    "min": 60,
    "heure": 3600,
    "heures": 3600,
    "jour": 86400,
    "jours": 86400,
    "semaine": 604800,
    "semaines": 604800,
    "mois": 2592000,
}


def parse_delay(text: str) -> int | None:
    """« dans 20 minutes », « dans une heure et demie » → durée en secondes."""
    normalized = normalize(text)

    # Formulations idiomatiques, testées en premier car « un quart d heure »
    # ressemble à « <quantité> heure » pour l'expression générique ci-dessous.
    if re.search(r"\bdans\s+un\s+quart\s+d\s*heure\b", normalized):
        return 900
    if re.search(r"\bdans\s+(?:une?|un)\s+demie?\s*heure\b", normalized):
        return 1800
    if re.search(r"\bdans\s+trois\s+quarts\s+d\s*heure\b", normalized):
        return 2700

    match = re.search(r"\bdans\s+(?P<qty>[a-z0-9 ]+?)\s*(?P<unit>%s)\b" % "|".join(_DELAY_UNITS), normalized)
    if not match:
        return None

    quantity = _word_number(match.group("qty").strip())
    if quantity is None or quantity <= 0:
        return None

    total = quantity * _DELAY_UNITS[match.group("unit")]

    tail = normalized[match.end() :]
    if re.match(r"\s*et\s+demie?\b", tail):
        total = int(total * 1.5)
    elif re.match(r"\s*et\s+quart\b", tail):
        total = int(total * 1.25)

    return total if total > 0 else None


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


def _next_weekday(today: dt.date, target: int, allow_today: bool = False) -> dt.date:
    delta = (target - today.weekday()) % 7
    if delta == 0 and not allow_today:
        delta = 7
    return today + dt.timedelta(days=delta)


def extract_date(text: str, today: dt.date | None = None) -> tuple[dt.date | None, str, bool]:
    """Extrait une date. Renvoie ``(date, reste, date_explicite)``."""
    today = today or dt.date.today()
    normalized = normalize(text)

    def _consume(pattern: str, value: dt.date) -> tuple[dt.date, str, bool]:
        rest = re.sub(pattern, " ", normalized, count=1)
        return value, re.sub(r"\s+", " ", rest).strip(), True

    if re.search(r"\bapres[\s-]*demain\b", normalized):
        return _consume(r"\bapres[\s-]*demain\b", today + dt.timedelta(days=2))
    if re.search(r"\bdemain\b", normalized):
        return _consume(r"\bdemain\b", today + dt.timedelta(days=1))
    if re.search(r"\baujourd[\s-]*hui\b", normalized):
        return _consume(r"\baujourd[\s-]*hui\b", today)
    if re.search(r"\bce\s+soir\b", normalized):
        return today, normalized, True

    # JJ/MM ou JJ/MM/AAAA
    match = re.search(r"\b(?P<d>\d{1,2})/(?P<m>\d{1,2})(?:/(?P<y>\d{2,4}))?\b", normalized)
    if match:
        day, month = int(match.group("d")), int(match.group("m"))
        year = match.group("y")
        if year:
            year = int(year)
            if year < 100:
                year += 2000
        else:
            year = today.year
        try:
            value = dt.date(year, month, day)
        except ValueError:
            return None, normalized, False
        if not match.group("y") and value < today:
            try:
                value = value.replace(year=year + 1)
            except ValueError:
                pass
        return _consume(re.escape(match.group(0)), value)

    # 12 mars [2026]
    match = re.search(r"\b(?P<d>\d{1,2})\s+(?P<month>%s)(?:\s+(?P<y>\d{4}))?\b" % "|".join(MONTHS), normalized)
    if match:
        day = int(match.group("d"))
        month = MONTHS[match.group("month")]
        year = int(match.group("y")) if match.group("y") else today.year
        try:
            value = dt.date(year, month, day)
        except ValueError:
            return None, normalized, False
        if not match.group("y") and value < today:
            value = value.replace(year=year + 1)
        return _consume(re.escape(match.group(0)), value)

    # Jour de la semaine
    for name, index in sorted(WEEKDAY_INDEX.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{name}\b", normalized):
            return _consume(rf"\b(prochain\s+)?{name}(\s+prochain)?\b", _next_weekday(today, index))

    return None, normalized, False


# ---------------------------------------------------------------------------
# API publique : échéance ponctuelle
# ---------------------------------------------------------------------------


def parse_when(text: str, now: dt.datetime | None = None) -> dt.datetime | None:
    """Convertit une expression temporelle en ``datetime`` local naïf.

    Renvoie ``None`` si aucune échéance exploitable n'est trouvée.
    """
    if text is None:
        return None
    now = now or dt.datetime.now()
    raw = str(text).strip()
    if not raw:
        return None

    # 1. Format ISO explicite (fourni par le modèle par exemple).
    iso_match = re.match(r"^\s*(\d{4}-\d{2}-\d{2})(?:[ tT](\d{1,2}):(\d{2})(?::(\d{2}))?)?", raw)
    if iso_match:
        try:
            date_part = dt.date.fromisoformat(iso_match.group(1))
        except ValueError:
            return None
        hour = int(iso_match.group(2)) if iso_match.group(2) else DEFAULT_HOUR[0]
        minute = int(iso_match.group(3)) if iso_match.group(3) else DEFAULT_HOUR[1]
        second = int(iso_match.group(4)) if iso_match.group(4) else 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
            return None
        return dt.datetime.combine(date_part, dt.time(hour, minute, second))

    # 2. Délai relatif : « dans 20 minutes ».
    delay = parse_delay(raw)
    if delay is not None:
        return (now + dt.timedelta(seconds=delay)).replace(microsecond=0)

    # 3. Date et/ou heure.
    date_value, rest, explicit_date = extract_date(raw, today=now.date())
    time_value, _ = extract_time(rest)

    if time_value is None and not explicit_date:
        return None

    if time_value is None:
        time_value = DEFAULT_HOUR

    if date_value is None:
        date_value = now.date()
        candidate = dt.datetime.combine(date_value, dt.time(*time_value))
        if candidate <= now:
            candidate += dt.timedelta(days=1)
        return candidate

    return dt.datetime.combine(date_value, dt.time(*time_value))


def extract_when(text: str, now: dt.datetime | None = None) -> tuple[dt.datetime | None, str]:
    """Sépare l'échéance du contenu du rappel.

    « rappelle-moi d'appeler Paul demain à 9h » →
    ``(datetime(demain 09:00), "appeler Paul")``.
    """
    moment = parse_when(text, now=now)
    if moment is None:
        return None, str(text or "").strip()

    cleaned = str(text or "")
    patterns = [
        r"(?i)\brappelle[- ]?moi\b",
        r"(?i)\brappelles[- ]?moi\b",
        r"(?i)\bpense[sz]? [aà]\b",
        r"(?i)\bn oublie pas de\b",
        r"(?i)\bde me rappeler\b",
        r"(?i)\bdans \w+ (?:secondes?|minutes?|heures?|jours?|semaines?)\b",
        r"(?i)\baujourd['’ ]?hui\b",
        r"(?i)\bapr[eè]s[- ]demain\b",
        r"(?i)\bdemain\b",
        r"(?i)\bce soir\b",
        r"(?i)\b(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)s?\b",
        r"(?i)\b\d{1,2}\s*h(?:eures?)?\s*\d{0,2}\b",
        r"(?i)\b\d{1,2}:\d{2}\b",
        r"(?i)\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b",
        r"(?i)\bmidi\b",
        r"(?i)\bminuit\b",
        r"(?i)\bprochain\b",
        r"(?i)\ba\s*$",
    ]
    for pattern in patterns:
        cleaned = re.sub(pattern, " ", cleaned)

    cleaned = re.sub(r"(?i)^\s*(?:de|d['’]\s*|que|qu['’]\s*|a|à|pour)\s*", " ", cleaned)
    cleaned = re.sub(r"(?i)\s+(?:a|à|de|d['’]|pour|le|la|les|vers)\s*$", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.;:-'’")
    return moment, cleaned


def next_occurrence(moment: dt.datetime, recurrence: str) -> dt.datetime | None:
    """Échéance suivante pour un rappel récurrent (``None`` si ponctuel)."""
    recurrence = (recurrence or "").strip().lower()
    if recurrence in {"", "none", "once", "aucune"}:
        return None
    if recurrence == "hourly":
        return moment + dt.timedelta(hours=1)
    if recurrence == "daily":
        return moment + dt.timedelta(days=1)
    if recurrence == "weekly":
        return moment + dt.timedelta(days=7)
    if recurrence == "weekdays":
        nxt = moment + dt.timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += dt.timedelta(days=1)
        return nxt
    if recurrence == "weekends":
        nxt = moment + dt.timedelta(days=1)
        while nxt.weekday() < 5:
            nxt += dt.timedelta(days=1)
        return nxt
    if recurrence == "monthly":
        month = moment.month + 1
        year = moment.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        day = moment.day
        while day > 28:
            try:
                return moment.replace(year=year, month=month, day=day)
            except ValueError:
                day -= 1
        return moment.replace(year=year, month=month, day=day)
    return None


def parse_recurrence(text: str) -> str:
    """Normalise une récurrence exprimée en langage naturel."""
    normalized = normalize(text)
    if not normalized:
        return ""
    if normalized in RECURRENCES:
        return normalized
    if re.search(r"\b(chaque heure|toutes les heures)\b", normalized):
        return "hourly"
    if re.search(r"\b(tous les jours|chaque jour|quotidien|quotidienne|daily)\b", normalized):
        return "daily"
    if re.search(r"\b(en semaine|jours de semaine|du lundi au vendredi|weekdays)\b", normalized):
        return "weekdays"
    if re.search(r"\b(week[ -]?end|le samedi et dimanche|weekends)\b", normalized):
        return "weekends"
    if re.search(r"\b(toutes les semaines|chaque semaine|hebdomadaire|weekly)\b", normalized):
        return "weekly"
    if re.search(r"\b(tous les mois|chaque mois|mensuel|mensuelle|monthly)\b", normalized):
        return "monthly"
    return ""


# ---------------------------------------------------------------------------
# API publique : planification récurrente (routines)
# ---------------------------------------------------------------------------


def parse_schedule(text) -> dict | None:
    """Convertit « tous les jours à 9h » en ``{"time": "09:00", "days": [...]}``.

    ``days`` suit la convention ``datetime.weekday()`` (lundi = 0). Renvoie
    ``None`` si aucune heure exploitable n'est trouvée.
    """
    if text is None:
        return None

    # Déjà structuré (rechargement depuis le fichier JSON).
    if isinstance(text, dict):
        raw_time = str(text.get("time") or "").strip()
        match = re.match(r"^(\d{1,2})[:h](\d{1,2})?$", raw_time)
        if not match:
            return None
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        days = text.get("days")
        if not isinstance(days, (list, tuple)) or not days:
            days = list(range(7))
        clean_days = sorted({int(d) for d in days if isinstance(d, int) or str(d).isdigit()} & set(range(7)))
        if not clean_days:
            clean_days = list(range(7))
        return {"time": f"{hour:02d}:{minute:02d}", "days": clean_days}

    normalized = normalize(text)
    if not normalized:
        return None

    time_value, rest = extract_time(normalized)
    if time_value is None:
        return None

    days: set[int] = set()
    if re.search(r"\b(en semaine|jours de semaine|du lundi au vendredi|jours ouvres|weekdays)\b", rest):
        days.update(range(5))
    if re.search(r"\b(week[ -]?end|weekends)\b", rest):
        days.update({5, 6})
    for name, index in WEEKDAY_INDEX.items():
        if re.search(rf"\b{name}\b", rest):
            days.add(index)
    if not days and re.search(r"\b(tous les jours|chaque jour|quotidien|quotidienne|daily)\b", rest):
        days.update(range(7))
    if not days:
        days.update(range(7))

    return {"time": "%02d:%02d" % time_value, "days": sorted(days)}


def describe_schedule(schedule: dict | None) -> str:
    """Description prononçable d'une planification."""
    if not schedule:
        return "aucune"
    days = schedule.get("days") or []
    time_text = schedule.get("time", "??:??")
    day_set = set(days)
    if day_set == set(range(7)):
        return f"tous les jours à {time_text}"
    if day_set == set(range(5)):
        return f"en semaine à {time_text}"
    if day_set == {5, 6}:
        return f"le week-end à {time_text}"
    names = [WEEKDAYS[d] for d in sorted(day_set)]
    if len(names) == 1:
        return f"chaque {names[0]} à {time_text}"
    return f"{', '.join(names[:-1])} et {names[-1]} à {time_text}"
