# Audit pré-fusion — PR #50 (Dashboard)

Audit ciblé réalisé le 2026-10-15 sur la branche
`arena/83d732a5-jarvis-live-ready` (tête auditée : `d3f1fbe`, base `main` @
`ec944e2`, inchangée). Priorité : stabilité du vocal et de la session Gemini
Live existante. Aucune fusion, aucun tag, aucune release.

Les tests « faux serveur » (`tests/live_harness.py`, `tests/voice_harness.py`)
vérifient la logique du client ; ils ne valident **pas** le vrai service
Gemini Live, qui reste **NON TESTABLE** ici (aucune clé API disponible).

## 1. État vérifié au départ

* PR #50 : brouillon, ouverte, fusionnable (`CLEAN`), 10 commits, tête
  `d3f1fbe` identique local/distant, arbre propre.
* CI sur `d3f1fbe` : `Tests (Windows)` réussi, `Lint` réussi, `Build & Release`
  ignoré (tag uniquement), `Validate Gemini Live` « réussi » mais verdict
  publié **NON TESTABLE** (étape `continue-on-error`, aucune clé).
* Suite complète rejouée indépendamment sur `d3f1fbe` : **1 495 réussis**,
  700 sous-tests, 20 avertissements préexistants, 0 échec (467,6 s) —
  conforme au rapport précédent.

## 2. Problèmes trouvés

Aucun défaut **bloquant** au sens « casse le vocal ou corrompt des données »
n'a été trouvé. Huit défauts ont été **démontrés** sur `d3f1fbe` — par un test
qui y échoue puis réussit après correction (P2 à P8), ou par le journal du
plugin d'audit des accès (P1) — puis corrigés.

