# Validation finale du lot 01.5

Date : 1 novembre 2026. Périmètre : Runtime, injection d'outils, interfaces et stabilisation du Context Engine ; pas de lot 02. **LOT 01.5 CLÔTURABLE : NON.** Ce document ne substitue pas des tests simulés à une validation graphique ou matérielle.

## A. Git : état initial et conservation

- Dépôt `grimalkin2811/Jarvis_Live_Ready`, branche imposée `arena/8edade8b-jarvis-live-ready`. Avant cette mission : HEAD distant `f3e143e1c78f83ed92401bf13848808adfeca877`, `origin/feature/v1.9.0` `43ff0029ca1eadfcaca40fd0cbdddb18248e11e8`, `origin/main` `ec944e26457b22492b5872ee520d6bb3ce410081`.
- L'état réinjecté dans le sandbox présentait une référence locale à `ec944e2` avec fichiers de travail avancés. Un index **temporaire**, construit depuis `f3e143e` et rafraîchi, a montré que les fichiers correspondaient à `f3e143e`, sauf `.gitignore`. Avance rapide de la référence et réalignement de l'index par `update-ref`/`read-tree`, sans réécriture des fichiers ; aucune branche créée ni reset destructif. Index réel ensuite vide.
- Historique depuis `43ff002` : `d393043`, `5cca4e4`, `f9a3144`, `749da3c`, `f4cdeca`, `f3e143e` dans cet ordre ; `ec944e2` est un ancêtre de `f3e143e`. Les commits `749da3c`, `f9a3144` et `43ff002` sont bien dans la lignée, et non des modifications perdues.
- `.gitignore` préexistant a cinq substitutions d'accents par U+FFFD ; SHA-256 initial `dd211e71f5d97f965505729839e147c895cec7409d786f3bd33f9dc94b11abce`. Il est conservé tel quel et exclu des commits. Son whitespace préexistant explique la sortie de `git diff --check` sans exclusion. Pas de fusion vers `main`, force-push, release ou suppression.

## B. Inventaire des dépendances et accès indirects

L'inventaire AST actualisé est [`inventaire-runtime-final-01-5.md`](inventaire-runtime-final-01-5.md), reproductible par `python scripts/inventory_runtime.py`. Il énumère les appels explicites, constructeurs, setters, `require` et `_resolve` reconnus et les états globaux. Il n'est **pas une preuve d'exhaustivité dynamique** : appels par alias, imports dynamiques, callbacks, services lancés dans un autre thread et fonctions de bibliothèques exigent la revue manuelle ci-dessous. Les lignes automatiques relatives aux getters sont des chemins possibles, pas une preuve qu'ils s'exécutent avec un Core injecté.

