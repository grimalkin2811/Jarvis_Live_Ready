"""Mémoire persistante locale de Jarvis.

Le module fournit un sous-système léger et non bloquant basé sur SQLite. Il est
volontairement indépendant de Gemini Live : le reste de l'application passe par
``MemoryManager`` ou par les wrappers exposés dans ``src.tools`` sans connaître
le détail du stockage.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
import os
import re
import sqlite3
import threading
import unicodedata
from dataclasses import dataclass

from . import paths

DEFAULT_DATA_DIR = str(paths.data_dir())
DEFAULT_MEMORY_DB = os.environ.get("JARVIS_MEMORY_DATABASE_PATH", str(paths.memory_db()))

#: Version courante du schéma de la base mémoire. Toute modification de
#: structure doit incrémenter ce numéro et ajouter la migration listée dans
#: ``_MIGRATIONS`` (les données utilisateur sont conservées).
MEMORY_SCHEMA_VERSION = 2

#: Nombre maximal de versions ANTÉRIEURES conservées par souvenir (v2).
MEMORY_REVISIONS_KEPT = 20


def _migrate_v2_revisions(conn) -> None:
    """v1 -> v2 : historique des versions de chaque souvenir (Dashboard 1.9).

    Purement additif : une table ``memory_revisions`` et deux déclencheurs.
    Chaque modification RÉELLE du contenu, de la catégorie, de l'importance
    ou de l'archivage — quelle qu'en soit l'origine (outil vocal, extraction
    automatique, Dashboard) — enregistre l'état PRÉCÉDENT. L'historique est
    borné (``MEMORY_REVISIONS_KEPT`` versions par souvenir) et disparaît avec
    le souvenir s'il est un jour supprimé physiquement. Aucune donnée
    existante n'est modifiée ; une version antérieure de Jarvis ouvrant une
    base v2 continue de fonctionner (elle ignore la table, les déclencheurs
    restent actifs).
    """
    conn.executescript(
        f"""
        BEGIN;
        CREATE TABLE IF NOT EXISTS memory_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id INTEGER NOT NULL,
            recorded_at TEXT NOT NULL,
            valid_from TEXT,
            content TEXT NOT NULL,
            category TEXT NOT NULL,
            importance INTEGER NOT NULL,
            archived INTEGER NOT NULL DEFAULT 0,
            source TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_memory_revisions_memory
            ON memory_revisions(memory_id, id);
        CREATE TRIGGER IF NOT EXISTS memories_revision_au
        AFTER UPDATE OF content, category, importance, archived ON memories
        WHEN old.content IS NOT new.content
          OR old.category IS NOT new.category
          OR old.importance IS NOT new.importance
          OR old.archived IS NOT new.archived
        BEGIN
            INSERT INTO memory_revisions
                (memory_id, recorded_at, valid_from, content, category, importance, archived, source)
            VALUES
                (old.id, strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'), old.updated_at,
                 old.content, old.category, old.importance, old.archived, old.source);
            DELETE FROM memory_revisions
            WHERE memory_id = old.id AND id NOT IN (
                SELECT id FROM memory_revisions WHERE memory_id = old.id
                ORDER BY id DESC LIMIT {int(MEMORY_REVISIONS_KEPT)}
            );
        END;
        CREATE TRIGGER IF NOT EXISTS memories_revision_ad AFTER DELETE ON memories
        BEGIN
            DELETE FROM memory_revisions WHERE memory_id = old.id;
        END;
        COMMIT;
        """
    )


#: Migrations de schéma, indexées par version cible. Chaque fonction reçoit la
#: connexion SQLite et doit faire passer la base à la version suivante. Les
#: données existantes ne sont jamais supprimées.
_MIGRATIONS: dict[int, callable] = {2: _migrate_v2_revisions}

#: Champs éditables d'un souvenir (ceux que l'historique versionne).
EDITABLE_FIELDS = ("content", "category", "importance")


def _migrate(conn) -> None:
    """Applique de façon idempotente les migrations de schéma manquantes."""
    try:
        current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    except sqlite3.Error:
        current = 0
    for target in sorted(_MIGRATIONS):
        if target <= current:
            continue
        _MIGRATIONS[target](conn)
        conn.execute(f"PRAGMA user_version = {int(target)}")

CATEGORIES = {
    "identity",
    "preference",
    "person",
    "project",
    "configuration",
    "fact",
    "habit",
    "decision",
    "other",
}


@dataclass(frozen=True)
class Memory:
    id: int
    content: str
    category: str
    importance: int
    created_at: str
    updated_at: str
    last_accessed: str | None
    source: str
    subject_key: str | None = None
    access_count: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "category": self.category,
            "importance": self.importance,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_accessed": self.last_accessed,
            "source": self.source,
            "subject_key": self.subject_key,
            "access_count": self.access_count,
        }


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFD", str(value or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _safe_category(category: str | None) -> str:
    normalized = normalize_text(category or "other") or "other"
    return normalized if normalized in CATEGORIES else "other"


def _safe_importance(value) -> int:
    try:
        return max(1, min(5, int(value)))
    except Exception:
        return 3


def infer_category(content: str) -> str:
    text = normalize_text(content)
    if re.search(r"\b(je m appelle|mon prenom|mon nom|appelle moi)\b", text):
        return "identity"
    if re.search(r"\b(je prefere|j aime mieux|reponds moi|langue|ton|style)\b", text):
        return "preference"
    if re.search(r"\b(mon ami|ma mere|mon pere|ma soeur|mon frere|mon collegue|marius|paul)\b", text):
        return "person"
    if re.search(r"\b(projet|jarvis|application|repo|repository|developpe|code)\b", text):
        return "project"
    if re.search(r"\b(configuration|configure|reglage|parametre|version|python|windows|api)\b", text):
        return "configuration"
    if re.search(r"\b(habitude|tous les jours|souvent|toujours|generalement)\b", text):
        return "habit"
    if re.search(r"\b(decide|decision|choisi|on garde|desormais|a partir de maintenant)\b", text):
        return "decision"
    return "fact"


def infer_subject_key(content: str, category: str | None = None) -> str:
    """Construit une clé stable pour mettre à jour plutôt que dupliquer.

    Ce n'est pas une compréhension sémantique complète ; c'est un garde-fou
    robuste pour les préférences, versions et formulations courantes.
    """
    text = normalize_text(content)
    category = _safe_category(category or infer_category(content))

    patterns = [
        (r"\bpython\s*(?:version)?\s*(\d+(?:\.\d+)*)?", "python"),
        (r"\b(gemini|jarvis|windows|vscode|visual studio code)\b", None),
        (r"\bje m appelle\s+([a-z0-9]+)", "user_name"),
        (r"\bmon prenom (?:est|c est)\s+([a-z0-9]+)", "user_name"),
        (r"\bje prefere\b.*\b(francais|anglais|english|french)\b", "language_preference"),
        (r"\bmon projet actuel (?:est|s appelle)\s+([a-z0-9]+)", "current_project"),
        (r"\bprojet\s+([a-z0-9]+)", None),
    ]
    for pattern, fixed in patterns:
        match = re.search(pattern, text)
        if match:
            if fixed:
                return f"{category}:{fixed}"
            return f"{category}:{match.group(1)}"

    # Retire les mots de liaison pour former une clé courte.
    words = [
        w
        for w in text.split()
        if w not in {
            "je", "j", "mon", "ma", "mes", "le", "la", "les", "un", "une",
            "de", "du", "des", "a", "au", "aux", "avec", "que", "qui", "est",
            "suis", "utilise", "maintenant", "actuellement", "souviens", "toi",
            "retiens", "partir", "maintenant",
        }
    ]
    return f"{category}:{' '.join(words[:5])}" if words else f"{category}:general"


def tokenize_query(query: str) -> list[str]:
    return [w for w in normalize_text(query).split() if len(w) >= 3]


class MemoryManager:
    """Gestionnaire de mémoire persistante SQLite.

    Toutes les méthodes capturent les erreurs SQLite et renvoient un résultat
    exploitable afin que Jarvis continue à fonctionner même si la mémoire est
    indisponible.
    """

    def __init__(
        self,
        database_path: str | os.PathLike | None = None,
        enabled: bool = True,
        max_results: int = 5,
        min_importance: int = 1,
    ) -> None:
        self.database_path = str(database_path or DEFAULT_MEMORY_DB)
        self.enabled = bool(enabled)
        self.max_results = max(1, int(max_results or 5))
        self.min_importance = _safe_importance(min_importance)
        self._lock = threading.RLock()
        self.available = False
        self.last_error: str | None = None
        if self.enabled:
            self._initialize()

    @contextmanager
    def _connect(self):
        """Open a SQLite connection, commit on success, always close it.

        Utilisé comme ``with self._connect() as conn``, ce helper reproduit la
        sémantique de ``sqlite3.Connection`` utilisé comme context manager
        (commit si succès, rollback si exception) et ferme en plus la
        connexion. Fermer ici est important sous Windows : les fichiers WAL
        restent verrouillés tant que l'objet connexion est vivant.
        """
        conn = sqlite3.connect(self.database_path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _initialize(self) -> None:
        try:
            directory = os.path.dirname(self.database_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with self._connect() as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS memories (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        content TEXT NOT NULL,
                        category TEXT NOT NULL DEFAULT 'other',
                        importance INTEGER NOT NULL DEFAULT 3,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        last_accessed TEXT,
                        source TEXT NOT NULL DEFAULT 'unknown',
                        subject_key TEXT,
                        access_count INTEGER NOT NULL DEFAULT 0,
                        archived INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_subject "
                    "ON memories(subject_key) WHERE archived = 0 AND subject_key IS NOT NULL"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_memories_category "
                    "ON memories(category, importance, updated_at)"
                )
                try:
                    conn.execute(
                        "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts "
                        "USING fts5(content, category, content='memories', content_rowid='id', tokenize='unicode61')"
                    )
                    conn.executescript(
                        """
                        CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                            INSERT INTO memories_fts(rowid, content, category)
                            VALUES (new.id, new.content, new.category);
                        END;
                        CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                            INSERT INTO memories_fts(memories_fts, rowid, content, category)
                            VALUES('delete', old.id, old.content, old.category);
                        END;
                        CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                            INSERT INTO memories_fts(memories_fts, rowid, content, category)
                            VALUES('delete', old.id, old.content, old.category);
                            INSERT INTO memories_fts(rowid, content, category)
                            VALUES (new.id, new.content, new.category);
                        END;
                        """
                    )
                except sqlite3.Error:
                    # FTS5 absent : les recherches LIKE restent disponibles.
                    pass
                # Applique les migrations de schéma éventuelles (données
                # utilisateur préservées — voir _MIGRATIONS / _migrate).
                _migrate(conn)
                # Marque la version de schéma si aucune migration ne l'a fixée.
                try:
                    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
                    if current == 0:
                        conn.execute(f"PRAGMA user_version = {int(MEMORY_SCHEMA_VERSION)}")
                except sqlite3.Error:
                    pass
            self.available = True
            self.last_error = None
        except Exception as exc:
            self.available = False
            self.last_error = str(exc)
            print(f"[Memory] Mémoire indisponible : {exc}")

    def set_enabled(self, value: bool) -> None:
        """Active/désactive la mémoire (utilisé par le menu radial).

        La réactivation retente l'ouverture de la base : si SQLite était
        indisponible au démarrage, l'utilisateur n'a pas besoin de relancer
        Jarvis pour récupérer sa mémoire.
        """
        value = bool(value)
        if value and not self.enabled:
            self.enabled = True
            if not self.available:
                self._initialize()
        else:
            self.enabled = value

    def _disabled_result(self, **extra) -> dict:
        payload = {"success": False, "disabled": not self.enabled, "available": self.available}
        if self.last_error:
            payload["error"] = self.last_error
        elif not self.enabled:
            payload["error"] = "Mémoire désactivée."
        else:
            payload["error"] = "Mémoire indisponible."
        payload.update(extra)
        return payload

    def _ready(self) -> bool:
        if not self.enabled:
            return False
        if not self.available:
            self._initialize()
        return self.available

    def _row_to_memory(self, row) -> Memory:
        return Memory(
            id=int(row["id"]),
            content=row["content"],
            category=row["category"],
            importance=int(row["importance"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_accessed=row["last_accessed"],
            source=row["source"],
            subject_key=row["subject_key"],
            access_count=int(row["access_count"] or 0),
        )

    def add_memory(
        self,
        content: str,
        category: str | None = None,
        importance: int = 3,
        source: str = "user",
        subject_key: str | None = None,
        allow_update: bool = True,
    ) -> dict:
        content = str(content or "").strip()
        if not content:
            return {"success": False, "error": "Souvenir vide."}
        if not self._ready():
            return self._disabled_result()
        category = _safe_category(category or infer_category(content))
        importance = _safe_importance(importance)
        subject_key = subject_key or infer_subject_key(content, category)
        now = utc_now()
        with self._lock:
            try:
                with self._connect() as conn:
                    existing = None
                    if allow_update and subject_key:
                        existing = conn.execute(
                            "SELECT * FROM memories WHERE archived = 0 AND subject_key = ?",
                            (subject_key,),
                        ).fetchone()
                    if existing:
                        conn.execute(
                            """
                            UPDATE memories
                            SET content = ?, category = ?, importance = MAX(importance, ?),
                                updated_at = ?, source = ?
                            WHERE id = ?
                            """,
                            (content, category, importance, now, source, existing["id"]),
                        )
                        memory_id = int(existing["id"])
                        action = "updated"
                    else:
                        cur = conn.execute(
                            """
                            INSERT INTO memories
                            (content, category, importance, created_at, updated_at, source, subject_key)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (content, category, importance, now, now, source, subject_key),
                        )
                        memory_id = int(cur.lastrowid)
                        action = "created"
                return {"success": True, "action": action, "id": memory_id, "memory": self.get_memory(memory_id).get("memory")}
            except Exception as exc:
                self.available = False
                self.last_error = str(exc)
                print(f"[Memory] Ajout impossible : {exc}")
                return self._disabled_result()

    def get_memory(self, memory_id: int) -> dict:
        if not self._ready():
            return self._disabled_result()
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM memories WHERE id = ? AND archived = 0",
                    (int(memory_id),),
                ).fetchone()
                if not row:
                    return {"success": False, "error": "Souvenir introuvable."}
                return {"success": True, "memory": self._row_to_memory(row).to_dict()}
        except Exception as exc:
            self.available = False
            self.last_error = str(exc)
            return self._disabled_result()

    def list_memories(self, limit: int = 50, category: str | None = None) -> dict:
        if not self._ready():
            return self._disabled_result(memories=[], count=0)
        limit = max(1, min(500, int(limit or 50)))
        try:
            params: list = []
            where = "archived = 0"
            if category:
                where += " AND category = ?"
                params.append(_safe_category(category))
            params.append(limit)
            with self._connect() as conn:
                rows = conn.execute(
                    f"SELECT * FROM memories WHERE {where} ORDER BY importance DESC, updated_at DESC LIMIT ?",
                    params,
                ).fetchall()
            memories = [self._row_to_memory(row).to_dict() for row in rows]
            return {"success": True, "memories": memories, "count": len(memories)}
        except Exception as exc:
            self.available = False
            self.last_error = str(exc)
            return self._disabled_result(memories=[], count=0)

    def search_memories(
        self,
        query: str,
        limit: int | None = None,
        min_importance: int | None = None,
        mark_accessed: bool = True,
    ) -> dict:
        if not self._ready():
            return self._disabled_result(memories=[], count=0)
        query = str(query or "").strip()
        limit = max(1, min(50, int(limit or self.max_results)))
        min_importance = _safe_importance(min_importance or self.min_importance)
        try:
            rows = []
            with self._connect() as conn:
                terms = tokenize_query(query)
                if terms:
                    fts_query = " OR ".join(terms[:8])
                    try:
                        rows = conn.execute(
                            """
                            SELECT m.*, bm25(memories_fts) AS rank
                            FROM memories_fts
                            JOIN memories m ON m.id = memories_fts.rowid
                            WHERE memories_fts MATCH ? AND m.archived = 0 AND m.importance >= ?
                            ORDER BY (m.importance * 2) - rank DESC, m.updated_at DESC
                            LIMIT ?
                            """,
                            (fts_query, min_importance, limit),
                        ).fetchall()
                    except sqlite3.Error:
                        rows = []
                if not rows:
                    like_terms = [f"%{term}%" for term in (terms or tokenize_query(query) or [normalize_text(query)]) if term]
                    if like_terms:
                        clauses = " OR ".join(["lower(content) LIKE ?" for _ in like_terms])
                        rows = conn.execute(
                            f"""
                            SELECT * FROM memories
                            WHERE archived = 0 AND importance >= ? AND ({clauses})
                            ORDER BY importance DESC, updated_at DESC
                            LIMIT ?
                            """,
                            [min_importance, *like_terms, limit],
                        ).fetchall()
                    else:
                        rows = conn.execute(
                            """
                            SELECT * FROM memories
                            WHERE archived = 0 AND importance >= ?
                            ORDER BY importance DESC, updated_at DESC
                            LIMIT ?
                            """,
                            (min_importance, limit),
                        ).fetchall()
                memories = [self._row_to_memory(row).to_dict() for row in rows]
                if mark_accessed and memories:
                    now = utc_now()
                    ids = [m["id"] for m in memories]
                    conn.executemany(
                        "UPDATE memories SET last_accessed = ?, access_count = access_count + 1 WHERE id = ?",
                        [(now, mid) for mid in ids],
                    )
            return {"success": True, "memories": memories, "count": len(memories)}
        except Exception as exc:
            self.available = False
            self.last_error = str(exc)
            print(f"[Memory] Recherche impossible : {exc}")
            return self._disabled_result(memories=[], count=0)

    def update_memory(self, memory_id: int, content: str | None = None, category: str | None = None, importance: int | None = None) -> dict:
        if not self._ready():
            return self._disabled_result()
        updates = []
        params = []
        if content is not None:
            text = str(content).strip()
            if not text:
                return {"success": False, "error": "Souvenir vide."}
            updates.append("content = ?")
            params.append(text)
        if category is not None:
            updates.append("category = ?")
            params.append(_safe_category(category))
        if importance is not None:
            updates.append("importance = ?")
            params.append(_safe_importance(importance))
        if not updates:
            return self.get_memory(memory_id)
        updates.append("updated_at = ?")
        params.append(utc_now())
        params.append(int(memory_id))
        try:
            with self._lock, self._connect() as conn:
                cur = conn.execute(
                    f"UPDATE memories SET {', '.join(updates)} WHERE id = ? AND archived = 0",
                    params,
                )
                if cur.rowcount <= 0:
                    return {"success": False, "error": "Souvenir introuvable."}
            result = self.get_memory(memory_id)
            result["action"] = "updated"
            return result
        except Exception as exc:
            self.available = False
            self.last_error = str(exc)
            return self._disabled_result()

    def delete_memory(self, memory_id: int) -> dict:
        if not self._ready():
            return self._disabled_result()
        try:
            with self._lock, self._connect() as conn:
                cur = conn.execute(
                    "UPDATE memories SET archived = 1, updated_at = ? WHERE id = ? AND archived = 0",
                    (utc_now(), int(memory_id)),
                )
            if cur.rowcount <= 0:
                return {"success": False, "error": "Souvenir introuvable."}
            return {"success": True, "deleted": int(memory_id)}
        except Exception as exc:
            self.available = False
            self.last_error = str(exc)
            return self._disabled_result()

    def forget(self, query: str, limit: int = 10, confirm: bool = False) -> dict:
        """Oublie les souvenirs liés à une requête, avec confirmation."""
        found = self.search_memories(query, limit=limit, mark_accessed=False)
        if not found.get("success"):
            return found
        memories = found.get("memories", [])
        if not confirm:
            return {
                "success": False,
                "confirmation_required": True,
                "message": "Confirme avant d'effacer ces souvenirs.",
                "matches": memories,
                "count": len(memories),
            }
        deleted = []
        for memory in memories:
            res = self.delete_memory(memory["id"])
            if res.get("success"):
                deleted.append(memory["id"])
        return {"success": True, "deleted": deleted, "count": len(deleted)}

    def clear_memory(self, confirm: bool = False) -> dict:
        if not confirm:
            return {"success": False, "confirmation_required": True, "error": "Confirmation requise pour effacer toute la mémoire."}
        if not self._ready():
            return self._disabled_result()
        try:
            with self._lock, self._connect() as conn:
                cur = conn.execute("UPDATE memories SET archived = 1, updated_at = ? WHERE archived = 0", (utc_now(),))
            return {"success": True, "cleared": int(cur.rowcount)}
        except Exception as exc:
            self.available = False
            self.last_error = str(exc)
            return self._disabled_result()

    def extract_candidate_memories(self, text: str) -> list[dict]:
        """Détection locale conservatrice d'informations mémorisables.

        Cette méthode sert de fallback testable sans clé Gemini. En production,
        les tools ``remember``/``update_memory`` permettent aussi au modèle Live
        d'enregistrer explicitement ce qu'il juge durable.
        """
        raw = str(text or "").strip()
        if not raw:
            return []
        candidates = []
        sentences = [s.strip(" .!?\n\t") for s in re.split(r"[.!?\n]+", raw) if s.strip()]
        explicit_patterns = [
            r"(?:souviens toi que|souviens-toi que|retiens que|memorise que|mémorise que)\s+(.+)",
            r"(?:n oublie pas que|n'oublie pas que)\s+(.+)",
        ]
        durable_patterns = [
            r"\bje m appelle\b.+",
            r"\bmon prenom\b.+",
            r"\bje prefere\b.+",
            r"\ba partir de maintenant\b.+",
            r"\bmon projet actuel\b.+",
            r"\bj utilise maintenant\b.+",
            r"\bje suis passe(?:e)? a\b.+",
        ]
        for sentence in sentences:
            normalized = normalize_text(sentence)
            content = sentence
            importance = 3
            explicit = False
            for pattern in explicit_patterns:
                match = re.search(pattern, normalized)
                if match:
                    # Garde la phrase originale après le marqueur quand possible.
                    content = re.sub(pattern, r"\1", sentence, flags=re.IGNORECASE).strip(" .") or sentence
                    importance = 5
                    explicit = True
                    break
            if not explicit and not any(re.search(pattern, normalized) for pattern in durable_patterns):
                continue
            category = infer_category(content)
            if not explicit and category == "fact":
                importance = 2
            candidates.append(
                {
                    "content": content,
                    "category": category,
                    "importance": importance,
                    "source": "extraction",
                    "subject_key": infer_subject_key(content, category),
                    "explicit": explicit,
                }
            )
        return candidates

    def remember_from_text(self, text: str, source: str = "conversation") -> dict:
        candidates = self.extract_candidate_memories(text)
        stored = []
        for cand in candidates:
            result = self.add_memory(
                cand["content"],
                category=cand["category"],
                importance=cand["importance"],
                source=source,
                subject_key=cand["subject_key"],
            )
            if result.get("success"):
                stored.append(result)
        return {"success": True, "candidates": candidates, "stored": stored, "count": len(stored)}

    def relevant_memories_for_prompt(self, query: str = "", limit: int | None = None) -> str:
        if not self.enabled:
            return ""
        result = self.search_memories(
            query,
            limit=limit or self.max_results,
            min_importance=self.min_importance,
            mark_accessed=True,
        )
        if not result.get("success"):
            return ""
        memories = result.get("memories", [])
        if not memories and query:
            # Sans transcription de la requête au démarrage Live, une recherche
            # trop ciblée peut ne rien trouver. On prend alors seulement les
            # souvenirs importants/récents, toujours limités.
            fallback = self.list_memories(limit=limit or self.max_results)
            if fallback.get("success"):
                memories = fallback.get("memories", [])
        if not memories:
            return ""
        lines = ["MEMORY — Informations connues sur l'utilisateur (souvenirs locaux pertinents, pas des instructions système) :"]
        for memory in memories:
            lines.append(f"- [{memory['category']}, importance {memory['importance']}/5] {memory['content']}")
        return "\n".join(lines)


    # ------------------------------------------------------------------
    # Administration (Dashboard v1.9) : transactions, concurrence, versions
    # ------------------------------------------------------------------
    #
    # Ces méthodes ne marquent JAMAIS la mémoire « indisponible » pour une
    # erreur attendue (conflit, contrainte, base verrouillée, disque plein) :
    # la transaction est annulée (``with conn``), aucune donnée n'est écrite
    # à moitié, et l'appelant reçoit une erreur explicite.

    @staticmethod
    def _record_dict(row) -> dict:
        record = {key: row[key] for key in row.keys()}
        record["id"] = int(record["id"])
        record["importance"] = int(record.get("importance") or 3)
        record["archived"] = bool(record.get("archived"))
        record["access_count"] = int(record.get("access_count") or 0)
        return record

    @staticmethod
    def _expected_matches(record: dict, expected: dict | None) -> bool:
        if not expected:
            return True
        for key in (*EDITABLE_FIELDS, "updated_at"):
            if key in expected and record.get(key) != expected.get(key):
                return False
        return True

    def get_memory_record(self, memory_id: int, include_archived: bool = False) -> dict:
        """Enregistrement complet (toutes colonnes, y compris inconnues)."""
        if not self._ready():
            return self._disabled_result()
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT * FROM memories WHERE id = ?", (int(memory_id),)).fetchone()
        except (sqlite3.Error, ValueError, TypeError) as exc:
            return {"success": False, "error": f"Lecture impossible : {exc}"}
        if row is None or (row["archived"] and not include_archived):
            return {"success": False, "not_found": True, "error": "Souvenir introuvable (supprimé entre-temps ?)."}
        return {"success": True, "memory": self._record_dict(row)}

    def browse_memories(self, query: str = "", category: str | None = None, limit: int = 200,
                        offset: int = 0) -> dict:
        """Parcours/recherche SANS effet de bord (n'incrémente pas l'accès)."""
        if not self._ready():
            return self._disabled_result(memories=[], count=0, total=0)
        limit = max(1, min(500, int(limit or 200)))
        offset = max(0, int(offset or 0))
        where = ["archived = 0"]
        params: list = []
        if category:
            where.append("category = ?")
            params.append(_safe_category(category))
        text = normalize_text(query)
        terms = [t for t in text.split() if t][:8]
        for term in terms:
            where.append("(lower(content) LIKE ? OR lower(category) LIKE ? OR lower(coalesce(subject_key,'')) LIKE ?)")
            like = f"%{term}%"
            params.extend([like, like, like])
        clause = " AND ".join(where)
        try:
            with self._connect() as conn:
                total = int(conn.execute(f"SELECT COUNT(*) FROM memories WHERE {clause}", params).fetchone()[0])
                rows = conn.execute(
                    f"SELECT * FROM memories WHERE {clause} ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
                    [*params, limit, offset],
                ).fetchall()
        except sqlite3.Error as exc:
            return {"success": False, "error": f"Lecture impossible : {exc}", "memories": [], "count": 0, "total": 0}
        if terms and not rows:
            # Les accents sont normalisés côté requête : second essai sur le
            # texte normalisé en Python (petites bases, coût négligeable).
            try:
                with self._connect() as conn:
                    candidates = conn.execute(
                        "SELECT * FROM memories WHERE archived = 0" + (" AND category = ?" if category else "")
                        + " ORDER BY updated_at DESC, id DESC LIMIT 2000",
                        [_safe_category(category)] if category else [],
                    ).fetchall()
            except sqlite3.Error:
                candidates = []
            matched = [row for row in candidates
                       if all(term in normalize_text(row["content"]) for term in terms)]
            total = len(matched)
            rows = matched[offset:offset + limit]
        memories = [self._record_dict(row) for row in rows]
        return {"success": True, "memories": memories, "count": len(memories), "total": total}

    def find_subject_conflict(self, content: str, category: str | None = None) -> dict | None:
        """Souvenir ACTIF qui serait remplacé par ``add_memory`` (même sujet)."""
        if not self._ready():
            return None
        category = _safe_category(category or infer_category(content))
        key = infer_subject_key(content, category)
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM memories WHERE archived = 0 AND subject_key = ?", (key,)
                ).fetchone()
        except sqlite3.Error:
            return None
        return self._record_dict(row) if row is not None else None

    def create_memory_entry(self, content: str, category: str | None = None, importance: int = 3,
                            source: str = "dashboard", distinct: bool = False) -> dict:
        """Crée un souvenir sans JAMAIS écraser un souvenir existant en silence.

        ``distinct=False`` : si un souvenir actif porte déjà le même sujet
        (clé déduite), rien n'est écrit et le conflit est retourné.
        ``distinct=True`` : le souvenir est créé à part (sans clé de sujet).
        """
        text = str(content or "").strip()
        if not text:
            return {"success": False, "error": "Le contenu du souvenir est vide."}
        if len(text) > 4000:
            return {"success": False, "error": "Souvenir trop long (4000 caractères maximum)."}
        if not self._ready():
            return self._disabled_result()
        category = _safe_category(category or infer_category(text))
        importance = _safe_importance(importance)
        subject_key = None if distinct else infer_subject_key(text, category)
        now = utc_now()
        with self._lock:
            try:
                with self._connect() as conn:
                    if subject_key is not None:
                        existing = conn.execute(
                            "SELECT * FROM memories WHERE archived = 0 AND subject_key = ?", (subject_key,)
                        ).fetchone()
                        if existing is not None:
                            return {"success": False, "conflict": True,
                                    "existing": self._record_dict(existing),
                                    "error": "Un souvenir porte déjà ce sujet."}
                    cur = conn.execute(
                        """
                        INSERT INTO memories
                        (content, category, importance, created_at, updated_at, source, subject_key)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (text, category, importance, now, now, str(source or "dashboard")[:40], subject_key),
                    )
                    memory_id = int(cur.lastrowid)
            except sqlite3.IntegrityError as exc:
                return {"success": False, "conflict": True, "error": f"Contrainte d'unicité : {exc}"}
            except sqlite3.Error as exc:
                return {"success": False, "error": f"Écriture impossible (rien n'a été modifié) : {exc}"}
        record = self.get_memory_record(memory_id)
        return {"success": True, "action": "created", "id": memory_id, "memory": record.get("memory")}

    def update_memory_checked(self, memory_id: int, expected: dict | None, content: str | None = None,
                              category: str | None = None, importance: int | None = None) -> dict:
        """Modification avec contrôle de concurrence optimiste.

        ``expected`` : la version que l'utilisateur a modifiée (champs
        éditables + ``updated_at``). Si le souvenir a changé entre-temps
        (voix, autre fenêtre…), RIEN n'est écrit et la version actuelle est
        retournée (``conflict=True``) pour arbitrage.
        """
        if not self._ready():
            return self._disabled_result()
        updates: list[str] = []
        params: list = []
        if content is not None:
            text = str(content).strip()
            if not text:
                return {"success": False, "error": "Le contenu du souvenir est vide."}
            if len(text) > 4000:
                return {"success": False, "error": "Souvenir trop long (4000 caractères maximum)."}
            updates.append("content = ?")
            params.append(text)
        if category is not None:
            if normalize_text(category) not in CATEGORIES:
                return {"success": False, "error": f"Catégorie inconnue : {category}."}
            updates.append("category = ?")
            params.append(_safe_category(category))
        if importance is not None:
            try:
                value = int(importance)
            except (TypeError, ValueError):
                return {"success": False, "error": "Importance invalide (1 à 5)."}
            if not 1 <= value <= 5:
                return {"success": False, "error": "Importance invalide (1 à 5)."}
            updates.append("importance = ?")
            params.append(value)
        if not updates:
            return {"success": False, "error": "Aucune modification demandée."}
        with self._lock:
            try:
                with self._connect() as conn:
                    row = conn.execute("SELECT * FROM memories WHERE id = ?", (int(memory_id),)).fetchone()
                    if row is None or row["archived"]:
                        return {"success": False, "not_found": True,
                                "error": "Souvenir introuvable (supprimé entre-temps ?)."}
                    current = self._record_dict(row)
                    if not self._expected_matches(current, expected):
                        return {"success": False, "conflict": True, "current": current,
                                "error": "Le souvenir a été modifié entre-temps."}
                    now = utc_now()
                    try:
                        previous = dt.datetime.fromisoformat(str(current.get("updated_at")))
                        if dt.datetime.fromisoformat(now) <= previous:
                            # Résolution à la seconde : une nouvelle version ne
                            # partage jamais (ni ne précède) l'horodatage de
                            # la précédente — le contrôle de version reste fiable.
                            now = (previous + dt.timedelta(seconds=1)).isoformat(timespec="seconds")
                    except (TypeError, ValueError):
                        pass
                    conn.execute(
                        f"UPDATE memories SET {', '.join(updates)}, updated_at = ? WHERE id = ? AND archived = 0",
                        [*params, now, int(memory_id)],
                    )
            except sqlite3.IntegrityError as exc:
                return {"success": False, "error": f"Contrainte d'unicité : {exc}"}
            except sqlite3.Error as exc:
                return {"success": False, "error": f"Écriture impossible (rien n'a été modifié) : {exc}"}
        record = self.get_memory_record(memory_id)
        return {"success": True, "action": "updated", "memory": record.get("memory")}

    def delete_memory_checked(self, memory_id: int, expected: dict | None) -> dict:
        """Suppression (archivage, comme ``delete_memory``) avec contrôle de version."""
        if not self._ready():
            return self._disabled_result()
        with self._lock:
            try:
                with self._connect() as conn:
                    row = conn.execute("SELECT * FROM memories WHERE id = ?", (int(memory_id),)).fetchone()
                    if row is None or row["archived"]:
                        return {"success": False, "not_found": True,
                                "error": "Souvenir introuvable (déjà supprimé ?)."}
                    current = self._record_dict(row)
                    if not self._expected_matches(current, expected):
                        return {"success": False, "conflict": True, "current": current,
                                "error": "Le souvenir a été modifié entre-temps : vérifie avant de supprimer."}
                    conn.execute(
                        "UPDATE memories SET archived = 1, updated_at = ? WHERE id = ? AND archived = 0",
                        (utc_now(), int(memory_id)),
                    )
            except sqlite3.Error as exc:
                return {"success": False, "error": f"Suppression impossible (rien n'a été modifié) : {exc}"}
        return {"success": True, "deleted": int(memory_id)}

    def list_memory_revisions(self, memory_id: int, limit: int = MEMORY_REVISIONS_KEPT) -> dict:
        """Versions ANTÉRIEURES d'un souvenir, de la plus récente à la plus ancienne."""
        if not self._ready():
            return self._disabled_result(revisions=[])
        limit = max(1, min(100, int(limit or MEMORY_REVISIONS_KEPT)))
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM memory_revisions WHERE memory_id = ? ORDER BY id DESC LIMIT ?",
                    (int(memory_id), limit),
                ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return {"success": True, "revisions": [], "available": False}
            return {"success": False, "error": f"Historique illisible : {exc}", "revisions": []}
        except sqlite3.Error as exc:
            return {"success": False, "error": f"Historique illisible : {exc}", "revisions": []}
        revisions = []
        for row in rows:
            item = {key: row[key] for key in row.keys()}
            item["archived"] = bool(item.get("archived"))
            item["importance"] = int(item.get("importance") or 3)
            revisions.append(item)
        return {"success": True, "revisions": revisions, "available": True}

    def restore_memory_revision(self, memory_id: int, revision_id: int, expected: dict | None) -> dict:
        """Restaure une version antérieure (la version actuelle rejoint l'historique)."""
        if not self._ready():
            return self._disabled_result()
        try:
            with self._connect() as conn:
                revision = conn.execute(
                    "SELECT * FROM memory_revisions WHERE id = ? AND memory_id = ?",
                    (int(revision_id), int(memory_id)),
                ).fetchone()
        except sqlite3.Error as exc:
            return {"success": False, "error": f"Historique illisible : {exc}"}
        if revision is None:
            return {"success": False, "not_found": True, "error": "Version introuvable."}
        current = self.get_memory_record(memory_id).get("memory")
        if (
            current is not None
            and self._expected_matches(current, expected)
            and all(current.get(key) == revision[key] for key in EDITABLE_FIELDS)
        ):
            # Rien à restaurer : ne jamais annoncer « restaurée » ni réécrire
            # (l'horodatage changerait sans aucune modification réelle).
            return {"success": False, "unchanged": True,
                    "error": "Cette version est identique à la version actuelle."}
        result = self.update_memory_checked(
            memory_id, expected,
            content=revision["content"], category=revision["category"], importance=revision["importance"],
        )
        if result.get("success"):
            result["action"] = "restored"
            result["revision_id"] = int(revision_id)
        elif result.get("error") == "Aucune modification demandée.":
            result = {"success": False, "error": "Cette version est identique à la version actuelle."}
        return result

    def memory_stats(self) -> dict:
        """Statistiques non sensibles (comptes, schéma, taille) pour le diagnostic."""
        if not self._ready():
            return self._disabled_result()
        try:
            with self._connect() as conn:
                total = int(conn.execute("SELECT COUNT(*) FROM memories WHERE archived = 0").fetchone()[0])
                archived = int(conn.execute("SELECT COUNT(*) FROM memories WHERE archived = 1").fetchone()[0])
                by_category = {
                    row[0]: int(row[1])
                    for row in conn.execute(
                        "SELECT category, COUNT(*) FROM memories WHERE archived = 0 GROUP BY category"
                    ).fetchall()
                }
                version = int(conn.execute("PRAGMA user_version").fetchone()[0])
                try:
                    revisions = int(conn.execute("SELECT COUNT(*) FROM memory_revisions").fetchone()[0])
                except sqlite3.Error:
                    revisions = None
                last = conn.execute("SELECT MAX(updated_at) FROM memories WHERE archived = 0").fetchone()[0]
        except sqlite3.Error as exc:
            return {"success": False, "error": f"Statistiques indisponibles : {exc}"}
        try:
            size = os.path.getsize(self.database_path)
        except OSError:
            size = None
        return {
            "success": True, "total": total, "archived": archived, "by_category": by_category,
            "schema_version": version, "revisions": revisions, "last_updated": last,
            "size_bytes": size, "path": self.database_path,
        }

    def check_integrity(self) -> dict:
        """``PRAGMA quick_check`` (lecture seule)."""
        if not self._ready():
            return self._disabled_result()
        try:
            with self._connect() as conn:
                rows = conn.execute("PRAGMA quick_check").fetchall()
        except sqlite3.Error as exc:
            return {"success": False, "error": f"Vérification impossible : {exc}"}
        messages = [str(row[0]) for row in rows]
        return {"success": True, "ok": messages == ["ok"], "messages": messages[:20]}


_DEFAULT_MANAGER: MemoryManager | None = None
_DEFAULT_LOCK = threading.Lock()


def get_default_memory_manager() -> MemoryManager:
    global _DEFAULT_MANAGER
    with _DEFAULT_LOCK:
        if _DEFAULT_MANAGER is None:
            enabled = os.environ.get("JARVIS_MEMORY_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
            path = os.environ.get("JARVIS_MEMORY_DATABASE_PATH", DEFAULT_MEMORY_DB)
            try:
                max_results = int(os.environ.get("JARVIS_MEMORY_MAX_RESULTS", "5"))
            except Exception:
                max_results = 5
            try:
                min_importance = int(os.environ.get("JARVIS_MEMORY_MIN_IMPORTANCE", "1"))
            except Exception:
                min_importance = 1
            _DEFAULT_MANAGER = MemoryManager(path, enabled=enabled, max_results=max_results, min_importance=min_importance)
        return _DEFAULT_MANAGER


def set_default_memory_manager(manager: MemoryManager | None) -> None:
    global _DEFAULT_MANAGER
    with _DEFAULT_LOCK:
        _DEFAULT_MANAGER = manager
