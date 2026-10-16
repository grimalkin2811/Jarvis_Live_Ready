"""Validation finale automatisée d'une PR de Jarvis — orchestrateur.

Lancé par ``valider_pr.bat`` (racine du dépôt). Bibliothèque standard
uniquement : ce script tourne AVANT que l'environnement de validation existe.

Principes (voir docs/validation/README.md) :

* la tête EXACTE de la PR est testée dans une copie isolée (``git worktree``
  dans le dossier de validation) : la copie de travail de l'utilisateur, ses
  modifications locales et ``main`` ne sont jamais touchés ;
* les dépendances sont installées dans un venv de VALIDATION dédié ; le
  ``.venv`` de développement et l'installation quotidienne sont seulement
  inspectés (jamais modifiés, désinstallés ni mis à jour) ;
* chaque processus de test charge la garde des données (``garde/``) et reçoit
  des dossiers de données temporaires : tout accès aux vraies données de
  Jarvis est bloqué et consigné ;
* aucune fusion, aucun push, aucun tag, aucune publication, aucun message à un
  tiers ; la clé Gemini n'est jamais affichée ni écrite ;
* le rapport est réécrit après chaque étape et dans un ``finally`` : il existe
  même en cas d'échec, d'erreur ou d'interruption (Ctrl+C).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

# Aucun fichier .pyc écrit dans la copie de travail de l'utilisateur.
sys.dont_write_bytecode = True
ICI = Path(__file__).resolve().parent
sys.path.insert(0, str(ICI))
import protocole as P  # noqa: E402

VERSION_PROTOCOLE = "1.0"
PR_DEFAUT = 50
DEPOT_GITHUB_DEFAUT = "grimalkin2811/Jarvis_Live_Ready"
COMMIT_AUDIT = "53ed72e28e6cb644e1e2f004fa5aeacb220ff80b"
DATE_AUDIT = "2026-10-15T19:23:56Z"
WINDOWS = os.name == "nt"
OUTILLAGE = ("scripts/validation/", "valider_pr.bat", "docs/validation/", "tests/test_validation_protocole.py")
VARIABLES_SENSIBLES = re.compile(r"(?i)(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)")
PYSIDE_EXCLUE = "6.12.0"
#: Lignes de verdict des scripts réels officiels (« AUDIO RÉEL : PASS », « S5 RECONNEXION REELLE : FAIL »…).
AVERTISSEMENT_FENETRES = (
    "Pendant les contrôles du Dashboard et de Gemini, de vraies fenêtres du Dashboard s'ouvrent, se ferment et "
    "passent brièvement en plein écran, et une icône Jarvis temporaire apparaît dans la zone de notification : "
    "ne cliquez pas dedans et ne fermez pas la console. Fermer Jarvis avant de lancer rend la preuve de "
    "non-modification des données concluante.")
VERDICT_RE = r"^([A-ZÀ-Ü0-9+][A-ZÀ-Ü0-9+ ]*?) : (PASS|FAIL|NON TESTABLE)\s*$"

VUES = [
    ("Dashboard (tous les tests du Dashboard)", lambda f, n: f.startswith("tests/test_dashboard_")),
    ("Gemini Live — faux serveur (pas une validation réelle)", lambda f, n: re.match(
        r"tests/test_(gemini|live|s6|turn_race|empty_generation|reconnect|session|seed|voice)", f) is not None),
    ("Mémoire et migration SQLite (bases temporaires)", lambda f, n: f in (
        "tests/test_memory.py", "tests/test_dashboard_memory_service.py")),
    ("Isolation des données de test", lambda f, n: f == "tests/test_suite_isolation.py"),
    ("Fenêtres : fermeture, réouverture, persistance", lambda f, n: f == "tests/test_dashboard_window.py"),
    ("Concurrence voix / texte", lambda f, n: f in (
        "tests/test_gemini_text_voice_interplay.py", "tests/test_gemini_text_input.py",
        "tests/test_dashboard_conversation_widget.py")),
    ("Garde PySide6 (fuite de références)", lambda f, n: f == "tests/test_pyside_refcount.py"),
]
VUES_MEMOIRE = {
    "F01": ("Migration de la mémoire v1 → v2 (bases temporaires)", (
        "test_schema_migrates_to_v2_with_revision_table", "test_existing_v1_database_is_migrated_without_data_loss",
        "test_failed_v2_migration_is_rolled_back_then_retried_without_data_loss")),
    "F02": ("Restauration d'une version (bases temporaires)", (
        "test_restore_previous_version_is_itself_reversible",
        "test_restoring_a_version_identical_to_the_current_one_writes_nothing")),
    "F03": ("Limite de l'historique des versions", (
        "test_history_is_bounded", "test_history_keeps_exactly_the_most_recent_versions")),
    "F04": ("Messages de confirmation honnêtes (aucun succès annoncé à tort)", (
        "test_unchanged_update_is_not_written", "test_write_failure_rolls_back_and_reports",
        "test_create_never_overwrites_silently", "test_concurrent_modification_is_detected_even_within_the_same_second",
        "test_restoring_a_version_identical_to_the_current_one_writes_nothing")),
}
ETAPES_DIAG_CI = [
    "tests.test_blob_visibility", "tests.test_menu_hover.HoverColorTests", "tests.test_menu_hover.HoverPaintingTests",
    "tests.test_menu_hover.HoverIntegrationTests", "tests.test_menu_integration", "tests.test_audio_controls",
    "tests.test_menu_state_bridge", "tests.test_menu_backgrounds", "tests.test_menu_layout",
    "tests.test_ui_menu_regression", "tests.test_item_bg_opacity", "tests.test_orb_visibility",
    "tests.test_visibility_bridge", "tests.test_mode_apps", "tests.test_mode_apps_dialog",
    "tests.test_tool_declarations", "tests.test_desktop_overlay", "tests.test_desktop_state_machine",
    "tests.test_desktop_config", "tests.test_desktop_hud", "tests.test_desktop_appearance_dialog",
    "tests.test_desktop_events", "tests.test_desktop_integration", "tests.test_interface_mode",
]


class Annulation(Exception):
    """L'utilisateur a annulé dans la fenêtre de lancement."""


def afficher(texte: str = "") -> None:
    try:
        print(texte, flush=True)
    except UnicodeEncodeError:
        print(texte.encode("ascii", "replace").decode("ascii"), flush=True)


def slug(texte: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", texte.lower()).strip("_")[:40] or "etape"


def sha256_fichier(path: Path, limite: int | None = None) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def user_version_sqlite(path: Path) -> int | None:
    """``PRAGMA user_version`` lu dans l'en-tête (octets 60-63) SANS ouvrir SQLite."""
    try:
        with open(path, "rb") as handle:
            header = handle.read(100)
    except OSError:
        return None
    if len(header) < 64 or not header.startswith(b"SQLite format 3\x00"):
        return None
    return int.from_bytes(header[60:64], "big")


def version_tuple(text: str | None) -> tuple:
    parts = []
    for item in re.split(r"[.+-]", str(text or "")):
        if item.isdigit():
            parts.append(int(item))
        else:
            break
    return tuple(parts)


def dossier_validation(explicite: str | None) -> Path:
    if explicite:
        return Path(explicite).expanduser().resolve()
    if WINDOWS and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "JarvisValidation"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "JarvisValidation"


class Execution:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.debut = time.time()
        self.horodatage = time.strftime("%Y%m%d-%H%M%S")
        self.depot = Path(args.depot).resolve() if args.depot else ICI.parents[1]
        self.base = dossier_validation(args.dossier_validation)
        run = self.base / "runs" / self.horodatage
        index = 2
        while run.exists():
            run = self.base / "runs" / f"{self.horodatage}_{index}"
            index += 1
        self.run = run
        self.logs = run / "journaux"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.masque = P.Masqueur()
        self.rapport = P.Rapport()
        self.interactif = not args.non_interactif and sys.stdin is not None and sys.stdin.isatty()
        self.cle = ""
        self.sources_cle: list[str] = []
        self.consentements = {"gemini_reel": False, "audio_guide": False, "notification_windows": False,
                              "migration_copie": False}
        self.worktree: Path | None = None
        self.commit_teste = ""
        self.slug = args.depot_github
        self.python_base = ""
        self.venv_python: str | None = None
        self.dev_python: str | None = None
        self.python_tests: str | None = None
        self.racines_reelles: list[Path] = []
        self.installation: Path | None = None
        self.jarvis_ouvert: list[str] = []
        self.instantane_avant: dict = {}
        self.statut_git_avant: dict = {}
        self.main_avant: dict = {}
        self.junit: dict | None = None
        self.audio: dict = {}
        self.dashboard_captures: list[str] = []
        self.notification_emise = False
        self.fichier_depot: Path | None = None
        self.fichier_local = self.run / "rapport.md"
        self.instantane_fait = False
        self.installation_qt = ""
        self.pyside612_reproduit: bool | None = None
        self.code_sortie = P.CODE_ERREUR_PROTOCOLE
        self._enregistrer_masques()

    # ------------------------------------------------------------------ utilitaires
    def _enregistrer_masques(self) -> None:
        m = self.masque
        m.chemin(self.run, "<exécution>")
        m.chemin(self.base, "%LOCALAPPDATA%\\JarvisValidation" if WINDOWS else "~/.local/share/JarvisValidation")
        m.chemin(self.depot, "<dépôt>")
        for var, marker in (("LOCALAPPDATA", "%LOCALAPPDATA%"), ("APPDATA", "%APPDATA%"), ("TEMP", "%TEMP%"),
                            ("TMP", "%TEMP%"), ("USERPROFILE", "~")):
            if os.environ.get(var):
                m.chemin(os.environ[var], marker)
        m.chemin(tempfile.gettempdir(), "%TEMP%")
        m.chemin(str(Path.home()), "~")

    def python_venv(self, venv: Path) -> Path:
        return venv / ("Scripts/python.exe" if WINDOWS else "bin/python")

    def rel(self, path: Path | str) -> str:
        return self.masque(str(path))

    def ecrire_rapport(self, final: bool = False) -> None:
        """Réécrit le rapport de CETTE exécution (jamais un rapport antérieur)."""
        try:
            self.rapport.meta["duree"] = f"{(time.time() - self.debut) / 60:.1f} min"
            texte = P.rendre_markdown(self.rapport, self.masque)
            self.fichier_local.write_text(texte, encoding="utf-8")
            (self.run / "rapport.json").write_text(self.masque(self.rapport.to_json()), encoding="utf-8")
            if self.fichier_depot is None:
                dossier = self.depot / "docs" / "validation" / "rapports"
                self.fichier_depot = P.creer_fichier_unique(dossier, f"validation_pr{self.args.pr}_{self.horodatage}")
            self.fichier_depot.write_text(texte, encoding="utf-8")
        except Exception as exc:  # le rapport ne doit jamais faire échouer le protocole
            afficher(f"[rapport] écriture impossible : {exc}")

    def env_enfant(self, nom: str, *, garde: bool = True, cle: bool = False, qt: str | None = "offscreen",
                   isoler: bool = True) -> dict:
        env = {k: v for k, v in os.environ.items() if not VARIABLES_SENSIBLES.search(k)}
        for key in list(env):
            if key.startswith("JARVIS_") and (key.endswith("_DATABASE_PATH") or key in (
                    "JARVIS_USER_CONTENT_DIR", "JARVIS_DATA_DIR")):
                env.pop(key)
        env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                    "PYTHONNOUSERSITE": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"})
        env.pop("PYTHONHOME", None)
        env.pop("PYTHONPATH", None)
        if isoler:
            root = self.run / "donnees" / slug(nom)
            for sub in ("jarvis", "localappdata", "appdata", "home"):
                (root / sub).mkdir(parents=True, exist_ok=True)
            env["JARVIS_DATA_DIR"] = str(root / "jarvis")
            if WINDOWS:
                env["LOCALAPPDATA"] = str(root / "localappdata")
                env["APPDATA"] = str(root / "appdata")
            else:
                env["HOME"] = str(root / "home")
        if qt:
            env["QT_QPA_PLATFORM"] = qt
        else:
            env.pop("QT_QPA_PLATFORM", None)
        if garde:
            env["JARVIS_VALIDATION_GARDE"] = "1"
            env["PYTHONPATH"] = str(ICI / "garde")
            env["JARVIS_VALIDATION_INTERDIT"] = os.pathsep.join(str(p) for p in self.racines_reelles)
            env["JARVIS_VALIDATION_LECTURE_SEULE"] = str(self.depot)
            env["JARVIS_VALIDATION_INCIDENTS"] = str(self.run / "incidents_garde.jsonl")
            env["JARVIS_VALIDATION_ACTIVATIONS"] = str(self.run / "garde_activations.jsonl")
        if self.worktree is not None:
            env["JARVIS_VALIDATION_RACINE"] = str(self.worktree)
        if cle and self.cle:
            env["GEMINI_API_KEY"] = self.cle
        return env

    def executer(self, etape: str, cmd: list[str], *, cwd: Path | None = None, env: dict | None = None,
                 timeout: float = 900, note: str = "", progression: Path | None = None,
                 afficher_sortie: bool = False) -> tuple[int | None, str]:
        """Lance une commande ; sortie journalisée (masquée), commande consignée."""
        index = len(self.rapport.commandes) + 1
        brut = self.logs / f"{index:02d}_{slug(etape)}.brut"
        final = self.logs / f"{index:02d}_{slug(etape)}.log"
        start = time.time()
        code: int | None = None
        afficher(f"   $ {self.masque(' '.join(cmd))}")
        kwargs: dict = {}
        if WINDOWS:
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True
        try:
            with open(brut, "w", encoding="utf-8", errors="replace") as handle:
                proc = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, env=env, stdout=handle,
                                        stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                                        encoding="utf-8", errors="replace", **kwargs)
                last = 0.0
                while True:
                    try:
                        code = proc.wait(timeout=2)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                    elapsed = time.time() - start
                    if progression is not None and time.time() - last > 20:
                        last = time.time()
                        info = P.lire_progression(progression)
                        if info["collectes"]:
                            afficher(f"     … {info['termines']}/{info['collectes']} tests ({elapsed / 60:.1f} min)")
                    if elapsed > timeout:
                        self._tuer(proc)
                        code = None
                        note = (note + " ; " if note else "") + f"délai dépassé ({timeout:.0f} s) : processus arrêté"
                        break
        except FileNotFoundError as exc:
            brut.write_text(f"commande introuvable : {exc}", encoding="utf-8")
            note = (note + " ; " if note else "") + "commande introuvable"
        except KeyboardInterrupt:
            try:
                self._tuer(proc)  # type: ignore[possibly-undefined]
            except Exception:
                pass
            raise
        raw = brut.read_text(encoding="utf-8", errors="replace") if brut.exists() else ""
        final.write_text(self.masque(raw), encoding="utf-8")
        brut.unlink(missing_ok=True)
        self.rapport.commandes.append(P.Commande(etape, " ".join(cmd), code, time.time() - start,
                                                 self.rel(final), note))
        if afficher_sortie:
            for line in self.masque(raw).splitlines()[-12:]:
                afficher(f"     {line}")
        return code, raw

    def _tuer(self, proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        try:
            if WINDOWS:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=30)
            else:
                import signal

                os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            proc.kill()
        try:
            proc.wait(timeout=10)
        except Exception:
            pass

    def git(self, *args: str, timeout: float = 120, etape: str = "", cwd: Path | None = None) -> tuple[int | None, str]:
        """git en LECTURE (ou opérations locales réversibles explicitement listées)."""
        cmd = ["git", "-C", str(cwd or self.depot), *args]
        if etape:
            code, out = self.executer(etape, cmd, timeout=timeout, env=self.env_enfant("git", garde=False, qt=None,
                                                                                       isoler=False))
            return code, out.strip()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=timeout, stdin=subprocess.DEVNULL)
            return proc.returncode, (proc.stdout or "").strip() or (proc.stderr or "").strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, str(exc)

    def api_github(self, chemin: str) -> object:
        url = f"https://api.github.com/repos/{self.slug}/{chemin}"
        request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                       "User-Agent": "jarvis-validation-pr"})
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))

    def probleme(self, gravite: str, titre: str, detail: str = "", emplacement: str = "", controle: str = "") -> None:
        self.rapport.problemes.append(P.Probleme(gravite, titre, detail, emplacement, controle))

    def ctl(self, cid: str, titre: str, *, requis: bool = True, gravite: str = "bloquant") -> P.Controle:
        return self.rapport.ajouter(cid, titre, requis=requis, gravite=gravite)


