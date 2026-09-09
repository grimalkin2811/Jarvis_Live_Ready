"""Assistant de premier lancement de Jarvis.

Objectif : à la toute première exécution (aucune ``config.json``), Jarvis guide
l'utilisateur pour saisir son **nom** et sa **clé Gemini API**, puis enregistre
la configuration dans ``%LOCALAPPDATA%\\Jarvis\\config\\config.json``. Aucune clé
n'est jamais codée en dur ni committée.

Deux parcours :

* ``run_cli_wizard`` — assistant en ligne de commande (mode console / launcher).
* ``run_qt_wizard``  — boîte de dialogue PySide6 (mode orbe / bureau).

Les deux produisent un ``settings.AppConfig`` et l'écrivent sur disque.
"""

from __future__ import annotations

import getpass
from pathlib import Path

from . import settings

DEFAULT_MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"


def _ask(prompt: str, default: str | None = None, secret: bool = False) -> str:
    if secret:
        try:
            value = getpass.getpass(prompt)
        except (EOFError, KeyboardInterrupt):
            print()
            return ""
    else:
        try:
            value = input(prompt)
        except (EOFError, KeyboardInterrupt):
            print()
            return ""
    value = value.strip()
    if not value and default is not None:
        return default
    return value


def run_cli_wizard(path: str | Path | None = None, *, confirm: bool = True) -> settings.AppConfig | None:
    """Assistant de premier lancement en ligne de commande.

    Retourne ``None`` si l'utilisateur annule.
    """
    print()
    print("=== Bienvenue dans Jarvis ===")
    print("Ceci est votre première configuration.")
    print("Vous n'avez besoin que de deux informations : votre prénom et votre")
    print("clé API Gemini (https://aistudio.google.com/app/apikey).")
    print()

    user = _ask("Votre prénom : ")
    if not user:
        print("Configuration annulée.")
        return None

    api_key = _ask("Votre clé Gemini API : ", secret=True)
    api_key = settings.validate_api_key(api_key)
    if not api_key:
        print("Clé API vide : configuration annulée.")
        return None

    model = _ask(
        f"Modèle Gemini (Entrée pour défaut) [{DEFAULT_MODEL}] : ",
        default=DEFAULT_MODEL,
    ) or DEFAULT_MODEL

    if confirm:
        print()
        print(f"  Prénom : {user}")
        print(f"  Clé    : {'•' * max(4, min(len(api_key), 12))}")
        print(f"  Modèle : {model}")
        ok = _ask("Confirmer ? (o/N) : ", default="n")
        if ok.strip().lower() not in {"o", "oui", "y", "yes", "1"}:
            print("Configuration annulée.")
            return None

    cfg = settings.AppConfig(user=user, api_key=api_key, model=model)
    target = settings.save_app_config(cfg, path)
    print()
    print(f"Configuration enregistrée dans : {target}")
    print("Jarvis va démarrer. Vous pourrez modifier ces réglages plus tard "
          "visuellement (menus radiaux / paramètres).")
    return cfg


def run_qt_wizard(parent=None, path: str | Path | None = None) -> settings.AppConfig | None:
    """Assistant de premier lancement sous forme de boîte de dialogue Qt.

    Retourne ``None`` si l'utilisateur ferme la boîte sans valider.
    """
    try:
        from PySide6.QtWidgets import (
            QDialog,
            QFormLayout,
            QLabel,
            QLineEdit,
            QMessageBox,
            QPushButton,
            QVBoxLayout,
        )
    except Exception:
        # PySide6 indisponible : repli console.
        return run_cli_wizard(path=path)

    dialog = QDialog(parent)
    dialog.setWindowTitle("Jarvis — première configuration")
    dialog.setModal(True)
    dialog.resize(460, 240)

    layout = QVBoxLayout(dialog)
    intro = QLabel(
        "<b>Bienvenue dans Jarvis.</b><br><br>"
        "Pour fonctionner, Jarvis a besoin de deux informations :<br>"
        "• votre prénom,<br>"
        "• votre clé d'API Gemini "
        "(<a href='https://aistudio.google.com/app/apikey'>aistudio.google.com</a>).<br><br>"
        "Aucune clé n'est stockée ailleurs que sur ce PC."
    )
    intro.setWordWrap(True)
    intro.setOpenExternalLinks(True)
    layout.addWidget(intro)

    form = QFormLayout()
    user_edit = QLineEdit()
    user_edit.setPlaceholderText("ex. Marius")
    api_edit = QLineEdit()
    api_edit.setEchoMode(QLineEdit.Password)
    api_edit.setPlaceholderText("AIza...")
    model_edit = QLineEdit(DEFAULT_MODEL)
    form.addRow("Votre prénom", user_edit)
    form.addRow("Clé Gemini API", api_edit)
    form.addRow("Modèle", model_edit)
    layout.addLayout(form)

    buttons = QVBoxLayout()
    ok_btn = QPushButton("Configurer et démarrer")
    ok_btn.setDefault(True)
    cancel_btn = QPushButton("Annuler")
    buttons.addWidget(ok_btn)
    buttons.addWidget(cancel_btn)
    layout.addLayout(buttons)

    def _on_ok() -> None:
        user = user_edit.text().strip()
        api_key = settings.validate_api_key(api_edit.text())
        if not user:
            QMessageBox.warning(dialog, "Champ manquant", "Indiquez votre prénom.")
            return
        if not api_key:
            QMessageBox.warning(dialog, "Champ manquant", "Indiquez votre clé Gemini API.")
            return
        model = model_edit.text().strip() or DEFAULT_MODEL
        cfg = settings.AppConfig(user=user, api_key=api_key, model=model)
        settings.save_app_config(cfg, path)
        dialog.accept()
        dialog._app_config = cfg  # type: ignore[attr-defined]

    ok_btn.clicked.connect(_on_ok)
    cancel_btn.clicked.connect(dialog.reject)

    result = dialog.exec()
    if result == QDialog.Accepted and getattr(dialog, "_app_config", None) is not None:
        return dialog._app_config  # type: ignore[attr-defined]
    return None


def run_wizard(path: str | Path | None = None, *, gui: bool = False) -> settings.AppConfig | None:
    """Point d'entrée unique : choisit l'assistant Qt ou console.

    ``gui=True`` tente l'assistant Qt (à défaut retombe sur le console).
    """
    if gui:
        cfg = run_qt_wizard(path=path)
        if cfg is not None:
            return cfg
        # L'utilisateur peut avoir annulé la boîte Qt : on ne le relance pas
        # en console afin de ne pas dupliquer la demande.
        return None
    return run_cli_wizard(path=path)
