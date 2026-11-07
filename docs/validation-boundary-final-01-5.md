# Validation de clôture — Runtime Boundary 01.5

## Reprise des six réserves — décision actuelle

**ARCHITECTURAL: PARTIAL** · **ENVIRONMENTAL: PARTIAL**. Le lot 01.5 n'est pas clos. Les six classifications ci-dessous remplacent celles des audits historiques. La branche était un checkout superficiel avec HEAD sur `ec944e2` mais fichiers correspondant exactement au SHA distant `ae477be6c788613a974926377cd7e7570db37794` ; comparaison avec index temporaire, aucun autre fichier non ignoré ni différence suivie hors `.gitignore`, puis avance rapide de référence/index sans toucher aux fichiers. `main` est restée à `ec944e26457b22492b5872ee520d6bb3ce410081`. `.gitignore` préexistant demeure hors commit et de SHA-256 `dd211e71f5d97f965505729839e147c895cec7409d786f3bd33f9dc94b11abce`.

Code et tests finaux : `2379a94aa75625742e141f92b1b49db46fa2aef7` ; corrections principales dans `2197660e3c8e07d7094a2fd5c603e0acc560580c`. Ce rapport est commis séparément : le SHA final se vérifie par `git rev-parse HEAD` et dans le compte rendu, sans auto-référence. État final attendu : seul `.gitignore` est modifié. Aucun lot 02.

| Réserve | Classification | Preuve/Test | Correction | Risque résiduel |
|---|---|---|---|---|
| #1 Orb / catalogue de routines | **NOT_VALIDATED** | `test_worker_launch_boundary.py` force admission/stop/création ; `test_routines_ui_boundary_without_widgets.py` exécute les corps AST réels des méthodes après stop, pas les widgets ; tests QtWidgets réels impossibles à importer | Orb et catalogue injecté lancent via `start_thread` ; dialogue prend des baux pour refresh, démarrage et mutation ; fermeture refuse le worker et la mutation. Routines autonomes restent séparées | intégration QtWidgets, tous les points d'entrée, callbacks tardifs et réinjections graphiques non prouvés dans cet environnement ; ne pas assimiler le test AST à un test GUI |
| #2 Protocoles / jobs déjà admis | **BLOCKING** | tests à barrières : T0 bail admis, T1 fermeture boundary, T2 lancement, T3 aucune création ; trois scénarios répétés 15 fois. Le test `test_protocol_admission_is_atomic_even_when_thread_start_is_delayed` force aussi un `Thread.start` déjà entré et une demande d'arrêt concurrente | `ActivityBoundary.launch` sérialise le démarrage effectif avec `reject_new` ; refus une fois la boundary fermée ; timers Runtime et thread routines l'utilisent aussi | si `Thread.start` ou `executor.submit` bloque déjà *dans* le verrou, `request_stop` attend et la création peut se produire après l'appel de demande d'arrêt, mais avant sa transition de fermeture. La propriété stricte dès la demande n'est donc pas démontrée ; les autres créateurs Core/factories custom ne sont pas tous couverts |
| #3 Hub non bloquant | **BLOCKING** | vrai sampler et job bloqués ; retour, statut incomplet, refus, libération, jointure des vrais threads, statut complet et aucun callback utilisateur ; tests répétés | `shutdown(blocking=False)` et `shutdown_status()` ; tentative de jointure bornée optionnelle, callbacks JobRunner déjà entrés comptés | la fermeture peut attendre le verrou d'une soumission en cours ou le code des hooks externes ; les callbacks externes déjà en vol / événements Qt en file ne sont pas universellement comptés. Le statut `complete` ne certifie que pool/retours JobRunner et sampler, pas SDK/bus Qt externes |
| #4 UI synchrone / hooks / dialogues | **NOT_VALIDATED** | inventaire manuel d'appels et propriétaires, tests QtCore, tests AST limités pour #1 | baux du catalogue et état JobRunner améliorés ; pas de migration mécanique des appels synchrones | autres références cachées, hooks de tâches/conversation, dialogues modes/tâches et callbacks Qt tardifs sans preuve globale ; QtWidgets non exécuté |
| #5 factories pré-démarrées / singletons | **BLOCKING** | revue des factories `CoreRuntime._run_pipeline`, tests d'identité/arrêt du chemin standard | aucune migration automatique des API standalone | une factory custom peut démarrer avant injection, voire partager un singleton avec un autre propriétaire ; pas de contrat imposant le non-démarrage ni de preuve d'isolation des mutations externes |
| #6 erreur de cleanup / provider bloqué | **BLOCKING** | tests `test_cleanup_failure_visibility.py` : échec permanent, close concurrent en cours, état FAILED/absence de faux succès ; test provider asynchrone retardé : owner retenu, état STOPPING | registre conserve le résultat d'échec, aucun retry implicite ni double appel ; `complete_deferred_cleanup` ne retourne plus vrai après échec ; `EngineState.STOPPING` expose un provider en cours de fermeture | callback/SDK pouvant bloquer indéfiniment, aucun timeout ou abandon sûr universel ; en cas de callback échoué Core libère encore l'owner après contrôle de ses threads connus, sans preuve sur workers externes ni retry sûr du callback |

