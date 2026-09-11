"""Résolution et chargement robustes des modèles OpenWakeWord (« Hey Jarvis »).

Contexte du bug corrigé (release 1.1.1) :

* En développement, ``tflite_runtime`` est présent (dépendance d'openwakeword)
  et les modèles ``.tflite`` sont résolus depuis le dossier du paquet installé.
* Dans l'application packagée (PyInstaller), ``tflite_runtime`` n'est pas
  importable : openWakeWord bascule silencieusement vers ONNX. Le modèle de
  wake word ``hey_jarvis_v0.1.onnx`` était bien embarqué dans
  ``resources/openwakeword/``, mais les modèles de pré-traitement
  (``melspectrogram.onnx`` et ``embedding_model.onnx``) sont résolus par
  openWakeWord UNIQUEMENT depuis ``openwakeword/resources/models/`` (dossier
  du paquet = ``_internal/openwakeword/resources/models/``), qui était VIDE
  dans le bundle. D'où l'erreur::

      [ONNXRuntimeError] NO_SUCHFILE : Load model ... melspectrogram.onnx

  Ni le re-téléchargement au premier lancement ni les tentatives ``tflite`` /
  ``onnx`` ne pouvaient réparer cela, car les chemins des modèles de
  pré-traitement n'étaient jamais fournis explicitement.
* Second bug latent : en chargeant le modèle par chemin explicite, la clé de
  prédiction est le nom du fichier (``hey_jarvis_v0.1``) et non
  ``hey_jarvis`` ; la recherche de score codée en dur ne correspondait donc
  jamais et la détection restait silencieusement inactive.

Stratégie retenue (ONNX d'abord) :

* ``onnxruntime`` est une dépendance directe, disponible sur Windows avec des
  roues officielles et simple à embarquer avec PyInstaller. C'est le framework
  supporté pour les builds distribués.
* Ce module résout les TROIS fichiers ``.onnx`` nécessaires depuis tous les
  emplacements possibles (bundle, dossier utilisateur, paquet installé,
  dépôt en développement) et les passe EXPLICITEMENT à openWakeWord
  (``wakeword_models``, ``melspec_model_path``, ``embedding_model_path``),
  sans jamais dépendre des chemins par défaut du paquet.
* ``tflite`` n'est tenté qu'en mode opportuniste (runtime importable ET
  fichiers ``.tflite`` résolus).
* La clé de score est détectée dynamiquement depuis le modèle chargé.

Le module n'importe ``openwakeword``/``onnxruntime`` qu'à l'intérieur des
fonctions (imports paresseux) afin de rester importable partout, y compris
dans le smoke test et les scripts de validation.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from . import paths

#: Nom canonique du wake word.
WAKEWORD_NAME = "hey_jarvis"

#: Fichiers ONNX requis pour une détection fonctionnelle.
REQUIRED_ONNX_MODELS: tuple[str, ...] = (
    "melspectrogram.onnx",
    "embedding_model.onnx",
    "hey_jarvis_v0.1.onnx",
)

#: Équivalents TFLite (mode opportuniste uniquement, jamais requis).
REQUIRED_TFLITE_MODELS: tuple[str, ...] = (
    "melspectrogram.tflite",
    "embedding_model.tflite",
    "hey_jarvis_v0.1.tflite",
)

#: Variable d'environnement pour désactiver tout téléchargement de modèles
#: (tests automatisés, environnements hors ligne). Les tests unitaires
#: l'activent via ``tests/__init__.py``.
NO_DOWNLOAD_ENV_VAR = "JARVIS_NO_MODEL_DOWNLOAD"


# ---------------------------------------------------------------------------
# Disponibilité des frameworks
# ---------------------------------------------------------------------------

def openwakeword_available() -> bool:
    """Vrai si le paquet ``openwakeword`` est importable."""
    try:
        import openwakeword  # noqa: F401
    except Exception:
        return False
    return True


def onnxruntime_available() -> bool:
    """Vrai si ``onnxruntime`` est importable."""
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return True


def tflite_available() -> bool:
    """Vrai si le runtime TFLite est importable (mode opportuniste)."""
    try:
        import tflite_runtime.interpreter  # noqa: F401
    except Exception:
        return False
    return True


def download_allowed(explicit: bool | None = None) -> bool:
    """Détermine si un téléchargement de modèles est autorisé.

    * ``explicit is True/False`` : forçage direct (les tests utilisent
      ``True`` avec des mocks, ``False`` pour simuler le hors-ligne).
    * ``None`` (défaut applicatif) : autorisé sauf si la variable
      d'environnement ``JARVIS_NO_MODEL_DOWNLOAD`` est positionnée à une
      valeur non vide différente de ``0``.
    """
    if explicit is not None:
        return bool(explicit)
    return os.environ.get(NO_DOWNLOAD_ENV_VAR, "").strip().lower() not in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Emplacements candidats
# ---------------------------------------------------------------------------

def candidate_dirs() -> list[Path]:
    """Répertoires où chercher les modèles, du plus spécifique au général.

    Pour une application distribuée, il ne faut PAS supposer que
    ``.venv/Lib/site-packages`` existe sur la machine de l'utilisateur.
    """
    dirs: list[Path] = []
    # 1. Dossier utilisateur (modèles téléchargés au premier lancement,
    #    préservés lors des mises à jour).
    dirs.append(paths.openwakeword_models_dir())
    # 2. Ressources embarquées dans le bundle PyInstaller (_MEIPASS).
    if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None):
        meipass = Path(str(sys._MEIPASS))  # type: ignore[attr-defined]
        # a. Emplacement d'origine du paquet openwakeword : le build y copie
        #    les .onnx (voir packaging/jarvis.spec) pour que même la
        #    résolution par défaut d'openWakeWord fonctionne.
        dirs.append(meipass / "openwakeword" / "resources" / "models")
        # b. Dossier de ressources fourni par le build.
        dirs.append(meipass / "resources" / "openwakeword")
        dirs.append(meipass / "resources" / "models")
    else:
        # 3. En développement : dossier resources/ du dépôt (rempli par
        #    scripts/download_models.py).
        repo_resources = paths.app_dir() / "resources" / "openwakeword"
        dirs.append(repo_resources)
        dirs.append(paths.app_dir() / "resources" / "models")
    # 4. Dossier de ressources du paquet installé (site-packages en dev).
    try:
        import openwakeword

        pkg_models = (
            Path(os.path.abspath(openwakeword.__file__)).parent / "resources" / "models"
        )
        dirs.append(pkg_models)
    except Exception:
        pass
    # Déduplique en préservant l'ordre.
    seen: set[str] = set()
    unique: list[Path] = []
    for directory in dirs:
        key = os.path.normcase(str(directory))
        if key not in seen:
            seen.add(key)
            unique.append(directory)
    return unique


def find_model_file(filename: str, *, extra_dirs: list[Path] | None = None) -> Path | None:
    """Retourne le chemin absolu de ``filename`` s'il existe dans les candidats."""
    search = list(extra_dirs or []) + candidate_dirs()
    for directory in search:
        try:
            candidate = directory / filename
        except Exception:
            continue
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def find_wakeword_file(extension: str, *, extra_dirs: list[Path] | None = None) -> Path | None:
    """Retrouve le modèle de wake word ``hey_jarvis*`` pour une extension.

    Gère les évolutions de nom de version (``hey_jarvis_v0.1.onnx``,
    ``hey_jarvis_v0.2.onnx``, …) : correspondance exacte d'abord, puis
    recherche libre par préfixe.
    """
    search = list(extra_dirs or []) + candidate_dirs()
    # 1. Nom exact attendu par openwakeword.MODELS si disponible.
    expected: str | None = None
    try:
        import openwakeword

        info = openwakeword.MODELS.get(WAKEWORD_NAME)
        if info:
            expected = Path(str(info.get("model_path", ""))).name
            if expected and not expected.endswith(extension):
                expected = None
    except Exception:
        expected = None
    names = [expected] if expected else []
    names.append(f"{WAKEWORD_NAME}_v0.1{extension}")
    for directory in search:
        for name in names:
            if not name:
                continue
            try:
                candidate = directory / name
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
        # 2. Recherche libre : tout fichier hey_jarvis*.<ext>.
        try:
            entries = sorted(os.listdir(directory))
        except OSError:
            continue
        for entry in entries:
            lower = entry.lower()
            if WAKEWORD_NAME in lower and lower.endswith(extension.lower()):
                return directory / entry
    return None


