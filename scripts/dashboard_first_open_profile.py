"""Profil de la PREMIÈRE ouverture du Dashboard, phase par phase (D01).

Validation Windows du 2026-10-16 (PR #50, Pentium Gold G5400) : fenêtre
affichée en 1 867 ms (seuil 1 500), contenu complet en 1 910 ms — alors que
la même ouverture prend ~50 ms hors écran sous Linux. Le contenu ne coûte que
~45 ms : tout le temps est AVANT la première peinture. Ce script découpe CE
MÊME évènement (de la demande d'ouverture à la fenêtre peinte) en phases,
dans des processus NEUFS (première ouverture réelle, rien de préchauffé), et
mesure séparément le coût à froid de la base de polices de Qt (premier texte
rendu dans un processus) :

* ``import_fenetre`` : import de ``UI.dashboard.window`` et de ses modules ;
* ``construction`` : ``DashboardWindow(...)`` (en-tête, navigation, style) ;
* ``affichage`` : ``show_restored()`` (fenêtre native, géométrie) ;
* ``premiere_peinture`` : jusqu'à la première peinture (polissage du style,
  polices, composition) — c'est l'évènement « fenêtre affichée » de D01 ;
* ``contenu`` : page et widgets construits après la peinture (indicatif).

Puis les ouvertures SUIVANTES dans le même processus (fenêtre masquée puis
rouverte). Aucune donnée réelle : dossier temporaire (``isolated_jarvis``),
moteur simulé.

    python scripts/dashboard_first_open_profile.py --natif --repetitions 5
    python scripts/dashboard_first_open_profile.py --json profil.json

Hors Windows, le rendu est hors écran : chiffres indicatifs seulement.
"""

from __future__ import annotations

import time

_T_PROCESS = time.perf_counter()  # au plus tôt : coût réel du démarrage du processus

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import statistics  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SEUIL_MS = 1500.0


def _ms(a: float, b: float) -> float:
    return round((b - a) * 1000.0, 1)


#: Symboles de l'interface du Dashboard (icônes de pages, boutons, cadres) :
#: absents de la police d'interface par défaut, ils peuvent déclencher une
#: recherche de police de REPLI au premier rendu.
SYMBOLES = "⌂◈◎☑⚙✎▤▦●◫⤢✉○⋯✕◌→"



def symboles_du_dashboard() -> str:
    """Tous les symboles (U+2190 et au-delà) des chaînes du Dashboard, hors docstrings.

    Inventaire vérifié par la sonde ``couverture`` : ceux de
    ``glyphs.INTERFACE_FONT_SYMBOLS`` doivent être dans CHAQUE police
    principale des thèmes, tous les autres dans la police de symboles —
    sinon Qt rechargerait les polices du système au premier rendu (D01).
    """
    import ast

    trouves: set[str] = set()
    for fichier in sorted((ROOT / "UI" / "dashboard").rglob("*.py")):
        arbre = ast.parse(fichier.read_text(encoding="utf-8"))
        docstrings = {id(n.body[0].value) for n in ast.walk(arbre)
                      if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                      and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)
                      and isinstance(n.body[0].value.value, str)}
        for n in ast.walk(arbre):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings:
                trouves.update(ch for ch in n.value if 0x2190 <= ord(ch) <= 0x2BFF or 0x1F000 <= ord(ch) <= 0x1FAFF)
    return "".join(sorted(trouves))


#: Étapes du constructeur de la fenêtre chronométrées une à une.
ETAPES_CONSTRUCTEUR = ("_build_header", "_build_sidebar", "_build_edit_bar", "_install_shortcuts",
                       "_apply_stylesheet", "rebuild_pages", "_update_status", "_update_bell",
                       "_restore_geometry")


def fichiers_mappes() -> set[str]:
    """Bibliothèques et polices projetées en mémoire par ce processus (psutil)."""
    try:
        import psutil

        noms = set()
        for carte in psutil.Process().memory_maps(grouped=True):
            chemin = str(getattr(carte, "path", "") or "")
            if chemin.lower().endswith((".dll", ".pyd", ".so", ".ttf", ".ttc", ".otf", ".fon")) or ".so." in chemin:
                noms.add(Path(chemin).name)
        return noms
    except Exception:
        return set()


