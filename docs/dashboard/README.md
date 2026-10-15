# Dashboard — guide

Le **Dashboard** est la troisième interface de Jarvis, à côté de l'orbe
(Blob) et du Desktop Mode. Il ne remplace ni l'un ni l'autre, ni la voix :
c'est un poste de pilotage (état réel de Jarvis, conversation écrite, tâches,
mémoire, notifications, diagnostics, réglages) qui partage **le même moteur,
la même conversation et les mêmes services** que la voix.

> Le nom affiché est centralisé dans `UI/dashboard/__init__.py`
> (`DASHBOARD_NAME = "Dashboard"`) : le changer suffit à renommer toutes les
> mentions visibles (fenêtre, menu de la zone de notification, notifications,
> messages).

## Documentation

| Document | Contenu |
|---|---|
| [PLAN.md](PLAN.md) | Plan court rédigé après l'audit (réutilisation, risques, tests prévus). |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Modules, fils d'exécution, état partagé, modifications du code existant. |
| [WIDGETS.md](WIDGETS.md) | Contrat des widgets, cycle de vie, états, liste des 20 widgets. |
| [AJOUTER_UN_WIDGET.md](AJOUTER_UN_WIDGET.md) | Procédure pas à pas (code + test). |
| [NAVIGATION_ET_THEMES.md](NAVIGATION_ET_THEMES.md) | Pages, OnePage, états de fenêtre, mode édition, préréglages, thèmes, animations. |
| [PERSISTANCE.md](PERSISTANCE.md) | Préférences versionnées, migrations, validation, reprise. |
| [MEMOIRE.md](MEMOIRE.md) | Édition de la mémoire : service, schéma v2, concurrence, confirmations. |
| [SECURITE.md](SECURITE.md) | Niveaux d'actions 1/2/3, règles d'honnêteté, secrets. |
| [PROTOCOLE_DE_TEST.md](PROTOCOLE_DE_TEST.md) | Commandes, critères, protocole visuel et de performance, check-list Windows. |
| [screenshots/](screenshots/) | Captures du protocole visuel (hors écran, données de démonstration). |

## Ouvrir le Dashboard