| Chemin / consommateur | Source effective et classement | Propriété, risque / raison |
|---|---|---|
| `CoreRuntime` → mémoire, conversation, scheduler, tâches, audio, provider | Enregistrement explicite au démarrage via factories ; `status`, `context`, `tools` au registre | Scheduler/tâches/audio sont fermés par Core ; mémoire et conversation ne sont pas fermées ici. Factories historiques règlent certains defaults globaux. Échec d'un scheduler ou Task Manager : service absent, pas de fabrication de remplacement par les outils injectés. |
| `CoreRuntime` → todo, routines, modes, activity, music | Services **empruntés**, lazy et mémorisés à la première résolution, via getters historiques | Ils ne sont pas la propriété exclusive du Core ; changement d'un default avant la première résolution ou appel legacy concurrent peut encore diverger. Registre sous verrou, pas de création parallèle via ce registre. |
| `GeminiLive` appel modèle et filet Dashboard | `tool_functions` et `services` injectés depuis Core ; provider autonome utilise `TOOL_FUNCTIONS` historique | Les outils sont exécutés en thread ; annuler la coroutine n'arrête pas le thread. Factory tierce à signature fixe antérieure reste compatible, mais ne reçoit pas cette injection et doit être migrée séparément. |
| `src/tools.py` mémoire, todo, tâches, conversation, scheduler, modes, routines, musique, activité | `_services` interne facultatif ; `_resolve` exige `services.require` avec Core, getter seulement hors Core | 62 fonctions/helpers migrés. `_services` n'est pas dans les déclarations utilisateur ; le wrapper refuse cet argument et masque ce paramètre de sa signature. L'outil `run_routine` transmet les outils liés au Core aux étapes. |
| Garde de mode et journal des outils | `modes` / `activity` du registre pour le dispatcher lié ; getters compatibles pour appels autonomes | Journalisation best-effort ; arguments non journalisés. Cette garde de mode n'est pas un moteur d'autorisation. |
| `routine_actions`, `backup._sources` | Scheduler et chemins de stockage obtenus du registre quand invoqués par les outils liés | APIs autonomes inchangées. Le backup exige toujours l'accord existant ; la voie modèle refuse les confirmations non vérifiées. |
| Routines lancées par scheduler ou interfaces legacy | `routines._TOOL_FUNCTIONS` / `TOOL_FUNCTIONS` global ; scheduler `_routines()` ou instance injectée selon construction | **Non migré** pour les exécutions planifiées et les appelants UI qui ne transmettent pas le dispatch ; la seule routine déclenchée via l'outil Core est liée. Ne pas conclure à un dispatch universel. |
| `protocols.py` et son thread différé | `_ACTIVE_RUN`, `_LISTENERS`, sondes via getters, `_tool_functions()` global | **Non migré**, le protocole peut continuer après retour de l'outil ; pas compté comme worker suivi par la nouvelle barrière. Console/UI et backend système demeurent historiques. |
| `scheduler.py` → notifications/routines/modes | `src.notifications` : hooks et éventuel worker Windows globaux ; suppression visuelle par getter mode ; gestionnaire de routines injectable ou fallback | **Non migré** ; un NotificationCenter Dashboard est local au hub, pas le bus backend, et ne doit pas être présenté comme la même instance. |
| `background_tasks` | Manager Core enregistré, boucle et workers internes, fichiers de sortie ; notifications backend globales | L'outil utilise le manager injecté mais une tâche lancée lui survit ; cycle de vie propre à tester indépendamment. Aucun Gemini ou outil externe réel n'a été appelé. |
| Orb, Desktop, tray, dialogues | Core et registre lorsqu'ils sont explicitement fournis ; getters historiques dans modes/dialogues si lancement autonome | Contrôleur Desktop partage le Core, overlay graphique local. Fermeture de fenêtre ≠ arrêt Core. Certaines actions/protocoles et callbacks restent historiques ; pas de preuve exhaustive GUI native. |
| Dashboard `hub`, services et actions | Hub injecté : status, conversation, tâches, mémoire, todo via Core ; hub autonome via getters. `actions.py` contient encore des chemins directs | `NotificationCenter`, jobs, moniteur ressources et préférences appartiennent au hub ; pont de notifications global ; UI fermée et nouvel hub ne sont pas une seule instance de centre de notifications. |
| `system_extras`, `tools` système / presse-papiers / timers, réseau, processus, `writing`, `UI/*_bridge` | APIs système, caches et bridges globaux historiques ; non remplacés par le Core | Whitelist et contrôles existants conservés ; accès réellement nécessaires au matériel/OS, non validés ici. Pas de prétention que tous les chemins passent par le nouveau dispatcher. |
| Tests et factories injectées | Doubles explicitement créés pour isolation, stores SQLite réels dans certains tests | Une fausse factory n'est pas une preuve d'identité en production ni une mesure de performance. |

## C. Architecture et arrêt

