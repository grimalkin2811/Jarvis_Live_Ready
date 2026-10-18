"""Registre EXPLICITE des widgets du Dashboard (aucune découverte dynamique).

Ajouter un widget (voir docs/dashboard/AJOUTER_UN_WIDGET.md) :
1. écrire une sous-classe de ``DashboardWidget`` dans ``widgets/`` ;
2. déclarer son ``WidgetSpec`` dans ``_builtin_specs`` ci-dessous ;
3. ajouter un test (le test de contrat parcourt automatiquement le registre).
"""

from __future__ import annotations

from typing import Any

from .widget_api import DashboardWidget, SettingField, WidgetContext, WidgetSpec


class WidgetRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, WidgetSpec] = {}

    def register(self, spec: WidgetSpec) -> None:
        errors = spec.contract_errors()
        if errors:
            raise ValueError(f"WidgetSpec {spec.type_id} invalide : {'; '.join(errors)}")
        if spec.type_id in self._specs:
            raise ValueError(f"WidgetSpec déjà enregistré : {spec.type_id}")
        self._specs[spec.type_id] = spec

    def spec(self, type_id: str) -> WidgetSpec | None:
        return self._specs.get(type_id)

    def specs(self) -> list[WidgetSpec]:
        return list(self._specs.values())

    def create(self, placement, context: WidgetContext) -> DashboardWidget | None:
        """Instancie la vue d'un placement ; ``None`` si type inconnu ou erreur."""
        spec = self._specs.get(placement.type)
        if spec is None:
            return None
        try:
            widget = spec.factory(context, spec.validate_settings(placement.settings))
        except Exception:
            import logging

            logging.getLogger("jarvis.dashboard").exception("création du widget %s impossible", placement.type)
            return None
        widget.spec = spec
        return widget

    def instances_allowed(self, type_id: str, page) -> bool:
        spec = self._specs.get(type_id)
        if spec is None:
            return False
        count = sum(1 for p in page.widgets if p.type == type_id)
        return count < spec.max_instances_per_page