# ---------------------------------------------------------------------------
# Résolution complète
# ---------------------------------------------------------------------------

def resolve_onnx_models(*, extra_dirs: list[Path] | None = None) -> dict[str, Path | None]:
    """Résout les trois modèles ONNX requis.

    Retourne ``{"melspectrogram": Path|None, "embedding": ..., "wakeword": ...}``.
    Un chemin vaut ``None`` quand le fichier est introuvable.
    """
    return {
        "melspectrogram": find_model_file("melspectrogram.onnx", extra_dirs=extra_dirs),
        "embedding": find_model_file("embedding_model.onnx", extra_dirs=extra_dirs),
        "wakeword": find_wakeword_file(".onnx", extra_dirs=extra_dirs),
    }


def resolve_tflite_models(*, extra_dirs: list[Path] | None = None) -> dict[str, Path | None]:
    """Résout les trois modèles TFLite (mode opportuniste uniquement)."""
    return {
        "melspectrogram": find_model_file("melspectrogram.tflite", extra_dirs=extra_dirs),
        "embedding": find_model_file("embedding_model.tflite", extra_dirs=extra_dirs),
        "wakeword": find_wakeword_file(".tflite", extra_dirs=extra_dirs),
    }


def models_status(*, extra_dirs: list[Path] | None = None) -> dict[str, Any]:
    """État lisible de la résolution (pour logs, diagnostics, smoke test)."""
    onnx = resolve_onnx_models(extra_dirs=extra_dirs)
    tflite = resolve_tflite_models(extra_dirs=extra_dirs)
    return {
        "openwakeword_importable": openwakeword_available(),
        "onnxruntime_importable": onnxruntime_available(),
        "tflite_importable": tflite_available(),
        "onnx": {key: (str(path) if path else None) for key, path in onnx.items()},
        "onnx_complete": all(onnx.values()),
        "tflite": {key: (str(path) if path else None) for key, path in tflite.items()},
        "tflite_complete": all(tflite.values()),
        "candidate_dirs": [str(d) for d in candidate_dirs()],
    }


