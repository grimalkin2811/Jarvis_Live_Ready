# Reprise temporelle 01.5 — lot ouvert


## Mission finale sur `819c796` — verdict courant

**ARCHITECTURAL: PARTIAL ; ENVIRONMENTAL: PARTIAL ; LOT 01.5: OPEN.** Cette section prime sur tout historique ci-dessous ; les contre-exemples restants sont des blocages architecturaux, pas seulement des limitations Qt.

Préflight : branche `arena/8edade8b-jarvis-live-ready`, remote `origin` du dépôt attendu, base distante `819c7967a687b19c636d1a67db4a156b746c0549`, `main` distante `ec944e26457b22492b5872ee520d6bb3ce410081`. Le snapshot avait remis ref/index sur `ec944e` tandis que les fichiers correspondaient à `819c796` : objet fetché, index temporaire `/tmp/jarvis-preflight-819c.RhUYiD`, comparaison sans différence suivie hors `.gitignore` et sans autre fichier non suivi ; avance rapide de ref/index sans toucher aux fichiers. `.gitignore` local seul antérieur, SHA-256 `dd211e71f5d97f965505729839e147c895cec7409d786f3bd33f9dc94b11abce`, préservé et exclu du commit. SHA final : vérifiable par `git rev-parse HEAD`, communiqué après le commit (aucune auto-référence de hash).

| Réserve | Verdict courant | Raisons et portée |
|---|---|---|
| #1 Orb / routines | **NOT_VALIDATED** | Chemins injectés déjà gardés ; tests de corps AST et absence de QtWidgets ne démontrent ni tous les callbacks ni les usages standalone. |
| #2 Protocoles / jobs / workers | **BLOCKING** | Gate de constructions synchrones et soumission Context renforcés ; contre-exemple réel d'un thread `asyncio.to_thread` de cleanup créé après STOPPING. Gemini et autres créations indirectes restent à couvrir. |
| #3 Hub | **BLOCKING** | Gate de submit et convergence des threads connus testés ; autres verrous/hooks et concurrence QObject non démontrés. |
| #4 UI / hooks / dialogues | **NOT_VALIDATED** | Callback tardif de vérification de mise à jour protégé, test AST ; inventaire des signaux/QTimers non exhaustif, QtWidgets non exécuté. |
| #5 Factories / singletons | **BLOCKING** | La factory synchrone déjà entrée ne franchit plus STOPPING ; une factory custom peut déléguer à un thread externe qui crée ensuite un nouveau worker après STOPPED. Elle reste acceptée et n'est pas certifiée ni refusée. |
| #6 Cleanup / providers | **BLOCKING** | États provider/registre désormais observables ; blocage indéfini, échec et workers SDK externes ne sont pas prouvés terminés ; owner peut être libéré après certaines erreurs, et cleanup peut créer un worker après STOPPING. |

### Contrat effectivement implémenté

`ActivityBoundary.create()` refuse une factory si l'intention est publiée et compte les constructions admises jusqu'au retour (ou exception). Le code de factory tourne **hors verrou de condition**. `reject_new()` publie d'abord l'intention, puis attend le retour de toutes ces constructions et de tout `launch()` déjà dans le gate avant d'écrire `_closing` : c'est le point effectif `STOPPING`. Une construction admise peut créer entre demande et transition, pas pendant ni après cette transition **tant que sa création est synchrone dans l'appel**. Les factories memory, set-memory, conversation, scheduler, tasks, audio, provider et `audio.start()` du pipeline Core passent par `create`. Un appel d'arrêt depuis le même thread d'une factory publie l'intention et revient pour éviter de s'attendre soi-même ; l'étape suivante ou la sortie d'`audio.start` fait entrer le cleanup. `reject_new()` direct depuis une factory est refusé par exception plutôt que deadlocker.

Une factory bloquée peut laisser STOP_REQUESTED indéfiniment et bloquer `request_stop()` ; un thread externe qui demande l'arrêt et qu'une factory attend peut également former un cycle. `drain(timeout)` attend d'abord la fin des constructions sans limite : son timeout ne borne donc pas cette attente. La garantie ne couvre pas les threads déjà existants, une factory qui délègue à d'autres threads, un singleton partagé ou un SDK qui crée des ressources après son retour. Ces intégrations ne sont **pas** proprement refusées actuellement : #5 reste bloquante.

