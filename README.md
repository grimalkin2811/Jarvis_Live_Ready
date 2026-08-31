# Jarvis Live Ready

Assistant vocal Windows prêt à tester avec Gemini Live.

## Démarrage
1. Décompresse ce ZIP.
2. Lance `setup.bat`.
3. Entre ton nom et ta clé Gemini API.
4. Une fois terminé, lance `Jarvis.bat`.

## Interface graphique (PySide6)

Jarvis propose désormais une interface reprenant l'orbe morphing de `grimalkin2811/Jarvis` :

| Commande | Effet |
|---|---|
| `Jarvis.bat` | Assistant headless (console), comme avant. |
| `Jarvis.bat --ui` | Orbe morphing interactif + menus radiaux + assistant vocal. |
| `Jarvis.bat --desktop` | Overlay halo plein écran (transparent aux clics) reflétant l'état : écoute / parole / veille. |

En mode `--ui`, survole les bords de l'orbe pour déplier les menus radiaux
(Voice, System, Memory, Appearance) et interagis directement avec les réglages :

| Contrôle | Interaction |
|---|---|
| **Toggles** | Clic pour activer/désactiver (indicateur vert = actif). |
| **Sliders** (TTS, Vitesse, Hotword, Transparence) | Glisse à la souris ou molette pour ajuster la valeur ; une barre affiche le niveau. |
| **Options** (Voice Select, Shortcuts) | Clic ou molette pour parcourir les choix. |
| **Response Mode** | Clic pour changer le mode de réponse (injecté dans le prompt Gemini). |
| **Mic Toggle** | Coupe/rétablit le micro en direct. |
| **Audio Test** | Émet un bip de test. |

Les réglages sont conservés dans `UI/menu_state.json` au redémarrage.
`Échap` pour quitter.

## Fonctions

Jarvis dispose de **57 outils** déclarés dans `src/tools.py` (voir
`TOOL_FUNCTIONS` / `TOOL_DECLARATIONS`).

| Catégorie | Outils |
|---|---|
| **Applications** | `open_application`, `close_application`, `is_application_running`, `list_applications`, `list_running_applications` |
| **Audio** | `set_volume`, `volume_up`, `volume_down`, `get_volume`, `mute_audio`, `unmute_audio`, `toggle_mute` |
| **Multimédia** | `media_play_pause`, `media_next`, `media_previous`, `media_stop` |
| **Système** | `get_system_info`, `get_battery_status`, `get_disk_usage`, `take_screenshot`, `lock_workstation`, `show_desktop`, `shutdown_pc`, `restart_pc`, `cancel_shutdown`, `set_brightness` |
| **Presse-papiers** | `get_clipboard`, `set_clipboard` |
| **Date / heure** | `get_local_time`, `get_local_date`, `get_datetime`, `days_until` |
| **Minuteurs** | `set_timer`, `list_timers`, `cancel_timer` |
| **Notes** | `take_note`, `read_notes`, `delete_notes` |
| **Web** | `open_website`, `list_websites`, `open_url`, `web_search`, `search_youtube`, `search_wikipedia`, `open_maps`, `get_directions`, `translate_text`, `get_weather`, `check_internet` |
| **Fichiers** | `open_folder`, `list_folder`, `search_files` |
| **Calcul & divers** | `calculate`, `random_number`, `flip_coin`, `roll_dice`, `pick_random` |

Exemples de phrases : « ouvre YouTube », « quelle météo à Lyon ? », « mets un
minuteur de 10 minutes pour les pâtes », « combien font racine de 144 fois
3 ? », « note que je dois appeler Paul », « capture l'écran », « verrouille le
PC », « cherche Iron Man sur Wikipédia », « itinéraire vers Lille ».

## Sécurité : listes blanches

Tout ce que Jarvis peut ouvrir est déclaré explicitement dans `src/tools.py` :

| Liste | Contenu |
|---|---|
| `APPS` | ~46 applications (système Windows, navigateurs, bureautique, dev, multimédia). |
| `SITES` | ~139 sites courants : Wikipédia, YouTube, Google, Gmail, Maps, Drive, GitHub, Stack Overflow, ChatGPT, Netflix, Twitch, Spotify, Deezer, Reddit, X, Instagram, LinkedIn, Amazon, Le Monde, France Info, BBC, SNCF, Doctolib, Steam… |
| `SITE_ALIASES` | Prononciations alternatives (« yt », « insta », « chat gpt », « la météo »…). |
| `ALLOWED_DOMAINS` | Domaines acceptés par `open_url` (dérivés de `SITES`). |
| `FOLDERS` | Dossiers utilisateur autorisés (Documents, Téléchargements, Bureau, Images, Musique, Vidéos). |
| `SEARCH_ENGINES` | Moteurs utilisables par `web_search` (Google, Bing, DuckDuckGo, Qwant, Ecosia, YouTube, Wikipédia, GitHub, Images, Maps, Amazon, Stack Overflow). |

La reconnaissance des noms est tolérante : casse, accents, tirets et petites
phrases (« ouvre le site wikipedia ») sont acceptés. Tout ce qui n'est pas dans
la liste blanche renvoie `success: false` — Jarvis l'annonce alors honnêtement.

Les actions irréversibles (`shutdown_pc`, `restart_pc`, `delete_notes`)
exigent un paramètre `confirm=true`, demandé oralement à l'utilisateur.

## Tests

```bash
python -m unittest discover tests
```

Les tests sont multiplateformes et ne déclenchent aucune action réelle
(ni ouverture d'application, ni navigateur).

