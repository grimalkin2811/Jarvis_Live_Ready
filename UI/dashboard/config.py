"""Préférences persistantes du Dashboard — versionnées, validées, récupérables.

Fichier : ``paths.ui_state_dir()/dashboard_state.json`` (dossier de données
utilisateur, jamais dans le dépôt). Aucun module Qt n'est importé ici.

Règles (cf. cahier des charges, §13)
------------------------------------
* **Versionné** : ``schema_version`` + table ``MIGRATIONS`` (v0 → v1 …).
* **Valeurs par défaut** pour chaque champ ; validation champ par champ.
* **Reprise** : un fichier illisible ou corrompu ne bloque jamais le
  lancement. Il est d'abord **copié** (``dashboard_state.corrupt-<date>.json``)
  avant que des valeurs par défaut ne soient utilisées : les préférences ne
  sont jamais supprimées silencieusement.
* **Compatibilité ascendante** : clés inconnues préservées (racine, sections,
  pages, placements) ; un fichier écrit par une version plus récente garde
  son numéro de schéma.
* **Écriture atomique** via ``src.json_state.save_dict``.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from src.json_state import read_text, save_dict

from . import CONFIG_SCHEMA_VERSION
from .layout_model import (
    MAX_CUSTOM_PAGES,
    Page,
    clean_title,
    new_page_id,
)
from .presets import (
    BUILTIN_PAGE_INFO,
    BUILTIN_PAGE_ORDER,
    PRESET_COCKPIT,
    PRESET_CUSTOM,
    available_presets,
    builtin_pages,
)

# ---------------------------------------------------------------------------
# Domaines de valeurs
# ---------------------------------------------------------------------------

THEMES = ("jarvis", "edith", "friday")
ANIMATION_LEVELS = ("leger", "normal", "pousse")
ANIMATION_LABELS = {"leger": "Léger", "normal": "Normal", "pousse": "Poussé"}
DENSITIES = ("compact", "normal", "confortable")
VIEW_MODES = ("multipage", "onepage")
CLOSE_MODES = ("persistent", "interactive", "shutdown")
CLOSE_MODE_LABELS = {
    "persistent": "Persistant — Jarvis continue, le Dashboard reste prêt",
    "interactive": "Interactif — Jarvis continue, services non essentiels réduits",
    "shutdown": "Arrêt complet — fermer le Dashboard quitte Jarvis",
}
WINDOW_STATES = ("normal", "maximized", "fullscreen")

#: Catégories de notification : identifiant → (libellé, Dashboard, natif).
NOTIFICATION_CATEGORIES: dict[str, tuple[str, bool, bool]] = {
    "task_completed": ("Tâche terminée", True, True),
    "task_failed": ("Tâche échouée", True, True),
    "intervention": ("Intervention requise", True, True),
    "network": ("Problème réseau", True, True),
    "audio": ("Problème audio", True, True),
    "resources": ("Seuil de ressources (si la surveillance est active)", True, False),
    "update": ("Mise à jour disponible", True, False),
}

#: Options du mode de fermeture « interactif » (services non essentiels).
INTERACTIVE_OPTIONS: dict[str, tuple[str, bool]] = {
    "suspend_monitoring": ("Suspendre la surveillance des ressources", True),
    "suspend_activity": ("Suspendre la capture du journal d'activité en direct", False),
    "release_window": ("Libérer la fenêtre et ses widgets (mémoire)", True),
}

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in {"true", "false", "1", "0", "oui", "non"}:
        return value.strip().lower() in {"true", "1", "oui"}
    return default


def _int(value: Any, default: int, low: int, high: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _choice(value: Any, choices: tuple[str, ...], default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in choices else default


def _extras(raw: dict[str, Any], known: set[str]) -> dict[str, Any]:
    return {k: copy.deepcopy(v) for k, v in raw.items() if k not in known}


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


@dataclass
class AppearancePrefs:
    theme: str = "jarvis"
    accent: str | None = None
    glow: int = 60
    panel_opacity: int = 86
    density: str = "normal"
    animation_level: str = "normal"
    respect_system_motion: bool = True
    extras: dict[str, Any] = field(default_factory=dict)

    KEYS = {"theme", "accent", "glow", "panel_opacity", "density", "animation_level", "respect_system_motion"}

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.extras)
        payload.update(
            theme=self.theme, accent=self.accent, glow=self.glow, panel_opacity=self.panel_opacity,
            density=self.density, animation_level=self.animation_level,
            respect_system_motion=self.respect_system_motion,
        )
        return payload

    @classmethod
    def from_dict(cls, raw: Any, notes: list[str]) -> "AppearancePrefs":
        if not isinstance(raw, dict):
            if raw is not None:
                notes.append("apparence invalide : valeurs par défaut")
            return cls()
        accent = raw.get("accent")
        if accent is not None and not (isinstance(accent, str) and _HEX_COLOR.match(accent)):
            notes.append("couleur d'accent invalide ignorée")
            accent = None
        prefs = cls(
            theme=_choice(raw.get("theme"), THEMES, "jarvis"),
            accent=accent.lower() if isinstance(accent, str) else None,
            glow=_int(raw.get("glow"), 60, 0, 100),
            panel_opacity=_int(raw.get("panel_opacity"), 86, 40, 100),
            density=_choice(raw.get("density"), DENSITIES, "normal"),
            animation_level=_choice(raw.get("animation_level"), ANIMATION_LEVELS, "normal"),
            respect_system_motion=_bool(raw.get("respect_system_motion"), True),
            extras=_extras(raw, cls.KEYS),
        )
        if raw.get("theme") is not None and prefs.theme != str(raw.get("theme")).lower():
            notes.append(f"thème inconnu « {raw.get('theme')} » remplacé par JARVIS")
        return prefs


@dataclass
class WindowPrefs:
    geometry: str = ""
    state: str = "normal"
    last_page: str = "accueil"
    extras: dict[str, Any] = field(default_factory=dict)

    KEYS = {"geometry", "state", "last_page"}

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.extras)
        payload.update(geometry=self.geometry, state=self.state, last_page=self.last_page)
        return payload

    @classmethod
    def from_dict(cls, raw: Any, notes: list[str]) -> "WindowPrefs":
        if not isinstance(raw, dict):
            return cls()
        geometry = raw.get("geometry")
        if not isinstance(geometry, str) or len(geometry) > 4096 or not re.fullmatch(r"[A-Za-z0-9+/=]*", geometry):
            geometry = ""
        return cls(
            geometry=geometry,
            state=_choice(raw.get("state"), WINDOW_STATES, "normal"),
            last_page=str(raw.get("last_page") or "accueil")[:64],
            extras=_extras(raw, cls.KEYS),
        )


@dataclass
class LayoutPrefs:
    view_mode: str = "multipage"
    preset: str = PRESET_COCKPIT
    pages: list[Page] = field(default_factory=list)
    custom_snapshot: list[dict[str, Any]] | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    KEYS = {"view_mode", "preset", "pages", "custom_snapshot"}

    def page(self, page_id: str) -> Page | None:
        for page in self.pages:
            if page.id == page_id:
                return page
        return None

    def instance_ids(self) -> set[str]:
        return {p.id for page in self.pages for p in page.widgets}

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.extras)
        payload.update(
            view_mode=self.view_mode,
            preset=self.preset,
            pages=[page.to_dict() for page in self.pages],
            custom_snapshot=copy.deepcopy(self.custom_snapshot),
        )
        return payload

    @classmethod
    def from_dict(cls, raw: Any, notes: list[str]) -> "LayoutPrefs":
        if not isinstance(raw, dict):
            raw = {}
        taken: set[str] = set()
        pages: list[Page] = []
        seen_pages: set[str] = set()
        custom_count = 0
        for item in raw.get("pages") or []:
            page = Page.from_dict(item, taken_ids=taken)
            if page is None or page.id in seen_pages:
                notes.append("page invalide ou dupliquée ignorée")
                continue
            if page.id in BUILTIN_PAGE_INFO:
                page.builtin = True
            else:
                page.builtin = False
                custom_count += 1
                if custom_count > MAX_CUSTOM_PAGES:
                    notes.append("trop de pages personnalisées : surplus conservé hors affichage")
                    page.extras["_hidden_overflow"] = True
            seen_pages.add(page.id)
            pages.append(page)
        preset = _choice(raw.get("preset"), available_presets(), PRESET_COCKPIT)
        # Pages intégrées manquantes : recréées depuis le préréglage courant
        # (ou Cockpit), sans toucher aux pages existantes.
        missing = [pid for pid in BUILTIN_PAGE_ORDER if pid not in seen_pages]
        if missing:
            template = {p.id: p for p in builtin_pages(preset if preset != PRESET_CUSTOM else PRESET_COCKPIT, taken)}
            if pages:
                notes.append("pages intégrées manquantes recréées : " + ", ".join(missing))
            for pid in missing:
                pages.append(template[pid])
            # Ordre stable : pages intégrées d'abord (ordre canonique), puis
            # pages personnalisées dans leur ordre d'origine.
            order = {pid: index for index, pid in enumerate(BUILTIN_PAGE_ORDER)}
            builtin = sorted((p for p in pages if p.builtin), key=lambda p: order[p.id])
            custom = [p for p in pages if not p.builtin]
            pages = builtin + custom if not raw.get("pages") else pages
        snapshot = raw.get("custom_snapshot")
        if snapshot is not None and not (isinstance(snapshot, list) and all(isinstance(i, dict) for i in snapshot)):
            notes.append("instantané de mise en page personnalisée invalide ignoré")
            snapshot = None
        return cls(
            view_mode=_choice(raw.get("view_mode"), VIEW_MODES, "multipage"),
            preset=preset,
            pages=pages,
            custom_snapshot=copy.deepcopy(snapshot),
            extras=_extras(raw, cls.KEYS),
        )


@dataclass
class NotificationPrefs:
    categories: dict[str, dict[str, bool]] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)

    KEYS = {"categories"}

    def __post_init__(self) -> None:
        for key, (_label, dashboard, native) in NOTIFICATION_CATEGORIES.items():
            entry = self.categories.setdefault(key, {})
            entry.setdefault("dashboard", dashboard)
            entry.setdefault("native", native)

    def wants(self, category: str, channel: str) -> bool:
        entry = self.categories.get(category)
        if entry is None:
            return False
        return bool(entry.get(channel, False))

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.extras)
        payload["categories"] = copy.deepcopy(self.categories)
        return payload

    @classmethod
    def from_dict(cls, raw: Any, notes: list[str]) -> "NotificationPrefs":
        if not isinstance(raw, dict):
            return cls()
        categories: dict[str, dict[str, bool]] = {}
        source = raw.get("categories") if isinstance(raw.get("categories"), dict) else {}
        for key, value in source.items():
            if key in NOTIFICATION_CATEGORIES and isinstance(value, dict):
                _label, d_default, n_default = NOTIFICATION_CATEGORIES[key]
                entry = {k: copy.deepcopy(v) for k, v in value.items() if k not in {"dashboard", "native"}}
                entry["dashboard"] = _bool(value.get("dashboard"), d_default)
                entry["native"] = _bool(value.get("native"), n_default)
                categories[key] = entry
            elif key not in NOTIFICATION_CATEGORIES:
                # Catégorie d'une version future : conservée telle quelle.
                categories[key] = copy.deepcopy(value)
        return cls(categories=categories, extras=_extras(raw, cls.KEYS))


@dataclass
class ClosePrefs:
    mode: str = "persistent"
    interactive: dict[str, bool] = field(default_factory=dict)
    close_hint_shown: bool = False
    extras: dict[str, Any] = field(default_factory=dict)

    KEYS = {"mode", "interactive", "close_hint_shown"}

    def __post_init__(self) -> None:
        for key, (_label, default) in INTERACTIVE_OPTIONS.items():
            self.interactive.setdefault(key, default)

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.extras)
        payload.update(mode=self.mode, interactive=dict(self.interactive), close_hint_shown=self.close_hint_shown)
        return payload

    @classmethod
    def from_dict(cls, raw: Any, notes: list[str]) -> "ClosePrefs":
        if not isinstance(raw, dict):
            return cls()
        interactive_raw = raw.get("interactive") if isinstance(raw.get("interactive"), dict) else {}
        interactive = {
            key: _bool(interactive_raw.get(key), default) for key, (_label, default) in INTERACTIVE_OPTIONS.items()
        }
        return cls(
            mode=_choice(raw.get("mode"), CLOSE_MODES, "persistent"),
            interactive=interactive,
            close_hint_shown=_bool(raw.get("close_hint_shown"), False),
            extras=_extras(raw, cls.KEYS),
        )


@dataclass
class MonitoringPrefs:
    enabled: bool = True
    interval_s: int = 2
    background: bool = False
    cpu_threshold: int = 90
    ram_threshold: int = 90
    disk_threshold: int = 95
    extras: dict[str, Any] = field(default_factory=dict)

    KEYS = {"enabled", "interval_s", "background", "cpu_threshold", "ram_threshold", "disk_threshold"}

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.extras)
        payload.update(
            enabled=self.enabled, interval_s=self.interval_s, background=self.background,
            cpu_threshold=self.cpu_threshold, ram_threshold=self.ram_threshold,
            disk_threshold=self.disk_threshold,
        )
        return payload

    @classmethod
    def from_dict(cls, raw: Any, notes: list[str]) -> "MonitoringPrefs":
        if not isinstance(raw, dict):
            return cls()
        return cls(
            enabled=_bool(raw.get("enabled"), True),
            interval_s=_int(raw.get("interval_s"), 2, 1, 60),
            background=_bool(raw.get("background"), False),
            cpu_threshold=_int(raw.get("cpu_threshold"), 90, 50, 100),
            ram_threshold=_int(raw.get("ram_threshold"), 90, 50, 100),
            disk_threshold=_int(raw.get("disk_threshold"), 95, 50, 100),
            extras=_extras(raw, cls.KEYS),
        )


@dataclass
class SecurityPrefs:
    #: action_id → demander confirmation ? (seulement pour les actions de
    #: niveau 2 abaissables ; la politique ignore le reste).
    confirm: dict[str, bool] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)

    KEYS = {"confirm"}

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.extras)
        payload["confirm"] = dict(self.confirm)
        return payload

    @classmethod
    def from_dict(cls, raw: Any, notes: list[str]) -> "SecurityPrefs":
        if not isinstance(raw, dict):
            return cls()
        source = raw.get("confirm") if isinstance(raw.get("confirm"), dict) else {}
        confirm = {str(k)[:80]: _bool(v, True) for k, v in source.items()}
        return cls(confirm=confirm, extras=_extras(raw, cls.KEYS))


@dataclass
class GeneralPrefs:
    open_on_launch: bool = False
    auto_update_check: bool = False
    last_update_check: float | None = None
    last_notified_version: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    KEYS = {"open_on_launch", "auto_update_check", "last_update_check", "last_notified_version"}

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.extras)
        payload.update(
            open_on_launch=self.open_on_launch, auto_update_check=self.auto_update_check,
            last_update_check=self.last_update_check, last_notified_version=self.last_notified_version,
        )
        return payload

    @classmethod
    def from_dict(cls, raw: Any, notes: list[str]) -> "GeneralPrefs":
        if not isinstance(raw, dict):
            return cls()
        last = raw.get("last_update_check")
        if not isinstance(last, (int, float)) or isinstance(last, bool) or last < 0:
            last = None
        return cls(
            open_on_launch=_bool(raw.get("open_on_launch"), False),
            auto_update_check=_bool(raw.get("auto_update_check"), False),
            last_update_check=float(last) if last is not None else None,
            last_notified_version=str(raw.get("last_notified_version") or "")[:32],
            extras=_extras(raw, cls.KEYS),
        )


# ---------------------------------------------------------------------------
# Configuration complète
# ---------------------------------------------------------------------------

_SECTION_TYPES = {
    "appearance": AppearancePrefs,
    "window": WindowPrefs,
    "layout": LayoutPrefs,
    "notifications": NotificationPrefs,
    "close": ClosePrefs,
    "monitoring": MonitoringPrefs,
    "security": SecurityPrefs,
    "general": GeneralPrefs,
}
_ROOT_KEYS = {"schema_version", "app", *_SECTION_TYPES}


@dataclass
class DashboardConfig:
    schema_version: int = CONFIG_SCHEMA_VERSION
    appearance: AppearancePrefs = field(default_factory=AppearancePrefs)
    window: WindowPrefs = field(default_factory=WindowPrefs)
    layout: LayoutPrefs = field(default_factory=LayoutPrefs)
    notifications: NotificationPrefs = field(default_factory=NotificationPrefs)
    close: ClosePrefs = field(default_factory=ClosePrefs)
    monitoring: MonitoringPrefs = field(default_factory=MonitoringPrefs)
    security: SecurityPrefs = field(default_factory=SecurityPrefs)
    general: GeneralPrefs = field(default_factory=GeneralPrefs)
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def defaults(cls) -> "DashboardConfig":
        config = cls()
        config.layout.pages = builtin_pages(PRESET_COCKPIT)
        return config

    def to_dict(self) -> dict[str, Any]:
        payload = copy.deepcopy(self.extras)
        payload["schema_version"] = max(int(self.schema_version), CONFIG_SCHEMA_VERSION)
        payload["app"] = "jarvis-dashboard"
        for name in _SECTION_TYPES:
            payload[name] = getattr(self, name).to_dict()
        return payload

    def copy(self) -> "DashboardConfig":
        notes: list[str] = []
        return config_from_dict(self.to_dict(), notes)[0]


def _migrate_v0_to_v1(raw: dict[str, Any]) -> dict[str, Any]:
    """v0 = fichier sans ``schema_version`` (brouillon/édité à la main).

    Les clés plates historiques éventuelles (``theme``, ``view_mode``) sont
    rangées dans leur section ; tout le reste est conservé tel quel.
    """
    migrated = dict(raw)
    appearance = dict(migrated.get("appearance") or {}) if isinstance(migrated.get("appearance"), dict) else {}
    layout = dict(migrated.get("layout") or {}) if isinstance(migrated.get("layout"), dict) else {}
    for key in ("theme", "accent", "glow", "density", "animation_level"):
        if key in migrated and key not in appearance:
            appearance[key] = migrated.pop(key)
    if "view_mode" in migrated and "view_mode" not in layout:
        layout["view_mode"] = migrated.pop("view_mode")
    if appearance:
        migrated["appearance"] = appearance
    if layout:
        migrated["layout"] = layout
    migrated["schema_version"] = 1
    return migrated


#: Migrations indexées par version CIBLE (même convention que src.memory).
MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {1: _migrate_v0_to_v1}


def migrate(raw: dict[str, Any], notes: list[str]) -> tuple[dict[str, Any], int]:
    """Applique les migrations manquantes ; retourne (données, version d'origine)."""
    version = raw.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        version = 0
    original = version
    data = dict(raw)
    for target in sorted(MIGRATIONS):
        if target <= version:
            continue
        try:
            data = MIGRATIONS[target](data)
            version = target
            notes.append(f"préférences migrées vers le schéma v{target}")
        except Exception as exc:  # pragma: no cover - défensif
            notes.append(f"migration v{target} impossible ({exc}) : valeurs par défaut pour les champs concernés")
            break
    return data, original


def config_from_dict(raw: dict[str, Any], notes: list[str]) -> tuple[DashboardConfig, int]:
    data, original = migrate(raw, notes)
    config = DashboardConfig()
    config.schema_version = max(original, CONFIG_SCHEMA_VERSION)
    for name, section_type in _SECTION_TYPES.items():
        try:
            setattr(config, name, section_type.from_dict(data.get(name), notes))
        except Exception as exc:  # pragma: no cover - défensif : jamais bloquant
            notes.append(f"section {name} illisible ({exc}) : valeurs par défaut")
            setattr(config, name, section_type())
    if not config.layout.pages:
        config.layout.pages = builtin_pages(PRESET_COCKPIT)
    config.extras = _extras(data, _ROOT_KEYS)
    return config, original


@dataclass
class LoadResult:
    config: DashboardConfig
    #: "defaults" (aucun fichier), "loaded", "repaired", "recovered"
    #: (fichier corrompu sauvegardé puis remplacé), "newer" (schéma futur),
    #: "unreadable" (erreur d'E/S).
    status: str
    notes: list[str] = field(default_factory=list)
    backup_path: str | None = None


def _backup_corrupt(path: str) -> str | None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base, _ext = os.path.splitext(path)
    target = f"{base}.corrupt-{stamp}.json"
    counter = 1
    while os.path.exists(target):
        target = f"{base}.corrupt-{stamp}-{counter}.json"
        counter += 1
    try:
        shutil.copy2(path, target)
        return target
    except OSError:
        return None


def load_config(path: str | os.PathLike | None) -> LoadResult:
    """Charge les préférences. Ne lève jamais ; ne bloque jamais le lancement."""
    if not path:
        return LoadResult(DashboardConfig.defaults(), "defaults")
    path = str(path)
    if not os.path.exists(path):
        return LoadResult(DashboardConfig.defaults(), "defaults")
    try:
        # Lecture partagée (json_state) : ne bloque jamais une sauvegarde
        # atomique concurrente du même fichier.
        text = read_text(path)
    except UnicodeDecodeError:
        text = None
    except OSError as exc:
        return LoadResult(DashboardConfig.defaults(), "unreadable", [f"lecture impossible : {exc}"])
    raw: Any = None
    if text is not None:
        try:
            raw = json.loads(text)
        except ValueError:
            raw = None
    if not isinstance(raw, dict):
        backup = _backup_corrupt(path)
        note = "fichier de préférences corrompu : valeurs par défaut utilisées"
        if backup:
            note += f" (copie conservée : {os.path.basename(backup)})"
        else:
            note += " (copie de sauvegarde impossible : fichier laissé en place)"
        result = LoadResult(DashboardConfig.defaults(), "recovered", [note], backup)
        if not backup:
            # Sans copie, on refuse d'écraser l'original : le Dashboard
            # fonctionne en mémoire seulement (voir ConfigStore.read_only).
            result.status = "unreadable"
        return result
    notes: list[str] = []
    config, original = config_from_dict(raw, notes)
    if original > CONFIG_SCHEMA_VERSION:
        notes.append(
            f"préférences écrites par une version plus récente (schéma v{original}) : "
            "champs inconnus conservés"
        )
        return LoadResult(config, "newer", notes)
    meaningful = [n for n in notes if not n.startswith("préférences migrées")]
    return LoadResult(config, "repaired" if meaningful else "loaded", notes)


def save_config(config: DashboardConfig, path: str | os.PathLike | None) -> bool:
    return save_dict(path, config.to_dict())


# ---------------------------------------------------------------------------
# Opérations sur la mise en page
# ---------------------------------------------------------------------------


def builtin_snapshot(config: DashboardConfig) -> list[dict[str, Any]]:
    return [page.to_dict() for page in config.layout.pages if page.builtin]


def save_custom_snapshot(config: DashboardConfig) -> None:
    """Enregistre la disposition courante comme préréglage « Personnalisée »."""
    config.layout.custom_snapshot = builtin_snapshot(config)
    config.layout.preset = PRESET_CUSTOM


def apply_preset(config: DashboardConfig, preset: str) -> tuple[bool, str]:
    """Applique un préréglage aux pages intégrées ; pages personnalisées intactes."""
    if preset not in available_presets():
        return False, "Préréglage inconnu."
    taken = {p.id for page in config.layout.pages if not page.builtin for p in page.widgets}
    if preset == PRESET_CUSTOM:
        snapshot = config.layout.custom_snapshot
        if not snapshot:
            return False, "Aucune mise en page personnalisée n'a encore été enregistrée."
        restored: dict[str, Page] = {}
        for raw in snapshot:
            page = Page.from_dict(raw, taken_ids=taken)
            if page is not None and page.id in BUILTIN_PAGE_INFO:
                page.builtin = True
                restored[page.id] = page
        template = {p.id: p for p in builtin_pages(PRESET_COCKPIT, taken)}
        new_builtin = [restored.get(pid) or template[pid] for pid in BUILTIN_PAGE_ORDER]
    else:
        fresh = {p.id: p for p in builtin_pages(preset, taken)}
        new_builtin = []
        for pid in BUILTIN_PAGE_ORDER:
            page = fresh[pid]
            current = config.layout.page(pid)
            if current is not None:
                # Titre/icône personnalisés par l'utilisateur conservés.
                page.title = current.title
                page.icon = current.icon
                page.extras = dict(current.extras)
            new_builtin.append(page)
    builtin_iter = iter(new_builtin)
    pages: list[Page] = []
    for page in config.layout.pages:
        pages.append(next(builtin_iter) if page.builtin else page)
    pages.extend(builtin_iter)  # pages intégrées absentes (ne devrait pas arriver)
    config.layout.pages = pages
    config.layout.preset = preset
    return True, ""


def add_custom_page(config: DashboardConfig, title: str) -> tuple[Page | None, str]:
    custom = [p for p in config.layout.pages if not p.builtin]
    if len(custom) >= MAX_CUSTOM_PAGES:
        return None, f"Limite de {MAX_CUSTOM_PAGES} pages personnalisées atteinte."
    name = clean_title(title, "")
    if not name:
        return None, "Le nom de la page est vide."
    page = Page(new_page_id(p.id for p in config.layout.pages), name, False, "◇", [])
    config.layout.pages.append(page)
    return page, ""


def rename_page(config: DashboardConfig, page_id: str, title: str) -> tuple[bool, str]:
    page = config.layout.page(page_id)
    if page is None:
        return False, "Page introuvable."
    name = clean_title(title, "")
    if not name:
        return False, "Le nom de la page est vide."
    page.title = name
    return True, ""


def delete_custom_page(config: DashboardConfig, page_id: str) -> tuple[bool, str]:
    page = config.layout.page(page_id)
    if page is None:
        return False, "Page introuvable."
    if page.builtin:
        return False, "Les pages intégrées ne peuvent pas être supprimées."
    config.layout.pages = [p for p in config.layout.pages if p.id != page_id]
    if config.window.last_page == page_id:
        config.window.last_page = "accueil"
    return True, ""


def move_page(config: DashboardConfig, page_id: str, delta: int) -> bool:
    pages = config.layout.pages
    for index, page in enumerate(pages):
        if page.id == page_id:
            target = max(0, min(len(pages) - 1, index + delta))
            if target == index:
                return False
            pages.insert(target, pages.pop(index))
            return True
    return False


# ---------------------------------------------------------------------------
# Magasin en mémoire (+ sauvegarde différée côté Qt)
# ---------------------------------------------------------------------------


class ConfigStore:
    """Configuration courante + persistance. Sans Qt : la temporisation des
    écritures est assurée par l'appelant (``DashboardController``)."""

    def __init__(self, path: str | os.PathLike | None) -> None:
        self.path = str(path) if path else ""
        result = load_config(self.path)
        self.config = result.config
        self.load_status = result.status
        self.load_notes = list(result.notes)
        self.backup_path = result.backup_path
        #: Un fichier illisible sans copie de sauvegarde n'est jamais écrasé.
        self.read_only = result.status == "unreadable" and os.path.exists(self.path)
        self.dirty = False
        self.last_save_ok: bool | None = None
        self._listeners: list[Callable[[str], None]] = []

    def add_listener(self, listener: Callable[[str], None]) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[str], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def changed(self, section: str) -> None:
        """Signale une modification de section (sauvegarde différée)."""
        self.dirty = True
        for listener in list(self._listeners):
            try:
                listener(section)
            except Exception:
                pass

    def save(self) -> bool:
        if self.read_only or not self.path:
            self.last_save_ok = False
            return False
        ok = save_config(self.config, self.path)
        self.last_save_ok = ok
        if ok:
            self.dirty = False
        return ok
