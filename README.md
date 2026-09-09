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
| **Routines** | `Catalogue` affiche toutes les routines et leurs interrupteurs ; les noms du menu lancent les macros personnelles actives. `Reload` recharge le fichier. |

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

### 12 routines préconfigurées — un seul interrupteur

Le catalogue est ajouté **automatiquement**, y compris sur une installation
existante. Toutes les routines sont **désactivées par défaut** : rien ne se
lance avant votre accord. Aucun compte, logiciel tiers, ville, chemin ou horaire
à renseigner, et aucun appel Gemini supplémentaire pour les exécuter.

**Pour activer/désactiver :**

- **Orbe (`--ui`) :** menu **Routines → Catalogue** (touche `5`, puis `Catalogue`).
- **Orbe ou overlay (`--desktop`) :** icône Jarvis dans la zone de notification → **Routines…**.
- **À la voix, tous les modes :** « Active la routine hydratation » / « Désactive
  la routine pause visuelle ». Jarvis utilise `update_routine(name=..., enabled=true/false)`
  sans demander de configuration.

Le panneau défile pour rendre **toutes** les routines accessibles, pas seulement
les six raccourcis de macros personnelles du menu radial. Chaque carte affiche
l'effet exact, les horaires et l'état enregistré. Une activation par la voix se
reflète aussi dans le panneau.

| Routine | Déclenchement préconfiguré (heure locale du PC) | Effet réel |
|---|---|---|
| **Briefing du matin** | Lun–ven, 9 h | Date et prochains rappels Jarvis de la journée, ou « aucun rappel prévu ». |
| **Hydratation** | Tous les jours, toutes les 2 h de 10 h à 18 h | Notification pour penser à boire de l'eau. |
| **Pause visuelle** | Lun–ven, toutes les 20 min de 9 h 20 à 17 h 40 | Invitation à regarder au loin pendant 20 secondes. |
| **Pause active** | Lun–ven, toutes les heures de 9 h 55 à 17 h 55 | Invitation à changer de position ou marcher un peu, sans verrouiller le PC. |
| **Pause déjeuner** | Lun–ven, 12 h 30 | Notification de pause, sans fermer ni réduire de fenêtre. |
| **Fin de journée** | Lun–ven, 18 h | Checklist : enregistrer son travail, noter la suite, déconnecter. Aucune extinction automatique. |
| **Préparer demain** | Tous les jours, 20 h 30 | Prochains rappels Jarvis du lendemain, récurrences incluses. |
| **Revue hebdomadaire** | Vendredi, 17 h | Prochains rappels sur sept jours, aujourd'hui inclus. |
| **Batterie faible** | Vérification toutes les 5 min | Alerte à ≤ 20 %, uniquement débranché ; au plus une notification par heure. Silencieuse sans batterie. |
| **Espace disque** | Vérification toutes les heures | Alerte sous 10 % d'espace libre sur le disque du dossier utilisateur ; au plus une notification par 24 h. Aucun fichier supprimé. |
| **Mode focus** | À la demande | Active une session de révision : bloque jeux, streaming, réseaux sociaux, achats, hasard et bavardages hors travail ; ferme les distractions connues si Windows le permet. |
| **Mode jeu** | À la demande | Réduit les processus non essentiels, masque les overlays/notifications, bloque ouvertures/fermetures/captures/interactions écran. Le volume et la sortie du mode restent autorisés. |

Les briefings affichent jusqu'à trois échéances, puis le nombre restant. Ils
lisent uniquement les **rappels locaux Jarvis**, pas un agenda Google/Outlook.
Ils fonctionnent aussi si aucun rappel n'a encore été créé et ne modifient pas
les rappels existants.

