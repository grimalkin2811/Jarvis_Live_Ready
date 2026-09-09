"""Routines de Jarvis : enchaînements d'outils nommés et rejouables.

Une routine est une suite d'appels d'outils déjà déclarés dans ``src.tools``.
Elle ne peut donc **rien faire de plus** que ce que Jarvis sait déjà faire :
la philosophie de liste blanche du projet est préservée, et les outils
destructeurs sont explicitement interdits dans une routine.

Stockage : un fichier JSON lisible et éditable à la main, par défaut
``~/.jarvis/routines.json`` (``JARVIS_ROUTINES_PATH`` pour le changer).

Format::

    {
      "version": 1,
      "routines": [
        {
          "name": "mode travail",
          "description": "Session de code du matin",
          "enabled": true,
          "schedule": {"time": "09:00", "days": [0, 1, 2, 3, 4]},
          "steps": [
            {"tool": "open_application", "args": {"application": "vscode"}},
            {"tool": "wait", "args": {"seconds": 2}},
            {"tool": "set_volume", "args": {"volume": 30}}
          ]
        }
      ]
    }

Les étapes peuvent aussi être décrites par une mini-syntaxe très pratique à
dicter, acceptée par les outils vocaux ::

    open_application(vscode); wait(2); set_volume(30); open_website(spotify)
"""

from __future__ import annotations

import json
import os
import re
import threading
import time

from .routine_presets import builtin_routines
from . import paths
from .timeparse import describe_schedule, normalize, parse_schedule

DEFAULT_DATA_DIR = str(paths.data_dir())
DEFAULT_ROUTINES_PATH = os.environ.get("JARVIS_ROUTINES_PATH", str(paths.routines_file()))

#: Outils interdits dans une routine : irréversibles, ou sources de récursion.
FORBIDDEN_TOOLS = {
    "shutdown_pc",
    "restart_pc",
    "delete_notes",
    "clear_memory",
    "forget",
    "delete_memory",
    "update_memory",
    "create_routine",
    "update_routine",
    "delete_routine",
    "run_routine",
    "cancel_reminder",
}

#: Étape interne (pas un outil) : pause entre deux actions.
WAIT_TOOL = "wait"
MAX_WAIT_SECONDS = 60
MAX_STEPS = 25
MAX_ROUTINES = 50

_REGISTRY_LOCK = threading.RLock()
_TOOL_FUNCTIONS: dict | None = None
_TOOL_DECLARATIONS: list | None = None


# ---------------------------------------------------------------------------
# Registre d'outils (injecté par src.tools pour éviter un import circulaire)
# ---------------------------------------------------------------------------


def set_tool_registry(functions: dict, declarations: list | None = None) -> None:
    """Enregistre les outils disponibles. Appelé par ``src.tools``."""
    global _TOOL_FUNCTIONS, _TOOL_DECLARATIONS
    with _REGISTRY_LOCK:
        _TOOL_FUNCTIONS = functions
        _TOOL_DECLARATIONS = declarations or []


def _registry() -> tuple[dict, list]:
    global _TOOL_FUNCTIONS, _TOOL_DECLARATIONS
    with _REGISTRY_LOCK:
        if _TOOL_FUNCTIONS is None:
            try:  # Import tardif : évite la dépendance circulaire au chargement.
                from . import tools as _tools

                _TOOL_FUNCTIONS = _tools.TOOL_FUNCTIONS
                _TOOL_DECLARATIONS = _tools.TOOL_DECLARATIONS
            except Exception:
                _TOOL_FUNCTIONS = {}
                _TOOL_DECLARATIONS = []
        return _TOOL_FUNCTIONS, (_TOOL_DECLARATIONS or [])


def available_tools() -> list[str]:
    """Outils utilisables dans une routine, triés."""
    functions, _ = _registry()
    return sorted({WAIT_TOOL} | (set(functions) - FORBIDDEN_TOOLS))


