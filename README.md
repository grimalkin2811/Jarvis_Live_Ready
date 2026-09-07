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

### Retour visuel permanent

L'orbe reflète en continu l'état de Jarvis, sans regarder la console :

* un libellé discret sous l'orbe indique **INITIALISATION…** (chargement du
  modèle), **À L'ÉCOUTE**, **RÉFLEXION…**, **RÉPONSE EN COURS** ou
  **MICRO COUPÉ** ;
* l'orbe respire plus fort quand Jarvis écoute ou parle, et s'atténue quand
  le micro est coupé ;
* chaque action dans un menu affiche une **pastille de confirmation** au-dessus
  de l'orbe (ex. `Micro : coupé`, `mode travail ✓`) — vous savez toujours si
  votre clic a été pris en compte ;
* l'icône de notification (tray) permet d'afficher Jarvis ou de le quitter
  proprement, y compris en mode `--desktop` qui n'a pas de fenêtre interactive.

### Menus radiaux

En mode `--ui`, survole les bords de l'orbe pour déplier les menus radiaux
(Voice, System, Memory, Appearance, Routines) et interagis directement avec les
réglages :

| Contrôle | Interaction |
|---|---|
| **Toggles** | Clic pour activer/désactiver (indicateur vert = actif). |
| **Sliders** (Volume, Vitesse, Hotword, Transparence) | Glisse à la souris (l'axe s'adapte au menu) ou molette (pas fin de 2 %) ; une barre affiche le niveau. |
| **Options** (Voice Select) | Clic ou molette (dans les deux sens) pour parcourir les choix. |
| **Response Mode** | Clic pour changer le mode de réponse (injecté dans le prompt Gemini). |
| **Mic Toggle** | Coupe/rétablit le micro en direct. |
| **Interrupt Word** | Active/désactive l'interruption vocale (dire « stop » coupe Jarvis). |
| **Stop Speaking** | Coupe immédiatement la réponse en cours. |
| **Audio Test** | Émet un bip de test synthétisé. |
| **Routines** | Clic sur un nom de routine pour l'exécuter ; `Reload` recharge le fichier. `⏱` = planifiée, `⏸` = désactivée, `▶` = manuelle. |

Les réglages sont conservés dans `UI/menu_state.json` au redémarrage — ils
s'appliquent aussi au mode console.

### Raccourcis clavier (mode `--ui`)

| Touche | Effet |
|---|---|
| `1` … `5` | Ouvre directement le menu radial correspondant. |
| Flèches / `Entrée` | Navigue dans le menu ouvert et active l'item sélectionné. |
| `M` | Coupe/rétablit le micro instantanément. |
| `S` | **Stop** : coupe immédiatement la réponse en cours. |
| `Échap` | Ferme d'abord le menu ouvert ; un second appui quitte Jarvis. |
| Clic droit | Ferme le menu radial ouvert. |

### Réglages réellement appliqués

Chaque contrôle du menu agit vraiment :

| Réglage | Effet |
|---|---|
| **TTS Volume** | Gain audio appliqué en temps réel sur la voix de Jarvis. |
| **Voice Select** | Change la voix prébuilt Gemini (reconnexion automatique et silencieuse de la session). |
| **Speech Speed** | Consigne de débit injectée dans le prompt système (posé / normal / vif). |
| **Always Listening** | Écoute continue : Jarvis reste actif sans dire « Hey Jarvis » (désactivé par défaut). |
| **Interrupt Word** | Interruption vocale : parler par-dessus Jarvis (« stop ») coupe sa réponse (activé par défaut). |
| **Startup** | Crée/supprime réellement le lanceur dans le dossier de démarrage Windows. |
| **Long-term Memory** | Active/désactive la mémoire persistante en direct. |
| **Reset Settings** | Remet les réglages du menu à leurs valeurs par défaut. |

Les compteurs (mémoire, rappels, routines) sont lus au plus une fois par
seconde et mis en cache : le rendu de l'orbe reste fluide (~60 FPS) sans
solliciter SQLite à chaque image.

