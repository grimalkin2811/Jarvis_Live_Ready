# Captures du Dashboard

Sélection issue de `scripts/dashboard_visual_protocol.py` (24 captures au
total, régénérables), convertie en JPEG 1 280 px pour limiter le poids du
dépôt.

> **À lire avant de regarder** : captures produites sous Linux en rendu
> logiciel hors écran (`QT_QPA_PLATFORM=offscreen`, police DejaVu au lieu de
> Segoe UI), avec des données de **démonstration** isolées et un état moteur
> **simulé** (« Connecté », aucune session Gemini réelle, aucun micro). Elles
> montrent le rendu, la mise en page et les états ; elles ne prouvent pas un
> fonctionnement de bout en bout sur Windows.

| Fichier | Contenu |
|---|---|
| `jarvis_accueil.jpg` | Thème JARVIS, page Accueil (préréglage Cockpit) : état, conversation partagée avec la voix, tâches, audio, actions rapides, notifications, ressources ; notification éphémère. |
| `edith_accueil.jpg` | Même page, thème EDITH (panneaux chanfreinés, grille tactique). |
| `friday_accueil.jpg` | Même page, thème FRIDAY (verre, aurores, typographie fine). |
| `jarvis_taches.jpg` | Page Tâches : tâche en cours (progression réelle), terminée non vue, échouée ; seules les actions supportées (pas de pause). |
| `jarvis_memoire.jpg` | Page Mémoire : formulaire structuré, historique des versions et différences mot à mot. |
| `edith_systeme.jpg` | Page Système : diagnostics avec preuves, ressources, modèles et routage (sélecteur verrouillé), journal. |
| `friday_parametres.jpg` | Page Paramètres : apparence, mise en page, notifications par catégorie, modes de fermeture et surveillance. |
| `jarvis_onepage_bas_diagnostics.jpg` | OnePage, bas de page : la section Système (diagnostics) arrive en dernier. |
| `jarvis_etroit.jpg` | État étroit (820 px) : navigation par icônes, grille 4 colonnes. |
| `jarvis_edition.jpg` | Mode édition : barre d'édition, réglages / masquer / retirer sur chaque widget, poignées. |
| `dialogue_niveau3.jpg` | Confirmation renforcée (niveau 3) : case à cocher obligatoire, « Annuler » par défaut. |

Régénérer : `python scripts/dashboard_visual_protocol.py --out build/dashboard_screens`
(voir [../PROTOCOLE_DE_TEST.md](../PROTOCOLE_DE_TEST.md)).