# ---------------------------------------------------------------------------
# Étapes
# ---------------------------------------------------------------------------


def declarer_controles(x: Execution) -> None:
    """Tous les contrôles existent dès le départ (NON TESTÉ tant qu'ils ne sont pas atteints)."""
    c = x.ctl
    c("A01", "Commit testé = tête distante de la PR")
    c("A02", "Copie de travail locale (branche, commit, retard éventuel)", requis=False, gravite="info")
    c("A03", "Modifications locales préexistantes préservées")
    c("A04", "`main` inchangé (local et distant) pendant le protocole")
    c("A05", "État de la PR (ouverte, non fusionnée)")
    c("A06", "CI GitHub sur le commit testé")
    c("A07", "Nouveaux commentaires depuis l'audit", requis=False, gravite="info")
    c("A08", "Changements de code depuis l'audit (53ed72e)", requis=False, gravite="info")
    c("B01", "Python de l'orchestrateur", requis=False, gravite="info")
    c("B02", "Environnement de validation isolé (requirements de la PR)")
    c("B03", "Contraintes déclarées respectées (pip check, PySide6 < 6.12)")
    c("B04", "Environnement de développement (.venv du dépôt, lecture seule)", requis=False, gravite="important")
    c("B05", "Installation quotidienne (version, Python et PySide6 embarqués)", requis=False, gravite="important")
    c("B06", "Garde PySide6 dans l'environnement de la PR (test_pyside_refcount)")
    c("B07", "Mesure du défaut PySide6 6.12.0 sur ce PC (justification du plafond < 6.12)")
    c("B08", "Scripts de lancement, de packaging et de mise à jour (inspection)", requis=False, gravite="important")
    c("B09", "Compatibilité de l'installation quotidienne avec la PR")
    c("C01", "Suite complète pytest (protocole CI) sous la garde des données")
    for index, (nom, _filtre) in enumerate(VUES, start=2):
        c(f"C{index:02d}", f"Vue : {nom}")
    c("C09", "Étapes diagnostiques de la CI (unittest, mêmes tests exécutés autrement)")
    c("C10", "Protocole officiel du Dashboard (unitaires, intégration, performances, visuel, statique)")
    c("C11", "Lint bloquant de la CI (ruff : src, launcher, run_jarvis.py, version.py)")
    c("C12", "Lint non bloquant de la CI (ruff : UI, tests)", requis=False, gravite="mineur")
    c("C13", "Lint des scripts (ruff : scripts)", requis=False, gravite="mineur")
    c("C14", "Compilation de tout le code (compileall)")
    c("C15", "Test de fumée du code de la PR (run_jarvis.py --smoke-test)")
    c("C16", "Test de fumée de l'exécutable installé (données redirigées)", requis=False, gravite="important")
    titres_d = {
        "D01": "Démarrage et première ouverture du Dashboard",
        "D02": "Points d'entrée : outil vocal, page inconnue, zone de notification, Ctrl+D, fermeture",
        "D03": "Pages et widgets sans erreur, captures natives",
        "D04": "Navigation : temps de changement de page",
        "D05": "Mode édition : ajout, enregistrement, relecture",
        "D06": "Thèmes : bascule à chaud et persistance",
        "D07": "Notes depuis le Dashboard (isolées)",
        "D08": "Tâches personnelles : ajout et achèvement (isolées)",
        "D09": "Tâches de fond : affichage d'une tâche réelle (exécuteur simulé)",
        "D10": "Notifications : toast, centre, notification Windows",
        "D11": "Fermeture/réouverture ×6 sans exception Qt ni fuite",
        "D12": "Persistance des préférences (fichier isolé)",
        "D13": "Plein écran et classes de largeur",
        "D14": "Conversation écrite dans l'interface native (moteur simulé)",
        "D15": "Aucune exception Python ni erreur Qt critique",
        "D16": "Écran, mise à l'échelle et DPI (informatif)",
        "D17": "Dashboard masqué : aucun widget actif, CPU au repos",
    }
    for cid, titre in titres_d.items():
        c(cid, titre, requis=cid != "D16", gravite="info" if cid == "D16" else "bloquant")
    c("D18", "Confirmation visuelle des captures natives (fenêtre finale)", requis=False, gravite="important")
    c("D19", "Notification Windows vue par l'utilisateur (fenêtre finale)", requis=False, gravite="mineur")
    titres_e = {
        "E01": "Conversation vocale normale avant ouverture du Dashboard",
        "E02": "Ouverture du Dashboard pendant une conversation",
        "E03": "Message écrit depuis le Dashboard et réponse",
        "E04": "Continuité du contexte voix → texte → voix",
        "E05": "Reprise de la voix après une réponse écrite",
        "E06": "Message écrit pendant que Jarvis parle ou travaille",
        "E07": "Fermeture et réouverture du Dashboard pendant une réponse",
        "E08": "Interruption, coupure réseau et reconnexion",
        "E09": "Non-régression d'une conversation purement vocale",
        "E10": "Aucune ouverture intempestive quand le Dashboard est seulement mentionné",
    }
    for cid, titre in titres_e.items():
        c(cid, titre + " (Gemini Live RÉEL)")
    c("E11", "Script officiel validate_real_gemini.py (Gemini Live RÉEL)")
    c("E12", "Script officiel validate_reconnect_v174_real.py (Gemini Live RÉEL)")
    c("E13", "Périphériques audio : entrée et sortie utilisables (formats de Jarvis)")
    c("E14", "Haut-parleur : tonalité entendue (test guidé)")
    c("E15", "Micro : niveau détecté pendant 3 s, rien conservé (test guidé)")
    c("E16", "Auto-test des harnais réels contre le faux serveur (ne valide PAS Gemini réel)", requis=False,
      gravite="important")
    for cid, (titre, _tests) in VUES_MEMOIRE.items():
        c(cid, titre)
    c("F05", "Migration de VOTRE base mémoire sur une COPIE (compatibilité)")
    c("F06", "Aucun secret dans les journaux, sorties et rapports")
    c("F07", "Données personnelles et installation non modifiées (instantanés avant/après)")
    c("F08", "Aucun accès aux données réelles pendant les tests (garde)")


def decouvrir(x: Execution) -> None:
    afficher("\n[1/11] Découverte de l'environnement")
    m = x.rapport.meta
    build = ""
    if WINDOWS:
        try:
            build = str(sys.getwindowsversion().build)
        except Exception:
            build = ""
    nom_os = platform.platform()
    if WINDOWS and build.isdigit():
        nom_os = f"Windows {'11' if int(build) >= 22000 else '10'} (build {build}) — {platform.machine()}"
    m.update({"pr": x.args.pr, "debut": time.strftime("%Y-%m-%d %H:%M:%S"), "machine": nom_os,
              "protocole": VERSION_PROTOCOLE, "mode": "interactif" if x.interactif else "non interactif"})
    x.ctl("B01", "").regler(P.REUSSI, f"Python {sys.version.split()[0]} ({platform.architecture()[0]})",
                            x.rel(sys.executable))
    racines = []
    if os.environ.get("JARVIS_DATA_DIR"):
        racines.append(Path(os.environ["JARVIS_DATA_DIR"]).expanduser())
    if WINDOWS:
        for var in ("LOCALAPPDATA", "APPDATA"):
            if os.environ.get(var):
                racines.append(Path(os.environ[var]) / "Jarvis")
    racines.append(Path.home() / ".jarvis")
    x.racines_reelles = []
    for racine in racines:
        resolved = racine.resolve() if racine.exists() else racine.absolute()
        if resolved not in x.racines_reelles:
            x.racines_reelles.append(resolved)
    existantes = [r for r in x.racines_reelles if r.exists()]
    if WINDOWS and os.environ.get("LOCALAPPDATA"):
        inst = Path(os.environ["LOCALAPPDATA"]) / "Jarvis"
        if (inst / "app" / "Jarvis.exe").exists():
            x.installation = inst
    dev = x.depot / ".venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")
    x.dev_python = str(dev) if dev.exists() else None
    x.jarvis_ouvert = processus_jarvis()
    sources = []
    if os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip():
        sources.append("variable d'environnement")
    if cle_dans_env_fichier(x.depot / ".env"):
        sources.append("fichier .env du dépôt")
    if x.installation is not None or (Path.home() / ".jarvis").exists():
        for racine in existantes:
            if cle_dans_config(racine / "config" / "config.json"):
                sources.append("configuration de Jarvis (config.json)")
                break
    x.sources_cle = sources
    afficher(f"   Machine : {nom_os}")
    afficher(f"   Données réelles protégées : {', '.join(x.rel(r) for r in x.racines_reelles)}")
    afficher(f"   Installation quotidienne : {x.rel(x.installation) if x.installation else 'absente'}")
    afficher(f"   .venv de développement : {'présent' if x.dev_python else 'absent'}")
    afficher(f"   Jarvis en cours d'exécution : {', '.join(x.jarvis_ouvert) or 'non'}")
    afficher(f"   Clé Gemini : {', '.join(sources) if sources else 'introuvable'} (jamais affichée)")
    m["donnees_reelles"] = [x.rel(r) for r in existantes]


def processus_jarvis() -> list[str]:
    found = []
    try:
        if WINDOWS:
            out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=30,
                                 errors="replace").stdout
            for name in ("Jarvis.exe", "JarvisLauncher.exe"):
                if f'"{name.lower()}"' in out.lower():
                    found.append(name)
            ps = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                 "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
                                 "Select-Object -ExpandProperty CommandLine"],
                                capture_output=True, text=True, timeout=40, errors="replace").stdout
            if re.search(r"src\.main|run_jarvis", ps or ""):
                found.append("Jarvis (Python, développement)")
        else:
            out = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True, timeout=20).stdout
            if re.search(r"python[^\n]*(src\.main|run_jarvis\.py)(?![^\n]*smoke)", out or ""):
                found.append("Jarvis (Python)")
    except Exception:
        pass
    return found


def cle_dans_env_fichier(path: Path) -> bool:
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() in ("GEMINI_API_KEY", "GOOGLE_API_KEY") and value.strip().strip("'\""):
                return True
    except OSError:
        pass
    return False


def cle_dans_config(path: Path) -> bool:
    try:
        return bool(str(json.loads(path.read_text(encoding="utf-8")).get("api_key") or "").strip())
    except Exception:
        return False


