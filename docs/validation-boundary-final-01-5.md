# Validation de clôture — Runtime Boundary 01.5

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
