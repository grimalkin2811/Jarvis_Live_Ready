"""Mises en page prédéfinies du Dashboard — sans Qt.

Un préréglage décrit la disposition des **pages intégrées** (Accueil,
Travail, Tâches, Mémoire, Système, Paramètres). Les pages personnalisées de
l'utilisateur ne sont jamais touchées par un changement de préréglage.

« Personnalisée » n'est pas un modèle figé : c'est l'instantané de la
dernière mise en page enregistrée par l'utilisateur (voir ``config``), que
l'on peut restaurer après avoir essayé un autre préréglage.
"""

from __future__ import annotations

from typing import Any

from .layout_model import Page, Placement, new_instance_id

PRESET_COCKPIT = "cockpit"
PRESET_PRODUCTIVITY = "productivite"
PRESET_DIAGNOSTIC = "diagnostic"
PRESET_CUSTOM = "personnalisee"

PRESET_LABELS: dict[str, str] = {
    PRESET_COCKPIT: "Cockpit",
    PRESET_PRODUCTIVITY: "Productivité",
    PRESET_DIAGNOSTIC: "Diagnostic",
    PRESET_CUSTOM: "Personnalisée",
}

PRESET_DESCRIPTIONS: dict[str, str] = {
    PRESET_COCKPIT: "Vue d'ensemble équilibrée : état, conversation et tâches au premier plan.",
    PRESET_PRODUCTIVITY: "Conversation, notes, documents et listes de tâches pour travailler.",
    PRESET_DIAGNOSTIC: "Santé des composants, ressources, journal et routage des modèles.",
    PRESET_CUSTOM: "Votre propre disposition, enregistrée depuis le mode édition.",
}

#: Pages intégrées : identifiant → (titre, icône).
BUILTIN_PAGE_INFO: dict[str, tuple[str, str]] = {
    "accueil": ("Accueil", "⌂"),
    "travail": ("Travail", "✎"),
    "taches": ("Tâches", "☑"),
    "memoire": ("Mémoire", "◈"),
    "systeme": ("Système", "◎"),
    "parametres": ("Paramètres", "⚙"),
}
BUILTIN_PAGE_ORDER = tuple(BUILTIN_PAGE_INFO)

# (type, x, y, w, h, settings)
_Spec = tuple[str, int, int, int, int, dict[str, Any]]

_SETTINGS_PAGE: list[_Spec] = [
    ("settings.appearance", 0, 0, 6, 8, {}),
    ("settings.layout", 6, 0, 6, 8, {}),
    ("settings.notifications", 0, 8, 6, 7, {}),
    ("settings.behavior", 6, 8, 6, 7, {}),
    ("settings.security", 0, 15, 12, 6, {}),
]

_MEMORY_PAGE: list[_Spec] = [
    ("memory.browser", 0, 0, 12, 11, {}),
]

