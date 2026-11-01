# Validation de clôture — lot 01.5

Date de validation : 1 novembre 2026. **Verdict général : PARTIELLEMENT VALIDÉ ; clôture définitive déconseillée.** Cette livraison améliore réellement le raccordement et le contexte, mais ni la migration complète des dépendances ni la validation graphique et Windows n'ont été démontrées.

## A. Git et protection des travaux locaux

- Dépôt `grimalkin2811/Jarvis_Live_Ready`, branche `arena/8edade8b-jarvis-live-ready` uniquement ; aucun merge, force-push, release ou modification de `main`.
- Base retenue `origin/feature/v1.9.0` : `43ff0029ca1eadfcaca40fd0cbdddb18248e11e8`, ancêtre de `f9a3144f24be84b5d1cf47a0f771099ccb78bb1a`, lui-même ancêtre de `749da3ca4c9b7c9c4cff5fe1adeda66733814a98`. L'historique depuis la base contient `d393043`, `5cca4e4`, `f9a3144` et `749da3c`. `main` distant est `ec944e26457b22492b5872ee520d6bb3ce410081`, ancêtre commun trouvé dans ce clone superficiel.
- À la reprise, le **HEAD local réel** était `ec944e2`, mais le contenu de travail correspondait à `749da3c` distant, à l'exception de `.gitignore`. Aucun changement indexé ni fichier supplémentaire non ignoré par rapport à l'arbre distant. Un index temporaire actualisé a servi à vérifier les contenus et modes. La référence locale a été avancée de façon non destructive jusqu'à `749da3c`, puis l'index aligné sans changer les fichiers ; aucune réécriture de commit.
- Code de cette mission : commit `f4cdecae51cc7b2411e9efa70066c55772e6128c`. Le commit qui contient ce rapport est le commit documentaire final ; son identifiant exact est celui retourné par `git log -1 --format=%H -- docs/validation-cloture-lot-01-5.md`. Un fichier ne peut pas contenir son propre hash Git complet sans en changer le hash. Après push, vérifier `git rev-parse HEAD` et `git ls-remote origin refs/heads/arena/8edade8b-jarvis-live-ready`, ainsi que `git ls-remote origin refs/heads/main`.
- `.gitignore` reste modifié localement, non indexé et non commité : cinq octets accentués du fichier distant (`e9`, `e9`, `e7`, `e8`, `e9`) ont été remplacés par cinq séquences UTF-8 `ef bf bd`. SHA-256 local préservé : `dd211e71f5d97f965505729839e147c895cec7409d786f3bd33f9dc94b11abce`. Son origine n'est pas établie ; il n'a pas été « corrigé » ni effacé. L'arbre de travail n'est donc volontairement **pas propre**.

## B. Runtime et inventaire

L'inventaire détaillé par fichier, ligne, consommateur, service, origine, risque et piste de migration est dans [inventaire-runtime-cloture-01-5.md](inventaire-runtime-cloture-01-5.md), régénérable par `python scripts/inventory_runtime.py`. Il recense les appels explicites et états globaux visés. C'est une analyse AST, **pas une preuve exhaustive des alias, imports dynamiques, callbacks ou constructions indirectes** ; elle doit être complétée par une revue humaine des frontières métier.

