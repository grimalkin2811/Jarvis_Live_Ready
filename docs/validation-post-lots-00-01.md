# Validation post-implémentation — lots 00 et 01

Ce rapport distingue les preuves locales, les limitations et les validations à réaliser. Il ne constitue pas une validation du lot 02.

## 1. État Git au début de la validation

- Dépôt : `grimalkin2811/Jarvis_Live_Ready` ; branche active `arena/8edade8b-jarvis-live-ready` ; arbre de travail initial propre.
- HEAD initial `5cca4e423a437bc4d6756a2f1c2089bfaf074044`. `d393043` et `5cca4e4` sont tous deux ancêtres de HEAD (`git merge-base --is-ancestor`).
- `git ls-remote` a confirmé que la branche distante pointait sur le même HEAD initial, `origin/feature/v1.9.0` sur `43ff0029ca1eadfcaca40fd0cbdddb18248e11e8` et `origin/main` sur `ec944e26457b22492b5872ee520d6bb3ce410081`, inchangé depuis le contrôle précédent. Le `main` local est aussi à `ec944e2`. Aucun changement de branche, merge dans main ni force-push.
- Les corrections et ce rapport sont ajoutés sur cette même branche ; vérifier le HEAD livré avec `git log` et `git ls-remote`.

## 2. Core : preuves et limites

| Question | Constat et preuve |
|---|---|
| Instance | `CoreRuntime.run` (`src/core/runtime.py`) protège `_owner` par `_owner_lock` : au plus **un runtime actif dans un processus**, pas une interdiction de plusieurs objets inactifs ni de plusieurs processus Jarvis. `test_runtime_shared_lifecycle_and_cancellation` vérifie le refus concurrent. |
| Console / Orb / Desktop | `src/main.py:run_headless` et `src/ui.py:_start_voice` construisent le même `CoreRuntime`. `InterfaceRouter` crée Orb ou Desktop et passe ses hooks au même thread vocal. Les changements de mode ne recréent pas le backend. `tests/test_core_entrypoint.py` et l'inspection du câblage le vérifient ; fonctionnement des fenêtres non vérifié ici. |
| Dashboard | `DashboardHub` utilise le tableau `runtime_status.RUNTIME`, le contexte global, `MemoryService` et `TasksService`. Les façades relisent les gestionnaires par défaut (`UI/dashboard/hub.py`, `memory_service.py`, `tasks_service.py`), pas le registre Core directement. `tests/test_core_dashboard_shared_services.py` démarre un vrai hub QtCore, une mémoire SQLite, le scheduler, un vrai TaskManager et le runtime avec faux audio/fournisseur : identité des instances mémoire, tâches et conversation, état et texte partagé, puis désabonnements et arrêt des threads. Ce n'est **pas** un test des widgets Qt. Certains services Dashboard (todo, notifications, configuration, monitoring) restent des services distincts ; on ne peut pas affirmer que tout le Dashboard passe par le registre Core. |
| Reconstruction indirecte | Les getters `get_default_*` créent paresseusement certains singletons s'ils n'existent pas ; le Dashboard peut donc créer le contexte ou la mémoire avant le démarrage du runtime. Le runtime réutilise le contexte global mais **remplace** le gestionnaire mémoire global par sa configuration ; `MemoryService` le relit à chaque appel. Le partage dépend donc de ces conventions historiques, pas d'une injection universelle. Pas de second `AudioIO` ou `GeminiLive` construit par ces façades, d'après inspection. |
| Imports | `default_factories` charge audio/Gemini à l'exécution. `tests/test_core_import_order.py` vérifie les deux ordres `core → gemini_live` et `gemini_live → core` en processus neufs ainsi que l'absence d'import audio au seul import Core. Il existe une dépendance de code paresseuse entre ces modules : ce test n'est pas une preuve d'absence de tout cycle d'import dans l'application. |
| Abonnements | `EventBus.publish` isole et journalise les erreurs, `clear` retire les abonnés à l'arrêt ; `ServiceRegistry.close` nettoie en ordre inverse. `DashboardHub.shutdown` détache ses hooks d'état, conversation et tâches. `RuntimeStatusBoard._emit` isolait déjà les exceptions mais les masquait : elles sont maintenant journalisées et le test vérifie que l'abonné suivant reçoit toujours l'événement. |
| Annulation / arrêt | `request_stop` annule la tâche depuis un autre thread ; le runtime ferme la porte micro et annule/attend les envois audio avant fermeture de session. Les watchers Gemini sont annulés et attendus ; le client async est fermé une seule fois à l'arrêt final, pas à chaque reconnexion. Le nettoyage du fournisseur est protégé contre une nouvelle annulation, puis audio, tâches et scheduler sont arrêtés. Les tests `test_core_shutdown_regressions.py`, `test_core_mic_shutdown.py`, `test_core_task_start_stop.py` et `test_core_thread_shutdown.py` couvrent ces cas. |
| Limites de fermeture | Une fonction synchrone déjà lancée par `asyncio.to_thread`, une API ou un callback qui bloque ne peut pas être interrompu de force en sécurité. Le worker de tâches est joint au plus 3 s et le scheduler 2 s ; le runtime signale `FAILED` si un de ces threads est encore vivant, mais ne peut pas le tuer. Le scheduler conserve sa référence et ne démarre pas un second worker pendant cet arrêt. L'UI attend 3 s puis signale que l'arrêt est encore en cours. Il reste possible qu'un thread récalcitrant survive temporairement à la fin du runtime : pas de garantie absolue sur toutes les ressources externes. |

