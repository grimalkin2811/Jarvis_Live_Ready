"""Détection de l'intention d'écriture.

Le chemin vocal principal reste l'appel d'outil Gemini (le modèle entend
l'audio). Ce classifieur est la règle déterministe correspondante :

* il sert de filet quand la transcription de la demande est connue
  (une hypothèse du type « qu'est-ce que tu écrirais » ne doit pas écrire) ;
* il route une commande textuelle via ``handle_command`` ;
* il est testé sans micro ni réseau.

Il ne transforme pas chaque occurrence du verbe « écrire » en action.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

CONVERSATION = "conversation"
WRITE_ACTIVE_FIELD = "write_active_field"
CREATE_TEXT_FILE = "create_text_file"
BOTH = "both"

_ARTIFACT = (
    r"(texte|fichier|document|mail|lettre|presentation|discours|cv|rapport|message)"
)


@dataclass(frozen=True)
class WritingIntent:
    """Résultat de classification d'une demande utilisateur."""

    action: str
    reason: str
    #: Vrai seulement pour une demande clairement non actionnable
    #: (hypothèse, négation, conseil). Un simple silence n'est pas un veto :
    #: l'appel d'outil explicite reste alors valide.
    explicit_non_action: bool = False

    @property
    def is_action(self) -> bool:
        return self.action in {WRITE_ACTIVE_FIELD, CREATE_TEXT_FILE, BOTH}


def normalize_utterance(text: str) -> str:
    """Minuscules, sans accents, apostrophes et tirets traités comme espaces."""
    value = str(text or "").lower().strip()
    value = value.replace("’", "'").replace("`", "'").replace("«", " ").replace("»", " ")
    value = unicodedata.normalize("NFD", value)
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    value = value.replace("'", " ").replace("-", " ")
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"^(hey |ok |salut |dis )?jarvis\b\s*", "", value)
    value = re.sub(r"^(s il te plait |s il vous plait |stp |svp )\s*", "", value)
    return value.strip()


def classify_writing_intent(utterance: str) -> WritingIntent:
    """Classe une demande en conversation, champ actif, fichier, ou les deux."""
    text = normalize_utterance(utterance)
    if not text:
        return WritingIntent(CONVERSATION, "vide", False)
    # Transcription partielle : ne pas bloquer un outil sur un fragment trop court,
    # sauf négation ou conditionnel déjà explicites.
    if len(text) < 8 and not _negated(text) and not _conditional(text):
        return WritingIntent(CONVERSATION, "trop_court", False)

    if _negated(text):
        return WritingIntent(CONVERSATION, "negation", True)
    if _user_narration(text) and not _leading_imperative(text):
        return WritingIntent(CONVERSATION, "recit", True)
    if _conditional(text) and not _leading_imperative(text) and not _polite_write(text):
        return WritingIntent(CONVERSATION, "hypothese", True)
    if _advice_or_meta(text) and not _leading_imperative(text) and not _polite_write(text):
        return WritingIntent(CONVERSATION, "question", True)

    wants_file = _wants_file(text)
    wants_field = _wants_field(text)
    if wants_file and wants_field and _dual_request(text):
        return WritingIntent(BOTH, "double_demande", False)
    if wants_file and wants_field:
        if re.search(r"\b(champ actif|dans le champ|au curseur)\b", text) and not re.search(
            r"\bfichier\b", text
        ):
            return WritingIntent(WRITE_ACTIVE_FIELD, "champ_explicite", False)
        if re.search(r"\b(fichier|sauvegarde|user_content)\b", text) and not re.search(
            r"\b(champ actif|dans le champ|au curseur)\b", text
        ):
            return WritingIntent(CREATE_TEXT_FILE, "fichier_explicite", False)
        if re.search(r"\bfichier\b", text):
            return WritingIntent(CREATE_TEXT_FILE, "fichier_explicite", False)
        return WritingIntent(WRITE_ACTIVE_FIELD, "ecriture_explicite", False)
    if wants_file:
        return WritingIntent(CREATE_TEXT_FILE, "creation_fichier", False)
    if wants_field:
        return WritingIntent(WRITE_ACTIVE_FIELD, "ecriture_champ", False)
    return WritingIntent(CONVERSATION, "aucune_action", False)


