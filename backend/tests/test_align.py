"""Attribution des mots aux locuteurs.

C'est le calcul le plus silencieux de toute la chaine : un mot mis au mauvais
locuteur ne leve aucune erreur, ne ralentit rien, et ne se voit qu'a la lecture
— quand la transcription fait dire a quelqu'un ce qu'il n'a pas dit. D'ou une
couverture serree, y compris sur les cas limites que la diarisation produit
vraiment (chevauchements, mots hors de tout tour, silences).
"""

from __future__ import annotations

import pytest

from murmure.align import (
    SpeakerBlock,
    SpeakerSegment,
    assign_speakers,
    count_speakers,
    format_transcript,
    speaker_for,
    speaker_label,
)
from murmure.engines.base import Word


def w(start: float, end: float, text: str = "mot") -> Word:
    return Word(start, end, text)


def seg(start: float, end: float, speaker: int) -> SpeakerSegment:
    return SpeakerSegment(start, end, speaker)


class TestSpeakerFor:
    def test_mot_entierement_dans_un_tour(self) -> None:
        assert speaker_for(w(1.0, 2.0), [seg(0.0, 5.0, 0)]) == 0

    def test_recouvrement_maximal_et_non_instant_de_debut(self) -> None:
        """Le mot commence pendant le locuteur 0 mais appartient au 1.

        C'est le cas des prises de parole qui s'enchainent : dater sur le debut
        attribuerait le mot a celui qui finit sa phrase.
        """
        segments = [seg(0.0, 1.1, 0), seg(1.0, 3.0, 1)]
        assert speaker_for(w(1.0, 2.5), segments) == 1

    def test_mot_entre_deux_tours_va_au_plus_proche(self) -> None:
        segments = [seg(0.0, 1.0, 0), seg(5.0, 8.0, 1)]
        assert speaker_for(w(1.2, 1.4), segments) == 0

    def test_mot_trop_loin_de_tout_tour_reste_sans_locuteur(self) -> None:
        """Au-dela de la tolerance c'est du silence ou de la musique : inventer
        un locuteur serait pire que ne rien dire."""
        segments = [seg(0.0, 1.0, 0)]
        assert speaker_for(w(60.0, 61.0), segments) is None

    def test_sans_aucun_tour(self) -> None:
        assert speaker_for(w(0.0, 1.0), []) is None

    def test_mot_de_duree_nulle(self) -> None:
        """Un jeton unique peut avoir debut == fin ; il doit quand meme etre
        attribue, par proximite a defaut de recouvrement."""
        assert speaker_for(w(2.0, 2.0), [seg(0.0, 5.0, 3)]) == 3