def mesure_unique(natif: bool, reouvertures: int) -> dict:
    """Une première ouverture dans CE processus (neuf), puis des réouvertures."""
    if natif and sys.platform == "win32":
        os.environ["QT_QPA_PLATFORM"] = "windows"
    else:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("JARVIS_REDUCED_MOTION", "0")
    sys.path.insert(0, str(ROOT))
    phases: dict[str, float] = {}
    t = time.perf_counter()
    from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F401

    phases["import_qt"] = _ms(t, time.perf_counter())
    t = time.perf_counter()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    phases["qapplication"] = _ms(t, time.perf_counter())
    t = time.perf_counter()
    from src.runtime_status import RUNTIME, EngineState
    from tests.dashboard_support import isolated_jarvis

    phases["import_jarvis"] = _ms(t, time.perf_counter())
    resultat: dict = {"plateforme_qt": "", "phases_ms": phases}
    with isolated_jarvis("jarvis-profil-ouverture-") as env:
        RUNTIME.reset()
        RUNTIME.set_engine_state(EngineState.READY, model="modele-de-test")
        RUNTIME.update(audio_state="ok", wake_word="loaded", memory_state="ok")
        t = time.perf_counter()
        from UI.dashboard.controller import DashboardController

        controller = DashboardController(
            state_path=str(Path(env["root"]) / "s.json"), attach_bridge=False,
            dialog_executor=lambda d: True, open_path=lambda p: True,
            native_sender=lambda title, message: None, reduced_motion_probe=lambda: False,
        )
        phases["controleur"] = _ms(t, time.perf_counter())
        # Chronométrage des étapes du constructeur (enveloppes posées AVANT la
        # mesure, hors du temps mesuré ; même code exécuté).
        import UI.dashboard.window as module_fenetre

        etapes: dict[str, float] = {}

        def enveloppe(nom, fonction):
            def mesuree(self, *args, **kwargs):
                debut = time.perf_counter()
                try:
                    return fonction(self, *args, **kwargs)
                finally:
                    etapes[nom] = round(etapes.get(nom, 0.0) + (time.perf_counter() - debut) * 1000.0, 1)
            return mesuree

        classe = module_fenetre.DashboardWindow
        originales = {nom: getattr(classe, nom) for nom in ETAPES_CONSTRUCTEUR if hasattr(classe, nom)}
        for nom, fonction in originales.items():
            setattr(classe, nom, enveloppe(nom, fonction))
        modules_avant = set(sys.modules)
        cartes_avant = fichiers_mappes()
        # ---- Évènement D01 : de la demande d'ouverture à la fenêtre peinte ----
        t0 = time.perf_counter()
        import UI.dashboard.window  # noqa: F401

        t_import = time.perf_counter()
        window = controller.ensure_window()
        t_build = time.perf_counter()
        for nom, fonction in originales.items():
            setattr(classe, nom, fonction)
        resultat["etapes_constructeur_ms"] = etapes
        resultat["modules_importes_pendant_la_construction"] = sorted(set(sys.modules) - modules_avant)[:40]
        cartes_apres_construction = fichiers_mappes()
        resultat["fichiers_charges_pendant_la_construction"] = sorted(cartes_apres_construction - cartes_avant)[:60]
        stamps: dict[str, float] = {}
        original = window._complete_initial_build

        def premiere_peinture() -> None:
            stamps.setdefault("peinture", time.perf_counter())
            original()

        window._complete_initial_build = premiere_peinture
        controller.open()
        t_show = time.perf_counter()
        deadline = time.monotonic() + 15.0
        while "peinture" not in stamps and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.001)
        t_paint = stamps.get("peinture", time.perf_counter())
        while getattr(window, "_initial_build_pending", False) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.001)
        t_content = time.perf_counter()
        phases.update(
            import_fenetre=_ms(t0, t_import),
            construction=_ms(t_import, t_build),
            affichage=_ms(t_build, t_show),
            premiere_peinture=_ms(t_show, t_paint),
            contenu=_ms(t_paint, t_content),
        )
        polices_contenu = sorted(n for n in fichiers_mappes() - cartes_apres_construction
                                 if n.lower().endswith((".ttf", ".ttc", ".otf", ".fon")))
        resultat["polices_chargees_pendant_le_contenu"] = polices_contenu[:40]
        resultat["premiere_ouverture_ms"] = _ms(t0, t_paint)
        resultat["contenu_complet_ms"] = _ms(t0, t_content)
        resultat["depuis_lancement_processus_ms"] = _ms(_T_PROCESS, t_paint)
        resultat["plateforme_qt"] = QtGui.QGuiApplication.platformName()
        durees = []
        for _ in range(max(0, reouvertures)):
            controller.hide_window()
            app.processEvents()
            time.sleep(0.05)
            t = time.perf_counter()
            controller.open()
            app.processEvents()
            durees.append(_ms(t, time.perf_counter()))
        resultat["reouvertures_ms"] = durees
        controller.shutdown()
        RUNTIME.reset()
    return resultat


