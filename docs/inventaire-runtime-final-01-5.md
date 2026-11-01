# Inventaire statique des dépendances et états de processus

Généré par `python scripts/inventory_runtime.py`. Les fonctions/méthodes indiquent le consommateur.
La recherche AST couvre ces noms et constructions explicites, pas les appels dynamiques, alias ou callbacks indirects ; voir les tableaux de responsabilités et exceptions dans `validation-finale-lot-01-5.md`.
Un getter historique reste une compatibilité à migrer, pas une preuve que le registre possède le service.

| Fichier:ligne | Consommateur | Service | Appel | Origine | Risque | Migration / raison de conservation |
|---|---|---|---|---|---|---|
| `src/activity.py:295` | `get_default_activity_log` | activity | `ActivityLog` | construction directe | nouvelle instance ; vérifier propriétaire et appelants | examiner propriétaire avant de remplacer ou retirer |
| `src/activity.py:308` | `log_action` | activity | `get_default_activity_log` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/background_tasks/manager.py:488` | `configure_default_task_manager` | tasks | `TaskManager` | construction directe | nouvelle instance ; vérifier propriétaire et appelants | examiner propriétaire avant de remplacer ou retirer |
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
| `src/core/context.py:171` | `ContextEngine.build` | conversation | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/context.py:194` | `ContextEngine.build` | memory | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/context.py:230` | `ContextEngine.build` | status | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:64` | `_default_todo` | todo | `get_default_todo_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | compatibilité des factories ; supprimer après migration des propriétaires |
| `src/core/runtime.py:69` | `_default_routines` | routines | `get_default_routine_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | compatibilité des factories ; supprimer après migration des propriétaires |
| `src/core/runtime.py:74` | `_default_modes` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | compatibilité des factories ; supprimer après migration des propriétaires |
| `src/core/runtime.py:79` | `_default_activity` | activity | `get_default_activity_log` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | compatibilité des factories ; supprimer après migration des propriétaires |
| `src/core/runtime.py:84` | `_default_music` | music | `get_default_music_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | compatibilité des factories ; supprimer après migration des propriétaires |
| `src/core/runtime.py:101` | `CoreRuntime.__init__` | status | `register` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:102` | `CoreRuntime.__init__` | context | `register` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:105` | `CoreRuntime.__init__` | todo | `register_lazy` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:106` | `CoreRuntime.__init__` | routines | `register_lazy` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:107` | `CoreRuntime.__init__` | modes | `register_lazy` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:108` | `CoreRuntime.__init__` | activity | `register_lazy` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:109` | `CoreRuntime.__init__` | music | `register_lazy` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:339` | `CoreRuntime._run_pipeline` | memory | `register` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:344` | `CoreRuntime._run_pipeline` | conversation | `register` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:349` | `CoreRuntime._run_pipeline` | scheduler | `register` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:355` | `CoreRuntime._run_pipeline` | tasks | `register` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:367` | `CoreRuntime._run_pipeline` | audio | `register` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:394` | `CoreRuntime._run_pipeline` | tools | `register` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/core/runtime.py:417` | `CoreRuntime._run_pipeline` | provider | `register` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/gemini_live.py:326` | `GeminiLive.__init__` | conversation | `get_default_conversation_context` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/gemini_live.py:1340` | `GeminiLive._connect_once` | modes | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/gemini_live.py:1340` | `GeminiLive._connect_once` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/main.py:238` | `run_headless` | runtime | `CoreRuntime` | construction directe | nouvelle instance ; vérifier propriétaire et appelants | examiner propriétaire avant de remplacer ou retirer |
| `src/memory.py:1119` | `get_default_memory_manager` | memory | `MemoryManager` | construction directe | nouvelle instance ; vérifier propriétaire et appelants | examiner propriétaire avant de remplacer ou retirer |
| `src/modes.py:1027` | `get_default_mode_manager` | modes | `JarvisModeManager` | construction directe | nouvelle instance ; vérifier propriétaire et appelants | examiner propriétaire avant de remplacer ou retirer |
| `src/music/manager.py:351` | `get_default_music_manager` | music | `MusicManager` | construction directe | nouvelle instance ; vérifier propriétaire et appelants | examiner propriétaire avant de remplacer ou retirer |
| `src/protocols.py:151` | `_probe_memory_bank` | memory | `get_default_memory_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/protocols.py:165` | `_probe_routines` | routines | `get_default_routine_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/protocols.py:177` | `_probe_reminders` | scheduler | `get_default_scheduler` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/routine_actions.py:16` | `_scheduler` | scheduler | `get_default_scheduler` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/routine_actions.py:16` | `_scheduler` | scheduler | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/routines.py:828` | `get_default_routine_manager` | routines | `RoutineManager` | construction directe | nouvelle instance ; vérifier propriétaire et appelants | examiner propriétaire avant de remplacer ou retirer |
| `src/scheduler.py:66` | `_notifications_suppressed_by_mode` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/scheduler.py:183` | `Scheduler._routines` | routines | `get_default_routine_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/scheduler.py:620` | `get_default_scheduler` | scheduler | `Scheduler` | construction directe | nouvelle instance ; vérifier propriétaire et appelants | examiner propriétaire avant de remplacer ou retirer |
| `src/scheduler.py:632` | `start_default_scheduler` | scheduler | `get_default_scheduler` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
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
| `src/tools.py:1818` | `remember` | memory | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1828` | `recall` | memory | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1833` | `list_memories` | memory | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1838` | `search_memories` | memory | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1843` | `update_memory` | memory | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1853` | `delete_memory` | memory | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1858` | `forget` | memory | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1863` | `clear_memory` | memory | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1873` | `create_routine` | routines | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1883` | `run_routine` | routines | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1886` | `run_routine` | tools | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/tools.py:1891` | `list_routines` | routines | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1896` | `describe_routine` | routines | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1901` | `update_routine` | routines | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1912` | `delete_routine` | routines | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1929` | `activate_focus_mode` | modes | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1937` | `activate_game_mode` | modes | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1945` | `disable_jarvis_mode` | modes | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1950` | `get_jarvis_mode` | modes | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1960` | `list_mode_applications` | modes | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1969` | `set_mode_applications` | modes | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1979` | `toggle_mode_application` | modes | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:1984` | `reset_mode_applications` | modes | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2231` | `reset_conversation` | conversation | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2255` | `get_conversation_state` | conversation | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2275` | `set_reminder` | scheduler | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2285` | `list_reminders` | scheduler | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2290` | `cancel_reminder` | scheduler | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2377` | `_task_manager` | tasks | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2472` | `add_todo` | todo | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2476` | `list_todos` | todo | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2480` | `complete_todo` | todo | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2484` | `reopen_todo` | todo | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2488` | `delete_todo` | todo | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2492` | `clear_todos` | todo | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2496` | `get_activity_log` | activity | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2500` | `clear_activity_log` | activity | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2706` | `_guard_tool_function._wrapped` | modes | `_resolve` | registre injecté ou fallback autonome | injection explicite si présente ; getter historique sinon | injecté pour les appels Core ; compatibilité hors Core |
| `src/tools.py:2720` | `_guard_tool_function._wrapped` | activity | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/ui.py:300` | `InterfaceModeController.activate_current` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/ui.py:302` | `InterfaceModeController.activate_current` | modes | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/ui.py:392` | `_start_voice` | runtime | `CoreRuntime` | construction directe | nouvelle instance ; vérifier propriétaire et appelants | examiner propriétaire avant de remplacer ou retirer |
| `src/ui.py:534` | `_build_tray_icon._show_routines_if_allowed` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/ui.py:536` | `_build_tray_icon._show_routines_if_allowed` | modes | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/ui.py:543` | `_build_tray_icon._show_routines_if_allowed` | routines | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/ui.py:544` | `_build_tray_icon._show_routines_if_allowed` | scheduler | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/ui.py:556` | `_build_tray_icon._show_background_tasks` | tasks | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/ui.py:573` | `_build_tray_icon._show_desktop_appearance` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/ui.py:575` | `_build_tray_icon._show_desktop_appearance` | modes | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/ui.py:595` | `_build_tray_icon._start_protocol_if_allowed` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injectable ; conservé pour les appelants historiques |
| `src/ui.py:597` | `_build_tray_icon._start_protocol_if_allowed` | modes | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `src/ui.py:718` | `run_ui` | runtime | `CoreRuntime` | construction directe | nouvelle instance ; vérifier propriétaire et appelants | examiner propriétaire avant de remplacer ou retirer |
| `UI/background_tasks_dialog.py:145` | `show_background_tasks_dialog` | tasks | `get_default_task_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/boot_sequence.py:506` | `ProtocolPresenter._visuals_suppressed` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/boot_sequence.py:508` | `ProtocolPresenter._visuals_suppressed` | modes | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `UI/dashboard/actions.py:94` | `conversation_new` | conversation | `get_default_conversation_context` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/dashboard/actions.py:140` | `routine_names` | routines | `get_default_routine_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/dashboard/actions.py:154` | `run_routine` | routines | `get_default_routine_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/dashboard/actions.py:232` | `build_actions._run` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
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
| `UI/desktop/overlay.py:835` | `DesktopOverlayController._suppressed` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/desktop/overlay.py:837` | `DesktopOverlayController._suppressed` | modes | `require` | registre injecté | dépendance indisponible après fermeture ou avant initialisation | migré ; disponibilité et arrêt à gérer |
| `UI/jarvis_menu.py:331` | `_routine_names` | routines | `get_default_routine_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/jarvis_menu.py:378` | `MorphingOrbWidget._service` | memory | `get_default_memory_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/jarvis_menu.py:381` | `MorphingOrbWidget._service` | tasks | `get_default_task_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/jarvis_menu.py:384` | `MorphingOrbWidget._service` | routines | `get_default_routine_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/jarvis_menu.py:387` | `MorphingOrbWidget._service` | scheduler | `get_default_scheduler` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/jarvis_menu.py:390` | `MorphingOrbWidget._service` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/mode_apps_dialog.py:46` | `_ModeSection.__init__` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/mode_apps_dialog.py:143` | `ModeAppsDialog.__init__` | modes | `get_default_mode_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/routines_dialog.py:30` | `RoutinesDialog.__init__` | routines | `get_default_routine_manager` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |
| `UI/routines_dialog.py:34` | `RoutinesDialog.__init__` | scheduler | `get_default_scheduler` | getter global historique | instance partagée ou création paresseuse ; setter peut diverger | injecter au point d’entrée ; fallback autonome conservé si présent |

## États globaux recensés

| Fichier:ligne | État |
|---|---|
| `src/activity.py:280` | `_DEFAULT_LOG` |
| `src/background_tasks/manager.py:481` | `_DEFAULT` |
| `src/conversation.py:1025` | `_DEFAULT` |
| `src/memory.py:1101` | `_DEFAULT_MANAGER` |
| `src/modes.py:1012` | `_DEFAULT_MANAGER` |
| `src/music/manager.py:39` | `_DEFAULT` |
| `src/notifications.py:18` | `_HOOKS` |
| `src/notifications.py:19` | `_WINDOWS_QUEUE` |
| `src/notifications.py:20` | `_WINDOWS_WORKER` |
| `src/protocols.py:427` | `_ACTIVE_RUN` |
| `src/protocols.py:428` | `_LISTENERS` |
| `src/routines.py:93` | `_TOOL_FUNCTIONS` |
| `src/routines.py:94` | `_TOOL_DECLARATIONS` |
| `src/routines.py:813` | `_DEFAULT_MANAGER` |
| `src/runtime_status.py:367` | `RUNTIME` |
| `src/scheduler.py:605` | `_DEFAULT_SCHEDULER` |
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
| `UI/routines_dialog.py:293` | `_DIALOG` |
| `UI/visibility_bridge.py:179` | `VISIBILITY` |
