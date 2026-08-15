"""Conversion des jetons dates d'onnx-asr en mots.

Format verifie sur la sortie reelle de Parakeet v3 : un jeton qui commence par
une espace ouvre un mot, les suivants le prolongent, et chaque jeton ne porte
qu'un instant de DEBUT.

    tokens : [' J', 'u', 'ju', ' e', 'go', ' So']
    stamps : [0.40, 0.64, 0.72, 0.96, 1.12, 1.44]
    -> « Juju » [0.40, 0.96]   « ego » [0.96, 1.44]   « So » [1.44, ...]
"""

from __future__ import annotations

from murmure.engines.parakeet import words_from_tokens


class TestWordsFromTokens:
    def test_regroupe_les_sous_mots(self) -> None:
        words = words_from_tokens(
            [" J", "u", "ju", " e", "go", " So"],
            [0.40, 0.64, 0.72, 0.96, 1.12, 1.44],
            duration=2.0,
        )
        assert [x.text for x in words] == ["Juju", "ego", "So"]

    def test_un_mot_finit_ou_le_suivant_commence(self) -> None:
        words = words_from_tokens([" un", " deux"], [1.0, 2.0], duration=3.0)
        assert words[0].start == 1.0
        assert words[0].end == 2.0
        assert words[1].start == 2.0

    def test_le_dernier_mot_se_termine_avec_l_audio(self) -> None:
        words = words_from_tokens([" seul"], [1.0], duration=4.0)
        assert words[0].end == 4.0

    def test_sans_duree_le_dernier_mot_est_ponctuel(self) -> None:
        words = words_from_tokens([" seul"], [1.0])
        assert words[0].start == words[0].end == 1.0

    def test_le_decalage_replace_un_morceau_dans_l_enregistrement(self) -> None:
        """Les longs fichiers sont decoupes et chaque morceau redate a zero.
        Sans decalage, toute la reunion se superposerait sur ses premieres
        secondes et la diarisation attribuerait tout au premier locuteur."""
        words = words_from_tokens([" un", " deux"], [0.5, 1.5], offset=60.0, duration=2.0)
        assert words[0].start == 60.5
        assert words[1].start == 61.5

    def test_la_fin_n_est_jamais_avant_le_debut(self) -> None:
        """Les instants d'onnx-asr peuvent se repeter sur des jetons proches ;
        un intervalle inverse casserait le calcul de recouvrement."""
        words = words_from_tokens([" a", " b"], [2.0, 1.0], duration=3.0)
        assert all(x.end >= x.start for x in words)

    def test_ponctuation_collee_au_mot(self) -> None:
        words = words_from_tokens([" Pin", "."], [3.84, 4.08], duration=5.0)
        assert [x.text for x in words] == ["Pin."]

    def test_premier_jeton_sans_espace(self) -> None:
        """Certains decodages n'ouvrent pas par une espace : le premier jeton
        doit malgre tout ouvrir un mot au lieu d'etre perdu."""
        words = words_from_tokens(["Bon", "jour"], [0.1, 0.3], duration=1.0)
        assert [x.text for x in words] == ["Bonjour"]

    def test_entrees_absentes(self) -> None:
        assert words_from_tokens(None, None) == []
        assert words_from_tokens([], []) == []
        assert words_from_tokens([" a"], None) == []

    def test_jetons_vides_ecartes(self) -> None:
        words = words_from_tokens([" ", " vrai"], [0.1, 0.5], duration=1.0)
        assert [x.text for x in words] == ["vrai"]
