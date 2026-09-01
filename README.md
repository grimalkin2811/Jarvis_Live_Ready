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
(Voice, System, Memory, Appearance, Routines) et interagis directement avec les
réglages :

| Contrôle | Interaction |
|---|---|
| **Toggles** | Clic pour activer/désactiver (indicateur vert = actif). |
| **Sliders** (TTS, Vitesse, Hotword, Transparence) | Glisse à la souris ou molette pour ajuster la valeur ; une barre affiche le niveau. |
| **Options** (Voice Select, Shortcuts) | Clic ou molette pour parcourir les choix. |
| **Response Mode** | Clic pour changer le mode de réponse (injecté dans le prompt Gemini). |
| **Mic Toggle** | Coupe/rétablit le micro en direct. |
| **Audio Test** | Émet un bip de test. |
| **Routines** | Clic sur un nom de routine pour l'exécuter ; `Reload` recharge le fichier. |

Les réglages sont conservés dans `UI/menu_state.json` au redémarrage.
`Échap` pour quitter.

## Mémoire persistante locale

Jarvis possède une mémoire durable légère basée sur SQLite (`src/memory.py`).
Elle sert à conserver uniquement les informations importantes sur l'utilisateur
(prénom, préférences, projets, personnes importantes, décisions, configuration),
pas l'intégralité des conversations.

### Stockage

Par défaut, la base est stockée hors du code source :

```text
~/.jarvis/memory.db
```

Le dossier peut être changé avec `JARVIS_DATA_DIR` ou le chemin complet avec
`JARVIS_MEMORY_DATABASE_PATH`. Les fichiers `data/` et `*.db` sont ignorés par
Git pour éviter d'envoyer des souvenirs privés dans le repository.

### Fonctionnement

- Au démarrage d'une session Gemini Live, Jarvis injecte seulement quelques
  souvenirs importants/récents dans un bloc séparé `MEMORY — ...`.
- Pendant la conversation, Gemini peut appeler les outils mémoire pour rechercher
  ou modifier les souvenirs : `remember`, `recall`, `list_memories`,
  `search_memories`, `update_memory`, `delete_memory`, `forget`, `clear_memory`.
- Si Gemini Live fournit une transcription de l'audio utilisateur, Jarvis lance
  en fin de tour une extraction locale conservatrice (`Souviens-toi que...`,
  `Je préfère...`, `Mon projet actuel...`, `J'utilise maintenant...`). Cette
  extraction ne nécessite pas de clé Gemini supplémentaire et ne mémorise pas les
  phrases banales.
- Les souvenirs similaires reçoivent une clé de sujet (`subject_key`) afin de
  mettre à jour une information existante plutôt que créer des contradictions
  (ex. Python 3.12 → Python 3.13.9).
- La mémoire est non bloquante : si SQLite est inaccessible ou corrompu, Jarvis
  continue à fonctionner sans mémoire.

### Commandes naturelles

Exemples :

- « Souviens-toi que je préfère les réponses en français. »
- « Qu'est-ce que tu sais sur moi ? »
- « Recherche dans ta mémoire mon projet actuel. »
- « Oublie que j'utilise Python 3.12. »
- « Efface toute ta mémoire. » (demande une confirmation)

### Configuration

Dans `.env` :

```env
JARVIS_MEMORY_ENABLED=1
JARVIS_MEMORY_DATABASE_PATH=C:\\Users\\Moi\\.jarvis\\memory.db  # optionnel
JARVIS_MEMORY_MAX_RESULTS=5
JARVIS_MEMORY_MIN_IMPORTANCE=1
```

Mettre `JARVIS_MEMORY_ENABLED=0` désactive la mémoire sans supprimer la base.

## Routines (macros vocales)

Une routine est un **enchaînement d'outils nommé et rejouable**. Elle ne peut
rien faire de plus que ce que Jarvis sait déjà faire : la liste blanche est
préservée, et les outils destructeurs y sont interdits.

### Créer et lancer

- « Crée une routine *mode travail* qui ouvre VS Code, met le volume à 30 et
  ouvre Spotify. »
- « Lance le mode travail. »
- « Quelles routines est-ce que tu connais ? »
- « Ajoute une pause de 2 secondes avant Spotify dans le mode travail. »
- « Planifie le mode travail en semaine à 9h. »
- « Supprime la routine cinéma. » (demande une confirmation)