def format_status(status: dict[str, Any]) -> str:
    """Formate ``models_status()`` pour les logs (100 % ASCII, console sûre)."""
    lines = ["[Wake Word] Etat des modeles :"]
    lines.append(
        "  openwakeword=%s onnxruntime=%s tflite=%s"
        % (
            "OK" if status.get("openwakeword_importable") else "MISSING",
            "OK" if status.get("onnxruntime_importable") else "MISSING",
            "OK" if status.get("tflite_importable") else "absent (optionnel)",
        )
    )
    for key in ("melspectrogram", "embedding", "wakeword"):
        found = (status.get("onnx") or {}).get(key)
        lines.append(f"  onnx/{key}: {found if found else 'INTROUVABLE'}")
    lines.append("  onnx_complet=%s" % ("oui" if status.get("onnx_complete") else "non"))
    text = "\n".join(lines)
    return text.encode("ascii", errors="backslashreplace").decode("ascii")


# ---------------------------------------------------------------------------
# Téléchargement
# ---------------------------------------------------------------------------

def download_models_to_user_dir() -> bool:
    """Télécharge les modèles (dont les .onnx) vers le dossier utilisateur.

    ``openwakeword.utils.download_models`` télécharge systématiquement les
    variantes ``.tflite`` ET ``.onnx`` des modèles demandés ainsi que des
    modèles de pré-traitement (melspectrogram, embedding). Retourne Vrai si,
    après l'opération, les trois ONNX sont résolus.
    """
    try:
        from openwakeword.utils import download_models
    except Exception as exc:
        print(f"[Wake Word] Telechargement impossible (openwakeword absent) : {exc}")
        return False
    target = paths.openwakeword_models_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
        print(f"[Wake Word] Telechargement des modeles vers {target} ...")
        download_models([WAKEWORD_NAME], target_directory=str(target))
    except Exception as exc:
        print(f"[Wake Word] Telechargement du modele impossible : {exc}")
        return False
    resolved = resolve_onnx_models()
    return all(resolved.values())