`ContextEngine.build_async` soumet maintenant directement l'executor sous `launch` au lieu de planifier une coroutine `to_thread` susceptible de ne démarrer qu'après STOPPING. La copie `contextvars` et le shield de la future préservent la propagation de contexte et l'attente sans annuler le worker. Un worker déjà créé qui n'a pas encore acquis son bail `context.build` est refusé après la demande ; le verrou mono-build est libéré en cas de refus/exception.

`CoreRuntime.cleanup_status()` expose séparément `provider` et `registry` : `CLEANUP_PENDING`, `CLEANUP_RUNNING`, `CLEANUP_DONE`, `CLEANUP_FAILED`. Un provider bloqué reste `CLEANUP_RUNNING` : on ne peut pas déduire `CLEANUP_BLOCKED` d'un délai arbitraire et il n'existe pas de terminaison forcée garantie. Ces états portent sur le retour des callbacks/coroutines, **pas** sur tous les threads du SDK. Le registre conserve ses échecs, ne rejoue pas les callbacks ; une interruption `BaseException` est propagée et mémorisée comme échec (les callbacks suivants ne sont alors pas exécutés). La distinction Hub `shutdown(False)`/`shutdown(True)` et ses limites sont décrites dans le contrat historique ci-dessous : le premier n'attend pas volontairement la convergence mais peut encore attendre d'autres verrous/hooks, le second ne borne que ses joins, pas tous les verrous.

### Preuves et contournements forcés

- `test_custom_scheduler_factory_creation_precedes_core_stopping` : factory en attente, demande d'arrêt sur autre vrai thread, `STOP_REQUESTED`, création effective du worker, retour de factory, puis `STOPPING`. Le worker actif est observé et la convergence différée est vérifiée. L'ancien test qui constatait la création *dans* STOPPING a été adapté au nouveau contrat ; il ne prouve pas les créations déléguées.
- `test_multiple_admitted_factories_delay_stopping_and_late_factory_is_refused` : deux vraies factories simultanées ; aucune transition après la libération d'une seule, refus d'une troisième, transition après la seconde ; exception et libération du compteur également testées.
- `test_external_factory_can_delegate_post_stop_creation_to_untracked_thread` : vraie factory acceptée démarrant un dispatcher, arrêt complet Core, puis dispatcher externe crée réellement un worker en phase STOPPING. Ce test vert **reproduit une violation**, il ne prouve pas sa correction.
- `test_runtime_cleanup_can_start_asyncio_executor_worker_after_stopping` : un executor asyncio neuf observe un vrai `Thread.start()` de cleanup en phase STOPPING ; le worker est joint en fin de scénario. C'est une seconde violation de la garantie globale ; les opérations `asyncio.to_thread` de cleanup et le waiter différé doivent être repensés sans simplement prétendre qu'ils ne sont pas des workers.
- `test_context_executor_start_inflight_completes_before_stopping` force `submit` bloqué, demande, vraie création et transition ; `test_async_context_refuses_before_executor_submission_after_stop` observe un executor sans thread ; propagation `contextvars` testée.
- `test_registry_cleanup_states.py` observe succès, concurrent, attente, exception, répétition et interruption, plus coroutine provider bloquée/libérée et exception. Un état RUNNING n'est pas une preuve de fuite ni de convergence ; le provider partagé et les SDK réels restent non validés.
- `test_dashboard_update_late_callbacks.py` exécute les corps réels chargés par AST : après `_quitting`, le `singleShot` ne soumet rien et les callbacks déjà capturés ne touchent ni store ni logs ; pas une validation d'intégration Qt.
- Répétitions déterministes : 15/15 de 10 scénarios factory/gate et arrêt pendant `audio.start` (`/tmp/jarvis-closure3-repeat.log`), 15/15 des quatre tests de contournement factory/cleanup (`/tmp/jarvis-closure3-repeat-external-final.log`, avec assertions STOPPED et owner libéré ; y compris les violations reproduites) et 15/15 des trois tests Context (`/tmp/jarvis-closure3-context-repeat.log`). Events et vrais threads, pas `sleep` comme unique synchronisation.

### Inventaire orienté création

Recherche statique sur `Thread`, `Timer`, `ThreadPoolExecutor`, `QTimer`, `singleShot`, `run_in_executor`, `to_thread`, `create_task`, `ensure_future`, `call_soon_threadsafe` : 70 occurrences, relevé `/tmp/jarvis-closure3-worker-audit.txt`. Il ne couvre ni alias ni création cachée dans un SDK et ne constitue pas un inventaire exhaustif. Chemins principaux :

