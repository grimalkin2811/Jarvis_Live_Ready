"""Fenêtre finale UNIQUE du protocole de validation (confirmations facultatives).

Regroupe en une seule fenêtre tout ce qui demande un humain :

* confirmation visuelle des captures du Dashboard réel (rendu Windows natif) ;
* notification Windows de test vue ou non ;
* test audio guidé : tonalité de 1 s sur le haut-parleur (« entendue ? »),
  puis niveau du micro pendant 3 s — AVERTISSEMENT affiché avant, capture en
  mémoire uniquement, rien n'est conservé ni envoyé.

Chaque question non répondue reste « non répondu » : le rapport la classe
« NON TESTÉ ». Fermeture automatique après 15 minutes sans interaction.

    python fenetre_finale.py --entree contexte.json --sortie reponses.json
    python fenetre_finale.py ... --auto   # sans interaction (tests)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ICI = Path(__file__).resolve().parent
NON_REPONDU = "non répondu"
INACTIVITE_MS = 15 * 60 * 1000


def lancer_sonde_audio(python: str, option: list[str]) -> dict:
    with tempfile.TemporaryDirectory(prefix="jarvis-validation-audio-") as tmp:
        target = Path(tmp) / "audio.json"
        try:
            subprocess.run([python, str(ICI / "sonde_audio.py"), "--json", str(target), *option],
                           timeout=30, capture_output=True, text=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return json.loads(target.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"erreur": f"{type(exc).__name__}: {exc}"[:300]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--entree", required=True)
    parser.add_argument("--sortie", required=True)
    parser.add_argument("--auto", action="store_true", help="aucune interaction : toutes les réponses « non répondu »")
    parser.add_argument("--fermer-apres", type=int, default=0, help=argparse.SUPPRESS)  # tests : fermeture en ms
    parser.add_argument("--capture", default="", help=argparse.SUPPRESS)  # tests : capture PNG avant fermeture
    args = parser.parse_args(argv)
    contexte = json.loads(Path(args.entree).read_text(encoding="utf-8"))
    reponses: dict = {
        "captures": NON_REPONDU,
        "notification_vue": NON_REPONDU if contexte.get("notification_emise") else "sans objet",
        "haut_parleur": {"teste": False, "entendu": NON_REPONDU},
        "micro": {"teste": False},
        "remarque": "",
        "ferme_par": "",
    }
    debut = time.time()

    def ecrire(ferme_par: str) -> None:
        reponses["ferme_par"] = ferme_par
        reponses["duree_s"] = round(time.time() - debut, 1)
        Path(args.sortie).write_text(json.dumps(reponses, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.auto:
        ecrire("auto")
        return 0

    from PySide6.QtCore import QEvent, QObject, Qt, QTimer
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import (QApplication, QButtonGroup, QDialog, QGridLayout, QGroupBox, QHBoxLayout,
                                   QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton, QScrollArea,
                                   QVBoxLayout, QWidget)

    class Vignette(QLabel):
        """Miniature cliquable : ouvre la capture en taille réelle."""

        def __init__(self, path: str) -> None:
            super().__init__()
            self.path = path
            self.setPixmap(QPixmap(path).scaledToWidth(300, Qt.SmoothTransformation))
            self.setToolTip(f"{Path(path).name} — cliquer pour agrandir")
            self.setCursor(Qt.PointingHandCursor)

        def mousePressEvent(self, event):  # noqa: N802
            dialog = QDialog(self.window())
            dialog.setWindowTitle(Path(self.path).name)
            dialog.resize(1280, 860)
            area = QScrollArea(dialog)
            image = QLabel()
            image.setPixmap(QPixmap(self.path))
            area.setWidget(image)
            box = QVBoxLayout(dialog)
            box.addWidget(area)
            dialog.exec()

    app = QApplication.instance() or QApplication(sys.argv)
    window = QWidget()
    window.setWindowTitle(f"Validation de la PR #{contexte.get('pr', '?')} — confirmations finales (facultatives)")
    window.resize(1100, 860)
    outer = QVBoxLayout(window)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    body = QWidget()
    layout = QVBoxLayout(body)
    scroll.setWidget(body)
    outer.addWidget(scroll)

    intro = QLabel(
        f"<b>Les contrôles automatiques sont terminés.</b> Verdict provisoire : <b>{contexte.get('verdict', '?')}</b><br>"
        "Les questions ci-dessous sont facultatives. Toute question sans réponse sera notée « NON TESTÉ » "
        "dans le rapport. Aucune donnée n'est envoyée ; aucun enregistrement n'est conservé.")
    intro.setWordWrap(True)
    layout.addWidget(intro)

    def question(titre: str, cle: str, choix: list[str]) -> QGroupBox:
        box = QGroupBox(titre)
        row = QHBoxLayout(box)
        group = QButtonGroup(box)
        for libelle in choix:
            radio = QRadioButton(libelle)
            group.addButton(radio)
            row.addWidget(radio)
            radio.toggled.connect(lambda checked, value=libelle: checked and reponses.__setitem__(cle, value))
        row.addStretch(1)
        return box

    captures = [c for c in contexte.get("captures", []) if Path(c).is_file()]
    if captures:
        box = QGroupBox(f"Captures du Dashboard réel ({len(captures)}) — rendu {contexte.get('rendu', '?')} — "
                        "cliquer sur une capture pour l'agrandir")
        grid = QGridLayout(box)
        for index, path in enumerate(captures[:24]):
            name = QLabel(Path(path).stem)
            cell = QVBoxLayout()
            cell.addWidget(Vignette(path))
            cell.addWidget(name)
            holder = QWidget()
            holder.setLayout(cell)
            grid.addWidget(holder, index // 3, index % 3)
        layout.addWidget(box)
        layout.addWidget(question("Ces captures sont-elles correctes (texte lisible, rien de coupé ni de superposé, "
                                  "thèmes appliqués) ?", "captures", ["oui", "non", "je ne sais pas"]))
    if contexte.get("notification_emise"):
        layout.addWidget(question("Pendant le test du Dashboard, avez-vous vu la notification Windows "
                                  "« Validation Jarvis » ?", "notification_vue", ["oui", "non", "pas regardé"]))

    audio = QGroupBox("Test audio guidé (facultatif)")
    audio_layout = QVBoxLayout(audio)
    python = contexte.get("python_audio") or sys.executable
    inventaire = contexte.get("audio") or {}
    def oui_non(value) -> str:
        return "oui" if value else "non"

    resume = QLabel(
        f"Entrée par défaut : {(inventaire.get('entree_defaut') or {}).get('type', 'aucune')} — utilisable par Jarvis : "
        f"{oui_non(inventaire.get('entree_utilisable'))} ; sortie par défaut : "
        f"{(inventaire.get('sortie_defaut') or {}).get('type', 'aucune')} — utilisable par Jarvis : "
        f"{oui_non(inventaire.get('sortie_utilisable'))}")
    resume.setWordWrap(True)
    audio_layout.addWidget(resume)
    avertissement = QLabel(
        "<b>Avertissement :</b> le test du micro capte le son pendant <b>3 secondes</b> après le clic, EN MÉMOIRE "
        "uniquement, pour mesurer le niveau sonore. Rien n'est enregistré sur le disque ni envoyé. "
        "Parlez normalement pendant le décompte.")
    avertissement.setWordWrap(True)
    audio_layout.addWidget(avertissement)
    speaker_button = QPushButton("Jouer une tonalité de 1 s sur le haut-parleur")
    mic_button = QPushButton("Tester le micro (3 s, rien n'est conservé)")
    status = QLabel("")
    status.setWordWrap(True)
    heard = question("Avez-vous entendu la tonalité ?", "_entendu", ["oui", "non"])
    heard.setEnabled(False)
    buttons = QHBoxLayout()
    buttons.addWidget(speaker_button)
    buttons.addWidget(mic_button)
    buttons.addStretch(1)
    audio_layout.addLayout(buttons)
    audio_layout.addWidget(heard)
    audio_layout.addWidget(status)
    enabled = bool(contexte.get("audio_autorise", True)) and not inventaire.get("erreur")
    speaker_button.setEnabled(enabled and bool(inventaire.get("sortie_utilisable")))
    mic_button.setEnabled(enabled and bool(inventaire.get("entree_utilisable")))
    if not enabled:
        status.setText("Test audio indisponible : " + (inventaire.get("erreur") or "non autorisé dans la fenêtre de lancement."))

    def tester_haut_parleur() -> None:
        status.setText("Lecture de la tonalité…")
        app.processEvents()
        result = lancer_sonde_audio(python, ["--test-haut-parleur"])
        reponses["haut_parleur"]["teste"] = not result.get("erreur")
        status.setText("Tonalité jouée." if not result.get("erreur") else f"Lecture impossible : {result['erreur']}")
        heard.setEnabled(not result.get("erreur"))

    def tester_micro() -> None:
        if QMessageBox.question(window, "Test du micro", "Le micro va capter 3 secondes EN MÉMOIRE (rien n'est "
                                "conservé). Continuer ?") != QMessageBox.Yes:
            return
        status.setText("Parlez maintenant… (3 s)")
        app.processEvents()
        result = lancer_sonde_audio(python, ["--test-micro", "3"])
        if result.get("erreur"):
            reponses["micro"] = {"teste": False, "erreur": result["erreur"]}
            status.setText(f"Capture impossible : {result['erreur']}")
            return
        reponses["micro"] = {"teste": True, **{k: result.get(k) for k in ("rms_dbfs", "crete", "signal_detecte", "conserve")}}
        status.setText(f"Niveau mesuré : {result.get('rms_dbfs')} dBFS — "
                       + ("voix détectée." if result.get("signal_detecte") else "signal trop faible (micro coupé ?)."))

    speaker_button.clicked.connect(tester_haut_parleur)
    mic_button.clicked.connect(tester_micro)
    layout.addWidget(audio)

    remark = QLineEdit()
    remark.setPlaceholderText("Remarque facultative (n'écrivez aucune information personnelle : elle figurera dans le rapport)")
    remark.setMaxLength(300)
    layout.addWidget(remark)
    layout.addStretch(1)
    finish = QPushButton("Terminer et écrire le rapport")
    finish.setDefault(True)
    outer.addWidget(finish)

    timer = QTimer(window)
    timer.setSingleShot(True)
    timer.setInterval(INACTIVITE_MS)
    state = {"ferme_par": "fenêtre fermée"}

    def terminer(ferme_par: str) -> None:
        state["ferme_par"] = ferme_par
        window.close()

    timer.timeout.connect(lambda: terminer("inactivité (15 min)"))
    timer.start()
    finish.clicked.connect(lambda: terminer("bouton Terminer"))

    class Activite(QObject):
        """Toute interaction relance le délai d'inactivité."""

        def eventFilter(self, obj, event):  # noqa: N802
            if event.type() in (QEvent.MouseButtonPress, QEvent.KeyPress):
                timer.start()
            return False

    activite = Activite(window)
    app.installEventFilter(activite)
    if args.fermer_apres:
        if args.capture:
            QTimer.singleShot(max(0, args.fermer_apres - 200), lambda: window.grab().save(args.capture))
        QTimer.singleShot(args.fermer_apres, lambda: terminer("fermeture automatique (test)"))
    window.show()
    window.raise_()
    window.activateWindow()
    app.exec()
    entendu = reponses.pop("_entendu", None)
    if entendu:
        reponses["haut_parleur"]["entendu"] = entendu
    reponses["remarque"] = remark.text().strip()
    ecrire(state["ferme_par"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