### Stockage

Les routines vivent dans un fichier JSON **lisible et modifiable à la main** :

```text
~/.jarvis/routines.json
```

```json
{
  "version": 1,
  "routines": [
    {
      "name": "mode travail",
      "description": "Session de code du matin",
      "enabled": true,
      "schedule": { "time": "09:00", "days": [0, 1, 2, 3, 4] },
      "steps": [
        { "tool": "open_application", "args": { "application": "vscode" } },
        { "tool": "wait", "args": { "seconds": 2 } },
        { "tool": "set_volume", "args": { "volume": 30 } }
      ]
    }
  ]
}
```

`days` suit la convention Python : lundi = 0, dimanche = 6. Un fichier édité à
la main est rechargé automatiquement (l'orbe le détecte en moins d'une seconde,
ou via `Reload` dans le menu Routines).

À la voix ou en ligne de commande, les étapes acceptent aussi une syntaxe
compacte, plus facile à dicter :

```text
open_application(vscode); wait(2); set_volume(30); open_website(spotify)
```

### Garde-fous

| Règle | Détail |
|---|---|
| **Liste blanche** | Une étape ne peut appeler qu'un outil existant de `TOOL_FUNCTIONS`. |
| **Outils interdits** | `shutdown_pc`, `restart_pc`, `delete_notes`, `clear_memory`, `forget`, `delete_memory`, `update_memory`, `run_routine` (pas de récursion) et les outils de gestion des routines. |
| **Refus explicite** | Une étape interdite fait **échouer** la création, plutôt que d'être retirée en silence : tu ne peux pas croire posséder une routine qui n'en fait pas autant qu'annoncé. |
| **Limites** | 25 étapes par routine, 50 routines, pause de 60 secondes maximum. |
| **Non bloquant** | Une étape en échec est rapportée mais n'interrompt pas la routine ; depuis l'orbe, l'exécution a lieu hors du thread graphique. |

`list_routine_tools` renvoie à tout moment la liste des outils autorisés.

## Rappels persistants

Les minuteurs (`set_timer`) vivent en mémoire et disparaissent au redémarrage.
Les **rappels** (`set_reminder`) sont conservés dans SQLite
(`~/.jarvis/schedule.db`) et rejoués au lancement de Jarvis — un rappel manqué
parce que le PC était éteint est annoncé au démarrage, avec son heure prévue.

- « Rappelle-moi d'appeler le dentiste demain à 9h. »
- « Rappelle-moi la réunion chaque lundi à 8h30. »
- « Quels sont mes rappels ? »
- « Annule le rappel numéro 3. »

Échéances comprises : `dans 20 minutes`, `dans une heure et demie`,
`demain à 9h`, `après-demain à 10h`, `lundi à 8h30`, `ce soir à 21h`, `midi`,
`12/03/2027 à 14h`, `le 12 mars`, ou une date ISO `2026-09-15T08:45`.
Récurrences : `daily`, `weekdays`, `weekends`, `weekly`, `monthly`, `hourly`
(« tous les jours », « en semaine », « chaque semaine »… sont reconnus).

Un rappel peut aussi déclencher une routine plutôt qu'annoncer un texte
(`set_reminder(text=..., when=..., routine="mode travail")`).

Le planificateur tourne dans un thread de fond démarré avec Jarvis (modes
console, `--ui` et `--desktop`). Il vérifie les échéances toutes les 15
secondes. Une routine planifiée ne part que dans les 5 minutes suivant son
heure — pas question de lancer le « mode travail » à 15h parce qu'il était
prévu à 9h — et jamais deux fois pour la même échéance.

### Configuration

Dans `.env` :

```env
JARVIS_ROUTINES_ENABLED=1
JARVIS_REMINDERS_ENABLED=1
JARVIS_ROUTINES_PATH=C:\\Users\\Moi\\.jarvis\\routines.json      # optionnel
JARVIS_SCHEDULE_DATABASE_PATH=C:\\Users\\Moi\\.jarvis\\schedule.db  # optionnel
```

Mettre l'une des variables à `0` désactive la fonctionnalité sans supprimer les
données.

## Liste de tâches (todo)

Les notes sont un journal ; la **todo list** a en plus un état *fait / à faire*.
Elle vit dans `~/.jarvis/todo.db` (SQLite, comme `memory.db` et `schedule.db`).

