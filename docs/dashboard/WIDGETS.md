# Widgets — contrat, cycle de vie, liste

Code : `UI/dashboard/widget_api.py` (contrat), `UI/dashboard/registry.py`
(registre explicite), `UI/dashboard/frame.py` (cadre commun),
`UI/dashboard/widgets/` (implémentations).

Il n'y a **pas de système de plugins** : chaque widget est déclaré
explicitement dans `registry._builtin_specs()`. Pour en ajouter un, voir
[AJOUTER_UN_WIDGET.md](AJOUTER_UN_WIDGET.md).

## 1. Le contrat : `WidgetSpec` (déclaratif)

Un `WidgetSpec` décrit un type de widget **sans l'instancier** (la
bibliothèque du mode édition et les tests le lisent tel quel).

| Champ | Rôle |
|---|---|
| `type_id` | Identifiant **stable** `domaine.nom` (ex. `tasks.background`). Persisté dans les préférences : ne jamais le renommer. |
| `title`, `description`, `icon` | Affichage (bibliothèque, cadre). |
| `category` | Une des catégories de `widget_api.CATEGORIES` (jarvis, conversation, taches, memoire, productivite, systeme, parametres, personnalise). |
| `factory(context, settings)` | Construit la vue (`DashboardWidget`). |
| `min_size`, `default_size`, `max_size` | Contraintes de mise en page, en cellules de la grille 12 colonnes (`1 ≤ min ≤ défaut ≤ 12×24`). |
| `capabilities` | Parmi `realtime`, `actions`, `settings`, `multi_instance`, `editor`, `graph`. |
| `dependencies` | Services requis : `engine`, `tasks`, `memory`, `psutil`, `todo`, `notes`. |
| `settings` | Réglages **par instance** (`SettingField` : `bool`, `int` borné, `choice`, `text` borné), validés à chaque lecture ; les clés inconnues sont conservées. |
| `max_instances_per_page` | 1 par défaut ; `multi_instance` exige ≥ 2. |
| `actions` | Identifiants des politiques de sécurité des actions proposées (voir [SECURITE.md](SECURITE.md)). |

`WidgetSpec.contract_errors()` vérifie la cohérence ; `WidgetRegistry.register`
refuse un spec invalide ou un `type_id` dupliqué. Le test
`tests/test_dashboard_widgets.py` parcourt automatiquement tout le registre :
contrat, création, activation, désactivation, libération.

## 2. La vue : `DashboardWidget` et son cycle de vie

```
__init__(context, settings)   construction LÉGÈRE : ni minuteur, ni abonnement, ni E/S
        │
activate()  ──► on_activate()     le widget devient visible : self.listen(...), self.every(...),
        │                          chargement via self.run_job(...) (hors thread graphique)
deactivate() ─► on_deactivate()    page masquée, fenêtre fermée, section OnePage hors champ :
        │                          abonnements et minuteurs LIBÉRÉS AUTOMATIQUEMENT
apply_settings(settings) ─► on_settings_changed()   réglages d'instance modifiés
        │
dispose()   ──► on_dispose()       destruction définitive (désactive d'abord)
```

Garanties apportées par la classe de base :

* `listen(signal, slot)` et `every(ms, callback)` enregistrent ce qu'ils
  créent ; `deactivate()` déconnecte et détruit tout. Un widget inactif ne
  consomme donc rien (vérifié par `performance_snapshot()` : 0 widget actif,
  0 minuteur actif quand le Dashboard est masqué).
* `run_job(fn, on_done=…, on_error=…)` exécute `fn` dans le pool
  `JobRunner` (3 threads) ; les rappels arrivent dans le thread Qt et sont
  **ignorés si le widget a été détruit** entre-temps.
* Une exception dans `on_activate`/`apply_settings` met le widget en état
  `error` au lieu de faire tomber le Dashboard.
* `activate()`/`deactivate()` sont idempotents.