| Groupe | Origine et consommateurs | État / risque / suite |
|---|---|---|
| Runtime, audio, Gemini | `CoreRuntime` construit les factories audio/provider une fois, console ou UI ; `_owner` interdit deux runtimes actifs dans le processus | Injection et arrêt testés avec doubles, pas avec appareils/API réels. La garantie ne couvre pas plusieurs processus. |
| Conversation, mémoire | factories Core, enregistrées `conversation`/`memory` ; provider et Dashboard les reçoivent | Instances partagées prouvées par tests factices et QtCore. Getters globaux des outils/backup/protocoles subsistent ; un setter externe peut changer l'identité. |
| Tâches et scheduler | factories historiques, enregistrées et fermées par Core | Hooks et arrêt testés ; appels historiques subsistent dans outils, routines et dialogues autonomes. Un worker bloquant ne peut pas être tué de force. |
| Todo, routines, modes | getters historiques, enregistrés paresseusement comme services **empruntés** | Ce n'est pas une propriété réelle du Core : un remplacement de singleton après résolution peut créer des références divergentes. Réviser propriétaire et supprimer setters/getters après migration des outils et consommateurs. |
| Activité, musique | `activity._DEFAULT_LOG`, `music.manager._DEFAULT` | Non migrés ; outils les utilisent directement. Conserver provisoirement plutôt que créer une seconde instance ; injecter au dispatcher et auditer leur fermeture. |
| Notifications, protocoles, événements et contrôles | `notifications._HOOKS`/worker, `protocols._ACTIVE_RUN`, `menu_state.LIVE`, ponts `DASHBOARD`, `DESKTOP_EVENTS`, `INTERFACE_MODE`, `VISIBILITY` | Conservés ; ponts UI ne sont pas des services métier. Gestion des souscriptions à auditer de bout en bout, y compris événements déjà en file. |
| Autres états | timers d'outils, historique système, override d'écriture, caches de déclarations et fenêtres `_DIALOG` | Non convertis artificiellement ; usage et persistance décrits dans l'inventaire et `lot-01-5-runtime-context.md`. |

**Changements effectifs.** `run_ui` crée le Core avant `InterfaceModeController`, le passe à Orb, contrôleur Desktop, Dashboard, tray et présentateur de protocole, puis au thread vocal. Orb résout mémoire, tâches, scheduler, routines et modes via le registre lorsqu'un Core est fourni ; les dialogues correspondants reçoivent ces dépendances. Desktop et protocoles résolvent les modes par le même registre. Les appels autonomes sans Core gardent leurs anciens fallbacks. L'import de l'orbe ne construit plus le gestionnaire de routines ; le menu reste initialisé au rafraîchissement. Le hub Dashboard ignore les signaux retardés après fermeture et ne se réabonne pas. Les dialogues de tâches et routines écartent les callbacks tardifs ; les dialogues ouverts sont fermés avant l'arrêt vocal.

**Preuves et réserves.** Les tests `test_core_*` prouvent l'unicité du runtime actif, le partage provider/QtCore Dashboard, l'injection, les abonnements et arrêts répétés ou partiels. Le nouveau test `test_ui_core_injection.py` tente le partage Orb/Desktop/Dashboard, mais **échoue à l'import Qt** ; la preuve graphique complète n'est donc pas obtenue. Les outils `src/tools.py` continuent d'appeler neuf familles de getters historiques ; Gemini utilise encore `get_default_mode_manager`, ainsi que les chemins routines, scheduler, backup et protocoles. Migrer ces chemins sans perdre leur compatibilité et leur politique d'exécution exige une injection explicite au dispatcher et une migration coordonnée des propriétaires, pas un singleton caché dans un registre. Certaines routines déjà lancées continuent dans un thread daemon après fermeture ; leurs effets ne sont pas annulables de force. L'Orb conserve des lectures SQLite/disque et un contrôle `mtime` à intervalle sur le thread UI ; l'absence totale de blocage ou de polling n'est **pas démontrée**.

## C. Context Engine

`ContextEngine` est enregistré dans le Core, sans dépendance Qt ou Gemini et sans possession des sources. Il ne collecte ni environnement ni réseau de lui-même et n'ajoute ni embeddings ni surveillance. `build` est synchrone ; `build_async` l'exécute sur l'exécuteur partagé uniquement à la demande et rend la main après délai. Une source synchrone bloquée ne peut pas être tuée : le worker peut survivre au timeout et retarder l'arrêt Python. Une seule construction asynchrone par engine peut rester en vol ; d'autres sont refusées. Les sources utilisées doivent donc rester locales et bornées ; une isolation/processus adapté est nécessaire pour un fournisseur inconnu ou non fiable.

Sources réellement utilisables : conversation existante (capture atomique ID/messages), recherche SQLite mémoire explicitement demandée avec `mark_accessed=False`, et quelques états runtime sur liste blanche. Projet : données `project_data` ou `ProjectEntry` fournies par l'appelant, **pas de gestionnaire de projet actif implémenté**. Environnement : texte fourni par l'appelant et `allow_environment=True`, **pas de collecte de l'environnement système ni d'autorisation vérifiée**. `memory=False` empêche l'appel à cette source. Une source qui lève une exception est indiquée comme manquante ; un timeout asynchrone remonte une exception, pas un faux snapshot complet.