La correction porte sur des défauts du cycle de vie : arrêt au milieu de l'initialisation et exception pendant l'enregistrement texte laissaient le fournisseur ouvert ; son client GenAI n'était pas fermé à l'arrêt final ; certains envois audio pouvaient survivre à la fermeture ; le TaskManager pouvait rester en `run_forever` après une fermeture pendant son initialisation ; un scheduler dont la jointure expirait perdait sa référence de thread. Les tests de régression reproduisent ces situations sans réseau ni matériel.

## 3. Reconstitution des 23 échecs antérieurs

L'exécution antérieure a exclu 21 modules non collectables et imposé un timeout artificiel de 15 s : **1 425 réussis, 561 sous-tests réussis, 114 ignorés, 23 échecs**. Chaque ligne ci-dessous provient de son rapport d'échec ; cela ne représente ni la suite intégrale ni une référence verte avant modification.

Légende d'erreur exacte :

- **Q** : `ImportError: libGL.so.1: cannot open shared object file: No such file or directory` à l'import de `PySide6.QtWidgets` ou `PySide6.QtGui`. Classification : dépendance système Linux manquante ; le comportement testé n'a pas été atteint. Correction : installer les bibliothèques système Qt dans un environnement de validation ou exécuter sur Windows ; pas de modification de code pour masquer l'erreur.
- **T15** : `Failed: Timeout (>15.0s) from pytest-timeout.` Classification : timeout de test artificiel ; le scénario comporte des attentes réelles. Rejeu ultérieur : les six premiers ont réussi avec délai 120 s ; le septième, `TestStability`, a atteint le timeout 120 s (il comporte déjà 30 + 30 + 60 secondes d'attente), puis a réussi isolément avec 180 s, en 139,83 s. Cela n'établit pas qu'ils ne peuvent jamais être instables.
- **A** : `AssertionError: 'get_default_conversation_context' not found in ... : src/main.py`. Classification : test statique couplé à l'ancien lieu d'assemblage, devenu obsolète après migration ; il a été adapté pour rechercher la délégation au Core et l'initialisation du contexte dans le Core, puis réussi. Les nouveaux tests de partage exercent aussi les identités de services réellement.

| Test intégral | Erreur / cause / correction ou résultat de rejeu |
|---|---|
| `tests/test_conversation_pipeline.py::ModeAndRestartTests::test_les_points_d_entree_ouvrent_une_conversation_neuve` | A |
| `tests/test_dashboard_config.py::test_presets_only_reference_registered_widgets` | Q |
| `tests/test_dashboard_notifications.py::test_hub_routes_real_task_hooks_and_bridge_filter_avoids_legacy_duplicate` | Q |
| `tests/test_dashboard_notifications.py::test_notification_bridge_filter_suppresses_display_only` | Q |
| `tests/test_dashboard_security.py::test_every_widget_action_has_a_policy` | Q |
| `tests/test_dashboard_ui_feedback.py::AudioFailureNotificationTests::test_non_audio_engine_failure_is_still_notified` | Q |
| `tests/test_dashboard_ui_feedback.py::AudioFailureNotificationTests::test_one_audio_failure_raises_one_notification` | Q |
| `tests/test_desktop_config.py::AppearanceStateIntegrationTests::test_desktop_block_is_persisted_and_restored` | Q |
| `tests/test_desktop_config.py::AppearanceStateIntegrationTests::test_no_second_config_file_is_created` | Q |
| `tests/test_desktop_config.py::AppearanceStateIntegrationTests::test_saving_a_v160_state_adds_the_block` | Q |
| `tests/test_desktop_config.py::AppearanceStateIntegrationTests::test_survives_multiple_save_load_cycles` | Q |
| `tests/test_desktop_config.py::AppearanceStateIntegrationTests::test_unknown_future_keys_do_not_break_loading` | Q |
| `tests/test_desktop_config.py::AppearanceStateIntegrationTests::test_v160_file_still_loads_with_defaults` | Q |
| `tests/test_echo_loop.py::TestA_Silence::test_silence_produces_zero_listening_restarts` | T15 ; rejeu 120 s réussi |
| `tests/test_echo_loop.py::TestD_Interrupt::test_barge_in_cuts_response_and_no_stale_timer_survives` | T15 ; rejeu 120 s réussi |
| `tests/test_echo_loop.py::TestE_FollowUpListening::test_window_expires_to_hidden_and_stays_hidden` | T15 ; rejeu 120 s réussi |
| `tests/test_echo_loop.py::TestG_SessionChurn::test_reconnect_during_playback_does_not_create_ghosts` | T15 ; rejeu 120 s réussi |
| `tests/test_echo_loop.py::TestListenModeRenewal::test_renewal_is_silent_and_creates_no_turns` | T15 ; rejeu 120 s réussi |
| `tests/test_echo_loop.py::TestStability::test_long_run_counters_are_exact` | T15 ; timeout 120 s au premier rejeu, réussi à 180 s |
| `tests/test_manual_stop.py::UiWiringTests::test_stop_handler_empties_output_then_marks_a_manual_stop` | Q |
| `tests/test_pyside_refcount.py::test_void_methods_do_not_leak_none_references` | Q |
| `tests/test_session_lifecycle.py::TestNoSpuriousReconnectDuringActiveWindow::test_silence_after_reply_triggers_no_reconnect_and_no_extra_turn` | T15 ; rejeu 120 s réussi |
| `tests/test_validation_protocole.py::test_lag_probe_charges_each_gap_to_the_phase_where_it_is_observed` | Q |

La première tentative de référence **avant** les lots s'était arrêtée à la collecte : 21 erreurs `libGL.so.1`, aucun test exécuté. Modules concernés : `test_blob_visibility`, `test_dashboard_conversation_widget`, `test_dashboard_entry_points`, `test_dashboard_perf`, `test_dashboard_productivity`, `test_dashboard_tasks`, `test_dashboard_widgets`, `test_dashboard_window`, `test_interruption_ui`, `test_item_bg_opacity`, `test_menu_backgrounds`, `test_menu_hover`, `test_menu_integration`, `test_menu_layout`, `test_menu_render_offscreen`, `test_menu_ux`, `test_mode_apps_dialog`, `test_orb_visibility`, `test_protocol_ui`, `test_routines_ui`, `test_ui_menu_regression`. Il n'existe donc pas de bilan complet avant/après comparable.

## 4. Résultats après corrections

- Tests ciblés, après corrections de cycle de vie : **132 réussis** (`tests/test_core*`, tâches, scheduler, état, Gemini simulé, aller-retour outils). Un second ensemble fonctionnel hors matériel a donné **490 réussis et 486 sous-tests réussis** : Core, mémoire, sauvegarde, contexte, tâches, rappels, outils, Gemini simulé, wakeword simulé, routines et machine d'état Desktop. Ruff, `pip check` et `git diff --check` réussis.
- Test `test_core_live_pipeline.py` : Core réel + `GeminiLive` réel + serveur Live factice + audio factice ; un tour vocal, puis une question texte via le tableau Dashboard utilisent la même session et le même contexte. Il ne teste ni l'API externe ni une carte son.
- Test `test_core_dashboard_shared_services.py` : hub Dashboard QtCore en sous-processus et services métier réels isolés ; ne teste pas les widgets Qt.
- Suite Linux complète demandée avec `QT_QPA_PLATFORM=offscreen python -m pytest -q -p no:cacheprovider --continue-on-collection-errors --timeout=240 --tb=short --disable-warnings` : **1 455 réussis, 561 sous-tests réussis, 114 ignorés, 15 échecs et 21 erreurs de collecte**, code 1, 487,71 s. Les 36 traces ont été examinées individuellement : toutes s'arrêtent avec `ImportError: libGL.so.1: cannot open shared object file`, avant d'atteindre le comportement Qt ; les 7 tests temporels précédemment interrompus et l'assertion migrée ne figurent plus parmi les échecs. Les 21 modules en erreur n'ont pas été exécutés ; la suite n'est donc pas verte. Aucun timeout de 15 s n'est imposé dans ce passage ; `pytest-timeout` est un outil supplémentaire local, non ajouté au verrou Windows.
- Référence précédente : `pip check` réussi ; smoke test développement code 0, mais PortAudio et modèles wakeword absents, non bloquants dans ce mode. Ils ne sont **pas** validés.

## 5. Windows : vérifications et procédure humaine

- Nouvelle tentative `gh workflow run build.yml --ref arena/8edade8b-jarvis-live-ready` : **HTTP 403 `Resource not accessible by integration`**. Aucune CI Windows n'a été déclenchée par cette tentative. `build.yml` déclenche ses tests Windows sur PR vers main ou lancement manuel, pas sur le simple push de cette branche ; le job de build en dispatch ne crée une release que sur un tag `v*`.
- YAML des deux workflows analysé ; `build.yml` et `pr-windows-native.yml` installent `requirements-lock/windows-py311.txt` avec `--require-hashes`, puis `pip check`; `build_windows.ps1` vérifie Windows x64/Python 3.11 et utilise le même verrou. Aucun workflow n'a été modifié pendant cette validation.
- 63 distributions épinglées, toutes avec empreintes SHA-256. Régénération uv pour Windows/Python 3.11 identique au verrou existant ; téléchargement croisé `pip download --no-deps --only-binary=:all: --platform win_amd64 --implementation cp --abi cp311 --python-version 3.11 --require-hashes ...` : **63 roues téléchargées et empreintes vérifiées**. `--no-deps` ne vérifie pas la résolution des dépendances sous Windows, ni les imports ou l'exécution. L'essai pip croisé avec dépendances sur Linux reste non concluant à cause des marqueurs évalués sur l'hôte.

Procédure locale Windows PowerShell, depuis ce checkout et **sans changer de branche** :

```powershell
# Contrôler d'abord la branche, le HEAD et un arbre de travail propre.
git status -sb
git rev-parse HEAD
git ls-remote origin refs/heads/arena/8edade8b-jarvis-live-ready
# Créer un environnement jetable dans TEMP, pas dans les données Jarvis.
$root = Join-Path $env:TEMP ("jarvis-validation-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $root | Out-Null
py -3.11 -m venv (Join-Path $root "venv")
$p = Join-Path $root "venv\Scripts\python.exe"
& $p -c "import struct,sys; assert sys.platform == 'win32' and sys.version_info[:2] == (3,11) and struct.calcsize('P') == 8"
& $p -m pip install --upgrade pip
& $p -m pip install --require-hashes -r requirements-lock/windows-py311.txt
& $p -m pip check
$env:QT_QPA_PLATFORM = 'offscreen'
$env:PYTHONUTF8 = '1'
$junit = Join-Path $root 'pytest-results.xml'
& $p -m pytest -q "--junitxml=$junit"
```

Vérifier le code de sortie de **chaque commande**, conserver le rapport JUnit et ne pas interpréter les skips comme des succès. Pour le build natif, après installation des prérequis externes décrits dans le dépôt et avec une connexion autorisée pour les modèles, exécuter `scripts/build_windows.ps1` avec `-PythonExe $p` et un `-DistDir` neuf sous `$root` ; inspecter ses erreurs et le smoke test du binaire. Le build télécharge des modèles et n'est pas bit-à-bit verrouillé. Tests Qt natifs, micro/haut-parleurs, wakeword, gestes Orb/Desktop, Dashboard, outils Windows non destructifs et session Gemini demandent une intervention humaine et un environnement isolé. Ne pas exécuter d'action destructive pour valider ; ne pas communiquer de clé ou de donnée privée. Les appels externes et leur coût nécessitent une autorisation explicite.

## 6. Fonctionnalités et interfaces

- Texte + voix + Gemini : route commune testée avec serveur et audio factices ; reconnexion, reprise, contexte et round-trip outils testés hors ligne. Service Gemini réel, latence et matériel **non testés**.
- Mémoire : manager partagé et tests SQLite isolés ; les souvenirs sont persistants. Le contexte de conversation est **volontairement éphémère**, remis à zéro au démarrage ; il est conservé/rejoué pour les reconnexions en cours de processus, mais il n'y a pas de persistance disque de l'historique conversationnel à promettre. Tests de contexte et mémoire locaux ne prouvent pas une migration des données réelles Windows.
- Tâches / rappels : tests manager et scheduler locaux, persistance et annulation simulées ; un service externe bloquant ne peut être arrêté de force. Outils Windows : tests avec mocks et garde-fous, pas d'exécution native ni de commandes destructives.
- Wakeword : tests de logique et fixtures ; modèles distribués et micro réels non testés.
- Orb et Desktop : câblage commun inspecté ; widgets et rendu non validés, QtWidgets ne s'importe pas ici.
- Dashboard : identité des services métier et désabonnement du hub vérifiés via QtCore ; affichage, widgets et événements utilisateurs réels non validés ici.

## 7. Garde-fou existant — pas de lot 02

`GeminiLive` applique `reject_unverified_action` dans le dispatch d'appels d'outils du modèle : console vocale, Orb, Desktop et texte Dashboard passent par cette même session. Toute valeur `confirm` évaluée vraie, même une chaîne non vide telle que `"false"`, est refusée ; `delete_memory` est toujours refusé par nom. Le modèle ne peut donc pas débloquer ces appels simplement avec `confirm=true` ; **aucun parcours indépendant ne permet aujourd'hui de les approuver vocalement**.

Les 21 déclarations d'outils contenant `confirm`, donc potentiellement bloquées en appel Live quand il vaut vrai, sont : `sleep_pc`, `hibernate_pc`, `turn_off_screen`, `empty_recycle_bin`, `wake_on_lan`, `capture_clipboard`, `paste_from_history`, `clear_clipboard_history`, `read_file_aloud`, `backup_data`, `restore_backup`, `delete_todo`, `clear_todos`, `clear_activity_log`, `shutdown_pc`, `restart_pc`, `delete_notes`, `forget`, `clear_memory`, `delete_routine`, `cancel_reminder`. Une opération légitimement demandée reste bloquée si elle nécessite ce booléen. Un `confirm=false` ne prouve rien mais n'est pas bloqué par cette frontière : les validations propres à l'outil et la politique de mode restent applicables.

Exemples précis d'actions encore accessibles en Live sans autorisation indépendante : `remember`/`update_memory`, `set_clipboard`/`get_clipboard`, création/lancement de routines, démarrage/annulation de tâches, et `cancel_reminder` pour un identifiant précis sans `confirm=true` ; selon l'action, des garde-fous de mode, whitelist, intention d'écriture ou validations de chemin subsistent. Ce sont des exemples, **pas un inventaire exhaustif ni une évaluation de sûreté**. Les routines et protocoles appellent `TOOL_FUNCTIONS` et sa politique de mode, avec `FORBIDDEN_TOOLS`, mais ne passent pas par `reject_unverified_action`. Le fallback local de transcription `open_dashboard`/`close_dashboard` appelle lui aussi directement `TOOL_FUNCTIONS`. Les tâches d'arrière-plan utilisent séparément un modèle et éventuellement Google Search ; leur exécuteur inspecté ne dispatch pas les 150 outils Live. Les actions Dashboard directes ont leur propre politique UI (niveaux 1/2/3), et Orb/menus/ordonnanceur ou appels internes directs ne traversent pas la frontière Live. Ni cette politique ni la frontière ne prouvent une autorisation globale.

À traiter au lot 02 : inventaire complet des effets et chemins indirects, provenance non falsifiable, autorisation sur arguments et durée, consentement de confiance, anti-rejeu, révocation, audit et tests adversariaux. Aucun moteur de ce type n'a été développé ici.

## 8. Restes et risques

Validation Windows native, installation propre réelle, GUI complète, matériel, wakeword, Gemini et coûts réels, arrêt de ressources externes bloquées, performance Pentium G5400 et sécurité globale restent non prouvés. Les singletons restent un couplage d'architecture. Le build vérifie les dépendances verrouillées mais reste tributaire des outils/modèles externes. Ne pas fusionner ou publier en supposant ces points acquis.

## 9. Verdict

**Lots 00 et 01 partiellement validés** : corrections et tests locaux probants, mais validation Windows/GUI/matériel/Live absente et sécurité du lot 02 non livrée. Un test non exécuté ou interrompu n'est pas un test réussi.