def cout_polices_a_froid(natif: bool) -> dict:
    """Coût du PREMIER texte rendu dans un processus neuf (base de polices Qt)."""
    if natif and sys.platform == "win32":
        os.environ["QT_QPA_PLATFORM"] = "windows"
    else:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication, QLabel

    from PySide6.QtGui import QFontMetrics

    app = QApplication.instance() or QApplication([])
    t = time.perf_counter()
    familles = len(QFontDatabase.families())
    t_db = time.perf_counter()
    label = QLabel("Jarvis — Tableau de bord")
    label.show()
    app.processEvents()
    t_label = time.perf_counter()
    avant = fichiers_mappes()
    t_sym = time.perf_counter()
    largeur = QFontMetrics(label.font()).horizontalAdvance(SYMBOLES)
    symboles = QLabel(SYMBOLES)
    symboles.show()
    app.processEvents()
    t_sym_end = time.perf_counter()
    return {"familles": familles, "base_polices_ms": _ms(t, t_db), "premier_libelle_ms": _ms(t_db, t_label),
            "symboles_ms": _ms(t_sym, t_sym_end), "symboles_largeur": largeur,
            "fichiers_charges_par_les_symboles": sorted(fichiers_mappes() - avant)[:30]}


POLICES_SONDEES = ("Segoe UI", "Segoe UI Variable Text", "Segoe UI Symbol", "Segoe UI Emoji", "Consolas",
                   "Cascadia Mono", "DejaVu Sans")


POLICES_EXT = (".ttf", ".ttc", ".otf", ".fon")


def _caracteres_hors_latin1(racine) -> str:
    """Caractères au-delà de Latin-1 dans les textes d'un cadre (hors éléments ``glyph``)."""
    from PySide6.QtWidgets import QAbstractButton, QComboBox, QLabel, QLineEdit, QListWidget, QPlainTextEdit, QTextEdit

    textes = [QLabel.text(w) for w in racine.findChildren(QLabel) if not w.property("glyph")]
    textes += [QAbstractButton.text(w) for w in racine.findChildren(QAbstractButton) if not w.property("glyph")]
    for liste in racine.findChildren(QListWidget):
        textes += [liste.item(i).text() for i in range(liste.count())]
    for combo in racine.findChildren(QComboBox):
        textes += [combo.itemText(i) for i in range(combo.count())]
    textes += [w.toPlainText() for w in racine.findChildren(QTextEdit)]
    textes += [w.toPlainText() + w.placeholderText() for w in racine.findChildren(QPlainTextEdit)]
    textes += [w.text() + w.placeholderText() for w in racine.findChildren(QLineEdit)]
    return "".join(sorted({ch for texte in textes for ch in texte if ord(ch) > 0xFF}))


def _pile_dashboard(cadre_py, limite: int = 4) -> list[str]:
    pile = []
    while cadre_py is not None and len(pile) < limite:
        chemin = cadre_py.f_code.co_filename.replace("\\", "/")
        if "/UI/dashboard/" in chemin:
            pile.append(f"{Path(chemin).name}:{cadre_py.f_lineno} {cadre_py.f_code.co_name}")
        cadre_py = cadre_py.f_back
    return pile


def _nom_appel(fonction) -> tuple[str, bool]:
    soi = getattr(fonction, "__self__", None)
    module = type(soi).__module__ if soi is not None else str(getattr(fonction, "__module__", "") or "")
    return f"{type(soi).__name__}.{getattr(fonction, '__name__', '?')}", module.startswith("PySide6")


