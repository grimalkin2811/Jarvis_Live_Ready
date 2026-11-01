# Lot 01.5 — runtime et fondations du contexte

## État et périmètre

Ce lot apporte une **migration partielle**, pas une unification complète de tous les consommateurs historiques. Aucun fournisseur ne reçoit les snapshots du nouvel engine ; aucun Security Engine, orchestrateur, plugin, workflow ou nouveau système de mémoire n'est introduit. La mémoire de conversation reste éphémère. Les garde-fous provisoires documentés dans `validation-post-lots-00-01.md` ne sont pas une autorisation générale.

## Inventaire des états partagés et responsabilités

| État / singleton | Propriétaire ou rôle actuel | Situation après ce lot |
|---|---|---|
| `CoreRuntime._owner` | garde de l'unique runtime actif, par processus | conservé ; pas une garantie interprocessus |
| `runtime_status.RUNTIME` | tableau d'état global et entrée texte | enregistré comme `status`, injecté au hub ; demeure un global pour compatibilité |
| `conversation._DEFAULT` | contexte éphémère, écouteurs | créé/récupéré par les factories du Core, enregistré `conversation` ; Dashboard injecté utilise le registre |
| `memory._DEFAULT_MANAGER` | mémoire durable SQLite | le Core construit, enregistre et publie l'instance existante ; Dashboard et provider partagent l'instance ; accès historique aux getters demeure |
| `background_tasks.manager._DEFAULT` | gestionnaire de tâches et worker | configuré par Core, enregistré `tasks`, arrêté par Core ; Dashboard attaché par événement |
| `scheduler._DEFAULT_SCHEDULER` | planificateur/worker | créé puis arrêté par Core, enregistré `scheduler` ; consommateurs historiques conservés |
| `todo._DEFAULT_MANAGER`, `routines._DEFAULT_MANAGER`, `modes._DEFAULT_MANAGER` | gestionnaires historiques, stockage et état | résolus paresseusement comme services empruntés `todo`, `routines`, `modes` ; le Core ne les détruit pas ; le Dashboard injecté les résout par le registre |
| `activity._DEFAULT_LOG`, `music.manager._DEFAULT` | journal d'activité, musique | conservés, **non migrés** dans le registre ; appelants historiques inchangés |
| `menu_state.LIVE` | commandes micro, voix et interruption | pont historique partagé Core/UI, non remplacé ; pas un second moteur |
| `dashboard_bridge.DASHBOARD`, `interface_mode_bridge.INTERFACE_MODE`, `desktop.events.DESKTOP_EVENTS`, `visibility_bridge.VISIBILITY` | ponts d'interface / handlers | conservés pour les interfaces ; détachement historique au shutdown, non enregistrés dans Core |
| `notifications._HOOKS`, queue et worker Windows ; `protocols._ACTIVE_RUN` et écouteurs ; `tools._TIMERS` ; `system_extras._HISTORY` ; registre de fonctions des routines ; `writing.service._writer_override` | états et bus de modules existants | conservés, pas convertis en services ; leur cycle de vie et autorisation restent à auditer |
| `UI.routines_dialog`, `mode_apps_dialog`, `desktop_appearance_dialog`, `background_tasks_dialog` : `_DIALOG` | fenêtres uniques et leur cycle de vie Qt | conservées dans la couche interface, non services métier |
| tables de constantes, déclarations d'outils et caches de configuration/polices des modules | données de module, pas des instances de runtime | conservées ; pas de migration artificielle |

Les `get_default_*` existent encore notamment dans `src.tools`, menus Orb/Desktop, dialogues, routines, backup et ponts historiques. Les façades Dashboard gardent un mode autonome compatible sans Core pour les anciens tests/appelants. En production graphique, `run_ui` crée le Core avant le Dashboard et passe **le même objet** au thread vocal. Le hub utilise `status`, `conversation`, `memory`, `tasks` et `todo` du registre ; les actions Dashboard utilisent `conversation`, `routines` et `modes`. L'annonce de disponibilité des services remplace le polling de démarrage pour ce chemin. La compatibilité hors Core conserve l'ancien timer.

Limites importantes : d'autres interfaces et des outils accèdent toujours aux globals ; la migration de tous leurs consommateurs n'est donc pas terminée. Le registre met en cache les services empruntés ; remplacer un singleton par son setter pendant un runtime actif pourrait rompre l'identité partagée et n'est pas pris en charge. La fermeture du registre ne détruit pas les singletons empruntés. Les tests QtCore vérifient le hub et le pipeline factice, pas les fenêtres Orb/Desktop/Dashboard complètes. Il ne faut pas affirmer l'unicité de tous les objets dans toutes les interfaces sur cette seule base.

Le registre est synchronisé ; doublon, service absent, type incorrect, registre fermé et dépendance paresseuse cyclique sont signalés. Les fabriques paresseuses sont évaluées au plus une fois en cas de succès. Nettoyage LIFO, idempotent et journalisé. Le runtime diffuse des événements de disponibilité, le hub s'en désabonne à l'arrêt ; les événements Qt en attente sont ignorés après fermeture.

## Context Engine

`src.core.context.ContextEngine` est enregistré sous `context` dès construction de `CoreRuntime`, sans import Qt ou fournisseur. Son API `build(ContextRequest)` retourne des dataclasses gelées contenant exclusivement tuples et chaînes : éléments, source et référence, estimation, troncature, exclusions, sources manquantes et budget. Les champs d'instructions sont séparés et toujours vides ; **chaque élément est une donnée non fiable**. Aucun assemblage prompt/modèle n'est effectué. Ne pas interpréter un souvenir ou un message comme instruction système, ni journaliser aveuglément le contenu du snapshot.