class TestAssignSpeakers:
    def test_regroupe_les_mots_consecutifs(self) -> None:
        words = [w(0.1, 0.5, "bonjour"), w(0.6, 1.0, "tout"), w(1.1, 1.5, "monde")]
        blocks = assign_speakers(words, [seg(0.0, 2.0, 0)])
        assert len(blocks) == 1
        assert blocks[0].text == "bonjour tout monde"
        assert blocks[0].speaker == 0

    def test_change_de_bloc_au_changement_de_locuteur(self) -> None:
        words = [w(0.1, 0.9, "salut"), w(1.2, 1.9, "bonjour"), w(2.2, 2.9, "oui")]
        segments = [seg(0.0, 1.0, 0), seg(1.1, 2.0, 1), seg(2.1, 3.0, 0)]
        blocks = assign_speakers(words, segments)
        assert [b.speaker for b in blocks] == [0, 1, 0]
        assert [b.text for b in blocks] == ["salut", "bonjour", "oui"]

    def test_un_locuteur_qui_reprend_la_parole_fait_un_nouveau_bloc(self) -> None:
        """On ne fusionne pas tout ce qu'une personne a dit : l'ordre de la
        conversation est justement ce qu'on veut lire."""
        words = [w(0.1, 0.9, "un"), w(1.2, 1.9, "deux"), w(2.2, 2.9, "trois")]
        segments = [seg(0.0, 1.0, 0), seg(1.1, 2.0, 1), seg(2.1, 3.0, 0)]
        assert len(assign_speakers(words, segments)) == 3

    def test_les_bornes_du_bloc_couvrent_ses_mots(self) -> None:
        words = [w(1.0, 1.4, "a"), w(2.0, 2.6, "b")]
        block = assign_speakers(words, [seg(0.0, 5.0, 0)])[0]
        assert block.start == 1.0
        assert block.end == 2.6

    def test_sans_diarisation_tout_reste_dans_un_bloc_sans_locuteur(self) -> None:
        """Repli : si la diarisation n'a rien rendu, on ne perd pas le texte."""
        words = [w(0.0, 1.0, "un"), w(1.0, 2.0, "deux")]
        blocks = assign_speakers(words, [])
        assert len(blocks) == 1
        assert blocks[0].speaker is None
        assert blocks[0].text == "un deux"

    def test_sans_mots(self) -> None:
        assert assign_speakers([], [seg(0.0, 1.0, 0)]) == []

    def test_l_ordre_des_mots_est_preserve(self) -> None:
        words = [w(i * 1.0, i * 1.0 + 0.5, f"mot{i}") for i in range(6)]
        blocks = assign_speakers(words, [seg(0.0, 10.0, 0)])
        assert blocks[0].text == "mot0 mot1 mot2 mot3 mot4 mot5"

    def test_aucun_mot_n_est_perdu(self) -> None:
        """Invariant : la concatenation des blocs rend tous les mots d'entree,
        dans le meme ordre. Un mot perdu serait du texte disparu."""
        words = [w(i * 0.7, i * 0.7 + 0.5, f"m{i}") for i in range(20)]
        segments = [seg(0.0, 5.0, 0), seg(5.0, 9.0, 1), seg(9.0, 15.0, 2)]
        blocks = assign_speakers(words, segments)
        rendus = [word.text for block in blocks for word in block.words]
        assert rendus == [word.text for word in words]


class TestFormatting:
    def test_numerotation_a_partir_de_un(self) -> None:
        """sherpa-onnx numerote a partir de 0, ce qui ne parle qu'a une machine."""
        assert speaker_label(0) == "Locuteur 1"
        assert speaker_label(3) == "Locuteur 4"

    def test_locuteur_inconnu(self) -> None:
        assert speaker_label(None) == "Locuteur ?"

    def test_transcription_lisible(self) -> None:
        blocks = [
            SpeakerBlock(0, 0.0, 1.0, "bonjour à tous"),
            SpeakerBlock(1, 1.2, 2.0, "bonjour"),
        ]
        assert format_transcript(blocks) == "Locuteur 1 : bonjour à tous\nLocuteur 2 : bonjour"

    def test_les_blocs_vides_sont_ecartes(self) -> None:
        blocks = [SpeakerBlock(0, 0.0, 1.0, "texte"), SpeakerBlock(1, 1.0, 2.0, "   ")]
        assert format_transcript(blocks) == "Locuteur 1 : texte"

    def test_comptage_des_locuteurs(self) -> None:
        blocks = [
            SpeakerBlock(0, 0.0, 1.0, "a"),
            SpeakerBlock(1, 1.0, 2.0, "b"),
            SpeakerBlock(0, 2.0, 3.0, "c"),
            SpeakerBlock(None, 3.0, 4.0, "d"),
        ]
        assert count_speakers(blocks) == 2

    @pytest.mark.parametrize("blocks", [[], [SpeakerBlock(None, 0.0, 1.0, "")]])
    def test_transcription_vide(self, blocks: list[SpeakerBlock]) -> None:
        assert format_transcript(blocks) == ""

    def test_serialisation_pour_l_historique(self) -> None:
        block = SpeakerBlock(1, 1.234, 2.567, "du texte")
        assert block.to_dict() == {
            "speaker": 1,
            "start": 1.23,
            "end": 2.57,
            "text": "du texte",
        }
