"""Orchestration du système d'écriture.

Vérifie l'intention, les interrupteurs et le texte, puis délègue l'insertion
ou la création de fichier. Une erreur est toujours un dictionnaire : Jarvis
revient à son fonctionnement normal.
"""

from __future__ import annotations

from . import settings as writing_settings
from .active_field import ActiveFieldInserter, platform_inserter
from .files import create_text_file as write_txt_file
from .filenames import derive_filename
from .intent import (
    BOTH,
    CONVERSATION,
    CREATE_TEXT_FILE,
    WRITE_ACTIVE_FIELD,
    classify_writing_intent,
)
from .text import MAX_TEXT_CHARS, finalize_text

SPOKEN_WRITTEN = "C'est écrit."
SPOKEN_FILE_CREATED = "Le fichier a été créé dans user_content."
SPOKEN_WRITE_FAILED = "Je n'ai pas réussi à écrire le texte."
SPOKEN_FILE_FAILED = "Je n'ai pas réussi à créer le fichier."
SPOKEN_FIELD_DISABLED = "L'écriture dans le champ actif est désactivée."
SPOKEN_FILES_DISABLED = "La création de fichiers texte est désactivée."
SPOKEN_NOT_EXPLICIT = "Cette demande est une question : je réponds sans rien écrire."

CONSIGNE = "Confirme uniquement avec le champ message. Ne lis pas le texte produit."

WRITING_TOOL_NAMES = frozenset({"write_to_active_field", "create_text_file"})

_writer_override: ActiveFieldInserter | None = None


def system_instruction() -> str:
    """Consigne ajoutée au prompt Gemini Live. Ne remplace pas le routage local."""
    return (
        "Système d'écriture, deux modes indépendants, jamais spontanés. "
        "Appelle write_to_active_field uniquement si l'utilisateur ordonne clairement d'écrire, "
        "rédiger, composer, taper ou insérer un texte à l'endroit du curseur "
        "(« écris-moi un mail », « rédige », « compose »). "
        "Appelle create_text_file uniquement s'il ordonne de créer un fichier, de sauvegarder un texte, "
        "ou dit « crée-moi un texte ». "
        "Une question ou une hypothèse (« qu'est-ce que tu écrirais », « comment rédiger », "
        "« explique-moi », « que mettrais-tu ») reste une réponse vocale normale : "
        "n'appelle aucun outil d'écriture. "
        "Ne remplace jamais une réponse conversationnelle par une écriture, "
        "et n'écris pas dans l'application active sans ordre explicite. "
        "Passe le texte final complet dans text, sans préambule ni guillemets. "
        "Si les deux actions sont demandées, appelle les deux outils. "
        "Si le mode demandé est désactivé, annonce-le : n'utilise pas l'autre mode à la place. "
        "Après un outil d'écriture, dis uniquement son champ message "
        "(succès : « C'est écrit. » ou « Le fichier a été créé dans user_content. ») "
        "et ne lis jamais le texte produit. Si success est faux, annonce le champ message, "
        "sans inventer un succès."
    )


def set_active_field_writer(writer: ActiveFieldInserter | None) -> None:
    """Injecte un inserteur (tests). ``None`` rétablit l'implémentation plateforme."""
    global _writer_override
    _writer_override = writer


def write_to_active_field(text, request: str = "", *, writer=None, enabled=None) -> dict:
    """Insère ``text`` au curseur si le mode est actif et la demande explicite."""
    try:
        veto = _veto(request, WRITE_ACTIVE_FIELD)
        if veto is not None:
            return veto
        if enabled is None:
            enabled = writing_settings.active_field_enabled()
        if not enabled:
            return _fail(SPOKEN_FIELD_DISABLED, "disabled", SPOKEN_FIELD_DISABLED)
        final = finalize_text(text)
        if not final:
            return _fail(SPOKEN_WRITE_FAILED, "empty", "Le texte à écrire est vide.")
        if len(final) > MAX_TEXT_CHARS:
            return _fail(SPOKEN_WRITE_FAILED, "too_long", "Texte trop long pour être inséré.")
        inserter = writer if writer is not None else (_writer_override or platform_inserter())
        outcome = inserter.write(final)
        if not outcome.get("success"):
            message = _field_failure_message(outcome)
            print(f"[writing] insertion refusée ({outcome.get('reason')})")
            return _fail(message, str(outcome.get("reason") or "injection_failed"), str(outcome.get("error") or message), **{
                key: outcome[key]
                for key in ("method", "clipboard_used", "clipboard_restored", "partial")
                if key in outcome
            })
        message = SPOKEN_WRITTEN
        if outcome.get("clipboard_used") and outcome.get("clipboard_restored") is False:
            message = "C'est écrit. Je n'ai pas pu restaurer le presse-papiers."
        print(f"[writing] insertion method={outcome.get('method')} chars={len(final)}")
        return _ok(
            message,
            action=WRITE_ACTIVE_FIELD,
            method=outcome.get("method", ""),
            caracteres=len(final),
            clipboard_used=bool(outcome.get("clipboard_used")),
            clipboard_restored=bool(outcome.get("clipboard_restored", True)),
        )
    except Exception as exc:
        print(f"[writing] insertion en erreur : {exc}")
        return _fail(SPOKEN_WRITE_FAILED, "exception", str(exc))