Le snapshot immuable contient `snapshot_id` aléatoire, `created_at` UTC, ID conversation lorsque disponible, `intent` facultative, `sources`, items, références, priorité, motif de sélection, estimation, troncature, exclusions et sources manquantes. ID et date sont exclus de la comparaison d'équivalence : deux assemblages identiques sont équivalents tout en ayant des identifiants différents. Les éléments sont `untrusted_data` ; `instructions` est toujours vide. La présence de ce marquage ne protège pas un futur consommateur qui traiterait mal ces données.

Règles déterministes pour des sources stables : requête, 30 messages les plus récents en ordre inverse et indicateur de recouvrement lexical ; jusqu'à 10 résultats de recherche mémoire, triés par importance décroissante puis identifiant ; entrées projet filtrées par mots communs à la requête, ou toutes si aucune requête exploitable ; champs runtime autorisés ; environnement fourni. Le texte `project_data` historique est fourni tel quel, **sans filtre de pertinence**. Les contradictions restent distinctes avec leurs références ; aucune résolution sémantique. Maximum 40 candidats sélectionnables, 8 000 caractères par texte, 40 entrées projet et budget 0–16 000 ; troncatures, contenu Unicode invalide et exclusions observées sont tracés. La sélection lexicale n'est pas une compréhension sémantique. Les messages antérieurs aux 30 derniers et souvenirs non renvoyés par la recherche ne sont pas collectés ni individuellement tracés : limite explicite du contrat actuel.

L'estimation est `ceil(octets UTF-8/4)` pour chaque texte sélectionné, sans métadonnées ; ce n'est ni le tokenizer du modèle ni une limite exacte de fournisseur. Les snapshots peuvent contenir des informations sensibles déjà fournies : aucun masquage automatique n'est garanti, ne pas les journaliser sans politique. Le provider Gemini historique injecte par ailleurs sa propre mémoire dans ses instructions : ce chemin antérieur est distinct du nouveau moteur et doit être traité au lot 02.

## D. Tests et validation

Exécutions après les dernières modifications du code `f4cdeca` (Python 3.11.2, Linux x86-64, PySide6 6.11.3, `QT_QPA_PLATFORM=offscreen`, dépendances dans un venv local) :

| Catégorie / commande | Résultat |
|---|---|
| Core, injection, vie/arrêt : `pytest tests/test_core_*.py -q --timeout=120` | **31 réussis** ; provider/audio factices, hub QtCore réel en sous-processus. |
| Contexte : `pytest tests/test_context_engine.py tests/test_context_scenarios.py -q --timeout=60` | **12 réussis** : conversation, projet, importance SQLite, contradictions, budget, exclusions, injection, erreur/timeout, déterminisme et provenance. |
| Interfaces : `pytest tests/test_core_dashboard_shared_services.py tests/test_dashboard_registry_actions.py tests/test_ui_core_injection.py -q --timeout=60` | **3 réussis, 1 échec** : le nouveau test Orb/Desktop/Dashboard ne dépasse pas l'import QtWidgets (`libGL.so.1` absente). Les 3 autres couvrent QtCore et actions sans widgets ; ils ne remplacent pas le test graphique. |
| Régressions ciblées : `pytest tests/test_core_*.py tests/test_context_engine.py tests/test_context_scenarios.py tests/test_dashboard_registry_actions.py tests/test_conversation_*.py tests/test_memory.py tests/test_scheduler.py tests/test_runtime_status.py tests/test_dashboard_memory_service.py --timeout=120 -q` | **213 réussis**. Les catégories ci-dessus se recouvrent : ne pas additionner leurs nombres. |
| Suite complète : `pytest --continue-on-collection-errors --timeout=240 -q` | **1 469 réussis, 561 sous-tests réussis, 114 ignorés, 16 échecs, 21 erreurs de collecte**, 512,52 s, sortie 1. Les 37 traces montrent `ImportError: libGL.so.1`. Aucun échec n'est compté comme succès. |
| Suite répétée avec motifs : même commande et `-rs` | **1 469 réussis, 561 sous-tests réussis, 114 ignorés, 16 échecs, 21 erreurs**, 511,42 s, sortie 1 ; mêmes 37 traces `libGL.so.1`. |
| Lint / dépendances | `ruff check .`, `python -m pip check`, `python -m compileall -q src UI scripts/benchmark_context.py scripts/inventory_runtime.py` : réussis ; `git diff --check` sur les changements de mission réussi. |

