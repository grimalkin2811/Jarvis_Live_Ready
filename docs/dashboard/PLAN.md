# Dashboard — plan court (rédigé après l'audit, avant l'implémentation)

## Ce qui est réutilisé (aucun système concurrent)

| Besoin | Système existant réutilisé |
|---|---|
| Conversation écrite | `GeminiLive` (même session Live) + `ConversationContext` partagé (`src/conversation.py`) |
| État du moteur | Faits publiés par `_run_voice_loop` (`src/ui.py`) dans un tableau d'état thread-safe |
| Tâches | `TaskManager` v1.8 (hooks, `cancel_task`, `retry_task`, `get_task_result`) |
| Mémoire | `MemoryManager` SQLite (même base que la voix) + migration de schéma v2 additive |
| Notifications | `src.notifications` + `UI/notification_bridge.py` (zone de notification) |
| Journal | logger `jarvis` (`src/logging_setup.py`) + journal d'activité optionnel |
| Interfaces | `InterfaceModeController` (orbe / Desktop), `INTERFACE_MODE` |
| Micro / interruption | pont `menu_state.LIVE` (`set_mic_enabled`, `request_stop_speaking`) |
| Notes / tâches perso | `tools.take_note/read_notes`, `TodoManager` |
| Ressources | `psutil` (dépendance existante) |
| Persistance | `src.json_state.save_dict` (écriture atomique), `paths.ui_state_dir()` |
| Mises à jour | `src.updater.build_update_plan` |

## Modules créés

* `src/runtime_status.py` — tableau d'état runtime (Qt-free) + point d'entrée texte.
* `UI/dashboard_bridge.py` — pont thread-safe des commandes (voix, raccourcis).
* `UI/dashboard/` — paquet du Dashboard (modules sans Qt : config, grille,
  préréglages, sécurité, services ; modules Qt : hub, thèmes, cadres, grille,
  fenêtre, contrôleur, widgets).

## Contrats

* Widgets : `WidgetSpec` (déclaratif) + `DashboardWidget` (cycle de vie
  `activate/deactivate/dispose`, libération automatique des abonnements et
  minuteurs, états `loading/ready/empty/error/unavailable`).
* Moteur : `GeminiLive.submit_text` / `submit_text_threadsafe` (ajout,
  chemin audio inchangé tant qu'aucun texte n'est envoyé).
* Contexte : `ConversationContext.add_listener` (notifications hors verrou).
* Mémoire : méthodes d'administration à contrôle de version optimiste.

## Préférences

`paths.ui_state_dir()/dashboard_state.json`, schéma versionné (v1), migrations,
validation champ par champ, sauvegarde du fichier corrompu avant reprise,
clés inconnues préservées.

## Risques de régression identifiés

1. Chemin audio Gemini Live (porte micro) → ajout strictement additif, testé.
2. Démarrage de l'interface → contrôleur paresseux (aucune fenêtre, aucun
   import lourd) ; mesuré.
3. Notifications en double (fin de tâche) → filtre explicite sur le relais natif.
4. Schéma mémoire → migration additive, testée sur base v1 réaliste.
5. Raccourci `Ctrl+D` de l'orbe → la touche `D` sans modificateur garde son rôle.
6. Charge CPU des animations → niveaux, mesures, arrêt fenêtre masquée.

## Tests prévus

Unitaires (config, grille, sécurité, état runtime, écouteurs, centre de
notifications, service mémoire, ressources), intégration (moteur texte sur
faux serveur Live, fenêtre/contrôleur, widgets, tâches réelles avec faux
modèles, points d'entrée), visuel (captures hors écran), performance
(script reproductible + garde-fous), non-régression (suite existante complète).

## Points de validation Windows (non exécutables dans le bac à sable Linux)

Bulles natives de la zone de notification, plein écran multi-écrans,
préférence « animations Windows » (SPI_GETCLIENTAREAANIMATION), conversation
écrite avec la vraie session Gemini Live (réponse audio + transcription),
micro/haut-parleurs réels, rendu des polices Segoe UI, mise à l'échelle DPI.