### Contrat Hub et preuve de convergence

`shutdown(blocking=False)` interdit les nouvelles soumissions et souscriptions du Hub, détache les hooks connus, signale le sampler et annule les futures en attente ; le pool est arrêté avec `wait=False`. Son retour ne garantit **pas** la mort des threads ni la fin d'un callback déjà engagé. `shutdown_status()` expose `requested`, `jobs_done`, `resources_done`, `complete` ; ce dernier est une preuve bornée aux threads executor, callbacks JobRunner déjà entrés et sampler, non une preuve d'extinction de tous les callbacks/threads Qt ou SDK. Une soumission de création en cours peut momentanément retenir le verrou et faire attendre `shutdown(False)` : le nom ne garantit donc pas une latence absolue. `shutdown(blocking=True, timeout=...)` tente une jointure bornée ; il faut toujours relire le statut. Le Hub est terminal et l'appel répété est idempotent. Les méthodes QObject du Hub restent à appeler depuis Qt ; les tests concurrents ciblent explicitement le JobRunner, pas l'ensemble des widgets.

### Politique cleanup et provider

Un callback de registre est appelé au plus une fois : les échecs sont persistés, journalisés et renvoyés à chaque `close`, sans retry infini ni reprise supposée sûre. Un second `close` pendant l'exécution renvoie `cleanup_in_progress`. Les services sont retirés du registre avant callbacks ; l'échec reste terminal et doit être traité par l'appelant. Pour un provider qui ne retourne pas, Core reste propriétaire et publie STOPPING pendant la coroutine de cleanup ; on ne force ni ne déclare fermé le SDK. Aucun timeout global sûr n'a été prouvé ; cette limite et la libération de l'owner après un callback échoué justifient #6 BLOCKING.

### Tests réellement exécutés

- Ciblés Runtime, Hub, TaskManager, protocoles, UI alternative, Context et audio logiciel : **359 passed in 40.20s** sur `2379a94`. Les 339 tests précédents sont conservés ; les méthodes QtWidgets elles-mêmes ne sont pas exécutées par les deux tests AST alternatifs.
- Courses d'admission et TaskManager : **15 exécutions sur 15**, six tests par exécution. Pour `launch`, le verrou représente un point de linéarisation ; une primitive native de création bloquée à l'intérieur peut bloquer le demandeur d'arrêt.
- Qt séparé sur `2197660` (avant la seule adaptation de signature `shutdown(False)`) : **12 passed, 1 failed, 4 collection errors in 8.84s** ; erreurs QtGui/QtWidgets/QtTest et échec d'identité UI causés par `ImportError: libGL.so.1`. QtCore n'est pas QtWidgets. Xvfb et autres bibliothèques graphiques natives ne sont pas supposés présents.
- Première suite complète sur `2197660` : `QT_QPA_PLATFORM=offscreen JARVIS_NO_MODEL_DOWNLOAD=1 python -m pytest -q -rs --continue-on-collection-errors --disable-warnings` : **1 516 passed, 561 subtests passed, 114 skipped, 16 failed, 21 errors in 515.20s**. Les 37 traces d'échec/erreur indiquent `ImportError: libGL.so.1`, classification environnementale ; il ne s'agit pas de PASS QtWidgets. Les skips existaient déjà. **Suite complète finale sur `2379a94` : 1 516 passed, 561 subtests passed, 114 skipped, 16 failed, 21 errors en 514.75s**, avec les mêmes 37 cas `libGL.so.1` que la liste individuelle historique ci-dessous. Windows natif, Gemini réel, audio matériel et QtWidgets réels non exécutés ; aucune permission CI contournée.
- `ruff check .`, compilation `src UI tests`, `pip check`, `git diff --check` hors `.gitignore` réussis.


---

## Décision de la reprise sur `2b4b339` — actuelle