def _negated(text: str) -> bool:
    return bool(
        re.search(
            r"\b("
            r"n ecris pas|n ecris rien|ne m ecris pas|ne m ecris rien|"
            r"n ecrivez pas|ne redige pas|n insere pas|ne cree pas|"
            r"ne cree rien|ne sauvegarde pas|n enregistre pas|"
            r"sans ecrire|pas la peine d ecrire|ne tape pas"
            r")\b",
            text,
        )
    )


def _conditional(text: str) -> bool:
    return bool(
        re.search(
            r"\b(ecrirais|ecrirait|ecririons|redigerais|redigerait|"
            r"composerais|composerait|mettrais|mettrait|dirais)\b",
            text,
        )
    )


def _user_narration(text: str) -> bool:
    return bool(
        re.search(
            r"\b(je t ecris|j ecris|j ai ecrit|je t ai ecrit|j ecrivais|"
            r"on m a ecrit|je lui ai ecrit)\b",
            text,
        )
    )


def _leading_imperative(text: str) -> bool:
    return bool(
        re.match(
            r"^(ecris|ecrivez|redige|redigez|compose|composez|tape|tapez|"
            r"insere|inserez|dicte|dictez|cree|creez|sauvegarde|sauvegardez|"
            r"enregistre|enregistrez|genere|generez)\b",
            text,
        )
    )


def _polite_write(text: str) -> bool:
    polite = re.search(
        r"\b(peux tu|peut tu|pourrais tu|tu peux|tu pourrais|"
        r"est ce que tu peux|est ce que tu pourrais|tu veux bien|voudrais tu)\b",
        text,
    )
    verb = re.search(
        r"\b(ecrire|rediger|composer|taper|inserer|dicter|creer|"
        r"sauvegarder|enregistrer|generer)\b",
        text,
    )
    return bool(polite and verb)


def _advice_or_meta(text: str) -> bool:
    if re.match(
        r"^(comment|pourquoi|qu est ce|c est quoi|explique|definition|"
        r"donne moi des idees|conseille|quel |quelle |quels |quelles |"
        r"que |quoi )\b",
        text,
    ):
        return True
    if re.search(
        r"\b(comment (ecrire|rediger|composer|creer|sauvegarder|faire)|"
        r"des idees pour|conseille moi|astuces pour ecrire)\b",
        text,
    ):
        return True
    return False


def _dual_request(text: str) -> bool:
    return bool(re.search(r"\b(et|puis|ensuite|aussi|egalement)\b", text))


def _wants_field(text: str) -> bool:
    if re.search(r"\b(dans le champ|champ actif|au curseur|a l emplacement)\b", text):
        return True
    if re.search(
        r"\b(ecris|ecrivez|ecrives|redige|rediges|redigez|compose|composes|"
        r"composez|tape|tapes|tapez|insere|inseres|inserez|dicte|dictez)\b",
        text,
    ):
        return True
    if _polite_write(text) and re.search(
        r"\b(ecrire|rediger|composer|taper|inserer|dicter)\b", text
    ):
        return True
    return False


def _wants_file(text: str) -> bool:
    if re.search(r"\b(fichier|fichiers)\b", text) or re.search(r"\btxt\b", text):
        return True
    if "user_content" in text or "user content" in text:
        return True
    if re.search(r"\b(sauvegarde|sauvegarder|sauvegardez)\b", text):
        return True
    if re.search(r"\b(enregistre|enregistrer|enregistrez)\b", text) and re.search(
        r"\b(fichier|texte|document|txt|ca|cela|contenu)\b", text
    ):
        return True
    if re.search(r"\bfais\b.{0,40}\bfichier\b", text):
        return True
    if re.search(
        rf"\b(cree|creer|crees|creez|genere|generer|generez)\b.{{0,60}}\b{_ARTIFACT}\b",
        text,
    ):
        return True
    return False