def _parameter_order(tool: str) -> list[str]:
    """Ordre des paramètres d'un outil, pour accepter les arguments positionnels."""
    if tool == WAIT_TOOL:
        return ["seconds"]
    _, declarations = _registry()
    for decl in declarations:
        if decl.get("name") == tool:
            params = decl.get("parameters") or {}
            properties = list((params.get("properties") or {}).keys())
            required = [name for name in (params.get("required") or []) if name in properties]
            return required + [name for name in properties if name not in required]
    return []


# ---------------------------------------------------------------------------
# Résultats normalisés
# ---------------------------------------------------------------------------


def _ok(**payload) -> dict:
    result = {"success": True}
    result.update(payload)
    return result


def _err(message: str, **payload) -> dict:
    result = {"success": False, "error": message}
    result.update(payload)
    return result


# ---------------------------------------------------------------------------
# Analyse des étapes
# ---------------------------------------------------------------------------


def _coerce(value: str):
    text = str(value).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    lowered = text.lower()
    if lowered in {"true", "vrai", "oui"}:
        return True
    if lowered in {"false", "faux", "non"}:
        return False
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def _split_top_level(text: str, separators: str) -> list[str]:
    """Découpe en ignorant ce qui est entre parenthèses ou entre guillemets."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    for char in text:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            current.append(char)
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char in separators and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def _parse_call(expression: str) -> dict | None:
    """``set_volume(volume=30)`` ou ``set_volume(30)`` → dict d'étape."""
    match = re.match(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:\((?P<args>.*)\))?\s*$", expression, re.DOTALL)
    if not match:
        return None

    tool = match.group(1)
    raw_args = (match.groupdict().get("args") or "").strip()
    args: dict = {}

    if raw_args:
        order = _parameter_order(tool)
        positional = 0
        for chunk in _split_top_level(raw_args, ","):
            key_value = re.match(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.+)$", chunk, re.DOTALL)
            if key_value:
                args[key_value.group(1)] = _coerce(key_value.group(2))
            else:
                if positional < len(order):
                    args[order[positional]] = _coerce(chunk)
                elif positional == 0:
                    args["value"] = _coerce(chunk)
                positional += 1

    return {"tool": tool, "args": args}


def parse_steps(steps) -> tuple[list[dict], list[str]]:
    """Normalise des étapes venant du modèle, du fichier JSON ou de la voix.

    Renvoie ``(étapes_valides, erreurs)``.
    """
    functions, _ = _registry()
    errors: list[str] = []
    raw_steps: list = []

    if steps is None:
        return [], ["Aucune étape fournie."]

    if isinstance(steps, str):
        text = steps.strip()
        if not text:
            return [], ["Aucune étape fournie."]
        if text.startswith("[") or text.startswith("{"):
            try:
                decoded = json.loads(text)
                raw_steps = decoded if isinstance(decoded, list) else [decoded]
            except Exception:
                raw_steps = _split_top_level(text, ";\n")
        else:
            raw_steps = _split_top_level(text, ";\n")
    elif isinstance(steps, (list, tuple)):
        raw_steps = list(steps)
    elif isinstance(steps, dict):
        raw_steps = [steps]
    else:
        return [], ["Format d'étapes non reconnu."]

    parsed: list[dict] = []
    for raw in raw_steps:
        step: dict | None
        if isinstance(raw, str):
            step = _parse_call(raw)
            if step is None:
                errors.append(f"Étape illisible : {raw!r}")
                continue
        elif isinstance(raw, dict):
            tool = str(raw.get("tool") or raw.get("name") or "").strip()
            if not tool:
                errors.append("Étape sans nom d'outil.")
                continue
            args = raw.get("args") or raw.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    inline = _parse_call(f"{tool}({args})")
                    args = inline["args"] if inline else {}
            if not isinstance(args, dict):
                args = {}
            step = {"tool": tool, "args": args}
        else:
            errors.append(f"Étape illisible : {raw!r}")
            continue

        tool = step["tool"]
        if tool in FORBIDDEN_TOOLS:
            errors.append(f"Outil interdit dans une routine : {tool}.")
            continue
        if tool != WAIT_TOOL and tool not in functions:
            errors.append(f"Outil inconnu : {tool}.")
            continue
        if tool == WAIT_TOOL:
            try:
                seconds = float(step["args"].get("seconds", 1))
            except Exception:
                seconds = 1.0
            step["args"] = {"seconds": max(0.0, min(MAX_WAIT_SECONDS, seconds))}

        parsed.append(step)
        if len(parsed) >= MAX_STEPS:
            errors.append(f"Routine tronquée à {MAX_STEPS} étapes.")
            break

    return parsed, errors