**VERDICT ARCHITECTURAL : PARTIAL. VERDICT ENVIRONNEMENTAL : PARTIAL.** Le lot n'est pas clos. Il reste des violations et des lifecycles critiques non prouvés, et non uniquement des limites de machine. Les sections historiques ci-dessous décrivent la validation précédente ; le présent encadré et l'inventaire complémentaire les complètent et priment si nécessaire.

Le préflight a constaté un checkout superficiel dont HEAD/index avaient été réinitialisés à `ec944e2` alors que la branche distante et le contenu de fichiers correspondaient à `2b4b339`. Fetch de l'objet, comparaison contre un index temporaire : aucune divergence de fichier suivi hors `.gitignore`, aucun fichier non ignoré supplémentaire ; `ec944e2` est ancêtre de `2b4b339`. Alignement par avance rapide de la référence et de l'index, sans changement de fichier ni réécriture de commits. Branche `arena/8edade8b-jarvis-live-ready` ; `main` distant reste `ec944e26457b22492b5872ee520d6bb3ce410081`. `.gitignore` préexistant non commité, SHA-256 inchangé `dd211e71f5d97f965505729839e147c895cec7409d786f3bd33f9dc94b11abce`.

**Code et tests évalués : `91be09342484631b875259eb0f303690105f4258`**, enfant de `2b4b339ee78f4f526d385ec9d0cdc1bbeb9f3b37`. Ce rapport et l'inventaire sont commis après les tests sans changer le code ; le SHA final du rapport est donné par `git rev-parse HEAD` après commit/push, sans autoréférence circulaire. L'état final visé est propre hors `.gitignore` préexistant.

### Corrections de cette reprise

- Dans `JobRunner`, admission de l'executor et fermeture du Hub sont sérialisées : une soumission qui a passé le contrôle ne peut plus créer un thread **après que le Hub s'est déclaré fermé**. En cas d'erreur inattendue de `submit`, le callback est retiré et l'erreur propagée. Le verrou couvre `ThreadPoolExecutor.submit`, opération usuellement brève ; si la création native de thread elle-même se bloque, un `shutdown` concurrent peut attendre, limite non qualifiée de réussite.
- Le `DashboardHub` est terminal après `shutdown`, y compris appelé avant `start` : nouvelle souscription/relance du sampler et démarrage du pool sont refusés. `start/shutdown` et gestion de l'UI restent des appels du thread Qt ; on ne déclare pas QObject thread-safe.
- `tests/test_final_hub_shutdown_admission.py` : vrai submit retardé contre shutdown concurrent, thread executor réellement joint, soumission tardive refusée, erreur inattendue nettoyée, shutdown répété, Hub fermé avant/après start. Quinze exécutions répétées avec les deux tests de courses TaskManager : **15/15**, six tests par exécution.
- Context : un nouveau test en subprocess interdit l'import de `google.genai` et PySide6 ainsi que les lectures `os.environ`/`os.getenv` pendant une construction offline ; les tests précédents couvrent snapshots gelés, provenance, exclusions, budget, sélection, mémoire SQLite en lecture et timeout. Ce test ne certifie pas l'interne d'un fournisseur externe.

### Réserves exactes bloquant la clôture

L'ensemble détaillé avec catégories exactes `OK_ARCHITECTURAL`, `COMPATIBILITY_JUSTIFIED`, `TO_CORRECT`, `NOT_VALIDATED`, propriétaires, threads et caches se trouve dans la section « Audit de décision complémentaire » de [l'inventaire](inventaire-runtime-final-01-5.md). Réserves critiques restantes :