Modules non collectés : `test_blob_visibility`, `test_dashboard_conversation_widget`, `test_dashboard_entry_points`, `test_dashboard_perf`, `test_dashboard_productivity`, `test_dashboard_tasks`, `test_dashboard_widgets`, `test_dashboard_window`, `test_interruption_ui`, `test_item_bg_opacity`, `test_menu_backgrounds`, `test_menu_hover`, `test_menu_integration`, `test_menu_layout`, `test_menu_render_offscreen`, `test_menu_ux`, `test_mode_apps_dialog`, `test_orb_visibility`, `test_protocol_ui`, `test_routines_ui`, `test_ui_menu_regression`. Les échecs exécutés concernent Dashboard/config/notifications/sécurité, Desktop config, arrêt manuel UI, PySide refcount, le nouveau test d'injection graphique et le protocole de validation ; chacun échoue à cet import. Sur le second run `-rs`, les 114 ignorés se répartissent ainsi : 28 `test_desktop_appearance_dialog`, 39 `test_desktop_hud`, 22 `test_desktop_integration`, 8 `test_desktop_overlay`, 4 `test_interface_mode`, 11 `test_launcher_gui`, 2 sous-tests unittest. Motifs affichés : 93 « PySide6 indisponible », 8 « PySide6 / OpenGL indisponible », 11 « PySide6 non installé » et 2 `Qt indisponible : libGL.so.1`. PySide6 est bien installé dans ce venv ; les libellés proviennent des gardes historiques qui considèrent l'import QtGui/Widgets échoué comme une absence de PySide6.

La tentative de fournir une bibliothèque GL isolée depuis un sysroot a révélé d'autres bibliothèques natives absentes (`libxkbcommon`, EGL, D-Bus) ; elle n'a pas été utilisée pour les résultats ci-dessus et n'est pas une validation graphique. Aucun skip n'a été ajouté pour masquer le problème. Windows, interaction GUI native, périphériques audio et Gemini réel ne sont pas validés. Refaire toute la suite sur Windows avec les dépendances natives et périphériques appropriés, puis contrôler manuellement les transitions Orb/Desktop/Dashboard et l'arrêt.

Windows préparatoire : `pip download --platform win_amd64 --python-version 3.11 --implementation cp --abi cp311 --only-binary=:all: --no-deps --require-hashes -r requirements-lock/windows-py311.txt` a vérifié et téléchargé **63 wheels**. Cela ne prouve ni résolution native des dépendances, ni installation, ni exécution Windows. `gh workflow run build.yml --ref arena/8edade8b-jarvis-live-ready` a répondu **HTTP 403 `Resource not accessible by integration`** ; aucun run n'a été déclenché par cette commande. Une validation Windows doit être lancée par une identité disposant des droits adéquats après push.

## E. Performances

Méthode reproductible : `python scripts/benchmark_context.py --iterations 500` ; VM Linux x86-64, 2 CPU logiques, Intel Xeon 2,60 GHz, Python 3.11.2. Données artificielles en mémoire, après échauffement, budget 2 048, pas de recherche SQLite, GUI, réseau ni audio ; `tracemalloc` ne mesure pas le RSS total.

| Messages source | Itérations | Mur (s) | CPU (s) | Pic Python tracé (octets) |
|---:|---:|---:|---:|---:|
| 5 | 500 | 0,3794 | 0,3794 | 8 771 |
| 30 | 500 | 1,9935 | 1,9921 | 18 219 |
| 200 | 500 | 1,9647 | 1,9644 | 18 300 |

Le moteur ne lit au plus que les 30 derniers messages : ces chiffres ne mesurent pas le coût de traitement de 200 messages. Pas de mesure Pentium G5400/Windows 11, pas d'extrapolation garantie sur la réactivité, SQLite, la contention ou la mémoire globale. Le code historique de l'Orb peut encore faire des lectures sur le thread graphique ; un profilage natif est nécessaire.

