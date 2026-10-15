"""Modèle du protocole de validation : statuts, verdict, masquage, rapport.

Bibliothèque standard uniquement : ce module est importé par l'orchestrateur
(``valider_pr.py``) avant que l'environnement de validation n'existe, et par
les tests (``tests/test_validation_protocole.py``).
"""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

REUSSI = "RÉUSSI"
ECHEC = "ÉCHEC"
NON_TESTE = "NON TESTÉ"
BLOQUE = "BLOQUÉ"
NON_APPLICABLE = "NON APPLICABLE"
STATUTS = (REUSSI, ECHEC, NON_TESTE, BLOQUE, NON_APPLICABLE)

VERDICT_PRETE = "PRÊTE À ÊTRE PROPOSÉE À LA FUSION (décision humaine, aucune fusion automatique)"
VERDICT_BLOQUEE = "BLOQUÉE"
VERDICT_INSUFFISANTE = "VALIDATION INSUFFISANTE"

#: Codes de sortie du lanceur.
CODE_PRETE, CODE_BLOQUEE, CODE_INSUFFISANTE, CODE_ERREUR_PROTOCOLE = 0, 1, 2, 3

GRAVITES = ("bloquant", "important", "mineur", "info")

SECTIONS = {
    "A": "Dépôt, commit et PR",
    "B": "Environnements Python et PySide6",
    "C": "Tests automatisés",
    "D": "Dashboard réel sous Windows",
    "E": "Gemini Live réel et audio",
    "F": "Mémoire et données personnelles",
}


@dataclass
class Controle:
    id: str
    titre: str
    statut: str = NON_TESTE
    resultat: str = ""
    preuve: str = ""
    raison: str = ""
    #: Requis pour le verdict « prête » (un contrôle non requis n'est qu'informatif).
    requis: bool = True
    #: Gravité d'un ÉCHEC : bloquant / important / mineur / info.
    gravite: str = "bloquant"

    @property
    def section(self) -> str:
        return self.id[:1]

    def regler(self, statut: str, resultat: str = "", preuve: str = "", raison: str = "") -> "Controle":
        if statut not in STATUTS:
            raise ValueError(f"statut inconnu : {statut!r}")
        self.statut = statut
        if resultat:
            self.resultat = resultat
        if preuve:
            self.preuve = preuve
        if raison:
            self.raison = raison
        return self


@dataclass
class Probleme:
    gravite: str
    titre: str
    detail: str = ""
    emplacement: str = ""
    controle: str = ""


@dataclass
class Commande:
    etape: str
    commande: str
    code: int | None
    duree_s: float
    journal: str = ""
    note: str = ""


@dataclass
class Rapport:
    meta: dict = field(default_factory=dict)
    depot: dict = field(default_factory=dict)
    pr: dict = field(default_factory=dict)
    versions: dict = field(default_factory=dict)
    controles: list[Controle] = field(default_factory=list)
    tests: dict = field(default_factory=dict)
    commandes: list[Commande] = field(default_factory=list)
    problemes: list[Probleme] = field(default_factory=list)
    donnees: dict = field(default_factory=dict)
    ecarts: list[str] = field(default_factory=list)
    windows: list[str] = field(default_factory=list)
    gemini_audio: list[str] = field(default_factory=list)
    remarques: list[str] = field(default_factory=list)
    termine: bool = False
    erreur_protocole: str = ""

    def controle(self, cid: str) -> Controle:
        for item in self.controles:
            if item.id == cid:
                return item
        raise KeyError(cid)

    def ajouter(self, cid: str, titre: str, *, requis: bool = True, gravite: str = "bloquant") -> Controle:
        try:
            return self.controle(cid)
        except KeyError:
            item = Controle(cid, titre, requis=requis, gravite=gravite)
            self.controles.append(item)
            return item

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, default=str)


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


def _cause(item: "Controle") -> str:
    texte = item.raison or item.resultat or ""
    return texte.split(" : ")[0].split(" — ")[0].strip()