## Interrompre Jarvis pendant sa réponse

Plus besoin de subir un monologue : **dites simplement « stop »** (ou
n'importe quelle phrase, « attends », « ça suffit ») pendant que Jarvis parle.
Il se tait immédiatement et vous rend la parole.

| Moyen d'interrompre | Où |
|---|---|
| **La voix** — parler par-dessus Jarvis | Tous les modes (console, `--ui`, `--desktop`) |
| **Touche `S`** | Mode `--ui` |
| **Menu radial Voice → `Stop Speaking`** | Mode `--ui` |

### Comment ça marche

1. Pendant que Jarvis parle, le micro reste analysé localement (RMS par blocs
   de 80 ms). Trois blocs consécutifs nettement au-dessus du niveau ambiant
   déclenchent l'interruption.
2. La lecture audio est **coupée instantanément** (file de sortie vidée), sans
   attendre le réseau.
3. Les derniers blocs micro (jusqu'à ~0,6 s) sont réémis vers Gemini, puis le
   flux reprend : Gemini entend la phrase **entière** — pas seulement la fin du
   mot « stop » — et arrête son tour côté serveur.
4. Si Gemini ne confirme jamais l'interruption (fausse détection, simple
   bruit), la lecture reprend automatiquement au bout de 4 secondes : Jarvis
   ne peut pas rester muet à cause de cette fonctionnalité.

### Garde-fous (pour que Jarvis ne s'interrompe pas tout seul)

| Garde-fou | Détail |
|---|---|
| **Plancher de bruit adaptatif** | Le niveau de l'écho des enceintes est appris en continu ; il faut le dépasser d'un bon facteur (2,6×) pour interrompre. Avec un casque, le seuil devient naturellement très bas. |
| **Période de grâce** | Les 0,6 première seconde d'une réponse ne peuvent pas être coupées (le temps d'apprendre l'écho, et pour ne pas se couper sur la fin de votre propre phrase). |
| **3 blocs consécutifs** | Un claquement de porte ou un clic de souris ne suffit pas : il faut ~240 ms de parole. |
| **Anti-rebond** | Pas de seconde interruption dans la seconde et demie qui suit. |
| **Micro coupé** | Micro sur Off = aucune interruption possible (rien n'est analysé). |
| **Désactivable** | `Voice → Interrupt Word` (persisté dans `UI/menu_state.json`, clé `barge_in`). Le bouton `Stop Speaking` et la touche `S` continuent de fonctionner. |

Les seuils sont des constantes lisibles en haut de `AudioIO`
(`BARGE_IN_MIN_RMS`, `BARGE_IN_FACTOR`, `BARGE_IN_BLOCKS`,
`BARGE_IN_GRACE_SECONDS`) : si votre micro est très peu sensible et que « stop »
ne passe pas, baissez `BARGE_IN_MIN_RMS` ; si Jarvis se coupe tout seul avec des
enceintes fortes, augmentez `BARGE_IN_FACTOR`.

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

### Routines préconfigurées

Jarvis est livré avec **14 routines prêtes à l'emploi**, installées
automatiquement dans `~/.jarvis/routines.json` au premier lancement. Rien à
configurer : elles n'utilisent que des outils autorisés, sans clé API ni
chemin particulier. Elles se modifient, se suppriment et se replanifient
exactement comme tes propres routines, et une préconfiguration supprimée ne
revient pas au redémarrage.

| Routine | Ce qu'elle fait | Planification | Active par défaut |
|---|---|---|---|
| `mode travail` | Ouvre VS Code puis volume à 30 % | — | ✅ |
| `focus` | Coupe le son + minuteur de 25 min | — | ✅ |
| `réunion` | Son coupé, bureau nettoyé + minuteur de 45 min | — | ✅ |
| `pause café` | Rétablit le son + minuteur de 5 min | — | ✅ |
| `capture` | Capture d'écran + ouvre le dossier Images | — | ✅ |
| `musique` | Ouvre Spotify (web) + volume à 40 % | — | ✅ |
| `actualités` | Ouvre France Info puis Le Monde | — | ✅ |
| `bonjour` | Briefing : heure, date, météo, dernières notes | — | ✅ |
| `bilan système` | Internet, batterie, disque, ressources du PC | — | ✅ |
| `je pars` | Stoppe la musique, coupe le son, verrouille la session | — | ✅ |
| `je reviens` | Rétablit le son + redonne l'heure | — | ✅ |
| `mode cinéma` | Bureau nettoyé + volume à 80 % | — | ✅ |
| `bonne nuit` | Musique stoppée, volume et luminosité au minimum, session verrouillée | tous les jours à 23h30 | ⛔️ à activer |
| `réveil` | Son rétabli, volume à 20 %, actualités ouvertes | tous les jours à 7h30 | ⛔️ à activer |

Rien ne se déclenche tout seul : les deux routines planifiées sont livrées
**désactivées** — c'est toi qui décide de les activer.

- « Lance le mode travail. » / « Jarvis, bonjour ! »
- « Active la routine réveil. » — le déclenchement automatique s'arme.
- « Désactive la routine bonne nuit. » — elle ne s'exécute plus du tout.
- « Réinstalle les routines préconfigurées. » — récupère celles supprimées
  (`restore_preset_routines`, sans jamais écraser les routines existantes).

Une routine **désactivée** refuse de s'exécuter, à la voix comme
automatiquement — Jarvis le dit et propose de l'activer. Pour couper
uniquement le déclenchement horaire, dis plutôt « retire la planification
du réveil ». Dans le menu radial, `⏱ 07:30` signale une routine planifiée
active, `⏸` une routine désactivée, `▶` une routine manuelle.

Variable d'environnement : `JARVIS_PRESET_ROUTINES=0` désactive l'installation
automatique des préconfigurations sans toucher au fichier existant.

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
ou via `Reload` dans le menu Routines). Une routine préconfigurée porte le
marqueur `"preset": true` ; le tableau `presets_removed` (s'il existe) retient
les préconfigurations que tu as supprimées pour qu'elles ne reviennent pas au
prochain démarrage.

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

## Fonctions

Jarvis dispose de **75 outils** déclarés dans `src/tools.py` (voir
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
| **Rappels persistants** | `set_reminder`, `list_reminders`, `cancel_reminder` |
| **Routines** | `create_routine`, `run_routine`, `list_routines`, `describe_routine`, `update_routine`, `delete_routine`, `list_routine_tools`, `restore_preset_routines` |
| **Notes** | `take_note`, `read_notes`, `delete_notes` |
| **Mémoire** | `remember`, `recall`, `list_memories`, `search_memories`, `update_memory`, `delete_memory`, `forget`, `clear_memory` |
| **Web** | `open_website`, `list_websites`, `open_url`, `web_search`, `search_youtube`, `search_wikipedia`, `open_maps`, `get_directions`, `translate_text`, `get_weather`, `check_internet` |
| **Fichiers** | `open_folder`, `list_folder`, `search_files` |
| **Calcul & divers** | `calculate`, `random_number`, `flip_coin`, `roll_dice`, `pick_random` |

Exemples de phrases : « ouvre YouTube », « quelle météo à Lyon ? », « mets un
minuteur de 10 minutes pour les pâtes », « combien font racine de 144 fois
3 ? », « note que je dois appeler Paul », « capture l'écran », « verrouille le
PC », « cherche Iron Man sur Wikipédia », « itinéraire vers Lille », « lance le
mode travail », « rappelle-moi d'appeler le dentiste demain à 9h ».

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

Les actions irréversibles (`shutdown_pc`, `restart_pc`, `delete_notes`,
`delete_routine`) exigent un paramètre `confirm=true`, demandé oralement à
l'utilisateur.

## Tests

```bash
python -m unittest discover tests
```

Les tests sont multiplateformes et ne déclenchent aucune action réelle
(ni ouverture d'application, ni navigateur). Les tests mémoire, routines et
rappels utilisent des fichiers et des bases SQLite temporaires, et ne
nécessitent pas de clé Gemini réelle.

