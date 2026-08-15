"""Historique des dictees : SQLite + index plein texte FTS5.

JSON reecrit en entier a chaque ajout (l'approche du projet precedent) ne tient pas
la recherche ni quelques milliers d'entrees. FTS5 est livre avec le sqlite3 de la
bibliotheque standard, donc zero dependance en plus.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .paths import AUDIO_DIR, HISTORY_DB

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id            TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    text          TEXT NOT NULL,
    model_id      TEXT NOT NULL,
    device        TEXT,
    audio_seconds REAL NOT NULL DEFAULT 0,
    latency_ms    INTEGER NOT NULL DEFAULT 0,
    audio_path    TEXT,
    pinned        INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_entries_created ON entries(created_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts
    USING fts5(text, content='entries', content_rowid='rowid', tokenize='unicode61');

CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
    INSERT INTO entries_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, text) VALUES('delete', old.rowid, old.text);
END;
CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, text) VALUES('delete', old.rowid, old.text);
    INSERT INTO entries_fts(rowid, text) VALUES (new.rowid, new.text);
END;
"""

# Colonnes ajoutees apres coup. `CREATE TABLE IF NOT EXISTS` ne touche pas une
# table existante : sans cette liste, une base creee par une version anterieure
# garderait son ancien schema et toute lecture de la nouvelle colonne echouerait.
#
# Ajouter une entree ici suffit ; SQLite pose une colonne nullable sans
# reecrire la table, et les anciennes lignes valent NULL — ce qui est
# exactement « cette dictee n'a pas ete diarisee ».
_MIGRATIONS: list[tuple[str, str]] = [
    # (colonne, definition)
    ("segments", "TEXT"),  # JSON : [{"speaker", "start", "end", "text"}, ...]
]


def _migrate(conn: sqlite3.Connection) -> None:
    """Applique les colonnes manquantes. Idempotent, et sans perte."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(entries)")}
    for column, definition in _MIGRATIONS:
        if column not in existing:
            log.info("Historique : ajout de la colonne « %s »", column)
            conn.execute(f"ALTER TABLE entries ADD COLUMN {column} {definition}")


def _row_to_entry(row: sqlite3.Row) -> dict:
    """Convertit une ligne en entree, tours de parole decodes.

    Le seul endroit ou le JSON est relu : une base ecrite a la main, ou par une
    version qui aurait stocke autre chose, ne doit pas faire disparaitre
    l'historique entier de l'interface. On perd les tours de parole, pas la
    dictee.
    """
    entry = dict(row)
    raw = entry.get("segments")
    if not raw:
        entry["segments"] = None
        return entry
    try:
        entry["segments"] = json.loads(raw)
    except (TypeError, ValueError):
        log.warning("Tours de parole illisibles sur l'entrée %s", entry.get("id"))
        entry["segments"] = None
    return entry


class History:
    def __init__(self, db_path: Path = HISTORY_DB) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        _migrate(self._conn)
        self._conn.commit()

    def add(
        self,
        *,
        text: str,
        model_id: str,
        device: str = "",
        audio_seconds: float = 0.0,
        latency_ms: int = 0,
        audio_path: str | None = None,
        segments: list[dict] | None = None,
    ) -> dict:
        entry = {
            "id": uuid.uuid4().hex,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "text": text,
            "model_id": model_id,
            "device": device,
            "audio_seconds": round(audio_seconds, 2),
            "latency_ms": latency_ms,
            "audio_path": audio_path,
            "pinned": 0,
            # Stocke en JSON plutot qu'en table liee : on ne fait jamais de
            # requete sur un tour de parole isole, on les relit toujours en
            # bloc avec leur dictee. Une table de plus n'apporterait qu'une
            # jointure. NULL = dictee non diarisee, ce qu'etait tout
            # l'historique anterieur.
            "segments": json.dumps(segments, ensure_ascii=False) if segments else None,
        }
        with self._lock:
            self._conn.execute(
                "INSERT INTO entries (id, created_at, text, model_id, device,"
                " audio_seconds, latency_ms, audio_path, pinned, segments)"
                " VALUES (:id, :created_at, :text, :model_id, :device,"
                " :audio_seconds, :latency_ms, :audio_path, :pinned, :segments)",
                entry,
            )
            self._conn.commit()
        # L'appelant recoit les tours de parole sous forme utilisable, pas la
        # chaine JSON qui vient d'etre ecrite.
        return {**entry, "segments": segments or None}

    def search(self, query: str = "", *, limit: int = 100, offset: int = 0) -> list[dict]:
        query = query.strip()
        with self._lock:
            if not query:
                rows = self._conn.execute(
                    "SELECT * FROM entries ORDER BY pinned DESC, created_at DESC"
                    " LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
            else:
                # Prefixe sur chaque terme : "iso" trouve "isopropylique".
                match = " ".join(f'"{t}"*' for t in query.split() if t)
                try:
                    rows = self._conn.execute(
                        "SELECT e.* FROM entries_fts f JOIN entries e ON e.rowid = f.rowid"
                        " WHERE entries_fts MATCH ? ORDER BY rank LIMIT ? OFFSET ?",
                        (match, limit, offset),
                    ).fetchall()
                except sqlite3.OperationalError:
                    # Requete FTS invalide : repli sur un LIKE.
                    rows = self._conn.execute(
                        "SELECT * FROM entries WHERE text LIKE ?"
                        " ORDER BY created_at DESC LIMIT ? OFFSET ?",
                        (f"%{query}%", limit, offset),
                    ).fetchall()
        return [_row_to_entry(r) for r in rows]

    def get(self, entry_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM entries WHERE id = ?", (entry_id,)
            ).fetchone()
        return _row_to_entry(row) if row else None

    def update_text(self, entry_id: str, text: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE entries SET text = ? WHERE id = ?", (text, entry_id))
            self._conn.commit()

    def set_pinned(self, entry_id: str, pinned: bool) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE entries SET pinned = ? WHERE id = ?", (int(pinned), entry_id)
            )
            self._conn.commit()

    def delete(self, entry_id: str) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT audio_path FROM entries WHERE id = ?", (entry_id,)
            ).fetchone()
            self._conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
            self._conn.commit()
        if row and row["audio_path"]:
            audio_path = Path(row["audio_path"]).resolve()
            # Les fichiers importes restent la propriete de l'utilisateur. Seuls
            # les enregistrements crees dans le dossier gere par Murmure sont
            # supprimes avec leur entree d'historique.
            if audio_path.is_relative_to(AUDIO_DIR.resolve()):
                audio_path.unlink(missing_ok=True)

    def stats(self) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(audio_seconds), 0) secs FROM entries"
            ).fetchone()
        return {"count": row["n"], "total_audio_seconds": round(row["secs"], 1)}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
