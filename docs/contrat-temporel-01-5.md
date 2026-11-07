# Reprise temporelle 01.5 — lot ouvert

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
