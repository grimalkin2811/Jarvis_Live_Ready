# Changelog

Le projet suit le [versionnage sémantique](https://semver.org/lang/fr/) :
`MAJOR.MINOR.PATCH`. Une **MINOR** ajoute des fonctionnalités compatibles avec
l'existant (comme ici), une **PATCH** corrige un bug, une **MAJOR** casse la
compatibilité.

Les notes détaillées de chaque version sont publiées dans les
[GitHub Releases](https://github.com/grimalkin2811/Jarvis_Live_Ready/releases)
et résumées ci-dessous.

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
