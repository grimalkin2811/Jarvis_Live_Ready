# Ajouter un widget

Le registre est **explicite** : pas de découverte dynamique, pas de plugin.
Ajouter un widget = une classe, une déclaration, un test. Lire d'abord
[WIDGETS.md](WIDGETS.md) (contrat et cycle de vie).

> Besoin d'un simple panneau d'indicateurs ? Le widget « Panneau
> personnalisé » (`custom.panel`) suffit souvent : ajouter une entrée à
> `METRICS` dans `UI/dashboard/widgets/custom_widget.py` (clé, libellé,
> fonction de lecture d'une source **réelle** du hub) fait apparaître le
> réglage correspondant automatiquement.

## 1. Écrire la vue

Fichier dans `UI/dashboard/widgets/` (nouveau ou existant, par domaine).
Exemple minimal : nombre de souvenirs, rafraîchi quand la mémoire change.

```python
# UI/dashboard/widgets/exemple_widget.py
from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout

from ..widget_api import STATE_EMPTY, STATE_READY, STATE_UNAVAILABLE, DashboardWidget
from .common import label


class MemoryCountWidget(DashboardWidget):
    """Nombre de souvenirs actifs (lecture seule, source : MemoryService)."""

    def __init__(self, context, settings=None) -> None:
        super().__init__(context, settings)
        # Construction LÉGÈRE : uniquement des widgets Qt. Ni minuteur, ni
        # abonnement, ni accès disque/réseau ici.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.value = label("—", "title")
        layout.addWidget(self.value)

    def on_activate(self) -> None:
        # Abonnements libérés AUTOMATIQUEMENT à la désactivation.
        self.listen(self.context.hub.memory_changed, self.refresh)
        self.refresh()

    def refresh(self) -> None:
        # Jamais d'E/S dans le thread graphique : run_job (pool JobRunner).
        self.run_job(self.context.hub.memory.stats, on_done=self._on_stats,
                     on_error=lambda exc: self.set_state(STATE_UNAVAILABLE, f"Mémoire illisible : {exc}"))

    def _on_stats(self, result: dict) -> None:
        if not result.get("success"):
            self.set_state(STATE_UNAVAILABLE, result.get("error") or "Mémoire indisponible.")
            return
        total = int(result.get("total", 0))
        self.value.setText(str(total))
        self.set_state(STATE_READY if total else STATE_EMPTY, "" if total else "Aucun souvenir.")
```

Règles :

* **Aucune donnée métier stockée dans le widget** : lire le hub et ses
  services (ils interrogent la source réelle) ; ne jamais afficher de valeur
  inventée — sans donnée, `empty` ou `unavailable` avec la raison.
* Minuteurs : `self.every(ms, callback)` (jamais `QTimer` brut non suivi).
* Actions : surcharger `actions()` et retourner des `WidgetAction` ;
  passer par `self.context.confirm(action_id, résumé, détails)` avant toute
  action de niveau ≥ 2, et n'annoncer le succès **qu'après** la réponse du
  service (voir [SECURITE.md](SECURITE.md)). Une action impossible est
  retournée avec `enabled=False` et `reason="…"`.
* Thème : surcharger `on_theme_changed(theme)` si la vue peint elle-même ;
  utiliser `self.context.theme` (couleurs, polices, niveau d'animation).

## 2. Déclarer le `WidgetSpec`

Dans `UI/dashboard/registry.py`, fonction `_builtin_specs()` : importer la
classe et ajouter une entrée.

```python
WidgetSpec("memory.count", "Souvenirs", "Nombre de souvenirs actifs dans la mémoire persistante.",
           "memoire", f(MemoryCountWidget), min_size=(2, 2), default_size=(3, 3),
           capabilities=frozenset({"realtime"}), dependencies=("memory",), icon="◈"),
```

* `type_id` **stable** (`domaine.nom`) : il est écrit dans les préférences
  des utilisateurs. Ne jamais le renommer ; un type disparu est conservé et
  affiché « widget indisponible » plutôt que supprimé.
* Réglages par instance : `settings=(SettingField(...), ...)` **et** la
  capacité `settings`. La boîte de réglages du mode édition est générée à
  partir des `SettingField` (bool, int borné, choix, texte borné).
* Plusieurs instances par page : capacité `multi_instance` +
  `max_instances_per_page ≥ 2`.
* Actions proposées : identifiants de politiques dans `actions=(...)`, et
  politique correspondante dans `UI/dashboard/security.py`
  (`DEFAULT_POLICIES`) si elle n'existe pas.

`WidgetRegistry.register()` refuse un spec incohérent (tailles, catégorie,
capacités, dépendances, réglages dupliqués…).

## 3. (Optionnel) Le placer dans un préréglage

`UI/dashboard/presets.py`, table `_TEMPLATES` : `(type_id, x, y, w, h,
réglages)` en unités de la grille 12 colonnes. Sinon, l'utilisateur
l'ajoute depuis la bibliothèque du mode édition (« + Ajouter un widget »).

## 4. Tester

* `tests/test_dashboard_widgets.py::test_every_widget_builds_activates_and_releases_everything`
  parcourt **automatiquement** tout le registre : création, activation,
  état valide, désactivation (0 connexion, 0 minuteur actif), destruction.
  Le nouveau widget y est donc couvert sans rien écrire.
* Ajouter un test de comportement propre (données réelles isolées) :

```python
def test_memory_count_widget_reflects_real_store(env):
    from UI.dashboard.layout_model import Placement
    from UI.dashboard.registry import default_registry

    registry = default_registry()
    env["data"]["memory"].create_memory_entry("Test isolé", "fact", 3, distinct=True)
    widget = registry.create(Placement("w-count", "memory.count", 0, 0, 3, 3), _context(env, "w-count"))
    widget.activate()
    assert wait_until(env["app"], lambda: widget.state == "ready")
    assert widget.value.text() == "1"
    widget.dispose()
```

`tests/dashboard_support.isolated_jarvis()` redirige **toutes** les données
(mémoire, notes, tâches, préférences) vers un dossier temporaire : ne jamais
tester sur la mémoire réelle de l'utilisateur.

## 5. Vérifier

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_dashboard_widgets.py
python scripts/dashboard_visual_protocol.py --out build/dashboard_screens --quick
python -m ruff check UI tests
```

et documenter le widget dans le tableau de [WIDGETS.md](WIDGETS.md).
