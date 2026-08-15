"""Decoupage d'un signal long en morceaux coupes sur les silences.

L'encodeur Conformer de Parakeet a une attention quadratique : un seul passage sur
400 s coute enormement plus cher que dix passages de 40 s, et l'export ONNX casse
carrement au-dela d'environ 5000 trames. On decoupe donc, en placant chaque coupure
sur le point le plus silencieux d'une fenetre de recherche pour ne jamais trancher
au milieu d'un mot.

Sans effet sur la dictee : en dessous de `max_chunk_s`, le signal ressort tel quel.
"""

from __future__ import annotations

import numpy as np

from .engines.base import SAMPLE_RATE

FRAME_MS = 20
TARGET_CHUNK_S = 45.0
MAX_CHUNK_S = 60.0
SEARCH_WINDOW_S = 15.0


def _frame_energy(audio: np.ndarray, frame_len: int) -> np.ndarray:
    """Energie RMS par trame."""
    usable = (len(audio) // frame_len) * frame_len
    if usable == 0:
        return np.zeros(1, dtype=np.float32)
    frames = audio[:usable].reshape(-1, frame_len)
    return np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1)).astype(np.float32)


def split_on_silence(
    audio: np.ndarray,
    *,
    sample_rate: int = SAMPLE_RATE,
    target_chunk_s: float = TARGET_CHUNK_S,
    max_chunk_s: float = MAX_CHUNK_S,
    search_window_s: float = SEARCH_WINDOW_S,
) -> list[np.ndarray]:
    """Retourne une liste de morceaux contigus (sans recouvrement, donc sans doublon)."""
    if len(audio) <= int(max_chunk_s * sample_rate):
        return [audio]

    frame_len = int(sample_rate * FRAME_MS / 1000)
    energy = _frame_energy(audio, frame_len)

    target_frames = int(target_chunk_s * 1000 / FRAME_MS)
    window_frames = int(search_window_s * 1000 / FRAME_MS)
    max_frames = int(max_chunk_s * 1000 / FRAME_MS)

    cuts: list[int] = []
    pos = 0
    total = len(energy)

    while total - pos > max_frames:
        lo = pos + max(target_frames - window_frames, 1)
        hi = min(pos + max_frames, total - 1)
        # Le point le plus silencieux de la fenetre : la coupure la moins
        # destructrice. Fenetre vide — il ne reste pas de quoi en chercher une —
        # on coupe au plafond, faute de mieux.
        cut = lo + int(np.argmin(energy[lo:hi])) if hi > lo else hi
        cuts.append(cut)
        pos = cut

    chunks: list[np.ndarray] = []
    start_sample = 0
    for cut in cuts:
        end_sample = cut * frame_len
        chunks.append(audio[start_sample:end_sample])
        start_sample = end_sample
    chunks.append(audio[start_sample:])

    return [c for c in chunks if len(c) > 0]