def regrouper(items: list["Controle"], seuil: int = 3) -> list[str]:
    """Une ligne par contrôle, sauf pour les séries partageant statut et cause (« D01–D17 (16) … »)."""
    lignes: list[str] = []
    index = 0
    while index < len(items):
        fin = index + 1
        while (fin < len(items) and items[fin].statut == items[index].statut
               and _cause(items[fin]) == _cause(items[index]) and _cause(items[index])):
            fin += 1
        groupe = items[index:fin]
        if len(groupe) >= seuil:
            lignes.append(f"{groupe[0].id}–{groupe[-1].id} ({len(groupe)} contrôles) : {groupe[0].statut} — "
                          f"{_cause(groupe[0])} (détail par contrôle en §5 et §11)")
        else:
            for item in groupe:
                lignes.append(f"{item.id} « {item.titre} » : {item.statut} — {item.raison or item.resultat}")
        index = fin
    return lignes


def calculer_verdict(rapport: Rapport) -> tuple[str, int, list[str]]:
    """Verdict global, code de sortie et justification (liste de phrases)."""
    raisons: list[str] = []
    if rapport.erreur_protocole:
        raisons.append(f"Le protocole s'est interrompu sur une erreur : {rapport.erreur_protocole}")
    requis = [c for c in rapport.controles if c.requis]
    echecs = [c for c in requis if c.statut == ECHEC]
    manquants = [c for c in requis if c.statut in (NON_TESTE, BLOQUE)]
    bloquants = [p for p in rapport.problemes if p.gravite == "bloquant"]
    if echecs or bloquants:
        for item in echecs:
            raisons.append(f"{item.id} « {item.titre} » : ÉCHEC — {item.resultat or item.raison}")
        for probleme in bloquants:
            if not any(probleme.controle == c.id for c in echecs):
                raisons.append(f"Problème bloquant : {probleme.titre}")
        return VERDICT_BLOQUEE, CODE_BLOQUEE, raisons
    if rapport.erreur_protocole or not rapport.termine or manquants or not requis:
        raisons.extend(regrouper(manquants))
        if not rapport.termine and not rapport.erreur_protocole:
            raisons.append("Le protocole n'est pas allé à son terme (interruption).")
        if not requis:
            raisons.append("Aucun contrôle requis n'a été enregistré.")
        code = CODE_ERREUR_PROTOCOLE if rapport.erreur_protocole else CODE_INSUFFISANTE
        return VERDICT_INSUFFISANTE, code, raisons
    raisons.append("Tous les contrôles requis sont RÉUSSIS, y compris les vérifications Windows, audio et Gemini réelles.")
    return VERDICT_PRETE, CODE_PRETE, raisons


# ---------------------------------------------------------------------------
# Masquage des secrets et des chemins privés
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{20,}"),
    re.compile(r"(?i)(x-goog-api-key[\"']?\s*[:=]\s*[\"']?)[^\s\"',;]{8,}"),
    re.compile(r"(?i)([?&](?:key|api_key|access_token)=)[^&\s\"']{8,}"),
    re.compile(r"(?i)((?:GEMINI|GOOGLE)_API_KEY[\"']?\s*[:=]\s*[\"']?)[^\s\"',;]{8,}"),
    re.compile(r"(?i)([\"']api_key[\"']\s*:\s*[\"'])[^\"']{8,}"),
]
MASQUE = "«secret masqué»"