## F. Sécurité : état réel

Aucune permission n'est accordée par le Context Engine. La donnée est étiquetée non fiable, les sources sensibles ne sont pas implicitement collectées, les exclusions ne recopient pas leurs contenus et les exceptions de source ne sont pas injectées dans les snapshots. Ces garanties ne constituent ni isolation, ni classification automatique des secrets, ni contrôle d'autorisation ; `allow_environment` est une intention libre. Les appels historiques restent des contournements potentiels. Le garde Live existant bloque certains cas de `confirm`, mais ne constitue pas une autorisation indépendante ; **un `confirm=true` fourni par le modèle ne doit jamais servir de preuve**.

## G. Verdicts distincts

| Partie | Verdict | Motif |
|---|---|---|
| Git et protection du travail local | **VALIDÉ SOUS RÉSERVE** | Vérifier les refs après push ; `.gitignore` reste volontairement local et modifié. |
| Core et cycle de vie non graphique | **VALIDÉ SOUS RÉSERVE** | Tests factices et QtCore réussis, non matériel ; workers bloquants non tuables. |
| Unification de toutes les interfaces et outils | **PARTIELLEMENT VALIDÉ** | Nouveaux branchements réellement effectués, mais singletons empruntés et nombreux chemins historiques demeurent ; test graphique bloqué. |
| Context Engine | **VALIDÉ SOUS RÉSERVE** | Scénarios couverts ; projet/environnement fournis, heuristiques lexicales et traçabilité des éléments non collectés limitées. |
| Régressions globales Linux | **PARTIELLEMENT VALIDÉ** | Nombreux tests réussis mais 16 échecs et 21 erreurs d'environnement non résolus. |
| Windows, GUI native, audio et Gemini réels | **NON VALIDÉ** | Aucune exécution native ou matérielle, Actions refusées. |
| Performances cible G5400 | **NON VALIDÉ** | Microbenchmark Xeon uniquement. |
| Clôture globale du lot 01.5 | **PARTIELLEMENT VALIDÉ** | Migration essentielle et tests GUI/Windows encore à terminer. |

## H. Lot 02 — proposition, non implémentée

1. **Prérequis** : terminer l'injection au dispatcher des outils, routines, backup, activité, musique et consommateurs legacy ; éviter setters globaux divergents ; obtenir une suite Qt et Windows exploitable ; cataloguer les effets réels et les ressources touchées.
2. Définir des identités et origines vérifiables distinctes (UI locale, utilisateur vocal authentifié, modèle, routine, tâche de fond) ; préserver l'origine et la chaîne de délégation à chaque passage de thread/processus. Une intention modèle ne vaut jamais identité ni consentement utilisateur.
3. Classifier chaque opération et chaque source de contexte par sensibilité, ressource, type d'accès et effet ; politique centrale explicite avec refus par défaut, sans bypass direct via fonctions historiques. Contrôler le chemin effectif et pas seulement le nom déclaré.
4. Accorder des capacités minimales, limitées en portée, durée et usage ; demander une confirmation indépendante sur un canal utilisateur fiable pour les effets sensibles. Lier la preuve au principal, à l'opération, aux arguments normalisés, à la ressource, au contexte, au nonce et à l'expiration ; consommation atomique, non-rejeu et révocation possibles.
5. Revalider identité, arguments et état de la ressource **après** confirmation et juste avant l'effet (TOCTOU) ; invalider la preuve si l'action, les paramètres ou la cible changent. Le booléen `confirm=true` du modèle ne crée aucune preuve.
6. Vérifier les autorisations **avant toute lecture** de conversation, mémoire, projet ou environnement ; le Context Engine reçoit seulement les sources autorisées, sans émettre ni élargir de permission. Maintenir provenance et confidentialité, protéger les snapshots et journaux, et interdire toute promotion implicite de données en instructions.
7. Journaliser les décisions et leur motif avec corrélation et expurgation, sans clé ni contenu sensible ; prévoir révocation, expiration, audit d'échec et interface de gestion. Tester refus par défaut, origines, injections, preuves expirées/rejouées, courses, annulations, tâches/routines et tous les chemins UI/outils, puis valider en Windows natif et sur la machine cible.
