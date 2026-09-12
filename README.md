# Jarvis Live Ready

Assistant vocal **Windows** avec Gemini Live (audio entrée/sortie, wake word
« Hey Jarvis », barge-in, mémoire persistante, routines, rappels, notifications,
modes focus/jeu, protocoles cinématiques et interface PySide6).

Cette version est conçue pour être **distribuée** : un installateur
`JarvisSetup.exe` installe l'application **sans Python ni `.venv`**, et un
launcher (`JarvisLauncher.exe`) reçoit automatiquement les nouvelles versions via
les **GitHub Releases**, **sans jamais toucher à vos données**.

---

## Pour les utilisateurs

### Installer Jarvis

1. Téléchargez `JarvisSetup-<version>.exe` depuis la page **Releases** du dépôt.
2. Double-cliquez dessus. L'installation se fait **pour votre compte** (aucun
   droit administrateur requis).
3. Deux raccourcis sont créés (bureau et menu Démarrer) au choix.
4. Lancez **Jarvis** depuis le raccourci (ou le launcher).

> Vous n'avez **rien** à installer à côté : ni Python, ni `.venv`, ni `pip`.
> L'application embarque tout ce dont elle a besoin.

### Premier lancement (configuration)

Au tout premier lancement, Jarvis vous demande :