Les notifications sont visibles dans la zone de notification Windows (ou une
carte non modale si la zone n'est pas disponible), même lorsque Jarvis est en
veille. En mode console Windows, des bulles natives sont envoyées en arrière-plan ;
le texte reste aussi dans la console. Les réglages de notifications de Windows
peuvent masquer les bulles. Les alertes batterie/disque ne produisent **aucun
message quand tout va bien**, et leur anti-spam survit au redémarrage.

**Jarvis doit rester ouvert**, avec les routines et le planificateur actifs.
L'activation sauvegarde simplement le choix ; les actions se déclenchent ensuite
selon les horaires. Le PC n'est jamais réveillé : une échéance de plus de cinq
minutes est ignorée, sans rafale de rattrapage. Désactiver une routine empêche
également son lancement manuel et les rappels qui la ciblent.

Les choix sont conservés dans `~/.jarvis/routines.json`. L'installation ne
remplace pas vos macros personnelles ni leurs modifications ; en cas d'homonyme,
le preset reçoit un suffixe `(Jarvis)`. Une routine explicitement supprimée à la
voix ne réapparaît pas au redémarrage. Le catalogue livré est défini dans
`src/routine_presets.py` ; aucune copie ou import manuel n'est nécessaire.

### Modes focus et jeu

Ces deux routines s'appuient sur un garde-fou runtime (`src/modes.py`) : même si
Gemini tente une action bloquée, Jarvis la refuse localement avant d'agir sur le
PC.

- « Active le mode focus » / « lance une session focus de 45 minutes » : Jarvis
  refuse les distractions (jeux, YouTube/Twitch/Netflix, réseaux sociaux,
  achats, hasard, commandes multimédia) et garde les actions utiles aux
  révisions.
- « Active le mode jeu » : Jarvis ferme en best effort des processus lourds non
  essentiels, abaisse sa priorité quand Windows/psutil le permettent, masque
  halo/orbe/notifications et bloque tout ce qui peut toucher au jeu ou à
  l'écran. Les commandes de volume (`set_volume`, `volume_up`, `volume_down`,
  mute/unmute) restent disponibles.
- « Désactive le mode Jarvis » / « quitte le mode jeu » revient au mode normal.

L'état actif est conservé dans `~/.jarvis/mode.json` (configurable avec
`JARVIS_MODES_PATH`). Un mode peut être lancé avec une durée en minutes ; à
l'expiration, Jarvis repasse automatiquement en mode normal.

### Créer et lancer ses propres routines

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