1. **Orb et catalogue routines** : `_run_routine_async` et le worker du dialogue peuvent créer un thread sans admission préalable après stop ; `_set_enabled` du dialogue conserve le manager emprunté et peut muter le fichier routines après arrêt Core. QtWidgets non disponible ici ; correction sûre et couverture intégrale de ces chemins restent à faire.
2. **Admissions déjà acceptées** : un protocole ou job Dashboard admis avant fermeture peut encore réellement créer son thread après début de `request_stop`. Le bail conserve les services, mais cela ne démontre pas la propriété plus stricte « aucun nouveau worker après début du shutdown » ; le test de protocole à démarrage retardé le montre explicitement.
3. **Fermeture complète Hub/Core** : `shutdown(wait=False)` peut rendre la main alors que des threads du pool, un sampler ou un callback sont encore vivants. Le test joint les threads non bloqués réellement ; il ne prouve pas leur fin au retour de `shutdown`, ni l'arrêt de workers externes bloqués. Le Core conserve son propriétaire pour ses activités suivies ; cela ne couvre pas universellement les workers UI.
4. **UI et caches** : acquisitions et usage réels de certains services/hook Qt, références de dialogues, lectures/mutations synchrones et callbacks tardifs ne sont pas tous couverts par un bail. L'inventaire identifie chaque famille et son thread. Les caches de chaînes/copies ne sont pas des services en soi et ne sont pas déplacés mécaniquement.
5. **Factories et singletons** : une factory Scheduler/TaskManager personnalisée peut rendre un objet déjà démarré avant injection ; les getters/setters historiques et services empruntés peuvent être modifiés concurremment ou hors session. Le mode autonome reste légitime, mais l'isolation universelle n'est pas prouvée.
6. **Erreur de cleanup** : `ServiceRegistry.close` efface les callbacks avant exécution ; si un callback échoue, le retry automatique de cet objet n'est pas garanti. Un provider SDK bloqué peut garder owner/ressources indéfiniment et un sampler repris pendant blocage ne redémarre pas automatiquement.

Une correction purement cosmétique ou une suppression des compatibilités ne réglerait pas ces cas. La sécurité des services suivis est prouvée dans les tests correspondants, pas pour tous les accès ni pour les internals SDK. Une synchronisation stricte entre arrêt et création de thread doit aussi éviter d'attendre indéfiniment dans le thread Qt ; ne pas déclarer le problème résolu en masquant les workers.

### Tests de cette reprise

- Tests ciblés Runtime/lifecycle, Context, Dashboard, TaskManager et audio logiciel : **339 passed in 37.22s** ; exécution Linux Python 3.11.2 avec PySide6 6.11.3 et `QT_QPA_PLATFORM=offscreen`, sur le contenu de `91be093`. Commande `python -m pytest -q` avec les fichiers `tests/test_{final_hub_shutdown_admission,core_activity_boundary,final_boundary_ui_lifecycle,final_task_boundary_integration,core_task_start_stop,background_tasks,protocols,dashboard_resources,core_runtime,core_runtime_tool_bindings,core_dashboard_runtime_events,context_engine,context_scenarios,conversation_context,live_context_harness,voice_runtime,core_concurrent_shutdown,core_thread_shutdown,core_deferred_integration,core_mic_shutdown,dashboard_registry_actions,dashboard_memory_service,core_dashboard_shared_services,core_shutdown_regressions,audio_controls,audio_turn_pending,mic_stream_pause,wakeword}.py`. `ruff check .`, `compileall` et `pip check` réussis ; `.gitignore` exclu de `git diff --check` car son espace final préexiste.
- Test Qt distinct : **10 passed, 1 failed, 4 collection errors in 6.72s**. Échec et erreurs : QtWidgets/QtGui/QtTest impossibles à importer, `libGL.so.1` absente. `ldd` signale également `libEGL.so.1`, `libxkbcommon.so.0`, `libdbus-1.so.3` absentes ; Xvfb absent. Les succès QtCore ne valident pas QtWidgets.
- Suite complète du code figé : `QT_QPA_PLATFORM=offscreen JARVIS_NO_MODEL_DOWNLOAD=1 python -m pytest -q -rs --continue-on-collection-errors --disable-warnings` : **1 506 passed, 561 subtests passed, 114 skipped, 16 failed, 21 errors in 504.47s**. Les 37 traces d'échec/erreur signalent `ImportError: libGL.so.1` ; chaque cas est classé individuellement en fin de rapport. L'échec d'import ne prouve pas le bon fonctionnement des tests qui n'ont pu s'exécuter.
- Non exécutés : Windows natif, Gemini réel, audio matériel, QtWidgets réelle et machine G5400. Les tests audio logiciels ne valident pas les pilotes ni périphériques ; aucune action CI/permission n'a été contournée.


---

## Historique de la validation précédente

## Verdicts distincts

- **Architecture : PARTIEL.** Les scénarios Core et les corrections ci-dessous sont vérifiés, mais la frontière n'est pas universelle : certains accès GUI synchrones et références cachées à des services ne détiennent pas de bail ; des getters/setters globaux et factories personnalisées pré-démarrées peuvent diverger. L'arrêt du seul Core ne prouve pas l'extinction des threads inactifs de l'executor appartenant au Hub. Il serait incorrect d'affirmer « zéro worker connu orphelin » pour tous les parcours ou l'absence générale d'accès après destruction. Pas de clôture architecturale globale sur cette preuve.
- **Environnement : PARTIELLE.** Tests Python et QtCore exécutés sur Linux ; QtWidgets bloqué par dépendances natives manquantes. Windows 11, Pentium Gold G5400/16 Go, Gemini réel et périphériques audio ne sont pas validés. Ces limitations ne constituent pas à elles seules un échec architectural.

