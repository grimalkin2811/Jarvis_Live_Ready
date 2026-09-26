"""Détection de l'intention d'écriture.

Le chemin vocal principal reste l'appel d'outil Gemini (le modèle entend
l'audio). Ce classifieur est la règle déterministe correspondante :

* il sert de filet quand la transcription de la demande est connue
  (une hypothèse du type « qu'est-ce que tu écrirais » ne doit pas écrire) ;
* il route une commande textuelle via ``handle_command`` ;
* il est testé sans micro ni réseau.

Il ne transforme pas chaque occurrence du verbe « écrire » en action.

Règle par défaut (1.4.2) : **le curseur actif est la sortie par défaut**.
Demander de rédiger, écrire, composer, formuler, produire ou générer un texte
— quel que soit l'artefact nommé (message, lettre, mail, paragraphe, CV,
rapport…) — écrit au curseur. Un fichier ``.txt`` n'est créé que si la demande
parle explicitement d'un fichier (« un fichier », « un .txt », « un fichier
texte », « sauvegarde », « enregistre dans un fichier »…). Sans mot de fichier,
le doute profite toujours au curseur.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

CONVERSATION = "conversation"
WRITE_ACTIVE_FIELD = "write_active_field"
CREATE_TEXT_FILE = "create_text_file"
BOTH = "both"

#: Sorties qui désignent **explicitement** un fichier. Seules elles créent un
#: ``.txt`` : « crée-moi un fichier… », « génère un document », « dans un .txt »,
#: « sauvegarde… ». « texte » y figure pour préserver le cas historique de la
#: 1.4.0 (« crée-moi un texte de présentation » = un fichier).
_FILE_NOUN = r"(fichier|fichiers|txt|document|documents|texte|contenu|user_content|user content)"

#: Artefacts rédactionnels : ils désignent un texte à écrire, pas un fichier.
#: Avec un verbe de création ils partent donc au curseur (défaut 1.4.2).
_TEXT_ARTIFACT = (
    r"(texte|textes|mail|mails|courriel|courriels|lettre|lettres|message|messages|"
    r"paragraphe|paragraphes|note|notes|article|articles|resume|resumes|discours|"
    r"presentation|presentations|cv|rapport|rapports|poeme|histoire|annonce|"
    r"reponse|reponses|commentaire|commentaires|post|liste|listes|brouillon)"
)

#: Verbes de production d'un texte : avec un artefact rédactionnel, ils visent
#: le curseur (« fais-moi une lettre », « génère-moi un résumé »).
_PRODUCE_VERB = (
    r"fais|fait|faites|cree|creer|crees|creez|genere|generer|generes|generez|"
    r"prepare|preparer|preparez|produis|produire|produisez|formule|formuler|formulez|"
    r"redige|rediges|redigez|ecris|ecrivez|compose|composez|tape|tapez"
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
        # Les deux sorties sont évoquées : le curseur ne l'emporte que s'il est
        # nommé explicitement (« au curseur », « dans le champ ») sans mot de
        # fichier à côté. Sinon, la sortie fichier explicitement demandée gagne.
        if _explicit_field(text) and not _explicit_file(text):
            return WritingIntent(WRITE_ACTIVE_FIELD, "champ_explicite", False)
        return WritingIntent(CREATE_TEXT_FILE, "fichier_explicite", False)
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
    """Récit de l'utilisateur (« j'ai fait une lettre ») : jamais une action.

    Les verbes de production ajoutés en 1.4.2 (« j'ai fait », « j'ai créé »)
    doivent rester un récit, pas un ordre d'écrire au curseur.
    """
    return bool(
        re.search(
            r"\b(je t ecris|j ecris|j ai ecrit|je t ai ecrit|j ecrivais|"
            r"on m a ecrit|je lui ai ecrit|"
            r"j ai fait|j ai cree|j ai redige|j ai genere|j ai tape|"
            r"tu as fait|tu as ecrit|il a fait|il a ecrit|elle a fait|on a fait)\b",
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
    if _explicit_field(text):
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
    # Défaut 1.4.2 : produire un texte (même avec « crée », « génère »,
    # « fais ») écrit au curseur, tant qu'aucun fichier n'est demandé.
    if re.search(
        rf"\b(?:{_PRODUCE_VERB})\b.{{0,40}}\b{_TEXT_ARTIFACT}\b",
        text,
    ):
        return True
    return False


def _wants_file(text: str) -> bool:
    if _explicit_file(text):
        return True
    if re.search(r"\b(enregistre|enregistrer|enregistrez)\b", text) and re.search(
        r"\b(fichier|texte|document|txt|ca|cela|contenu)\b", text
    ):
        return True
    if re.search(r"\bfais\b.{0,40}\bfichier\b", text):
        return True
    if re.search(
        rf"\b(cree|creer|crees|creez|genere|generer|generez)\b.{{0,60}}\b{_FILE_NOUN}\b",
        text,
    ):
        return True
    return False


def _explicit_field(text: str) -> bool:
    """Vrai si la demande nomme la sortie « curseur / champ actif »."""
    return bool(re.search(r"\b(dans le champ|champ actif|au curseur|a l emplacement)\b", text))


def _explicit_file(text: str) -> bool:
    """Vrai si la demande nomme explicitement un fichier (seule sortie ``.txt``)."""
    if re.search(r"\b(fichier|fichiers)\b", text) or re.search(r"\btxt\b", text):
        return True
    if "user_content" in text or "user content" in text:
        return True
    if re.search(r"\b(sauvegarde|sauvegarder|sauvegardez)\b", text):
        return True
    if re.search(r"\b(telechargeable|telecharger|telecharge)\b", text):
        return True
    return False