* votre **prénom** ;
* votre **clé API Gemini** (sur <https://aistudio.google.com/app/apikey>) ;
* le **modèle** (conservé par défaut).

Ces informations sont enregistrées **sur votre PC**, dans
`%LOCALAPPDATA%\Jarvis\config\config.json`. Aucune clé n'est envoyée ailleurs ni
stockée dans l'application elle-même.

### Le launcher (interface graphique)

Double-cliquer le raccourci **Jarvis** ouvre le launcher, une vraie fenêtre
(PySide6) qui affiche :

* la **version installée** et la **dernière version** disponible ;
* l'**état** de l'installation (prêt, mise à jour disponible, corrompue…) ;
* un **journal** détaillant chaque opération ;
* les boutons **Lancer Jarvis**, **Vérifier les mises à jour**,
  **Mettre à jour** et **Quitter** ;
* le choix du mode : **Orbe** (recommandé), **Overlay bureau** ou **Console**.

### Mises à jour

Le launcher vérifie automatiquement les **GitHub Releases** au démarrage :

```text
JarvisLauncher.exe
   ├─ vérifie la version locale
   ├─ interroge GitHub (dernière release)
   ├─ si une version plus récente existe : vous demande confirmation
   ├─ télécharge (barre de progression) et vérifie (SHA-256)
   └─ remplace l'application, préserve vos données
```

Cliquez ensuite **Lancer Jarvis**. Pour les scripts et le diagnostic, le
launcher conserve un mode console : `JarvisLauncher.exe --check`,
`--validate`, `--no-gui`, `--console`, `--force`… (voir
`JarvisLauncher.exe --help`, historiquement : `--no-update` lance directement
sans vérifier).

Vos données **survivent** aux mises à jour :

| Donnée | Emplacement | Préservée |
|---|---|---|
| Configuration | `%LOCALAPPDATA%\Jarvis\config\` | ✅ |
| Mémoire (SQLite) | `%LOCALAPPDATA%\Jarvis\memory.db` | ✅ |
| Routines | `%LOCALAPPDATA%\Jarvis\routines.json` | ✅ |
| Rappels | `%LOCALAPPDATA%\Jarvis\schedule.db` | ✅ |
| Modes | `%LOCALAPPDATA%\Jarvis\mode.json` | ✅ |
| Préférences UI | `%LOCALAPPDATA%\Jarvis\ui\` | ✅ |
| Logs | `%LOCALAPPDATA%\Jarvis\logs\` | ✅ |
| Modèles OpenWakeWord | `%LOCALAPPDATA%\Jarvis\models\` | ✅ |

### Désinstallation

Lancez **Panneau de configuration → Programmes → Désinstaller Jarvis**
(vous pouvez aussi relancer l'installateur et choisir *Supprimer*).

L'uninstallateur supprime **uniquement** le programme. Vos données (mémoire,
config, routines…) sont **conservées**. Pour les effacer, supprimez le dossier
`%LOCALAPPDATA%\Jarvis`.

### Diagnostic

En cas de problème, les journaux se trouvent dans :
`%LOCALAPPDATA%\Jarvis\logs\jarvis.log` (avec rotation). En mode console,
lancez `JarvisLauncher.exe --console` pour voir la sortie en direct.

---

## Pour les développeurs

### Cloner et installer (environnement de développement)

Les développeurs utilisent encore un `.venv` (facultatif pour l'utilisateur
final, indispensable pour itérer).

```bash
# 1. Cloner
git clone https://github.com/grimalkin2811/Jarvis_Live_Ready.git
cd Jarvis_Live_Ready

# 2. Environnement virtuel
python -m venv .venv

# 3. Dépendances
.venv\Scripts\pip install -r requirements.txt   # Windows
# ou : source .venv/bin/activate && pip install -r requirements.txt  # Linux/macOS

# 4. Environnement "dev" : un fichier .env à la racine
#    (setup.bat le crée ; sinon copiez ce bloc)
copy .env.example .env              # Windows
```

Le build nécessite ensuite les outils de dev :

```bash
.venv\Scripts\pip install -r requirements-dev.txt   # ruff + pyinstaller
```

### Lancer en développement

```bash
.venv\Scripts\python -m src.main          # console / headless
.venv\Scripts\python -m src.main --ui     # orbe morphing interactif
.venv\Scripts\python -m src.main --desktop# overlay halo plein écran
.venv\Scripts\python -m src.main --protocol wake_up  # séquence cinématique
```

> En développement, `Jarvis.bat` et `setup.bat` créent le `.venv` et le `.env`
> automatiquement. Ils ne sont **pas** destinés à l'utilisateur final.

### Configurer en développement

Le `.env` (ou les variables d'environnement) reste supporté :

```env
JARVIS_USER=Prénom
GEMINI_API_KEY=AIza...
GEMINI_MODEL=gemini-2.5-flash-native-audio-preview-12-2025
JARVIS_MEMORY_ENABLED=1
JARVIS_ROUTINES_ENABLED=1
JARVIS_REMINDERS_ENABLED=1
```

Au premier lancement, Jarvis migre ce `.env` vers `config.json`.

### Lancer les tests

```bash
# Toute la suite (matériel audio et Gemini sont simulés)
.venv\Scripts\python -m unittest discover tests
```

Les tests sont multiplateformes et ne déclenchent aucune action réelle. Ceux
qui nécessitent Qt s'exécutent en mode `offscreen` (et passent sur Windows/CI).

### Construire le build local (Windows)

```powershell
# 1. Télécharge les modèles OpenWakeWord et appelle PyInstaller (app + launcher)
.\scripts\build_windows.ps1 -DistDir dist

# 2. Compile l'installateur Windows (Inno Setup)
.\scripts\download_models.py            # si non fait par le build
iscc packaging\installer.iss /DVERSION=1.0.0 /DSourceDir=..\dist
```

Résultats dans `dist/` :

```text
dist/
├── Jarvis/                       # dossier onedir de l'application (Jarvis.exe + _internal)
├── JarvisLauncher.exe            # le launcher
├── Jarvis-v<version>-portable.zip        # archive téléchargée par le launcher
├── Jarvis-v<version>-portable.zip.sha256 # empreinte SHA-256
└── JarvisSetup-<version>.exe     # installateur Inno Setup
```

La version provient d'**une seule source** : `src/version.py`. Toutes les
parties (Jarvis, launcher, updater, build, CI) la lisent.

### Architecture (code / données)

```text
%LOCALAPPDATA%\Jarvis\
├── JarvisLauncher.exe        # launcher (stable, met à jour l'app)
├── version.json              # marqueur de version locale
├── app\                      # l'application (Jarvis.exe + bundle) -> REMPLACÉE
│   ├── Jarvis.exe
│   └── _internal\
├── config\config.json        # données utilisateur
├── memory.db                 # mémoire SQLite
├── routines.json
├── schedule.db
├── mode.json
├── ui\                       # préférences de l'interface
├── logs\jarvis.log           # logs (rotation)
└── models\openwakeword\      # modèles wake word
```

Le principe est simple :

* **Code / application** : remplaçable à chaque mise à jour (`app\`).
* **Données utilisateur** : hors du code, jamais écrasées par une mise à jour.

---

## Fonctionnalités (détail)

> **Note configuration** : les sections ci-dessous mentionnent parfois `~/.jarvis`
> et `.env`. En développement, `.env` reste supporté et les fichiers de données
> restent dans `~/.jarvis` (Linux) ou `%LOCALAPPDATA%\Jarvis` (Windows dev).
> Dans l'**application distribuée**, tout est géré par `src/paths.py` et
> `config.json` (voir la section « Architecture »).

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

Par défaut, la base est stockée **hors du code source** :

```text
%LOCALAPPDATA%\Jarvis\memory.db      (Windows, application distribuée)
~/.jarvis/memory.db                  (Linux/macOS, développement)
```

Le dossier de données est centralisé dans `src/paths.py`. Il peut être changé
avec `JARVIS_DATA_DIR` (racine du dossier) ou, pour la mémoire
spécifiquement, avec `JARVIS_MEMORY_DATABASE_PATH`. Les fichiers `data/`,
`*.db` et les modèles téléchargés sont ignorés par Git pour éviter d'envoyer
des souvenirs privés dans le repository.

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

La distribution ajoute des tests ciblés : `test_version` (source unique de
version, comparaison sémantique), `test_paths` (emplacements de données
utilisateur), `test_config` (config JSON + migration depuis `.env`),
`test_updater` (détection de release, SHA-256, téléchargement, installation
atomique et rollback), `test_logging_setup` (logs à rotation) et
`test_first_run` (assistant de premier lancement).

### Publication d'une nouvelle version

1. Modifiez `src/version.py` (`__version__`).
2. `git add . && git commit -m "v1.0.1"`.
3. `git push`.
4. Créez un tag : `git tag v1.0.1 && git push origin v1.0.1`.
5. Le workflow GitHub Actions (`.github/workflows/build.yml`) construit
   automatiquement l'application, le launcher et l'installateur, puis **crée la
   release** avec :

   ```text
   JarvisSetup-<version>.exe
   Jarvis-v<version>-portable.zip
   Jarvis-v<version>-portable.zip.sha256
   ```

6. Les utilisateurs existants reçoivent cette version via le launcher
   automatiquement.


---

## Architecture de distribution (détail technique)

### Chaîne complète

```text
source
  ↓
PyInstaller 6.x (onedir)
  ↓
dist/Jarvis/
├── Jarvis.exe
└── _internal/
    ├── python311.dll        # CRITIQUE : doit rester dans _internal/
    ├── base_library.zip
    ├── PySide6/
    └── ...
  ↓ (robocopy, préserve _internal/)
dist/app/                     # copie exacte de dist/Jarvis/
├── Jarvis.exe
└── _internal/
    ├── python311.dll
    └── ...
  ↓ (Python zipfile, préserve _internal/)
dist/Jarvis-vX.Y.Z-portable.zip
├── Jarvis.exe
└── _internal/
    ├── python311.dll
    └── ...
  ↓ (Inno Setup, recursesubdirs)
JarvisSetup-X.Y.Z.exe
  ↓ (installation silencieuse)
%LOCALAPPDATA%/Jarvis/
├── JarvisLauncher.exe
├── version.json
└── app/
    ├── Jarvis.exe
    └── _internal/
        ├── python311.dll
        └── ...
  ↓ (launcher)
Jarvis.exe --ui
  ↓ (update)
Nouvelle version → validation → backup → remplacement atomique → rollback si échec
```

### Pourquoi python311.dll doit rester dans _internal/

PyInstaller 6.x produit une structure moderne où toutes les dépendances sont dans `_internal/`.
L'exécutable `Jarvis.exe` cherche `python311.dll` dans `_internal/` via un chemin codé en dur.
Si `python311.dll` se retrouve à la racine de `app/` (aplatissement), l'application échoue avec :

```text
[PYI-20752:ERROR] Failed to load Python DLL
'C:\Users\...\AppData\Local\Jarvis\app\_internal\python311.dll'.
LoadLibrary: Le module spécifié est introuvable.
```

Et on observe alors :

```text
C:\...\Jarvis\app\python311.dll          # existe (BAD)
C:\...\Jarvis\app\_internal\python311.dll # manquant (BAD)
```

### Garde-fous implémentés (v1.0.2+)

**1. Build Windows (`scripts/build_windows.ps1`) :**
- Validation immédiate après PyInstaller : `dist/Jarvis/_internal/python311.dll` doit exister
- Utilise `robocopy` (pas `Copy-Item *` qui peut aplatir `_internal/`)
- Validation après copie : `dist/app/_internal/python311.dll` doit exister
- Détection d'aplatissement : `dist/app/python311.dll` ne doit PAS exister
- Création ZIP via Python `zipfile` (robuste) au lieu de `Compress-Archive`
- Validation du ZIP : contient `_internal/python311.dll`, pas `python311.dll` à la racine
- Smoke test : `Jarvis.exe --smoke-test` doit démarrer

**2. Validation centralisée (`src/packaging_validation.py`) :**
- Source unique de vérité pour les fichiers critiques
- Utilisée par build, CI, updater, launcher, tests
- Détecte flattening : `python311.dll` à la racine = invalide

**3. Installer (`packaging/installer.iss`) :**
- `recursesubdirs` + `createallsubdirs` préservent `_internal/`
- Validation post-install dans `[Code]` : log si `_internal/python311.dll` manquant

**4. Launcher (`launcher/`) :**
- `core.py` : logique sans Qt (validation, version, mise à jour, lancement),
  partagée par les deux interfaces
- `gui.py` : fenêtre PySide6 (version, état, progression, journal) ; tâches
  réseau en `QThread`, exécutable compilé en mode *windowed*
- `main.py` : GUI par défaut sans arguments, console sinon (`--check`,
  `--validate`, `--no-gui`, `--force`, …) avec rattachement automatique au
  terminal parent
- Valide `app/_internal/python311.dll` avant lancement
- Message d'erreur clair si installation corrompue
- `--validate` : valide l'installation et quitte

**4b. Wake word embarqué (`src/wakeword.py`, correctif 1.1.1) :**
- Stratégie **ONNX d'abord** (`onnxruntime`, dépendance directe) : les trois
  modèles `melspectrogram.onnx`, `embedding_model.onnx`, `hey_jarvis_v0.1.onnx`
  sont résolus explicitement (bundle, dossier utilisateur, paquet installé)
  et passés avec chemins absolus à openWakeWord — aucune dépendance aux
  chemins par défaut du paquet ni à `tflite-runtime` en distribution
- `packaging/jarvis.spec` copie aussi les `.onnx` vers
  `openwakeword/resources/models` dans le bundle (filet de sécurité)
- `scripts/download_models.py` refuse un téléchargement incomplet (gate build)
- `scripts/validate_build.py --require-wakeword` exige les modèles (CI bloquant)
- `Jarvis.exe --smoke-test` instancie réellement le modèle et prédit sur de
  l'audio synthétique (bloquant en bundle, informatif en dev)
- La clé de score est détectée dynamiquement (`hey_jarvis` ou `hey_jarvis_v0.1`)

**4c. Anti-console (`launcher/core.py`, correctif 1.1.2) :**
- `Jarvis.exe` est compilé en sous-système console (mode headless) ; lancé
  depuis le launcher *windowed*, Windows ouvrait une fenêtre console parasite
  en mode orbe/overlay
- `launch_jarvis` passe désormais `CREATE_NO_WINDOW` au sous-processus pour
  les modes `ui`/`desktop`, et ne conserve la console que pour le mode
  `console` explicite (headless, sortie visible)

**5. Updater (`src/updater.py`) :**
- Valide le ZIP avant extraction (rejette les archives aplaties)
- Valide la structure extraite avant remplacement
- Valide la nouvelle installation après remplacement
- Rollback automatique si validation échoue
- Ne remplace jamais l'installation par une archive invalide

**6. CI (`/.github/workflows/build.yml`) :**
- Test 1 : PyInstaller layout (`Jarvis.exe` + `_internal/python311.dll` + modèles ONNX)
- Test 1b : `dist/app` layout (+ modèles ONNX)
- Test 2 : ZIP layout (conserve `_internal/` sans flattening, + modèles ONNX)
- Test 3 : Executable smoke test (`Jarvis.exe --smoke-test`, dont chaîne wake word réelle)
- Test 4 : Installer layout (installation silencieuse + validation)
- Test 5 : Installed executable smoke test (+ `JarvisLauncher.exe --validate`)
- Test 6 : Update validation (archive invalide rejetée)

Si un test échoue, **aucune release n'est publiée**.

### Scripts de validation

```bash
# Valide la sortie PyInstaller
python scripts/validate_build.py --pyinstaller dist/Jarvis

# Valide dist/app/
python scripts/validate_build.py --app-dir dist/app

# Valide le ZIP portable
python scripts/validate_build.py --zip dist/Jarvis-v1.0.1-portable.zip

# Valide une installation
python scripts/validate_build.py --install-dir %LOCALAPPDATA%/Jarvis

# Exige les modèles ONNX du wake word (bloquant en CI)
python scripts/validate_build.py --app-dir dist/app --require-wakeword

# Crée le ZIP de façon robuste
python scripts/make_portable_zip.py --app-dir dist/app --output dist/Jarvis-v1.0.1-portable.zip
```

### Publication d'une nouvelle version (procédure robuste)

1. Modifiez `src/version.py` (`__version__ = "X.Y.Z"`)
2. Vérifiez cohérence : `python -c "from src.version import __version__; print(__version__)"`
3. Lancez les tests : `python -m unittest discover tests -v`
4. Testez le packaging (Windows) : `.\scripts\build_windows.ps1 -DistDir dist`
5. Validez : `python scripts/validate_build.py --app-dir dist/app && python scripts/validate_build.py --zip dist/Jarvis-vX.Y.Z-portable.zip`
6. Commit : `git add . && git commit -m "vX.Y.Z"`
7. Push : `git push origin main`
8. Tag : `git tag vX.Y.Z && git push origin vX.Y.Z`
   - Le tag DOIT correspondre à `src/version.py`, sinon le workflow échoue
9. Le workflow GitHub Actions :
   - Build + validation complète
   - Si tests packaging échouent → **aucune release**
   - Si tout passe → crée la release avec `JarvisSetup-X.Y.Z.exe` + ZIP + SHA256
10. Les utilisateurs reçoivent la mise à jour via le launcher automatiquement

**En cas d'échec de build :**
- Ne pas déplacer le tag existant
- Corriger le code
- Créer un nouveau commit + tag (ex: vX.Y.Z+1)
- Ou supprimer le tag cassé : `git tag -d vX.Y.Z && git push origin :refs/tags/vX.Y.Z`