def ensure_onnx_models(*, download: bool | None = None) -> dict[str, Path | None]:
    """Garantit (si possible) la présence des trois ONNX et les retourne.

    Si des fichiers manquent et que le téléchargement est autorisé, un
    téléchargement vers le dossier utilisateur est tenté une fois.
    """
    resolved = resolve_onnx_models()
    if all(resolved.values()):
        return resolved
    if download_allowed(download):
        download_models_to_user_dir()
        resolved = resolve_onnx_models()
    return resolved


# ---------------------------------------------------------------------------
# Création du modèle openWakeWord
# ---------------------------------------------------------------------------

def detect_score_key(model: Any, *, preferred: str = WAKEWORD_NAME) -> str:
    """Détecte la clé de score à lire dans ``model.predict(...)``.

    openWakeWord nomme les prédictions d'après le nom fourni (chargement par
    nom : ``hey_jarvis``) ou d'après le nom du fichier (chargement par chemin :
    ``hey_jarvis_v0.1``). On préfère la correspondance exacte, puis toute clé
    contenant ``hey_jarvis``, puis la première clé disponible.
    """
    try:
        keys = list(getattr(model, "models", {}).keys())
    except Exception:
        keys = []
    if not keys:
        return preferred
    if preferred in keys:
        return preferred
    for key in keys:
        if preferred in str(key):
            return str(key)
    return str(keys[0])


def create_model(
    *,
    framework: str = "onnx",
    download: bool | None = None,
    extra_dirs: list[Path] | None = None,
    verbose: bool = True,
) -> tuple[Any, str] | tuple[None, str]:
    """Crée le modèle openWakeWord avec chemins explicites.

    Args:
        framework: ``"onnx"`` (recommandé, défaut) ou ``"tflite"``
            (opportuniste). Toute autre valeur lève ``ValueError``.
        download: autorise un téléchargement si des fichiers manquent
            (``None`` = suit ``JARVIS_NO_MODEL_DOWNLOAD``).
        extra_dirs: répertoires prioritaires supplémentaires (tests).
        verbose: affiche la progression sur stdout.

    Retourne ``(model, score_key)`` ou ``(None, "")`` si le chargement est
    impossible (openwakeword absent, fichiers manquants, runtime absent…).
    Ne lève jamais pour les cas fonctionnels : les appelants (audio, smoke
    test) gèrent le mode dégradé.
    """
    if framework not in ("onnx", "tflite"):
        raise ValueError(f"Framework inconnu : {framework!r} (attendu 'onnx' ou 'tflite')")

    def log(message: str) -> None:
        if verbose:
            print(message)

    if not openwakeword_available():
        log("[Wake Word] openwakeword indisponible, detection desactivee.")
        return None, ""

    if framework == "onnx":
        if not onnxruntime_available():
            log("[Wake Word] onnxruntime indisponible, detection desactivee.")
            return None, ""
        resolved = resolve_onnx_models(extra_dirs=extra_dirs)
        missing = sorted(key for key, path in resolved.items() if path is None)
        if missing and download_allowed(download):
            log(f"[Wake Word] Modeles manquants ({', '.join(missing)}) : telechargement ...")
            download_models_to_user_dir()
            resolved = resolve_onnx_models(extra_dirs=extra_dirs)
            missing = sorted(key for key, path in resolved.items() if path is None)
        if missing:
            log(f"[Wake Word] Modeles ONNX introuvables : {', '.join(missing)}.")
            log(format_status(models_status(extra_dirs=extra_dirs)))
            return None, ""
        assert resolved["melspectrogram"] and resolved["embedding"] and resolved["wakeword"]
        try:
            from openwakeword.model import Model

            model = Model(
                wakeword_models=[str(resolved["wakeword"])],
                inference_framework="onnx",
                melspec_model_path=str(resolved["melspectrogram"]),
                embedding_model_path=str(resolved["embedding"]),
            )
        except Exception as exc:
            log(f"[Wake Word] Chargement (onnx) impossible : {exc}")
            return None, ""
        key = detect_score_key(model)
        log(f"[Wake Word] Modele charge (onnx, cle={key}).")
        return model, key

    # --- TFLite opportuniste : runtime ET fichiers requis, sinon abandon. ---
    if not tflite_available():
        log("[Wake Word] tflite indisponible (runtime absent).")
        return None, ""
    resolved = resolve_tflite_models(extra_dirs=extra_dirs)
    missing = sorted(key for key, path in resolved.items() if path is None)
    if missing:
        log(f"[Wake Word] Modeles TFLite introuvables : {', '.join(missing)}.")
        return None, ""
    assert resolved["melspectrogram"] and resolved["embedding"] and resolved["wakeword"]
    try:
        from openwakeword.model import Model

        model = Model(
            wakeword_models=[str(resolved["wakeword"])],
            inference_framework="tflite",
            melspec_model_path=str(resolved["melspectrogram"]),
            embedding_model_path=str(resolved["embedding"]),
        )
    except Exception as exc:
        log(f"[Wake Word] Chargement (tflite) impossible : {exc}")
        return None, ""
    key = detect_score_key(model)
    log(f"[Wake Word] Modele charge (tflite, cle={key}).")
    return model, key