| # | Gravité | Problème | Preuve | Impact | Correction | Test |
|---|---|---|---|---|---|---|
| P1 | **Important** | La suite pytest touche les données **réelles** : un test ajouté par la PR (`test_orb_ctrl_d_opens_dashboard_through_the_bridge`) et 5 fichiers préexistants écrivent `ui/appearance_state.json`, `menu_state.json`, `system_state.json` ; 6 fichiers préexistants ouvrent la vraie `memory.db` (désormais **migrée en v2**) et `schedule.db` ; la collecte écrit `routines.json` (11 fichiers de tests au total). | Plugin d'audit sur la suite complète avec `HOME` vierge : 124 accès distincts journalisés sous `~/.jarvis`, base créée en `user_version = 2`. | Lancer `pytest` sur le PC de l'utilisateur (ce que la documentation recommande) modifiait ses réglages d'interface et le schéma de sa vraie mémoire (migration additive, sans perte, mais non consentie). | `tests/conftest.py` : toute la session pointe vers un dossier temporaire avant tout import de `src` ; les surcharges réelles (`JARVIS_*_DATABASE_PATH`, `JARVIS_USER_CONTENT_DIR`) sont ignorées ; filet `autouse` si un test retire `JARVIS_DATA_DIR`. | `test_suite_isolation.py` ; suite rejouée : **0 accès** sous `~/.jarvis`. |
| P2 | **Important** | Le widget Conversation recevait la confirmation d'envoi par `future.add_done_callback(self._send_result.emit)` : émission depuis le thread du moteur vocal sur un widget qui peut être détruit. | Fenêtre réelle fermée en mode interactif (fenêtre libérée) pendant un envoi : `RuntimeError: Signal source has been deleted` journalisé en ERROR depuis le thread de la boucle vocale. | Trace d'erreur dans le processus vocal ; une destruction **simultanée** de l'émission serait une course inter-threads (comportement indéfini, potentiellement fatal pour tout Jarvis, voix comprise). | `JobRunner.watch_future` : le relais durable du hub émet (jamais le widget), propriétaire vérifié dans le thread Qt. | `test_confirmation_after_widget_destruction_is_dropped_without_error`, `test_closing_the_window_mid_send_keeps_the_turn_and_shows_it_on_reopen`. |
| P3 | Important | `window.py` connectait une **lambda** à `hub.engine_changed` ; le hub survit à la fenêtre (mode interactif, fenêtre libérée) : la connexion n'était jamais retirée. | 2 cycles fermeture/réouverture puis 2 changements d'état moteur : 8 exceptions `Internal C++ object (StatusPill/StatusDot) already deleted`. | Une exception par fenêtre morte à chaque changement d'état du moteur (reconnexion…), fuite des objets Python des fenêtres libérées. | Méthode liée `_on_engine_changed` (déconnectée automatiquement par PySide6). | `test_released_window_no_longer_reacts_to_engine_changes`. |
| P4 | Mineur (rare) | Filet de 30 s (`_text_turn_watchdog`) : époque du tour capturée **après** `await send_client_content`. Si la réponse est traitée pendant l'envoi, le filet porte l'époque du tour suivant. | Faux serveur avec écriture réseau qui cède la main : le filet du 1er message rouvre le micro du 2e message (toujours en attente) avant son propre délai. | Micro rouvert trop tôt pendant un texte en attente : audio temps réel mêlé à un `clientContent` sans réponse (la situation S6 que la porte micro doit justement empêcher). | Époque capturée avant l'envoi. | `test_watchdog_of_an_answered_text_never_releases_the_next_text_early`. |
| P5 | Mineur (latent) | Annulation du `Future` pendant l'envoi : `CancelledError` (non `Exception`) n'était pas traitée — porte micro du texte fermée, filet non armé. | Test d'annulation : `_text_turn_pending` reste vrai, `can_send()` faux. | Aucun appelant n'annule aujourd'hui ; si cela arrivait, micro retenu jusqu'au prochain tour clos ou à une reconnexion. | Tour clos en échec avant de propager l'annulation. | `test_cancelled_submission_never_leaves_the_mic_closed`. |
| P6 | Mineur | Restaurer une version **identique** à la version actuelle répondait `success / restored` et réécrivait l'horodatage. | A → B → A puis restauration de A. | Le Dashboard annonçait « Version restaurée » sans aucune modification réelle. | Refus explicite « Cette version est identique à la version actuelle. », rien n'est écrit ; message relayé par le widget. | `test_restoring_a_version_identical_to_the_current_one_writes_nothing`. |
| P7 | Mineur | `open_dashboard` annonçait `page = <demandée>` même pour une page inexistante ; Dashboard déjà visible, la confirmation pouvait précéder le traitement réel de la demande. | `open_dashboard("page-qui-n-existe-pas")` avec le vrai contrôleur : « Dashboard ouvert », `page = "page-qui-n-existe-pas"`, fenêtre restée sur la page précédente. | Jarvis pouvait affirmer avoir ouvert une page qui n'existe pas. | Identifiant de demande (`request_open_tracked`), rapport atomique de l'interface (`handled_request`, `page`, `page_found`) ; rétrocompatible (consommateur sans suivi = visibilité seule). | `test_voice_open_dashboard_reports_the_page_really_shown`. |
| P8 | Mineur (test) | `test_send_success_uses_shared_context_and_clears_input` échouait lancé seul : l'import paresseux de `google.genai` par le moteur **factice** (≈0,7 s) était compté dans le budget « send() < 0,2 s ». | Échec reproduit seul sur `d3f1fbe` ; réussite dans la suite complète (module déjà importé). | Faux négatif pour qui lance ce fichier seul ; aucun impact produit (aucun import dans le thread graphique en production). | Module préchargé dans le fichier de test. | Fichier lancé seul 3× : 9/9. |

## 3. Chemin vocal et Gemini Live — ce qui a été vérifié

Faux serveur, vrai `GeminiLive`, boucle de reconnexion identique à `ui.py`
(`tests/test_gemini_text_voice_interplay.py`, 12 scénarios) :