`RuntimeToolBindings` est propre à un Core : table de wrappers, résolution des dépendances par invocation, suivi sous `Condition`. `request_stop` interdit les nouveaux appels ; après fermeture du provider, une attente bornée de 2 s vérifie les appels actifs. Si un thread persiste, le Core signale `FAILED`, conserve le registre et le verrou de propriétaire au lieu de fermer des ressources encore utilisées. `complete_deferred_cleanup()` ne bloque pas sur le worker : l'appelant doit le rappeler après la fin effective pour fermer les services et libérer le propriétaire ; aucune libération automatique n'est promise. Les protocoles différés et autres chemins hors dispatcher ne sont pas couverts par cette barrière. L'attente est bornée mais synchrone sur la boucle Core ; il faut mesurer son impact réel. Les callbacks tardifs du hub sont ignorés après son arrêt.

Le registre est verrouillé, lazy et sans fallback si le service explicitement requis est absent ou fermé. La compatibilité standalone reste intentionnelle. Un Core actif maximum est admis par processus. L'injection ne fait pas disparaître les singletons empruntés ni ne rend tous les services immuables.

## D. Preuves d'intégration

- `tests/test_core_runtime_tool_bindings.py` : Core vivant avec audio/provider factices et vrais stores mémoire, todo et rappels SQLite ; écriture par outils, lecture par les gestionnaires du même Core, conversation partagée, `services` et table d'outils reçus par le provider, refus d'un argument interne, absence de fallback en cas de service manquant. Une routine réellement stockée appelle un outil lié et écrit dans la mémoire injectée. Quatre threads simultanés obtiennent une seule instance lazy.
- Même fichier : arrêt pendant un outil bloqué, rejet des nouveaux appels, statut d'échec, registre et propriétaire préservés, puis fin du thread et nettoyage différé ; factory provider à ancienne signature. Vrai objet `GeminiLive` avec session factice : appel modèle et filet Dashboard passent par la table injectée, **sans réseau réel**. `test_core_live_pipeline.py` couvre aussi audio et texte sur une session Live simulée, pas un test Gemini ou micro réel.
- `tests/test_core_dashboard_runtime_events.py` : sous-processus `QCoreApplication` réel, `DashboardHub`, Core, SQLite réels ; partage d'état mémoire/todo/rappels, événement du bus, notification du scheduler reçue par le hub, fermeture puis nouveau hub, même Core et réception d'une seconde notification. `test_core_dashboard_shared_services.py` couvre notamment identité et détachement des tâches/conversation et callback tardif. Les notifications utilisent le bus historique : le centre de chaque hub est local.
- **Pas une preuve GUI** : QtCore n'affiche ni Orb ni Desktop ni fenêtre Dashboard. `tests/test_ui_core_injection.py` tente de construire les widgets mais son import `QtWidgets` échoue ici ; pas de démonstration réelle Orb/Desktop/Dashboard, partage UI intégral, fermeture et réouverture graphiques. Aucun succès ne lui est attribué.

## E. Tests et régressions

| Catégorie / commande | Résultat exécuté |
|---|---|
| Core, injection, identité, lifecycle, concurrence : `pytest -q tests/test_core*.py` | **39 réussis** ; inclut les 7 tests de `test_core_runtime_tool_bindings.py` et le test QtCore de notification. Second Core refusé lorsqu'un Core fonctionne ; initialisation simultanée depuis deux threads distincts non démontrée. |
| Context : `pytest -q tests/test_context_engine.py tests/test_context_scenarios.py` | **12 réussis**. |
| Outils et régression ciblée : `pytest -q tests/test_tools.py tests/test_backup.py tests/test_routines.py tests/test_routine_presets.py tests/test_dashboard_intent_fallback.py tests/test_gemini_live.py` | **107 réussis, 36 sous-tests réussis** ; APIs autonomes, routines, backup et Gemini simulé. |
| Ensemble ciblé après correction finale : les trois groupes ci-dessus | **158 réussis, 36 sous-tests réussis**, 13,18 s. Les catégories sont des groupes de fichiers, pas une couverture totale des chemins. |
| UI : `pytest -q tests/test_ui_core_injection.py tests/test_core_dashboard_shared_services.py tests/test_core_dashboard_runtime_events.py tests/test_dashboard_registry_actions.py` | **4 réussis, 1 échec** (`QtWidgets`, `libGL.so.1`). Les réussites QtCore ne sont pas une validation graphique. |
| Suite complète finale : `QT_QPA_PLATFORM=offscreen JARVIS_NO_MODEL_DOWNLOAD=1 python -m pytest -q -rs --continue-on-collection-errors --disable-warnings` | **1 477 réussis, 561 sous-tests réussis, 114 skips, 16 échecs, 21 erreurs de collecte**, 498,66 s, sortie 1. |
| Vérifications statiques | `ruff check` sur fichiers modifiés, `python -m compileall -q src UI scripts/inventory_runtime.py`, `pip check` et `git diff --check` hors `.gitignore` : réussis. |

