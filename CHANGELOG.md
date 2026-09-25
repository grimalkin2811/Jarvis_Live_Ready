# Changelog

Toutes les versions notables de Jarvis sont documentées ici.

Le format s'inspire de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/),
et le projet adhère au [Versioning sémantique](https://semver.org/lang/fr/).

## [1.5.0] — 2026-09-25

### Ajouté

- **Intégration musicale Deezer** — première intégration musique native de Jarvis.
  - Architecture `MusicManager` → `DeezerProvider` (`src/music/`).
  - 15 outils Gemini Live : `music_play`, `music_search`, `music_pause`,
    `music_resume`, `music_next`, `music_previous`, `music_current`,
    `music_list_playlists`, `music_play_track/artist/album/playlist`,
    `music_status`, `music_stop`, `music_disconnect`.
  - Recherche catalogue public (artistes, morceaux, albums, playlists) via
    l'API officielle `api.deezer.com` **sans authentification**.
  - Lecture par deep-link `deezer://` (app desktop) ou URL web dans le
    navigateur **par défaut** (aucun navigateur hardcodé).
  - Contrôles pause / reprise / suivant / précédent via touches multimédia
    Windows.
  - Playlists personnelles si `DEEZER_ACCESS_TOKEN` (ou fichier local
    `deezer_auth.json`) est fourni.
  - Parseur d'intentions FR pour formulations naturelles
    (`src/music/intents.py`) + instructions Gemini Live dédiées.
  - Gestion d'ambiguïté (« Joue Halo ») : demande de précision au lieu d'un
    choix arbitraire.
  - Tolérance accents / casse / transcription partielle.
  - Deezer ajouté à la liste blanche des applications.
  - Outils musique bloqués en mode focus (cohérent avec les distractions).

### Documentation

- Section README « Musique Deezer (v1.5.0) » : capacités, auth, limitations
  honnêtes, commandes vocales.
- `.env.example` : variables `DEEZER_ACCESS_TOKEN` / `JARVIS_DEEZER_TOKEN`.
- `.gitignore` : `deezer_auth.json`.
- Ce CHANGELOG.

### Limitations connues (Deezer)

- Pas de streaming audio direct via l'API tierce (retiré par Deezer pour les
  apps individuelles).
- Pas de now-playing temps réel API : Jarvis maintient un état local de la
  dernière lecture lancée.
- Création de nouvelles apps OAuth Deezer restreinte côté Deezer (2025+) :
  les playlists personnelles nécessitent un token existant valide.
- Aucune simulation : si une fonction est indisponible, Jarvis le dit
  clairement.

### Tests

- `tests/test_music_intents.py` — formulations naturelles FR.
- `tests/test_music_deezer.py` — client HTTP mocké, recherche, lecture,
  contrôles, auth, ambiguïté, erreurs réseau/timeout, MusicManager, tools.
- Aucun compte Deezer réel requis pour la CI.

### Technique

- Version `src/version.py` → **1.5.0**.
- Dépendances inchangées (stdlib `urllib` pour Deezer, pas de package tiers).

## [1.4.x] — Writing Mode et correctifs UI

Voir les releases GitHub `v1.4.0`, `v1.4.1` et la PR Writing Mode 1.4.2 pour
le détail des versions intermédiaires (hors périmètre de ce fichier au moment
de la création du CHANGELOG centralisé).

## [1.3.x] — Modes, layout, démarrage

Voir les releases `v1.3.0` → `v1.3.2`.

## [1.2.0] — Blob, écoute post-réponse, hover

Voir la release `v1.2.0`.

## [1.1.x] — Wake word, launcher, anti-console

Voir les releases `v1.1.0` → `v1.1.2`.

## [1.0.x] — Distribution Windows initiale

Voir les releases `v1.0.0` → `v1.0.2`.

[1.5.0]: https://github.com/grimalkin2811/Jarvis_Live_Ready/compare/v1.4.1...v1.5.0