def _builtin_specs() -> list[WidgetSpec]:
    from .widgets.conversation_widget import ConversationWidget
    from .widgets.custom_widget import SETTINGS as CUSTOM_SETTINGS
    from .widgets.custom_widget import CustomPanelWidget
    from .widgets.jarvis_widgets import AudioWidget, ModelsWidget, QuickActionsWidget, StateWidget
    from .widgets.memory_widget import MemoryWidget
    from .widgets.productivity_widgets import DocumentsWidget, NotesWidget
    from .widgets.settings_widgets import (
        AppearanceSettingsWidget,
        BehaviorSettingsWidget,
        LayoutSettingsWidget,
        NotificationSettingsWidget,
        SecuritySettingsWidget,
    )
    from .widgets.system_widgets import ActivityWidget, DiagnosticsWidget, NotificationsWidget, ResourcesWidget
    from .widgets.tasks_widgets import BackgroundTasksWidget, TodoWidget

    def f(cls):
        return lambda context, settings: cls(context, settings)

    return [
        WidgetSpec("jarvis.state", "État de Jarvis",
                   "Opérationnel, dégradé, indisponible ou inconnu — avec les preuves de chaque composant.",
                   "jarvis", f(StateWidget), (3, 4), (4, 5), capabilities=frozenset({"realtime"}),
                   dependencies=("engine",), icon="◉"),
        WidgetSpec("jarvis.conversation", "Conversation",
                   "Dialogue écrit avec la même session que la voix ; historique partagé, état de génération.",
                   "conversation", f(ConversationWidget), (4, 5), (5, 9),
                   capabilities=frozenset({"realtime", "actions", "settings"}), dependencies=("engine",),
                   settings=(SettingField("show_tools", "Afficher les actions exécutées", "bool", True),
                             SettingField("max_messages", "Messages affichés au maximum", "int", 100, minimum=20,
                                          maximum=200)),
                   icon="✉", actions=("conversation.new", "voice.stop")),
        WidgetSpec("jarvis.audio", "Audio en direct",
                   "Niveaux micro et voix issus du pipeline existant, présence, micro coupé/actif.",
                   "jarvis", f(AudioWidget), (3, 3), (4, 4), capabilities=frozenset({"realtime", "actions", "graph"}),
                   dependencies=("engine",), icon="♪", actions=("mic.toggle", "voice.stop")),
        WidgetSpec("jarvis.models", "Modèles IA et routage",
                   "Modèle conversationnel réel, routage automatique des tâches, quotas ; sélection manuelle verrouillée.",
                   "jarvis", f(ModelsWidget), (3, 3), (5, 4), capabilities=frozenset({"realtime"}),
                   dependencies=("engine", "tasks"), icon="◇"),
        WidgetSpec("jarvis.quick_actions", "Actions rapides",
                   "Actions réelles : micro, interruption, nouvelle conversation, interfaces, dossiers, routines.",
                   "jarvis", f(QuickActionsWidget), (3, 3), (4, 4), capabilities=frozenset({"actions", "settings"}),
                   settings=(SettingField("show_routines", "Afficher les routines activées", "bool", True),
                             SettingField("show_protocols", "Afficher les protocoles visuels", "bool", False)),
                   # « ▷ » et non « ⚡ » : mesure native D01, le seul glyphe de
                   # l'interface dont le rendu charge les 149 polices du système.
                   icon="▷", actions=("mic.toggle", "voice.stop", "conversation.new", "interface.blob",
                                      "interface.desktop", "folder.results", "routine.run", "protocol.run")),
        WidgetSpec("tasks.background", "Tâches d'arrière-plan",
                   "Statuts réels du Task Manager ; voir, ouvrir, annuler ou relancer selon le statut.",
                   "taches", f(BackgroundTasksWidget), (3, 5), (4, 8),
                   capabilities=frozenset({"realtime", "actions", "settings"}), dependencies=("tasks",),
                   settings=(SettingField("filter", "Filtre par défaut", "choice", "all",
                                          choices=(("all", "Toutes"), ("active", "Actives"), ("done", "Terminées"),
                                                   ("failed", "Échouées / annulées"))),),
                   icon="☑", actions=("task.cancel", "task.retry", "task.open_result")),
        WidgetSpec("tasks.todo", "Tâches personnelles",
                   "Liste de tâches personnelles (la même que « ajoute une tâche » à la voix).",
                   "taches", f(TodoWidget), (3, 3), (4, 5), capabilities=frozenset({"actions"}),
                   dependencies=("todo",), icon="✓", actions=("todo.add", "todo.complete")),
        WidgetSpec("memory.browser", "Mémoire",
                   "Parcourir, rechercher, créer, modifier, supprimer ; historique, différences et restauration.",
                   "memoire", f(MemoryWidget), (6, 6), (12, 11),
                   capabilities=frozenset({"realtime", "actions", "editor", "settings"}), dependencies=("memory",),
                   settings=(SettingField("limit", "Souvenirs chargés au maximum", "int", 200, minimum=20,
                                          maximum=500),),
                   icon="◈", actions=("memory.create", "memory.update", "memory.delete", "memory.restore")),
        WidgetSpec("productivity.notes", "Notes",
                   "Notes de Jarvis (même fichier que « prends note » à la voix).",
                   "productivite", f(NotesWidget), (3, 3), (5, 5), capabilities=frozenset({"actions", "settings"}),
                   dependencies=("notes",),
                   settings=(SettingField("limit", "Notes affichées", "int", 20, minimum=5, maximum=50),),
                   icon="✎", actions=("note.add",)),
        WidgetSpec("productivity.documents", "Documents",
                   "Fichiers réellement présents : textes écrits par Jarvis et résultats de tâches.",
                   "productivite", f(DocumentsWidget), (3, 3), (5, 4), capabilities=frozenset({"actions", "settings"}),
                   settings=(SettingField("limit", "Fichiers listés", "int", 100, minimum=10, maximum=200),),
                   icon="▣", actions=("folder.documents",)),
        WidgetSpec("system.activity", "Journal d'activité",
                   "Journal applicatif et faits structurés (tâches, moteur, outils) ; secrets masqués.",
                   "systeme", f(ActivityWidget), (3, 3), (6, 5),
                   capabilities=frozenset({"realtime", "settings", "multi_instance"}), max_instances_per_page=3,
                   settings=(SettingField("level", "Niveau minimal", "choice", "info",
                                          choices=(("debug", "Débogage"), ("info", "Information"),
                                                   ("warning", "Avertissement"), ("error", "Erreur"))),
                             SettingField("source", "Source", "choice", "all",
                                          choices=(("all", "Toutes"), ("tasks", "Tâches"), ("engine", "Moteur"),
                                                   ("tools", "Outils"), ("ui", "Interface"),
                                                   ("general", "Général")))),
                   icon="≡"),
        WidgetSpec("system.resources", "Ressources",
                   "Processeur, mémoire vive, disque et réseau (mesures réelles, suspendables).",
                   "systeme", f(ResourcesWidget), (3, 3), (4, 4), capabilities=frozenset({"realtime", "graph"}),
                   dependencies=("psutil",), icon="▤"),
        WidgetSpec("system.notifications", "Notifications",
                   "Fil des notifications du Dashboard (et des rappels Jarvis).",
                   "systeme", f(NotificationsWidget), (3, 3), (5, 4), capabilities=frozenset({"realtime", "actions"}),
                   icon="✉", actions=("notifications.clear",)),
        WidgetSpec("system.diagnostics", "Diagnostics",
                   "Santé de chaque composant avec preuves, rapport copiable sans secret.",
                   "systeme", f(DiagnosticsWidget), (4, 5), (7, 8),
                   capabilities=frozenset({"realtime", "actions", "settings"}), dependencies=("engine", "memory"),
                   settings=(SettingField("expanded", "Déplier les preuves", "bool", False),),
                   icon="◎", actions=("diagnostics.integrity", "update.check")),
        WidgetSpec("settings.appearance", "Apparence",
                   "Thème JARVIS / EDITH / FRIDAY, accent, lueur, transparence, densité, animations.",
                   "parametres", f(AppearanceSettingsWidget), (5, 6), (6, 8), icon="✦"),
        WidgetSpec("settings.layout", "Mise en page",
                   "OnePage ou pages multiples, préréglages, pages personnalisées.",
                   "parametres", f(LayoutSettingsWidget), (5, 6), (6, 8), capabilities=frozenset({"actions"}),
                   icon="▦", actions=("layout.preset", "page.delete")),
        WidgetSpec("settings.notifications", "Préférences de notification",
                   "Par catégorie : affichage dans le Dashboard et/ou notification Windows.",
                   "parametres", f(NotificationSettingsWidget), (5, 5), (6, 7), icon="✉"),
        WidgetSpec("settings.behavior", "Fermeture & surveillance",
                   "Mode de fermeture, services réduits, surveillance des ressources, démarrage.",
                   "parametres", f(BehaviorSettingsWidget), (5, 6), (6, 7), icon="◐"),
        WidgetSpec("settings.security", "Sécurité des actions",
                   "Niveaux de confirmation des actions (niveau 3 non abaissable).",
                   "parametres", f(SecuritySettingsWidget), (5, 4), (12, 6), icon="⊘"),
        WidgetSpec("custom.panel", "Panneau personnalisé",
                   "Composez votre panneau à partir d'indicateurs réels (sans plugin ni code).",
                   "personnalise", f(CustomPanelWidget), (3, 3), (4, 4),
                   capabilities=frozenset({"realtime", "settings", "multi_instance"}), max_instances_per_page=6,
                   settings=CUSTOM_SETTINGS, icon="✧"),
    ]


def default_registry() -> WidgetRegistry:
    registry = WidgetRegistry()
    for spec in _builtin_specs():
        registry.register(spec)
    return registry


def describe(registry: WidgetRegistry) -> list[dict[str, Any]]:
    """Description sérialisable (documentation, tests)."""
    return [
        {
            "type_id": s.type_id, "title": s.title, "category": s.category, "min_size": s.min_size,
            "default_size": s.default_size, "capabilities": sorted(s.capabilities), "dependencies": list(s.dependencies),
            "settings": [f.key for f in s.settings], "max_instances_per_page": s.max_instances_per_page,
        }
        for s in registry.specs()
    ]