| Source → admission → création → propriétaire | Lifecycle et limite |
|---|---|
| Core `_run_pipeline` → `create` → factories / `audio.start` → Core ou objet externe | appel synchrone borné par STOPPING ; délégation tardive et worker préexistant non contrôlés |
| Context async → `launch` → executor par défaut → boucle asyncio | soumission gardée ; le pool partagé et les autres utilisateurs ne sont pas possédés par Context |
| protocoles / timer `src/tools.py` → boundary runtime → `Thread` / `Timer` → Core | tests ciblés existants ; compatibilité autonome hors Core non couverte |
| scheduler/tasks par défaut → gate de start → thread → singleton/Runtime | singleton préexistant et partage non certifiés |
| Orb / catalogue → `start_thread` si Core injecté, sinon standalone → thread | standalone, hooks et interface graphique non globalement prouvés |
| Dashboard → JobRunner gate → `ThreadPoolExecutor` ; ResourceMonitor → verrou propre → thread | ownership Hub limité ; intégration Qt et tous les hooks non prouvés |
| `runtime.py` cleanup → `asyncio.to_thread` / waiter → pool partagé / thread | création après STOPPING prouvée, **BLOCKING** |
| Gemini / context / notifications Windows / UI timer et `singleShot` | diverses APIs asyncio/Qt/externes ; pas toutes sous boundary ni toutes auditées |

Le Context Engine conserve son périmètre : snapshots immuables, budget et estimation, provenance/exclusions, sélection déterministe, données d'environnement uniquement explicites et mémoire opt-in ; aucune Memory Engine future ajoutée. Tests de scénario existants et nouveaux tests executor exécutés, mais cela ne suffit pas à clore le lifecycle.

Tests ciblés finaux Core, Context, Hub, UI alternative et nouvelles preuves : **105 passed in 31.80s** (`/tmp/jarvis-closure3-target-last.log`). `ruff check .`, `compileall -q src UI tests`, `pip check`, `git diff --check` hors `.gitignore` sont exécutés séparément. Les tentatives de suite globale démarrées pendant les modifications ont été interrompues volontairement, puis relancées sur le code stabilisé ; seul le résultat de cette dernière peut être comparé au bilan historique. Les assertions STOPPED et owner de la reproduction externe ont été ajoutées après collecte et vérifiées séparément, notamment dans les 15 répétitions finales. Suite globale sur code stabilisé : **1 535 passed, 561 subtests passed, 114 skipped, 16 failed, 21 errors in 540.49s** (`/tmp/jarvis-closure3-full-complete.log`). Les 37 traces contiennent `ImportError: libGL.so.1`, avec les mêmes 16 cas et 21 erreurs Qt que le bilan précédent. Le test de factory renforcé après collecte a été exécuté séparément 15 fois ; le code applicatif n'a pas changé depuis le lancement de la suite. QtWidgets reste bloqué par `libGL.so.1` ; Windows natif, matériel audio et Gemini réel ne sont pas testés.


---

**ARCHITECTURAL: PARTIAL ; ENVIRONMENTAL: PARTIAL.** Ce document et l'encadré en tête des deux rapports remplacent leurs conclusions antérieures. Le travail ci-dessous ne valide pas le lot 02 et ne démontre pas l'absence globale de création après STOPPING.

## Préflight et portée

Départ attendu et distant Arena : `93395aa28f44fd67aebcb306b193952277763b49`. Le snapshot présentait la référence et l'index sur `ec944e26457b22492b5872ee520d6bb3ce410081`, mais les fichiers suivis correspondaient au commit Arena hors `.gitignore` : comparaison à un index temporaire après fetch, aucun autre fichier non suivi, avance rapide contrôlée de la référence et de l'index sans toucher au worktree. `main` distante à `ec944e26457b22492b5872ee520d6bb3ce410081`. Branche Arena inchangée ; `.gitignore` local antérieur exclu des commits, SHA-256 `dd211e71f5d97f965505729839e147c895cec7409d786f3bd33f9dc94b11abce`. Le SHA final se lit par `git rev-parse HEAD` après commit et est communiqué séparément : ce rapport ne prétend pas contenir son propre hash.

## Contrat réellement implémenté

