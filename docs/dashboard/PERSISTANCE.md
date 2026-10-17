# Persistance des préférences et migrations

Code : `UI/dashboard/config.py` (sans Qt), écriture différée par
`UI/dashboard/controller.py`. Migration de la base mémoire : voir
[MEMOIRE.md](MEMOIRE.md).

## 1. Fichier

`paths.ui_state_dir()/dashboard_state.json` — dossier de données utilisateur
de Jarvis (jamais dans le dépôt, aucun secret). Écriture **atomique** via
`src.json_state.save_dict` (fichier temporaire puis remplacement), différée
de 600 ms après la dernière modification (800 ms pour la géométrie) et forcée
à l'arrêt (`controller.flush()` dans `shutdown()`).

## 2. Contenu (schéma v1)

```jsonc
{
  "schema_version": 1,
  "app": "jarvis-dashboard",
  "appearance":    { "theme": "jarvis|edith|friday", "accent": "#rrggbb|null", "glow": 0-100,
                     "panel_opacity": 40-100, "density": "compact|normal|confortable",
                     "animation_level": "leger|normal|pousse", "respect_system_motion": true },
  "window":        { "geometry": "<base64 Qt>", "state": "normal|maximized|fullscreen", "last_page": "accueil" },
  "layout":        { "view_mode": "multipage|onepage", "preset": "cockpit|productivite|diagnostic|personnalisee",
                     "pages": [ { "id": "accueil", "title": "Accueil", "builtin": true, "icon": "◉",
                                  "widgets": [ { "id": "w-…", "type": "jarvis.state", "x": 0, "y": 0, "w": 4, "h": 5,
                                                 "hidden": false, "settings": {} } ] } ],
                     "custom_snapshot": [ /* pages intégrées enregistrées (« Personnalisée ») */ ] },
  "notifications": { "categories": { "task_completed": { "dashboard": true, "native": true }, "…": {} } },
  "close":         { "mode": "persistent|interactive|shutdown",
                     "interactive": { "suspend_monitoring": true, "suspend_activity": false, "release_window": true },
                     "close_hint_shown": false },
  "monitoring":    { "enabled": true, "interval_s": 1-60, "background": false,
                     "cpu_threshold": 50-100, "ram_threshold": 50-100, "disk_threshold": 50-100 },
  "security":      { "confirm": { "task.retry": false } },
  "general":       { "open_on_launch": false, "auto_update_check": false,
                     "last_update_check": null, "last_notified_version": "" }
}
```

Toutes les préférences demandées sont couvertes : thème et réglages
visuels, niveau d'animation, géométrie et état de la fenêtre, OnePage, mises
en page, pages personnalisées, réglages de widgets (par instance),
préférences de notification, mode de fermeture, surveillance, sécurité.

## 3. Règles

* **Valeurs par défaut** pour chaque champ (`DashboardConfig.defaults()` :
  préréglage Cockpit).
* **Validation champ par champ** : type, bornes, listes de valeurs ; une
  valeur invalide est remplacée par la valeur par défaut **de ce champ
  seulement**, avec une note (ex. « thème inconnu « matrix » remplacé par
  JARVIS »). Les réglages de widgets sont validés par les `SettingField` du
  spec à chaque lecture.
* **Clés inconnues préservées** (racine, sections, pages, placements,
  réglages de widgets, catégories de notification futures) et réécrites à
  l'identique : un fichier écrit par une version plus récente n'est pas
  appauvri.
* **Types de widgets inconnus conservés** : affichés « widget indisponible »
  au lieu d'être supprimés.
* **Pages intégrées manquantes** recréées depuis le préréglage courant, sans
  toucher aux autres ; pages dupliquées ou invalides ignorées (notées) ;
  pages personnalisées au-delà de 12 conservées hors affichage.
* **Jamais bloquant** : `load_config()` ne lève jamais d'exception.

## 4. Statuts de chargement et reprise

| Statut | Situation | Comportement |
|---|---|---|
| `defaults` | Aucun fichier. | Valeurs par défaut. |
| `loaded` | Fichier valide (éventuellement migré). | Utilisé tel quel. |
| `repaired` | Valeurs invalides corrigées. | Notes consignées dans le journal d'activité. |
| `newer` | `schema_version` > version connue. | Chargé au mieux, numéro de schéma **conservé**, clés inconnues préservées. |
| `recovered` | JSON illisible / non-objet. | Le fichier est d'abord **copié** en `dashboard_state.corrupt-AAAAMMJJ-HHMMSS.json`, puis valeurs par défaut ; notification « intervention requise ». |
| `unreadable` | Erreur d'E/S, ou copie de sauvegarde impossible. | Valeurs par défaut **en mémoire seulement** (`read_only`) : l'original n'est jamais écrasé. |

Les préférences ne sont donc **jamais supprimées silencieusement**.

## 5. Migrations

Table `MIGRATIONS` indexée par version **cible** (même convention que
`src.memory._MIGRATIONS`). v0 → v1 : fichier sans `schema_version` (brouillon
ou édité à la main) — les clés plates historiques (`theme`, `view_mode`…)
sont rangées dans leur section, tout le reste est conservé.

Ajouter une migration v1 → v2 :

1. incrémenter `CONFIG_SCHEMA_VERSION` dans `UI/dashboard/__init__.py` ;
2. écrire `_migrate_v1_to_v2(raw: dict) -> dict` (pure, sans perte : ne
   supprimer aucune clé inconnue) et l'enregistrer dans `MIGRATIONS[2]` ;
3. ajouter un test dans `tests/test_dashboard_config.py` (fichier v1 réel →
   v2, aller-retour, clés inconnues préservées, échec de migration non
   bloquant).

## 6. Préréglages et instantané « Personnalisée »

* Appliquer un préréglage (`apply_preset`) remplace la disposition des pages
  intégrées seulement ; titres/icônes personnalisés et pages personnalisées
  sont conservés.
* Enregistrer le mode édition écrit `layout.custom_snapshot` (copie des pages
  intégrées) et sélectionne « Personnalisée » ; choisir à nouveau
  « Personnalisée » après un autre préréglage restaure cet instantané.

## 7. Tests

`tests/test_dashboard_config.py` : défauts, aller-retour, validation et
notes, fichier corrompu sauvegardé puis remplacé, fichier illisible jamais
écrasé, schéma plus récent conservé, migration v0 → v1, clés inconnues,
préréglages et instantané, pages personnalisées (création, renommage,
suppression, limite). `tests/test_dashboard_window.py` : persistance réelle
de la géométrie, du thème, de OnePage, du mode d'édition et de la page
courante à travers une réouverture du contrôleur.