L'inventaire détaillé, avec propriétaires, arrêt, accès et verdicts `OK`, `COMPATIBILITÉ JUSTIFIÉE`, `À CORRIGER` ou `NON VALIDÉ`, est dans [inventaire-runtime-final-01-5.md](inventaire-runtime-final-01-5.md). Il concerne les chemins recensés, pas les imports dynamiques ou internals des SDK. Les compatibilités autonomes ne sont pas à migrer sans risque démontré. Les mentions `OK` sont limitées au chemin et aux scénarios indiqués.

## Provenance et intégrité Git

- Base initiale de la mission : `a26e4c2da524629e72df56955567471ba151c2f0` ; commit de code et d'inventaire : `7d4a89a7edb81d5a482fb13f405a15dddbb6f613` ; synchronisation d'un test de course : `1a0eb100dab9ccc6bbcf4e181d148f99be71cc61`. Ce rapport sera dans un commit distinct ; son SHA final se vérifie par `git rev-parse HEAD` et dans le compte rendu de livraison, pour éviter l'autoréférence.
- Branche de travail et push prévus exclusivement sur `arena/8edade8b-jarvis-live-ready` ; `main` distant contrôlé à `ec944e26457b22492b5872ee520d6bb3ce410081`, non modifié. Les commits `43ff002`, `f9a3144`, `749da3c` sont ancêtres de la base ; la branche `feature/v1.9.0` était à `43ff0029ca1eadfcaca40fd0cbdddb18248e11e8` au contrôle.
- `.gitignore` était déjà modifié localement ; exclu des commits et conservé octet pour octet, SHA-256 `dd211e71f5d97f965505729839e147c895cec7409d786f3bd33f9dc94b11abce`. Ce fichier seul doit rester modifié dans le statut final.
- Inspection du diff de code et des nouveaux tests, `git diff --check -- . ':!.gitignore'`, compilation, `ruff check .`, `pip check` réussis. La vérification incluant `.gitignore` signale un espace final préexistant ; ce fichier n'a pas été modifié pour le corriger. Recherche ciblée de formes clés privées, clés Gemini et jetons GitHub sur les fichiers livrés sans correspondance ; ce contrôle limité ne prouve pas l'absence de tout secret. Aucun reset destructif, force-push, fusion sur main ou release.

## Correctifs apportés et preuves

1. **Protocole.** Le bail est réservé avant `Thread.start`, transféré au worker et libéré à sa sortie ; une demande tardive retourne `None` avant création. Test avec vrai thread dont `start` est bloqué : le Core reste propriétaire et conserve les services jusqu'au retour. Le mode autonome reste séparé.
2. **TaskManager.** Un seul `loop.stop` est programmé à la première demande d'arrêt. La décision d'exécuter la boucle et la prise de décision de stop sont synchronisées : pas de stop résiduel dans le `run_until_complete` de nettoyage lorsqu'on ferme entre `_ready` et `run_forever`. Les opérations de marquage et d'annulation tardives sur un manager lié au Runtime sont refusées ; les mutations internes nécessaires à la fin des tâches restent permises. Tests déterministes de stop répété sur boucle bloquée et de la fenêtre ready/run, client fermé exactement une fois, tâche annulée et état persistant relu. Intégration réelle scheduler/routine + protocole : même manager que TasksService et Core, deux tâches achevées et restaurées depuis disque, thread réellement terminé et création tardive refusée.
3. **Dashboard.** Lecture synchrone de conversation et remise à zéro sous bail ; un cache n'est pas réutilisé après fermeture. Admission des jobs avant soumission et bail propre au thread à l'exécution ; soumission tardive retourne `0` sans créer de thread. La disponibilité des tâches et l'accès Orb refusent la fermeture ; bascule mémoire sous bail avec erreur visible en cas d'échec. QtCore : worker réellement bloqué conservant un service Core possédé, destruction une seule fois après sa sortie ; répétition et retry de nettoyage vérifiés. Les autres accès UI synchrones ne sont pas tous couverts.
4. **ResourceMonitor.** Si le sampler est bloqué, son thread demeure visible après timeout et empêche un doublon ; le résultat du sampler bloqué testé n'est pas distribué après signal de stop. Limite fonctionnelle : un abonnement repris pendant ce blocage ne redémarre pas automatiquement après sortie ; les callbacks déjà en cours ne sont pas universellement drainés.
5. **Provider bloqué.** Test d'un shutdown asynchrone retardé : Core et services restent possédés jusqu'à sa libération. L'attente n'a pas de timeout global : un SDK définitivement bloqué peut conserver indéfiniment le propriétaire. Aucune prétention de tuer un thread externe.