- `ActivityBoundary` expose `OPEN`, `STOP_REQUESTED`, `STOPPING`. `begin_stop` publie immédiatement l'intention par Event sans attendre le verrou ; admissions et lancements qui n'ont pas franchi le contrôle dans le gate sont refusés. `reject_new` attend un démarrage déjà entré dans le gate avant d'écrire `_closing` : cette écriture est la transition effective `STOPPING`. Un `Thread.start` ou `executor.submit` déjà entré peut donc *réellement* créer après la demande mais avant STOPPING. S'il bloque, `request_stop` peut bloquer ; il n'existe aucune promesse de délai absolu.
- Une activité déjà admise n'a pas automatiquement le droit de lancer après l'intention ; une activité/son callback déjà en cours peut continuer. `drain` mesure les seuls baux enregistrés. `QUIESCENT` (plus aucun bail/worker connu) et `STOPPED` (cleanup terminé) sont des notions de validation distinctes, **pas** des phases renvoyées par `ActivityBoundary.phase` ni une preuve de fermeture des SDK externes. Les workers et tâches qui ne prennent pas de bail ne sont pas couverts.
- Démarrage Scheduler par défaut sous gate ; `TaskManager` par défaut contrôle le vrai `Thread.start` séparément de son attente de disponibilité. La compatibilité `start()` reste inchangée. Une factory personnalisée, un singleton déjà en marche, audio/provider et les appels autonomes ne sont pas devenus contrôlés pour autant.
- Hub : `JobRunner.request_shutdown` publie un Event sans attendre le verrou de soumission ; le dernier détenteur exécute la fermeture différée et revérifie après déverrouillage pour couvrir la fenêtre check/unlock. `JobRunner.shutdown` conserve sa compatibilité bloquante. Le Hub appelle la nouvelle API avant de détacher ses hooks ; `shutdown_status` renvoie prudemment incomplet si le verrou des jobs est occupé. Un submit déjà entré peut créer son thread après le retour de `shutdown(False)` mais avant la fermeture effective de l'executor (`jobs.shutdown_requested()`). Les callbacks déjà entrés comptent jusqu'à leur retour ; les threads executor et sampler sont inspectés. Le retour et `requested` ne prouvent pas la convergence, et `complete` n'inclut ni SDK ni tous les signaux Qt.
- **Limite importante** : `shutdown(False)` n'attend plus *le verrou de submit*, mais exécute encore des débranchements et `ResourceMonitor.stop(0)` synchrones pouvant prendre d'autres verrous ; il n'est pas prouvé non bloquant face à un hook ou verrou externe. `blocking=True` borne les joins, pas nécessairement l'attente initiale d'un verrou. Les méthodes QObject du Hub doivent être appelées sur le thread Qt ; aucune garantie de simultanéité arbitraire de `start`/`shutdown` Qt n'est déclarée.

## Scénarios concurrents et observations

Les tests utilisent des Events et de vrais threads ; les délais servent de garde anti-blocage, non d'unique preuve d'ordre.

| Scénario | Test / observation | Classification |
|---|---|---|
| lancement déjà dans gate, demande, création, transition | `test_stop_intent_rejects_new_admission_while_prior_start_finishes` : ordre enregistré `stop_requested < thread_started < stopping`, worker vivant réellement inspecté puis joint ; admissions et second lancement refusés pendant STOP_REQUESTED et STOPPING | **PROVEN** pour ce gate, non pour toute factory |
| admission/start puis arrêt avant création | `test_task_manager_stop_between_start_and_actual_thread_creation` : Thread créé en mémoire mais `ident is None`, stop, reprise, refus, toujours aucun thread OS ; cas direct après intention également testé | **PROVEN** pour TaskManager par défaut utilisant le gate |
| submit Hub entré puis shutdown non bloquant | `test_hub_nonblocking_request_does_not_wait_for_inflight_executor_submit` : retour avant libération, refus suivant, fermeture executor différée, vrai worker rejoint et état complet | **PROVEN** pour le verrou de submit seul |
| intention entre test sous verrou et unlock | `test_runner_deferred_stop_survives_intent_between_check_and_unlock` force précisément cette fenêtre ; fermeture finale et refus vérifiés | **PROVEN** pour cet interleaving |
| factory scheduler déjà entrée, arrêt, création après STOPPING | `test_custom_scheduler_factory_can_create_worker_after_core_stopping` force la factory à reprendre après `STOPPING` : vrai thread créé, worker toujours vivant après retour du runtime, cleanup différé et owner retenu ; libération explicite dans le test | **BLOCKING**, reproduction d'une violation ; une réussite du test prouve la présence de la limite, pas sa résolution |
| shutdown et callbacks / provider | tests `test_final_hub_shutdown_admission.py`, `test_final_boundary_ui_lifecycle.py` et `test_cleanup_failure_visibility.py` : worker/sampler bloqués, callback déjà entré, registry en cours/échoué et provider bloqué, puis libération et observation ; portée et limites ci-dessous | **PARTIALLY_PROVEN** |
| scénarios A/B/C pour *tous* protocoles, routines, UI et callbacks SDK | tests existants couvrent certains chemins de protocoles/jobs, pas les SDK arbitraires ni toutes les fermetures/singletons | **BLOCKING** globalement |