def fatal_errors(errors: list[str]) -> list[str]:
    """Erreurs qui doivent faire echouer la creation plutot que d'etre ignorees.

    Retirer silencieusement une etape refusee serait trompeur : l'utilisateur
    croirait avoir une routine qui eteint le PC alors qu'elle ne le fait pas.
    """
    return [
        error
        for error in errors
        if error.startswith("Outil interdit") or error.startswith("Outil inconnu")
    ]


def steps_to_text(steps: list[dict]) -> str:
    """Rendu compact et prononçable des étapes."""
    rendered = []
    for step in steps:
        args = step.get("args") or {}
        if args:
            inner = ", ".join(f"{key}={value}" for key, value in args.items())
            rendered.append(f"{step['tool']}({inner})")
        else:
            rendered.append(f"{step['tool']}()")
    return " ; ".join(rendered)


# ---------------------------------------------------------------------------
# Gestionnaire de routines
# ---------------------------------------------------------------------------


class RoutineManager:
    """Lecture / écriture / exécution des routines. Jamais bloquant.

    Toute erreur (fichier corrompu, disque en lecture seule, outil en échec)
    est capturée et renvoyée sous forme de dictionnaire, comme le reste de la
    boîte à outils de Jarvis.
    """

    def __init__(
        self, path: str | os.PathLike | None = None, enabled: bool = True,
        *, include_presets: bool = True,
    ) -> None:
        self.path = str(path or DEFAULT_ROUTINES_PATH)
        self.enabled = bool(enabled)
        self._lock = threading.RLock()
        self.last_error: str | None = None
        self._on_run = None
        if self.enabled and include_presets:
            self.install_presets()

    # -- Hooks ---------------------------------------------------------
    def set_run_hook(self, hook) -> None:
        """Callback ``hook(name, result)`` appelé après chaque exécution."""
        self._on_run = hook

    # -- Persistance ---------------------------------------------------
    def _empty(self) -> dict:
        return {"version": 1, "routines": []}

    def _load(self, *, sanitize: bool = True) -> dict:
        if not os.path.exists(self.path):
            self.last_error = None
            return self._empty()
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            self.last_error = f"Fichier de routines illisible : {exc}"
            return self._empty()

        if not isinstance(payload, dict):
            payload = {"version": 1, "routines": payload if isinstance(payload, list) else []}
        routines = payload.get("routines")
        if not isinstance(routines, list):
            routines = []
        payload["routines"] = [
            self._sanitize(item) if sanitize else dict(item)
            for item in routines if isinstance(item, dict)
        ]
        payload["routines"] = [item for item in payload["routines"] if item]
        self.last_error = None
        return payload

    def _save(self, payload: dict) -> bool:
        try:
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            temp = f"{self.path}.tmp"
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temp, self.path)
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = f"Écriture impossible : {exc}"
            return False

    def _sanitize(self, item: dict) -> dict | None:
        name = str(item.get("name") or "").strip()
        if not name:
            return None
        steps, _ = parse_steps(item.get("steps"))
        schedule = parse_schedule(item.get("schedule")) if item.get("schedule") else None
        try:
            run_count = max(0, int(item.get("run_count") or 0))
        except (TypeError, ValueError):
            run_count = 0
        return {
            **item,
            "name": name,
            "preset_id": str(item.get("preset_id") or ""),
            "description": str(item.get("description") or "").strip(),
            "enabled": bool(item.get("enabled", True)),
            "schedule": schedule,
            "steps": steps,
            "last_run": item.get("last_run") or None,
            "run_count": run_count,
        }

    def install_presets(self) -> dict:
        """Ajout idempotent, sans écraser une routine ni réactiver un choix.

        Le journal d'installation survit à une suppression explicite : une
        routine supprimée ne réapparaît pas au prochain lancement. Les presets
        ne consomment pas les 50 emplacements de routines personnelles.
        """
        if not self.enabled:
            return _err("Routines désactivées.")
        with self._lock:
            # Migration additive : les étapes/horaires personnels sont conservés
            # tels quels, même s'ils nécessitent un outil actuellement absent.
            payload = self._load(sanitize=False)
            if self.last_error:
                # Ne jamais remplacer un fichier illisible par le catalogue.
                return _err(self.last_error)
            installed = payload.get("installed_presets", [])
            installed = set(x for x in installed if isinstance(x, str)) if isinstance(installed, list) else set()
            previous = set(installed)
            existing_ids = {str(item.get("preset_id") or "") for item in payload["routines"]}
            names = {normalize(item.get("name")) for item in payload["routines"]}
            added = []
            for preset in builtin_routines():
                preset_id = preset["preset_id"]
                if preset_id in installed:
                    continue
                if preset_id not in existing_ids:
                    # Même un homonyme personnel doit rester intact.
                    base_name = preset["name"]
                    suffix = 1
                    while normalize(preset["name"]) in names:
                        preset["name"] = f"{base_name} (Jarvis{'' if suffix == 1 else ' ' + str(suffix)})"
                        suffix += 1
                    payload["routines"].append(preset)
                    names.add(normalize(preset["name"]))
                    added.append(preset["name"])
                installed.add(preset_id)
            if installed != previous or added:
                payload["installed_presets"] = sorted(installed)
                if not self._save(payload):
                    return _err(self.last_error or "Sauvegarde impossible.")
            return _ok(installees=added)

    # -- Recherche ------------------------------------------------------
    def _find(self, payload: dict, name: str) -> dict | None:
        target = normalize(name)
        if not target:
            return None
        routines = payload["routines"]
        for routine in routines:
            if normalize(routine["name"]) == target:
                return routine
        for routine in routines:
            if normalize(routine.get("preset_id")) == target:
                return routine
        # Correspondance tolérante : « lance mode travail » → « mode travail ».
        matches = []
        for routine in routines:
            candidate = normalize(routine["name"])
            if candidate and (candidate in target or target in candidate):
                matches.append(routine)
        # « pause » ne doit pas activer arbitrairement l'une des trois pauses.
        return matches[0] if len(matches) == 1 else None

    def mtime(self) -> float:
        try:
            return os.path.getmtime(self.path)
        except Exception:
            return 0.0

    # -- API ------------------------------------------------------------
    def list_routines(self) -> dict:
        if not self.enabled:
            return _err("Routines désactivées.")
        with self._lock:
            payload = self._load()
        routines = [
            {
                "name": item["name"],
                "preset_id": item.get("preset_id", ""),
                "description": item["description"],
                "enabled": item["enabled"],
                "etapes": len(item["steps"]),
                "planification": describe_schedule(item["schedule"]),
                "derniere_execution": item["last_run"],
            }
            for item in payload["routines"]
        ]
        return _ok(routines=routines, count=len(routines), fichier=self.path)

    def describe_routine(self, name: str) -> dict:
        if not self.enabled:
            return _err("Routines désactivées.")
        with self._lock:
            payload = self._load()
            routine = self._find(payload, name)
        if routine is None:
            return _err(f"Routine introuvable : {name}.", disponibles=[r["name"] for r in payload["routines"]])
        return _ok(
            nom=routine["name"],
            preset_id=routine.get("preset_id", ""),
            description=routine["description"],
            active=routine["enabled"],
            planification=describe_schedule(routine["schedule"]),
            etapes=steps_to_text(routine["steps"]),
            nombre_etapes=len(routine["steps"]),
            derniere_execution=routine["last_run"],
            executions=routine["run_count"],
        )

    def create_routine(self, name, steps, description="", schedule=None, enabled=True) -> dict:
        if not self.enabled:
            return _err("Routines désactivées.")
        clean_name = str(name or "").strip()
        if not clean_name:
            return _err("Nom de routine manquant.")
        if len(clean_name) > 60:
            return _err("Nom de routine trop long (60 caractères maximum).")

        parsed_steps, errors = parse_steps(steps)
        blocking = fatal_errors(errors)
        if blocking:
            return _err(
                " ".join(blocking),
                refuse=True,
                outils_disponibles=available_tools(),
            )
        if not parsed_steps:
            return _err(
                ("Aucune étape valide. " + " ".join(errors)).strip(),
                outils_disponibles=available_tools(),
            )

        parsed_schedule = parse_schedule(schedule) if schedule else None
        if schedule and parsed_schedule is None:
            return _err("Planification incomprise. Exemple : « tous les jours à 9h ».")

        with self._lock:
            payload = self._load()
            if self.last_error:
                return _err(self.last_error)
            if sum(not item.get("preset_id") for item in payload["routines"]) >= MAX_ROUTINES:
                return _err(f"Trop de routines personnelles (maximum {MAX_ROUTINES}).")
            existing = self._find(payload, clean_name)
            if existing is not None and normalize(clean_name) in {
                normalize(existing["name"]), normalize(existing.get("preset_id")),
            }:
                return _err(
                    f"La routine « {existing['name']} » existe déjà. Utilise update_routine pour la modifier.",
                    existe=True,
                )
            routine = {
                "name": clean_name,
                "description": str(description or "").strip(),
                "enabled": bool(enabled),
                "schedule": parsed_schedule,
                "steps": parsed_steps,
                "last_run": None,
                "run_count": 0,
            }
            payload["routines"].append(routine)
            if not self._save(payload):
                return _err(self.last_error or "Sauvegarde impossible.")

        return _ok(
            nom=clean_name,
            etapes=steps_to_text(parsed_steps),
            nombre_etapes=len(parsed_steps),
            planification=describe_schedule(parsed_schedule),
            avertissements=errors,
            fichier=self.path,
        )

    def update_routine(self, name, steps=None, description=None, schedule=None, enabled=None) -> dict:
        if not self.enabled:
            return _err("Routines désactivées.")
        with self._lock:
            payload = self._load()
            routine = self._find(payload, name)
            if routine is None:
                return _err(f"Routine introuvable : {name}.", disponibles=[r["name"] for r in payload["routines"]])

            warnings: list[str] = []

            if steps is not None:
                parsed_steps, errors = parse_steps(steps)
                blocking = fatal_errors(errors)
                if blocking:
                    return _err(" ".join(blocking), refuse=True, outils_disponibles=available_tools())
                if not parsed_steps:
                    return _err(("Aucune étape valide. " + " ".join(errors)).strip())
                routine["steps"] = parsed_steps
                warnings.extend(errors)

            if description is not None:
                routine["description"] = str(description).strip()

            if schedule is not None:
                text = str(schedule).strip() if not isinstance(schedule, dict) else schedule
                if isinstance(text, str) and normalize(text) in {"aucune", "jamais", "none", "off", ""}:
                    routine["schedule"] = None
                else:
                    parsed_schedule = parse_schedule(text)
                    if parsed_schedule is None:
                        return _err("Planification incomprise. Exemple : « en semaine à 8h30 ».")
                    routine["schedule"] = parsed_schedule

            if enabled is not None:
                routine["enabled"] = bool(enabled)

            if not self._save(payload):
                return _err(self.last_error or "Sauvegarde impossible.")

        return _ok(
            nom=routine["name"],
            etapes=steps_to_text(routine["steps"]),
            planification=describe_schedule(routine["schedule"]),
            active=routine["enabled"],
            avertissements=warnings,
        )

    def delete_routine(self, name, confirm=False) -> dict:
        if not self.enabled:
            return _err("Routines désactivées.")
        with self._lock:
            payload = self._load()
            routine = self._find(payload, name)
            if routine is None:
                return _err(f"Routine introuvable : {name}.", disponibles=[r["name"] for r in payload["routines"]])
            if not confirm:
                return _err(
                    f"Confirmation requise pour supprimer « {routine['name']} ».",
                    confirmation_requise=True,
                    nom=routine["name"],
                )
            payload["routines"] = [item for item in payload["routines"] if item is not routine]
            if not self._save(payload):
                return _err(self.last_error or "Sauvegarde impossible.")
        return _ok(nom=routine["name"], supprimee=True)

    def run_routine(self, name, dry_run=False) -> dict:
        """Exécute les étapes d'une routine, séquentiellement."""
        if not self.enabled:
            return _err("Routines désactivées.")
        with self._lock:
            payload = self._load()
            routine = self._find(payload, name)
            if routine is None:
                return _err(f"Routine introuvable : {name}.", disponibles=[r["name"] for r in payload["routines"]])
            steps = list(routine["steps"])
            routine_name = routine["name"]
            if not routine["enabled"] and not dry_run:
                return _err(
                    f"La routine « {routine_name} » est désactivée. Active-la d'abord.",
                    disabled=True,
                )

        if not steps:
            return _err(f"La routine « {routine_name} » ne contient aucune étape.")

        if dry_run:
            return _ok(nom=routine_name, simulation=True, etapes=steps_to_text(steps))

        functions, _ = _registry()
        results = []
        succeeded = 0
        notification_handled = False

        for index, step in enumerate(steps, start=1):
            tool = step["tool"]
            args = dict(step.get("args") or {})

            if tool == WAIT_TOOL:
                time.sleep(max(0.0, min(MAX_WAIT_SECONDS, float(args.get("seconds", 1)))))
                results.append({"etape": index, "outil": tool, "success": True})
                succeeded += 1
                continue

            function = functions.get(tool)
            if function is None:
                results.append({"etape": index, "outil": tool, "success": False, "error": "Outil indisponible"})
                continue

            try:
                outcome = function(**args)
            except TypeError as exc:
                outcome = {"success": False, "error": f"Arguments invalides : {exc}"}
            except Exception as exc:  # pragma: no cover - dépend de la plateforme
                outcome = {"success": False, "error": str(exc)}

            if not isinstance(outcome, dict):
                outcome = {"success": True, "resultat": outcome}

            # Garder le contenu réel pour la voix et éviter une notification
            # générique en plus de celle émise (ou volontairement supprimée)
            # par une étape de notification/alerte conditionnelle.
            notification_handled = notification_handled or "notification" in outcome
            entry = {"etape": index, "outil": tool,
                     "success": bool(outcome.get("success", True)), "resultat": outcome}
            if not entry["success"]:
                entry["error"] = outcome.get("error", "échec")
            results.append(entry)
            if entry["success"]:
                succeeded += 1

        failed = [item for item in results if not item["success"]]

        with self._lock:
            payload = self._load()
            stored = self._find(payload, routine_name)
            if stored is not None:
                stored["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
                stored["run_count"] = int(stored.get("run_count") or 0) + 1
                self._save(payload)

        result = _ok(
            nom=routine_name,
            etapes_reussies=succeeded,
            etapes_totales=len(steps),
            echecs=failed,
            details=results,
            notification_handled=notification_handled,
        )
        result["success"] = not failed

        if self._on_run is not None:
            try:
                self._on_run(routine_name, result)
            except Exception:
                pass

        return result

    def scheduled_routines(self) -> list[dict]:
        """Routines actives possédant une planification horaire."""
        if not self.enabled:
            return []
        with self._lock:
            payload = self._load()
        return [item for item in payload["routines"] if item["enabled"] and item["schedule"]]


# ---------------------------------------------------------------------------
# Instance par défaut
# ---------------------------------------------------------------------------

_DEFAULT_MANAGER: RoutineManager | None = None
_DEFAULT_LOCK = threading.Lock()


def get_default_routine_manager() -> RoutineManager:
    global _DEFAULT_MANAGER
    with _DEFAULT_LOCK:
        if _DEFAULT_MANAGER is None:
            enabled = os.environ.get("JARVIS_ROUTINES_ENABLED", "1").strip().lower() not in {
                "0",
                "false",
                "no",
                "off",
            }
            path = os.environ.get("JARVIS_ROUTINES_PATH", DEFAULT_ROUTINES_PATH)
            _DEFAULT_MANAGER = RoutineManager(path, enabled=enabled)
        return _DEFAULT_MANAGER


def set_default_routine_manager(manager: RoutineManager | None) -> None:
    global _DEFAULT_MANAGER
    with _DEFAULT_LOCK:
        _DEFAULT_MANAGER = manager
