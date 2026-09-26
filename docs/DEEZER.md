# Intégration Deezer — Jarvis 1.5.0

Guide technique et utilisateur de l'intégration musicale.

## Architecture

```text
src/music/
├── __init__.py          # exports publics
├── models.py            # Track, Artist, Album, Playlist, SearchResults
├── intents.py           # parseur d'intentions FR (fallback / tests)
├── manager.py           # MusicManager (façade)
└── providers/
    ├── __init__.py
    ├── base.py          # protocole MusicProvider
    └── deezer.py        # DeezerHTTPClient + DeezerProvider
```

Point d'entrée outils : `src/tools.py` (`music_*`).
Gemini Live est instruit dans `src/gemini_live.py` pour router les demandes
musicales vers ces outils.

## Capacités réelles de l'API Deezer (état 2025/2026)

| Capacité | Disponible | Mécanisme Jarvis |
|---|---|---|
| Recherche catalogue | Oui (public, sans clé) | `GET /search`, `/search/artist|album|track|playlist` |
| Métadonnées track/artist/album | Oui | `GET /track/{id}`, etc. |
| Charts | Oui | `GET /chart/0/tracks` |
| Playlists personnelles | Oui **avec OAuth** | `GET /user/me/playlists` |
| Profil `/user/me` | Oui **avec OAuth** | token |
| Lecture / streaming API | **Non** (retiré pour apps individuelles) | deep-link app ou web |
| Now-playing temps réel | **Non** | état local Jarvis |
| Pause / next API | **Non** | touches média Windows |

Sources : documentation historique developers.deezer.com, retours communauté
2024-2025 (création d'apps API restreinte, code erreur OAuth 50, absence de
playback tierce).

## Authentification

### Sans token (par défaut)

Tout le catalogue public fonctionne. Aucune configuration requise.

### Avec token (playlists personnelles)

1. Si vous disposez d'un **access_token** Deezer encore valide (app développeur
   existante), placez-le dans :

   ```env
   DEEZER_ACCESS_TOKEN=votre_token
   ```

   ou

   ```env
   JARVIS_DEEZER_TOKEN=votre_token
   ```

2. Alternativement, Jarvis peut écrire
   `%LOCALAPPDATA%\Jarvis\deezer_auth.json`
   (Linux/macOS dev : `~/.jarvis/deezer_auth.json`) via
   `DeezerProvider.connect_with_token`.

3. Déconnexion : outil `music_disconnect` ou suppression du fichier.

**Interdit** : mot de passe Deezer en clair, token commité, token dans les logs.

### Restriction Deezer 2025+

Deezer a désactivé la création de nouvelles applications API pour les
particuliers (abus / T&Cs). Jarvis **ne contourne pas** cette restriction.
Si aucun token n'est disponible, les fonctions personnelles répondent :

> Les playlists personnelles nécessitent une authentification Deezer
> (DEEZER_ACCESS_TOKEN). Cette fonction n'est pas disponible sans compte lié.

## Ouverture de contenus

Ordre :

1. `deezer://www.deezer.com/{type}/{id}?autoplay=true` via `os.startfile` /
   exécutable Deezer desktop s'il est trouvé.
2. À défaut : `https://www.deezer.com/{type}/{id}?autoplay=true` via
   `webbrowser.open` (navigateur **par défaut** du système).

Aucun navigateur n'est imposé (Chrome, Edge, Firefox, Opera… selon l'OS).

## Contrôles de lecture

Sur Windows, Jarvis envoie les virtual-keys :

| Action | VK |
|---|---|
| Pause / Resume | `0xB3` MEDIA_PLAY_PAUSE |
| Suivant | `0xB0` MEDIA_NEXT_TRACK |
| Précédent | `0xB1` MEDIA_PREV_TRACK |
| Stop | `0xB2` MEDIA_STOP |

Ces touches sont relayées par Windows à l'application multimédia au premier
plan. Si Deezer n'a pas le focus, le comportement dépend du gestionnaire média
du système — Jarvis le signale en cas d'échec d'envoi.

Hors Windows, sans sender injecté (tests), l'action renvoie une limitation
claire.

## État du morceau en cours

`music_current` / « Quel morceau joue ? » :

- Si Jarvis a lancé une lecture : annonce le dernier track / contexte connu.
- Sinon : « Je ne peux pas récupérer le morceau en cours… » (pas d'invention).

## Intentions supportées

`PLAY_TRACK`, `PLAY_ARTIST`, `PLAY_ALBUM`, `PLAY_PLAYLIST`, `PLAY_MUSIC`,
`SEARCH`, `PAUSE`, `RESUME`, `NEXT`, `PREVIOUS`, `STOP`, `CURRENT_TRACK`,
`LIST_PLAYLISTS`, `AUTH_STATUS`.

Le moteur principal reste Gemini Live (compréhension libre). Le parseur local
sert de filet et de base de tests.

## Sécurité & modes

- Mode **focus** : tous les outils `music_*` sont bloqués
  (`FOCUS_ALWAYS_BLOCKED_TOOLS`), ainsi que l'app Deezer.
- Mode **jeu** : non listés dans `GAME_ALLOWED_TOOLS` → bloqués (volume OK).
- Token stocké avec permissions `0o600` quand le FS le permet.
- `.gitignore` couvre `deezer_auth.json`.

## Tests

```bash
python -m unittest tests.test_music_intents tests.test_music_deezer -v
```

Tout est mocké (opener HTTP injectable, media keys injectables, content
opener injectable). CI Windows GitHub Actions n'a besoin d'aucun secret Deezer.

## Dépannage

| Symptôme | Piste |
|---|---|
| « Réseau indisponible » | Firewall / DNS / api.deezer.com joignable ? |
| « Token refusé » | Token expiré ou app Deezer désactivée → `music_disconnect` + nouveau token |
| Pause ne fait rien | Deezer / onglet web a-t-il le focus média Windows ? |
| Playlist perso indisponible | Token manquant — limitation normale sans OAuth |
| Ambiguïté « Halo » | Préciser l'artiste : « Halo de Beyoncé » |