Les 17 tests de `test_temporal_shutdown_contract.py` (cinq tests à ce moment), `test_worker_launch_boundary.py`, `test_final_hub_shutdown_admission.py`, `test_cleanup_failure_visibility.py` ont été exécutés **15 fois sur 15** (`/tmp/jarvis-temporal-repeat-stable.log`). Le sixième test de fenêtre check/unlock a également réussi **15 répétitions sur 15** (`/tmp/jarvis-temporal-gap-repeat.log`). Pendant la mise au point, une répétition précédente s'est arrêtée sur une assertion incorrecte du nouveau test de factory ; elle a été corrigée pour constater honnêtement le worker encore vivant, pas masquée par un skip. La suite ciblée Core/TaskManager/Hub : **24 passed** (`/tmp/jarvis-temporal-regressions.log`, avant ajout des deux derniers tests de caractérisation).

## Classification des ressources/factories

Ces étiquettes décrivent les intentions et risques, non une validation d'un contrat imposé aux objets arbitraires.

| Ressource | Classification et contrôle | Limite concrète |
|---|---|---|
| ActivityBoundary, RuntimeTimers, JobRunner, sampler Hub | **OWNED**, contrôles propres ; JobRunner et sampler ont des threads inspectés | événements Qt en file et autres ressources non comptés ; sampler a son propre verrou |
| TaskManager par défaut | **OWNED** pour le runtime lié, mais singleton historique partagé ; démarrage du thread sous gate | un worker existant avant injection et l'usage simultané d'un autre propriétaire ne sont pas prouvés |
| Scheduler par défaut | **BORROWED/PRESTARTED possibles**, getter singleton ; Core le lie, le démarre sous gate et demande son arrêt | si déjà actif avant l'injection, exécution antérieure incontrôlée ; Core peut arrêter une instance utilisée ailleurs |
| Factories scheduler/tasks personnalisées | **UNSUPPORTED** pour la garantie stricte, pas refusées actuellement pour compatibilité | elles peuvent démarrer avant injection ou après STOPPING ; test reproductible ci-dessus. **Ni refus effectif ni ownership exclusif établi** |
| audio/provider custom ou SDK réel | **EXTERNAL/UNSUPPORTED** pour les threads internes ; construction et cleanup confiés à leurs interfaces | factory et SDK peuvent créer d'autres threads, construire ou agir après la frontière ; aucune introspection exhaustive ni arrêt forcé |
| executor externe/singleton partagé | **EXTERNAL/BORROWED/UNSUPPORTED** | pas de contrat de propriété vérifié ni de test exhaustif de partage ; ne pas appeler sa fermeture arbitrairement |
| todo/routines/modes/activity/music lazy et conversation | **BORROWED**, getters historiques et identités partagées | mutation tardive, propriétaire et isolation inter-consommateurs à prouver |
| `asyncio.to_thread`, notifications Windows, UI autonome | **EXTERNAL** ou lifecycle séparé | annulation ne tue pas un thread ; notification Windows/QTimers/signaux ne relèvent pas tous du gate |

L'inventaire statique (`rg` sur `Thread`, `Timer`, `ThreadPoolExecutor`, `QTimer`) identifie également `UI/notification_bridge.py`, `UI/jarvis_menu.py`, `UI/routines_dialog.py`, `UI/dashboard/{window,widget_api,pages_view,grid_view,controller,widgets}`, `UI/desktop/overlay.py`, `UI/boot_sequence.py`, `src/ui.py`, `src/notifications.py`, `src/audio.py`. Les branches standalone Orb/catalogue ne prennent pas nécessairement un bail Core ; timers, `singleShot`, signaux en file, closures capturant services et hooks dialogues restent à auditer sous Qt réel. L'inventaire textuel ne prouve pas tous les appels indirects.

