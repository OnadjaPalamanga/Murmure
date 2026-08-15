"""Decoupage des longs enregistrements sur les silences.

Ce que ces tests tiennent : les morceaux se recollent EXACTEMENT en l'original.
Un recouvrement ferait transcrire deux fois les memes mots, un trou en perdrait.
"""

from __future__ import annotations

import numpy as np

from murmure.chunking import MAX_CHUNK_S, split_on_silence
from murmure.engines.base import SAMPLE_RATE


def _bruit(seconds: float, *, amplitude: float = 0.5, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(int(seconds * SAMPLE_RATE)) * amplitude).astype(np.float32)


def test_signal_court_ressort_tel_quel() -> None:
    """Le cas de la dictee : en dessous du plafond, aucun decoupage."""
    audio = _bruit(30.0)
    chunks = split_on_silence(audio)
    assert len(chunks) == 1
    assert np.array_equal(chunks[0], audio)


def test_les_morceaux_se_recollent_a_l_identique() -> None:
    audio = _bruit(200.0)
    chunks = split_on_silence(audio)
    assert len(chunks) > 1
    assert np.array_equal(np.concatenate(chunks), audio)


def test_aucun_morceau_ne_depasse_le_plafond() -> None:
    audio = _bruit(300.0)
    for chunk in split_on_silence(audio):
        assert len(chunk) <= int(MAX_CHUNK_S * SAMPLE_RATE)


def test_aucun_morceau_vide() -> None:
    """Un morceau vide partirait au moteur pour rien et pourrait le faire lever."""
    assert all(len(c) > 0 for c in split_on_silence(_bruit(250.0)))


def test_la_coupure_tombe_dans_le_silence() -> None:
    """On coupe au creux d'energie, jamais au milieu d'un mot : c'est toute la
    raison d'etre du module."""
    parole = _bruit(50.0, amplitude=0.5)
    silence = np.zeros(int(3.0 * SAMPLE_RATE), dtype=np.float32)
    audio = np.concatenate([parole, silence, _bruit(50.0, amplitude=0.5, seed=1)])

    chunks = split_on_silence(audio)
    assert len(chunks) > 1

    frontiere = len(chunks[0])
    debut_silence, fin_silence = len(parole), len(parole) + len(silence)
    assert debut_silence <= frontiere <= fin_silence


def test_signal_vide() -> None:
    chunks = split_on_silence(np.zeros(0, dtype=np.float32))
    assert len(chunks) == 1
    assert chunks[0].size == 0