def profileur_appels_natifs(seuil_ms: float = 5.0, compter=None):
    """Profileur (sys.setprofile) des appels NATIFS (PySide6) d'une portion de code.

    - appels d'au moins ``seuil_ms`` (durée), avec la ligne du Dashboard qui les a faits ;
    - si ``compter`` est fourni (nombre de fichiers de police projetés), chaque
      appel PySide6 après lequel ce nombre AUGMENTE, avec les appels natifs
      englobants (un rappel Python exécuté pendant un appel plus large n'est
      pas confondu avec lui). À chaud (fichiers en cache), un repli des
      polices peut durer moins de ``seuil_ms`` : seul le comptage le voit.
    """
    en_cours: list = []
    longs: list[dict] = []
    sauts: list[dict] = []
    etat = {"dernier": compter() if compter else 0}

    def profil(cadre_py, evenement, fonction):
        if evenement == "c_call":
            en_cours.append((time.perf_counter(), fonction, cadre_py))
            return
        if evenement not in ("c_return", "c_exception") or not en_cours:
            return
        debut, fonction, cadre_appel = en_cours.pop()
        duree = (time.perf_counter() - debut) * 1000.0
        nom, pyside = _nom_appel(fonction)
        if not pyside:
            return
        if duree >= seuil_ms:
            longs.append({"ms": round(duree, 1), "appel": nom, "pile": _pile_dashboard(cadre_appel)})
        if compter is not None and len(sauts) < 6:
            n = compter()
            if n > etat["dernier"]:
                englobants = [f"{_nom_appel(f)[0]} @ {(_pile_dashboard(c, 1) or ['?'])[0]}"
                              for _t, f, c in en_cours if _nom_appel(f)[1]]
                sauts.append({"polices": n - etat["dernier"], "appel": nom, "ms": round(duree, 1),
                              "pile": _pile_dashboard(cadre_appel, 3), "englobants": englobants[-3:]})
            etat["dernier"] = n

    profil.sauts = sauts
    return profil, longs