### Scénarios A–D

- **A, absence d'orphelins : partiellement démontrée.** Scheduler, TaskManager, minuteurs et activités explicitement suivies sont attendus ou maintiennent le propriétaire ; des tests examinent l'état réel des threads, non seulement un drapeau. Un worker ou provider bloqué reste visible/possédé. Ni tous les callbacks UI/SDK, ni les threads inactifs du Hub après seul arrêt Core ne sont prouvés éteints.
- **B, durée de vie d'un service : démontrée pour les accès sous bail testés.** Un worker bloqué accède réellement au service avant fermeture ; fermeture exactement une fois après sortie effective et drain.
- **C, refus d'activité nouvelle : démontré pour les voies testées.** Submit Dashboard, protocole et création/redémarrage TaskManager refusés ; les accès synchrones/caches non protégés restent une réserve.
- **D, répétition / erreurs / timeout / worker déjà terminé : couvert pour les scénarios des tests Core et TaskManager, pas universellement.** Nettoyage différé et répétition testés ; si un callback de `ServiceRegistry.close` échoue, le registre est déjà vidé et un nouveau retry automatique de ce callback n'est pas garanti.

## Context et limites fonctionnelles

Le Core crée un ContextEngine pour sa session et injecte ses dépendances : les snapshots de conversation sont éphémères et distincts de la mémoire persistante SQLite. Les tests ciblés couvrent la construction, l'identité et les scénarios ; un contexte autonome explicite peut exister hors Core. Les messages/souvenirs collectés restent des données non fiables, la mémoire est optionnelle et les instructions restent vides. Le budget UTF-8/4 est une estimation, pas un comptage garanti côté API ; les traces ne couvrent pas les éléments non collectés au-delà des limites. Ces garanties ne constituent pas une autorisation de sécurité indépendante.

## UI synchrone et externes

| Consommateur | Service et propriété | Durée / arrêt / risque | Preuve |
|---|---|---|---|
| Dashboard conversation | instance de conversation enregistrée Core, référencée par Hub | snapshot et nouvelle conversation sous bail, pas de doublon par fallback si Core injecté | identité, lecture bloquée et refus tardif, QtCore réel |
| Dashboard tâches | TaskManager du registre, cache Hub et TasksService | `tasks_available` refuse après fermeture ; appels de jobs suivis ; références déjà récupérées restent à examiner | identité Core/TasksService/routine/protocole, worker TaskManager réel |
| Orb mémoire | mémoire enregistrée Core | `_service` refuse si boundary fermée ; toggle détient bail ; autres appels récupérés antérieurement non tous protégés | vérification statique ; QtWidgets indisponible |
| Dialogues routines / modes / tâches | managers injectés, dialogues singleton UI | rebind/reconstruction selon cas ; une routine déjà lancée n'est pas annulée par fermeture du dialogue ; caches et callbacks tardifs pas entièrement prouvés | QtWidgets non validé dans cet environnement |
| Orb / Desktop / tray | Core injecté lorsqu'il existe ; fallback autonome historique | vie de l'interface distincte de Core, attention aux accès synchrones | test graphique d'identité bloqué par QtWidgets |

| Bibliothèque / système | Contrôle Jarvis | Limite |
|---|---|---|
| Python asyncio / threads / futures | baux, signal, annulation, jointure de threads Python connus ; `to_thread` conserve le bail réel | annuler une future n'interrompt pas de force le thread |
| PySide6 / Qt | objets UI et arrêt explicite de timers/hub ; signaux en file | event loop et bibliothèques natives, callbacks tardifs et QtWidgets non entièrement validés |
| Google GenAI / réseau | provider et client TaskManager fermés via leurs APIs | internals réseau/threads et Gemini réel non inspectés ni exécutés |
| PortAudio / sounddevice / ONNX | signal/arrêt wrapper AudioIO et jointure wake thread ; tests logiciels antérieurs | pilotes, callbacks natifs, modèle ONNX et micro/haut-parleur non validés ici ; pas de réécriture audio |
| psutil | thread sampler local, arrêt borné | appel natif bloqué ne peut être interrompu de force ; reprise automatique limitée |
| Windows notifications / OS | wrappers et désabonnements existants | worker et environnement Windows non testés dans cette mission |

