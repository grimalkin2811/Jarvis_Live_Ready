"""Contrôle du Dashboard RÉEL — fenêtres Qt natives (D01 à D17).

Lancé par ``valider_pr.py`` avec le venv de validation, dans la copie isolée
de la PR, sous la garde des données réelles. Sous Windows (``--natif``), la
plateforme Qt est ``windows`` : vraies fenêtres, vraie zone de notification,
vrai rendu. Ailleurs, le script s'exécute hors écran à titre de RÉPÉTITION :
l'orchestrateur classe alors tous les contrôles « NON TESTÉ » (un rendu hors
écran ne valide pas Windows).

Composition identique à la production (``src/ui.py``) : contrôleur avec le
pont vocal, icône de notification réelle, relais de notifications réel. Seuls
les effets de bord sont neutralisés : aucune boîte de dialogue modale (refus
automatique), aucun fichier ouvert dans l'explorateur, aucune fermeture de
Jarvis, moteur vocal simulé pour D14 (Gemini réel = étape E), exécuteur de
tâches de fond simulé (aucun appel réseau). Toutes les données sont dans un
dossier temporaire (``isolated_jarvis``).

Sortie : ``<out>/dashboard_reel.json`` + captures PNG dans ``<out>``.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import statistics
import sys
import tempfile
import threading
import time
import traceback
import weakref
from pathlib import Path

#: Code de Jarvis testé : la copie isolée de la PR (posée par l'orchestrateur), jamais la copie de
#: travail de l'utilisateur ; à défaut (exécution manuelle), le dépôt qui contient ce script.
ROOT = Path(os.environ.get("JARVIS_VALIDATION_RACINE") or Path(__file__).resolve().parents[2]).resolve()

REUSSI, ECHEC, NON_TESTE, NON_APPLICABLE = "RÉUSSI", "ÉCHEC", "NON TESTÉ", "NON APPLICABLE"

#: Seuils officiels (scripts/dashboard_perf.py, docs/dashboard/PROTOCOLE_DE_TEST.md).
SEUIL_OUVERTURE_MS = 1500.0
SEUIL_PAGE_MEDIANE_MS = 150.0
SEUIL_PAGE_MAX_MS = 400.0
SEUIL_CPU_MASQUE = 2.0
SEUIL_RSS_MO = 40.0

RESULTATS: list[dict] = []
EVENEMENTS: dict[str, list] = {"exceptions": [], "qt": []}
CAPTURES: list[str] = []


def _excepthook(exc_type, exc, tb):
    EVENEMENTS["exceptions"].append("".join(traceback.format_exception(exc_type, exc, tb))[-2000:])
    sys.__excepthook__(exc_type, exc, tb)


def _thread_excepthook(args):
    EVENEMENTS["exceptions"].append(
        "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))[-2000:])


def _gravite_qt(kind) -> str:
    from PySide6.QtCore import QtMsgType

    return {QtMsgType.QtCriticalMsg: "critique", QtMsgType.QtFatalMsg: "fatal",
            QtMsgType.QtWarningMsg: "avertissement"}.get(kind, "info")


def _message_qt(kind, context, message):
    gravite = _gravite_qt(kind)
    if gravite == "info":
        return
    EVENEMENTS["qt"].append({"gravite": gravite, "message": str(message)[:400]})


def criticals() -> list[dict]:
    dangereux = ("Signal source has been deleted", "already deleted", "Internal C++ object")
    return [e for e in EVENEMENTS["qt"] if e["gravite"] in ("critique", "fatal")
            or any(d in e["message"] for d in dangereux)]


def controle(cid: str, titre: str, requis: bool = True):
    def decorate(fn):
        def run(*args, **kwargs):
            exc_before = len(EVENEMENTS["exceptions"])
            crit_before = len(criticals())
            start = time.perf_counter()
            try:
                statut, texte, preuve, mesures = fn(*args, **kwargs)
            except Exception as exc:
                statut, texte = ECHEC, f"exception : {type(exc).__name__}: {exc}"
                preuve, mesures = traceback.format_exc(limit=5)[-1200:], {}
            nouvelles = EVENEMENTS["exceptions"][exc_before:]
            nouveaux_qt = criticals()[crit_before:]
            if statut == REUSSI and (nouvelles or nouveaux_qt):
                statut = ECHEC
                texte += (f" — {len(nouvelles)} exception(s) Python et {len(nouveaux_qt)} message(s) Qt "
                          "critique(s) pendant le contrôle")
                preuve = (preuve + " | " + (nouvelles[0][-400:] if nouvelles else nouveaux_qt[0]["message"])).strip(" |")
            RESULTATS.append({
                "id": cid, "titre": titre, "statut": statut, "resultat": texte, "preuve": preuve,
                "mesures": mesures, "requis": requis, "duree_s": round(time.perf_counter() - start, 2),
            })
            print(f"[{cid}] {statut} — {titre} : {texte}", flush=True)
            return statut
        return run
    return decorate


class Contexte:
    """État partagé des contrôles (application, contrôleur, dossiers)."""

    def __init__(self, out: Path, natif: bool, notification_native: bool) -> None:
        self.out = out
        self.natif = natif
        self.notification_native = notification_native
        self.app = None
        self.env = None
        self.controller = None
        self.tray = None
        self.notification_bridge = None
        self.created: list = []
        self.opened_paths: list[str] = []
        self.native_sent: list[tuple[str, str]] = []
        self.tray_messages: list[tuple[str, str]] = []

    def capture(self, widget, nom: str) -> str:
        path = self.out / f"{nom}.png"
        try:
            pixmap = widget.grab()
            if pixmap.isNull() or not pixmap.save(str(path)):
                return ""
        except Exception:
            return ""
        CAPTURES.append(path.name)
        return path.name


def _interface_factice():
    from PySide6.QtCore import QObject, Signal

    class InterfaceFactice(QObject):
        mode_changed = Signal(str)

        def __init__(self) -> None:
            super().__init__()
            self.mode = "blob"

        def current_mode(self) -> str:
            return self.mode

        def set_mode(self, mode: str) -> str:
            self.mode = mode
            self.mode_changed.emit(mode)
            return mode

    return InterfaceFactice()


def fabrique(ctx: Contexte, **kwargs):
    from UI.dashboard.controller import DashboardController

    kwargs.setdefault("state_path", str(Path(ctx.env["root"]) / "ui" / "dashboard_state.json"))
    kwargs.setdefault("attach_bridge", False)
    kwargs.setdefault("dialog_executor", lambda dialog: False)  # jamais de dialogue modal bloquant
    kwargs.setdefault("open_path", lambda path: ctx.opened_paths.append(path) or True)
    kwargs.setdefault("reduced_motion_probe", lambda: False)
    kwargs.setdefault("quit_callback", lambda: None)
    kwargs.setdefault("interface_controller", _interface_factice())
    if not ctx.notification_native:
        kwargs.setdefault("native_sender", lambda title, message: ctx.native_sent.append((title, message)))
    controller = DashboardController(**kwargs)
    ctx.created.append(controller)
    return controller


def attendre(ctx: Contexte, predicate, timeout: float = 3.0) -> bool:
    from tests.dashboard_support import wait_until

    return wait_until(ctx.app, predicate, timeout)


def pomper(ctx: Contexte, seconds: float = 0.2) -> None:
    from tests.dashboard_support import pump

    pump(ctx.app, seconds)


def widget_par_type(window, type_id: str):
    for canvas in window.all_canvases():
        for placement in canvas.page.widgets:
            if placement.type == type_id:
                frame = canvas.frames.get(placement.id)
                if frame is not None and frame.widget is not None:
                    return canvas.page.id, frame.widget
    return None, None


def page_contenant(controller, type_id: str) -> str | None:
    for page in controller.store.config.layout.pages:
        if any(w.type == type_id for w in page.widgets):
            return page.id
    return None


def rss_mo() -> float | None:
    try:
        import psutil

        return psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Contrôles
# ---------------------------------------------------------------------------


@controle("D01", "Démarrage et première ouverture du Dashboard")
def d01(ctx: Contexte):
    from PySide6.QtGui import QGuiApplication

    controller = ctx.controller
    start = time.perf_counter()
    controller.open()
    shown = attendre(ctx, lambda: controller.is_open(), 5.0)
    elapsed_ms = (time.perf_counter() - start) * 1000
    window = controller.window
    exposed = bool(window is not None and window.windowHandle() is not None and window.windowHandle().isExposed())
    pomper(ctx, 0.4)
    image = ctx.capture(window, "D01_ouverture") if window is not None else ""
    mesures = {"plateforme_qt": QGuiApplication.platformName(), "ouverture_ms": round(elapsed_ms, 1),
               "exposee": exposed, "seuil_ms": SEUIL_OUVERTURE_MS}
    ok = shown and exposed and elapsed_ms <= SEUIL_OUVERTURE_MS
    texte = (f"fenêtre affichée en {elapsed_ms:.0f} ms (seuil {SEUIL_OUVERTURE_MS:.0f}), plateforme "
             f"{mesures['plateforme_qt']}, exposée={exposed}")
    return (REUSSI if ok else ECHEC), texte, image, mesures


@controle("D02", "Points d'entrée : outil vocal, page inconnue, zone de notification, Ctrl+D de l'orbe, fermeture")
def d02(ctx: Contexte):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QSystemTrayIcon

    from src import tools

    controller = ctx.controller
    details, problemes = {}, []
    controller.hide_window()
    pomper(ctx, 0.1)

    box: dict = {}
    worker = threading.Thread(target=lambda: box.setdefault("r", tools.open_dashboard("memoire")))
    worker.start()
    while worker.is_alive():
        pomper(ctx, 0.02)
    result = box.get("r") or {}
    page_ok = attendre(ctx, lambda: controller.is_open() and controller.window.current_page() == "memoire", 3)
    details["outil_open_dashboard"] = {k: result.get(k) for k in ("success", "confirmed", "page_found")}
    if not (result.get("success") and result.get("confirmed", True) and page_ok):
        problemes.append(f"outil open_dashboard('memoire') : {details['outil_open_dashboard']}")

    box.clear()
    worker = threading.Thread(target=lambda: box.setdefault("r", tools.open_dashboard("page-qui-n-existe-pas")))
    worker.start()
    while worker.is_alive():
        pomper(ctx, 0.02)
    unknown = box.get("r") or {}
    details["page_inconnue"] = {k: unknown.get(k) for k in ("success", "page_found")}
    message = str(unknown.get("message") or unknown.get("error") or "")
    # Contrat (audit P7) : la page inconnue n'est JAMAIS annoncée comme affichée.
    if unknown.get("page_found") is not False or "introuvable" not in message:
        problemes.append(f"page inconnue annoncée comme trouvée : {details['page_inconnue']}")

    closed = tools.close_dashboard()
    hidden = attendre(ctx, lambda: not controller.is_open(), 3)
    details["outil_close_dashboard"] = {k: closed.get(k) for k in ("success", "confirmed")}
    if not (closed.get("success") and hidden):
        problemes.append("close_dashboard n'a pas masqué la fenêtre")

    if ctx.tray is not None and getattr(ctx.tray, "_dashboard_action", None) is not None:
        ctx.tray._dashboard_action.trigger()
        tray_ok = attendre(ctx, controller.is_open, 3)
        details["zone_de_notification"] = "entrée « Ouvrir le Dashboard » déclenchée" if tray_ok else "sans effet"
        if not tray_ok:
            problemes.append("l'entrée de la zone de notification n'ouvre pas le Dashboard")
        controller.hide_window()
    else:
        available = QSystemTrayIcon.isSystemTrayAvailable()
        details["zone_de_notification"] = f"indisponible (isSystemTrayAvailable={available})"
        if ctx.natif:
            problemes.append("zone de notification indisponible en mode natif")

    from UI.jarvis_menu import MorphingOrbWidget

    orb = MorphingOrbWidget()
    try:
        orb.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_D, Qt.ControlModifier, "d"))
        orb_ok = attendre(ctx, controller.is_open, 3)
        details["orbe_ctrl_d"] = "ouvre le Dashboard" if orb_ok else "sans effet"
        if not orb_ok:
            problemes.append("Ctrl+D dans l'orbe n'ouvre pas le Dashboard")
    finally:
        orb.timer.stop()
        orb.close()
        orb.deleteLater()
        pomper(ctx, 0.05)
    statut = REUSSI if not problemes else ECHEC
    return statut, "; ".join(problemes) or "5 points d'entrée vérifiés", "", details


@controle("D03", "Pages et widgets : chargement sans erreur, captures natives")
def d03(ctx: Contexte):
    controller = ctx.controller
    controller.open()
    attendre(ctx, controller.is_open, 3)
    window = controller.window
    pages, erreurs, images = {}, [], []
    for page in controller.store.config.layout.pages:
        if not window.open_page(page.id, animate=False):
            erreurs.append(f"page {page.id} : ouverture refusée")
            continue
        canvas = window._current_canvas()
        attendre(ctx, lambda: all(f.widget is None or f.widget.state != "loading" for f in canvas.frames.values()), 6)
        pomper(ctx, 0.3)
        etats: dict[str, int] = {}
        for placement in canvas.page.widgets:
            frame = canvas.frames.get(placement.id)
            widget = frame.widget if frame is not None else None
            state = getattr(widget, "state", "absent") if widget is not None else "absent"
            etats[state] = etats.get(state, 0) + 1
            if state in ("error", "absent"):
                detail = ""
                for attr in ("info", "message", "error_label"):
                    label = getattr(widget, attr, None)
                    if label is not None and hasattr(label, "text"):
                        detail = label.text()[:160]
                        break
                erreurs.append(f"{page.id}/{placement.type} : {state} {detail}".strip())
        pages[page.id] = etats
        images.append(ctx.capture(window, f"D03_page_{page.id}"))
    statut = REUSSI if pages and not erreurs else ECHEC
    texte = f"{len(pages)} pages, " + (f"{len(erreurs)} widget(s) en erreur : " + " | ".join(erreurs[:6]) if erreurs
                                       else "aucun widget en erreur")
    return statut, texte, ", ".join(i for i in images if i), {"pages": pages}


@controle("D04", "Navigation : temps de changement de page (rendu natif)")
def d04(ctx: Contexte):
    window = ctx.controller.window
    ids = [p.id for p in ctx.controller.store.config.layout.pages]
    durees = []
    for _round in range(3):
        for page in ids:
            start = time.perf_counter()
            window.open_page(page, animate=False)
            window.repaint()
            ctx.app.processEvents()
            durees.append((time.perf_counter() - start) * 1000)
    mediane, maximum = statistics.median(durees), max(durees)
    ok = mediane <= SEUIL_PAGE_MEDIANE_MS and maximum <= SEUIL_PAGE_MAX_MS
    texte = (f"médiane {mediane:.1f} ms (seuil {SEUIL_PAGE_MEDIANE_MS:.0f}), max {maximum:.1f} ms "
             f"(seuil {SEUIL_PAGE_MAX_MS:.0f}), {len(durees)} changements")
    return (REUSSI if ok else ECHEC), texte, "", {"mediane_ms": round(mediane, 1), "max_ms": round(maximum, 1)}


@controle("D05", "Mode édition : ajout de widget, enregistrement, relecture après redémarrage")
def d05(ctx: Contexte):
    controller = ctx.controller
    window = controller.window
    window.open_page("accueil", animate=False)
    window.set_edit_mode(True)
    pomper(ctx, 0.1)
    image = ctx.capture(window, "D05_mode_edition")
    placement = window.add_widget("custom.panel")
    if placement is None:
        window.set_edit_mode(False, save=False)
        return ECHEC, "add_widget a refusé l'ajout (aucune place ?)", image, {}
    window.set_edit_mode(False, save=True)
    flushed = controller.flush()
    again = fabrique(ctx, state_path=controller.store.path)
    try:
        present = any(w.id == placement.id for w in again.store.config.layout.page("accueil").widgets)
        preset = again.store.config.layout.preset
    finally:
        again.shutdown()
    ok = flushed and present and preset == "personnalisee"
    texte = f"widget ajouté, enregistré (flush={flushed}), relu par un nouveau contrôleur={present}, préréglage={preset}"
    return (REUSSI if ok else ECHEC), texte, image, {}


@controle("D06", "Thèmes : bascule à chaud et persistance")
def d06(ctx: Contexte):
    from UI.dashboard.config import load_config

    controller = ctx.controller
    images, erreurs = [], []
    for theme in ("edith", "friday", "jarvis"):
        controller.theme_manager.update(theme=theme)
        pomper(ctx, 0.15)
        applied = controller.theme_manager.theme
        if applied.spec.id != theme or applied.accent.name() not in controller.window.styleSheet():
            erreurs.append(theme)
        images.append(ctx.capture(controller.window, f"D06_theme_{theme}"))
    controller.flush()
    saved = load_config(controller.store.path).config.appearance.theme
    ok = not erreurs and saved == "jarvis"
    texte = f"3 thèmes appliqués, thème relu sur disque : {saved}" + (f" ; échecs : {erreurs}" if erreurs else "")
    return (REUSSI if ok else ECHEC), texte, ", ".join(i for i in images if i), {}


@controle("D07", "Notes : ajout depuis le Dashboard, même fichier que l'outil vocal (isolé)")
def d07(ctx: Contexte):
    from src import tools

    controller = ctx.controller
    page = page_contenant(controller, "productivity.notes")
    if page is None:
        return NON_TESTE, "aucun widget Notes dans la disposition par défaut", "", {}
    controller.window.open_page(page, animate=False)
    _page, widget = widget_par_type(controller.window, "productivity.notes")
    attendre(ctx, lambda: widget.state in ("ready", "empty"), 4)
    widget.entry.setText("Note du protocole de validation")
    widget._add()
    attendre(ctx, lambda: widget.message.text() == "Note enregistrée.", 4)
    notes = tools.read_notes(limit=5).get("notes", [])
    ok = any(n.get("texte") == "Note du protocole de validation" for n in notes)
    return (REUSSI if ok else ECHEC), f"message « {widget.message.text()} », note relue={ok}", "", {}


@controle("D08", "Tâches personnelles : ajout et achèvement (base isolée)")
def d08(ctx: Contexte):
    controller = ctx.controller
    page = page_contenant(controller, "tasks.todo")
    if page is None:
        return NON_TESTE, "aucun widget Tâches dans la disposition par défaut", "", {}
    controller.window.open_page(page, animate=False)
    _page, widget = widget_par_type(controller.window, "tasks.todo")
    attendre(ctx, lambda: widget.state in ("ready", "empty"), 4)
    widget.entry.setText("Tâche du protocole de validation")
    widget._add()
    added = attendre(ctx, lambda: widget.message.text() == "Tâche ajoutée.", 4)
    attendre(ctx, lambda: widget.list.count() >= 1 and widget.list.itemWidget(widget.list.item(0)) is not None, 4)
    box = widget.list.itemWidget(widget.list.item(0))
    box.setChecked(True)
    done = attendre(ctx, lambda: widget.message.text() == "Tâche terminée.", 4)
    pending = ctx.env["todo"].list_tasks(status="pending", limit=50).get("taches", [])
    ok = added and done and not pending
    return (REUSSI if ok else ECHEC), f"ajout={added}, achèvement={done}, restantes={len(pending)}", "", {}


@controle("D09", "Tâches de fond : affichage d'une tâche réelle du gestionnaire (exécuteur simulé)")
def d09(ctx: Contexte):
    controller = ctx.controller
    manager = ctx.env.get("task_manager")
    if manager is None:
        return NON_TESTE, "gestionnaire de tâches isolé indisponible", "", {}
    page = page_contenant(controller, "tasks.background")
    if page is None:
        return NON_TESTE, "aucun widget Tâches de fond dans la disposition par défaut", "", {}
    controller.window.open_page(page, animate=False)
    _page, widget = widget_par_type(controller.window, "tasks.background")
    task = manager.create_task("Recherche de validation", "contenu de test")
    shown = attendre(ctx, lambda: widget.list.count() >= 1, 4)
    # Une tâche terminée dont le résultat n'a pas encore été consulté porte le
    # badge « NOUVEAU » (voulu) ; « Terminée » une fois le résultat vu.
    finished = attendre(ctx, lambda: widget.list.count() >= 1 and widget.list.item(0).text().startswith(
        ("[NOUVEAU]", "[Terminée]")), 8)
    real = manager.get_task(task.id)
    status = getattr(getattr(real, "status", None), "value", "?")
    texte = widget.list.item(0).text().replace("\n", " ")[:120] if widget.list.count() else ""
    image = ctx.capture(controller.window, "D09_taches_de_fond")
    ok = shown and finished and status == "COMPLETED"
    return (REUSSI if ok else ECHEC), (f"tâche affichée={shown}, statut gestionnaire={status}, badge de fin "
                                       f"affiché={finished} ({texte})"), image, {}


@controle("D10", "Notifications : toast dans le Dashboard, centre de notifications, notification Windows")
def d10(ctx: Contexte):
    controller = ctx.controller
    center = controller.hub.notifications
    controller.open()
    attendre(ctx, controller.is_open, 3)
    before = center.unread_count()
    item = center.notify("task_completed", "Validation Jarvis", "Notification de test du protocole (premier plan)",
                         key="validation:premier-plan")
    pomper(ctx, 0.3)
    toasts = list(controller.window.toasts.history)
    toast_ok = any("Validation Jarvis" in message for message, _level in toasts)
    image = ctx.capture(controller.window, "D10_notification")
    native_details = "non demandée"
    native_ok = True
    if ctx.notification_native:
        controller.hide_window()
        pomper(ctx, 0.2)
        center.notify("task_completed", "Validation Jarvis", "Notification Windows de test (aucune action requise)",
                      key="validation:windows")
        pomper(ctx, 1.0)
        native_ok = bool(ctx.tray_messages)
        native_details = (f"bulle Windows émise via la zone de notification ({len(ctx.tray_messages)})" if native_ok
                          else "aucune bulle émise")
        controller.open()
        attendre(ctx, controller.is_open, 3)
    ok = item is not None and toast_ok and center.unread_count() > before and native_ok
    texte = f"toast affiché={toast_ok}, non lues {before}→{center.unread_count()}, Windows : {native_details}"
    return (REUSSI if ok else ECHEC), texte, image, {"bulles": len(ctx.tray_messages)}


@controle("D11", "Fermeture/réouverture ×3 (masquage puis libération de la fenêtre) sans exception ni fuite")
def d11(ctx: Contexte):
    from PySide6.QtCore import QCoreApplication, QEvent

    controller = ctx.controller
    rss_start = rss_mo()
    refs = []
    for cycle in range(3):
        controller.open()
        attendre(ctx, controller.is_open, 3)
        pomper(ctx, 0.2)
        controller.window.close()  # même chemin que la croix de la fenêtre
        attendre(ctx, lambda: not controller.is_open(), 3)
    controller.store.config.close.mode = "interactive"
    controller.store.config.close.interactive["release_window"] = True
    controller.store.changed("close")
    for cycle in range(3):
        controller.open()
        attendre(ctx, controller.is_open, 3)
        pomper(ctx, 0.2)
        refs.append(weakref.ref(controller.window))
        controller.window.close()
        attendre(ctx, lambda: controller.window is None, 3)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        gc.collect()
        pomper(ctx, 0.1)
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    gc.collect()
    survivors = sum(1 for ref in refs if ref() is not None)
    controller.store.config.close.mode = "hide"
    controller.store.changed("close")
    controller.open()
    reopened = attendre(ctx, controller.is_open, 3)
    rss_end = rss_mo()
    growth = (rss_end - rss_start) if (rss_start is not None and rss_end is not None) else None
    ok = reopened and survivors == 0 and (growth is None or growth <= SEUIL_RSS_MO)
    texte = (f"6 fermetures (3 masquages, 3 libérations), fenêtres libérées encore vivantes={survivors}, "
             f"réouverture={reopened}, mémoire {('%+.1f Mo' % growth) if growth is not None else 'non mesurée'}")
    return (REUSSI if ok else ECHEC), texte, "", {"survivantes": survivors, "croissance_rss_mo": growth}


@controle("D12", "Persistance des préférences (géométrie, vue, dernière page) — fichier isolé")
def d12(ctx: Contexte):
    controller = ctx.controller
    window = controller.window
    controller.open("taches")
    attendre(ctx, controller.is_open, 3)
    screen = window.screen().availableGeometry()
    width, height = min(1000, screen.width() - 60), min(700, screen.height() - 80)
    window.showNormal()
    window.resize(width, height)
    pomper(ctx, 0.2)
    controller.set_view_mode("onepage")
    pomper(ctx, 0.2)
    controller.hide_window()
    controller.flush()
    again = fabrique(ctx, state_path=controller.store.path)
    try:
        cfg = again.store.config
        again.open()
        attendre(ctx, again.is_open, 3)
        pomper(ctx, 0.2)
        size = (again.window.width(), again.window.height())
        view, last = cfg.layout.view_mode, cfg.window.last_page
    finally:
        again.shutdown()
        pomper(ctx, 0.1)
    controller.set_view_mode("multipage")
    size_ok = abs(size[0] - width) <= 2 and abs(size[1] - height) <= 2
    ok = view == "onepage" and last == "taches" and size_ok
    texte = f"vue={view}, dernière page={last}, taille {size} (attendu {(width, height)})"
    return (REUSSI if ok else ECHEC), texte, "", {}


@controle("D13", "Plein écran et classes de largeur (écran réel)")
def d13(ctx: Contexte):
    controller = ctx.controller
    controller.open()
    attendre(ctx, controller.is_open, 3)
    window = controller.window
    window.toggle_fullscreen()
    pomper(ctx, 0.4)
    full = window.isFullScreen()
    image = ctx.capture(window, "D13_plein_ecran")
    window.toggle_fullscreen()
    pomper(ctx, 0.4)
    restored = not window.isFullScreen()
    screen = window.screen().availableGeometry()
    classes, skipped = {}, []
    for width, expected in ((800, "narrow"), (1250, "normal"), (1700, "wide")):
        if width > screen.width():
            skipped.append(f"{expected} ({width} px > écran {screen.width()} px)")
            continue
        window.resize(width, min(800, screen.height() - 80))
        pomper(ctx, 0.25)
        classes[expected] = window.size_class()
    wrong = {k: v for k, v in classes.items() if k != v}
    ok = full and restored and not wrong and classes
    texte = (f"plein écran={full}, restauration={restored}, classes={classes}"
             + (f", non testables sur cet écran : {skipped}" if skipped else ""))
    return (REUSSI if ok else ECHEC), texte, image, {"ecran": [screen.width(), screen.height()]}


@controle("D14", "Conversation écrite depuis le widget (interface native, moteur SIMULÉ — Gemini réel en E)")
def d14(ctx: Contexte):
    from src.conversation import get_default_conversation_context
    from src.runtime_status import RUNTIME, EngineState
    from tests.dashboard_support import FakeTextEngine

    controller = ctx.controller
    page = page_contenant(controller, "jarvis.conversation")
    if page is None:
        return NON_TESTE, "aucun widget Conversation dans la disposition par défaut", "", {}
    engine = FakeTextEngine(get_default_conversation_context(), reply="Réponse simulée du protocole.")
    RUNTIME.set_engine_state(EngineState.READY, "")
    RUNTIME.register_text_handler(engine)
    try:
        controller.open(page)
        attendre(ctx, controller.is_open, 3)
        _page, widget = widget_par_type(controller.window, "jarvis.conversation")
        attendre(ctx, lambda: widget.send_button.isEnabled(), 3)
        widget.input.setPlainText("Message écrit du protocole de validation")
        widget.send()
        accepted = attendre(ctx, lambda: not widget._pending and widget.input.toPlainText() == "", 4)
        image = ctx.capture(controller.window, "D14_conversation")
        engine.mode = "busy"
        widget.input.setPlainText("Deuxième message pendant que Jarvis répond")
        widget.send()
        refused = attendre(ctx, lambda: widget.banner.isVisible() and "occupé" in widget.banner.text(), 4)
        kept = widget.input.toPlainText() == "Deuxième message pendant que Jarvis répond"
    finally:
        RUNTIME.unregister_text_handler(engine)
        RUNTIME.set_engine_state(EngineState.NOT_STARTED, "")
    ok = accepted and refused and kept and engine.calls[:1] == ["Message écrit du protocole de validation"]
    texte = f"envoi accepté et champ vidé={accepted}, refus « occupé » affiché={refused}, texte conservé={kept}"
    return (REUSSI if ok else ECHEC), texte, image, {}


@controle("D15", "Aucune exception Python ni erreur Qt critique sur l'ensemble des contrôles")
def d15(ctx: Contexte):
    crit = criticals()
    warnings = [e for e in EVENEMENTS["qt"] if e not in crit]
    ok = not EVENEMENTS["exceptions"] and not crit
    texte = (f"{len(EVENEMENTS['exceptions'])} exception(s) Python, {len(crit)} message(s) Qt critique(s), "
             f"{len(warnings)} avertissement(s) Qt non bloquant(s)")
    preuve = (EVENEMENTS["exceptions"][0][-500:] if EVENEMENTS["exceptions"] else
              (crit[0]["message"] if crit else (warnings[0]["message"] if warnings else "")))
    return (REUSSI if ok else ECHEC), texte, preuve, {"avertissements_qt": [w["message"] for w in warnings[:10]]}


@controle("D16", "Écran, mise à l'échelle et DPI (informatif)", requis=False)
def d16(ctx: Contexte):
    from PySide6.QtGui import QGuiApplication

    screens = []
    for screen in QGuiApplication.screens():
        geo = screen.geometry()
        screens.append({"taille": [geo.width(), geo.height()], "ratio": screen.devicePixelRatio(),
                        "dpi_logique": round(screen.logicalDotsPerInch(), 1)})
    return REUSSI, f"{len(screens)} écran(s) : {screens}", "", {"ecrans": screens}


@controle("D17", "Dashboard masqué : aucun widget actif, aucun minuteur, CPU au repos")
def d17(ctx: Contexte):
    from PySide6.QtCore import QEventLoop, QTimer

    controller = ctx.controller
    controller.open()
    attendre(ctx, controller.is_open, 3)
    pomper(ctx, 0.3)
    controller.hide_window()
    pomper(ctx, 0.5)
    stats = controller.window.widget_stats() if controller.window is not None else {"active_widgets": 0, "active_timers": 0}
    loop = QEventLoop()
    cpu0, wall0 = time.process_time(), time.perf_counter()
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    cpu = (time.process_time() - cpu0) / max(1e-6, time.perf_counter() - wall0) * 100
    resources = controller.hub.resources.running()
    ok = stats.get("active_widgets") == 0 and stats.get("active_timers") == 0 and not resources and cpu <= SEUIL_CPU_MASQUE
    texte = (f"widgets actifs={stats.get('active_widgets')}, minuteurs={stats.get('active_timers')}, "
             f"mesures ressources={'actives' if resources else 'arrêtées'}, CPU {cpu:.2f} % (seuil {SEUIL_CPU_MASQUE})")
    return (REUSSI if ok else ECHEC), texte, "", {"cpu_pct": round(cpu, 2)}


# ---------------------------------------------------------------------------


def preparer(ctx: Contexte) -> None:
    from PySide6.QtCore import qInstallMessageHandler
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    ctx.app = QApplication.instance() or QApplication([])
    qInstallMessageHandler(_message_qt)
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook

    from src.background_tasks import manager as manager_module
    from tests.dashboard_support import make_task_manager

    task_root = Path(ctx.env["root"]) / "taches_de_fond"
    try:
        manager = make_task_manager(task_root)
        manager_module._DEFAULT = manager
        ctx.env["task_manager"] = manager
    except Exception as exc:
        print(f"[D] gestionnaire de tâches isolé indisponible : {exc}", flush=True)

    ctx.controller = fabrique(ctx, attach_bridge=True)
    if QSystemTrayIcon.isSystemTrayAvailable():
        from src.ui import SessionVisibility, _build_tray_icon
        from UI.notification_bridge import NotificationBridge

        ctx.tray = _build_tray_icon(lambda: None, lambda: None, on_hide=lambda: None, visibility=SessionVisibility(),
                                    mode_getter=lambda: "blob", on_mode_change=lambda m: None,
                                    on_dashboard=ctx.controller.open)
        if ctx.tray is not None:
            ctx.controller.tray = ctx.tray
            original = ctx.tray.showMessage

            def espion(title, message, *args, **kwargs):
                ctx.tray_messages.append((str(title), str(message)))
                return original(title, message, *args, **kwargs)

            ctx.tray.showMessage = espion
            if ctx.notification_native:
                ctx.notification_bridge = NotificationBridge(ctx.tray)
                ctx.controller.attach_notification_bridge(ctx.notification_bridge)


def nettoyer(ctx: Contexte) -> None:
    for controller in reversed(ctx.created):
        try:
            controller.shutdown()
        except Exception:
            pass
    if ctx.notification_bridge is not None:
        try:
            ctx.notification_bridge.close()
        except Exception:
            pass
    if ctx.tray is not None:
        try:
            ctx.tray.hide()
        except Exception:
            pass
    manager = (ctx.env or {}).get("task_manager")
    if manager is not None:
        for name in ("shutdown", "close", "stop"):
            if hasattr(manager, name):
                try:
                    getattr(manager, name)()
                except Exception:
                    pass
                break
    try:
        pomper(ctx, 0.2)
    except Exception:
        pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--natif", action="store_true", help="plateforme Qt native (Windows)")
    parser.add_argument("--notification-native", action="store_true",
                        help="émettre une vraie notification Windows (consentement de la fenêtre de lancement)")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.natif and sys.platform == "win32":
        os.environ["QT_QPA_PLATFORM"] = "windows"
    else:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT))
    ctx = Contexte(out, natif=bool(args.natif and sys.platform == "win32"),
                   notification_native=bool(args.notification_native))
    debut = time.strftime("%Y-%m-%dT%H:%M:%S")
    erreur = ""
    from tests.dashboard_support import isolated_jarvis

    with isolated_jarvis(prefix="jarvis-validation-dashboard-") as env:
        ctx.env = env
        try:
            preparer(ctx)
            for check in (d01, d02, d03, d04, d05, d06, d07, d08, d09, d10, d11, d12, d13, d14, d15, d16, d17):
                check(ctx)
        except Exception as exc:  # pragma: no cover - erreur de mise en place
            erreur = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        finally:
            nettoyer(ctx)
    try:
        from PySide6.QtGui import QGuiApplication

        plateforme = QGuiApplication.platformName()
    except Exception:
        plateforme = os.environ.get("QT_QPA_PLATFORM", "?")
    rapport = {
        "debut": debut,
        "fin": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "plateforme_qt": plateforme,
        "natif": ctx.natif,
        "erreur": erreur,
        "controles": RESULTATS,
        "captures": CAPTURES,
        "notifications_windows_emises": len(ctx.tray_messages),
        "exceptions_python": len(EVENEMENTS["exceptions"]),
        "messages_qt": EVENEMENTS["qt"][:50],
    }
    (out / "dashboard_reel.json").write_text(json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if not erreur and all(r["statut"] != ECHEC for r in RESULTATS) else 1


if __name__ == "__main__":
    tempfile.tempdir = None
    raise SystemExit(main())