def attribution_contenu(natif: bool) -> dict:
    """Polices chargées par CHAQUE cadre (rendu forcé dès sa création), processus neuf.

    Toutes les pages (vue multipage) puis la vue OnePage : le premier cadre qui
    fait charger les polices du système (repli de Qt) est le coupable ; ses
    caractères hors Latin-1 sont relevés. Chiffres d'attribution seulement
    (le rendu forcé modifie les durées : aucune durée n'est rapportée).
    """
    if natif and sys.platform == "win32":
        os.environ["QT_QPA_PLATFORM"] = "windows"
    else:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("JARVIS_REDUCED_MOTION", "0")
    sys.path.insert(0, str(ROOT))
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from src.runtime_status import RUNTIME, EngineState
    from tests.dashboard_support import isolated_jarvis

    def polices(avant: set[str]) -> list[str]:
        return sorted(n for n in fichiers_mappes() - avant if n.lower().endswith(POLICES_EXT))

    def attendre(condition, delai: float = 8.0) -> None:
        fin = time.monotonic() + delai
        while not condition() and time.monotonic() < fin:
            app.processEvents()
            time.sleep(0.002)

    with isolated_jarvis("jarvis-profil-attribution-") as env:
        RUNTIME.reset()
        RUNTIME.set_engine_state(EngineState.READY, model="modele-de-test")
        RUNTIME.update(audio_state="ok", wake_word="loaded", memory_state="ok")
        import UI.dashboard.grid_view as grille
        from UI.dashboard.controller import DashboardController

        controller = DashboardController(
            state_path=str(Path(env["root"]) / "s.json"), attach_bridge=False,
            dialog_executor=lambda d: True, open_path=lambda p: True,
            native_sender=lambda title, message: None, reduced_motion_probe=lambda: False,
        )
        cadres: list[dict] = []
        vue = {"nom": "multipage"}
        originale = grille.GridCanvas._create_frame

        elements: list[dict] = []
        appels_longs: list[dict] = []
        sauts_polices: list[dict] = []

        def creer(self, placement):
            avant = fichiers_mappes()
            if not cadres:
                def compter() -> int:
                    return sum(1 for n in fichiers_mappes() if n.lower().endswith(POLICES_EXT))

                profil, longs = profileur_appels_natifs(compter=compter)
                sys.setprofile(profil)
                try:
                    cadre = originale(self, placement)
                finally:
                    sys.setprofile(None)
                appels_longs.extend(sorted(longs, key=lambda a: -a["ms"])[:4])
                sauts_polices.extend(profil.sauts)
            else:
                cadre = originale(self, placement)
            if not cadres:
                # Premier cadre : construction seule, puis chaque élément (feuilles
                # d'abord : un conteneur rendrait tous ses enfants d'un coup).
                construction_cadre = polices(avant)
                elements.append({"element": "construction du cadre", "polices": len(construction_cadre)})
                descendants = cadre.findChildren(QtWidgets.QWidget)

                def profondeur(w) -> int:
                    n = 0
                    while w is not None and w is not cadre:
                        w, n = w.parentWidget(), n + 1
                    return n

                for w in sorted(descendants, key=profondeur, reverse=True):
                    a = fichiers_mappes()
                    w.ensurePolished()
                    w.sizeHint()
                    if w.isVisibleTo(cadre):
                        w.grab()
                    nouvelles_w = polices(a)
                    if nouvelles_w:
                        f = w.font()
                        texte = ""
                        for lecture in (QtWidgets.QLabel.text, QtWidgets.QAbstractButton.text):
                            try:
                                texte = lecture(w)
                                break
                            except TypeError:
                                continue
                        elements.append({"element": type(w).__name__, "role": w.property("role"),
                                         "glyph": bool(w.property("glyph")), "texte": str(texte)[:30],
                                         "familles": f.families() or [f.family()], "graisse": int(f.weight()),
                                         "px": f.pixelSize(), "pt": round(f.pointSizeF(), 1),
                                         "espacement": round(f.letterSpacing(), 1), "polices": len(nouvelles_w)})
            cadre.grab()  # mise en page + peinture du cadre et de ses enfants
            app.processEvents()
            nouvelles = polices(avant)
            cadres.append({"vue": vue["nom"], "page": self.page.id, "type": placement.type,
                           "polices": len(nouvelles), "exemples": nouvelles[:3],
                           "caracteres": _caracteres_hors_latin1(cadre)})
            return cadre

        grille.GridCanvas._create_frame = creer
        initial = fichiers_mappes()
        window = controller.ensure_window()
        construction = polices(initial)
        controller.open()
        attendre(lambda: not getattr(window, "_initial_build_pending", False))

        def page_complete(page_id: str) -> bool:
            toiles = [c for c in window.all_canvases() if c.page.id == page_id]
            return bool(toiles) and all(p.id in c.frames for c in toiles for p in c.page.widgets if not p.hidden)

        for page in list(controller.store.config.layout.pages):
            if page.extras.get("_hidden_overflow"):
                continue
            window.open_page(page.id)
            attendre(lambda pid=page.id: page_complete(pid))
        vue["nom"] = "onepage"
        controller.set_view_mode("onepage")
        window._onepage.build_all()
        attendre(lambda: True, 0.3)
        avant = fichiers_mappes()
        window.grab()
        fenetre = polices(avant)
        grille.GridCanvas._create_frame = originale
        controller.shutdown()
        RUNTIME.reset()
    par_page: dict[str, int] = {}
    for c in cadres:
        cle = f"{c['vue']}:{c['page']}"
        par_page[cle] = par_page.get(cle, 0) + c["polices"]
    return {"polices_construction": len(construction), "cadres": len(cadres), "polices_par_page": par_page,
            "coupables": [c for c in cadres if c["polices"] > 0][:6],
            "premier_cadre_par_element": elements[:8],
            "premier_cadre_appels_longs": appels_longs,
            "premier_cadre_sauts_de_polices": sauts_polices,
            "polices_fenetre_entiere": len(fenetre)}


