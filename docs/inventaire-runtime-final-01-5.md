# Inventaire statique des dépendances et états de processus


## Mission finale sur `819c796` — décision courante

**ARCHITECTURAL: PARTIAL · ENVIRONMENTAL: PARTIAL · LOT 01.5: OPEN.** La section « Mission finale » de `contrat-temporel-01-5.md` prime sur tous les encadrés historiques. La factory synchrone entrée est désormais drainée avant STOPPING et Context soumet son executor sous gate ; toutefois un test observe encore une factory qui délègue à un thread externe créant après STOPPED, et un autre observe un thread d'executor de cleanup créé après STOPPING. #2/#3/#5/#6 restent BLOCKING ; #1/#4 NOT_VALIDATED. Les états de cleanup sont observables sans prétendre pouvoir interrompre un SDK externe.

## Reprise temporelle la plus récente

Le tableau OWNED/BORROWED/PRESTARTED/EXTERNAL/UNSUPPORTED, l'inventaire ciblé des workers et la reproduction d'une création par factory custom après STOPPING sont dans `contrat-temporel-01-5.md`. Une factory non contrôlée est classée UNSUPPORTED pour la garantie mais **n'est pas actuellement refusée** ; son partage/ses ressources externes ne sont pas prouvés. Le lot reste ARCHITECTURAL PARTIAL et ENVIRONMENTAL PARTIAL. Les relevés ci-dessous sont historiques.

Généré par `python scripts/inventory_runtime.py`. Les fonctions/méthodes indiquent le consommateur.
La recherche AST couvre ces noms et constructions explicites, pas les appels dynamiques, alias ou callbacks indirects ; voir les tableaux de responsabilités et exceptions dans `validation-finale-lot-01-5.md`.
Un getter historique est une compatibilité à examiner, pas une preuve que le registre possède le service ni une raison suffisante pour le migrer. Les lignes AST ci-dessous sont celles du relevé précédent et peuvent avoir changé ; le tableau de clôture en fin de document prime.