Qui appelle le cycle de vie ? `GridCanvas` (un par page) active les widgets
de la page visible ; `OnePageView` n'active que les sections proches de la
zone visible (marge de 200 px) ; la fenêtre désactive tout quand elle est
masquée ou réduite.

## 3. États d'affichage

`set_state(state, message)` avec `state` parmi :

| État | Signification | Affichage |
|---|---|---|
| `loading` | Données en cours de chargement. | Voile du cadre « Chargement… ». |
| `ready` | Données affichées. | Le contenu du widget. |
| `empty` | Source joignable mais vide. | Le widget reste utilisable (ex. champ d'ajout) et affiche lui-même un état vide explicite (ex. « Aucune tâche personnelle en attente. »). |
| `error` | Erreur réelle de la source. | Voile « Erreur » + message + bouton « Réessayer » (`refresh()`). |
| `unavailable` | Dépendance absente (moteur arrêté, Task Manager non démarré…). | Voile « Indisponible » + raison réelle. |

Un widget peut restreindre les états qui masquent son contenu
(attribut `overlay_states`).

Aucun widget n'affiche de valeur inventée : sans donnée, il passe en
`empty` ou `unavailable` avec la raison.

## 4. Contexte injecté : `WidgetContext`

Un widget n'accède jamais à un état global : il reçoit un `WidgetContext`
(`hub`, `theme_manager`, `instance_id`, `page_id`, `confirm(action_id, résumé,
détails)`, `toast(message, niveau)`, `open_page(page_id)`, `open_path(chemin)`,
`controller`). `confirm` applique la politique de sécurité (niveaux 1/2/3).

## 5. État partagé sans duplication

Les données métier ne vivent **jamais** dans un widget : il lit le hub
(`UI/dashboard/hub.py`) et ses services, qui interrogent la source réelle
(`ConversationContext`, base SQLite de la mémoire, `TaskManager`,
`TodoManager`, psutil…). Deux instances d'un même widget, sur la même page ou
sur deux pages, affichent la même source et reçoivent les mêmes signaux ;
un changement côté moteur (outil vocal, tâche, extraction mémoire) se
reflète partout. Testé dans `tests/test_dashboard_widgets.py` et
`tests/test_dashboard_tasks.py`.

Retirer ou masquer un widget ne supprime **aucune** donnée métier : seul le
placement (position, taille, réglages d'instance) est retiré de la page.

## 6. Les 20 widgets intégrés

| `type_id` | Titre | Dépendances | Actions (politiques) | Notes |
|---|---|---|---|---|
| `jarvis.state` | État de Jarvis | engine | — | Opérationnel / dégradé / indisponible / inconnu, avec preuve par composant (moteur, audio, mot d'éveil, mémoire, tâches). « Opérationnel » exige moteur prêt **et** audio démarré. |
| `jarvis.conversation` | Conversation | engine | `conversation.new`, `voice.stop` | Même session et même contexte que la voix ; voir ci-dessous. Réglages : actions affichées, nombre de messages. |
| `jarvis.audio` | Audio en direct | engine | `mic.toggle`, `voice.stop` | Niveaux issus du pipeline existant (aucune seconde capture), présence, état du micro. |
| `jarvis.models` | Modèles IA et routage | engine, tasks | — | Modèle conversationnel publié par la session (sinon « configuré par défaut — non confirmé »), routage automatique et quotas tels que rapportés par le Task Manager (`TasksService.model_status`). **Sélecteur manuel visible mais verrouillé.** |
| `jarvis.quick_actions` | Actions rapides | — | micro, interruption, nouvelle conversation, orbe/Desktop, dossiers, routines, protocoles | Une action indisponible est affichée grisée avec sa raison. |
| `tasks.background` | Tâches d'arrière-plan | tasks | `task.cancel`, `task.retry`, `task.open_result` | Statuts QUEUED, RUNNING, WAITING_FOR_TOOL, COMPLETED, FAILED, CANCELLED ; actions selon le statut (annuler si active ; relancer si échouée/annulée ; voir / ouvrir les fichiers si terminée). **Pas de pause/reprise** (non supporté par le Task Manager). Hooks réactifs. |
| `tasks.todo` | Tâches personnelles | todo | `todo.add`, `todo.complete` | Même liste que « ajoute une tâche » à la voix. |
| `memory.browser` | Mémoire | memory | `memory.create/update/delete/restore` | Voir [MEMOIRE.md](MEMOIRE.md). |
| `productivity.notes` | Notes | notes | `note.add` | Même fichier que « prends note » (`tools.take_note`). |
| `productivity.documents` | Documents | — | `folder.documents` | Fichiers réellement présents dans `user_content` (textes écrits, résultats de tâches). |
| `system.activity` | Journal d'activité | — | — | Logger `jarvis` + faits structurés ; secrets masqués ; filtres niveau/source ; jusqu'à 3 instances par page. |
| `system.resources` | Ressources | psutil | — | CPU, RAM, disque, réseau ; thread de mesure démarré à la demande, intervalle réglable, suspendable ; graphes de mesures réelles uniquement. |
| `system.notifications` | Notifications | — | `notifications.clear` | Fil du centre de notifications. |
| `system.diagnostics` | Diagnostics | engine, memory | `diagnostics.integrity`, `update.check` | Santé par composant avec preuves, rapport copiable sans secret. |
| `settings.appearance` | Apparence | — | — | Thème, accent, lueur, transparence, densité, animations. |
| `settings.layout` | Mise en page | — | `layout.preset`, `page.delete` | OnePage/pages multiples, préréglages, pages personnalisées. |
| `settings.notifications` | Préférences de notification | — | — | Par catégorie : Dashboard et/ou natif. |
| `settings.behavior` | Fermeture & surveillance | — | — | Mode de fermeture, services réduits, surveillance, ouverture au lancement. |
| `settings.security` | Sécurité des actions | — | — | Niveaux de confirmation (niveau 3 non abaissable). |
| `custom.panel` | Panneau personnalisé | — | — | Mécanisme de widget personnalisé **sans plugin** : titre, note et indicateurs réels choisis dans une liste blanche (santé, moteur, présence, interface, tâches actives, résultats non vus, notifications, tours, souvenirs, CPU, RAM). Jusqu'à 6 instances par page. |

### Conversation (widget prioritaire)

* **Historique** : lu dans le `ConversationContext` partagé (source unique,
  pas de copie), rafraîchi par ses écouteurs (`add_listener`) — un message
  dit à la voix apparaît, un message écrit est visible du moteur.
* **Envoi** : `RUNTIME.submit_text` → `GeminiLive.submit_text_threadsafe` ;
  le thread graphique ne bloque jamais (`concurrent.futures.Future` +
  signal Qt).
* **Concurrence explicite** : un seul envoi en vol ; si Jarvis parle, exécute
  un outil, relance une génération ou traite une demande vocale, le moteur
  refuse (`busy`) avec la raison et **le texte saisi est conservé**.
* **Génération** : indicateur « Jarvis répond… » piloté par les évènements
  réels (présence, transcriptions) ; erreurs et interruptions affichées.
* **Hors ligne** : envoi désactivé avec la raison réelle
  (`RUNTIME.unavailable_reason()`), aucune réponse simulée.
* La réponse est **dite à voix haute** (modèle audio natif) ; sa transcription
  s'affiche dans le widget.

## 7. Cadre commun (`frame.py`)

Le cadre (`WidgetFrame`) peint le panneau selon le thème (pixmap mis en
cache, redessiné seulement si la taille ou le thème change), affiche titre,
sous-titre, états non `ready`, menu « ⋯ » des actions du widget et, en mode
édition, les boutons Réglages / Masquer / Retirer et les poignées de
déplacement et de redimensionnement.