def create_text_file(
    text,
    filename: str | None = None,
    request: str = "",
    *,
    directory=None,
    enabled=None,
) -> dict:
    """Crée un ``.txt`` dans ``user_content`` si le mode est actif."""
    try:
        veto = _veto(request, CREATE_TEXT_FILE)
        if veto is not None:
            return veto
        if enabled is None:
            enabled = writing_settings.text_files_enabled()
        if not enabled:
            return _fail(SPOKEN_FILES_DISABLED, "disabled", SPOKEN_FILES_DISABLED)
        final = finalize_text(text)
        if not final:
            return _fail(SPOKEN_FILE_FAILED, "empty", "Le texte à enregistrer est vide.")
        if len(final) > MAX_TEXT_CHARS:
            return _fail(SPOKEN_FILE_FAILED, "too_long", "Texte trop long pour un fichier.")
        chosen = str(filename).strip() if filename else ""
        derived = None if chosen else derive_filename(request, final)
        outcome = write_txt_file(
            final,
            filename=chosen or None,
            directory=directory,
            derived_name=derived,
        )
        if not outcome.get("success"):
            print(f"[writing] fichier refusé ({outcome.get('reason')})")
            return _fail(
                SPOKEN_FILE_FAILED,
                str(outcome.get("reason") or "io_error"),
                str(outcome.get("error") or SPOKEN_FILE_FAILED),
            )
        print(f"[writing] fichier={outcome.get('fichier')} chars={outcome.get('caracteres')}")
        return _ok(
            SPOKEN_FILE_CREATED,
            action=CREATE_TEXT_FILE,
            fichier=outcome.get("fichier"),
            dossier=outcome.get("dossier", "user_content"),
            chemin=outcome.get("chemin"),
            chemin_relatif=outcome.get("chemin_relatif"),
            caracteres=outcome.get("caracteres", len(final)),
        )
    except Exception as exc:
        print(f"[writing] fichier en erreur : {exc}")
        return _fail(SPOKEN_FILE_FAILED, "exception", str(exc))


def handle_command(
    utterance: str,
    text,
    *,
    filename: str | None = None,
    writer=None,
    directory=None,
    field_enabled=None,
    files_enabled=None,
) -> dict:
    """Route une commande textuelle. Une conversation n'écrit rien."""
    intent = classify_writing_intent(utterance)
    if not intent.is_action:
        return {
            "success": True,
            "handled": False,
            "action": CONVERSATION,
            "reason": intent.reason,
            "message": "",
            "consigne": CONSIGNE,
        }
    if intent.action == WRITE_ACTIVE_FIELD:
        result = write_to_active_field(
            text, request=utterance, writer=writer, enabled=field_enabled
        )
        result["handled"] = True
        result["action"] = WRITE_ACTIVE_FIELD
        return result
    if intent.action == CREATE_TEXT_FILE:
        result = create_text_file(
            text,
            filename=filename,
            request=utterance,
            directory=directory,
            enabled=files_enabled,
        )
        result["handled"] = True
        result["action"] = CREATE_TEXT_FILE
        return result
    field = write_to_active_field(text, request=utterance, writer=writer, enabled=field_enabled)
    created = create_text_file(
        text,
        filename=filename,
        request=utterance,
        directory=directory,
        enabled=files_enabled,
    )
    # Les deux appels reçoivent une demande « both » : le veto ne les bloque pas.
    parts = []
    if field.get("success"):
        parts.append(field.get("message") or SPOKEN_WRITTEN)
    else:
        parts.append(field.get("message") or SPOKEN_WRITE_FAILED)
    if created.get("success"):
        parts.append(created.get("message") or SPOKEN_FILE_CREATED)
    else:
        parts.append(created.get("message") or SPOKEN_FILE_FAILED)
    return {
        "success": bool(field.get("success") and created.get("success")),
        "handled": True,
        "action": BOTH,
        "message": " ".join(parts),
        "consigne": CONSIGNE,
        "field": field,
        "file": created,
    }


def _veto(request: str, expected: str) -> dict | None:
    """Bloque seulement une demande clairement non actionnable.

    Un appel d'outil sans transcription, ou une demande ambiguë, n'est pas
    refusé : le choix de l'outil est déjà une action explicite. En revanche
    « qu'est-ce que tu écrirais » ne doit rien écrire, même si le modèle appelle
    l'outil par erreur.
    """
    if not str(request or "").strip():
        return None
    intent = classify_writing_intent(request)
    if not intent.explicit_non_action:
        return None
    if expected == WRITE_ACTIVE_FIELD:
        return _fail(SPOKEN_NOT_EXPLICIT, "not_explicit", intent.reason, action=CONVERSATION)
    return _fail(SPOKEN_NOT_EXPLICIT, "not_explicit", intent.reason, action=CONVERSATION)


def _field_failure_message(outcome: dict) -> str:
    reason = str(outcome.get("reason") or "")
    if reason == "own_process":
        return "Je n'ai pas réussi à écrire le texte. Place le curseur dans l'application cible."
    if reason == "inaccessible":
        return "Je n'ai pas réussi à écrire le texte."
    return SPOKEN_WRITE_FAILED


def _ok(message: str, **extra) -> dict:
    payload = {"success": True, "message": message, "consigne": CONSIGNE}
    payload.update(extra)
    return payload


def _fail(message: str, reason: str, error: str, **extra) -> dict:
    payload = {
        "success": False,
        "message": message,
        "reason": reason,
        "error": error,
        "consigne": CONSIGNE,
    }
    payload.update(extra)
    return payload