* **Session et contexte partagés** : voix → texte → voix dans la même session
  (aucune reconnexion), un seul contexte ; la réponse écrite exploite ce qui a
  été dit à la voix ; aucun doublon du message écrit.
* **Concurrence** : texte refusé (`busy`, texte non versé au contexte) pendant
  la transcription d'un tour vocal puis pendant la réponse ; deux envois
  simultanés depuis deux threads → un accepté, un « occupé ».
* **Micro** : aucun bloc micro ne part pendant qu'un texte attend sa réponse ;
  une interruption réelle reste prioritaire ; micro rendu à la fin du tour.
* **Watchdog 30 s** : rend le micro si aucune réponse ; ne rouvre jamais le
  micro d'un texte suivant (P4 corrigé).
* **Erreurs / annulations / reconnexions** : relance de génération vide (micro
  retenu, pas de doublon), interruption serveur, appel d'outil (micro fermé
  pendant l'outil, second texte refusé), coupure réseau (message conservé et
  marqué « sans réponse », micro rendu, la conversation continue après
  reconnexion), nouvelle conversation pendant un texte en attente, annulation
  (P5 corrigé).
* **Fermeture du Dashboard pendant une génération** : aucune erreur, le tour
  continue dans le moteur, réponse affichée à la réouverture (P2, P3 corrigés).
* **Thread graphique** : `send()` rend la main immédiatement ; la confirmation
  est livrée par signal en file d'attente.
* **Non-régression vocale** : un tour purement vocal ne traverse jamais le
  chemin texte (aucune raison « message texte en cours », aucun évènement
  `TEXT_*`, aucun `clientContent`) ; l'ensemble des suites vocales historiques
  passe.
* **Écoute après réponse** : un message écrit ne réveille pas le micro si
  Jarvis était en veille (`AudioIO.extend_listening` sort si `awake` est faux).

Consigne système `open_dashboard` / `close_dashboard` : outils exécutés hors
boucle de réception (`asyncio.to_thread`) ; `close_dashboard` masque toujours
la fenêtre et ne passe jamais par le mode de fermeture « arrêt » ; ouvrir ne
change pas de mode et n'interrompt ni tâches ni voix ; aucun déclenchement
local par mot-clé (seul le modèle décide). L'intention réelle du modèle
(demande explicite vs simple mention d'un « tableau de bord ») est **NON
TESTABLE** sans clé.

`audio.start()` : l'exception d'origine est re-levée telle quelle (la
publication dans `RUNTIME` ne lève pas : crochets isolés) ; l'état `FAILED`
n'est pas écrasé par `STOPPED` à l'arrêt ; aucun double traitement de
l'exception. Observation : deux notifications distinctes (« Problème audio »,
catégorie audio ; « Backend vocal indisponible », catégorie intervention)
pour une même cause — non corrigé (choix d'ergonomie, aucun impact de
stabilité).

## 4. Compatibilité PySide6

Voir `PROTOCOLE_DE_TEST.md` §7. Résumé :

* **Cause** : reproduite dans un venv isolé (Linux, CPython 3.11.2, 6.12.0) :
  une référence à `None` perdue par appel de méthode `void` ; dérive −2000 au
  garde-fou puis `none_dealloc` (code 134) ; processus tué après ~5 100 appels
  `save()/restore()`. 6.11.2 : aucune dérive.
* **Application de la borne** : CI Windows (`pip install -r requirements.txt`),
  `build_windows.ps1` (exécutable et launcher), `setup.bat`,
  `verify-release.yml` ; le workflow de validation Gemini n'installe pas
  PySide6 ; `pyproject.toml` ne déclare aucune dépendance.
* **Environnements** : Python 3.11 (CI, build, `python311.dll`). Sans objet
  sur ≥ 3.12 (`None` immortel) ; les 6.11.x exigent Python < 3.15 (aucun
  effet aujourd'hui : onnxruntime n'a pas de roue 3.15) — note ajoutée dans
  `requirements.txt`.
