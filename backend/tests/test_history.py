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


MOTS = [
    {"start": 0.0, "end": 0.4, "text": "bonjour", "speaker": 0},
    {"start": 0.5, "end": 0.9, "text": "monde", "speaker": 0},
]


class TestMotsDates:
    """Les mots dates, qui rendent une entree exportable en sous-titres.

    Ils sont volumineux — une heure d'audio fait dix mille mots — et c'est ce
    volume qui dicte leur traitement : ecrits une fois, jamais renvoyes avec une
    liste, relus seulement a l'export.
    """

    def test_les_mots_sont_relus_tels_qu_ecrits(self, history: History) -> None:
        entry = history.add(text="bonjour monde", model_id="m", words=MOTS)
        assert history.get(entry["id"])["words"] == MOTS

    def test_une_recherche_ne_renvoie_jamais_les_mots(self, history: History) -> None:
        """Deux cents entrees d'une heure feraient traverser des dizaines de
        megaoctets au WebSocket pour n'afficher qu'un bouton d'export."""
        history.add(text="bonjour monde", model_id="m", words=MOTS)
        found = history.search("")[0]
        assert "words" not in found
        assert found["has_words"] is True

    def test_l_ajout_ne_renvoie_pas_les_mots_non_plus(self, history: History) -> None:
        """La valeur rendue par `add` part directement dans l'evenement
        `file_done`, vers la meme interface."""
        entry = history.add(text="bonjour monde", model_id="m", words=MOTS)
        assert "words" not in entry
        assert entry["has_words"] is True

    def test_une_entree_sans_mots_le_dit(self, history: History) -> None:
        history.add(text="dictee au vol", model_id="m")
        found = history.search("")[0]
        assert found["has_words"] is False
        assert history.get(found["id"])["words"] is None

    def test_la_recherche_plein_texte_expose_le_meme_drapeau(self, history: History) -> None:
        """La requete FTS passe par une jointure et une autre liste de colonnes :
        c'est un second chemin, qui peut oublier `has_words` tout seul."""
        history.add(text="le budget previsionnel", model_id="m", words=MOTS)
        found = history.search("budget")[0]
        assert found["has_words"] is True
        assert "words" not in found

    def test_des_mots_illisibles_ne_font_pas_perdre_la_dictee(self, history: History) -> None:
        """Meme garantie que pour les tours de parole : on perd les mots, pas la
        transcription."""
        entry = history.add(text="une dictee", model_id="m", words=MOTS)
        history._conn.execute(
            "UPDATE entries SET words = ? WHERE id = ?", ("{ pas du json", entry["id"])
        )
        history._conn.commit()
        relu = history.get(entry["id"])
        assert relu["text"] == "une dictee"
        assert relu["words"] is None