Les tests d'événement asynchrone traversent le relais QtCore et des callbacks de thread ; cela ne prouve pas toutes les courses GUI. Tests d'arrêts multiples, arrêt pendant démarrage, échec de service, abonné défaillant et callback tardif dans `test_core_runtime.py`, `test_core_shutdown_regressions.py`, `test_core_dashboard_shared_services.py` et `test_core_runtime_tool_bindings.py`.

La session précédente avait 1 469 réussis, 561 sous-tests réussis, 114 skips, 16 échecs et 21 erreurs de collecte ; les 37 traces pointaient vers `libGL.so.1` manquante. Un rerun intermédiaire avant la dernière petite correction a donné 1 477 réussis, 561 sous-tests, 114 skips, 16 échecs, 21 erreurs en 497,50 s : **37 traces `ImportError: libGL.so.1`**, donc mêmes symptômes que précédemment ; cela n'exclut pas d'autres erreurs une fois Qt fonctionnel. Le rerun final après correction donne exactement les mêmes noms d'échecs et de collecte et 37 traces `libGL` ; son résultat est indiqué dans le tableau. Installation Linux non verrouillée, pas de périphériques ni API réels. `--continue-on-collection-errors` permet de voir les autres résultats sans transformer les erreurs en skips.

Détail des **16 échecs du rerun final**, tous sur `ImportError: libGL.so.1` :

- `test_dashboard_config.py::test_presets_only_reference_registered_widgets` ;
- `test_dashboard_notifications.py::{test_hub_routes_real_task_hooks_and_bridge_filter_avoids_legacy_duplicate,test_notification_bridge_filter_suppresses_display_only}` ;
- `test_dashboard_security.py::test_every_widget_action_has_a_policy` ;
- `test_dashboard_ui_feedback.py::AudioFailureNotificationTests::{test_non_audio_engine_failure_is_still_notified,test_one_audio_failure_raises_one_notification}` ;
- `test_desktop_config.py::AppearanceStateIntegrationTests::{test_desktop_block_is_persisted_and_restored,test_no_second_config_file_is_created,test_saving_a_v160_state_adds_the_block,test_survives_multiple_save_load_cycles,test_unknown_future_keys_do_not_break_loading,test_v160_file_still_loads_with_defaults}` ;
- `test_manual_stop.py::UiWiringTests::test_stop_handler_empties_output_then_marks_a_manual_stop` ;
- `test_pyside_refcount.py::test_void_methods_do_not_leak_none_references` ;
- `test_ui_core_injection.py::test_orb_desktop_dashboard_share_injected_core` ;
- `test_validation_protocole.py::test_lag_probe_charges_each_gap_to_the_phase_where_it_is_observed`.

Les **21 erreurs de collecte** concernent `test_blob_visibility`, `test_dashboard_conversation_widget`, `test_dashboard_entry_points`, `test_dashboard_perf`, `test_dashboard_productivity`, `test_dashboard_tasks`, `test_dashboard_widgets`, `test_dashboard_window`, `test_interruption_ui`, `test_item_bg_opacity`, `test_menu_backgrounds`, `test_menu_hover`, `test_menu_integration`, `test_menu_layout`, `test_menu_render_offscreen`, `test_menu_ux`, `test_mode_apps_dialog`, `test_orb_visibility`, `test_protocol_ui`, `test_routines_ui`, `test_ui_menu_regression` (tous dans `tests/`, extension `.py`), même import manquant.