* **Packaging** : un exécutable construit depuis cette branche embarque
  6.11.2. La v1.8.2 publiée (2026-10-11, dépendance non bornée) a très
  probablement embarqué 6.12.0 ; l'effet sous Windows n'est pas prouvé
  (reproductions uniquement Linux ; CI Windows de `main` verte, non
  concluant). Vérification : version de `_internal\PySide6\Qt6Core.dll`.
* **Garde-fou** : `test_pyside_refcount.py` vérifie le **défaut** (pas la
  borne elle-même) ; il échoue sur 6.12.0 (prouvé sous Linux), réussit sur
  6.11.2, est ignoré sur ≥ 3.12. Sous Linux, une suite complète en 6.12.0
  s'arrêtait dans les tests de l'orbe avant de l'atteindre (code 134). Sous
  Windows, rien n'est prouvé : la vérification décisive consiste à lancer ce
  garde-fou dans un venv jetable avec `PySide6==6.12.0` sur le PC Windows.

## 5. Mémoire et sécurité

* Migration v1 → v2 : additive, une transaction (`BEGIN … COMMIT`), annulée par
  `with conn` en cas d'échec ; la base reste v1 avec ses données ; retentée
  automatiquement à l'accès suivant (`_ready()` → `_initialize()`), sans
  redémarrage — comportement correct de la PR, désormais prouvé par
  `test_failed_v2_migration_is_rolled_back_then_retried_without_data_loss`.
* Compatibilité descendante (vérification ponctuelle avec le `memory.py` de
  `main`) : Jarvis v1.8.2 lit, modifie, ajoute et supprime sur une base v2 ; les
  déclencheurs continuent d'historiser ; v1.9 relit cet historique ;
  intégrité « ok ».
* Purge : exactement les 20 versions antérieures les plus récentes (prouvé).
* Confirmations : création niveau 1, modification et restauration niveau 2
  non abaissable, suppression niveau 3 (politique testée).
* Secrets : journal d'activité et rapport de diagnostic passent par
  `redact_secrets` (test dédié) ; seule la présence d'une clé est indiquée.
* Données réelles : plus aucun accès pendant la suite (P1) ; aucune opération
  destructive n'a été exécutée sur des données réelles pendant l'audit.

## 6. Tests exécutés pendant l'audit

Environnement : Linux x86-64 (2 vCPU), CPython 3.11.2, PySide6 6.11.2,
`QT_QPA_PLATFORM=offscreen`, bibliothèques système simulées (aucune
influence sous Windows).

| Exécution | Résultat |
|---|---|
| Suite complète rejouée sur `d3f1fbe` (avant corrections) | **1 495 réussis**, 700 sous-tests, 20 avertissements, 0 échec — 467,6 s |
| Même suite + plugin d'audit des accès (`HOME` vierge) | 124 accès distincts sous `~/.jarvis`, 11 fichiers de tests (P1) |
| Tests d'audit sur le code de `d3f1fbe` | échecs attendus : P2 (×2), P3, P4, P5, P6, P7, P8 (fichier lancé seul) ; les autres scénarios réussissent |
| Fichier `test_gemini_text_voice_interplay.py` après corrections | 12/12, stable sur 5 exécutions (avec `test_gemini_text_input.py` : 23 réussis + 3 sous-tests) |
| **Suite complète finale** (après corrections, 23 tests ajoutés), `HOME` vierge | **1 518 réussis**, 700 sous-tests, 20 avertissements préexistants, 0 échec, 0 ignoré — 466,2 s ; aucun `~/.jarvis` créé |
| Protocole dédié `scripts/run_dashboard_protocol.py` | **SUCCÈS** : unitaires 95, intégration 88 (+3 sous-tests), performance 3 + mesures (seuils tous respectés), 24 captures, ruff et compileall sans erreur |
| Statique | `ruff check src launcher run_jarvis.py version.py`, `ruff check UI tests`, `ruff check scripts` : aucune erreur ; `compileall` : code 0 |
| `run_jarvis.py --smoke-test` | code 0 (avertissement préexistant : modèles du mot d'éveil non téléchargés en développement) |
| PySide6 6.12.0 (venv isolé) | garde-fou en échec (−2000) puis `none_dealloc` (code 134) — défaut reproduit |
| Compatibilité descendante mémoire (`memory.py` de `main` sur base v2) | lecture, modification, ajout, suppression, historique, intégrité : OK |

Mesures de performance (protocole, Linux hors écran, moteur simulé) :
première ouverture 170,5 ms (≤ 1 500), changement de page médian 23,0 ms /
max 82,1 ms (≤ 150 / 400), latence max de la boucle 96,2 ms (≤ 150), CPU
léger 1,11 % / normal 1,61 % / poussé 12,62 % (≤ 2 / 10 / 20), masqué 0,98 %
(≤ 2), 0 widget actif et 0 minuteur Dashboard masqué.

CI GitHub : voir les commentaires de la PR #50 pour le commit final (les
journaux bruts des runners ne sont pas lisibles depuis l'environnement de
développement ; seuls les statuts des tâches et étapes le sont).

