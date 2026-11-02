# Inventaire statique des dépendances et états de processus

Généré par `python scripts/inventory_runtime.py`. Les fonctions/méthodes indiquent le consommateur.
La recherche AST couvre ces noms et constructions explicites, pas les appels dynamiques, alias ou callbacks indirects ; voir les tableaux de responsabilités et exceptions dans `validation-finale-lot-01-5.md`.
Un getter historique reste une compatibilité à migrer, pas une preuve que le registre possède le service.

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
| `protocols._ACTIVE_RUN`, `_LISTENERS`, `ProtocolRun.thread` | état de processus, callbacks et thread protocole | si services injectés, corps suivi via dispatcher ; pas de garantie pour console autonome. Signal de succès peut précéder le rejet à la fermeture. |
| `UI.dashboard.async_jobs.JobRunner`, `DashboardHub.jobs` | executor local au hub, jobs asynchrones consultent les services | bail acquis sur thread d'exécution ; en file après fermeture rejeté avant accès ; hub arrête son executor sans bloquer ; tests QtCore réels. Les callbacks Qt et accès synchrones ne sont pas tous sous bail. |
| `UI.dashboard.resources.ResourceMonitor._thread` | prélèvement psutil et abonnés, propriétaire hub, pas de service Core requis | arrêt/jointure bornée existants ; référence effacée même si sampler bloqué, doublon possible lors d'une reprise ; non corrigé ni certifié. |
| `UI.routines_dialog._DIALOG`, `jarvis_menu._run_routine_async` | dialogues et threads UI, runner injecté ; ancien dialogue autonome peut conserver une dépendance obsolète | nouvelle injection réaffectée si inactif, refus si routine précédente active ; fonctions Core suivies ; fallback autonome ne l'est pas. QtWidgets non validé. |
| `UI.background_tasks_dialog._DIALOG`, `UI.mode_apps_dialog._DIALOG` | hooks/dialogues process ; ancienne instance peut garder mauvais manager | fermeture/détachement puis reconstruction en cas de nouvelle identité ; QtWidgets non validé. Autres `_DIALOG` : vues locales, pas de propriété globale universelle prouvée. |
| `notifications._HOOKS/_WINDOWS_QUEUE/_WINDOWS_WORKER`, `desktop.events.DESKTOP_EVENTS`, ponts `DASHBOARD/INTERFACE_MODE/VISIBILITY`, `menu_state.LIVE` | bus, états d'UI et worker Windows de processus, pas des managers Core | propriétaires historiques conservés ; désabonnements existants, pas d'attente de tous callbacks/worker Windows par Core ; non certifié. |
| `system_extras._HISTORY`, `writing.service._writer_override`, `routines._TOOL_FUNCTIONS/_TOOL_DECLARATIONS` | presse-papiers RAM, override tests, caches déclarations | globaux historiques distincts de Context ; pas de preuve d'isolation totale ; override n'est pas un moteur propriétaire. |
| QTimers (`UI/*`, Dashboard, Desktop, `notification_bridge`) | événements sur boucle Qt, propriétaires widgets/hub/contrôleurs ; certains callbacks accèdent aux services | destruction/stop côté UI, non tous inclus dans barrière ; courses de callbacks tardifs et accès synchrones restent à vérifier graphiquement. |
| `src.ui._start_voice`, thread voix | lance et possède boucle Core ; stop par événement/`request_stop` | jointure UI bornée existante ; cleanup différé peut continuer après fin du thread voix ; pas d'attente indéfinie dans UI. |

La collecte AST est indicative, non une preuve exhaustive des imports dynamiques, fermetures, SDK, drivers natifs, ni d'une autorisation de sécurité. Les tests ciblés n'exécutent pas tous les appelants de chaque ligne.