Les **114 skips** du rerun final : `test_desktop_appearance_dialog.py` 28, `test_desktop_hud.py` 39, `test_desktop_integration.py` 22, `test_desktop_overlay.py` 8, `test_interface_mode.py` 4, `test_launcher_gui.py` 11, deux via `unittest`. Motifs déclarés : 93 « PySide6 indisponible », 8 « PySide6 / OpenGL indisponible », 11 « PySide6 non installé », 2 mentionnant `libGL.so.1`. PySide6 est en réalité installé ; les libellés sont ceux des tests détectant l'échec de chargement natif. Un skip n'est pas une réussite.

## F. Context Engine

`ContextEngine` est enregistré dans le Core, mais ne construit aucune connexion Gemini ni ne crée de singleton. Tests `test_context_engine.py` et `test_context_scenarios.py` : sources explicitement demandées, mémoire désactivée par défaut, environnement fourni par appelant et conditionné par `allow_environment` (ce booléen n'est **pas** une permission), pas de collecte implicite ni accès mémoire modifié ; snapshots et éléments `frozen`, tuples, identifiant/heure UTC, conversation, intention, provenance, sélection et exclusions. Données marquées `untrusted_data`, `instructions` vide. Ce marquage n'est pas une garantie de résistance aux injections d'un futur consommateur. Sélection lexicale, coût UTF-8/4 seulement indicatif ; limites de 30 messages et 10 souvenirs font que les éléments non collectés n'ont pas chacun une trace d'exclusion. `build_async` est single-flight et borné par timeout, mais le thread sous-jacent ne peut pas être tué et peut retarder l'arrêt Python. Le contexte éphémère ne remplace pas la mémoire persistante.

## G. Windows 11, G5400, performance et reproduction

Aucun Windows natif, matériel audio, GPU, API Gemini réel ou Pentium G5400 n'a été exécuté pour cette livraison. Ne pas extrapoler à cette configuration. Benchmark relancé sur Linux x86-64, Python 3.11.2, Xeon 2,60 GHz, 2 CPU logiques (`scripts/benchmark_context.py --iterations 500`) :

| Messages artificiels | Temps mur pour 500 | CPU pour 500 | Pic Python `tracemalloc` |
|---:|---:|---:|---:|
| 5 | 0,3386 s | 0,3382 s | 8 771 octets |
| 30 | 1,5482 s | 1,5481 s | 18 219 octets |
| 200 | 1,5975 s | 1,5958 s | 18 300 octets |

Au plus 30 messages lus, ni SQLite ni RSS ni GUI, concurrence possible avec la suite de tests ; ce n'est pas une mesure Windows ou G5400.

Qt Linux : `PySide6` installé mais `ldd .../PySide6/Qt/lib/libQt6Gui.so.6` montre `libGL.so.1`, `libEGL.so.1`, `libxkbcommon.so.0`, `libdbus-1.so.3` absentes. Recherche de wheels officielles OpenCV/VTK pour bibliothèques isolées : aucune pile GL complète retenue ; essais sysroot précédents ont révélé d'autres dépendances. Aucun binaire tiers non vérifié n'a été installé et QtCore n'est pas qualifié GUI. Reproduction : installer les paquets natifs de distribution fournissant ces bibliothèques, puis `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui_core_injection.py` et la suite complète. Tester aussi la plateforme graphique native, pas seulement offscreen.

Windows PowerShell reproductible sur machine et commit exact à vérifier :

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --require-hashes -r requirements-lock/windows-py311.txt
python -m pip check
$env:QT_QPA_PLATFORM = 'windows'
$env:JARVIS_NO_MODEL_DOWNLOAD = '1'
python -m pytest --continue-on-collection-errors -q -rs
python scripts/dashboard_first_open_profile.py --natif --repetitions 5 --reouvertures 5 --json profil.json
python scripts/dashboard_perf.py --out perf_natif
python scripts/benchmark_context.py --iterations 500
```

Si les outils de test ne sont pas dans le verrou, les installer dans un environnement de test documenté séparément et conserver leurs versions. Exécuter aussi manuellement transitions Orb/Desktop/Dashboard, affichage/fermeture/réouverture, messages, rappels et notifications, puis arrêt pendant activité et contrôle du gestionnaire des tâches. Mesurer réactivité, CPU, RAM/RSS et ouverture à froid/chaud, avec modèle et charge documentés, sur le PC cible. La vérification hors ligne antérieure des hashes de 63 wheels Windows n'est pas une installation ni un test Windows.

Après push du code `a806e2d`, `gh workflow run build.yml --ref arena/8edade8b-jarvis-live-ready` a répondu **HTTP 403 `Resource not accessible by integration`**, endpoint `https://api.github.com/repos/grimalkin2811/Jarvis_Live_Ready/actions/workflows/354263232/dispatches`. Aucun run n'a été créé par cette commande ; aucune permission n'a été contournée. Un utilisateur disposant des droits Actions doit lancer une validation native sur le HEAD final. Le workflow `pr-windows-native.yml` est déclenché par PR et non par ce dispatch.

## H. Préparation du lot 02, sans faux Security Engine

La table `RuntimeToolBindings.functions` est un point d'entrée par Core pour les appels Gemini et pour les étapes de routines déclenchées par cet outil. Le code reçoit le nom (clé), les arguments (wrapper), les services (registre) et renvoie un résultat ; `ActionProposal` distingue déjà `origin='model'`. Il ne fournit pas encore un contrat complet de provenance/consommateur par invocation, ni une classification fiable voix/texte, ni une couverture des routines planifiées, protocoles, UI et workers. Ne pas déclarer la frontière universelle.

`confirm=true` venant du modèle est refusé par `reject_unverified_action` : ce n'est pas une autorisation. La garde de mode et les contrôles UI existants ne sont ni des preuves indépendantes de consentement ni un Security Engine. Le futur lot devra définir origine, consommateur, identité de l'outil, arguments normalisés, services/ressources, résultat, autorisation indépendante vérifiable et audit expurgé pour **toutes** les voies, sans faire confiance au modèle ou aux données de contexte. Rien de cela n'est simulé comme déjà opérationnel ici.

## I. Verdicts et blocages

| Périmètre | Verdict | Portée |
|---|---|---|
| Runtime | PARTIEL | Core unique et enregistrement testés ; dépendances historiques empruntées. |
| Outils | PARTIEL | Appels directs Core et routines déclenchées via outil migrés ; protocoles, routines planifiées et chemins legacy restent globaux. |
| Identité et état partagé | PARTIEL | SQLite, conversation et hub QtCore démontrés ; identité totale entre toutes UI et notifications non démontrée. |
| UI | NON VALIDÉ | QtWidgets et test intégré graphique bloqués, Windows natif absent. |
| Lifecycle / concurrence | PARTIEL | appels directs suivis et arrêt différé testés ; workers hors dispatcher, callbacks et procédures réelles restent à couvrir. |
| Context Engine | VALIDÉ SOUS RÉSERVES | tests offline ; collecte explicite et immuabilité ; timeout sans interruption de thread, traçabilité bornée. |
| Suite / régression | NON VALIDÉ | échecs et erreurs présents ; voir décompte ci-dessus. |

**LOT 01.5 CLÔTURABLE : NON.** Priorités : (1) valider et corriger la suite sur environnement Qt réel, puis Orb/Desktop/Dashboard avec un Core, fermeture et réouverture ; (2) finir l'injection des routines planifiées, protocoles, notifications et actions UI sans casser les appels autonomes, et couvrir leurs workers/arrêts ; (3) exécuter la suite Windows 11 native et les scénarios matériels/G5400 ; (4) vérifier les suites complètes et traiter chaque échec restant avant toute clôture. Aucun des verdicts partiels n'autorise à présenter le lot comme terminé.