| Phrase | Effet |
|---|---|
| « ajoute *réviser la présentation* à ma todo » | `add_todo` |
| « ajoute *appeler le dentiste* pour demain à 9h, priorité haute » | `add_todo` avec échéance et priorité |
| « qu'est-ce qu'il me reste à faire ? » | `list_todos` |
| « marque la présentation comme faite » | `complete_todo` (par libellé ou par identifiant) |
| « remets cette tâche à faire » / « supprime-la » | `reopen_todo` / `delete_todo` |
| « vide les tâches terminées » | `clear_todos(only_done=true)` |

Les tâches sont triées par priorité puis par échéance, et sont incluses dans la
sauvegarde JSON. `JARVIS_TODO_DATABASE_PATH` permet de changer le chemin.

## Journal d'activité et sauvegarde

Chaque outil déclenché est consigné **localement** dans `~/.jarvis/activity.db` :
nom de l'outil, résumé court des arguments, succès, heure. Rien ne part sur le
réseau, les entrées de plus de 30 jours sont purgées, et les outils de simple
lecture (heure, listes…) sont ignorés.

| Phrase | Effet |
|---|---|
| « qu'as-tu fait aujourd'hui ? » / « et hier ? » | `get_activity_log` |
| « efface le journal » | `clear_activity_log` (confirmation requise) |
| « sauvegarde ta mémoire » | `backup_data` → `~/.jarvis/backups/jarvis_backup_*.json` |
| « quelles sauvegardes existent ? » | `list_backups` |

La sauvegarde rassemble mémoire, routines, tâches, rappels, notes et un extrait
du journal dans **un seul fichier JSON lisible**, facile à copier ailleurs.

```env
JARVIS_ACTIVITY_ENABLED=0                 # désactive le journal
JARVIS_ACTIVITY_RETENTION_DAYS=30         # durée de conservation
JARVIS_BACKUP_DIR=D:\\Sauvegardes\\Jarvis   # dossier de sauvegarde
```

## Fonctions

Jarvis dispose de **101 outils** déclarés dans `src/tools.py` (voir
`TOOL_FUNCTIONS` / `TOOL_DECLARATIONS`).

| Catégorie | Outils |
|---|---|
| **Applications** | `open_application`, `close_application`, `is_application_running`, `list_applications`, `list_running_applications` |
| **Audio** | `set_volume`, `volume_up`, `volume_down`, `get_volume`, `mute_audio`, `unmute_audio`, `toggle_mute` |
| **Multimédia** | `media_play_pause`, `media_next`, `media_previous`, `media_stop` |
| **Système** | `get_system_info`, `get_battery_status`, `get_disk_usage`, `get_folder_size`, `take_screenshot`, `lock_workstation`, `show_desktop`, `turn_off_screen`, `sleep_pc`, `hibernate_pc`, `shutdown_pc`, `restart_pc`, `cancel_shutdown`, `set_brightness`, `empty_recycle_bin`, `show_notification`, `wake_on_lan` |
| **Clavier** | `type_text`, `press_key` |
| **Presse-papiers** | `get_clipboard`, `set_clipboard`, `get_clipboard_history`, `paste_from_history`, `clear_clipboard_history` |
| **Date / heure** | `get_local_time`, `get_local_date`, `get_datetime`, `days_until` |
| **Minuteurs** | `set_timer`, `list_timers`, `cancel_timer` |
| **Rappels persistants** | `set_reminder`, `list_reminders`, `cancel_reminder` |
| **Routines** | `create_routine`, `run_routine`, `list_routines`, `describe_routine`, `update_routine`, `delete_routine`, `list_routine_tools` |
| **Notes** | `take_note`, `read_notes`, `delete_notes` |
| **Todo** | `add_todo`, `list_todos`, `complete_todo`, `reopen_todo`, `delete_todo`, `clear_todos` |
| **Journal & sauvegarde** | `get_activity_log`, `clear_activity_log`, `backup_data`, `list_backups` |
| **Mémoire** | `remember`, `recall`, `list_memories`, `search_memories`, `update_memory`, `delete_memory`, `forget`, `clear_memory` |
| **Web** | `open_website`, `list_websites`, `open_url`, `web_search`, `search_youtube`, `search_wikipedia`, `open_maps`, `get_directions`, `translate_text`, `get_weather`, `get_forecast`, `find_something_to_watch`, `draft_email`, `check_internet` |
| **Fichiers** | `open_folder`, `list_folder`, `search_files`, `read_file_aloud` |
| **Calcul & divers** | `calculate`, `random_number`, `flip_coin`, `roll_dice`, `pick_random` |