class Masqueur:
    """Remplace secrets et chemins privés par des marqueurs neutres."""

    def __init__(self) -> None:
        self._secrets: list[str] = []
        self._chemins: list[tuple[str, str]] = []

    def secret(self, valeur: str | None) -> None:
        if valeur and len(valeur) >= 8 and valeur not in self._secrets:
            self._secrets.append(valeur)
            self._secrets.sort(key=len, reverse=True)

    def chemin(self, valeur: str | os.PathLike | None, marqueur: str) -> None:
        if not valeur:
            return
        text = str(valeur).rstrip("\\/")
        if len(text) < 3:
            return
        variants = {text, text.replace("\\", "/"), text.replace("/", "\\")}
        for variant in variants:
            if (variant, marqueur) not in self._chemins:
                self._chemins.append((variant, marqueur))
        self._chemins.sort(key=lambda pair: len(pair[0]), reverse=True)

    def secrets_connus(self) -> int:
        return len(self._secrets)

    def contient_secret(self, texte: str) -> bool:
        if any(s in texte for s in self._secrets):
            return True
        for pattern in _SECRET_PATTERNS:
            for match in pattern.finditer(texte):
                # Partie secrète = après le préfixe éventuel ; un marqueur de masquage n'est pas un secret.
                secret = texte[match.end(1):match.end()] if pattern.groups else match.group(0)
                if MASQUE not in secret and "«" not in secret[:2]:
                    return True
        return False

    def __call__(self, texte: object) -> str:
        result = "" if texte is None else str(texte)
        for value in self._secrets:
            result = result.replace(value, MASQUE)
        for pattern in _SECRET_PATTERNS:
            if pattern.groups:
                result = pattern.sub(lambda m: m.group(1) + MASQUE, result)
            else:
                result = pattern.sub(MASQUE, result)
        for value, marker in self._chemins:
            if os.name == "nt":
                result = re.sub(re.escape(value), lambda _m, marker=marker: marker, result, flags=re.IGNORECASE)
            else:
                result = result.replace(value, marker)
        # Nom d'utilisateur restant dans un chemin Windows/Unix.
        result = re.sub(r"(?i)([A-Z]:[\\/]+Users[\\/]+)[^\\/\s\"'<>|]+", r"\1<utilisateur>", result)
        result = re.sub(r"(/home/)[^/\s\"'<>|]+", r"\1<utilisateur>", result)
        return result


# ---------------------------------------------------------------------------
# Comptage des tests (JUnit + progression)
# ---------------------------------------------------------------------------


def lire_junit(chemin: str | os.PathLike) -> dict | None:
    """Compte les TESTS (éléments ``testcase``), jamais les sous-tests.

    L'attribut ``tests`` de pytest additionne tests et sous-tests : il n'est
    donc volontairement pas utilisé.
    """
    path = Path(chemin)
    if not path.is_file():
        return None
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return None
    totals = {"executes": 0, "reussis": 0, "echoues": 0, "erreurs": 0, "ignores": 0, "duree_s": 0.0}
    par_fichier: dict[str, dict[str, int]] = {}
    echecs: list[dict] = []
    cas: list[dict] = []
    for case in root.iter("testcase"):
        classname = case.attrib.get("classname", "")
        name = case.attrib.get("name", "")
        fichier = classname.split(".")
        module = []
        for part in fichier:
            module.append(part)
            if part.startswith("test_") or part.endswith("_test"):
                break
        key = "/".join(module) + ".py" if module else "?"
        bucket = par_fichier.setdefault(key, {"reussis": 0, "echoues": 0, "erreurs": 0, "ignores": 0})
        try:
            totals["duree_s"] += float(case.attrib.get("time", 0) or 0)
        except ValueError:
            pass
        tags = [child.tag for child in case]
        issue = ("skipped" if "skipped" in tags else "error" if "error" in tags
                 else "failure" if "failure" in tags else "passed")
        cas.append({"fichier": key, "nom": name.split("[", 1)[0], "id": name, "issue": issue})
        if "skipped" in tags:
            totals["ignores"] += 1
            bucket["ignores"] += 1
            continue
        totals["executes"] += 1
        if "error" in tags:
            totals["erreurs"] += 1
            bucket["erreurs"] += 1
            outcome = "error"
        elif "failure" in tags:
            totals["echoues"] += 1
            bucket["echoues"] += 1
            outcome = "failure"
        else:
            totals["reussis"] += 1
            bucket["reussis"] += 1
            continue
        child = next(c for c in case if c.tag == outcome)
        echecs.append({
            "test": f"{key}::{name}",
            "type": outcome,
            "message": (child.attrib.get("message") or "").strip().splitlines()[0][:300] if child.attrib.get("message") else "",
        })
    totals["duree_s"] = round(totals["duree_s"], 2)
    return {"totaux": totals, "par_fichier": par_fichier, "echecs": echecs, "cas": cas}