Sélection stable pour des sources identiques : requête, jusqu'à 30 messages récents (plus récent d'abord), jusqu'à 10 souvenirs renvoyés par la recherche, projet fourni par l'appelant, champs runtime sur liste blanche et environnement explicitement fourni. Le nombre de candidats utilisés est limité à 40 ; chaque texte est plafonné à 8 000 caractères et le budget à 16 000 unités. La troncature et les exclusions sont enregistrées sans recopier leurs contenus. Estimation grossière : `ceil(octets UTF-8 / 4)` par élément, sans coût des métadonnées. **Ce n'est pas un tokenizer ni une garantie de limite API** ; les sources peuvent évoluer entre deux appels, notamment pendant une conversation ou une recherche SQLite.

La mémoire durable n'est interrogée que si `memory=True`, avec `mark_accessed=False` et une requête bornée : elle conserve son propriétaire et ses règles. Un projet est une donnée fournie explicitement, pas un nouveau magasin. L'environnement n'est jamais découvert ou collecté ; il doit être fourni et `allow_environment=True`. Ce booléen exprime l'intention de l'appelant, **pas une autorisation de sécurité vérifiée**. Le runtime ne transmet que quelques états sur liste blanche, sans chemins, clés ni diagnostics libres. L'engine ne crée aucun worker, ne surveille rien et ne fait aucun appel réseau ; aucune protection automatique contre les secrets déjà présents dans les données fournies n'est promise.

## Validation et limites

Tests ajoutés : déterminisme, immuabilité et séparation des instructions, données adversariales étiquetées non fiables, budget Unicode et refus d'environnement implicite, mémoire optionnelle et recherche réelle SQLite sans modification, sources absentes, cycle de dépendance, concurrence et typage du registre. Un test en sous-processus QtCore démarre le vrai hub avec les services Core et fournisseur/audio factices, vérifie les identités, l'attachement par événement, l'absence de polling, puis les détachements et arrêts répétés. Les tests de cycle de vie Core antérieurs restent pertinents.

Mesure indicative, pas une certification matérielle : Linux x86-64 VM Xeon 2,60 GHz, Python 3.11, 1 000 constructions d'un contexte factice de 30 messages avec budget 2 048, sans mémoire SQLite : 1,178 s mur, 1,178 s CPU, pic `tracemalloc` 19 154 octets après échauffement. Cela ne mesure ni RSS global, ni GPU, ni E/S, ni Windows/Pentium G5400. Aucun processus ou thread supplémentaire n'est créé par le Context Engine.

Exécution Linux ciblée finale : **207 tests réussis**, incluant tous les `test_core_*`, Context, conversation, mémoire, scheduler, état runtime et façade mémoire Dashboard ; Ruff et `pip check` réussis. Une exécution incluant `test_dashboard_tasks.py` a obtenu 204 réussites et une erreur de collecte `libGL.so.1` absente. Suite Linux complète lancée avant les dernières modifications mineures de Context et l'ajout du test d'actions : **1 462 réussis, 561 sous-tests réussis, 114 ignorés, 15 échecs, 21 erreurs de collecte**, en 492,64 s ; les 36 traces sont des imports bloqués par `libGL.so.1` absente. Ce n'est pas une suite verte ; les dernières modifications sont couvertes par le ciblage final, pas par cette suite complète. Les résultats précédents se trouvent dans `validation-post-lots-00-01.md`. Tester sur Windows natif avec pilotes audio et Qt avant toute affirmation sur la GUI, le matériel ou les performances cibles. Aucune validation réseau Gemini réelle n'a été réalisée.

## Proposition précise pour le lot 02 — non implémentée ici

1. Définir des identités/origines distinctes : interface locale, demande vocale utilisateur authentifiée, modèle/outils, tâche de fond et automatisation. Ne jamais confondre une intention émise par le modèle avec l'accord utilisateur.
2. Faire passer **tous** les chemins (Core, outils, Dashboard, Orb/Desktop, routines, tâches, backup, musique et accès Context) par un point d'autorisation indépendant, refus par défaut ; inventorier et fermer les appels directs qui le contournent.
3. Exprimer les capacités par opération et ressource (`context.read.conversation`, `context.read.memory`, `context.read.environment`, écriture/suppression, fichiers, réseau, etc.), avec portée et durée minimales. Accorder séparément la lecture de l'environnement et la mémoire durable.
4. Pour les opérations sensibles, obtenir une confirmation locale fiable liée à l'identité, l'action, la ressource, le contenu pertinent et l'expiration ; preuve à usage unique, non rejouable, vérifiée au moment de l'effet. Un champ `confirm` issu du modèle n'est pas une preuve.
5. Fournir au Context Engine une autorisation vérifiable et non un booléen libre ; refuser ou expurger les sources non autorisées, garder la provenance et éviter tout accès avant vérification. Ne brancher aucun snapshot au modèle avant tests adversariaux d'injection et politique de divulgation.
6. Tester refus, autorisations croisées, réutilisation et expiration des preuves, changement d'état entre confirmation et effet, chemins asynchrones/routines, initialisation partielle et logs expurgés, puis valider en conditions natives.