def couverture_du_correctif(installees: set[str]) -> dict:
    """Vérifie les deux prémisses du correctif D01 sur les polices RÉELLES du système.

    * ``interface_non_couverts`` : symboles laissés dans le texte
      (``INTERFACE_FONT_SYMBOLS``) absents d'une police principale d'un thème
      (interface, titres, chiffres ; première famille installée de chaque rôle) ;
    * ``symboles_non_couverts`` : autres symboles du Dashboard absents de la
      police de symboles. Les deux doivent être vides.
    """
    from PySide6.QtGui import QFont, QRawFont

    sys.path.insert(0, str(ROOT))
    from UI.dashboard.glyphs import INTERFACE_FONT_SYMBOLS
    from UI.dashboard.theme import SYMBOL_FAMILIES, THEMES

    def manquants(famille: str, texte: str) -> str:
        brute = QRawFont.fromFont(QFont(famille))
        return "".join(ch for ch in texte if not brute.supportsCharacter(ord(ch)))

    inventaire = symboles_du_dashboard()
    surs = "".join(sorted(INTERFACE_FONT_SYMBOLS))
    principales = sorted({next((f for f in familles if f in installees), "")
                          for spec in THEMES.values()
                          for familles in (spec.font_ui, spec.font_title, spec.font_mono)} - {""})
    interface = {famille: manquants(famille, surs) for famille in principales}
    symboles = next((f for f in SYMBOL_FAMILIES if f in installees), "")
    autres = "".join(ch for ch in inventaire if ch not in INTERFACE_FONT_SYMBOLS)
    return {"inventaire": inventaire, "symboles_surs_dans_le_texte": surs,
            "polices_principales": principales,
            "interface_non_couverts": {f: m for f, m in interface.items() if m},
            "police_symboles": symboles or "aucune",
            "symboles_non_couverts": manquants(symboles, autres) if symboles else autres}


def sonde_polices(natif: bool, mode: str) -> dict:
    """Couverture des symboles par police (sans repli) ou coût d'un rendu, dans un processus neuf.

    ``couverture`` : ``QRawFont.supportsCharacter`` — la police seule, aucune
    recherche de repli ; ``rendu:<familles>`` : fichiers de police chargés et
    durée du rendu des symboles avec cette chaîne de familles.
    """
    if natif and sys.platform == "win32":
        os.environ["QT_QPA_PLATFORM"] = "windows"
    else:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QFont, QFontDatabase, QRawFont
    from PySide6.QtWidgets import QApplication, QLabel

    app = QApplication.instance() or QApplication([])
    installees = set(QFontDatabase.families())
    if mode == "couverture":
        resultat = {}
        for famille in POLICES_SONDEES:
            if famille not in installees:
                resultat[famille] = "absente"
                continue
            brute = QRawFont.fromFont(QFont(famille))
            manquants = "".join(ch for ch in SYMBOLES if not brute.supportsCharacter(ord(ch)))
            resultat[famille] = f"manquants : {manquants}" if manquants else "tous"
        return {"couverture": resultat, **couverture_du_correctif(installees)}
    morceaux = mode.split(":")
    familles = morceaux[1].split("|")
    texte = SYMBOLES if (len(morceaux) < 3 or morceaux[2] == "symboles") else "Accueil · Mémoire — Tâches « Paramètres »"
    avant = fichiers_mappes()
    font = QFont()
    font.setFamilies(familles)
    style = morceaux[3] if len(morceaux) > 3 else ""
    if "gras" in style:
        font.setBold(True)
    if "espacement" in style:
        font.setLetterSpacing(QFont.AbsoluteSpacing, 2.0)
    if "26px" in style:
        font.setPixelSize(26)
    t = time.perf_counter()
    if morceaux[0] == "icone":
        # Dessin des glyphes dans une image (icônes) avec cette police seule.
        from PySide6.QtGui import QPainter, QPixmap

        pixmap = QPixmap(400, 40)
        peintre = QPainter(pixmap)
        peintre.setFont(font)
        peintre.drawText(4, 30, texte)
        peintre.end()
    else:
        label = QLabel(texte)
        label.setFont(font)
        label.show()
        app.processEvents()
    return {"mode": mode, "rendu_ms": _ms(t, time.perf_counter()),
            "polices_chargees": len(fichiers_mappes() - avant),
            "exemples": sorted(fichiers_mappes() - avant)[:8]}