def lire_progression(chemin: str | os.PathLike) -> dict:
    """Exploite le journal de progression du plugin (robuste aux plantages)."""
    path = Path(chemin)
    collected = None
    started: list[str] = []
    finished: set[str] = set()
    results: dict[str, str] = {}
    subtests = {"reussis": 0, "echoues": 0, "ignores": 0}
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            kind = entry.get("evenement")
            if kind == "collecte":
                collected = int(entry.get("nombre", 0))
            elif kind == "debut":
                started.append(entry.get("test", ""))
            elif kind == "fin":
                finished.add(entry.get("test", ""))
            elif kind == "resultat":
                if entry.get("sous_test"):
                    issue = entry.get("issue")
                    key = {"passed": "reussis", "failed": "echoues", "skipped": "ignores"}.get(issue)
                    if key:
                        subtests[key] += 1
                    continue
                test = entry.get("test", "")
                issue = entry.get("issue", "")
                previous = results.get(test)
                if previous == "failed":
                    continue
                if issue == "failed" or previous is None or previous == "passed":
                    results[test] = issue
    in_progress = [t for t in started if t not in finished]
    return {
        "collectes": collected,
        "demarres": len(started),
        "termines": len(finished),
        "en_cours_au_plantage": in_progress[-1] if in_progress else "",
        "resultats": results,
        "sous_tests": subtests,
    }


def compter_tests(collectes: int | None, junit: dict | None, progression: dict | None) -> dict:
    """Synthèse SANS double comptage.

    * ``executes`` : tests réellement lancés (hors ignorés) ;
    * ``bloques`` : tests collectés mais jamais exécutés (plantage, arrêt) ;
    * les sous-tests sont comptés à part.
    """
    source = "junit" if junit else ("progression" if progression and progression.get("resultats") else "aucune")
    if junit:
        t = junit["totaux"]
        executes, reussis = t["executes"], t["reussis"]
        echoues = t["echoues"] + t["erreurs"]
        ignores = t["ignores"]
    elif progression and progression.get("resultats"):
        values = list(progression["resultats"].values())
        ignores = sum(1 for v in values if v == "skipped")
        echoues = sum(1 for v in values if v == "failed")
        reussis = sum(1 for v in values if v == "passed")
        executes = reussis + echoues
    else:
        executes = reussis = echoues = ignores = 0
    if collectes is None and progression:
        collectes = progression.get("collectes")
    bloques = max(0, (collectes or 0) - executes - ignores) if collectes is not None else None
    return {
        "source": source,
        "collectes": collectes,
        "executes": executes,
        "reussis": reussis,
        "echoues": echoues,
        "ignores": ignores,
        "bloques": bloques,
        "sous_tests": (progression or {}).get("sous_tests", {"reussis": 0, "echoues": 0, "ignores": 0}),
        "en_cours_au_plantage": (progression or {}).get("en_cours_au_plantage", ""),
    }


# ---------------------------------------------------------------------------
# Fichiers de rapport : jamais d'écrasement silencieux
# ---------------------------------------------------------------------------


def creer_fichier_unique(dossier: str | os.PathLike, base: str, extension: str = ".md") -> Path:
    """Crée (mode exclusif) un fichier qui n'existait pas : suffixe _2, _3…"""
    folder = Path(dossier)
    folder.mkdir(parents=True, exist_ok=True)
    index = 1
    while True:
        name = f"{base}{'' if index == 1 else f'_{index}'}{extension}"
        candidate = folder / name
        try:
            with open(candidate, "x", encoding="utf-8") as handle:
                handle.write("")
            return candidate
        except FileExistsError:
            index += 1
            if index > 999:
                raise


# ---------------------------------------------------------------------------
# Rendu Markdown
# ---------------------------------------------------------------------------


def _cellule(texte: object) -> str:
    value = "" if texte is None else str(texte)
    value = value.replace("|", "\\|").replace("\r", "").strip()
    return value.replace("\n", "<br>") or "—"


def _table(entetes: list[str], lignes: list[list[object]]) -> list[str]:
    out = ["| " + " | ".join(entetes) + " |", "|" + "|".join("---" for _ in entetes) + "|"]
    for ligne in lignes:
        out.append("| " + " | ".join(_cellule(c) for c in ligne) + " |")
    return out


def _kv(dico: dict, masque: Masqueur) -> list[str]:
    lignes = []
    for cle, valeur in dico.items():
        if isinstance(valeur, (dict, list)):
            valeur = json.dumps(valeur, ensure_ascii=False)
        lignes.append([cle, masque(valeur)])
    return _table(["Élément", "Valeur"], lignes)