_TEMPLATES: dict[str, dict[str, list[_Spec]]] = {
    PRESET_COCKPIT: {
        "accueil": [
            ("jarvis.state", 0, 0, 4, 5, {}),
            ("jarvis.conversation", 4, 0, 5, 9, {}),
            ("tasks.background", 9, 0, 3, 9, {}),
            ("jarvis.audio", 0, 5, 4, 4, {}),
            ("jarvis.quick_actions", 0, 9, 4, 4, {}),
            ("system.notifications", 4, 9, 5, 4, {}),
            ("system.resources", 9, 9, 3, 4, {}),
        ],
        "travail": [
            ("jarvis.conversation", 0, 0, 6, 9, {}),
            ("productivity.notes", 6, 0, 6, 5, {}),
            ("productivity.documents", 6, 5, 6, 4, {}),
            ("tasks.todo", 0, 9, 6, 5, {}),
            ("jarvis.quick_actions", 6, 9, 6, 5, {}),
        ],
        "taches": [
            ("tasks.background", 0, 0, 8, 10, {}),
            ("tasks.todo", 8, 0, 4, 6, {}),
            ("system.activity", 8, 6, 4, 4, {"source": "tasks"}),
        ],
        "memoire": _MEMORY_PAGE,
        "systeme": [
            ("system.diagnostics", 0, 0, 7, 8, {}),
            ("system.resources", 7, 0, 5, 4, {}),
            ("jarvis.models", 7, 4, 5, 4, {}),
            ("system.activity", 0, 8, 7, 6, {}),
            ("system.activity", 7, 8, 5, 6, {"level": "warning"}),
        ],
        "parametres": _SETTINGS_PAGE,
    },
    PRESET_PRODUCTIVITY: {
        "accueil": [
            ("jarvis.state", 0, 0, 3, 4, {}),
            ("jarvis.conversation", 3, 0, 6, 10, {}),
            ("tasks.background", 9, 0, 3, 5, {}),
            ("tasks.todo", 9, 5, 3, 5, {}),
            ("productivity.notes", 0, 4, 3, 6, {}),
            ("jarvis.quick_actions", 0, 10, 3, 4, {}),
            ("productivity.documents", 3, 10, 9, 4, {}),
        ],
        "travail": [
            ("productivity.notes", 0, 0, 6, 6, {}),
            ("productivity.documents", 6, 0, 6, 6, {}),
            ("tasks.todo", 0, 6, 6, 6, {}),
            ("jarvis.conversation", 6, 6, 6, 8, {}),
        ],
        "taches": [
            ("tasks.background", 0, 0, 7, 10, {}),
            ("tasks.todo", 7, 0, 5, 10, {}),
        ],
        "memoire": _MEMORY_PAGE,
        "systeme": [
            ("system.diagnostics", 0, 0, 7, 8, {}),
            ("system.resources", 7, 0, 5, 4, {}),
            ("jarvis.models", 7, 4, 5, 4, {}),
            ("system.activity", 0, 8, 12, 5, {}),
        ],
        "parametres": _SETTINGS_PAGE,
    },
    PRESET_DIAGNOSTIC: {
        "accueil": [
            ("jarvis.state", 0, 0, 4, 5, {}),
            ("system.diagnostics", 4, 0, 8, 7, {}),
            ("system.resources", 0, 5, 4, 5, {}),
            ("jarvis.conversation", 4, 7, 4, 7, {}),
            ("tasks.background", 8, 7, 4, 7, {}),
            ("jarvis.audio", 0, 10, 4, 4, {}),
        ],
        "travail": [
            ("jarvis.conversation", 0, 0, 7, 9, {}),
            ("system.activity", 7, 0, 5, 9, {}),
        ],
        "taches": [
            ("tasks.background", 0, 0, 8, 10, {}),
            ("system.activity", 8, 0, 4, 10, {"source": "tasks"}),
        ],
        "memoire": _MEMORY_PAGE,
        "systeme": [
            ("system.diagnostics", 0, 0, 6, 9, {}),
            ("jarvis.models", 6, 0, 6, 4, {}),
            ("system.resources", 6, 4, 6, 5, {}),
            ("system.activity", 0, 9, 6, 6, {}),
            ("system.activity", 6, 9, 6, 6, {"level": "warning"}),
            ("system.notifications", 0, 15, 12, 4, {}),
        ],
        "parametres": _SETTINGS_PAGE,
    },
}


def available_presets() -> tuple[str, ...]:
    return (PRESET_COCKPIT, PRESET_PRODUCTIVITY, PRESET_DIAGNOSTIC, PRESET_CUSTOM)


def builtin_pages(preset: str = PRESET_COCKPIT, taken_ids: set[str] | None = None) -> list[Page]:
    """Pages intégrées d'un préréglage (nouveaux identifiants d'instance)."""
    template = _TEMPLATES.get(preset) or _TEMPLATES[PRESET_COCKPIT]
    taken = taken_ids if taken_ids is not None else set()
    pages: list[Page] = []
    for page_id in BUILTIN_PAGE_ORDER:
        title, icon = BUILTIN_PAGE_INFO[page_id]
        widgets: list[Placement] = []
        for widget_type, x, y, w, h, settings in template.get(page_id, []):
            instance_id = new_instance_id(taken)
            taken.add(instance_id)
            widgets.append(Placement(instance_id, widget_type, x, y, w, h, False, dict(settings)))
        pages.append(Page(page_id, title, True, icon, widgets))
    return pages


def template_widget_types() -> set[str]:
    """Tous les types de widgets utilisés par les préréglages (tests de cohérence)."""
    types: set[str] = set()
    for pages in _TEMPLATES.values():
        for specs in pages.values():
            types.update(spec[0] for spec in specs)
    return types