def _enfant(args: list[str]) -> dict:
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=300, cwd=ROOT)
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    return {"erreur": (proc.stderr or proc.stdout)[-600:], "code": proc.returncode}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--natif", action="store_true", help="plateforme Qt native (Windows)")
    parser.add_argument("--repetitions", type=int, default=3, help="processus neufs (premières ouvertures)")
    parser.add_argument("--reouvertures", type=int, default=3)
    parser.add_argument("--json", default="", help="fichier de sortie")
    parser.add_argument("--enfant", help=argparse.SUPPRESS)
    parser.add_argument("--sonde-polices", action="store_true",
                        help="couverture des symboles par police et coût du rendu selon la chaîne de familles")
    args = parser.parse_args(argv)
    if args.enfant == "ouverture":
        print(json.dumps(mesure_unique(args.natif, args.reouvertures), ensure_ascii=False))
        return 0
    if args.enfant == "polices":
        print(json.dumps(cout_polices_a_froid(args.natif), ensure_ascii=False))
        return 0
    if args.enfant == "attribution":
        print(json.dumps(attribution_contenu(args.natif), ensure_ascii=False))
        return 0
    if args.enfant and args.enfant.startswith(("couverture", "rendu:", "icone:")):
        print(json.dumps(sonde_polices(args.natif, args.enfant), ensure_ascii=False))
        return 0
    natif = ["--natif"] if args.natif else []
    if args.sonde_polices:
        sonde = {"couverture": _enfant(["--enfant", "couverture", *natif]),
                 "attribution": _enfant(["--enfant", "attribution", *natif])}
        rendus = {}
        for mode in ("rendu:Segoe UI:latin", "rendu:Segoe UI|Inter|Noto Sans:latin", "rendu:Segoe UI:symboles",
                     "rendu:Segoe UI Symbol:symboles", "icone:Segoe UI Symbol:symboles",
                     "rendu:Segoe UI Semibold:latin", "rendu:Segoe UI Semibold:latin:26px",
                     "rendu:Segoe UI Semibold:latin:espacement", "rendu:Segoe UI:latin:gras",
                     "rendu:Segoe UI:latin:espacement", "rendu:Cascadia Mono:latin", "rendu:Consolas:latin"):
            r = _enfant(["--enfant", mode, *natif])
            rendus[mode] = [r.get("rendu_ms"), r.get("polices_chargees")] if "rendu_ms" in r else r
        sonde["rendus [ms, fichiers]"] = rendus
        print(json.dumps(sonde, ensure_ascii=False))
        if args.json:
            Path(args.json).write_text(json.dumps(sonde, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    essais = [_enfant(["--enfant", "ouverture", "--reouvertures", str(args.reouvertures), *natif])
              for _ in range(max(1, args.repetitions))]
    polices = [_enfant(["--enfant", "polices", *natif]) for _ in range(max(1, min(3, args.repetitions)))]
    valides = [e for e in essais if "premiere_ouverture_ms" in e]
    rapport = {
        "plateforme": f"{sys.platform} · Python {sys.version.split()[0]}",
        "cpu": os.cpu_count(),
        "seuil_ms": SEUIL_MS,
        "essais": essais,
        "polices_a_froid": polices,
    }
    if valides:
        premieres = [e["premiere_ouverture_ms"] for e in valides]
        reouv = [d for e in valides for d in e.get("reouvertures_ms", [])]
        rapport["synthese"] = {
            "premiere_ouverture_mediane_ms": round(statistics.median(premieres), 1),
            "premiere_ouverture_max_ms": max(premieres),
            "reouverture_mediane_ms": round(statistics.median(reouv), 1) if reouv else None,
            "phases_medianes_ms": {
                cle: round(statistics.median(e["phases_ms"][cle] for e in valides), 1)
                for cle in valides[0]["phases_ms"]
            },
        }
    if args.json:
        Path(args.json).write_text(json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8")
    synthese = rapport.get("synthese", {})
    print(f"Plateforme : {rapport['plateforme']} ({rapport['cpu']} CPU), Qt "
          f"{valides[0]['plateforme_qt'] if valides else '?'}")
    for index, essai in enumerate(essais, 1):
        if "premiere_ouverture_ms" in essai:
            print(f"  processus {index} : première ouverture {essai['premiere_ouverture_ms']} ms "
                  f"(contenu {essai['contenu_complet_ms']} ms) — phases {essai['phases_ms']} — "
                  f"réouvertures {essai['reouvertures_ms']}")
        else:
            print(f"  processus {index} : ERREUR {essai.get('erreur', '')[:300]}")
    for index, police in enumerate(polices, 1):
        print(f"  polices à froid {index} : {police}")
    if synthese:
        print(f"Synthèse : première ouverture médiane {synthese['premiere_ouverture_mediane_ms']} ms "
              f"(max {synthese['premiere_ouverture_max_ms']}, seuil {SEUIL_MS:.0f}), réouverture médiane "
              f"{synthese['reouverture_mediane_ms']} ms ; phases médianes {synthese['phases_medianes_ms']}")
    return 0 if valides else 1


if __name__ == "__main__":
    raise SystemExit(main())