def rendre_markdown(rapport: Rapport, masque: Masqueur) -> str:
    verdict, code, raisons = calculer_verdict(rapport)
    meta = rapport.meta
    out: list[str] = []
    out.append(f"# Rapport de validation — PR #{meta.get('pr', '?')} de Jarvis")
    out.append("")
    out.append(f"*Généré par `valider_pr.bat` le {meta.get('debut', '?')} — durée {meta.get('duree', '?')} — "
               f"machine : {meta.get('machine', '?')} — mode : {meta.get('mode', '?')}.*")
    if not rapport.termine:
        out.append("")
        out.append("> **Rapport intermédiaire ou interrompu** : le protocole n'est pas allé à son terme. "
                   "Les contrôles non atteints restent « NON TESTÉ ».")
    counts = {s: sum(1 for c in rapport.controles if c.statut == s) for s in STATUTS}

    out += ["", "## 1. Résumé exécutif", ""]
    out.append(f"- Commit testé : `{rapport.depot.get('commit_teste', '?')}` "
               f"(tête de la PR distante : `{rapport.depot.get('tete_pr_distante', '?')}`).")
    out.append("- Contrôles : " + ", ".join(f"{counts[s]} {s}" for s in STATUTS) + ".")
    tests = rapport.tests.get("suite") or {}
    if tests:
        out.append(f"- Suite automatisée : {tests.get('executes', 0)} tests exécutés, {tests.get('reussis', 0)} réussis, "
                   f"{tests.get('echoues', 0)} en échec, {tests.get('ignores', 0)} ignorés, "
                   f"{tests.get('bloques', '?')} bloqués (sous-tests comptés à part).")
    for ligne in meta.get("resume", []):
        out.append(f"- {masque(ligne)}")

    out += ["", "## 2. Verdict global", ""]
    out.append(f"**{verdict}** (code de sortie {code})")
    out.append("")
    for raison in raisons:
        out.append(f"- {masque(raison)}")

    out += ["", "## 3. Commit, branche, PR et `main`", ""]
    out += _kv(rapport.depot, masque)
    if rapport.pr:
        out.append("")
        out += _kv(rapport.pr, masque)

    out += ["", "## 4. Versions détectées", ""]
    for env, info in rapport.versions.items():
        out.append(f"### {env}")
        out.append("")
        if isinstance(info, dict):
            out += _kv(info, masque)
        else:
            out.append(masque(info))
        out.append("")

    out += ["## 5. Contrôles (statut, résultat, preuve)", ""]
    for section, titre in SECTIONS.items():
        items = [c for c in rapport.controles if c.section == section]
        if not items:
            continue
        out.append(f"### {section}. {titre}")
        out.append("")
        lignes = []
        for c in items:
            detail = c.resultat
            if c.raison and c.statut in (NON_TESTE, BLOQUE, NON_APPLICABLE, ECHEC):
                detail = f"{detail} — {c.raison}" if detail else c.raison
            lignes.append([c.id, c.titre, f"**{c.statut}**", masque(detail), masque(c.preuve),
                           "oui" if c.requis else "non"])
        out += _table(["ID", "Contrôle", "Statut", "Résultat", "Preuve", "Requis"], lignes)
        out.append("")

    out += ["## 6. Nombre exact de tests", ""]
    if rapport.tests:
        lignes = []
        for nom, t in rapport.tests.items():
            if not isinstance(t, dict) or "executes" not in t:
                continue
            st = t.get("sous_tests") or {}
            lignes.append([nom, t.get("collectes", "?"), t.get("executes", 0), t.get("reussis", 0), t.get("echoues", 0),
                           t.get("ignores", 0), t.get("bloques", "?"),
                           f"{st.get('reussis', 0)} réussis / {st.get('echoues', 0)} en échec",
                           t.get("source", "")])
        out += _table(["Exécution", "Collectés", "Exécutés", "Réussis", "Échoués", "Ignorés", "Bloqués",
                       "Sous-tests (à part)", "Source"], lignes)
        vues = rapport.tests.get("vues")
        if vues:
            out.append("")
            out.append("Vues par domaine **extraites de la même exécution** (non additives : un test peut "
                       "appartenir à plusieurs vues ; aucun test n'est relancé ni compté deux fois dans le total) :")
            out.append("")
            out += _table(["Domaine", "Exécutés", "Réussis", "Échoués", "Ignorés", "Fichiers"],
                          [[v["nom"], v["executes"], v["reussis"], v["echoues"], v["ignores"], v["fichiers"]] for v in vues])
        echecs = rapport.tests.get("echecs_detail")
        if echecs:
            out.append("")
            out.append("Tests en échec :")
            out.append("")
            for e in echecs[:60]:
                out.append(f"- `{masque(e['test'])}` ({e['type']}) : {masque(e.get('message', ''))}")
    else:
        out.append("Aucune exécution de tests n'a eu lieu.")

    out += ["", "## 7. Commandes exécutées et codes de retour", ""]
    out += _table(["Étape", "Commande", "Code", "Durée", "Journal", "Note"],
                  [[c.etape, f"`{masque(c.commande)}`", "—" if c.code is None else c.code, f"{c.duree_s:.1f} s",
                    masque(c.journal), masque(c.note)] for c in rapport.commandes])

    out += ["", "## 8. Erreurs et avertissements par gravité", ""]
    if rapport.problemes:
        for gravite in GRAVITES:
            items = [p for p in rapport.problemes if p.gravite == gravite]
            if not items:
                continue
            out.append(f"**{gravite.capitalize()}**")
            out.append("")
            for p in items:
                where = f" — `{masque(p.emplacement)}`" if p.emplacement else ""
                ref = f" [{p.controle}]" if p.controle else ""
                out.append(f"- {masque(p.titre)}{ref}{where}" + (f" : {masque(p.detail)}" if p.detail else ""))
            out.append("")
    else:
        out.append("Aucune erreur ni avertissement consigné.")
        out.append("")

    out += ["## 9. Vérifications Windows réellement effectuées", ""]
    out += [f"- {masque(x)}" for x in rapport.windows] or ["- Aucune."]
    out += ["", "## 10. Vérifications Gemini et audio réellement effectuées", ""]
    out += [f"- {masque(x)}" for x in rapport.gemini_audio] or ["- Aucune."]

    out += ["", "## 11. Contrôles non testés et raisons", ""]
    non = [c for c in rapport.controles if c.statut in (NON_TESTE, BLOQUE, NON_APPLICABLE)]
    if non:
        out += _table(["ID", "Contrôle", "Statut", "Raison", "Requis"],
                      [[c.id, c.titre, c.statut, masque(c.raison or c.resultat), "oui" if c.requis else "non"] for c in non])
    else:
        out.append("Aucun.")

    out += ["", "## 12. Preuve de non-modification des données personnelles", ""]
    if rapport.donnees:
        out += _kv(rapport.donnees, masque)
    else:
        out.append("Aucune mesure disponible (protocole interrompu avant l'instantané).")

    out += ["", "## 13. Écarts entre installation quotidienne, environnement de test et packaging", ""]
    out += [f"- {masque(x)}" for x in rapport.ecarts] or ["- Aucun écart relevé."]

    out += ["", "## 14. Problèmes restants classés par priorité", ""]
    restants = sorted(rapport.problemes, key=lambda p: GRAVITES.index(p.gravite) if p.gravite in GRAVITES else 9)
    restants = [p for p in restants if p.gravite != "info"]
    if restants:
        out += _table(["Priorité", "Problème", "Fichier / ligne", "Contrôle", "Détail"],
                      [[p.gravite, masque(p.titre), masque(p.emplacement), p.controle, masque(p.detail)] for p in restants])
    else:
        out.append("Aucun problème restant démontré par ce protocole (les contrôles non testés sont listés en §11).")

    out += ["", "## 15. Recommandation", ""]
    precedent_liste = False
    for ligne in meta.get("recommandation", []) or [verdict]:
        liste = str(ligne).startswith("- ")
        if precedent_liste and not liste:
            out.append("")
        out.append(masque(ligne))
        if not liste:
            out.append("")
        precedent_liste = liste
    if precedent_liste:
        out.append("")
    if rapport.remarques:
        out += ["### Remarques", ""] + [f"- {masque(r)}" for r in rapport.remarques] + [""]
    out.append("---")
    out.append("*Aucune fusion, aucun tag, aucune publication ni aucun envoi à un tiers n'a été effectué par ce protocole. "
               "Aucune clé, aucun jeton ni aucune conversation personnelle ne figure dans ce rapport.*")
    texte = "\n".join(out) + "\n"
    return masque(texte)
