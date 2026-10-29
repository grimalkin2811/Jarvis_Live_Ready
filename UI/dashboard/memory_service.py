"""Couche de service « Mémoire » du Dashboard — sans Qt.

Toutes les opérations passent par le ``MemoryManager`` EXISTANT (même base
SQLite que la voix, ``get_default_memory_manager()`` relu à chaque appel :
le Dashboard suit toujours la même instance que la boucle vocale). Ce module
ajoute ce dont une interface d'édition a besoin :

* validation des formulaires (jamais de JSON/SQL brut côté interface) ;
* contrôle de concurrence optimiste (version attendue) ;
* historique, diff lisible et restauration ;
* libellés français des catégories.

Il ne garde AUCUNE copie de l'état mémoire : chaque lecture interroge la base.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

CATEGORY_LABELS: dict[str, str] = {
    "identity": "Identité",
    "preference": "Préférence",
    "person": "Personne",
    "project": "Projet",
    "configuration": "Configuration",
    "fact": "Fait",
    "habit": "Habitude",
    "decision": "Décision",
    "other": "Autre",
}

MAX_CONTENT_CHARS = 4000


@dataclass
class ValidationResult:
    ok: bool
    errors: dict[str, str]
    cleaned: dict[str, Any]


def version_of(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """Version « attendue » d'un souvenir (champs éditables + horodatage)."""
    if not record:
        return None
    return {key: record.get(key) for key in ("content", "category", "importance", "updated_at")}


def validate_form(content: Any, category: Any, importance: Any) -> ValidationResult:
    errors: dict[str, str] = {}
    text = str(content or "").strip()
    if not text:
        errors["content"] = "Le contenu est obligatoire."
    elif len(text) > MAX_CONTENT_CHARS:
        errors["content"] = f"{len(text)} caractères : maximum {MAX_CONTENT_CHARS}."
    cat = str(category or "").strip().lower()
    if cat not in CATEGORY_LABELS:
        errors["category"] = "Catégorie inconnue."
    try:
        level = int(importance)
        if not 1 <= level <= 5:
            raise ValueError
    except (TypeError, ValueError):
        errors["importance"] = "Importance entre 1 et 5."
        level = 3
    return ValidationResult(not errors, errors, {"content": text, "category": cat, "importance": level})


def text_diff(old: str, new: str) -> list[tuple[str, str]]:
    """Diff mot à mot : liste de (op, texte) avec op ∈ {"=", "-", "+"}."""
    a = str(old or "").split()
    b = str(new or "").split()
    result: list[tuple[str, str]] = []
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            result.append(("=", " ".join(a[i1:i2])))
        elif op == "delete":
            result.append(("-", " ".join(a[i1:i2])))
        elif op == "insert":
            result.append(("+", " ".join(b[j1:j2])))
        else:
            result.append(("-", " ".join(a[i1:i2])))
            result.append(("+", " ".join(b[j1:j2])))
    return result


def describe_changes(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Résumé lisible des champs modifiés entre deux versions."""
    lines: list[str] = []
    if old.get("category") != new.get("category"):
        lines.append(
            f"Catégorie : {CATEGORY_LABELS.get(old.get('category'), old.get('category'))} → "
            f"{CATEGORY_LABELS.get(new.get('category'), new.get('category'))}"
        )
    if old.get("importance") != new.get("importance"):
        lines.append(f"Importance : {old.get('importance')} → {new.get('importance')}")
    if old.get("content") != new.get("content"):
        lines.append("Contenu modifié")
    if bool(old.get("archived")) != bool(new.get("archived")):
        lines.append("Supprimé" if new.get("archived") else "Restauré")
    return lines


class MemoryService:
    """Façade du ``MemoryManager`` pour le Dashboard."""

    def __init__(self, manager_getter: Callable[[], Any] | None = None) -> None:
        if manager_getter is None:
            def manager_getter():
                from src.memory import get_default_memory_manager

                return get_default_memory_manager()
        self._manager_getter = manager_getter

    def manager(self):
        return self._manager_getter()

    # -- lecture -----------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        try:
            manager = self.manager()
        except Exception as exc:
            return {"available": False, "enabled": False, "error": str(exc)}
        return {
            "available": bool(getattr(manager, "available", False)),
            "enabled": bool(getattr(manager, "enabled", False)),
            "error": getattr(manager, "last_error", None) or "",
            "path": getattr(manager, "database_path", ""),
        }

    def browse(self, query: str = "", category: str | None = None, limit: int = 200) -> dict[str, Any]:
        return self.manager().browse_memories(query=query, category=category or None, limit=limit)

    def get(self, memory_id: int) -> dict[str, Any]:
        return self.manager().get_memory_record(memory_id)

    def history(self, memory_id: int) -> dict[str, Any]:
        return self.manager().list_memory_revisions(memory_id)

    def stats(self) -> dict[str, Any]:
        return self.manager().memory_stats()

    def integrity(self) -> dict[str, Any]:
        return self.manager().check_integrity()

    # -- écriture ----------------------------------------------------------------

    def create(self, content: Any, category: Any, importance: Any, *, distinct: bool = False) -> dict[str, Any]:
        check = validate_form(content, category, importance)
        if not check.ok:
            return {"success": False, "validation": check.errors, "error": "Formulaire invalide."}
        data = check.cleaned
        return self.manager().create_memory_entry(
            data["content"], data["category"], data["importance"], source="dashboard", distinct=distinct
        )

    def update(self, memory_id: int, expected: dict[str, Any] | None, content: Any, category: Any,
               importance: Any) -> dict[str, Any]:
        check = validate_form(content, category, importance)
        if not check.ok:
            return {"success": False, "validation": check.errors, "error": "Formulaire invalide."}
        data = check.cleaned
        if expected and all(expected.get(k) == data[k] for k in ("content", "category", "importance")):
            return {"success": False, "unchanged": True, "error": "Aucune modification à enregistrer."}
        return self.manager().update_memory_checked(
            memory_id, expected, content=data["content"], category=data["category"], importance=data["importance"]
        )

    def delete(self, memory_id: int, expected: dict[str, Any] | None) -> dict[str, Any]:
        return self.manager().delete_memory_checked(memory_id, expected)

    def restore(self, memory_id: int, revision_id: int, expected: dict[str, Any] | None) -> dict[str, Any]:
        return self.manager().restore_memory_revision(memory_id, revision_id, expected)
