"""Détection d'intentions musicales à partir d'un texte (FR/EN).

Ce module n'est **pas** un dictionnaire exhaustif de phrases : il extrait une
intention structurée (PLAY_TRACK, PAUSE, …) et des slots (titre, artiste…).
Gemini Live reste le moteur principal de compréhension ; ces helpers servent
aux tests, au fallback local, et à normaliser les arguments des outils.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any


# Intentions supportées (alignées sur le cahier des charges 1.5.0).
PLAY_TRACK = "PLAY_TRACK"
PLAY_ARTIST = "PLAY_ARTIST"
PLAY_ALBUM = "PLAY_ALBUM"
PLAY_PLAYLIST = "PLAY_PLAYLIST"
PLAY_MUSIC = "PLAY_MUSIC"  # « mets de la musique » sans cible
SEARCH = "SEARCH"
PAUSE = "PAUSE"
RESUME = "RESUME"
NEXT = "NEXT"
PREVIOUS = "PREVIOUS"
STOP = "STOP"
CURRENT_TRACK = "CURRENT_TRACK"
LIST_PLAYLISTS = "LIST_PLAYLISTS"
AUTH_STATUS = "AUTH_STATUS"
UNKNOWN = "UNKNOWN"

ALL_INTENTS = (
    PLAY_TRACK,
    PLAY_ARTIST,
    PLAY_ALBUM,
    PLAY_PLAYLIST,
    PLAY_MUSIC,
    SEARCH,
    PAUSE,
    RESUME,
    NEXT,
    PREVIOUS,
    STOP,
    CURRENT_TRACK,
    LIST_PLAYLISTS,
    AUTH_STATUS,
    UNKNOWN,
)


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize(value: str) -> str:
    text = _strip_accents(str(value or "")).lower()
    text = text.replace("'", " ")
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


@dataclass
class MusicIntent:
    intent: str = UNKNOWN
    query: str = ""
    track: str | None = None
    artist: str | None = None
    album: str | None = None
    playlist: str | None = None
    personal: bool = False
    raw: str = ""
    confidence: float = 0.0
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Verbes de lecture
_PLAY_VERBS = (
    r"(?:joue|lance|mets|met|play|start|écoute|ecoute|enclenche|démarre|demarre)"
)
_SEARCH_VERBS = r"(?:cherche|recherche|trouve|find|search|c'est quoi|c est quoi)"

# Mots « ma/mes » pour playlists personnelles
_MY = r"(?:ma|mes|mon|my)"

# Nettoyage des formules de politesse / fillers
_FILLERS = re.compile(
    r"\b("
    r"s il te plait|s il te plait|sil te plait|stp|please|peux tu|peut tu|pourrais tu|"
    r"est ce que tu peux|jarvis|hey jarvis|ok|alors|euh"
    r")\b",
    re.IGNORECASE,
)


def _clean_utterance(text: str) -> str:
    t = str(text or "").strip()
    # Normalise apostrophes / accents avant de retirer les fillers.
    t = t.replace("'", " ").replace("’", " ").replace("‘", " ")
    t = _strip_accents(t)
    t = _FILLERS.sub(" ", t)
    t = re.sub(r"[,:;]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" \t.!?")
    return t


# Contrôles simples (match exact normalisé)
_CONTROL_EXACT: list[tuple[str, re.Pattern[str]]] = [
    (PAUSE, re.compile(r"^(pause|met[st]?\s+en\s+pause|pause\s+la\s+musique|stop\s+pas)$")),
    (RESUME, re.compile(r"^(reprends?|reprend|resume|continue|relance|c est reparti|remets)$")),
    (NEXT, re.compile(r"^(suivant|next|passe|morceau\s+suivant|piste\s+suivante|skip)$")),
    (
        PREVIOUS,
        re.compile(
            r"^(precedent|précédent|previous|retour|morceau\s+precedent|"
            r"piste\s+precedente|reviens)$"
        ),
    ),
    (STOP, re.compile(r"^(stop|arrete|arrête|stoppe|coupe\s+la\s+musique)$")),
    (
        CURRENT_TRACK,
        re.compile(
            r"^(quel\s+morceau|quelle\s+chanson|qu est ce qui (joue|passe)|"
            r"c est quoi ce morceau|now\s+playing|current\s+track|"
            r"qu est ce que j ecoute|qui chante)$"
        ),
    ),
    (
        LIST_PLAYLISTS,
        re.compile(
            r"^(liste\s+(mes\s+)?playlists?|mes\s+playlists?|"
            r"quelles?\s+(sont\s+)?mes\s+playlists?|list\s+playlists?)$"
        ),
    ),
    (
        PLAY_MUSIC,
        re.compile(
            r"^(de\s+la\s+musique|musique|mets?\s+de\s+la\s+musique|"
            r"lance\s+de\s+la\s+musique|play\s+music|something\s+to\s+listen)$"
        ),
    ),
]


# Patterns avec capture
_PLAY_PLAYLIST_RE = re.compile(
    rf"^{_PLAY_VERBS}\s+(?:{_MY}\s+)?(?:playlist\s+)?(?P<name>.+)$",
    re.IGNORECASE,
)
_PLAY_ALBUM_RE = re.compile(
    rf"^{_PLAY_VERBS}\s+(?:l\s+|le\s+|la\s+|mon\s+|ma\s+|my\s+)?album\s+"
    rf"(?P<name>.+)$",
    re.IGNORECASE,
)
_PLAY_ARTIST_ONLY_RE = re.compile(
    rf"^{_PLAY_VERBS}\s+(?:l\s+artiste\s+|artiste\s+)?(?P<name>.+)$",
    re.IGNORECASE,
)
_SEARCH_RE = re.compile(
    rf"^{_SEARCH_VERBS}\s+(?:sur\s+deezer\s+)?(?P<query>.+)$",
    re.IGNORECASE,
)
_PLAY_GENERIC_RE = re.compile(
    rf"^{_PLAY_VERBS}\s+(?P<body>.+)$",
    re.IGNORECASE,
)

# « X de Y » pour titre/artiste
_OF_RE = re.compile(
    r"^(?P<title>.+?)\s+(?:de|d|par|by|-|—|–)\s+(?P<artist>.+)$",
    re.IGNORECASE,
)


def parse_music_intent(text: str) -> MusicIntent:
    """Analyse une phrase et renvoie une ``MusicIntent`` structurée."""
    raw = str(text or "").strip()
    cleaned = _clean_utterance(raw)
    norm = normalize(cleaned)
    intent = MusicIntent(raw=raw, query=cleaned)

    if not norm:
        return intent

    # 1. Contrôles exacts
    for name, pattern in _CONTROL_EXACT:
        if pattern.match(norm):
            intent.intent = name
            intent.confidence = 0.95
            if name == LIST_PLAYLISTS:
                intent.personal = True
            return intent

    # Variantes « passe au morceau suivant »
    if re.search(r"\b(morceau|piste|chanson|track)\s+suivant", norm) or re.search(
        r"\b(passe|skip|next)\b", norm
    ):
        if "preced" in norm or "previous" in norm or "retour" in norm:
            intent.intent = PREVIOUS
        else:
            # Éviter de capturer « passe-moi du jazz » etc. : exiger un marqueur média
            if re.search(r"\b(suivant|next|skip|morceau|piste|chanson)\b", norm):
                intent.intent = NEXT
                intent.confidence = 0.9
                return intent
    if re.search(r"\b(morceau|piste|chanson)\s+preced", norm) or re.search(
        r"\b(previous|precedent)\b", norm
    ):
        intent.intent = PREVIOUS
        intent.confidence = 0.9
        return intent

    if re.search(r"\b(qu est ce qui (joue|passe)|quel morceau|quelle chanson|now playing)\b", norm):
        intent.intent = CURRENT_TRACK
        intent.confidence = 0.9
        return intent

    if re.search(r"\b(mes playlists|liste.*playlists)\b", norm):
        intent.intent = LIST_PLAYLISTS
        intent.confidence = 0.9
        intent.personal = True
        return intent

    # 2. Recherche
    m = _SEARCH_RE.match(cleaned) or _SEARCH_RE.match(norm)
    if m:
        intent.intent = SEARCH
        intent.query = m.group("query").strip()
        intent.confidence = 0.9
        _fill_slots_from_query(intent, intent.query)
        return intent

    # 3. Playlist explicite
    if re.search(r"\bplaylist\b", norm) or re.search(rf"\b{_MY}\s+playlist\b", norm):
        personal = bool(re.search(rf"\b{_MY}\b", norm))
        # Extraire le nom
        name = None
        m = re.search(
            rf"(?:{_PLAY_VERBS}\s+)?(?:{_MY}\s+)?playlist\s+(?P<name>.+)$",
            cleaned,
            re.IGNORECASE,
        )
        if not m:
            m = re.search(
                rf"(?:{_PLAY_VERBS}\s+)(?:{_MY}\s+)(?P<name>.+)$",
                cleaned,
                re.IGNORECASE,
            )
        if m:
            name = m.group("name").strip()
            # Retirer un éventuel « playlist » résiduel
            name = re.sub(r"^playlist\s+", "", name, flags=re.IGNORECASE).strip()
        if name:
            intent.intent = PLAY_PLAYLIST
            intent.playlist = name
            intent.personal = personal
            intent.confidence = 0.92
            return intent

    # 4. Album explicite
    m = _PLAY_ALBUM_RE.match(cleaned) or re.search(
        rf"(?:{_PLAY_VERBS}\s+)?(?:l |le |la |mon |ma |my )?album\s+(?P<name>.+)$",
        cleaned,
        re.IGNORECASE,
    )
    if m and re.search(r"\balbum\b", norm):
        name = m.group("name").strip()
        title, artist = _split_of(name)
        intent.intent = PLAY_ALBUM
        intent.album = title
        intent.artist = artist
        intent.confidence = 0.9
        return intent

    # 5. Lecture générique
    m = _PLAY_GENERIC_RE.match(cleaned)
    if m:
        body = m.group("body").strip()
        body_norm = normalize(body)

        # « de la musique » / « un truc » → PLAY_MUSIC
        if body_norm in {"de la musique", "la musique", "musique", "music", "un truc", "quelque chose"}:
            intent.intent = PLAY_MUSIC
            intent.confidence = 0.9
            return intent

        # « l'artiste X » / « artiste X »
        art = re.match(r"^(?:l\s+)?artiste\s+(?P<name>.+)$", body, re.IGNORECASE)
        if art:
            intent.intent = PLAY_ARTIST
            intent.artist = art.group("name").strip()
            intent.confidence = 0.9
            return intent

        title, artist = _split_of(body)
        if artist:
            # Heuristique : si le « titre » est très court et ressemble à un article, artiste seul.
            # Sinon PLAY_TRACK avec artiste.
            intent.intent = PLAY_TRACK
            intent.track = title
            intent.artist = artist
            intent.confidence = 0.85
            return intent

        # Corps simple : artiste OU morceau — on laisse PLAY_ARTIST par défaut
        # (le MusicManager bascule sur track si besoin). Marqueur « morceau/chanson ».
        if re.match(r"^(?:le\s+|la\s+|l\s+)?(?:morceau|chanson|titre|track)\s+", body, re.IGNORECASE):
            track_name = re.sub(
                r"^(?:le\s+|la\s+|l\s+)?(?:morceau|chanson|titre|track)\s+",
                "",
                body,
                flags=re.IGNORECASE,
            ).strip()
            intent.intent = PLAY_TRACK
            intent.track = track_name
            intent.confidence = 0.85
            return intent

        intent.intent = PLAY_ARTIST
        intent.artist = body
        intent.query = body
        intent.confidence = 0.7
        return intent

    # 6. Pause / resume avec plus de contexte
    if re.search(r"\b(pause|met[st]? en pause)\b", norm):
        intent.intent = PAUSE
        intent.confidence = 0.8
        return intent
    if re.search(r"\b(reprends?|resume|continue la (musique|lecture))\b", norm):
        intent.intent = RESUME
        intent.confidence = 0.8
        return intent

    # 7. Fallback : si ça ressemble à une requête musicale libre
    if any(w in norm for w in ("musique", "deezer", "chanson", "morceau", "playlist", "album")):
        intent.intent = SEARCH
        intent.query = cleaned
        intent.confidence = 0.4
        return intent

    intent.intent = UNKNOWN
    intent.confidence = 0.0
    return intent


def _split_of(text: str) -> tuple[str, str | None]:
    raw = (text or "").strip()
    m = _OF_RE.match(raw)
    if m:
        title = m.group("title").strip(" \t-—–")
        artist = m.group("artist").strip(" \t-—–")
        if title and artist and len(artist) >= 2:
            return title, artist
    return raw, None


def _fill_slots_from_query(intent: MusicIntent, query: str) -> None:
    title, artist = _split_of(query)
    if artist:
        intent.track = title
        intent.artist = artist
    else:
        intent.query = query


def intent_to_tool_args(intent: MusicIntent) -> dict[str, Any]:
    """Convertit une intention en arguments d'outil MusicManager."""
    data: dict[str, Any] = {"intent": intent.intent}
    if intent.track:
        data["track"] = intent.track
    if intent.artist:
        data["artist"] = intent.artist
    if intent.album:
        data["album"] = intent.album
    if intent.playlist:
        data["playlist"] = intent.playlist
    if intent.query:
        data["query"] = intent.query
    if intent.personal:
        data["personal"] = True
    return data
