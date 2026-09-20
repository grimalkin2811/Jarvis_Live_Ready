# Changelog

Le projet suit le [versionnage sémantique](https://semver.org/lang/fr/) :
`MAJOR.MINOR.PATCH`. Une **MINOR** ajoute des fonctionnalités compatibles avec
l'existant (comme ici), une **PATCH** corrige un bug, une **MAJOR** casse la
compatibilité.

Les notes détaillées de chaque version sont publiées dans les
[GitHub Releases](https://github.com/grimalkin2811/Jarvis_Live_Ready/releases)
et résumées ci-dessous.

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