## 7. Validations impossibles ici

* **Gemini Live réel** : NON TESTABLE (aucune clé ; le workflow publie
  « NON TESTABLE ») — conversation écrite réelle, voix après texte, changement
  d'interface en session réelle, intention du modèle pour
  `open_dashboard`/`close_dashboard`, mention ordinaire d'un tableau de bord.
* **Audio réel, Windows manuel** : liste `PROTOCOLE_DE_TEST.md` §6 (items 1 à
  16) à dérouler sur le PC de l'utilisateur.
* **PySide6 6.12.0 sous Windows** et contenu exact de la v1.8.2 publiée.

## 8. Risques résiduels

* Comportement du vrai modèle face au texte (latence, réponses, éventuels
  tours vides non couverts par le faux serveur) et intention des outils
  Dashboard : non validés.
* Course théorique entre une voix captée juste avant l'envoi d'un texte (sans
  transcription encore reçue) et ce texte : le texte part ; la transcription
  tardive rejoindrait le même tour. Inhérent au protocole, non observé.
* Visuel Desktop : un message écrit déclenche l'évènement « transcription »
  (état « à l'écoute » transitoire de l'overlay) alors que le micro n'écoute
  pas — à confirmer sur Windows (item 4/14).
* `python -m unittest discover tests` ne charge pas `conftest.py` : seule la
  commande `pytest` (CI, README, protocole) bénéficie de l'isolation P1.

## 9. Recommandation

**Encore bloquée pour la fusion** — non plus par un défaut de code connu (les
huit défauts démontrés sont corrigés et couverts, suites et protocole au
vert), mais par des validations externes impossibles ici :

1. La PR modifie le moteur vocal (entrée texte, porte micro) et, pour TOUTES
   les sessions vocales, la consigne système et la liste des outils
   (`open_dashboard`, `close_dashboard`). Ces chemins ne sont couverts que par
   le faux serveur : une validation réelle avec clé (workflow
   `validate-gemini-live.yml` alimenté, puis conversation écrite + voix +
   changement d'interface) est nécessaire avant de l'exposer aux
   utilisateurs.
2. La liste Windows (§6 de `PROTOCOLE_DE_TEST.md`, au minimum les items 1, 2,
   4, 7, 13 et 14) doit être déroulée sur un PC réel (micro, haut-parleurs).
3. Vérifier PySide6 sous Windows (garde-fou en 6.12.0 dans un venv jetable,
   et version embarquée par la v1.8.2) : sans effet sur cette branche (bornée
   en 6.11.2), mais déterminant pour les utilisateurs de la v1.8.2.

Ces trois points levés, la PR pourra être proposée à la fusion (choix du
numéro de version au moment de la release ; la décision de fusionner reste
celle du propriétaire du dépôt).
