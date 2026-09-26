# Changelog

Le projet suit le [versionnage sémantique](https://semver.org/lang/fr/) :
`MAJOR.MINOR.PATCH`. Une **MINOR** ajoute des fonctionnalités compatibles avec
l'existant (comme ici), une **PATCH** corrige un bug, une **MAJOR** casse la
compatibilité.

Les notes détaillées de chaque version sont publiées dans les
[GitHub Releases](https://github.com/grimalkin2811/Jarvis_Live_Ready/releases)
et résumées ci-dessous.

## [Non publié]

### Corrigé

- **Régression UI de la release v1.5.0 — fonds d'items et menus superposés.**
  Le tag `v1.5.0` a été posé sur `68392d4`, tête de la branche Deezer
  (PR #27), branche coupée depuis `v1.1.2`. Ce commit ne contient donc
  **aucun** des travaux 1.2.0 → 1.4.2 : pas de fonds d'items dans les menus
  radiaux (1.3.1/1.3.2), pas de solveur de layout anti-chevauchement (1.3.2),
  pas de masquage du Blob, pas de Writing Mode. Les binaires publiés
  (`JarvisSetup-1.5.0.exe`, zip portable) embarquent donc une interface
  antérieure à la 1.2.0. `main` (union réalisée par la PR #28) n'a, lui,
  **jamais** perdu ces fonctionnalités : son rendu est identique au pixel
  près à celui de `v1.3.2`, aux deux items Voice ajoutés par le Writing Mode
  près. Aucun code UI n'avait donc à être restauré ; c'est la **publication**
  qui était en cause. → republier depuis `main`.

### Ajouté

- `scripts/check_release_lineage.py` — garde-fou de publication : refuse
  d'étiqueter/publier un commit qui ne contient pas toutes les versions
  déjà livrées, et vérifie la présence du contrat UI (fonds d'items +
  anti-chevauchement). Exécuté par le job `build` sur les tags. Il aurait
  bloqué la v1.5.0 (`v1.2.0` … `v1.4.1` absents de la lignée).
- `tests/test_ui_menu_regression.py` — verrouillage explicite du contrat UI :
  présence des API (échoue sur tout arbre antérieur à la 1.3.2), fond peint
  derrière chaque item des 5 menus (mesure différentielle), absence de
  highlight permanent, survol distinct et localisé, disparition des fonds à
  la fermeture, et zéro chevauchement — dont le menu Voice à 12 items.
  Les 15 tests échouent sur l'arbre publié en v1.5.0.

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

## 1.4.2 — Writing Mode : le curseur par défaut

Release volontairement ciblée : un seul changement fonctionnel, dans le
système d'écriture. Le reste de Jarvis est inchangé.

### Changement

- **Le curseur actif devient la sortie par défaut.** « Écris-moi un message
  pour prévenir mon professeur », « rédige une lettre », « fais-moi une lettre
  de motivation » : Jarvis génère le texte et l'écrit à l'emplacement du
  curseur, sans créer de fichier.
- **Un fichier `.txt` n'est créé que sur demande explicite** : « dans un
  fichier », « un fichier texte », « un .txt », « un document à générer »,
  « un fichier à enregistrer / sauvegarder », « un contenu sous forme de
  fichier », « un fichier téléchargeable ».
- **Cas ambigu** (aucun format précisé) : le curseur est privilégié, aucune
  clarification n'est demandée. Un artefact rédactionnel seul (lettre, mail,
  message, CV, rapport, paragraphe, résumé…) ne déclenche plus de fichier.
- Filet local : si le modèle appelle `create_text_file` alors que la demande
  vise clairement le curseur, l'écriture part au curseur — aucun `.txt` n'est
  créé. Inversement, une sortie explicitement demandée n'est jamais remplacée
  par l'autre.

### Inchangé

Génération du texte, insertion au curseur (`SendInput`, presse-papiers en
secours), création des `.txt` dans `user_content/` (noms sûrs, pas
d'écrasement), interrupteurs Writing → Active field / Text files, refus des
questions et hypothèses, gestion des erreurs.

## 1.4.1 — Stabilisation (voix Desktop, persistance, tray)

Release de correction : pas de nouvelle grosse fonctionnalité. Writing Mode
est inchangé.

### Corrections

- **Voix Desktop** : le mode overlay utilise la voix, le volume, le débit et
  le mode de réponse choisis dans les paramètres (même pont `LIVE` que l'orbe
  et la console), au lieu de retomber sur la voix Gemini par défaut.
- **Persistance** : les réglages vocaux et le mode de réponse sont rechargés
  au démarrage des trois modes. Transparence et always-on-top sont appliqués
  dès l'ouverture de l'orbe.
- **Tray** : Masquer / Afficher / Quitter. L'état masqué n'est pas persisté :
  un relance réaffiche l'interface. Quitter depuis le tray termine le process.
- **Launcher** : refuse une double instance de Jarvis.
- **Cadre Desktop** : apparaît à l'écoute / la réflexion / la réponse, puis
  disparaît après la fenêtre de follow-up (y compris en écoute continue).
- **Menus** : fermeture sans surbrillance résiduelle ; always-on-top ne sort
  plus du plein écran.

## 1.4.0 — Writing System

Deux modes d'écriture, indépendants, uniquement sur ordre explicite. Une réponse
conversationnelle ne déclenche jamais d'écriture, et le texte produit n'est pas
relu à voix haute.

### Ajouts

- **Insertion dans le champ actif** : le texte généré est tapé au curseur de la
  fenêtre au premier plan (navigateur, mail, éditeur, Discord, Word,
  formulaire), sans sélectionner ni remplacer le texte déjà présent. Accents,
  paragraphes et textes longs sont conservés. Sous Windows, la saisie passe par
  `SendInput` ; le presse-papiers n'est utilisé qu'en secours, puis restauré.
- **Fichiers `.txt`** : création dans `user_content/` (dossier créé s'il manque,
  relatif à l'application — jamais un chemin absolu figé). Noms courts et sûrs
  (`trous_noirs.txt`, sinon `document.txt`). Jamais d'écrasement silencieux :
  `document_1.txt`, `document_2.txt`.
- **Réglages indépendants** dans le menu Voix, persistés, activés par défaut :
  Writing → Active field, Writing → Create text files.
- Assainissement des noms de fichiers, collisions, erreurs non fatales et tests.

## 1.3.2 — Layout sans chevauchement, opacité des fonds, démarrage plus rapide

### Ajouts

- **Appearance → « Item BG Opacity »** : nouveau slider (0–100 %) qui règle
  l'opacité des **fonds des items** des menus (pas le blob, ni le texte, ni
  le halo). Appliqué à l'image suivante, cohérent sur les 5 menus (même
  `appearance_state`), persisté avec les autres réglages Appearance dans
  `appearance_state.json`. Fichier d'avant 1.3.2 sans la clé → défaut
  identique au comportement 1.3.1 (`ITEM_BG_REST`).

### Corrections

- **Layout des menus sans chevauchement (systémique)** : le placement
  historique reste la base ; un solveur mesure les libellés avec les
  métriques de police exactes du rendu (pire cas survol) et élargit
  **seulement** l'espacement fautif (colonne ou pas de rang) quand deux
  zones se chevaucheraient. Corrige notamment le menu Voice (9 chevauchements
  mesurés en 1.3.1) **sans redessiner l'UI** — valable pour toutes les
  tailles de contenu et les deux orientations (grille / ligne).
- **Masquage jamais pris pour un démarrage** : `blob_hidden` n'est plus
  sérialisé (`state_to_dict`) et est ignoré au chargement (même ancien
  fichier `blob_hidden: true` → orbe **visible**). Masquer en session
  fonctionne exactement comme avant ; masquer → fermeture → relancement =
  visible, quels que soient les chemins de fermeture (Échap, System → Quit,
  icône de notification, `aboutToQuit`).

### Performance

- **Démarrage** : imports lourds déportés hors du chemin critique
  (`asyncio`, `audio`, `gemini_live`, `memory`, `scheduler` dans la boucle
  vocale ; `screen_halo_overlay` en mode desktop seul ; `UI` exports lazy
  PEP 562 ; construction du menu Routines via `list_routine_names()` sans
  importer `src.tools`). Mesure locale (offscreen) : « Interface démarrée »
  **≈ 0,68 s → ≈ 0,17 s**. Le launcher n'est pas concerné (hors périmètre).
  La voix reste prête au même instant absolu (~0,8 s) — présence « loading »
  pendant ~130 ms de plus, aucun écran de chargement ajouté.

### Qualité

- Tests : `test_menu_layout.py` (zéro chevauchement, 5 menus × 2
  orientations), `test_item_bg_opacity.py` (présentation, application,
  persistance, périmètre), réécriture de `test_blob_visibility.py`
  (contrat visible au démarrage + chemins de fermeture), différentiel des
  fonds patché sur `state.item_bg_opacity`.
- CI : étapes `diag:` pour les deux nouveaux fichiers de tests.

## 1.3.1 — Fonds des items réellement visibles

### Correction

- **Les fonds d'items des 5 menus sont enfin visibles.** En 1.3.0, le fond
  permanent ajouté ne concernait que la pastille du nœud : le rectangle
  derrière le libellé restait réservé au survol. À l'écran, rien ne
  changeait — mesuré sur un rendu réel : opacité du fond **5/255** sans
  survol, contre **224/255** au survol.
  Désormais chaque item affiche son rectangle dès l'ouverture du menu
  (**≈ 121/255**, soit 55 % du fond de survol), dans les 5 menus et
  indépendamment du survol ; le survol ne fait plus que le renforcer.
- Nouvelle constante `ITEM_BG_REST` (0.55) : `0.0` = comportement d'origine
  (fond uniquement au survol), `1.0` = fond identique au survol.

### Qualité

- **Garde-fou des outils** : tout outil implémenté doit être déclaré à
  Gemini (`tests/test_tool_declarations.py`). Un outil absent de
  `TOOL_DECLARATIONS` n'existe pas pour le modèle — il n'est jamais appelé à
  la voix, alors que son code fonctionne et que ses tests directs passent.
- **Test des fonds en différentiel** : le menu est rendu deux fois (avec puis
  sans le fond permanent) et on compte les pixels modifiés. Mesurer une
  valeur absolue ne prouvait rien ici, le texte et la pastille étant déjà
  opaques : c'est ainsi que la 1.3.0 passait ses propres tests sans qu'aucun
  fond ne soit visible. Le test échoue sur la 1.3.0 pour les 5 menus.
- Suite complète : **532 tests + 403 subtests, 0 échec**.

## 1.3.0 — Modes configurables, commandes d'affichage, fonds des menus

Version construite **au-dessus de la 1.2.0** (masquage du Blob, écoute
post-réponse, survol thématisé : tout est conservé).

### Ajouts

- **Listes d'applications des modes jeu/focus, personnalisables sans toucher
  au code** : menu `System` → `Mode Apps` (cases du catalogue + entrées
  « sur mesure ») ou à la voix (« dans le mode jeu, ne ferme pas Opera GX »,
  « ajoute Spotify à la liste du mode focus », « réinitialise les
  applications du mode jeu »).
  - Les deux modes ont des listes **indépendantes** et persistantes
    (`mode.json` v2 : `game_apps` / `focus_apps`) ; aucune exception codée en
    dur (ex. Opera GX n'est plus « protégé » : c'est un choix d'utilisateur,
    absent des valeurs par défaut du mode jeu).
  - Catalogue générique de ~28 apps (`src/mode_apps.py`, identification par
    image de processus) + applications sur mesure.
  - Migration automatique des configs v1 ; clé corrompue → valeurs par
    défaut ; liste vide conservée (ne rien fermer) ; application non ouverte
    → pas d'erreur (fermeture best effort).
- **Commandes d'affichage explicites** : « affiche le blob » / « montre
  l'orbe », « masque le blob », « affiche le menu *X* », « ferme le menu ».
  Une commande s'exécute **dans tous les états** (mode jeu/focus actif, orbe
  configuré caché, menu fermé) : la commande passe devant la politique de
  mode et le réglage « Blob Visible » de la 1.2.0 ; le masquage utilisateur
  (réglage ou commande) survit aux transitions de mode. Affichage/masquage
  centralisés dans un pont unique thread-safe (`UI/visibility_bridge.py`) —
  pas de sources contradictoires ; le menu ouvert est nettoyé en bloc si le
  blob est masqué. « Afficher Jarvis » de la zone de notification suit le
  même chemin.
- **9 nouveaux outils Gemini** : `show_blob`, `hide_blob`, `show_menu(menu)`,
  `hide_menu`, `get_ui_state`, `list_mode_applications`,
  `set_mode_applications`, `toggle_mode_application`,
  `reset_mode_applications` (total : 95 outils).

### Comportement des menus

- À l'ouverture d'un des 5 menus, **tous les fonds d'items sont visibles
  simultanément**, indépendamment du survol (le survol conserve son
  surcroît d'accent et le rectangle thématisé de la 1.2.0) ; à la fermeture,
  au changement de menu, ou au masquage par un mode, les fonds disparaissent
  **en bloc** : aucun résidu, pour n'importe quelle séquence.

### Détails techniques

- `UI/jarvis_menu.py` : politique de visibilité unique dans `tick()`
  (réglage `blob_hidden` + commande « masque le blob » + politique de mode,
  avec surcote d'affichage réinitialisée à la transition) ;
  `_reset_menu_visuals()` appelé sur tous les chemins de masquage.
- `UI/mode_apps_dialog.py` : dialogue des applications des modes (singleton,
  signal `destroyed` branché).
- `src/tools.py` : 9 nouveaux outils (déclarations + enregistrement), tous
  autorisés en mode jeu/focus (`MODE_CONTROL_TOOLS`).
- `src/gemini_live.py` : consignes système — listes de modes
  indépendantes/configurables/persistées (ne jamais préjuger), commandes
  d'affichage toujours exécutées.
- `UI/jarvis_menu.py` : normalisation des fins de ligne (fichier CRLF/LF
  mélangé).

### Tests

- 69 nouveaux tests : catalogue et persistance des modes, isolation
  jeu/focus, migration v1→v2 et corruption, pont de visibilité, rendu
  offscreen par pixels des 5 menus, intégration blob/menu, dialogue Qt.
- Suite complète : **524 tests + 113 subtests, 0 échec**.

## 1.2.0 — Nouveautés d'ergonomie de l'orbe et de l'écoute

### Ajouts

- **Masquer / réafficher l'orbe** (menu `Appearance` → `Blob Visible`).
  Un interrupteur masque l'orbe ou le fait réapparaître. L'état est conservé
  entre les sessions (`appearance_state.json`). Masqué, la fenêtre disparaît
  mais l'assistant (wake word, Gemini Live, routines, rappels, notifications)
  continue de tourner ; l'icône de notification (« Afficher Jarvis ») le
  réaffiche.
- **Écoute post-réponse activable/désactivable** (menu `Voice` →
  `Listen After Reply`, actif par défaut).
  - *Activé* (comportement historique) : après sa réponse, Jarvis reste à
    l'écoute pendant la fenêtre de suivi ; on peut enchaîner sans redire
    « Hey Jarvis ».
  - *Désactivé* : Jarvis retourne en veille dès la fin de sa réponse ; le wake
    word « Hey Jarvis » redevient obligatoire à chaque interaction.
  - `Always Listening` reste prioritaire si les deux sont actifs, et le wake
    word n'est pas altéré.
- **Véritable effet de survol dans les menus radiaux** : survoler un item
  affiche un rectangle sombre aux coins arrondis derrière son libellé, pour
  montrer clairement l'option ciblée. La couleur du fond est dérivée du thème
  courant de l'orbe (elle suit automatiquement un changement de couleur), reste
  sombre pour garder le texte lisible, et n'ajoute aucune marge : rien ne se
  déplace au survol.

### Détails techniques

- `UI/menu_state.py` : nouveau réglage persisté `post_response_listen` +
  pont `LiveControls.get/set_post_response_listen`, synchronisé au chargement.
- `src/audio.py` : `AudioIO` accepte `post_response_provider` ; `extend_listening`
  honore le réglage via un chemin de mise en veille unique (`_go_to_sleep`).
- `UI/appearance_actions.py` : nouveau champ persisté `blob_hidden` +
  `set_blob_hidden` / `toggle_blob_visibility`.
- `UI/jarvis_menu.py` : items `Blob Visible` et `Listen After Reply`,
  masquage de la fenêtre dans `tick()`, et helpers de survol
  `hover_background_color` / `hover_border_color` (dérivés du thème).
- `src/ui.py` / `src/main.py` : branchement du provider sur le moteur audio et
  restauration de l'orbe depuis l'icône de notification.

### Tests

- `tests/test_blob_visibility.py`, `tests/test_menu_hover.py`,
  `tests/test_menu_integration.py` (nouveaux) et extensions de
  `tests/test_audio_controls.py` / `tests/test_menu_state_bridge.py` :
  masquage + persistance, survol sombre et thématisé sans déplacement du texte,
  écoute post-réponse (activée/désactivée, priorité `Always Listening`,
  réversibilité) et intégration clic → pont → backend. La suite complète passe
  (459 tests, 6 sauts liés à l'absence d'`openwakeword` hors ligne).

## 1.1.2

Correctif anti-console (`CREATE_NO_WINDOW` au lancement UI/desktop).

## 1.1.1

Correctif OpenWakeWord du build distribué (modèles ONNX résolus explicitement).

## 1.1.0

Fonctionnalités d'écoute : interruption vocale, écoute continue, protocoles.

## 1.0.x

Fondations : distribution, launcher, mises à jour, mémoire, routines, rappels.

[1.5.0]: https://github.com/grimalkin2811/Jarvis_Live_Ready/compare/v1.4.1...v1.5.0
