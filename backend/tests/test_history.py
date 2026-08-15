"""Historique : persistance, recherche, et migration du schema.

La migration est la partie qui n'a pas droit a l'erreur : elle s'applique a une
base que l'utilisateur a deja remplie. Un historique perdu ne se rejoue pas.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from murmure.history import History


@pytest.fixture
def history(tmp_path: Path) -> History:
    return History(tmp_path / "history.db")


class TestMigration:
    def test_une_base_d_avant_la_diarisation_est_migree_sans_perte(
        self, tmp_path: Path
    ) -> None:
        """Le schema d'origine, celui qu'ont toutes les installations
        existantes. `CREATE TABLE IF NOT EXISTS` ne l'aurait pas touche : sans
        migration, la lecture de la nouvelle colonne echouerait a chaque
        ouverture de l'historique."""
        db = tmp_path / "ancienne.db"
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE entries (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, text TEXT NOT NULL,
                model_id TEXT NOT NULL, device TEXT,
                audio_seconds REAL NOT NULL DEFAULT 0,
                latency_ms INTEGER NOT NULL DEFAULT 0,
                audio_path TEXT, pinned INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        conn.execute(
            "INSERT INTO entries (id, created_at, text, model_id) VALUES"
            " ('abc', '2026-01-01T00:00:00', 'une dictee d avant', 'whisper-large-v3-turbo')"
        )
        conn.commit()
        conn.close()

        store = History(db)
        entries = store.search("")
        assert len(entries) == 1, "la dictee anterieure a disparu"
        assert entries[0]["text"] == "une dictee d avant"
        assert entries[0]["segments"] is None, "une dictee d'avant n'a pas de locuteurs"
        store.close()

    def test_la_migration_est_idempotente(self, tmp_path: Path) -> None:
        db = tmp_path / "history.db"
        for _ in range(3):
            store = History(db)
            store.add(text="essai", model_id="m")
            store.close()
        store = History(db)
        assert len(store.search("")) == 3
        store.close()


class TestSegments:
    def test_aller_retour_des_tours_de_parole(self, history: History) -> None:
        segments = [
            {"speaker": 0, "start": 0.0, "end": 1.5, "text": "bonjour"},
            {"speaker": 1, "start": 1.6, "end": 3.0, "text": "salut"},
        ]
        entry = history.add(
            text="Locuteur 1 : bonjour\nLocuteur 2 : salut",
            model_id="whisper-large-v3-turbo",
            segments=segments,
        )
        assert entry["segments"] == segments, "add() doit rendre des donnees utilisables"
        assert history.get(entry["id"])["segments"] == segments

    def test_une_dictee_sans_diarisation_a_des_tours_nuls(self, history: History) -> None:
        entry = history.add(text="du texte", model_id="m")
        assert entry["segments"] is None
        assert history.get(entry["id"])["segments"] is None

    def test_des_tours_illisibles_ne_font_pas_perdre_la_dictee(
        self, history: History, tmp_path: Path
    ) -> None:
        """Une base bricolee a la main ne doit pas faire disparaitre
        l'historique de l'interface : on perd les locuteurs, pas le texte."""
        entry = history.add(text="texte important", model_id="m")
        history._conn.execute(
            "UPDATE entries SET segments = ? WHERE id = ?", ("{ pas du json", entry["id"])
        )
        history._conn.commit()

        recovered = history.get(entry["id"])
        assert recovered["text"] == "texte important"
        assert recovered["segments"] is None

    def test_la_recherche_plein_texte_trouve_le_texte_diarise(self, history: History) -> None:
        """Le texte etiquete est stocke tel quel : la recherche doit continuer
        de fonctionner dessus."""
        history.add(
            text="Locuteur 1 : le budget prévisionnel\nLocuteur 2 : d'accord",
            model_id="m",
            segments=[{"speaker": 0, "start": 0, "end": 1, "text": "le budget prévisionnel"}],
        )
        assert len(history.search("budget")) == 1
        assert len(history.search("prévisionnel")) == 1


class TestBase:
    def test_ajout_et_recherche(self, history: History) -> None:
        history.add(text="une note sur l isopropylique", model_id="m")
        assert len(history.search("iso")) == 1, "la recherche par prefixe doit marcher"

    def test_statistiques(self, history: History) -> None:
        history.add(text="a", model_id="m", audio_seconds=10.0)
        history.add(text="b", model_id="m", audio_seconds=5.5)
        stats = history.stats()
        assert stats["count"] == 2
        assert stats["total_audio_seconds"] == pytest.approx(15.5)

    def test_suppression(self, history: History) -> None:
        entry = history.add(text="a supprimer", model_id="m")
        history.delete(entry["id"])
        assert history.get(entry["id"]) is None

    def test_epinglage_remonte_l_entree(self, history: History) -> None:
        first = history.add(text="ancienne", model_id="m")
        history.add(text="recente", model_id="m")
        history.set_pinned(first["id"], True)
        assert history.search("")[0]["id"] == first["id"]