Exemples de phrases : « ouvre YouTube », « quelle météo à Lyon ? », « mets un
minuteur de 10 minutes pour les pâtes », « combien font racine de 144 fois
3 ? », « note que je dois appeler Paul », « capture l'écran », « verrouille le
PC », « cherche Iron Man sur Wikipédia », « itinéraire vers Lille », « lance le
mode travail », « rappelle-moi d'appeler le dentiste demain à 9h », « écris
*bonjour* dans le champ », « appuie sur entrée », « fais ctrl+s », « éteins
l'écran », « mets le PC en veille », « vide la corbeille », « combien pèse
Téléchargements ? », « quel temps fera-t-il cette semaine ? », « que regarder ce
soir sur Netflix ? », « rédige un email à paul@example.com pour annuler la
réunion », « lis-moi le fichier notes.md », « recolle ce que j'avais copié
avant », « réveille le PC du bureau », « qu'as-tu fait aujourd'hui ? »,
« sauvegarde ta mémoire ».

### Nouveautés Windows

* **Clavier** — `type_text` tape un texte Unicode dans la fenêtre active,
  `press_key` envoie une touche de la liste blanche `KEYS` avec des
  modificateurs (`ctrl`, `alt`, `maj`, `win`) : « fais ctrl+s ».
* **Énergie & écran** — `sleep_pc` (veille), `hibernate_pc` (hibernation) et
  `turn_off_screen` (écran éteint sans verrouillage) complètent
  `shutdown_pc` / `restart_pc`.
* **Disque** — `empty_recycle_bin` (confirmation obligatoire) et
  `get_folder_size`, qui donne le poids d'un dossier autorisé et ses cinq plus
  gros éléments.
* **Notifications** — `show_notification` affiche un toast Windows ; les
  minuteurs et les rappels en émettent désormais un automatiquement, en plus du
  bip et de la voix.
* **Presse-papiers** — un veilleur léger enregistre les textes copiés dans
  `~/.jarvis/clipboard_history.json` (50 entrées max) : `get_clipboard_history`
  les liste et `paste_from_history` en remet un dans le presse-papiers,
  éventuellement collé directement avec `ctrl+v`.
* **Réseau** — `wake_on_lan` envoie un paquet magique pour réveiller un autre
  PC du réseau local.
* **Fichiers** — `read_file_aloud` renvoie le contenu d'un fichier texte d'un
  dossier autorisé pour que Jarvis le lise ou le résume à voix haute.
* **Email** — `draft_email` prépare un brouillon `mailto:` pré-rempli ; Jarvis
  rédige, l'utilisateur relit et envoie lui-même.
* **Météo & loisirs** — `get_forecast` donne jusqu'à 7 jours de prévisions
  (open-meteo, sans clé API, repli sur wttr.in) et `find_something_to_watch`
  ouvre la fiche ou le catalogue JustWatch correspondant.

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

Les actions irréversibles (`shutdown_pc`, `restart_pc`, `hibernate_pc`,
`empty_recycle_bin`, `delete_notes`, `delete_routine`, `clear_todos`,
`clear_activity_log`, `clear_clipboard_history`) exigent un paramètre
`confirm=true`, demandé oralement à l'utilisateur. Elles sont également
interdites comme étape de routine (`FORBIDDEN_TOOLS` dans `src/routines.py`).

Le clavier est lui aussi sous liste blanche : `press_key` n'accepte que les
touches déclarées dans `KEYS` (lettres, chiffres, F1–F12, entrée, tabulation,
échap, flèches…) et les modificateurs de `MODIFIERS`, et `type_text` est limité
à 2 000 caractères.

## Tests

```bash
python -m unittest discover tests
```

Les tests sont multiplateformes et ne déclenchent aucune action réelle
(ni ouverture d'application, ni navigateur). Les tests mémoire, routines et
rappels utilisent des fichiers et des bases SQLite temporaires, et ne
nécessitent pas de clé Gemini réelle.