## Nettoyage et frontières de preuve

| Owner / ressource | Opération et visibilité | Risque |
|---|---|---|
| Core / provider | `close` et `shutdown` async ; STOPPING pendant attente ; exception journalisée, FAILED | blocage indéfini retient owner et run ; état d'un SDK externe inconnu ; pas de timeout universel sûr |
| Registry / audio, scheduler, tasks | callbacks synchrones inversés, au plus une fois ; `cleanup_in_progress` en concurrence ; échecs conservés | callback bloquant ou échoué ; aucun retry implicite ; owner parfois libéré après échec malgré état SDK inconnu |
| Scheduler/tasks/audio/timers suivis | demande puis joins bornés ; dépassement conserve owner et lance un waiter de convergence | si worker ne reçoit pas une demande efficace, conservation indéfinie ; le test de factory montre un cycle où `stop` est différé jusqu'à la sortie du worker |
| Hub / executor/sampler | `request_shutdown`, annulation des jobs en attente, `stop(0)` ; `shutdown_status` inspecte threads et callbacks JobRunner | pas de preuve de tous hooks Qt ni de tous verrous ; externalité non comptée |
| services empruntés | pas de callback registry générique | fermeture, partage et fuites relèvent de leur propriétaire externe ; non prouvé |

## Réserves restant ouvertes

| # | Verdict | Motif |
|---|---|---|
| 1 | **NOT_VALIDATED** | Orb/routines : AST sans QtWidgets ne prouve pas signal/fermeture graphique et tous chemins |
| 2 | **BLOCKING** | gate défini et tests ciblés renforcés, mais factory custom crée un vrai thread après STOPPING ; audio/provider et autres chemins non contrôlés |
| 3 | **BLOCKING** | course du verrou de submit traitée ; autres verrous/hooks, concurrence Qt et callbacks externes non prouvés |
| 4 | **NOT_VALIDATED** | inventaire UI/timers/hooks incomplet ; intégration QtWidgets impossible ici |
| 5 | **BLOCKING** | factory pré-démarrée/partagée et executor externe ni intégrés ni effectivement refusés |
| 6 | **BLOCKING** | cleanup provider/callback bloqué ou échoué et propriété externe non résolus |

QtWidgets échoue à l'import (`libGL.so.1` absent) ; QtCore ne le remplace pas. Windows natif, matériel audio, API Gemini réelle et intégration GUI réelle ne sont pas validés. Les résultats historiques de suite complète figurent dans le rapport historique ; tout nouveau résultat est communiqué avec son état exact, sans transformer les erreurs en skips.

Contrôle complémentaire Core et Dashboard (`tests/test_core*.py`, `tests/test_dashboard*.py`, nouveaux tests et tests ciblés, avec `--continue-on-collection-errors`) : **162 passed, 6 failed, 7 errors** ; les six échecs et sept erreurs affichent tous `ImportError: libGL.so.1` (`/tmp/jarvis-temporal-target.log`). Ces résultats ne prouvent pas QtWidgets. `ruff check .`, `compileall -q src UI tests`, `pip check` et `git diff --check` hors `.gitignore` ont réussi.

Ciblage final après ajout de tous les tests : **39 passed in 22.21s** (`/tmp/jarvis-temporal-target-green.log`) : fichier temporel complet, gate worker, Hub, cleanup, démarrage TaskManager, boundary Core, cycle UI alternatif et routines AST. Cela n'étend pas la preuve au-delà des scénarios indiqués.

Suite complète Linux (`QT_QPA_PLATFORM=offscreen JARVIS_NO_MODEL_DOWNLOAD=1 python -m pytest -q --continue-on-collection-errors`) : **1 519 passed, 561 subtests passed, 114 skipped, 16 failed, 21 errors in 542.87s** (`/tmp/jarvis-temporal-full.log`). Les 37 traces d'échec/erreur contiennent `ImportError: libGL.so.1` ; la liste correspond aux mêmes cas Qt que la suite historique. La collecte de cette suite a eu lieu pendant l'ajout du fichier de tests : trois tests temporels étaient présents à sa collecte ; les trois suivants ont été exécutés séparément dans le ciblage final à 39 tests. Cette suite n'est donc pas une validation Qt ni une exécution complète du fichier temporel final.
