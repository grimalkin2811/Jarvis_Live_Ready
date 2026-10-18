# Dashboard — architecture

Le Dashboard est la **troisième interface** de Jarvis. Il coexiste avec l'orbe
(Blob) et le Desktop Mode : ouvrir, fermer ou masquer le Dashboard ne change
pas le mode orbe/Desktop, ne redémarre pas le moteur vocal, ne crée pas de
nouvelle conversation et n'interrompt ni le micro ni les tâches.

## Vue d'ensemble

```
            boucle vocale (thread asyncio, src/ui.py::_run_voice_loop)
   GeminiLive ── AudioIO ── TaskManager ── MemoryManager ── ConversationContext
        │  faits publiés                       │ hooks             │ écouteurs
        ▼                                      ▼                   ▼
 src/runtime_status.RUNTIME  ◄── submit_text (thread-safe) ──┐
        │ hooks (tout thread)                                │
        ▼                                                    │
 UI/dashboard/hub.DashboardHub (thread Qt) ── signaux Qt ──► widgets
        ▲  présence / outils / transcriptions / niveaux      │
        │  (fan-out des hooks existants dans run_ui)          │
 UI/dashboard/controller.DashboardController ─ fenêtre ─ pages ─ cadres ─ widgets
        ▲            ▲                 ▲
   icône tray   UI/dashboard_bridge   Ctrl+D (orbe), --dashboard
                (outils open_dashboard / close_dashboard)
```

## Modules

| Module | Qt | Rôle |
|---|---|---|
| `src/runtime_status.py` | non | État moteur publié par la boucle vocale (prêt, reconnexion, clé refusée, audio, mot d'éveil…), synthèse **opérationnel / dégradé / indisponible / inconnu** (jamais « opérationnel » sans preuve), masquage des secrets, point d'entrée texte. |
| `UI/dashboard_bridge.py` | non | Commandes `open/close/toggle` depuis n'importe quel thread ; état d'affichage rapporté. |
| `UI/dashboard/config.py` | non | Préférences versionnées, migrations, validation, reprise, préréglages, pages. |
| `UI/dashboard/layout_model.py` | non | Pages, placements, grille 12 colonnes, collisions, compaction, projection étroite/normale. |
| `UI/dashboard/presets.py` | non | Cockpit, Productivité, Diagnostic (+ « Personnalisée » = instantané utilisateur). |
| `UI/dashboard/security.py` | non | Niveaux 1/2/3, préférences abaissables dans les limites. |
| `UI/dashboard/notification_center.py` | non | Catégories, canaux (Dashboard / natif), anti-doublons, libellé « Dashboard — ». |
| `UI/dashboard/memory_service.py` | non | Validation, contrôle de version, diff, restauration (sur `MemoryManager`). |
| `UI/dashboard/tasks_service.py` | non | Façade du `TaskManager` (actions réellement supportées seulement). |
| `UI/dashboard/notes_service.py` | non | Notes (`take_note`), documents réels (`user_content`), tâches perso. |
| `UI/dashboard/resources.py` | non | Mesures psutil dans un thread démarré à la demande. |
| `UI/dashboard/diagnostics.py` | non | Vérifications par composant avec preuves, rapport sans secret. |
| `UI/dashboard/actions.py` | non | Actions rapides réelles (aucune commande système arbitraire). |
| `UI/dashboard/log_buffer.py` | non | Tampon circulaire du logger `jarvis` (secrets masqués). |
| `UI/dashboard/async_jobs.py` | oui | Travaux hors thread graphique, résultat livré par signal ; `watch_future` relaie un `Future` résolu sur n'importe quel thread (confirmation d'envoi du moteur) sans jamais émettre depuis un widget détruit. |
| `UI/dashboard/hub.py` | oui | État réactif partagé, branchement des sources. |
| `UI/dashboard/theme.py`, `painting.py` | oui | Identités JARVIS / EDITH / FRIDAY, peinture, cache. |
| `UI/dashboard/widget_api.py`, `registry.py` | oui | Contrat et registre explicite des widgets. |
| `UI/dashboard/frame.py`, `grid_view.py`, `pages_view.py`, `window.py` | oui | Cadres, grille éditable, pages / OnePage, fenêtre. |
| `UI/dashboard/dialogs.py` | oui | Confirmations, bibliothèque, réglages, arrêt complet. |
| `UI/dashboard/controller.py` | oui | Cycle de vie, points d'entrée, modes de fermeture. |
| `UI/dashboard/widgets/` | oui | 20 widgets intégrés. |

## Modifications du code existant (toutes additives)

* `src/gemini_live.py` : `submit_text`, `submit_text_threadsafe`,
  `engine_snapshot`, porte micro `_text_turn_pending` (fausse par défaut :
  chemin audio inchangé sans texte), consigne système pour `open_dashboard`.
* `src/conversation.py` : écouteurs de changement (`add_listener`).
* `src/memory.py` : migration v2 (historique) + méthodes d'administration.
* `src/ui.py` : publication des faits dans `RUNTIME`, enregistrement du point
  d'entrée texte, fan-out des hooks vers le Dashboard, entrée du menu de la
  zone de notification, création/arrêt du contrôleur.
* `src/main.py` : option `--dashboard` ; smoke test étendu.
* `src/tools.py` : outils `open_dashboard`, `close_dashboard`.
* `UI/jarvis_menu.py` : `Ctrl+D` dans l'orbe.
* `UI/notification_bridge.py` : filtres d'affichage natif (anti-doublon).
* `packaging/jarvis.spec` : inclusion du paquet `UI.dashboard`.
* `requirements.txt` : exclusion de PySide6 6.12 (bogue de comptage de
  références, voir `tests/test_pyside_refcount.py`).

## Fil d'exécution et non-blocage

* Le thread graphique ne fait **aucune** E/S bloquante : SQLite, disque,
  Task Manager, routines, rapport de diagnostic, vérification de mise à jour
  passent par `JobRunner` (pool de 3 threads), résultats livrés par signal.
* Les hooks backend (Task Manager, `RUNTIME`, contexte, journal, notifications)
  n'émettent que des signaux Qt en file d'attente ; aucune exception ne
  remonte vers la boucle vocale.
* L'envoi de texte renvoie un `concurrent.futures.Future` résolu sur la boucle
  du moteur ; toutes les vérifications d'état ont lieu sur cette boucle.
* Le contrôleur n'importe ni `google.genai` ni les modules de widgets au
  démarrage (registre construit à la première ouverture).

## État partagé, sans duplication

Aucun widget ne stocke de donnée métier : il lit le hub et ses services, qui
interrogent la source réelle (contexte, base, Task Manager…). Deux instances
d'un même widget (même page ou pages différentes) affichent la même source et
reçoivent les mêmes signaux (testé).

## Dépendances

Aucune nouvelle dépendance. `psutil` et `PySide6` étaient déjà requis ; la
seule modification est la borne `PySide6<6.12` (stabilité).