def load_best_model(
    *,
    download: bool | None = None,
    extra_dirs: list[Path] | None = None,
    verbose: bool = True,
) -> tuple[Any, str, str]:
    """Charge le meilleur modèle disponible : ONNX d'abord, TFLite sinon.

    Retourne ``(model, score_key, framework)`` ; ``(None, "", "")`` si aucun
    chargement n'est possible.
    """
    model, key = create_model(
        framework="onnx", download=download, extra_dirs=extra_dirs, verbose=verbose
    )
    if model is not None:
        return model, key, "onnx"
    model, key = create_model(
        framework="tflite", download=download, extra_dirs=extra_dirs, verbose=verbose
    )
    if model is not None:
        return model, key, "tflite"
    if verbose:
        print("[Wake Word] Aucun modele wake-word disponible.")
    return None, "", ""


# ---------------------------------------------------------------------------
# Vérification de bout en bout (smoke test, CI)
# ---------------------------------------------------------------------------

def smoke_check(
    *,
    download: bool = False,
    extra_dirs: list[Path] | None = None,
    verbose: bool = True,
) -> tuple[bool, str]:
    """Valide toute la chaîne : fichiers -> sessions ONNX -> prédiction.

    Instancie le vrai modèle openWakeWord et exécute plusieurs prédictions
    sur de l'audio synthétique (16 kHz int16), sans aucun matériel audio.
    C'est le test qui aurait détecté le bug 1.1.0 en CI (fichiers présents
    au mauvais endroit + sessions non chargeables).

    Retourne ``(ok, message)``. Ne lève jamais.
    """
    def log(message: str) -> None:
        if verbose:
            print(message)

    status = models_status(extra_dirs=extra_dirs)
    log(format_status(status))
    if not status["openwakeword_importable"]:
        return False, "openwakeword non importable"
    if not status["onnx_complete"] and not download_allowed(download):
        return False, "modeles ONNX incomplets (telechargement desactive)"
    model, key, framework = load_best_model(
        download=download, extra_dirs=extra_dirs, verbose=verbose
    )
    if model is None:
        return False, "chargement du modele impossible"
    try:
        import numpy as np
    except Exception as exc:
        return False, f"numpy indisponible : {exc}"
    try:
        rng = np.random.default_rng(1234)
        audio = (rng.uniform(-1.0, 1.0, 1280) * 8000).astype(np.int16)
        scores: dict = {}
        for _ in range(8):
            scores = model.predict(audio)
        if not isinstance(scores, dict) or key not in scores:
            return False, f"cle de score {key!r} absente : {sorted(scores)}"
        value = float(scores[key])
        if not (0.0 <= value <= 1.0):
            return False, f"score hors bornes : {value!r}"
        try:
            model.reset()
        except Exception:
            pass
    except Exception as exc:
        return False, f"prediction impossible : {exc}"
    message = f"wake word OK (framework={framework}, cle={key}, score_test={value:.3f})"
    log(f"[Wake Word] {message}")
    return True, message