| Fichier:ligne | Consommateur | Service | Appel | Origine | Risque | Migration / raison de conservation |
|---|---|---|---|---|---|---|
| `src/activity.py:295` | `get_default_activity_log` | activity | `ActivityLog` | construction directe | nouvelle instance ; vérifier propriétaire et appelants | examiner propriétaire avant de remplacer ou retirer |
| `src/activity.py:308` | `log_action` | activity | `get_default_activity_log` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/background_tasks/manager.py:552` | `configure_default_task_manager` | tasks | `TaskManager` | construction directe | nouvelle instance ; vérifier propriétaire et appelants | examiner propriétaire avant de remplacer ou retirer |
| `src/backup.py:52` | `_sources` | memory | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/backup.py:52` | `_sources` | memory | `get_default_memory_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/backup.py:53` | `_sources` | scheduler | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/backup.py:53` | `_sources` | scheduler | `get_default_scheduler` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/backup.py:54` | `_sources` | todo | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/backup.py:54` | `_sources` | todo | `get_default_todo_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/backup.py:55` | `_sources` | activity | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/backup.py:55` | `_sources` | activity | `get_default_activity_log` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/backup.py:56` | `_sources` | routines | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/backup.py:56` | `_sources` | routines | `get_default_routine_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/conversation.py:1034` | `get_default_conversation_context` | conversation | `ConversationContext` | construction directe | nouvelle instance ; vérifier propriétaire et appelants | examiner propriétaire avant de remplacer ou retirer |
| `src/conversation.py:1046` | `start_new_conversation` | conversation | `get_default_conversation_context` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/core/context.py:178` | `ContextEngine._build` | conversation | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/context.py:201` | `ContextEngine._build` | memory | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/context.py:237` | `ContextEngine._build` | status | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:64` | `_default_tasks` | tasks | `configure_default_task_manager` | configuration globale historique | cycle de vie et remplacement à coordonner | compatibilité des factories ; supprimer après migration des propriétaires |
| `src/core/runtime.py:71` | `_default_scheduler` | scheduler | `get_default_scheduler` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | compatibilité des factories ; supprimer après migration des propriétaires |
| `src/core/runtime.py:76` | `_default_todo` | todo | `get_default_todo_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | compatibilité des factories ; supprimer après migration des propriétaires |
| `src/core/runtime.py:81` | `_default_routines` | routines | `get_default_routine_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | compatibilité des factories ; supprimer après migration des propriétaires |
| `src/core/runtime.py:86` | `_default_modes` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | compatibilité des factories ; supprimer après migration des propriétaires |
| `src/core/runtime.py:91` | `_default_activity` | activity | `get_default_activity_log` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | compatibilité des factories ; supprimer après migration des propriétaires |
| `src/core/runtime.py:96` | `_default_music` | music | `get_default_music_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | compatibilité des factories ; supprimer après migration des propriétaires |
| `src/core/runtime.py:113` | `CoreRuntime.__init__` | status | `register` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:114` | `CoreRuntime.__init__` | activity_boundary | `register` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:115` | `CoreRuntime.__init__` | context | `register` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:119` | `CoreRuntime.__init__` | todo | `register_lazy` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:120` | `CoreRuntime.__init__` | routines | `register_lazy` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:121` | `CoreRuntime.__init__` | modes | `register_lazy` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:122` | `CoreRuntime.__init__` | activity | `register_lazy` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:123` | `CoreRuntime.__init__` | music | `register_lazy` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:528` | `CoreRuntime._run_pipeline` | memory | `register` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:533` | `CoreRuntime._run_pipeline` | conversation | `register` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:538` | `CoreRuntime._run_pipeline` | runtime_timers | `register` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:543` | `CoreRuntime._run_pipeline` | tools | `register` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:546` | `CoreRuntime._run_pipeline` | scheduler | `register` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:562` | `CoreRuntime._run_pipeline` | tasks | `register` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:591` | `CoreRuntime._run_pipeline` | audio | `register` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:635` | `CoreRuntime._run_pipeline` | provider | `register` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/gemini_live.py:326` | `GeminiLive.__init__` | conversation | `get_default_conversation_context` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/gemini_live.py:1340` | `GeminiLive._connect_once` | modes | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/gemini_live.py:1340` | `GeminiLive._connect_once` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/main.py:238` | `run_headless` | runtime | `CoreRuntime` | construction directe | nouvelle instance ; vérifier propriétaire et appelants | examiner propriétaire avant de remplacer ou retirer |
| `src/memory.py:1119` | `get_default_memory_manager` | memory | `MemoryManager` | construction directe | nouvelle instance ; vérifier propriétaire et appelants | examiner propriétaire avant de remplacer ou retirer |
| `src/modes.py:1027` | `get_default_mode_manager` | modes | `JarvisModeManager` | construction directe | nouvelle instance ; vérifier propriétaire et appelants | examiner propriétaire avant de remplacer ou retirer |
| `src/music/manager.py:351` | `get_default_music_manager` | music | `MusicManager` | construction directe | nouvelle instance ; vérifier propriétaire et appelants | examiner propriétaire avant de remplacer ou retirer |
| `src/protocols.py:151` | `_probe_memory_bank` | memory | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/protocols.py:151` | `_probe_memory_bank` | memory | `get_default_memory_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/protocols.py:165` | `_probe_routines` | routines | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/protocols.py:165` | `_probe_routines` | routines | `get_default_routine_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/protocols.py:178` | `_probe_reminders` | scheduler | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/protocols.py:178` | `_probe_reminders` | scheduler | `get_default_scheduler` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/protocols.py:397` | `_run_tool` | tools | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/protocols.py:599` | `start_protocol` | tools | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/routine_actions.py:16` | `_scheduler` | scheduler | `get_default_scheduler` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/routine_actions.py:16` | `_scheduler` | scheduler | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/routines.py:828` | `get_default_routine_manager` | routines | `RoutineManager` | construction directe | nouvelle instance ; vérifier propriétaire et appelants | examiner propriétaire avant de remplacer ou retirer |
| `src/scheduler.py:67` | `_notifications_suppressed_by_mode` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/scheduler.py:189` | `Scheduler._routines` | routines | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/scheduler.py:194` | `Scheduler._routines` | routines | `get_default_routine_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/scheduler.py:201` | `Scheduler._run_routine` | tools | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/scheduler.py:209` | `Scheduler._mode_suppresses_notifications` | modes | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/scheduler.py:658` | `get_default_scheduler` | scheduler | `Scheduler` | construction directe | nouvelle instance ; vérifier propriétaire et appelants | examiner propriétaire avant de remplacer ou retirer |
| `src/scheduler.py:670` | `start_default_scheduler` | scheduler | `get_default_scheduler` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/todo.py:367` | `get_default_todo_manager` | todo | `TodoManager` | construction directe | nouvelle instance ; vérifier propriétaire et appelants | examiner propriétaire avant de remplacer ou retirer |
| `src/tools.py:713` | `_music_manager` | music | `get_default_music_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au dispatcher ; préserver API routines/outils historique |
| `src/tools.py:722` | `music_search` | music | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:738` | `music_play` | music | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:793` | `music_play_track` | music | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:798` | `music_play_artist` | music | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:803` | `music_play_album` | music | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:808` | `music_play_playlist` | music | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:813` | `music_pause` | music | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:818` | `music_resume` | music | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:823` | `music_next` | music | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:828` | `music_previous` | music | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:833` | `music_stop` | music | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:838` | `music_current` | music | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:847` | `music_list_playlists` | music | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:852` | `music_playlist_save` | music | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:861` | `music_playlist_import` | music | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:866` | `music_playlist_remove` | music | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:871` | `music_playlist_list` | music | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:876` | `music_status` | music | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:881` | `music_disconnect` | music | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1363` | `set_timer` | runtime_timers | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/tools.py:1889` | `remember` | memory | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1899` | `recall` | memory | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1904` | `list_memories` | memory | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1909` | `search_memories` | memory | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1914` | `update_memory` | memory | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1924` | `delete_memory` | memory | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1929` | `forget` | memory | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1934` | `clear_memory` | memory | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1944` | `create_routine` | routines | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1954` | `run_routine` | routines | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1957` | `run_routine` | tools | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/tools.py:1962` | `list_routines` | routines | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1967` | `describe_routine` | routines | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1972` | `update_routine` | routines | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1983` | `delete_routine` | routines | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2000` | `activate_focus_mode` | modes | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2008` | `activate_game_mode` | modes | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2016` | `disable_jarvis_mode` | modes | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2021` | `get_jarvis_mode` | modes | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2031` | `list_mode_applications` | modes | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2040` | `set_mode_applications` | modes | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2050` | `toggle_mode_application` | modes | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2055` | `reset_mode_applications` | modes | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2302` | `reset_conversation` | conversation | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2326` | `get_conversation_state` | conversation | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2346` | `set_reminder` | scheduler | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2356` | `list_reminders` | scheduler | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2361` | `cancel_reminder` | scheduler | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2449` | `_task_manager` | tasks | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2544` | `add_todo` | todo | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2548` | `list_todos` | todo | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2552` | `complete_todo` | todo | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2556` | `reopen_todo` | todo | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2560` | `delete_todo` | todo | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2564` | `clear_todos` | todo | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2568` | `get_activity_log` | activity | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2572` | `clear_activity_log` | activity | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2778` | `_guard_tool_function._wrapped` | modes | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2792` | `_guard_tool_function._wrapped` | activity | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/ui.py:300` | `InterfaceModeController.activate_current` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/ui.py:302` | `InterfaceModeController.activate_current` | modes | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/ui.py:392` | `_start_voice` | runtime | `CoreRuntime` | construction directe | nouvelle instance ; vérifier propriétaire et appelants | examiner propriétaire avant de remplacer ou retirer |
| `src/ui.py:534` | `_build_tray_icon._show_routines_if_allowed` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/ui.py:536` | `_build_tray_icon._show_routines_if_allowed` | modes | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/ui.py:543` | `_build_tray_icon._show_routines_if_allowed` | routines | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/ui.py:544` | `_build_tray_icon._show_routines_if_allowed` | scheduler | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/ui.py:545` | `_build_tray_icon._show_routines_if_allowed` | tools | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/ui.py:557` | `_build_tray_icon._show_background_tasks` | tasks | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/ui.py:574` | `_build_tray_icon._show_desktop_appearance` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/ui.py:576` | `_build_tray_icon._show_desktop_appearance` | modes | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/ui.py:596` | `_build_tray_icon._start_protocol_if_allowed` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/ui.py:598` | `_build_tray_icon._start_protocol_if_allowed` | modes | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/ui.py:722` | `run_ui` | runtime | `CoreRuntime` | construction directe | nouvelle instance ; vérifier propriétaire et appelants | examiner propriétaire avant de remplacer ou retirer |
| `UI/background_tasks_dialog.py:145` | `show_background_tasks_dialog` | tasks | `get_default_task_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/boot_sequence.py:506` | `ProtocolPresenter._visuals_suppressed` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/boot_sequence.py:508` | `ProtocolPresenter._visuals_suppressed` | modes | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `UI/dashboard/actions.py:97` | `conversation_new` | conversation | `get_default_conversation_context` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/dashboard/actions.py:143` | `routine_names` | routines | `get_default_routine_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/dashboard/actions.py:157` | `run_routine` | routines | `get_default_routine_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/dashboard/actions.py:236` | `build_actions._run` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/dashboard/controller.py:584` | `DashboardController.maybe_open_on_launch` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/dashboard/controller.py:586` | `DashboardController.maybe_open_on_launch` | modes | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `UI/dashboard/hub.py:96` | `DashboardHub.__init__` | status | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `UI/dashboard/hub.py:100` | `DashboardHub.__init__` | tasks | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `UI/dashboard/hub.py:101` | `DashboardHub.__init__` | memory | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `UI/dashboard/hub.py:102` | `DashboardHub.__init__` | todo | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `UI/dashboard/hub.py:114` | `DashboardHub.__init__.conversation_getter` | conversation | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `UI/dashboard/hub.py:119` | `DashboardHub.__init__.conversation_getter` | conversation | `get_default_conversation_context` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/dashboard/memory_service.py:116` | `MemoryService.__init__.manager_getter` | memory | `get_default_memory_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/dashboard/notes_service.py:95` | `TodoService.__init__.manager_getter` | todo | `get_default_todo_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/dashboard/tasks_service.py:81` | `TasksService.__init__.manager_getter` | tasks | `get_default_task_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/dashboard/widgets/jarvis_widgets.py:375` | `QuickActionsWidget._host` | tools | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `UI/desktop/overlay.py:835` | `DesktopOverlayController._suppressed` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/desktop/overlay.py:837` | `DesktopOverlayController._suppressed` | modes | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `UI/jarvis_menu.py:331` | `_routine_names` | routines | `get_default_routine_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/jarvis_menu.py:378` | `MorphingOrbWidget._service` | memory | `get_default_memory_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/jarvis_menu.py:381` | `MorphingOrbWidget._service` | tasks | `get_default_task_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/jarvis_menu.py:384` | `MorphingOrbWidget._service` | routines | `get_default_routine_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/jarvis_menu.py:387` | `MorphingOrbWidget._service` | scheduler | `get_default_scheduler` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/jarvis_menu.py:390` | `MorphingOrbWidget._service` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/jarvis_menu.py:1931` | `MorphingOrbWidget._run_routine_async._worker` | tools | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `UI/jarvis_menu.py:2316` | `MorphingOrbWidget._menu_callback_for._callback` | tools | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `UI/mode_apps_dialog.py:46` | `_ModeSection.__init__` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/mode_apps_dialog.py:143` | `ModeAppsDialog.__init__` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/routines_dialog.py:30` | `RoutinesDialog.__init__` | routines | `get_default_routine_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/routines_dialog.py:35` | `RoutinesDialog.__init__` | scheduler | `get_default_scheduler` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |

## États globaux recensés

| Fichier:ligne | État |
|---|---|
| `src/activity.py:280` | `_DEFAULT_LOG` |
| `src/background_tasks/manager.py:542` | `_DEFAULT` |
| `src/conversation.py:1025` | `_DEFAULT` |
| `src/memory.py:1101` | `_DEFAULT_MANAGER` |
| `src/modes.py:1012` | `_DEFAULT_MANAGER` |
| `src/music/manager.py:39` | `_DEFAULT` |
| `src/notifications.py:18` | `_HOOKS` |
| `src/notifications.py:19` | `_WINDOWS_QUEUE` |
| `src/notifications.py:20` | `_WINDOWS_WORKER` |
| `src/protocols.py:432` | `_ACTIVE_RUN` |
| `src/protocols.py:433` | `_LISTENERS` |
| `src/routines.py:93` | `_TOOL_FUNCTIONS` |
| `src/routines.py:94` | `_TOOL_DECLARATIONS` |
| `src/routines.py:813` | `_DEFAULT_MANAGER` |
| `src/runtime_status.py:367` | `RUNTIME` |
| `src/scheduler.py:643` | `_DEFAULT_SCHEDULER` |
| `src/system_extras.py:99` | `_HISTORY` |
| `src/todo.py:359` | `_DEFAULT_MANAGER` |
| `src/tools.py:1267` | `_TIMERS` |
| `src/writing/service.py:35` | `_writer_override` |
| `UI/background_tasks_dialog.py:137` | `_DIALOG` |
| `UI/dashboard_bridge.py:134` | `DASHBOARD` |
| `UI/desktop/events.py:87` | `DESKTOP_EVENTS` |
| `UI/desktop_appearance_dialog.py:634` | `_DIALOG` |
| `UI/interface_mode_bridge.py:39` | `INTERFACE_MODE` |
| `UI/menu_state.py:333` | `LIVE` |
| `UI/mode_apps_dialog.py:246` | `_DIALOG` |
| `UI/routines_dialog.py:295` | `_DIALOG` |
| `UI/visibility_bridge.py:179` | `VISIBILITY` |

## Revue manuelle complémentaire — boundary 01.5

La table AST donne les appels par symbole et leurs lignes ; les mentions génériques « injecter » ne prouvent pas à elles seules un chemin complet. Les décisions ci-dessous s'appliquent au Core injecté, sauf mention autonome. Preuves exécutées et limites dans `validation-finale-runtime-boundary-01-5.md`.

| État/factory et appelants | Nature et risque | Décision / preuve |
|---|---|---|
| `core.runtime.CoreRuntime`, `src.ui._start_voice` | seul propriétaire actif du processus ; registre par session | propriétaire retenu pendant nettoyage différé ; tests threads Core. Une instance créée mais jamais exécutée n'est pas propriétaire. |
| `core.context.ContextEngine`, `context.build/build_async`, registres | un contexte par Core ; snapshots éphémères et non mémoire persistante | bail sur thread exécutant, même après timeout/annulation asyncio ; `test_core_activity_boundary.py` et scénarios Context. Context autonome reste explicitement séparé. |
| `tools.RuntimeToolBindings`, `gemini_live`, `scheduler`, `protocols`, routines/Orb | table de fonctions liée au Core ; chemins autonomes `TOOL_FUNCTIONS` séparés | barrière partagée, même thread que l'appel réel ; scheduler et protocole utilisent dispatcher injecté ; tests Core et magasins SQLite. Les appels directs à des fonctions historiques en dehors du dispatcher ne sont pas tous protégés. |
| `background_tasks.manager._DEFAULT`, `configure_default_task_manager`, `_default_tasks`, outils et `TasksService` | manager mutable persistant, thread/loop/futures, singleton historique | construction sans démarrage, enregistrement et liaison avant start par défaut ; rejet création/restart pendant fermeture, annulation, jointure et rétention Core si résistant. Test outil/TasksService/persistance/restauration. Une factory personnalisée peut démarrer avant de rendre l'objet. |
| `memory._DEFAULT_MANAGER`, `set_default_memory_manager`, conversation `_DEFAULT` | mémoire SQLite et conversation RAM distinctes ; les setters ou appels directs externes peuvent diverger | Core enregistre et injecte ; compatibilité globale conservée, sans garantie contre remplacement concurrent externe ; tests SQLite et identité. |
| `scheduler._DEFAULT_SCHEDULER`, `_default_scheduler`, `Scheduler._thread` | worker et SQLite ; son callback peut rester bloqué | injection avant démarrage de la factory standard ; signal puis attente bornée et propriétaire retenu ; factory personnalisée déjà démarrée = course résiduelle. |
| `todo._DEFAULT_MANAGER`, `routines._DEFAULT_MANAGER`, `modes._DEFAULT_MANAGER`, `activity._DEFAULT_LOG`, `music.manager._DEFAULT` | services empruntés paresseux, potentiellement utilisés aussi par API autonomes | identités résolues et conservées dans le registre ; non détruits comme propriété du Core ; getters/setters historiques restent une frontière non universelle. |
| `tools._TIMERS`, `_TIMER_SEQ`, `set_timer`, `RuntimeTimers` | dictionnaire historique partagé et un `threading.Timer` par minuteur ; ancien minuteur pouvait survivre au Core | minuteurs lancés via outils Core possédés/annulés/attendus par adaptateur ; callback exécuté sous bail ; API autonome garde son propre cycle ; tests d'annulation et callback bloqué. Dictionnaire historique retient les métadonnées terminées. |
| `audio.AudioIO.wake_thread`, `ins/outs` | thread wake et callbacks PortAudio ; accès possible au Core via micro | signal explicite, jointure bornée avant destruction des services et nettoyage différé si vivant ; erreurs de fermeture propagées. Test worker simulé avec vrai thread ; matériel/callbacks natifs non validés. |
| `gemini_live.GeminiLive` watcher/stall/tasks/`asyncio.to_thread` | tâches asyncio, client externe et appels outils qui peuvent continuer après annulation | shutdown provider existant, outil réel pris dans barrière ; tâches internes et SDK ne sont pas intégralement inspectables, pas de validation Gemini réel. |
| `protocols._ACTIVE_RUN`, `_LISTENERS`, `ProtocolRun.thread` | état de processus, callbacks et thread protocole | si services injectés, admission avant démarrage du thread et bail transféré jusqu’à sa sortie ; refus après fermeture. Console autonome hors suivi. |
| `UI.dashboard.async_jobs.JobRunner`, `DashboardHub.jobs` | executor local au hub, jobs asynchrones consultent les services | admission avant soumission, puis bail acquis sur thread d'exécution ; en file après fermeture rejeté avant accès ; hub arrête son executor sans bloquer ; tests QtCore réels. Les callbacks Qt et accès synchrones ne sont pas tous sous bail. |
| `UI.dashboard.resources.ResourceMonitor._thread` | prélèvement psutil et abonnés, propriétaire hub, pas de service Core requis | arrêt/jointure bornée ; référence conservée si sampler bloqué et doublon empêché, test réel ; une souscription pendant le blocage ne redémarre pas automatiquement à sa sortie. |
| `UI.routines_dialog._DIALOG`, `jarvis_menu._run_routine_async` | dialogues et threads UI, runner injecté ; ancien dialogue autonome peut conserver une dépendance obsolète | nouvelle injection réaffectée si inactif, refus si routine précédente active ; fonctions Core suivies ; fallback autonome ne l'est pas. QtWidgets non validé. |
| `UI.background_tasks_dialog._DIALOG`, `UI.mode_apps_dialog._DIALOG` | hooks/dialogues process ; ancienne instance peut garder mauvais manager | fermeture/détachement puis reconstruction en cas de nouvelle identité ; QtWidgets non validé. Autres `_DIALOG` : vues locales, pas de propriété globale universelle prouvée. |
| `notifications._HOOKS/_WINDOWS_QUEUE/_WINDOWS_WORKER`, `desktop.events.DESKTOP_EVENTS`, ponts `DASHBOARD/INTERFACE_MODE/VISIBILITY`, `menu_state.LIVE` | bus, états d'UI et worker Windows de processus, pas des managers Core | propriétaires historiques conservés ; désabonnements existants, pas d'attente de tous callbacks/worker Windows par Core ; non certifié. |
| `system_extras._HISTORY`, `writing.service._writer_override`, `routines._TOOL_FUNCTIONS/_TOOL_DECLARATIONS` | presse-papiers RAM, override tests, caches déclarations | globaux historiques distincts de Context ; pas de preuve d'isolation totale ; override n'est pas un moteur propriétaire. |
| QTimers (`UI/*`, Dashboard, Desktop, `notification_bridge`) | événements sur boucle Qt, propriétaires widgets/hub/contrôleurs ; certains callbacks accèdent aux services | destruction/stop côté UI, non tous inclus dans barrière ; courses de callbacks tardifs et accès synchrones restent à vérifier graphiquement. |
| `src.ui._start_voice`, thread voix | lance et possède boucle Core ; stop par événement/`request_stop` | jointure UI bornée existante ; cleanup différé peut continuer après fin du thread voix ; pas d'attente indéfinie dans UI. |

La collecte AST est indicative, non une preuve exhaustive des imports dynamiques, fermetures, SDK, drivers natifs, ni d'une autorisation de sécurité. Les tests ciblés n'exécutent pas tous les appelants de chaque ligne.

## Classification de clôture — revue manuelle 01.5

Périmètre : chemins applicatifs examinés à partir des recherches `Thread`, `Timer`, `create_task`, `Future`, `to_thread`, `ThreadPoolExecutor`, `QTimer`, factories/getters/états et de la table AST ci-dessus. Ce n'est pas une preuve exhaustive des alias, imports dynamiques, modules natifs ou activités initiées par des extensions. `OK` signifie uniquement le chemin et la preuve indiqués, non une garantie générale. `À CORRIGER` désigne un risque restant ; `NON VALIDÉ` un résultat non démontré. Une compatibilité n'est pas une migration automatique.

| Composant | Dépendance | Création | Propriétaire | Arrêt | Accès Runtime | Verdict |
|---|---|---|---|---|---|---|
| CoreRuntime / registre / owner | services et boundary | par session, factories injectables | Core, un owner actif | signal, drain, fermeture ou rétention différée | registre propre ; owner conservé si worker suivi vivant | OK |
| ContextEngine | conversation, mémoire optionnelle, status | une instance par Core | Core | bail même si `to_thread` survit à son timeout | registre ; snapshots éphémères distincts de mémoire | OK |
| Tools / Gemini dispatch | services partagés | bindings par Core | Core / provider | bail effectif côté appelant ; activités tardives refusées | registry injecté ; garde Live n'est pas une autorisation générale | OK |
| APIs `TOOL_FUNCTIONS`, Context standalone | getters historiques | module/factory autonome | appelant autonome | hors Core | non injectable par défaut | COMPATIBILITÉ JUSTIFIÉE |
| Conversation / memory | contexte RAM / SQLite | factory Core, getters/setters historiques possibles | Core pour instances enregistrées ; global indépendant pour API historique | fermeture Core uniquement pour ce qu'il possède | UI Hub : identité et lectures protégées ; setters externes peuvent diverger | À CORRIGER |
| Todo / routines / modes / activity / musique | stores, état et getters globaux | lazy historique, puis cache registre | empruntés par Core, pas détruits par lui | vie de processus / appelant | identité stable dans registre ; remplacement global concurrent possible | À CORRIGER |
| TaskManager standard | fichiers, event loop, client Gemini | construction sans start, bind avant start | Core | refuse création/restart et mutations seen/cancel tardives, annule futures, ferme client et joint thread ; conserve owner si bloqué | même manager tools, TasksService, routine et protocole testés | OK |
| TaskManager factory custom / références directes | thread ou mutations déjà en cours | code externe injectable | factory / Core après bind | démarrage antérieur et mutations synchrones débutées avant stop non universellement suivis | accès directs potentiels hors boundary | NON VALIDÉ |
| Scheduler standard | routine, dispatcher, SQLite | factory Core, injection avant start | Core | signal + jointure bornée et rétention si occupé | dispatcher injecté | OK |
| Scheduler custom pré-démarré | routine / dispatcher | factory externe | factory puis Core | signal et jointure existants | course avant injection non exclue | À CORRIGER |
| RuntimeTimers | tool / callback | timers par Core | Core | annule, attend ; conserve owner si callback bloqué | callback lié à boundary | OK |
| `_TIMERS` autonome | notifications, historique | global historique | processus / appelant | propre au timer | pas le registre Core | COMPATIBILITÉ JUSTIFIÉE |
| ProtocolRun | dispatcher, callbacks, `_ACTIVE_RUN` | thread admis avant start | Core si injecté ; appelant sinon | bail jusqu'à sortie, refus après stop | registre injecté ; API console autonome séparée | OK |
| Orb / dialogue routines workers | routines, tools | threads UI par demande | UI ; activités Core sous dispatcher | dialogue ferme sans tuer routine ; callback tardif ignoré | injection réaffectée ou refusée si déjà actif ; standalone hors Core | NON VALIDÉ |
| Dashboard JobRunner | executor QtCore, services | executor local Hub, threads à la demande | Hub ; activité effective sous bail Core | `shutdown(wait=False)` sur Hub ; nouvelle soumission refusée par Core | entrée sous bail puis travail réel sous bail séparé | NON VALIDÉ |
| Dashboard ResourceMonitor | psutil, abonnés UI | thread local à la demande | Hub | stop/join bornés ; référence conservée si bloqué | indépendant des services Core | COMPATIBILITÉ JUSTIFIÉE |
| Dashboard conversation synchronisé | conversation | Hub + widgets | Hub / Core | lecture et reset sous bail, refus après stop | identité registre testée, pas tous widgets | OK |
| Dialogues tâches / modes / routines, Orb autres accès | managers et hooks | QWidgets et singletons UI | UI, managers empruntés | rebind ou reconstruction selon dialogue ; certains caches persistent | accès synchrones et callbacks pas tous sous bail | À CORRIGER |
| QTimers / signaux / callbacks UI | Qt event loop, parfois services | widget / hub / contrôleur | Qt/UI | stop/destroy de l'objet, parfois callback en file | couverture non exhaustive des accès tardifs | NON VALIDÉ |
| Notification Windows, bus UI et ponts globaux | queue Windows / abonnés / événements | globals et worker de processus | processus ou UI | désabonnements existants, pas drain universel par Core | pas tous liés aux services Core | NON VALIDÉ |
| AudioIO wake thread | audio, service vocal | AudioIO/Core | Core pour wrapper | signal, jointure, owner conservé si thread résistant | thread Python vérifié ; callbacks PortAudio non prouvés | NON VALIDÉ |
| Gemini Live watcher / stall / async | genai, websocket, asyncio et outils | provider Core | provider pour wrapper | shutdown provider, garde des appels suivis ; arrêt SDK non inspectable | services via bindings Core | NON VALIDÉ |
| PortAudio / ONNX / psutil / Qt / SDK Gemini | threads internes, pilotes, bibliothèques natives | bibliothèques externes | chaque bibliothèque ; Jarvis possède seulement ses wrappers | API explicites utilisées, pas de contrôle générique des threads internes | dépend du wrapper | NON VALIDÉ |
| Globals d'UI (états, dialogue, écriture), caches déclarations et historique système | UI, stockage local ou tests | modules / factories | processus, UI ou appelant | propre à chaque propriétaire | pas tous des services Core ; isolation totale non démontrée | COMPATIBILITÉ JUSTIFIÉE |

Le ResourceMonitor ne crée pas de doublon en cas de blocage mais une nouvelle souscription pendant le blocage ne relance pas automatiquement l'échantillonnage après la sortie. Le pool Dashboard peut conserver des threads inactifs après le seul arrêt Core tant que le Hub vit ; l'absence totale de thread UI après cet arrêt n'est pas démontrée. Voir `validation-boundary-final-01-5.md` pour les preuves et les limites.

## Audit de décision complémentaire — reprise sur 2b4b339

Cette section prime sur les qualificatifs antérieurs. L'analyse couvre les constructions recensées (`Thread`, timers, `asyncio`, futures, callbacks, getters, caches et chemins UI ci-dessous), mais pas l'intérieur opaque des SDK ni les chemins dynamiques non recensés. `OK_ARCHITECTURAL` se rapporte à la portée testée, pas à une perfection universelle. Un chemin critique `NOT_VALIDATED` ou `TO_CORRECT` empêche la clôture. Les API Qt et les champs du Hub supposent le thread Qt : ne pas appeler `DashboardHub.start/shutdown/subscribe_resources` depuis un thread arbitraire ; seul `JobRunner.submit/shutdown` est ici testé concurrentiellement.

| Réserve / accès | Thread, source, propriétaire / fin | Cycle, cache, raison et preuve | Classification |
|---|---|---|---|
| Registry services eager et lazy | Core ; `_lock`, registre propre à une session | identité unique, constructeur lazy sérialisé ; `close` inverse l'ordre et s'exécute une seule fois (tests Core). Ne possède que les objets avec callback explicitement enregistré. | OK_ARCHITECTURAL |
| Callback de fermeture du registre en échec | thread cleanup Core | `close` vide la liste et les services avant callbacks ; un échec est journalisé mais n'est pas rejouable automatiquement, même si fermeture réellement incomplète | TO_CORRECT |
| `status`, `activity_boundary`, `context`, `tools`, timers | Core/session ; status éventuellement injecté, context/dispatcher construits par Core | pas de fermeture des objets purs ; outils et context sous bail ; timers stoppés/attendus (tests Core et context) | OK_ARCHITECTURAL |
| Conversation + mémoire | factories Core, `conversation` en RAM et SQLite mémoire séparés ; getter/setter de processus compatibles | registre fournit la même instance à Hub et tools. Les objets mémoire/conversation n'ont pas de callback de close du registre ; les références peuvent survivre à Core et les setters externes concurrents divergent ; isolation universelle non prouvée | NOT_VALIDATED |
| Todo/routines/modes/activity/music | singletons historiques empruntés et initialisés à la demande, registre en cache | le Core ne détruit pas un service emprunté ; usages autonomes légitimes mais remplacement concurrent et mutation après arrêt non tous sous boundary | TO_CORRECT |
| Scheduler standard | Core ; thread dédié, services injectés avant démarrage standard | signal, jointure et rétention owner si encore vivant ; tests arrêt et callback bloqué | OK_ARCHITECTURAL |
| Scheduler factory custom | factory extérieure ; éventuellement thread démarré avant retour | injection post-factory, donc un callback antérieur peut utiliser autre service/default ; pas d'interdiction ni d'adaptateur contractuel | TO_CORRECT |
| TaskManager standard | Core ; une loop et thread, hooks, futures, magasin | création, refus après stop, annulation, `aclose`, jointure et persistance testés ; tâches bloquées conservent owner. Accès direct initié juste avant stop pas universellement sous bail | NOT_VALIDATED |
| TaskManager / factory custom pré-démarrée | appelant puis Core | factory peut commencer avant bind ; références externes et accès sans bail, pas de preuve universelle | TO_CORRECT |
| `ProtocolRun` injecté | lease Core réservé avant `Thread.start` ; thread et `_ACTIVE_RUN` global | durée de vie des services protégée et refus des demandes arrivant après stop ; le test `test_protocol_admission_is_atomic_even_when_thread_start_is_delayed` démontre aussi qu'un lancement déjà admis peut effectivement créer son thread après début stop. Incompatible avec la propriété stricte demandée, même si le owner est retenu | TO_CORRECT |
| `JobRunner` et Hub terminal | Hub ; executor pool et QCoreApplication | submit et `shutdown` sérialisés par `_lock`, erreur submit nettoie callback ; Hub ne redémarre plus après son shutdown, même avant start ; tests vrais threads et Hub QtCore | OK_ARCHITECTURAL |
| `JobRunner` face au stop du Core | Hub distinct de Core | bail de soumission puis bail du worker ; une soumission déjà admise peut démarrer son thread après `reject_new` ; arrêt Core seul ne ferme pas le pool Hub | TO_CORRECT |
| Hub shutdown / pool et sampler bloqués | Hub, non Core ; `shutdown(wait=False)`, stop sampler borné | pas d'attente bloquante Qt. Le test joint réellement les threads après arrêt non bloqué ; aucune garantie que tous soient morts au retour immédiat, ou lorsqu'un callback externe est bloqué ; pas de signal universel de fin complète | TO_CORRECT |
| ResourceMonitor réabonnement concurrent | Hub / psutil, `_lock` et signaux | thread bloqué reste référencé, pas de doublon ; retour d'abonné pendant blocage ne déclenche pas de reprise automatique ; callback déjà engagé peut survivre au signal | NOT_VALIDATED |
| Provider Gemini / `to_thread` | wrapper Core ; tâches asyncio et SDK externe | shutdown retardé garde owner testé ; SDK peut rester bloqué indéfiniment. Le SDK n'est pas tuable ni introspectable de force | NOT_VALIDATED |
| AudioIO / wake thread | wrapper Core, PortAudio/ONNX externes | signal, jointure du thread Python connu et rétention owner ; callbacks natifs et matériel hors preuve | NOT_VALIDATED |
| Worker nettoyage différé | Core ; thread créé après timeout d'arrêt | attendu tant qu'une ressource réelle persiste ; un blocage définitif peut le retenir indéfiniment, état FAILED et owner retenu | COMPATIBILITY_JUSTIFIED |
| Notifications Windows et ponts UI | processus / UI ; hooks et queue Windows | restent indépendants de Core mais cycle et callbacks Windows réels non exécutés ; aucune extinction universelle prouvée | NOT_VALIDATED |
| API historiques standalone / timers autonomes | appelant/processus séparé | explicitement hors session ; ne pas les migrer mécaniquement ; une activité autonome déjà lancée n'est pas suivie par Core | COMPATIBILITY_JUSTIFIED |
| ContextEngine | registre Core / sources éphémères ; mémoire persistante seulement sur demande | snapshots/éléments gelés, budget UTF-8/4, ordre/provenance/exclusions testés, subprocess bloquant Gemini/Qt et lectures `os.environ`/`getenv` ; coût estimé, pas tokenisation modèle | OK_ARCHITECTURAL |

### Inventaire des accès UI synchrones et caches

Les chemins ci-dessous sont appelés par slots/rendu Qt, sauf worker explicitement indiqué. Une acquisition synchrone et brève évite de rendre arbitrairement asynchrone une API UI ; elle ne doit pas bloquer sur un worker qui attend Qt. Les accès dont l'usage réel n'est pas détenu par un bail ne sont pas réputés sûrs par le seul getter.

| Appel / cache | Usage, durée, invalidation et risque | Classification |
|---|---|---|
| `DashboardHub.conversation_data`, `actions.conversation_new` | snapshot/reset sous bail durant toute l'utilisation, refus après arrêt ; cache `_conversation` appartient au Hub, détaché à son arrêt ; test conversation bloquée + identité | OK_ARCHITECTURAL |
| `DashboardHub.conversation()`, `_attach_conversation`, `_attach_tasks`, `_detach_tasks` | contrôle fermeture instantané mais hook/add/remove et références de manager sont conservés côté UI ; course entre lookup et cleanup non couverte par bail ; callbacks Qt différés | NOT_VALIDATED |
| Dashboard `TasksWidget._cancel/_retry` | appel Qt direct au TaskManager ; guards tardifs cancel/retry du manager liés à Core ; course d'une mutation commencée juste avant fermeture non protégée de bout en bout | NOT_VALIDATED |
| Dashboard `_tasks_active/_tasks_unread`, `HealthWidget`, `Controller.active_task_titles` | Qt lit `tasks.counts/list_tasks` synchrones via getter ; copies en mémoire protégées par verrous manager mais récupération/utilisation sans bail ; cache et destruction concurrente non testés | NOT_VALIDATED |
| Dashboard `SystemWidget._collect`, mémoire/todo/jobs | job exécuté sous bail ; UI reçoit des copies/signaux, `JobRunner` et son propriétaire Hub ont les limites ci-dessus | OK_ARCHITECTURAL |
| Orb `_memory_value`, `_menu_toggle_value`, `_routines_value`, `_refresh_routines_spec`, `_mode_suppresses_visuals` | Qt : getter puis appel séparé sans bail ; `_status_cache` garde uniquement chaînes pendant TTL, `_routines_cache` des valeurs rendues, `_routines_mtime` une date. UI thread attendu, invalidation TTL/mtime/toggle, pas de référence service ; course lookup/utilisation et valeur après arrêt non prouvées | NOT_VALIDATED |
| Orb `_menu_set_toggle` mémoire | Qt, bail explicite pour `set_enabled`, échec visible ; cache invalidé après succès ; mécanisme général des baux testé mais ce slot QtWidgets non exécuté | NOT_VALIDATED |
| Orb `_run_routine_async` | thread UI crée directement `jarvis-routine`, ensuite le worker cherche les tools ; aucune admission avant création, donc clic après stop peut créer un thread, même si dispatcher refuse ensuite | TO_CORRECT |
| Orb modes et ouverture dialogues `_service` | Qt, getter refuse fermeture déjà constatée ; récupération/usage ou construction des dialogues non atomiques avec stop | NOT_VALIDATED |
| `RoutinesDialog.refresh/_set_enabled/_run_routine` | QTimer/slot Qt, références `manager/scheduler` conservées ; toggle appelle `manager.update_routine` directement sans bail ni vérification shutdown, mutation persistante possible après fin Core ; worker UI non admis avant start | TO_CORRECT |
| `ModeAppsDialog` et `_ModeSection` | Qt, cache manager ; accès lecture/écriture directs, fermeture Core ne détruit pas l'emprunt mais isolation de session et accès tardifs ne sont pas établis | NOT_VALIDATED |
| `BackgroundTasksDialog` | Qt, manager conservé et hook, fermeture/reconstruction sur identité différente ; snapshots et guards propres manager, pas de bail universel autour des callbacks | NOT_VALIDATED |
| `MENU_SPECS` / `_DIALOG` / ponts UI | états globaux UI et références widgets, pas services Core en soi ; reconstruction/détachement au changement d'identité selon dialogue ; durées et callbacks globaux pas toutes prouvées | NOT_VALIDATED |
| `ResourceMonitor._history`, `Hub._engine`, `LogRingHandler` | copies bornées UI, verrous propres aux buffers/sampler ou thread Qt pour Hub, pas de références de services Core ; arrêt du sampler décrit séparément | COMPATIBILITY_JUSTIFIED |
| Getters/defaults hors Runtime, `system_extras._HISTORY`, déclaration routines | état historique / autonome, caches de déclarations ou données non propriétaires de session ; pas de migration uniquement parce que global | COMPATIBILITY_JUSTIFIED pour usage autonome ; injection concurrente traitée ci-dessus |

L'état du travail et les commandes/résultats réellement exécutés figurent dans `validation-boundary-final-01-5.md`. Les réserves critiques explicitement classées ci-dessus empêchent `ACCEPTED` ; un SDK ou callback bloqué ne peut pas être détruit de force en toute sécurité.

## Reprise six réserves — statut sur le code `2379a94`

Ces décisions priment sur les tableaux précédents. Le rapport de validation contient exactement une classification finale par réserve ; les statuts individuels ci-dessous précisent sa portée. Le point de linéarisation de l'arrêt est la fermeture de `ActivityBoundary`, synchronisée avec le démarrage effectif. Une attente bloquée à l'intérieur de `Thread.start`/`executor.submit` peut toutefois retenir la fermeture : propriété de non-création après transition ≠ garantie d'arrêt non bloquant.

| Élément et thread | Propriétaire / protection / arrêt | Classe individuelle | Limite |
|---|---|---|---|
| Orb `_run_routine_async` (Qt → worker) | Core injecté : `start_thread` prend un bail et utilise `launch` ; fallback autonome appartient à l'appelant | NOT_VALIDATED | corps AST et primitive testés ; widget Qt réel et tous les callbacks non exécutés |
| Catalogue `_run_routine`, `_set_enabled`, `refresh` (Qt → worker) | dépendances et boundary injectées depuis Orb/tray ; bail sur les accès synchrones, start atomique et bail sur worker | NOT_VALIDATED | les widgets et connexions Qt réels restent bloqués par `libGL`; singleton réinjecté uniquement si routine précédente inactive |
| ProtocolRun et JobRunner face à `reject_new` | Core protège lancement et baux ; Hub possède le pool | TO_CORRECT | T0 admission/T1 fermeture/T2 lancement forcés sur les deux voies sont refusés, mais si la création est déjà dans la section critique elle retient l'arrêt et peut finir après la demande ; propriété stricte dès la demande non démontrée |
| RuntimeTimers | Core ; `launch` puis annulation et attente | NOT_VALIDATED | passage à la primitive, tests timers existants mais interleaving T0/T1/T2 non reproduit spécifiquement pour le Timer |
| TaskManager standard / scheduler | Core signale et joint threads connus ; manager protège ses propres mutations et `_closing` | NOT_VALIDATED | factory custom pré-démarrée et certains interleavings d'initialisation/fermeture restent hors preuve ; ne pas conclure zéro création universelle |
| Hub / JobRunner / ResourceMonitor | Hub terminal, `shutdown_status` mesure threads pool/sampler et livraisons JobRunner | NOT_VALIDATED | blocage possible sur locks/hooks pendant shutdown, signaux Qt externes et SDK hors compteur ; `complete` est limité aux ressources contrôlées |
| Hub cache conversation/tasks et dialogues modes/tâches | Qt, accès via registre et caches/hook détachés par Hub | NOT_VALIDATED | certains usages synchrones ou callbacks déjà en vol ne détiennent pas de bail complet ; QtWidgets indisponible |
| Références/singletons empruntés et factories custom | propriété historique externe puis emprunt Core | TO_CORRECT | démarrage avant injection et mutation concurrente possibles ; fermer un emprunt comme propriété exclusive serait incorrect |
| `ServiceRegistry.close` | propriétaire Core ; callback exécuté au plus une fois | NOT_VALIDATED | échec et in-progress persistants, pas de retry automatique ; Core peut libérer owner après échec malgré état inconnu des dépendances externes |
| Provider Gemini / SDK externe | provider Core, SDK propriétaire de ses internals | NOT_VALIDATED | STOPPING conserve owner tant que shutdown coroutine ne rend pas ; pas de timeout général ni interruption forcée des internals |
| ContextEngine | Core/session, sources explicitement demandées | OK_ARCHITECTURAL | snapshots/budget/provenance et séparation mémoire testés ; test offline interdit provider/Qt et lectures d'environnement, pas une preuve d'API externe |
| Historique autonome non injecté | appelant / processus, hors Runtime | COMPATIBILITY_JUSTIFIED | frontière volontaire séparée ; ne prouve pas isolation lorsqu'une référence est partagée avec Core |