## Résultats exécutés

- **Ciblés sur le commit de code** : `QT_QPA_PLATFORM=offscreen JARVIS_NO_MODEL_DOWNLOAD=1 python -m pytest -q` avec `test_core_activity_boundary`, `test_final_boundary_ui_lifecycle`, `test_final_task_boundary_integration`, `test_core_task_start_stop`, `test_background_tasks`, `test_protocols`, `test_dashboard_resources`, `test_core_runtime`, `test_core_runtime_tool_bindings`, `test_core_dashboard_runtime_events`, `test_context_engine`, `test_context_scenarios`, `test_conversation_context`, `test_live_context_harness`, `test_voice_runtime` : **251 passed in 32.30s** après synchronisation du test de course. Celui-ci a également réussi 15 exécutions isolées sur 15. Exécution avec `/home/user/.venv-jarvis/bin/python` et fichiers `tests/*.py` correspondants.
- **Qt distinct** : 6 passed, 1 failed, 4 collection errors en 6.78 s. Le test Orb/Desktop/Dashboard échoue et quatre modules QtWidgets/QtGui/QtTest ne se collectent pas à cause de `ImportError: libGL.so.1`. `ldd` signale aussi `libEGL.so.1`, `libxkbcommon.so.0`, `libdbus-1.so.3` manquantes ; `Xvfb` absent. Aucune transformation en skip.
- **Suite complète finale** : `QT_QPA_PLATFORM=offscreen JARVIS_NO_MODEL_DOWNLOAD=1 python -m pytest -q -rs --continue-on-collection-errors --disable-warnings` : **16 failed, 1501 passed, 114 skipped, 21 errors, 561 subtests passed en 531,80 s**. Les 37 traces d'échec/erreur indiquent `ImportError: libGL.so.1`. Les skips proviennent des tests existants ; aucun n'a été ajouté. Le code applicatif et les tests étaient figés à `1a0eb10` pendant cette exécution.
- Première suite complète : 17 failed, 1500 passed, 114 skipped, 21 errors, 561 subtests passed en 527,87 s. Le 17e échec a révélé une course dans le nouveau test ready/run : le thread appelant pouvait terminer avant la demande de stop. Le test a été synchronisé sans retirer l'assertion puis relancé ; le code applicatif n'a pas changé.
- Environnement : Linux, Python 3.11.2, PySide6 6.11.3, google-genai 2.34.0, pytest 9.2.0, psutil 7.2.2, sounddevice 0.5.6. `pip check` propre. Le mode offscreen ne remplace pas les bibliothèques natives manquantes.
- Benchmarks historiques sur doubles Linux (mission précédente, pas mesure G5400) : 20 cycles, construction 0,234 ms, démarrage 24,798 ms, arrêt 1,750 ms ; 100 000 baux 1,057 s mur sous tracemalloc. Ces chiffres ne prouvent ni consommation native ni performance matérielle réelle.

Pas d'exécution Windows, Gemini réseau ou matériel audio ; les CI Windows anciennes concernent des commits antérieurs et ne certifient pas celui-ci. La tentative précédente de déclenchement Actions a reçu HTTP 403 ; aucune permission n'a été contournée. Aucun travail du lot 02 ni moteur ultérieur n'a été entrepris.

## Reste à traiter avant une clôture architecturale globale

1. Cartographier puis couvrir les accès GUI synchrones et callbacks/timers tardifs utilisant une référence mise en cache : bail couvrant l'utilisation réelle, rejet après fermeture, test d'identité et de durée de vie, sans bloquer la boucle Qt. Tester les dialogues conservés et la réinjection entre sessions.
2. Garantir ou documenter la coordination des remplacements concurrents de globals empruntés ; imposer pour les factories injectées un objet non démarré ou un adaptateur qui prouve l'injection préalable, sans casser les usages autonomes légitimes.
3. Définir la fin du cycle Hub/executor et vérifier les vrais threads après fermeture complète Hub + Core, y compris jobs en attente ou bloqués ; ne pas assimiler thread idle et worker actif sur un service, mais ne pas affirmer leur disparition sans mesure.
4. Préciser le contrat de timeout/erreur du provider et la reprise des échecs de cleanup sans détruire de services encore utilisés ; tester le sampler avec retour d'abonné pendant blocage et callbacks déjà engagés.
5. Exécuter les tests QtWidgets après installation de dépendances système et environnement graphique adapté, puis validation Windows 11/G5400, Gemini réel et audio physique avec configuration et autorisations appropriées. Ne pas considérer les CI historiques comme une validation de ce commit.