def lire_cle(x: Execution) -> str:
    """Lit la clé (après consentement) : environnement, .env du dépôt, config.json. Jamais affichée."""
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if os.environ.get(name, "").strip():
            return os.environ[name].strip()
    try:
        for line in (x.depot / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() in ("GEMINI_API_KEY", "GOOGLE_API_KEY") and value.strip().strip("'\""):
                return value.strip().strip("'\"")
    except OSError:
        pass
    for racine in x.racines_reelles:
        try:
            value = str(json.loads((racine / "config" / "config.json").read_text(encoding="utf-8")).get("api_key") or "")
            if value.strip():
                return value.strip()
        except Exception:
            continue
    return ""


def fenetre_lancement(x: Execution) -> None:
    afficher("\n[2/11] Autorisations")
    memoire = any((r / "memory.db").exists() for r in x.racines_reelles)
    defauts = {
        "gemini_reel": bool(x.sources_cle) and x.args.gemini_reel != "non",
        "audio_guide": x.args.audio_guide != "non",
        "notification_windows": WINDOWS and x.args.notification_windows != "non",
        "migration_copie": memoire and x.args.migration_copie != "non",
    }
    if x.args.gemini_reel == "oui" and not x.sources_cle:
        defauts["gemini_reel"] = False
    choix, origine = dict(defauts), "options par défaut (exécution non interactive)"
    if x.interactif:
        try:
            choix, origine = fenetre_tk(x, defauts, memoire)
        except Annulation:
            raise
        except Exception as exc:
            afficher(f"   Fenêtre graphique indisponible ({type(exc).__name__}) : questions dans la console.")
            choix, origine = questions_console(x, defauts, memoire), "réponses dans la console"
    if not WINDOWS:
        choix["notification_windows"] = False
    x.consentements = choix
    x.rapport.meta["consentements"] = {**choix, "origine": origine}
    if choix["gemini_reel"]:
        x.cle = lire_cle(x)
        x.masque.secret(x.cle)
        if not x.cle:
            choix["gemini_reel"] = False
    for key, value in choix.items():
        afficher(f"   {key} : {'oui' if value else 'non'}")
    afficher(f"\n   ATTENTION : {AVERTISSEMENT_FENETRES}")


def fenetre_tk(x: Execution, defauts: dict, memoire: bool) -> tuple[dict, str]:
    import tkinter as tk

    resultat: dict = {"choix": None}
    root = tk.Tk()
    root.title(f"Validation de la PR #{x.args.pr} de Jarvis")
    root.attributes("-topmost", True)
    frame = tk.Frame(root, padx=18, pady=14)
    frame.pack(fill="both", expand=True)
    texte = (
        "Ce protocole teste la PR dans une copie isolée, sans toucher à votre copie de travail, à votre "
        "installation quotidienne ni à vos données Jarvis (accès bloqués et consignés).\n\n"
        f"Données Jarvis protégées : {', '.join(x.rel(r) for r in x.racines_reelles if r.exists()) or 'aucune'}\n"
        f"Installation quotidienne : {'détectée' if x.installation else 'absente'} — "
        f"Jarvis ouvert : {', '.join(x.jarvis_ouvert) or 'non'}\n"
        f"Clé Gemini : {', '.join(x.sources_cle) or 'introuvable'} (jamais affichée ni enregistrée)\n\n"
        "Durée : 30 à 60 min (la première fois, installation d'un environnement isolé d'environ 1 Go).\n"
        f"{AVERTISSEMENT_FENETRES}")
    tk.Label(frame, text=texte, justify="left", wraplength=620).pack(anchor="w")
    variables = {}
    libelles = {
        "gemini_reel": "Valider Gemini Live RÉEL avec ma clé (consomme un peu de quota API ; aucune donnée "
                       "personnelle n'est envoyée, uniquement des phrases de test)",
        "notification_windows": "Afficher une notification Windows de test pendant le contrôle du Dashboard",
        "migration_copie": "Tester la migration de ma base mémoire sur une COPIE temporaire (l'original n'est "
                           "jamais ouvert en écriture ; la copie est supprimée ensuite)",
        "audio_guide": "Proposer à la fin un test guidé du haut-parleur et du micro (avertissement avant capture ; "
                       "rien n'est conservé)",
    }
    for key in ("gemini_reel", "notification_windows", "migration_copie", "audio_guide"):
        var = tk.BooleanVar(value=defauts[key])
        variables[key] = var
        state = "normal"
        if key == "gemini_reel" and not x.sources_cle:
            state = "disabled"
        if key == "migration_copie" and not memoire:
            state = "disabled"
        if key == "notification_windows" and not WINDOWS:
            state = "disabled"
        tk.Checkbutton(frame, text=libelles[key], variable=var, state=state, wraplength=600,
                       justify="left").pack(anchor="w", pady=2)
    restant = {"s": 180}
    compte = tk.Label(frame, text="")
    compte.pack(anchor="w", pady=(8, 0))

    def valider(origine: str) -> None:
        resultat["choix"] = ({k: bool(v.get()) for k, v in variables.items()}, origine)
        root.destroy()

    def annuler() -> None:
        resultat["choix"] = "annule"
        root.destroy()

    def tic() -> None:
        restant["s"] -= 1
        compte.config(text=f"Démarrage automatique avec ces choix dans {restant['s']} s.")
        if restant["s"] <= 0:
            valider("délai de la fenêtre écoulé : choix affichés appliqués")
        else:
            root.after(1000, tic)

    buttons = tk.Frame(frame)
    buttons.pack(fill="x", pady=(10, 0))
    tk.Button(buttons, text="Lancer la validation", command=lambda: valider("fenêtre de lancement")).pack(side="right")
    tk.Button(buttons, text="Annuler", command=annuler).pack(side="right", padx=8)
    root.protocol("WM_DELETE_WINDOW", annuler)
    root.after(1000, tic)
    root.mainloop()
    if resultat["choix"] in (None, "annule"):
        raise Annulation("validation annulée dans la fenêtre de lancement")
    return resultat["choix"]


def questions_console(x: Execution, defauts: dict, memoire: bool) -> dict:
    choix = dict(defauts)
    questions = [("gemini_reel", "Valider Gemini Live RÉEL avec votre clé (quota API) ?", bool(x.sources_cle)),
                 ("notification_windows", "Afficher une notification Windows de test ?", WINDOWS),
                 ("migration_copie", "Tester la migration de votre mémoire sur une COPIE ?", memoire),
                 ("audio_guide", "Test guidé haut-parleur/micro à la fin (rien n'est conservé) ?", True)]
    for key, question, possible in questions:
        if not possible:
            choix[key] = False
            continue
        default = "O/n" if choix[key] else "o/N"
        try:
            answer = input(f"   {question} [{default}] ").strip().lower()
        except EOFError:
            answer = ""
        if answer:
            choix[key] = answer.startswith("o") or answer.startswith("y")
    return choix


def instantane(x: Execution) -> dict:
    """Taille, date et empreinte (petits fichiers) de tout ce qui vit sous les racines réelles."""
    data: dict = {}
    for racine in x.racines_reelles:
        if not racine.exists():
            continue
        count = 0
        for dirpath, _dirnames, filenames in os.walk(racine):
            for name in filenames:
                path = Path(dirpath) / name
                try:
                    st = path.stat()
                except OSError:
                    continue
                entry = [st.st_size, st.st_mtime_ns]
                parts = path.relative_to(racine).parts
                # Gros fichiers sans données personnelles (installation, sauvegardes de mise à jour,
                # modèles) : taille et date seulement ; tout le reste est haché (SHA-256).
                lourd = (parts[:1] in (("app",), ("models",), ("cache",), (".jarvis-backups",))
                         or st.st_size > 64 * 1024 * 1024)
                if not lourd:
                    try:
                        entry.append(sha256_fichier(path))
                    except OSError:
                        entry.append("illisible")
                data[str(path)] = entry
                count += 1
                if count > 60000:
                    data[f"{racine}/…"] = ["tronqué"]
                    break
    return data


def bases_memoire(x: Execution) -> dict:
    return {x.rel(r / "memory.db"): user_version_sqlite(r / "memory.db")
            for r in x.racines_reelles if (r / "memory.db").exists()}


def statut_git(x: Execution) -> dict:
    code, out = x.git("status", "--porcelain=v1", "-uall")
    entries = {}
    rapports = "docs/validation/rapports/"
    for line in out.splitlines() if code == 0 else []:
        path = line[3:].strip().strip('"')
        if path.startswith(rapports):
            continue  # rapports produits par le protocole lui-même
        full = x.depot / path
        digest = ""
        if full.is_file():
            try:
                digest = sha256_fichier(full)
            except OSError:
                digest = "illisible"
        entries[path] = [line[:2], digest]
    return {"code": code, "entrees": entries}


def etat_main(x: Execution) -> dict:
    code, local = x.git("rev-parse", "--verify", "-q", "refs/heads/main")
    code_r, remote = x.git("ls-remote", "origin", "refs/heads/main", timeout=60)
    return {"local": local if code == 0 else "absent",
            "distant": remote.split()[0] if code_r == 0 and remote else "inconnu"}


def verifier_depot(x: Execution) -> None:
    afficher("\n[3/11] Dépôt, commit et PR")
    r = x.rapport
    code, top = x.git("rev-parse", "--show-toplevel")
    if code != 0:
        x.ctl("A01", "").regler(P.BLOQUE, raison=f"pas un dépôt git ({top[:120]})")
        r.depot["depot"] = "copie sans git (archive ?)"
    else:
        _c, branche = x.git("rev-parse", "--abbrev-ref", "HEAD")
        _c, head = x.git("rev-parse", "HEAD")
        r.depot.update({"depot": x.rel(top), "branche_locale": branche, "commit_local": head})
    _c, url = x.git("remote", "get-url", "origin")
    url = re.sub(r"//[^/@]+@", "//", url or "")
    match = re.search(r"github\.com[:/]+([^/]+/[^/.\s]+)", url or "")
    if match and not x.args.depot_github_force:
        x.slug = match.group(1)
    r.depot["depot_distant"] = x.slug
    x.statut_git_avant = statut_git(x)
    x.main_avant = etat_main(x)
    r.depot["modifications_locales_avant"] = len(x.statut_git_avant["entrees"])
    r.depot["main_avant"] = x.main_avant
    code, out = x.git("ls-remote", "origin", f"refs/pull/{x.args.pr}/head", timeout=60)
    tete_ls = out.split()[0] if code == 0 and out else ""
    pr = {}
    try:
        pr = x.api_github(f"pulls/{x.args.pr}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        r.pr["api_github"] = f"indisponible ({type(exc).__name__})"
    tete_api = (pr.get("head") or {}).get("sha", "") if pr else ""
    tete = tete_ls or tete_api
    r.depot["tete_pr_distante"] = tete or "inconnue"
    if pr:
        r.pr.update({"etat": pr.get("state"), "brouillon": pr.get("draft"), "fusionnee": pr.get("merged"),
                     "fusionnable": pr.get("mergeable_state"), "branche": (pr.get("head") or {}).get("ref"),
                     "base": (pr.get("base") or {}).get("ref"), "commits": pr.get("commits"),
                     "fichiers": pr.get("changed_files"), "ajouts": pr.get("additions"),
                     "suppressions": pr.get("deletions"), "mise_a_jour": pr.get("updated_at")})
        ok = pr.get("state") == "open" and not pr.get("merged")
        x.ctl("A05", "").regler(P.REUSSI if ok else P.ECHEC,
                                f"PR {pr.get('state')}{' (brouillon)' if pr.get('draft') else ''}, fusionnée="
                                f"{pr.get('merged')}, état de fusion={pr.get('mergeable_state')}",
                                f"api.github.com pulls/{x.args.pr}")
    else:
        x.ctl("A05", "").regler(P.NON_TESTE, raison="API GitHub injoignable")
    # Récupération de la tête exacte (FETCH_HEAD seulement : aucune branche locale modifiée).
    if tete:
        x.git("fetch", "--no-tags", "origin", f"+refs/pull/{x.args.pr}/head", etape="Récupération de la tête de la PR",
              timeout=300)
    code_obj, _ = x.git("cat-file", "-e", f"{tete}^{{commit}}") if tete else (1, "")
    if tete and code_obj == 0:
        x.commit_teste = tete
    elif tete:
        x.commit_teste = tete  # archive GitHub en repli (voir creer_copie_isolee)
    else:
        x.commit_teste = r.depot.get("commit_local", "")
    r.depot["commit_teste"] = x.commit_teste or "inconnu"
    if tete and tete_ls and tete_api and tete_ls != tete_api:
        x.ctl("A01", "").regler(P.ECHEC, f"désaccord git ({tete_ls[:12]}) / API ({tete_api[:12]})")
    elif tete:
        x.ctl("A01", "").regler(P.REUSSI, f"tête de la PR {tete[:12]} testée exactement",
                                f"git ls-remote refs/pull/{x.args.pr}/head" + (" + API" if tete_api else ""))
    elif x.ctl("A01", "").statut != P.BLOQUE:
        x.ctl("A01", "").regler(P.NON_TESTE, raison="dépôt distant injoignable : commit local testé à défaut")
    local = r.depot.get("commit_local", "")
    if local:
        if tete and local != tete:
            _c, behind = x.git("rev-list", "--count", f"{local}..{tete}")
            _c, ahead = x.git("rev-list", "--count", f"{tete}..{local}")
            x.ctl("A02", "").regler(P.REUSSI, f"branche {r.depot.get('branche_locale')} à {local[:12]} : "
                                    f"{behind or '?'} commit(s) de retard, {ahead or '?'} d'avance sur la PR "
                                    "(la copie de travail n'est PAS modifiée ; la PR est testée à part)")
        else:
            x.ctl("A02", "").regler(P.REUSSI, f"branche {r.depot.get('branche_locale')} alignée sur la PR ({local[:12]})")
    # CI et commentaires.
    if x.commit_teste and pr:
        verifier_ci(x)
        try:
            comments = x.api_github(f"issues/{x.args.pr}/comments?since={DATE_AUDIT}&per_page=100")
            nouveaux = [c for c in comments if str(c.get("created_at", "")) > DATE_AUDIT]
            reviews = [v for v in x.api_github(f"pulls/{x.args.pr}/reviews?per_page=100")
                       if str(v.get("submitted_at") or "") > DATE_AUDIT]
            resume = [f"{c.get('user', {}).get('login')} le {c.get('created_at')} : "
                      f"{(c.get('body') or '').strip().splitlines()[0][:120] if (c.get('body') or '').strip() else ''}"
                      for c in nouveaux + reviews]
            x.ctl("A07", "").regler(P.REUSSI, f"{len(nouveaux)} commentaire(s) et {len(reviews)} revue(s) depuis "
                                    f"l'audit ({DATE_AUDIT})", " | ".join(resume)[:900])
        except (urllib.error.URLError, OSError, ValueError, AttributeError) as exc:
            x.ctl("A07", "").regler(P.NON_TESTE, raison=f"API GitHub injoignable ({type(exc).__name__})")
    elif not pr:
        x.ctl("A06", "").regler(P.NON_TESTE, raison="API GitHub injoignable")
        x.ctl("A07", "").regler(P.NON_TESTE, raison="API GitHub injoignable")


def verifier_ci(x: Execution, moment: str = "au début") -> None:
    """État des vérifications GitHub du commit testé (relu en fin d'exécution si encore en cours)."""
    try:
        runs = x.api_github(f"commits/{x.commit_teste}/check-runs?per_page=100").get("check_runs", [])
    except (urllib.error.URLError, OSError, ValueError, AttributeError) as exc:
        x.ctl("A06", "").regler(P.NON_TESTE, raison=f"API GitHub injoignable ({type(exc).__name__})")
        return
    lignes = [f"{c.get('name')}={c.get('conclusion') or c.get('status')}" for c in runs]
    x.rapport.pr["ci"] = lignes
    x.rapport.pr["ci_relue"] = moment
    pending = [c for c in runs if c.get("status") != "completed"]
    bad = [c for c in runs if c.get("status") == "completed"
           and c.get("conclusion") not in ("success", "skipped", "neutral")]
    if not runs:
        x.ctl("A06", "").regler(P.NON_TESTE, raison=f"aucune exécution de CI pour ce commit ({moment})")
    elif bad:
        x.ctl("A06", "").regler(P.ECHEC, "échecs CI : " + ", ".join(c.get("name", "?") for c in bad), "; ".join(lignes))
    elif pending:
        x.ctl("A06", "").regler(P.NON_TESTE, raison=f"CI en cours ({moment}) : "
                                + ", ".join(c.get("name", "?") for c in pending))
    else:
        x.ctl("A06", "").regler(P.REUSSI, f"{len(runs)} vérifications terminées sans échec, relues {moment} "
                                "(NB : « Validation réelle » est verte même quand son verdict est NON TESTABLE)",
                                "; ".join(lignes))


def creer_copie_isolee(x: Execution) -> None:
    afficher("\n[4/11] Copie isolée de la PR")
    cible = x.run / "depot"
    if x.commit_teste:
        code, _out = x.git("worktree", "add", "--detach", str(cible), x.commit_teste, etape="Copie isolée (git worktree)",
                           timeout=300)
        if code == 0:
            x.worktree = cible
    if x.worktree is None and x.commit_teste:
        try:
            url = f"https://codeload.github.com/{x.slug}/tar.gz/{x.commit_teste}"
            archive = x.run / "pr.tar.gz"
            urllib.request.urlretrieve(url, archive)
            with tarfile.open(archive) as handle:
                handle.extractall(x.run / "archive")
            racine = next((x.run / "archive").iterdir())
            racine.rename(cible)
            archive.unlink(missing_ok=True)
            x.worktree = cible
            x.rapport.depot["copie_isolee"] = "archive GitHub du commit (git worktree impossible)"
        except Exception as exc:
            x.probleme("bloquant", "Copie isolée de la PR impossible", f"{type(exc).__name__}: {exc}")
    if x.worktree is not None:
        x.rapport.depot.setdefault("copie_isolee", "git worktree détaché dans le dossier de validation")
        x.masque.chemin(x.worktree, "<copie PR>")
        changements_depuis_audit(x)
    else:
        x.rapport.depot["copie_isolee"] = "impossible"


def changements_depuis_audit(x: Execution) -> None:
    audit = x.args.commit_audit
    code, _ = x.git("cat-file", "-e", f"{audit}^{{commit}}")
    if code != 0:
        x.ctl("A08", "").regler(P.NON_TESTE, raison=f"commit d'audit {audit[:12]} absent de l'historique local")
        return
    if x.commit_teste == audit:
        x.ctl("A08", "").regler(P.REUSSI, "aucun changement depuis l'audit (même commit)")
        return
    code, log = x.git("log", "--oneline", "--no-decorate", f"{audit}..{x.commit_teste}")
    code2, files = x.git("diff", "--name-only", f"{audit}..{x.commit_teste}")
    fichiers = [f for f in files.splitlines() if f.strip()] if code2 == 0 else []
    produit = [f for f in fichiers if not f.startswith(OUTILLAGE)]
    x.rapport.depot["commits_depuis_audit"] = log.splitlines()[:30] if code == 0 else "?"
    texte = (f"{len(log.splitlines()) if code == 0 else '?'} commit(s), {len(fichiers)} fichier(s) dont "
             f"{len(produit)} hors outillage de validation")
    preuve = ", ".join(produit[:25]) if produit else "uniquement l'outillage de validation (scripts/validation, docs/validation…)"
    x.ctl("A08", "").regler(P.REUSSI, texte, preuve)
    if produit:
        x.probleme("info", "Code produit modifié depuis l'audit : à relire", ", ".join(produit[:25]), controle="A08")


def environnements(x: Execution) -> None:
    afficher("\n[5/11] Environnements Python et PySide6")
    if x.worktree is None:
        for cid in ("B02", "B03", "B06", "B07", "B09"):
            x.ctl(cid, "").regler(P.BLOQUE, raison="copie isolée de la PR indisponible")
        inspecter_dev(x)
        inspecter_installation(x)
        return
    x.python_base = trouver_python311(x)
    creer_venv_validation(x)
    inspecter_dev(x)
    inspecter_installation(x)
    inspecter_scripts(x)
    garde_pyside(x)
    mesure_pyside_612(x)
    if x.venv_python:
        x.python_tests = x.venv_python
    elif x.dev_python and dev_a_pytest(x):
        x.python_tests = x.dev_python
        x.rapport.ecarts.append("La suite a été exécutée avec le .venv de développement (lecture seule) car le venv "
                                "de validation n'a pas pu être créé.")


def trouver_python311(x: Execution) -> str:
    if sys.version_info[:2] == (3, 11):
        return getattr(sys, "_base_executable", sys.executable) or sys.executable
    if WINDOWS:
        try:
            out = subprocess.run(["py", "-3.11", "-c", "import sys; print(sys.executable)"], capture_output=True,
                                 text=True, timeout=30).stdout.strip()
            if out and Path(out).exists():
                return out
        except Exception:
            pass
    for name in ("python3.11",):
        found = shutil.which(name)
        if found:
            return found
    x.rapport.ecarts.append(f"Python 3.11 introuvable : environnement de validation créé avec Python "
                            f"{sys.version.split()[0]} (la CI et l'exécutable distribué utilisent 3.11).")
    return sys.executable


def creer_venv_validation(x: Execution) -> None:
    wt = x.worktree
    reqs = [wt / "requirements.txt", wt / "requirements-dev.txt"]
    try:
        version = subprocess.run([x.python_base, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
                                 capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        version = "?"
    digest = hashlib.sha256((version + platform.platform() + "".join(
        p.read_text(encoding="utf-8", errors="replace") for p in reqs if p.exists())).encode()).hexdigest()[:12]
    venv = x.base / f"venv-py{version.replace('.', '')[:3]}-{digest}"
    marker = venv / ".jarvis-validation-ok"
    python = x.python_venv(venv)
    if marker.exists() and python.exists():
        afficher(f"   Réutilisation de l'environnement de validation ({x.rel(venv)})")
    else:
        if venv.exists():
            shutil.rmtree(venv, ignore_errors=True)
        env = x.env_enfant("pip", garde=False, qt=None, isoler=False)
        code, _ = x.executer("Création du venv de validation", [x.python_base, "-m", "venv", str(venv)], env=env,
                             timeout=600)
        if code == 0:
            code, _ = x.executer("Installation des dépendances de la PR", [
                str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                "-r", str(reqs[0]), "-r", str(reqs[1])], env=env, timeout=3600, cwd=wt)
        if code == 0:
            marker.write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
            (venv / ".jarvis-validation").write_text("venv créé par le protocole de validation", encoding="utf-8")
        else:
            x.ctl("B02", "").regler(P.BLOQUE, raison="création ou installation impossible (réseau ? voir journal)")
            x.ctl("B03", "").regler(P.BLOQUE, raison="environnement de validation indisponible")
            return
    if not x.args.garder_venvs:
        for old in x.base.glob("venv-py*"):
            if old != venv and (old / ".jarvis-validation").exists():
                shutil.rmtree(old, ignore_errors=True)
    x.venv_python = str(python)
    info = sonde(x, str(python), "venv de validation")
    if info:
        x.rapport.versions["Environnement de validation (PR)"] = resume_sonde(info)
        x.ctl("B02", "").regler(P.REUSSI, f"Python {info.get('python')}, PySide6 {info['paquets'].get('PySide6')}, "
                                f"google-genai {info['paquets'].get('google-genai')}", x.rel(venv))
    code, out = x.executer("pip check (venv de validation)", [str(python), "-m", "pip", "check"],
                           env=x.env_enfant("pip", garde=False, qt=None, isoler=False), timeout=300)
    pyside = (info or {}).get("paquets", {}).get("PySide6") or ""
    ok_pyside = bool(pyside) and version_tuple(pyside) < (6, 12)
    if code == 0 and ok_pyside:
        x.ctl("B03", "").regler(P.REUSSI, f"pip check sans conflit ; PySide6 {pyside} < 6.12 comme exigé par requirements.txt")
    else:
        x.ctl("B03", "").regler(P.ECHEC, f"pip check code {code}, PySide6 {pyside or 'absent'}", out.strip()[-300:])


def sonde(x: Execution, python: str, nom: str, *, garde: bool = True) -> dict | None:
    target = x.run / f"sonde_{slug(nom)}.json"
    code, _ = x.executer(f"Sonde {nom}", [python, str(ICI / "sonde_env.py"), "--json", str(target)],
                         env=x.env_enfant(f"sonde {nom}", garde=garde), timeout=300, cwd=x.run)
    if code == 0 and target.exists():
        return json.loads(target.read_text(encoding="utf-8"))
    return None


def resume_sonde(info: dict) -> dict:
    paquets = {k: v for k, v in (info.get("paquets") or {}).items() if v}
    return {"python": f"{info.get('python')} ({info.get('bits')} bits)", "executable": info.get("executable"),
            "PySide6 (importé)": (info.get("imports") or {}).get("PySide6.__version__")
            or (info.get("imports") or {}).get("PySide6"),
            "Qt": (info.get("imports") or {}).get("Qt (qVersion)"),
            "PortAudio": (info.get("imports") or {}).get("PortAudio") or (info.get("imports") or {}).get("sounddevice"),
            "paquets": paquets}


def dev_a_pytest(x: Execution) -> bool:
    info = x.rapport.versions.get("Développement (.venv du dépôt)") or {}
    return bool((info.get("paquets") or {}).get("pytest"))


def inspecter_dev(x: Execution) -> None:
    if not x.dev_python:
        x.ctl("B04", "").regler(P.NON_APPLICABLE, raison="aucun .venv de développement dans le dépôt")
        return
    info = sonde(x, x.dev_python, "venv de développement")
    if not info:
        x.ctl("B04", "").regler(P.ECHEC, "la sonde n'a pas pu s'exécuter dans le .venv (voir journal)")
        return
    x.rapport.versions["Développement (.venv du dépôt)"] = resume_sonde(info)
    pyside = info["paquets"].get("PySide6") or info["paquets"].get("PySide6-Essentials")
    fuite = mesure_fuite(x, x.dev_python, "venv de développement", appels=20000)
    texte = f"Python {info.get('python')}, PySide6 {pyside or 'absent'} ; fuite None : {fuite['resume']}"
    if pyside and version_tuple(pyside) >= (6, 12):
        x.ctl("B04", "").regler(P.ECHEC, texte, raison="PySide6 exclu par la PR dans votre .venv (non modifié)")
        x.probleme("important", f"Votre .venv de développement contient PySide6 {pyside}, exclu par la PR",
                   "`pip install -r requirements.txt` dans ce .venv le ramènera sous 6.12 (le protocole ne le modifie "
                   "pas).", ".venv / requirements.txt", "B04")
    else:
        x.ctl("B04", "").regler(P.REUSSI, texte, x.rel(x.dev_python))
    if not info["paquets"].get("pytest"):
        x.rapport.ecarts.append("Le .venv de développement ne contient pas les outils de test (requirements-dev.txt "
                                "non installé) : normal avec setup.bat, qui n'installe que requirements.txt.")


def mesure_fuite(x: Execution, python: str, nom: str, appels: int) -> dict:
    target = x.run / f"fuite_{slug(nom)}.json"
    code, out = x.executer(f"Mesure fuite PySide6 ({nom})", [python, str(ICI / "sonde_env.py"), "--fuite-pyside",
                                                             "--appels", str(appels), "--json", str(target)],
                           env=x.env_enfant(f"fuite {nom}"), timeout=600, cwd=x.run)
    data = json.loads(target.read_text(encoding="utf-8")) if code == 0 and target.exists() else {}
    if data:
        resume = f"dérive {data.get('derive_none')} sur {appels} appels" + (" — FUITE" if data.get("fuite") else " — aucune fuite")
    elif "none_dealloc" in out or code not in (0, None):
        resume = f"PLANTAGE de l'interpréteur (code {code}{', none_dealloc' if 'none_dealloc' in out else ''})"
    else:
        resume = f"mesure impossible (code {code})"
    return {"code": code, "data": data, "resume": resume, "plantage": not data and code not in (0, None)}


def version_fichier_windows(path: Path) -> str:
    if not WINDOWS or not path.exists():
        return ""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                              f"(Get-Item -LiteralPath '{path}').VersionInfo.ProductVersion"],
                             capture_output=True, text=True, timeout=40, errors="replace").stdout.strip()
        return out
    except Exception:
        return ""


def inspecter_installation(x: Execution) -> None:
    inst = x.installation
    if inst is None:
        x.ctl("B05", "").regler(P.NON_APPLICABLE, raison="aucune installation quotidienne détectée sur ce PC")
        x.ctl("B09", "").regler(P.NON_APPLICABLE, raison="aucune installation quotidienne détectée sur ce PC")
        x.ctl("C16", "").regler(P.NON_APPLICABLE, raison="aucune installation quotidienne détectée sur ce PC")
        return
    info: dict = {}
    for candidate in (inst / "version.json", inst / "app" / "version.json"):
        if candidate.exists():
            try:
                info["version.json"] = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                info["version.json"] = "illisible"
            break
    internal = inst / "app" / "_internal"
    pythons = sorted(p.name for p in internal.glob("python3*.dll")) if internal.exists() else []
    info["python_embarque"] = ", ".join(pythons) or "inconnu"
    qtcore = next(iter(internal.glob("PySide6/Qt6Core.dll")), None) if internal.exists() else None
    info["Qt6Core.dll"] = version_fichier_windows(qtcore) if qtcore else "absent"
    for dll in ("pyside6.abi3.dll", "shiboken6.abi3.dll"):
        found = next(iter(internal.rglob(dll)), None) if internal.exists() else None
        info[dll] = version_fichier_windows(found) if found else "absent"
    dist = sorted(p.name for p in internal.glob("PySide6*.dist-info")) if internal.exists() else []
    info["metadonnees_pyside"] = ", ".join(dist) or "non embarquées"
    x.rapport.versions["Installation quotidienne"] = info
    qt = info.get("Qt6Core.dll") or ""
    x.installation_qt = qt
    texte = f"version {info.get('version.json', {}).get('version', '?') if isinstance(info.get('version.json'), dict) else '?'}, " \
            f"{info['python_embarque']}, Qt6Core {qt or '?'}"
    if qt.startswith(PYSIDE_EXCLUE):
        x.ctl("B05", "").regler(P.ECHEC, texte, raison="l'installation embarque Qt/PySide6 6.12.0 (version exclue)")
        x.probleme("important", "L'installation quotidienne embarque PySide6/Qt 6.12.0",
                   "Reconstruire après fusion (build_windows.ps1 installe requirements.txt, plafonné < 6.12).",
                   "scripts/build_windows.ps1", "B05")
    else:
        x.ctl("B05", "").regler(P.REUSSI, texte, x.rel(inst))


def inspecter_scripts(x: Execution) -> None:
    wt = x.worktree
    constats, ko = [], []

    def lire(rel: str) -> str:
        try:
            return (wt / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            ko.append(f"{rel} absent")
            return ""

    req = lire("requirements.txt")
    if re.search(r"(?im)^\s*PySide6\s*[<>=!~].*<\s*6\.12", req) or re.search(r"(?im)^\s*PySide6\s*<\s*6\.12", req):
        constats.append("requirements.txt plafonne PySide6 < 6.12")
    else:
        ko.append("requirements.txt ne plafonne pas PySide6 < 6.12")
    build = lire("scripts/build_windows.ps1")
    (constats if "requirements.txt" in build else ko).append("build_windows.ps1 installe requirements.txt")
    setup = lire("setup.bat")
    (constats if "requirements.txt" in setup else ko).append("setup.bat installe requirements.txt")
    lanceur = lire("Jarvis.bat")
    (constats if ".venv" in lanceur else ko).append("Jarvis.bat lance le .venv du dépôt")
    iss = lire("packaging/installer.iss")
    (constats if "{localappdata}\\Jarvis" in iss.replace("/", "\\") else ko).append(
        "installateur : %LOCALAPPDATA%\\Jarvis, sans droits administrateur")
    updater = lire("src/updater.py")
    (constats if ".jarvis-backups" in updater and "install_dir" in updater else ko).append(
        "mise à jour : remplace app/ avec sauvegarde (.jarvis-backups), données conservées")
    statut = P.REUSSI if not ko else P.ECHEC
    x.ctl("B08", "").regler(statut, "; ".join(constats) + (" — écarts : " + "; ".join(ko) if ko else ""),
                            "inspection statique de la copie de la PR")


def garde_pyside(x: Execution) -> None:
    if not x.venv_python:
        x.ctl("B06", "").regler(P.BLOQUE, raison="environnement de validation indisponible")
        return
    code, out = x.executer("Garde PySide6 (environnement de la PR)", [
        x.venv_python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--noconftest", "tests/test_pyside_refcount.py"],
        cwd=x.worktree, env=x.env_enfant("garde pyside"), timeout=600)
    last = out.strip().splitlines()[-1] if out.strip() else ""
    x.ctl("B06", "").regler(P.REUSSI if code == 0 else P.ECHEC, last, "tests/test_pyside_refcount.py")


def mesure_pyside_612(x: Execution) -> None:
    if x.args.sans_pyside612 or "pyside612" in x.args.sauter:
        x.ctl("B07", "").regler(P.NON_TESTE, raison="mesure sautée à la demande (--sans-pyside612)")
        return
    try:
        version = subprocess.run([x.python_base, "-c", "import sys; print(sys.version_info[:2] >= (3, 12))"],
                                 capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        version = "?"
    if version == "True":
        x.ctl("B07", "").regler(P.NON_APPLICABLE, raison="Python ≥ 3.12 : None est immortel, le défaut ne peut pas planter")
        return
    venv = x.base / "venv-pyside6120"
    python = x.python_venv(venv)
    marker = venv / ".jarvis-validation-ok"
    env = x.env_enfant("pip pyside612", garde=False, qt=None, isoler=False)
    if not marker.exists():
        shutil.rmtree(venv, ignore_errors=True)
        code, _ = x.executer("Venv jetable PySide6 6.12.0", [x.python_base, "-m", "venv", str(venv)], env=env, timeout=600)
        if code == 0:
            code, _ = x.executer("Installation PySide6-Essentials 6.12.0 (venv jetable)", [
                str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                f"PySide6-Essentials=={PYSIDE_EXCLUE}", f"shiboken6=={PYSIDE_EXCLUE}", "pytest"], env=env, timeout=1800)
        if code != 0:
            x.ctl("B07", "").regler(P.BLOQUE, raison="installation de PySide6 6.12.0 impossible (réseau ? voir journal)")
            return
        marker.write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
        (venv / ".jarvis-validation").write_text("venv jetable du protocole", encoding="utf-8")
    code, out = x.executer("Garde PySide6 avec 6.12.0 (venv jetable)", [
        str(python), "-m", "pytest", "-q", "-p", "no:cacheprovider", "--noconftest", "tests/test_pyside_refcount.py"],
        cwd=x.worktree, env=x.env_enfant("garde pyside612"), timeout=600)
    fuite = mesure_fuite(x, str(python), "PySide6 6.12.0", appels=20000)
    reproduit = code != 0 or fuite["plantage"] or bool(fuite["data"].get("fuite"))
    texte = (f"PySide6 6.12.0 sur ce PC : test de garde code {code} ; mesure étendue : {fuite['resume']} — "
             + ("défaut REPRODUIT : le plafond < 6.12 de la PR est nécessaire ici" if reproduit else
                "défaut NON reproduit ici : plafond prudent, non démontré nécessaire sur ce PC"))
    x.ctl("B07", "").regler(P.REUSSI, texte, "venv jetable (n'affecte ni le .venv ni l'installation)")
    if not WINDOWS:
        x.ctl("B07", "").regler(P.NON_TESTE, texte, raison="mesure faite hors de Windows : la question posée par "
                                "l'audit (comportement sous Windows) reste ouverte")
    x.pyside612_reproduit = reproduit


def tests_automatises(x: Execution) -> None:
    afficher("\n[6/11] Tests automatisés")
    if not x.python_tests or x.worktree is None:
        for cid in ["C01", "C09", "C10", "C11", "C12", "C13", "C14", "C15"] + [f"C{i:02d}" for i in range(2, 9)]:
            x.ctl(cid, "").regler(P.BLOQUE, raison="environnement de validation indisponible")
        return
    py, wt = x.python_tests, x.worktree
    if "suite" in x.args.sauter:
        for cid in ["C01"] + [f"C{i:02d}" for i in range(2, 9)]:
            x.ctl(cid, "").regler(P.NON_TESTE, raison="étape sautée à la demande (--sauter suite)")
        etapes_ci(x)
        protocole_dashboard(x)
        lint_et_compilation(x)
        fumee(x)
        return
    junit = x.run / "junit_suite.xml"
    progression = x.run / "progression_suite.jsonl"
    env = x.env_enfant("suite", qt=None if x.args.suite_native else "offscreen")
    env["JARVIS_VALIDATION_PROGRESSION"] = str(progression)
    code_c, out_c = x.executer("Collecte pytest", [py, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
                               cwd=wt, env=x.env_enfant("collecte"), timeout=900)
    match = re.search(r"(\d+) tests? collected", out_c)
    collectes = int(match.group(1)) if match else None
    code, out = x.executer("Suite complète pytest", [py, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p",
                                                     "garde_pytest", "-rfE", f"--junitxml={junit}"],
                           cwd=wt, env=env, timeout=x.args.delai_suite, progression=progression)
    x.junit = P.lire_junit(junit)
    prog = P.lire_progression(progression)
    compte = P.compter_tests(collectes, x.junit, prog)
    x.rapport.tests["suite"] = compte
    x.rapport.tests["echecs_detail"] = (x.junit or {}).get("echecs", [])
    resume = re.findall(r"=+ (.+ in [\d.]+s.*) =+", out)
    texte = (f"{compte['executes']} exécutés, {compte['reussis']} réussis, {compte['echoues']} en échec, "
             f"{compte['ignores']} ignorés, {compte['bloques']} bloqués ; sous-tests : "
             f"{compte['sous_tests']['reussis']} réussis / {compte['sous_tests']['echoues']} en échec")
    if compte.get("en_cours_au_plantage") and code not in (0, 1):
        texte += f" — arrêt brutal pendant {compte['en_cours_au_plantage']}"
    ok = code == 0 and compte["echoues"] == 0 and compte["bloques"] == 0 and compte["executes"] > 0
    x.ctl("C01", "").regler(P.REUSSI if ok else P.ECHEC, texte, (resume[-1] if resume else f"code {code}") +
                            " — rendu " + ("natif" if x.args.suite_native else "hors écran"))
    if not ok:
        for e in (x.junit or {}).get("echecs", [])[:10]:
            x.probleme("bloquant", f"Test en échec : {e['test']}", e.get("message", ""), e["test"].split("::")[0], "C01")
        if compte.get("bloques"):
            x.probleme("bloquant", f"{compte['bloques']} test(s) jamais exécutés (arrêt de la suite)",
                       compte.get("en_cours_au_plantage", ""), controle="C01")
    vues = []
    cases = (x.junit or {}).get("cas", [])
    for index, (nom, filtre) in enumerate(VUES, start=2):
        selection = [c for c in cases if filtre(c["fichier"], c["nom"])]
        executes = [c for c in selection if c["issue"] != "skipped"]
        echecs = [c for c in executes if c["issue"] in ("failure", "error")]
        vues.append({"nom": nom, "executes": len(executes), "reussis": len(executes) - len(echecs),
                     "echoues": len(echecs), "ignores": len(selection) - len(executes),
                     "fichiers": len({c["fichier"] for c in selection})})
        cid = f"C{index:02d}"
        if not cases:
            x.ctl(cid, "").regler(P.NON_TESTE, raison="résultats JUnit indisponibles")
        elif not executes:
            x.ctl(cid, "").regler(P.NON_TESTE, raison="aucun test exécuté dans cette vue")
        else:
            x.ctl(cid, "").regler(P.ECHEC if echecs else P.REUSSI,
                                  f"{len(executes)} tests ({len(echecs)} en échec) — vue extraite de C01, non relancée")
    x.rapport.tests["vues"] = vues
    etapes_ci(x)
    protocole_dashboard(x)
    lint_et_compilation(x)
    fumee(x)


def etapes_ci(x: Execution) -> None:
    if "diag" in x.args.sauter:
        x.ctl("C09", "").regler(P.NON_TESTE, raison="étape sautée à la demande (--sauter diag)")
        return
    code, out = x.executer("Étapes diagnostiques CI (unittest)", [x.python_tests, "-m", "unittest", *ETAPES_DIAG_CI],
                           cwd=x.worktree, env=x.env_enfant("diag ci"), timeout=1800)
    ran = re.search(r"Ran (\d+) tests?", out)
    fails = re.search(r"FAILED \((.+)\)", out)
    n = int(ran.group(1)) if ran else 0

    def nombre(label: str) -> int:
        found = re.search(rf"{label}=(\d+)", out.split("Ran ")[-1])
        return int(found.group(1)) if found else 0

    ignores, echecs = nombre("skipped"), nombre("failures") + nombre("errors")
    executes = n - ignores
    x.rapport.tests["etapes_diag_ci"] = {"source": "unittest (sortie texte)", "collectes": n, "executes": executes,
                                         "reussis": executes - echecs, "echoues": echecs, "ignores": ignores,
                                         "bloques": 0 if ran else None, "sous_tests": {"reussis": 0, "echoues": 0}}
    x.ctl("C09", "").regler(P.REUSSI if code == 0 and n > 0 else P.ECHEC,
                            f"{n} tests unittest ({len(ETAPES_DIAG_CI)} modules/classes de build.yml) — mêmes tests que C01, "
                            "comptés à part", (fails.group(0) if fails else "OK"))


def protocole_dashboard(x: Execution) -> None:
    if "protocole" in x.args.sauter:
        x.ctl("C10", "").regler(P.NON_TESTE, raison="étape sautée à la demande (--sauter protocole)")
        return
    out_dir = x.run / "protocole_dashboard"
    code, out = x.executer("Protocole officiel du Dashboard", [x.python_tests, "scripts/run_dashboard_protocol.py",
                                                                "--out", str(out_dir)],
                           cwd=x.worktree, env=x.env_enfant("protocole dashboard"), timeout=2400)
    summary = out_dir / "summary.json"
    detail = ""
    etapes_ok: bool | None = None
    if summary.exists():
        try:
            data = json.loads(summary.read_text(encoding="utf-8"))
            steps = data.get("steps") or data.get("etapes") or []
            if isinstance(steps, dict):
                steps = [dict(v, nom=k) for k, v in steps.items()]
            parts, verdicts = [], []
            for step in steps:
                name = step.get("name") or step.get("nom") or step.get("step") or "?"
                ok = step.get("ok", step.get("success", step.get("returncode", 1) == 0))
                verdicts.append(bool(ok))
                tail = str(step.get("last_line") or "")[:70]
                parts.append(f"{name}={'OK' if ok else 'ÉCHEC'}" + (f" ({tail})" if tail else ""))
            detail = ", ".join(parts)
            etapes_ok = bool(verdicts) and all(verdicts)
        except Exception:
            detail = "summary.json illisible"
    statut = P.REUSSI if code == 0 and etapes_ok is not False else P.ECHEC
    if statut == P.ECHEC and code != 0 and etapes_ok:
        # Incohérence (validation Windows PR #50 : « ÉCHEC » global, sous-étapes
        # réussies) : le verdict reste ÉCHEC — rien n'est masqué — mais la
        # cause visible est donnée : code observé et fin de la sortie du script.
        fin = [ligne.strip() for ligne in x.masque(out or "").splitlines() if ligne.strip()][-2:]
        detail = (f"code de sortie {'absent (délai dépassé)' if code is None else code} alors que toutes les "
                  f"étapes du résumé ont réussi ({detail}) ; fin de sortie : {' | '.join(fin)[:240]}")
    x.ctl("C10", "").regler(statut, detail or f"code {code}", x.rel(summary) + " — rendu hors écran (officiel)")


def lint_et_compilation(x: Execution) -> None:
    py, wt = x.python_tests, x.worktree
    if "lint" in x.args.sauter:
        for cid in ("C11", "C12", "C13", "C14"):
            x.ctl(cid, "").regler(P.NON_TESTE, raison="étape sautée à la demande (--sauter lint)")
        return
    env = x.env_enfant("lint", garde=False)
    for cid, cibles in (("C11", ["src", "launcher", "run_jarvis.py", "version.py"]), ("C12", ["UI", "tests"]),
                        ("C13", ["scripts"])):
        code, out = x.executer(f"ruff {' '.join(cibles)}", [py, "-m", "ruff", "check", *cibles], cwd=wt, env=env,
                               timeout=600)
        last = out.strip().splitlines()[-1] if out.strip() else ""
        if code is None or "No module named ruff" in out:
            x.ctl(cid, "").regler(P.BLOQUE, raison="ruff indisponible")
        else:
            x.ctl(cid, "").regler(P.REUSSI if code == 0 else P.ECHEC, last)
    code, out = x.executer("compileall", [py, "-m", "compileall", "-q", "src", "UI", "launcher", "scripts",
                                          "run_jarvis.py", "version.py"], cwd=wt, env=env, timeout=900)
    x.ctl("C14", "").regler(P.REUSSI if code == 0 else P.ECHEC, "aucune erreur de syntaxe" if code == 0 else out[-300:])


def fumee(x: Execution) -> None:
    code, out = x.executer("Test de fumée (code de la PR)", [x.python_tests, "run_jarvis.py", "--smoke-test"],
                           cwd=x.worktree, env=x.env_enfant("fumee pr"), timeout=300)
    last = [line for line in out.strip().splitlines() if line.strip()][-1:] or [""]
    x.ctl("C15", "").regler(P.REUSSI if code == 0 else P.ECHEC, last[0][:200])
    if x.installation is not None:
        exe = x.installation / "app" / "Jarvis.exe"
        code, out = x.executer("Test de fumée (exécutable installé, données redirigées)", [str(exe), "--smoke-test"],
                               cwd=x.run, env=x.env_enfant("fumee installee", garde=False), timeout=120)
        last = [line for line in out.strip().splitlines() if line.strip()][-1:] or [""]
        x.ctl("C16", "").regler(P.REUSSI if code == 0 else P.ECHEC, f"code {code} — {last[0][:160]}",
                                "JARVIS_DATA_DIR/LOCALAPPDATA/APPDATA temporaires")


def dashboard_reel(x: Execution) -> None:
    afficher("\n[7/11] Dashboard réel")
    ids = [f"D{i:02d}" for i in range(1, 18)]
    if "dashboard" in x.args.sauter:
        for cid in ids:
            x.ctl(cid, "").regler(P.NON_TESTE, raison="étape sautée à la demande (--sauter dashboard)")
        return
    if not x.python_tests or x.worktree is None:
        for cid in ids:
            x.ctl(cid, "").regler(P.BLOQUE, raison="environnement de validation indisponible")
        return
    out_dir = x.run / "dashboard_reel"
    cmd = [x.python_tests, str(ICI / "controle_dashboard_reel.py"), "--out", str(out_dir)]
    if WINDOWS:
        cmd.append("--natif")
    if x.consentements.get("notification_windows"):
        cmd.append("--notification-native")
    code, _out = x.executer("Contrôle du Dashboard réel", cmd, cwd=x.worktree,
                            env=x.env_enfant("dashboard reel", qt=None if WINDOWS else "offscreen"), timeout=900)
    result_file = out_dir / "dashboard_reel.json"
    if not result_file.exists():
        for cid in ids:
            x.ctl(cid, "").regler(P.ECHEC if WINDOWS else P.NON_TESTE,
                                  raison=f"le contrôle n'a produit aucun résultat (code {code}, voir journal)")
        return
    data = json.loads(result_file.read_text(encoding="utf-8"))
    x.dashboard_captures = [str(out_dir / name) for name in data.get("captures", [])]
    x.notification_emise = bool(data.get("notifications_windows_emises"))
    natif = bool(data.get("natif"))
    for item in data.get("controles", []):
        ctl = x.ctl(item["id"], item.get("titre", ""))
        if natif:
            ctl.regler(item["statut"], item.get("resultat", ""), item.get("preuve", ""))
        else:
            ctl.regler(P.NON_TESTE, raison=(f"pas de Windows natif (plateforme Qt « {data.get('plateforme_qt')} ») : "
                                            f"répétition hors écran = {item['statut']} — {item.get('resultat', '')}"[:400]))
    if natif:
        x.rapport.windows.append(f"Dashboard réel en rendu Windows natif (plateforme Qt « {data.get('plateforme_qt')} ») : "
                                 f"{sum(1 for i in data['controles'] if i['statut'] == P.REUSSI)}/{len(data['controles'])} "
                                 f"contrôles réussis, {len(x.dashboard_captures)} captures natives.")
        if x.notification_emise:
            x.rapport.windows.append("Notification Windows réelle émise via la zone de notification (bulle).")
    if data.get("erreur"):
        x.probleme("bloquant" if natif else "info", "Contrôle du Dashboard réel interrompu", data["erreur"], controle="D01")
    for item in data.get("controles", []):
        if natif and item["statut"] == P.ECHEC:
            x.probleme("bloquant" if item.get("requis", True) else "mineur", f"{item['id']} : {item['titre']}",
                       item.get("resultat", ""), controle=item["id"])


def gemini_et_audio(x: Execution) -> None:
    afficher("\n[8/11] Gemini Live réel et audio")
    if x.python_tests and x.worktree is not None:
        target = x.run / "audio.json"
        code, _ = x.executer("Inventaire audio (aucune capture)", [x.python_tests, str(ICI / "sonde_audio.py"), "--json",
                                                                   str(target)],
                             cwd=x.run, env=x.env_enfant("audio"), timeout=120)
        x.audio = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {"erreur": f"code {code}"}
        if x.audio.get("erreur"):
            x.ctl("E13", "").regler(P.NON_TESTE if not WINDOWS else P.ECHEC,
                                    raison=f"inventaire impossible : {x.audio['erreur'][:200]}")
        else:
            ok = x.audio.get("entree_utilisable") and x.audio.get("sortie_utilisable")
            x.ctl("E13", "").regler(P.REUSSI if ok else P.ECHEC,
                                    f"{x.audio.get('entrees')} entrée(s), {x.audio.get('sorties')} sortie(s) ; entrée par défaut : "
                                    f"{(x.audio.get('entree_defaut') or {}).get('type')} ({(x.audio.get('entree_defaut') or {}).get('api')}) "
                                    f"16 kHz accepté={x.audio.get('entree_utilisable')} ; sortie par défaut : "
                                    f"{(x.audio.get('sortie_defaut') or {}).get('type')} 24 kHz accepté={x.audio.get('sortie_utilisable')}",
                                    "noms des périphériques masqués")
            x.rapport.gemini_audio.append("Inventaire des périphériques audio (aucune capture, aucun son joué).")
        if "selftest" in x.args.sauter:
            x.ctl("E16", "").regler(P.NON_TESTE, raison="étape sautée à la demande (--sauter selftest)")
        else:
            selftests(x)
    else:
        for cid in ("E13", "E16"):
            x.ctl(cid, "").regler(P.BLOQUE, raison="environnement de validation indisponible")
    ids = [f"E{i:02d}" for i in range(1, 13)]
    if "gemini" in x.args.sauter:
        for cid in ids:
            x.ctl(cid, "").regler(P.NON_TESTE, raison="étape sautée à la demande (--sauter gemini)")
        return
    if not x.consentements.get("gemini_reel") or not x.cle:
        raison = ("clé indisponible" if not x.sources_cle else
                  "non autorisé dans la fenêtre de lancement" if not x.consentements.get("gemini_reel") else
                  "clé illisible")
        for cid in ids:
            x.ctl(cid, "").regler(P.NON_TESTE, raison=raison + " (les faux serveurs ne sont pas équivalents)")
        return
    if not x.python_tests or x.worktree is None:
        for cid in ids:
            x.ctl(cid, "").regler(P.BLOQUE, raison="environnement de validation indisponible")
        return
    env = x.env_enfant("gemini reel", cle=True, qt=None if WINDOWS else "offscreen")
    code, out = x.executer("validate_real_gemini.py --mode auto (RÉEL)", [
        x.python_tests, str(ICI / "outils_surs.py"), "--script", "scripts/validate_real_gemini.py", "--", "--mode", "auto"],
        cwd=x.worktree, env=env, timeout=1800)
    verdicts = dict(re.findall(VERDICT_RE, out, re.MULTILINE))
    statut = P.REUSSI if verdicts and all(v == "PASS" for v in verdicts.values()) and code == 0 else (
        P.NON_TESTE if not verdicts or all(v == "NON TESTABLE" for v in verdicts.values()) else P.ECHEC)
    x.ctl("E11", "").regler(statut, ", ".join(f"{k}={v}" for k, v in verdicts.items()) or f"code {code}",
                            "sortie du script officiel (journal masqué)")
    code2, out2 = x.executer("validate_reconnect_v174_real.py (RÉEL)", [
        x.python_tests, str(ICI / "outils_surs.py"), "--script", "scripts/validate_reconnect_v174_real.py"],
        cwd=x.worktree, env=env, timeout=2400)
    verdicts2 = dict(re.findall(VERDICT_RE, out2, re.MULTILINE))
    statut2 = P.REUSSI if verdicts2 and all(v == "PASS" for v in verdicts2.values()) and code2 == 0 else (
        P.NON_TESTE if not verdicts2 or all(v == "NON TESTABLE" for v in verdicts2.values()) else P.ECHEC)
    x.ctl("E12", "").regler(statut2, ", ".join(f"{k}={v}" for k, v in verdicts2.items()) or f"code {code2}",
                            "sortie du script officiel (journal masqué)")
    out_dir = x.run / "gemini_reel"
    cmd = [x.python_tests, str(ICI / "gemini_reel_dashboard.py"), "--out", str(out_dir)]
    if WINDOWS:
        cmd.append("--natif")
    code3, _ = x.executer("Scénarios réels Dashboard + Gemini (E1-E10)", cmd, cwd=x.worktree, env=env, timeout=2400)
    result = out_dir / "gemini_reel.json"
    data = json.loads(result.read_text(encoding="utf-8")) if result.exists() else {}
    vus = set()
    for item in data.get("scenarios", []):
        cid = item["id"]
        vus.add(cid)
        x.ctl(cid, "").regler(item["statut"], item.get("resultat", ""), item.get("preuve", ""))
        if item["statut"] == P.ECHEC:
            x.probleme("bloquant", f"{cid} (Gemini réel) : {item.get('titre')}", item.get("resultat", ""), controle=cid)
    for cid in ids[:10]:
        if cid not in vus:
            x.ctl(cid, "").regler(P.ECHEC if data else P.BLOQUE,
                                  raison=data.get("erreur") or f"scénario non exécuté (code {code3})")
    if statut in (P.ECHEC,) or statut2 in (P.ECHEC,):
        e09 = x.ctl("E09", "")
        if e09.statut == P.REUSSI:
            e09.regler(P.ECHEC, e09.resultat + " ; mais un script vocal officiel a échoué (E11/E12)")
    x.rapport.gemini_audio.append(f"Gemini Live RÉEL (modèle {data.get('modele', '?')}) : entrée audio = "
                                  f"{data.get('entree', '?')} ; scénarios E1-E10, scripts officiels E11/E12. "
                                  f"Outils à effet de bord neutralisés (appelés : {data.get('outils_neutralises_appeles', [])}).")


def selftests(x: Execution) -> None:
    env = x.env_enfant("selftest", qt=None if WINDOWS else "offscreen")
    code1, out1 = x.executer("Auto-test du script officiel (faux serveur)", [
        x.python_tests, "scripts/validate_real_gemini.py", "--selftest"], cwd=x.worktree, env=env, timeout=600)
    out_dir = x.run / "selftest_dashboard"
    cmd = [x.python_tests, str(ICI / "gemini_reel_dashboard.py"), "--selftest", "--out", str(out_dir)]
    if WINDOWS:
        cmd.append("--natif")
    code2, _ = x.executer("Auto-test du harnais Dashboard + Gemini (faux serveur)", cmd, cwd=x.worktree, env=env,
                          timeout=600)
    data = {}
    if (out_dir / "gemini_reel.json").exists():
        data = json.loads((out_dir / "gemini_reel.json").read_text(encoding="utf-8"))
    resume = ", ".join(f"{s['id']}={s['statut']}" for s in data.get("scenarios", []))
    ok = code1 == 0 and code2 == 0
    x.ctl("E16", "").regler(P.REUSSI if ok else P.ECHEC,
                            f"script officiel --selftest code {code1} ; harnais Dashboard code {code2} ({resume})",
                            "faux serveur : prouve le harnais, PAS le service réel")


def memoire_et_donnees(x: Execution) -> None:
    afficher("\n[9/11] Mémoire et données personnelles")
    cases = (x.junit or {}).get("cas", [])
    for cid, (titre, noms) in VUES_MEMOIRE.items():
        selection = [c for c in cases if c["nom"] in noms]
        if not cases:
            x.ctl(cid, "").regler(P.NON_TESTE, raison="résultats JUnit indisponibles")
        elif not selection:
            x.ctl(cid, "").regler(P.NON_TESTE, raison="tests correspondants introuvables")
        else:
            echecs = [c for c in selection if c["issue"] in ("failure", "error")]
            x.ctl(cid, "").regler(P.ECHEC if echecs else P.REUSSI,
                                  f"{len(selection)} tests ({len(echecs)} en échec), bases SQLite temporaires — vue de C01",
                                  ", ".join(sorted({c['nom'] for c in selection}))[:400])
    migration_copie(x)


def migration_copie(x: Execution) -> None:
    bases = [r / "memory.db" for r in x.racines_reelles if (r / "memory.db").exists()]
    if not bases:
        x.ctl("F05", "").regler(P.NON_APPLICABLE, raison="aucune base mémoire Jarvis sur ce PC")
        x.ctl("B09", "").regler(P.NON_APPLICABLE if x.installation is None else P.NON_TESTE,
                                raison="aucune base mémoire à vérifier")
        return
    if not x.consentements.get("migration_copie"):
        x.ctl("F05", "").regler(P.NON_TESTE, raison="non autorisé dans la fenêtre de lancement")
        if x.installation is not None:
            x.ctl("B09", "").regler(P.NON_TESTE, raison="migration sur copie non autorisée")
        return
    if not x.python_tests or x.worktree is None:
        x.ctl("F05", "").regler(P.BLOQUE, raison="environnement de validation indisponible")
        return
    source = bases[0]
    copie_dir = x.run / "copie_memoire"
    copie_dir.mkdir(exist_ok=True)
    copie = copie_dir / "memory.db"
    try:
        # Lecture seule de l'original (ouverture en lecture binaire) : jamais SQLite sur l'original.
        for suffix in ("", "-wal"):
            src = Path(str(source) + suffix)
            if src.exists():
                with open(src, "rb") as reader, open(str(copie) + suffix, "wb") as writer:
                    shutil.copyfileobj(reader, writer)
        script = (
            "import json, sqlite3, sys\n"
            "sys.path.insert(0, '.')\n"
            "path = sys.argv[1]\n"
            "def compter(p):\n"
            "    con = sqlite3.connect(p)\n"
            "    try:\n"
            "        ok = con.execute('PRAGMA integrity_check').fetchone()[0]\n"
            "        tables = [r[0] for r in con.execute(\"select name from sqlite_master where type='table'\")]\n"
            "        counts = {t: con.execute(f'select count(*) from \"{t}\"').fetchone()[0] for t in tables}\n"
            "        version = con.execute('PRAGMA user_version').fetchone()[0]\n"
            "    finally:\n"
            "        con.close()\n"
            "    return {'integrite': ok, 'version': version, 'lignes': counts}\n"
            "avant = compter(path)\n"
            "from src.memory import MemoryManager\n"
            "manager = MemoryManager(path)\n"
            "stats = manager.memory_stats() if hasattr(manager, 'memory_stats') else {}\n"
            "for name in ('close', 'shutdown'):\n"
            "    if hasattr(manager, name):\n"
            "        getattr(manager, name)()\n"
            "        break\n"
            "apres = compter(path)\n"
            "print(json.dumps({'avant': avant, 'apres': apres, 'stats': {k: v for k, v in stats.items() if isinstance(v, (int, float))}}))\n"
        )
        code, out = x.executer("Migration de la mémoire sur une COPIE", [x.python_tests, "-c", script, str(copie)],
                               cwd=x.worktree, env=x.env_enfant("migration copie"), timeout=600)
        line = next((l for l in reversed(out.splitlines()) if l.startswith("{")), "")
        data = json.loads(line) if line else {}
        avant, apres = data.get("avant", {}), data.get("apres", {})
        conserve = all(apres.get("lignes", {}).get(t, -1) >= n for t, n in avant.get("lignes", {}).items()
                       if not t.startswith("sqlite_"))
        ok = code == 0 and avant.get("integrite") == "ok" and apres.get("integrite") == "ok" and conserve
        texte = (f"version {avant.get('version')} → {apres.get('version')}, intégrité {avant.get('integrite')} → "
                 f"{apres.get('integrite')}, aucune ligne perdue={conserve} "
                 f"(souvenirs : {avant.get('lignes', {}).get('memories', '?')} → {apres.get('lignes', {}).get('memories', '?')})")
        if avant.get("integrite") not in (None, "ok"):
            x.ctl("F05", "").regler(P.NON_TESTE, texte, raison="copie incohérente (Jarvis en cours d'écriture ?) : "
                                    "relancer avec Jarvis fermé")
        else:
            x.ctl("F05", "").regler(P.REUSSI if ok else P.ECHEC, texte,
                                    "copie temporaire supprimée ensuite ; l'original n'a jamais été ouvert par SQLite")
        if x.installation is not None:
            b05 = x.ctl("B05", "")
            b09_ok = ok and b05.statut == P.REUSSI
            x.ctl("B09", "").regler(P.REUSSI if b09_ok else (P.ECHEC if not ok else P.NON_TESTE),
                                    f"base réelle migrable par la PR ({texte}) ; installation : {b05.resultat}",
                                    raison="" if b09_ok or not ok else "installation non entièrement inspectée")
    finally:
        shutil.rmtree(copie_dir, ignore_errors=True)


def fenetre_finale(x: Execution) -> None:
    afficher("\n[10/11] Confirmations finales")
    if not x.interactif:
        for cid in ("D18", "D19", "E14", "E15"):
            x.ctl(cid, "").regler(P.NON_TESTE, raison="exécution non interactive (aucune fenêtre affichée)")
        return
    if not x.python_tests:
        for cid in ("D18", "D19", "E14", "E15"):
            x.ctl(cid, "").regler(P.BLOQUE, raison="PySide6 indisponible pour la fenêtre finale")
        return
    verdict, _code, _r = P.calculer_verdict(x.rapport)
    contexte = {"pr": x.args.pr, "verdict": verdict, "captures": x.dashboard_captures,
                "rendu": "Windows natif" if WINDOWS else "hors écran (répétition)",
                "notification_emise": x.notification_emise, "audio": x.audio,
                "audio_autorise": bool(x.consentements.get("audio_guide")), "python_audio": x.python_tests}
    entree, sortie = x.run / "fenetre_finale_entree.json", x.run / "fenetre_finale_reponses.json"
    entree.write_text(json.dumps(contexte, ensure_ascii=False), encoding="utf-8")
    x.executer("Fenêtre finale", [x.python_tests, str(ICI / "fenetre_finale.py"), "--entree", str(entree), "--sortie",
                                  str(sortie)], cwd=x.run, env=x.env_enfant("fenetre finale", qt=None if WINDOWS else "offscreen"),
               timeout=3600)
    rep = json.loads(sortie.read_text(encoding="utf-8")) if sortie.exists() else {}
    na = "non répondu"
    captures = rep.get("captures", na)
    if not x.dashboard_captures:
        x.ctl("D18", "").regler(P.NON_TESTE, raison="aucune capture native à confirmer")
    elif not WINDOWS:
        x.ctl("D18", "").regler(P.NON_TESTE, raison=f"captures hors écran, pas Windows (réponse : {captures})")
    elif captures in (na, "je ne sais pas"):
        x.ctl("D18", "").regler(P.NON_TESTE, raison=f"réponse : {captures}")
    else:
        x.ctl("D18", "").regler(P.REUSSI if captures == "oui" else P.ECHEC, f"réponse de l'utilisateur : {captures}")
        x.rapport.windows.append(f"Captures natives du Dashboard relues par l'utilisateur : « {captures} ».")
    vue = rep.get("notification_vue", na)
    if not x.notification_emise:
        x.ctl("D19", "").regler(P.NON_TESTE if WINDOWS else P.NON_APPLICABLE,
                                raison="aucune notification Windows émise")
    elif vue in ("oui", "non"):
        x.ctl("D19", "").regler(P.REUSSI if vue == "oui" else P.ECHEC, f"réponse : {vue}")
    else:
        x.ctl("D19", "").regler(P.NON_TESTE, raison=f"réponse : {vue}")
    hp = rep.get("haut_parleur") or {}
    if hp.get("teste") and hp.get("entendu") in ("oui", "non"):
        x.ctl("E14", "").regler(P.REUSSI if hp["entendu"] == "oui" else P.ECHEC, f"tonalité jouée, entendue : {hp['entendu']}")
        x.rapport.gemini_audio.append("Haut-parleur : tonalité de 1 s jouée sur la sortie par défaut.")
    else:
        x.ctl("E14", "").regler(P.NON_TESTE, raison="test du haut-parleur non effectué ou sans réponse")
    mic = rep.get("micro") or {}
    if mic.get("teste"):
        x.ctl("E15", "").regler(P.REUSSI if mic.get("signal_detecte") else P.ECHEC,
                                f"niveau {mic.get('rms_dbfs')} dBFS, voix détectée={mic.get('signal_detecte')}, "
                                f"enregistrement conservé={mic.get('conserve')}")
        x.rapport.gemini_audio.append("Micro : 3 s captées en mémoire après avertissement, niveau mesuré, tampon effacé.")
    else:
        x.ctl("E15", "").regler(P.NON_TESTE, raison=mic.get("erreur") or "test du micro non effectué")
    if rep.get("remarque"):
        x.rapport.remarques.append(f"Remarque de l'utilisateur : {rep['remarque'][:300]}")


def verifier_secrets(x: Execution) -> None:
    trouves, masques = [], 0
    fichiers = [p for p in x.run.rglob("*") if p.is_file() and p.suffix in (".log", ".json", ".md", ".jsonl", ".xml", ".txt")]
    if x.fichier_depot is not None:
        fichiers.append(x.fichier_depot)
    for path in fichiers:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if x.masque.contient_secret(text):
            cleaned = x.masque(text)
            if x.masque.contient_secret(cleaned):
                trouves.append(x.rel(path))
            else:
                path.write_text(cleaned, encoding="utf-8")
                masques += 1
    if trouves:
        x.ctl("F06", "").regler(P.ECHEC, f"secret encore présent dans {len(trouves)} fichier(s)", ", ".join(trouves[:5]))
        x.probleme("bloquant", "Secret détecté dans les sorties du protocole", ", ".join(trouves[:5]), controle="F06")
    else:
        x.ctl("F06", "").regler(P.REUSSI, f"{len(fichiers)} fichiers analysés (clé connue + motifs AIza…, ghp_…, "
                                f"Bearer…) ; {masques} fichier(s) re-masqué(s)",
                                "clé jamais transmise en argument, seulement par variable d'environnement aux "
                                "processus réels" if x.cle else "aucune clé chargée")


def donnees_personnelles(x: Execution) -> None:
    incidents_file = x.run / "incidents_garde.jsonl"
    incidents = []
    if incidents_file.exists():
        for line in incidents_file.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                incidents.append(json.loads(line))
            except ValueError:
                continue
    if incidents:
        par_test: dict[str, int] = {}
        for item in incidents:
            par_test[item.get("test") or item.get("pile", ["?"])[-1]] = par_test.get(
                item.get("test") or item.get("pile", ["?"])[-1], 0) + 1
        detail = "; ".join(f"{k} ×{v}" for k, v in list(par_test.items())[:10])
        x.ctl("F08", "").regler(P.ECHEC, f"{len(incidents)} accès bloqués aux données réelles", detail)
        x.probleme("bloquant", "Des tests ont tenté d'accéder aux données réelles (bloqué, rien n'a été modifié)",
                   detail, controle="F08")
    else:
        activations_file = x.run / "garde_activations.jsonl"
        activations = len(activations_file.read_text(encoding="utf-8").splitlines()) if activations_file.exists() else 0
        if activations:
            x.ctl("F08", "").regler(P.REUSSI, f"0 accès aux données réelles ; garde chargée dans {activations} processus "
                                    "Python du protocole (tests, contrôles, scénarios)",
                                    x.rel(activations_file) + " ; " + (x.rel(incidents_file) if incidents_file.exists()
                                                                       else "aucun incident"))
        else:
            x.ctl("F08", "").regler(P.NON_TESTE, raison="aucun processus de test n'a été lancé sous la garde")
    if not x.instantane_fait:
        x.ctl("F07", "").regler(P.NON_TESTE, raison="instantané initial non réalisé (protocole arrêté avant)")
        return
    apres = instantane(x)
    avant = x.instantane_avant
    ajoutes = sorted(set(apres) - set(avant))
    supprimes = sorted(set(avant) - set(apres))
    modifies = sorted(k for k in avant if k in apres and avant[k] != apres[k])
    x.rapport.donnees.update({
        "racines_protegees": [x.rel(r) for r in x.racines_reelles],
        "fichiers_avant": len(avant), "fichiers_apres": len(apres),
        "ajoutes": [x.rel(p) for p in ajoutes[:20]], "supprimes": [x.rel(p) for p in supprimes[:20]],
        "modifies": [x.rel(p) for p in modifies[:20]],
        "version_memoire_apres": bases_memoire(x),
        "incidents_garde": len(incidents),
        "jarvis_ouvert_pendant": x.jarvis_ouvert or "non",
    })
    if not avant and not apres:
        x.ctl("F07", "").regler(P.NON_APPLICABLE, raison="aucune donnée ni installation Jarvis sur ce PC")
    elif not (ajoutes or supprimes or modifies):
        x.ctl("F07", "").regler(P.REUSSI, f"{len(avant)} fichiers identiques avant/après (taille, date, empreinte SHA-256 "
                                "hors gros fichiers), version de la mémoire inchangée",
                                f"avant : {x.rapport.donnees.get('version_memoire_avant')} ; après : "
                                f"{x.rapport.donnees['version_memoire_apres']}")
    elif x.jarvis_ouvert:
        x.ctl("F07", "").regler(P.NON_TESTE, f"{len(ajoutes)} ajout(s), {len(supprimes)} suppression(s), {len(modifies)} "
                                "modification(s)", raison="Jarvis était ouvert et a pu écrire lui-même : preuve par "
                                "instantané non concluante (voir F08) — relancer avec Jarvis fermé")
    else:
        x.ctl("F07", "").regler(P.ECHEC, f"{len(ajoutes)} ajout(s), {len(supprimes)} suppression(s), {len(modifies)} "
                                "modification(s) alors que Jarvis était fermé",
                                ", ".join(x.rel(p) for p in (ajoutes + supprimes + modifies)[:8]))
        x.probleme("bloquant", "Fichiers Jarvis réels modifiés pendant la validation",
                   ", ".join(x.rel(p) for p in (ajoutes + supprimes + modifies)[:8]), controle="F07")


def finaliser_depot(x: Execution) -> None:
    if x.worktree is not None and (x.run / "depot").exists() and not x.args.garder_worktree:
        if x.rapport.depot.get("copie_isolee", "").startswith("git worktree"):
            code, _ = x.git("worktree", "remove", "--force", str(x.worktree), etape="Suppression de la copie isolée",
                            timeout=300)
            if code != 0:
                shutil.rmtree(x.worktree, ignore_errors=True)
                x.git("worktree", "prune")
        else:
            shutil.rmtree(x.worktree, ignore_errors=True)
    a06 = x.ctl("A06", "")
    if x.commit_teste and a06.statut == P.NON_TESTE and "CI en cours" in a06.raison:
        verifier_ci(x, "en fin d'exécution")
    apres = statut_git(x)
    main_apres = etat_main(x)
    x.rapport.depot["main_apres"] = main_apres
    x.rapport.depot["modifications_locales_apres"] = len(apres["entrees"])
    if x.statut_git_avant.get("code") == 0:
        identiques = apres["entrees"] == x.statut_git_avant["entrees"]
        x.ctl("A03", "").regler(P.REUSSI if identiques else P.ECHEC,
                                f"{len(x.statut_git_avant['entrees'])} modification(s) locale(s) avant, "
                                f"{len(apres['entrees'])} après, contenus identiques={identiques}",
                                "git status + SHA-256 de chaque fichier modifié (hors rapports du protocole)")
    else:
        x.ctl("A03", "").regler(P.NON_TESTE, raison="git status indisponible")
    if x.main_avant:
        same_local = main_apres["local"] == x.main_avant["local"]
        same_remote = main_apres["distant"] == x.main_avant["distant"] or "inconnu" in (main_apres["distant"], x.main_avant["distant"])
        x.ctl("A04", "").regler(P.REUSSI if same_local and same_remote else P.ECHEC,
                                f"main local {str(main_apres['local'])[:12]}, distant {str(main_apres['distant'])[:12]} — "
                                "identiques avant/après ; aucune commande push/merge/tag n'existe dans le protocole")


def emplacement(x: Execution, rel: str, motif: str) -> str:
    """« fichier:ligne » du motif dans la copie TESTÉE (exact pour ce commit), sinon le fichier seul."""
    racine = x.worktree if x.worktree is not None and x.worktree.exists() else x.depot
    try:
        for numero, ligne in enumerate((racine / rel).read_text(encoding="utf-8").splitlines(), start=1):
            if motif in ligne:
                return f"{rel}:{numero}"
    except OSError:
        pass
    return rel


def points_connus(x: Execution) -> None:
    """Points relevés par l'audit (docs/dashboard/AUDIT_PRE_FUSION.md) encore ouverts."""
    if x.ctl("E10", "").statut != P.REUSSI:
        x.probleme("important", "Intention du modèle face à une simple mention du « tableau de bord » non validée : "
                   "aucune garde locale, seule la description de l'outil guide Gemini",
                   "Couvert par E10 (Gemini réel) ; tant que E10 n'est pas RÉUSSI, le risque d'ouverture intempestive "
                   "reste non mesuré.", emplacement(x, "src/tools.py", '"Ouvre le Dashboard (tableau de bord'), "E10")
    # Corrigés depuis la validation Windows du 2026-10-16 (ne plus les signaler) :
    # double notification pour une panne audio (UI/dashboard/hub.py), overlay
    # « à l'écoute » pendant un message écrit (GeminiLive.submit_text publie
    # « réflexion » d'abord), ``python -m unittest`` sans isolation des données
    # (tests/_isolation.py). Tests : test_dashboard_ui_feedback.py,
    # test_unittest_isolation.py.


def resume_windows(x: Execution) -> None:
    if not WINDOWS:
        x.rapport.windows.append("Aucune : ce protocole a tourné hors de Windows. Les contrôles propres à Windows "
                                 "sont classés « NON TESTÉ » ou « NON APPLICABLE ».")
        return
    lignes = []
    if x.ctl("C01", "").statut in (P.REUSSI, P.ECHEC):
        lignes.append(f"Suite pytest complète exécutée sous Windows ({x.rapport.meta.get('machine')}), rendu "
                      f"{'natif' if x.args.suite_native else 'hors écran'} : {x.ctl('C01', '').statut}.")
    if x.ctl("C09", "").statut in (P.REUSSI, P.ECHEC):
        lignes.append(f"Étapes diagnostiques de la CI (unittest) sous Windows : {x.ctl('C09', '').statut}.")
    if x.ctl("B05", "").statut in (P.REUSSI, P.ECHEC):
        lignes.append(f"Installation quotidienne inspectée (version, DLL Python et Qt) : {x.ctl('B05', '').resultat}.")
    if x.ctl("C16", "").statut in (P.REUSSI, P.ECHEC):
        lignes.append(f"Exécutable installé lancé en test de fumée (données redirigées) : {x.ctl('C16', '').statut}.")
    if x.ctl("B07", "").statut == P.REUSSI:
        lignes.append(f"PySide6 6.12.0 mesuré sous Windows dans un venv jetable : {x.ctl('B07', '').resultat}.")
    x.rapport.windows[:0] = lignes


def calculer_ecarts(x: Execution) -> None:
    v = x.rapport.versions
    valid = v.get("Environnement de validation (PR)") or {}
    dev = v.get("Développement (.venv du dépôt)") or {}
    inst = v.get("Installation quotidienne") or {}
    lignes = []
    if valid:
        lignes.append(f"Environnement de test (PR) : Python {valid.get('python')}, PySide6 {valid.get('PySide6 (importé)')}, "
                      f"Qt {valid.get('Qt')} — identique à la CI (Python 3.11, requirements.txt + requirements-dev.txt).")
    if dev:
        dev_pyside = (dev.get("paquets") or {}).get("PySide6") or dev.get("PySide6 (importé)")
        lignes.append(f"Développement (.venv) : Python {dev.get('python')}, PySide6 {dev_pyside}"
                      + (" — DIFFÉRENT de l'environnement de test" if valid and str(dev_pyside) != str(
                          (valid.get("paquets") or {}).get("PySide6")) else ""))
    if inst:
        qt = inst.get("Qt6Core.dll") or "?"
        lignes.append(f"Installation quotidienne : {inst.get('python_embarque')}, Qt6Core {qt}"
                      + (" — Qt 6.12.0 embarqué : exposé au défaut si reproduit (B07)" if str(qt).startswith(PYSIDE_EXCLUE)
                         else "") + ". Elle sera remplacée par le build de la PR (build_windows.ps1 → requirements.txt).")
    elif WINDOWS:
        lignes.append("Aucune installation quotidienne détectée : Jarvis est utilisé depuis le dépôt (.venv).")
    b08 = x.ctl("B08", "")
    if b08.statut in (P.REUSSI, P.ECHEC):
        lignes.append("Packaging de la PR : scripts/build_windows.ps1 installe requirements.txt (PySide6 < 6.12) "
                      "puis PyInstaller — le prochain exécutable embarquera la même série PySide6 que l'environnement "
                      f"de test ; inspection : {b08.statut}.")
    x.rapport.ecarts[:0] = lignes


def recommandation(x: Execution) -> None:
    verdict, code, raisons = P.calculer_verdict(x.rapport)
    manuels = [c for c in x.rapport.controles if c.requis and c.statut in (P.NON_TESTE, P.BLOQUE)]
    lignes = [f"**Verdict : {verdict}.**"]
    if verdict == P.VERDICT_BLOQUEE:
        lignes.append("Ne pas fusionner : au moins un contrôle requis est en ÉCHEC (détail en §2, §8 et §14). "
                      "Corriger, pousser sur la branche de la PR, puis relancer `valider_pr.bat`.")
    elif verdict == P.VERDICT_INSUFFISANTE:
        lignes.append("Aucun échec bloquant démontré, mais la validation est INCOMPLÈTE : la PR ne peut pas être "
                      "déclarée prête. Contrôles requis restant à exécuter :")
        lignes += [f"- {ligne}" for ligne in P.regrouper(manuels)]
        lignes.append("Relancer `valider_pr.bat` sur le PC Windows de l'utilisateur, avec la clé Gemini disponible et "
                      "en répondant aux confirmations de la fenêtre finale.")
    else:
        lignes.append("Tous les contrôles requis sont RÉUSSIS sur ce PC, y compris Windows natif, Gemini réel et audio. "
                      "La PR peut être proposée à la fusion ; la décision et l'action restent humaines (aucune fusion "
                      "automatique).")
    x.rapport.meta["recommandation"] = lignes
    resume = []
    for section, titre in P.SECTIONS.items():
        items = [c for c in x.rapport.controles if c.section == section]
        compte = {st: sum(1 for c in items if c.statut == st) for st in P.STATUTS}
        resume.append(f"{section}. {titre} : " + ", ".join(f"{n} {st}" for st, n in compte.items() if n))
    windows = "oui (rendu natif)" if any(c.id.startswith("D") and c.statut == P.REUSSI and c.requis
                                         for c in x.rapport.controles) else "non"
    gemini = sum(1 for c in x.rapport.controles if c.id in [f"E{i:02d}" for i in range(1, 13)] and c.statut == P.REUSSI)
    resume.append(f"Dashboard validé sous Windows natif : {windows} ; scénarios Gemini RÉELS réussis : {gemini}/12 ; "
                  f"accès aux données réelles bloqués : {x.rapport.donnees.get('incidents_garde', '?')}.")
    x.rapport.meta["resume"] = resume
    x.code_sortie = code


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Validation finale automatisée d'une PR de Jarvis")
    parser.add_argument("--pr", type=int, default=PR_DEFAUT)
    parser.add_argument("--depot-github", default=DEPOT_GITHUB_DEFAUT)
    parser.add_argument("--depot-github-force", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--depot", default=None, help="racine du dépôt (défaut : celui de ce script)")
    parser.add_argument("--dossier-validation", default=None)
    parser.add_argument("--commit-audit", default=COMMIT_AUDIT)
    parser.add_argument("--non-interactif", action="store_true")
    parser.add_argument("--gemini-reel", choices=("auto", "oui", "non"), default="auto")
    parser.add_argument("--audio-guide", choices=("oui", "non"), default="oui")
    parser.add_argument("--notification-windows", choices=("oui", "non"), default="oui")
    parser.add_argument("--migration-copie", choices=("oui", "non"), default="oui")
    parser.add_argument("--sans-pyside612", action="store_true")
    parser.add_argument("--suite-native", action="store_true", help="suite pytest en rendu natif (comme la CI)")
    parser.add_argument("--garder-worktree", action="store_true")
    parser.add_argument("--garder-venvs", action="store_true")
    parser.add_argument("--delai-suite", type=int, default=5400)
    # Répétitions/diagnostic : étapes sautées => « NON TESTÉ — sautée à la demande », jamais « RÉUSSI ».
    parser.add_argument("--sauter", nargs="*", default=[], help=argparse.SUPPRESS,
                        choices=("suite", "diag", "protocole", "lint", "pyside612", "dashboard", "gemini", "selftest"))
    args = parser.parse_args(argv)

    x = Execution(args)
    x.code_sortie = P.CODE_ERREUR_PROTOCOLE
    declarer_controles(x)
    afficher("=" * 72)
    afficher(f" Validation de la PR #{args.pr} de Jarvis — protocole {VERSION_PROTOCOLE}")
    afficher(f" Journaux : {x.run}")
    afficher("=" * 72)
    try:
        decouvrir(x)
        x.ecrire_rapport()
        fenetre_lancement(x)
        afficher("\n   Instantané des données réelles (lecture seule)…")
        x.instantane_avant = instantane(x)
        x.instantane_fait = True
        x.rapport.donnees["version_memoire_avant"] = bases_memoire(x)
        verifier_depot(x)
        x.ecrire_rapport()
        creer_copie_isolee(x)
        environnements(x)
        x.ecrire_rapport()
        tests_automatises(x)
        x.ecrire_rapport()
        dashboard_reel(x)
        x.ecrire_rapport()
        gemini_et_audio(x)
        x.ecrire_rapport()
        memoire_et_donnees(x)
        x.ecrire_rapport()
        fenetre_finale(x)
        x.rapport.termine = True
    except Annulation as exc:
        x.rapport.erreur_protocole = str(exc)
    except KeyboardInterrupt:
        x.rapport.erreur_protocole = "interrompu par l'utilisateur (Ctrl+C)"
    except Exception as exc:
        x.rapport.erreur_protocole = f"{type(exc).__name__}: {exc}"
        (x.logs / "erreur_protocole.txt").write_text(x.masque(traceback.format_exc()), encoding="utf-8")
        afficher(f"\n[ERREUR DU PROTOCOLE] {x.masque(str(exc))} — le rapport est tout de même écrit.")
    finally:
        afficher("\n[11/11] Vérifications finales et rapport")
        for step in (finaliser_depot, donnees_personnelles, verifier_secrets):
            try:
                step(x)
            except Exception as exc:
                x.probleme("important", f"Vérification finale « {step.__name__} » impossible", str(exc))
        x.cle = ""
        for step in (points_connus, resume_windows, calculer_ecarts):
            try:
                step(x)
            except Exception as exc:
                x.probleme("mineur", f"Synthèse « {step.__name__} » impossible", str(exc))
        recommandation(x)
        x.ecrire_rapport(final=True)
        try:
            avant_f06 = x.ctl("F06", "").statut
            verifier_secrets(x)  # second passage : inclut le rapport final
            if x.ctl("F06", "").statut != avant_f06:
                recommandation(x)
                x.ecrire_rapport(final=True)
        except Exception:
            pass
        verdict, code, _ = P.calculer_verdict(x.rapport)
        afficher("\n" + "=" * 72)
        afficher(f" VERDICT : {verdict}")
        afficher(f" Rapport (dépôt) : {x.fichier_depot}")
        afficher(f" Copie locale    : {x.fichier_local}")
        afficher("=" * 72)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