Une plage répétée peut aussi utiliser `end_time` et `interval_minutes`, par
exemple `{"time": "09:20", "end_time": "17:40", "interval_minutes": 20,
"days": [0, 1, 2, 3, 4]}`. Les bornes sont incluses, sans passage de minuit ;
l'intervalle est un entier de 5 à 1 440 minutes. Les horaires simples existants
restent compatibles.

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
| **Limites** | 25 étapes par routine, 50 routines personnelles (en plus des 12 presets), pause de 60 secondes maximum. |
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
JARVIS_MODES_ENABLED=1
JARVIS_ROUTINES_PATH=C:\\Users\\Moi\\.jarvis\\routines.json      # optionnel
JARVIS_SCHEDULE_DATABASE_PATH=C:\\Users\\Moi\\.jarvis\\schedule.db  # optionnel
JARVIS_MODES_PATH=C:\\Users\\Moi\\.jarvis\\mode.json              # optionnel
```

Mettre l'une des variables à `0` désactive la fonctionnalité sans supprimer les
données.

## Fonctions

Jarvis dispose de **86 outils** déclarés dans `src/tools.py` (voir
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
| **Notifications locales** | `notify_user`, `show_reminder_briefing`, `check_battery_alert`, `check_disk_alert` |
| **Rappels persistants** | `set_reminder`, `list_reminders`, `cancel_reminder` |
| **Routines** | `create_routine`, `run_routine`, `list_routines`, `describe_routine`, `update_routine`, `delete_routine`, `list_routine_tools` |
| **Modes focus/jeu** | `activate_focus_mode`, `activate_game_mode`, `disable_jarvis_mode`, `get_jarvis_mode` |
| **Notes** | `take_note`, `read_notes`, `delete_notes` |
| **Mémoire** | `remember`, `recall`, `list_memories`, `search_memories`, `update_memory`, `delete_memory`, `forget`, `clear_memory` |
| **Web** | `open_website`, `list_websites`, `open_url`, `web_search`, `search_youtube`, `search_wikipedia`, `open_maps`, `get_directions`, `translate_text`, `get_weather`, `check_internet` |
| **Fichiers** | `open_folder`, `list_folder`, `search_files` |
| **Calcul & divers** | `calculate`, `random_number`, `flip_coin`, `roll_dice`, `pick_random` |
| **Protocoles** | `run_protocol`, `list_protocols`, `cancel_protocol` |

Exemples de phrases : « ouvre YouTube », « quelle météo à Lyon ? », « mets un
minuteur de 10 minutes pour les pâtes », « combien font racine de 144 fois
3 ? », « note que je dois appeler Paul », « capture l'écran », « verrouille le
PC », « cherche Iron Man sur Wikipédia », « itinéraire vers Lille », « lance le
mode travail », « active le mode focus », « active le mode jeu », « désactive le
mode Jarvis », « rappelle-moi d'appeler le dentiste demain à 9h ».

## Les Protocoles — « Jarvis, wake up »

Une fonction volontairement discrète : aucun bouton dans l'orbe, aucune ligne
dans les menus radiaux. Un **protocole** est une séquence cinématique plein
écran qui interroge réellement la machine pendant qu'elle se joue.

Ce n'est pas une animation décorative : chaque ligne du journal affiche une
**vraie mesure** (charge CPU, RAM, espace disque, batterie, latence réseau,
nombre de souvenirs en base, routines armées, rappels en attente).

### Les quatre protocoles

| Protocole | Ce qu'il fait | Couleur |
|---|---|---|
| `wake_up` | Allumage complet en 12 étapes : noyau, opérateur, CPU, RAM, disque, alimentation, liaison distante, banque mémoire, routines, planificateur, audio. | Cyan |
| `diagnostic` | Bilan système condensé en 7 relevés. | Vert |
| `focus` | Baisse le volume à 25 % et arme un minuteur de 25 minutes. | Ambre |
| `stand_down` | Mise en veille : sauvegarde, atténuation audio, micro maintenu. | Violet |

### Les cinq façons de le déclencher

1. **À la voix** — « Jarvis, réveille-toi », « wake up », « lance un diagnostic
   complet », « mode concentration ». Gemini appelle l'outil `run_protocol`.
2. **Code secret tapé** — l'orbe ayant le focus, tapez simplement `wakeup`,
   `jarvis`, `reveil`, `bilan`, `focus` ou `veille`. Aucun champ de saisie :
   les lettres sont reconnues au vol, et la saisie expire après 1,6 s.
3. **Geste secret** — trois clics rapides au cœur de l'orbe.
4. **Icône de notification** — menu « Protocoles », pour qui ne connaît pas
   les codes.
5. **En ligne de commande** — sans micro ni clé API :

```bat
python -m src.main --protocol wake_up
python -m src.main --protocol diagnostic
```

En console, la séquence se dessine en ASCII ; avec l'interface, elle s'affiche
en plein écran (réacteur à anneaux contrarotatifs, balayage, journal qui
s'écrit à la machine à écrire).

**Échap interrompt toujours** un protocole en cours — et c'est sa seule action
tant qu'une séquence tourne : impossible de fermer Jarvis par accident.

### Garanties

* Un protocole ne peut appeler **aucun outil destructeur** : la liste
  `FORBIDDEN_TOOLS` des routines s'applique telle quelle (un beat qui tenterait
  `shutdown_pc` est refusé et signalé en jaune).
* Une sonde ou un outil qui échoue **dégrade la ligne affichée**, jamais la
  séquence.
* Un seul protocole tourne à la fois : le relancer annule proprement le
  précédent.
* `src/protocols.py` n'importe **ni Qt ni sounddevice** — d'où les tests
  complets sans interface.

Les codes secrets ne commencent jamais par `m`, `s` ou `d`, afin que les
raccourcis d'une lettre (micro, stop, debug) restent instantanés.

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
Les modes focus/jeu ajoutent une seconde couche : un outil pourtant autorisé en
temps normal peut être refusé avec `blocked_by_mode=true` si le mode actif le
bloque.

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
nécessitent pas de clé Gemini réelle. Les tests des presets couvrent les douze
routines, la migration sans écrasement, la persistance des interrupteurs, les
plages horaires, les alertes conditionnelles, les modes focus/jeu et
l'anti-spam. Le panneau et le relais de notifications Qt sont testés offscreen
(clavier, défilement,
thread graphique, erreur d'écriture), sans afficher de bulle Windows réelle.

Les Protocoles ajoutent 49 tests (`tests/test_protocols.py`,
`tests/test_protocol_ui.py`) : catalogue et recherche tolérante, progression,
annulation, sonde ou outil en échec, refus des outils destructeurs, codes
secrets (expiration, accents, non-collision avec les raccourcis `m`/`s`/`d`),
geste des trois clics, et rendu offscreen de l'overlay à chaque étape sur
trois résolutions.