## Classification individuelle des échecs — reprise `91be093`

Chaque entrée ci-dessous est classée **3 — problème d’environnement** : `ImportError: libGL.so.1`, constaté dans sa trace. Ces erreurs empêchent l’exécution du test concerné ; elles ne prouvent ni correction ni défaut du code QtWidgets. Aucun autre échec (catégories 1, 2, 4 ou 5) n’a été observé dans cette exécution. Les 114 skips sont ceux prévus par la suite, non ajoutés pour cette mission.

| Type | Test ou module | Classification |
|---|---|---|
| Collecte | `tests/test_blob_visibility.py` | 3 — environnement : libGL |
| Collecte | `tests/test_dashboard_conversation_widget.py` | 3 — environnement : libGL |
| Collecte | `tests/test_dashboard_entry_points.py` | 3 — environnement : libGL |
| Collecte | `tests/test_dashboard_perf.py` | 3 — environnement : libGL |
| Collecte | `tests/test_dashboard_productivity.py` | 3 — environnement : libGL |
| Collecte | `tests/test_dashboard_tasks.py` | 3 — environnement : libGL |
| Collecte | `tests/test_dashboard_widgets.py` | 3 — environnement : libGL |
| Collecte | `tests/test_dashboard_window.py` | 3 — environnement : libGL |
| Collecte | `tests/test_interruption_ui.py` | 3 — environnement : libGL |
| Collecte | `tests/test_item_bg_opacity.py` | 3 — environnement : libGL |
| Collecte | `tests/test_menu_backgrounds.py` | 3 — environnement : libGL |
| Collecte | `tests/test_menu_hover.py` | 3 — environnement : libGL |
| Collecte | `tests/test_menu_integration.py` | 3 — environnement : libGL |
| Collecte | `tests/test_menu_layout.py` | 3 — environnement : libGL |
| Collecte | `tests/test_menu_render_offscreen.py` | 3 — environnement : libGL |
| Collecte | `tests/test_menu_ux.py` | 3 — environnement : libGL |
| Collecte | `tests/test_mode_apps_dialog.py` | 3 — environnement : libGL |
| Collecte | `tests/test_orb_visibility.py` | 3 — environnement : libGL |
| Collecte | `tests/test_protocol_ui.py` | 3 — environnement : libGL |
| Collecte | `tests/test_routines_ui.py` | 3 — environnement : libGL |
| Collecte | `tests/test_ui_menu_regression.py` | 3 — environnement : libGL |
| Échec | `test_presets_only_reference_registered_widgets` | 3 — environnement : libGL |
| Échec | `test_hub_routes_real_task_hooks_and_bridge_filter_avoids_legacy_duplicate` | 3 — environnement : libGL |
| Échec | `test_notification_bridge_filter_suppresses_display_only` | 3 — environnement : libGL |
| Échec | `test_every_widget_action_has_a_policy` | 3 — environnement : libGL |
| Échec | `AudioFailureNotificationTests.test_non_audio_engine_failure_is_still_notified` | 3 — environnement : libGL |
| Échec | `AudioFailureNotificationTests.test_one_audio_failure_raises_one_notification` | 3 — environnement : libGL |
| Échec | `AppearanceStateIntegrationTests.test_desktop_block_is_persisted_and_restored` | 3 — environnement : libGL |
| Échec | `AppearanceStateIntegrationTests.test_no_second_config_file_is_created` | 3 — environnement : libGL |
| Échec | `AppearanceStateIntegrationTests.test_saving_a_v160_state_adds_the_block` | 3 — environnement : libGL |
| Échec | `AppearanceStateIntegrationTests.test_survives_multiple_save_load_cycles` | 3 — environnement : libGL |
| Échec | `AppearanceStateIntegrationTests.test_unknown_future_keys_do_not_break_loading` | 3 — environnement : libGL |
| Échec | `AppearanceStateIntegrationTests.test_v160_file_still_loads_with_defaults` | 3 — environnement : libGL |
| Échec | `UiWiringTests.test_stop_handler_empties_output_then_marks_a_manual_stop` | 3 — environnement : libGL |
| Échec | `test_void_methods_do_not_leak_none_references` | 3 — environnement : libGL |
| Échec | `test_orb_desktop_dashboard_share_injected_core` | 3 — environnement : libGL |
| Échec | `test_lag_probe_charges_each_gap_to_the_phase_where_it_is_observed` | 3 — environnement : libGL |