| Point d'entrée | Détail |
|---|---|
| Icône de la zone de notification | Entrée « Ouvrir le Dashboard » (orbe **et** Desktop). |
| Orbe | `Ctrl+D` (la touche `D` seule garde son rôle de débogage). |
| Voix | « ouvre le Dashboard », « affiche la page mémoire du tableau de bord »… → outil `open_dashboard(page)` ; « ferme le Dashboard » → `close_dashboard`. L'outil ne confirme l'ouverture que si la fenêtre a réellement signalé son affichage (≤ 1,5 s). |
| Ligne de commande | `python run_jarvis.py --dashboard` (avec l'interface enregistrée) ou `--ui --dashboard` / `--desktop --dashboard`. |
| Démarrage | Préférence « Ouvrir le Dashboard au lancement » (page Paramètres). |

Ouvrir, fermer, masquer le Dashboard ou passer de l'orbe au Desktop depuis le
Dashboard **ne redémarre pas** le moteur, **ne crée pas** de nouvelle
conversation, **n'interrompt pas** le micro et laisse les tâches continuer
(testé : `tests/test_dashboard_entry_points.py`, `tests/test_dashboard_window.py`).

## Raccourcis clavier (fenêtre du Dashboard)

| Raccourci | Action |
|---|---|
| `Ctrl+1` … `Ctrl+9` | Aller à la page n (ordre de la barre latérale). |
| `Ctrl+O` | Basculer OnePage / pages multiples (persisté). |
| `Ctrl+E` | Entrer / sortir du mode édition (sortir = enregistrer). |
| `Échap` | Quitter le plein écran, sinon annuler le mode édition. |
| `F11` | Plein écran / retour à l'état précédent (normal ou maximisé). |
| `Ctrl+W` | Fermer la fenêtre — selon le mode de fermeture (voir ci-dessous). |
| `Entrée` / `Maj+Entrée` | (Conversation) envoyer / nouvelle ligne. |

## Pages intégrées

Accueil (état, conversation et tâches au premier plan), Travail, Tâches,
Mémoire, Système (diagnostics), Paramètres. Les pages sont personnalisables
(mode édition) ; des pages personnalisées peuvent être créées, renommées,
réordonnées et supprimées (12 au maximum ; supprimer une page ne supprime
aucune donnée). Voir [NAVIGATION_ET_THEMES.md](NAVIGATION_ET_THEMES.md).

## Modes de fermeture

Choisis dans Paramètres → « Fermeture & surveillance » (persisté) :

| Mode | Fermer la fenêtre… |
|---|---|
| **Persistant** (défaut) | masque le Dashboard ; Jarvis continue, le Dashboard reste prêt (réouverture instantanée). |
| **Interactif** | masque le Dashboard et réduit les services non essentiels, au choix : suspendre la surveillance des ressources, suspendre la capture du journal en direct, libérer la fenêtre et ses widgets. Ne touche jamais au moteur, au micro, aux tâches ni aux notifications. Tout est rétabli à la réouverture. |
| **Arrêt complet** | ouvre une confirmation qui explique ce qui s'arrête (voix et session Gemini Live, conversation en cours, rappels et routines programmés, tâches actives listées) et ce qui est conservé (mémoire, notes, préférences). Avec des tâches actives, la case « J'accepte d'interrompre les tâches en cours » doit être cochée pour activer « Quitter Jarvis ». « Masquer seulement (Jarvis continue) » et « Annuler » restent proposés. |

La fermeture de la fenêtre **ne quitte jamais Jarvis implicitement** : seul le
mode « Arrêt complet », après confirmation, appelle la procédure d'arrêt
normale de Jarvis. La première fermeture affiche un rappel (« Jarvis continue
de fonctionner… »).

## Notifications

Catégories : tâche terminée, tâche échouée, intervention requise, problème
réseau, problème audio, seuil de ressources (si la surveillance est active),
mise à jour disponible (mécanisme existant `src.updater`, vérification
automatique désactivée par défaut). Pour chaque catégorie : affichage dans le
Dashboard et/ou notification native (zone de notification), persistés.

* Les notifications fonctionnent **Dashboard fermé** tant que Jarvis tourne
  (le centre vit dans le contrôleur, créé au lancement de l'interface).
* Hors du Dashboard, les bulles natives sont préfixées « Dashboard — ».
* Anti-doublons : une tâche ne notifie qu'une fois son statut final ; pas de
  bulle native si le Dashboard est au premier plan avec le canal Dashboard
  actif ; la bulle historique « Tâche terminée » du Task Manager est filtrée
  (le centre la remplace, avec préférences).
* Jamais de succès annoncé sur un échec ou un statut inconnu ; un échec de
  l'API native est consigné (`native_error`) sans interrompre Jarvis.

## Hors ligne

Sans connexion au modèle, la conversation écrite est désactivée avec la
raison réelle publiée par le moteur (« Hors ligne : connexion au modèle
perdue, nouvelle tentative en cours », « Clé Gemini API refusée… »,
« Connexion au modèle en cours… », « Le moteur vocal n'est pas démarré… ») ;
aucune réponse n'est simulée. La mémoire, les notes, les tâches
personnelles, les documents, les ressources, les diagnostics et les réglages
restent utilisables.

## Limites connues (honnêteté)

* Développé et validé localement sous Linux, en rendu hors écran
  (`QT_QPA_PLATFORM=offscreen`), avec des bibliothèques graphiques factices.
  La suite complète (tests du Dashboard compris) passe aussi sur le runner
  Windows de la CI GitHub (`windows-latest`, hors écran, PR #50). **Aucune
  validation manuelle sur un PC Windows réel** (bulles natives, plein écran
  multi-écrans, DPI, polices Segoe UI, audio) dans ce travail.
* La conversation écrite a été testée contre le faux serveur Live du dépôt
  (`tests/live_harness.py`), **pas** contre la vraie API Gemini Live ni avec
  un vrai micro / haut-parleur.
* La réponse de Jarvis à un message écrit est **dite à voix haute** (modèle
  audio natif) ; sa transcription s'affiche dans le widget Conversation.
* Le sélecteur manuel de modèle est volontairement **verrouillé** (aucun
  changement de routage depuis le Dashboard).
