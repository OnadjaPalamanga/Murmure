"""Garde-fous du re-decodage : coutures et refus.

Ces fonctions sont pures, sans modele : ce sont les seules du chemin de la
dictee continue qu'on peut verifier exactement plutot qu'a l'oreille.
"""

from __future__ import annotations

import pytest

from murmure.polish import MAX_SEAM_WORDS, choose, merge_seam, words


class TestWords:
    def test_ponctuation_et_casse_ignorees(self) -> None:
        assert words("C'est une vision, du contenu.") == ["cest", "une", "vision", "du", "contenu"]

    def test_jetons_purement_typographiques_ecartes(self) -> None:
        assert words("... — ?") == []

    def test_texte_vide(self) -> None:
        assert words("") == []


class TestMergeSeam:
    def test_retire_le_chevauchement(self) -> None:
        previous = "je vais essayer de faire une comparaison"
        new = "une comparaison ou a certaines epoques"
        assert merge_seam(previous, new) == "ou a certaines epoques"

    def test_compare_hors_ponctuation_et_casse(self) -> None:
        """La fenetre precedente ferme par un point la ou la suivante ouvre par
        une majuscule : c'est le meme mot, il ne doit sortir qu'une fois."""
        assert merge_seam("une vision du contenu.", "Contenu extremement riche") == (
            "extremement riche"
        )

    def test_sans_chevauchement_le_texte_est_intact(self) -> None:
        assert merge_seam("premiere phrase", "deuxieme phrase entiere") == (
            "deuxieme phrase entiere"
        )

    def test_prend_le_plus_long_chevauchement(self) -> None:
        # "de la" et "la" conviennent tous deux ; c'est le plus long qui compte,
        # sans quoi "de" resterait en double.
        assert merge_seam("au coeur de la", "de la question") == "question"

    def test_borne_a_max_seam_words(self) -> None:
        """Au-dela, ce n'est plus une marge conservee mais un decoupage casse :
        on ne mange pas un morceau entier de la fenetre."""
        commun = " ".join(f"mot{i}" for i in range(MAX_SEAM_WORDS + 4))
        assert merge_seam(commun, f"{commun} suite") != "suite"

    def test_une_couture_purement_typographique_ne_mange_rien(self) -> None:
        """`all(tail)` dans le code : une suite de jetons sans lettre ni chiffre
        se comparerait egale a n'importe quoi et emporterait le debut."""
        assert merge_seam("une phrase ...", "... la suite du texte") == "... la suite du texte"

    @pytest.mark.parametrize(("previous", "new"), [("", "du texte"), ("du texte", "")])
    def test_texte_vide_d_un_cote(self, previous: str, new: str) -> None:
        assert merge_seam(previous, new) == new


class TestChoose:
    def test_un_polissage_plausible_est_retenu(self) -> None:
        poli = "C'est une vision du contenu extremement riche."
        brut = "C'est une vision du contenu. Extremement riche."
        assert choose(poli, brut) == poli

    def test_un_polissage_vide_est_refuse(self) -> None:
        brut = "un texte que l'utilisateur a deja vu tomber"
        assert choose("", brut) == brut

    def test_une_boucle_de_repetition_est_refusee(self) -> None:
        brut = "la question de la ponctuation"
        boucle = " ".join(["la question"] * 20)
        assert choose(boucle, brut) == brut

    def test_une_troncature_severe_est_refusee(self) -> None:
        brut = " ".join(f"mot{i}" for i in range(20))
        assert choose("mot0 mot1", brut) == brut

    def test_retrait_legitime_de_reprises_accepte(self) -> None:
        """Un re-decodage contextuel a le droit de supprimer les hesitations :
        les bornes sont larges expres, elles ne visent que le derapage."""
        brut = "c'est c'est une vision une vision du contenu"
        poli = "c'est une vision du contenu"
        assert choose(poli, brut) == poli

    def test_brut_vide_laisse_passer_le_poli(self) -> None:
        assert choose("du texte retrouve", "") == "du texte retrouve"

    def test_les_deux_vides(self) -> None:
        assert choose("", "") == ""
