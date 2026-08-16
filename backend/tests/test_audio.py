"""Le tampon de pre-roll et le plafond d'enregistrement.

Aucun peripherique n'est ouvert : on manipule directement le tampon et on
appelle `stop()`, qui est la ou les deux defauts vivaient.
"""

from __future__ import annotations

import numpy as np
import pytest

from murmure.audio import BLOCK_MS, MAX_RECORD_S, Recorder
from murmure.engines.base import SAMPLE_RATE


@pytest.fixture
def recorder() -> Recorder:
    return Recorder(preroll_ms=400)


class TestPreRoll:
    def test_la_profondeur_suit_le_reglage_initial(self) -> None:
        assert Recorder(preroll_ms=400)._preroll.maxlen == 400 // BLOCK_MS
        assert Recorder(preroll_ms=1000)._preroll.maxlen == 1000 // BLOCK_MS

    def test_changer_le_reglage_redimensionne_le_tampon(self, recorder) -> None:
        # Le defaut du bogue : `preroll_ms` etait un simple attribut, le
        # `maxlen` de la deque restait celui de la construction, et deplacer le
        # curseur dans les reglages n'avait aucun effet jusqu'au redemarrage.
        recorder.preroll_ms = 1000
        assert recorder._preroll.maxlen == 50
        assert recorder.preroll_ms == 1000

    def test_retrecir_garde_les_blocs_les_plus_recents(self, recorder) -> None:
        for index in range(20):
            recorder._preroll.append(np.full(4, index, dtype=np.float32))

        recorder.preroll_ms = 100  # 5 blocs
        assert recorder._preroll.maxlen == 5
        # Un pre-roll plus court, c'est « garde moins de passe » : ce sont les
        # derniers blocs qui restent, pas les premiers.
        assert [int(block[0]) for block in recorder._preroll] == [15, 16, 17, 18, 19]

    def test_agrandir_conserve_ce_qui_etait_la(self, recorder) -> None:
        for index in range(5):
            recorder._preroll.append(np.full(4, index, dtype=np.float32))

        recorder.preroll_ms = 1000
        assert [int(block[0]) for block in recorder._preroll] == [0, 1, 2, 3, 4]

    def test_une_valeur_identique_ne_reconstruit_rien(self, recorder) -> None:
        before = recorder._preroll
        recorder.preroll_ms = 400
        assert recorder._preroll is before

    def test_le_tampon_garde_au_moins_un_bloc(self) -> None:
        assert Recorder(preroll_ms=0)._preroll.maxlen == 1


class TestPlafondDEnregistrement:
    def _record(self, recorder: Recorder, seconds: float) -> np.ndarray:
        recorder.is_recording = True
        recorder._captured = [np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)]
        return recorder.stop()

    def test_une_dictee_courte_ressort_entiere(self, recorder) -> None:
        audio = self._record(recorder, 5)
        assert len(audio) == 5 * SAMPLE_RATE

    def test_au_dela_du_plafond_l_audio_est_tronque(self, recorder) -> None:
        audio = self._record(recorder, MAX_RECORD_S + 30)
        assert len(audio) == int(MAX_RECORD_S * SAMPLE_RATE)

    def test_la_troncature_est_annoncee(self) -> None:
        # C'est tout l'objet du correctif : la coupe avait lieu en silence, et
        # l'utilisateur decouvrait une transcription qui s'arretait au milieu
        # d'une phrase sans que rien ne l'explique.
        seen: list[tuple[float, float]] = []
        recorder = Recorder(on_truncated=lambda limit, lost: seen.append((limit, lost)))
        self._record(recorder, MAX_RECORD_S + 30)

        assert len(seen) == 1
        limit, lost = seen[0]
        assert limit == MAX_RECORD_S
        assert lost == pytest.approx(30, abs=0.1)

    def test_rien_n_est_annonce_sous_le_plafond(self) -> None:
        seen = []
        recorder = Recorder(on_truncated=lambda limit, lost: seen.append(limit))
        self._record(recorder, 10)
        assert seen == []

    def test_un_hook_qui_leve_ne_perd_pas_l_audio(self) -> None:
        def boom(limit: float, lost: float) -> None:
            raise RuntimeError("abonne casse")

        recorder = Recorder(on_truncated=boom)
        audio = self._record(recorder, MAX_RECORD_S + 5)
        # L'audio prime sur la notification, toujours.
        assert len(audio) == int(MAX_RECORD_S * SAMPLE_RATE)
