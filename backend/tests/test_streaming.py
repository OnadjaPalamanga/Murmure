"""Dictee continue : segmentation, coutures et fenetres de polissage.

Le vrai detecteur de parole (Silero, via faster-whisper) est remplace par une
porte deterministe qui decide sur l'amplitude. Ce qu'on verifie ici n'est pas la
qualite de la detection — c'est la machine a etats qui l'entoure, et elle doit
tenir des invariants exacts :

  * AUCUN ECHANTILLON NE PART DEUX FOIS AU MOTEUR. Un doublon, c'est un mot
    ecrit deux fois dans le document de l'utilisateur.
  * Les fenetres de polissage RECOUVRENT les phrases sans trou ni chevauchement.
    Un trou, c'est du texte affiche puis jamais remplace.

Ces deux proprietes sont annoncees par la documentation ; jusqu'ici rien ne les
empechait de casser silencieusement lors d'une modification du decoupage.
"""

from __future__ import annotations

from itertools import pairwise

import numpy as np
import pytest

import murmure.streaming as streaming
from murmure.engines.base import SAMPLE_RATE
from murmure.streaming import VAD_FRAME, PhraseStreamer, normalise_for_engine

SPEECH_AMPLITUDE = 0.4
BLOCK = SAMPLE_RATE * 20 // 1000  # 20 ms, comme les blocs du micro


class FakeVadGate:
    """Porte de decision deterministe, au meme contrat que `_VadGate`.

    Une decision par trame de `VAD_FRAME` echantillons consommee, dans l'ordre :
    c'est cette correspondance qui garde `_frame_pos` aligne sur le tampon.
    """

    def __init__(self, threshold: float = SPEECH_AMPLITUDE / 2) -> None:
        self.threshold = threshold
        self._pending = np.zeros(0, dtype=np.float32)

    def push(self, block: np.ndarray) -> list[bool]:
        self._pending = np.concatenate((self._pending, block))
        decisions = []
        while len(self._pending) >= VAD_FRAME:
            frame, self._pending = self._pending[:VAD_FRAME], self._pending[VAD_FRAME:]
            decisions.append(bool(np.abs(frame).max() > self.threshold))
        return decisions

    def drain(self) -> list[bool]:
        if len(self._pending) == 0:
            return []
        pad = np.zeros(VAD_FRAME - len(self._pending), dtype=np.float32)
        return self.push(pad)


@pytest.fixture
def fake_vad(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(streaming, "_VadGate", FakeVadGate)


def build_audio(pattern: list[tuple[str, float]], *, seed: int = 0) -> np.ndarray:
    """Assemble parole et silence. La parole est un bruit unique : chaque morceau
    remis au moteur peut donc etre relocalise exactement dans la source."""
    rng = np.random.default_rng(seed)
    pieces = []
    for kind, seconds in pattern:
        length = int(seconds * SAMPLE_RATE)
        if kind == "speech":
            pieces.append((rng.standard_normal(length) * SPEECH_AMPLITUDE).astype(np.float32))
        else:
            pieces.append(np.zeros(length, dtype=np.float32))
    return np.concatenate(pieces)


def locate(chunk: np.ndarray, source: np.ndarray) -> int:
    """Position absolue de `chunk` dans `source`.

    Les morceaux sont des tranches contigues d'un bruit aleatoire : la premiere
    valeur non nulle suffit a les situer sans ambiguite.
    """
    non_zero = np.flatnonzero(chunk)
    if non_zero.size == 0:
        return -1
    index = int(non_zero[0])
    matches = np.flatnonzero(source == chunk[index])
    assert matches.size == 1, "signal ambigu : le test ne peut pas situer le morceau"
    return int(matches[0]) - index


def run_stream(audio: np.ndarray, **kwargs) -> dict:
    """Pousse l'audio bloc par bloc, comme le ferait le micro, et collecte tout."""
    phrases: list[np.ndarray] = []
    windows: list[np.ndarray] = []
    commits: list[tuple[str, int]] = []
    revisions: list[tuple[str, int, int]] = []

    def transcribe(chunk: np.ndarray) -> str:
        phrases.append(chunk.copy())
        return f"phrase{len(phrases)}"

    absorbees = {"n": 0}

    def polish(chunk: np.ndarray) -> str:
        """Rend un texte de meme longueur que celui qu'il remplace.

        `polish.choose` refuse un polissage dont le nombre de mots sort des
        bornes de vraisemblance : un marqueur d'un seul mot pour une fenetre de
        deux phrases serait ecarte, et le test verifierait le repli au lieu du
        chemin qu'il vise.
        """
        windows.append(chunk.copy())
        couvertes = len(commits) - absorbees["n"]
        absorbees["n"] = len(commits)
        return " ".join(f"fenetre{len(windows)}.{k}" for k in range(max(1, couvertes)))

    streamer = PhraseStreamer(
        transcribe=transcribe,
        on_phrase=lambda text, index: commits.append((text, index)),
        polish=polish,
        on_revise=lambda text, first, last: revisions.append((text, first, last)),
        **kwargs,
    )
    streamer.start()
    for offset in range(0, len(audio), BLOCK):
        streamer.feed(audio[offset : offset + BLOCK])
    result = streamer.finish(timeout=30.0)

    return {
        "phrases": phrases,
        "windows": windows,
        "commits": commits,
        "revisions": revisions,
        "result": result,
    }


class TestNormaliseForEngine:
    def test_remonte_une_phrase_faible(self) -> None:
        faible = np.full(1000, 0.02, dtype=np.float32)
        assert float(np.abs(normalise_for_engine(faible)).max()) > 0.3

    def test_n_attenue_jamais_un_signal_correct(self) -> None:
        fort = np.full(1000, 0.9, dtype=np.float32)
        assert np.array_equal(normalise_for_engine(fort), fort)

    def test_ne_fabrique_pas_de_parole_a_partir_du_silence(self) -> None:
        """Le gain est plafonne : amplifier sans limite un signal quasi nul
        remonterait le bruit de fond au niveau de la parole."""
        tres_faible = np.full(1000, 1e-6, dtype=np.float32)
        assert float(np.abs(normalise_for_engine(tres_faible)).max()) < 0.01

    def test_silence_absolu_intact(self) -> None:
        silence = np.zeros(1000, dtype=np.float32)
        assert np.array_equal(normalise_for_engine(silence), silence)

    def test_jamais_de_saturation(self) -> None:
        signal = np.array([0.001, -0.001, 0.05], dtype=np.float32)
        assert float(np.abs(normalise_for_engine(signal)).max()) <= 1.0


@pytest.mark.usefixtures("fake_vad")
class TestSegmentation:
    def test_les_phrases_sont_decoupees_sur_les_silences(self) -> None:
        audio = build_audio(
            [
                ("silence", 0.5),
                ("speech", 3.0),
                ("silence", 2.0),
                ("speech", 3.0),
                ("silence", 2.0),
            ]
        )
        run = run_stream(audio)
        assert len(run["phrases"]) == 2
        assert [text for text, _ in run["commits"]] == ["phrase1", "phrase2"]

    def test_aucun_echantillon_ne_part_deux_fois_au_moteur(self) -> None:
        """L'invariant central. Deux phrases qui se chevauchent, c'est du texte
        ecrit deux fois dans le document."""
        audio = build_audio(
            [
                ("silence", 0.5),
                ("speech", 3.0),
                ("silence", 2.0),
                ("speech", 4.0),
                ("silence", 2.0),
                ("speech", 3.0),
                ("silence", 2.0),
            ]
        )
        run = run_stream(audio)
        assert len(run["phrases"]) >= 2

        spans = []
        for chunk in run["phrases"]:
            start = locate(chunk, audio)
            assert start >= 0, "une phrase ne contient que du silence"
            spans.append((start, start + len(chunk)))

        for (_, fin), (debut_suivant, _) in pairwise(spans):
            assert fin <= debut_suivant, f"chevauchement : {fin} > {debut_suivant}"

    def test_une_hesitation_ne_ferme_pas_la_phrase(self) -> None:
        """En dessous de MIN_COMMIT_S de parole, un silence ordinaire ne valide
        rien : un fragment isole est indecodable et ressort en charabia."""
        audio = build_audio(
            [
                ("silence", 0.5),
                ("speech", 1.0),
                ("silence", 0.9),  # au-dela de SILENCE_MS, sous LONG_SILENCE_MS
                ("speech", 2.5),
                ("silence", 2.5),
            ]
        )
        run = run_stream(audio)
        assert len(run["phrases"]) == 1, "l'hesitation a ete prise pour une fin de phrase"

    def test_un_monologue_est_coupe_au_plafond(self) -> None:
        """Sans silence, `max_phrase_s` tranche : sinon rien ne sortirait jamais."""
        audio = build_audio([("silence", 0.5), ("speech", 14.0), ("silence", 2.0)])
        run = run_stream(audio, max_phrase_s=5.0)
        assert len(run["phrases"]) >= 2

    def test_pas_de_doublon_apres_une_coupe_au_plafond(self) -> None:
        """Le cas ou la couture est la plus fragile : la phrase suivante reprend
        exactement ou la precedente s'arrete, sans marge d'attaque en arriere."""
        audio = build_audio([("silence", 0.5), ("speech", 14.0), ("silence", 2.0)])
        run = run_stream(audio, max_phrase_s=5.0)

        spans = []
        for chunk in run["phrases"]:
            start = locate(chunk, audio)
            assert start >= 0
            spans.append((start, start + len(chunk)))

        for (_, fin), (debut_suivant, _) in pairwise(spans):
            assert fin <= debut_suivant, f"doublon a la couture : {fin} > {debut_suivant}"

    def test_le_silence_seul_ne_produit_rien(self) -> None:
        run = run_stream(build_audio([("silence", 5.0)]))
        assert run["phrases"] == []
        assert run["commits"] == []


@pytest.mark.usefixtures("fake_vad")
class TestPolissage:
    def test_les_fenetres_recouvrent_les_phrases_sans_trou(self) -> None:
        """Contrat exact vu par le frontend : chaque fenetre reprend a la phrase
        suivant celle ou s'arretait la precedente, et la derniere va au bout."""
        audio = build_audio(
            [
                ("silence", 0.5),
                ("speech", 3.0),
                ("silence", 0.9),
                ("speech", 3.0),
                ("silence", 2.5),  # vraie pause : declenche le polissage
                ("speech", 3.0),
                ("silence", 2.5),
            ]
        )
        run = run_stream(audio)
        commits, revisions = run["commits"], run["revisions"]
        assert commits and revisions

        attendu = 1
        for _, first, last in revisions:
            assert first == attendu, f"trou ou chevauchement a la fenetre {first}-{last}"
            attendu = last + 1
        assert attendu == len(commits) + 1, "les dernieres phrases n'ont ete polies par personne"

    def test_une_fenetre_d_une_seule_phrase_n_est_pas_re_decodee(self) -> None:
        """Ses echantillons sont exactement ceux deja transcrits : le calcul
        rendrait le meme texte au meme prix. Une revision est quand meme emise,
        c'est elle qui fait passer le texte au curseur."""
        audio = build_audio(
            [
                ("silence", 0.5),
                ("speech", 3.0),
                ("silence", 2.5),
                ("speech", 3.0),
                ("silence", 2.5),
            ]
        )
        run = run_stream(audio)
        assert len(run["commits"]) == 2
        assert len(run["revisions"]) == 2
        assert run["windows"] == [], "une fenetre d'une phrase a ete re-decodee pour rien"

    def test_une_fenetre_de_plusieurs_phrases_est_re_decodee(self) -> None:
        audio = build_audio(
            [
                ("silence", 0.5),
                ("speech", 3.0),
                ("silence", 0.9),
                ("speech", 3.0),
                ("silence", 2.5),
            ]
        )
        run = run_stream(audio)
        assert len(run["commits"]) == 2
        assert len(run["windows"]) == 1, "les deux phrases auraient du repartir d'un bloc"
        # Une seule fenetre a la place des deux phrases : c'est son texte qui
        # fait foi, pas leur concatenation.
        assert run["result"] == ["fenetre1.0 fenetre1.1"]

    def test_le_resultat_prefere_les_fenetres_polies(self) -> None:
        audio = build_audio(
            [("silence", 0.5), ("speech", 3.0), ("silence", 0.9), ("speech", 3.0), ("silence", 2.5)]
        )
        run = run_stream(audio)
        # Le texte retenu est celui des fenetres, pas la suite des phrases.
        assert "phrase1" not in run["result"]

    def test_sans_polissage_le_resultat_est_la_suite_des_phrases(self) -> None:
        audio = build_audio(
            [("silence", 0.5), ("speech", 3.0), ("silence", 2.5), ("speech", 3.0), ("silence", 2.5)]
        )
        phrases: list[np.ndarray] = []

        def transcribe(chunk: np.ndarray) -> str:
            phrases.append(chunk)
            return f"phrase{len(phrases)}"

        streamer = PhraseStreamer(transcribe=transcribe, on_phrase=lambda text, index: None)
        streamer.start()
        for offset in range(0, len(audio), BLOCK):
            streamer.feed(audio[offset : offset + BLOCK])
        assert streamer.finish(timeout=30.0) == ["phrase1", "phrase2"]


@pytest.mark.usefixtures("fake_vad")
class TestCycleDeVie:
    def test_annuler_ne_rend_aucun_texte(self) -> None:
        audio = build_audio([("silence", 0.5), ("speech", 3.0), ("silence", 2.5)])
        streamer = PhraseStreamer(
            transcribe=lambda chunk: "du texte", on_phrase=lambda text, index: None
        )
        streamer.start()
        for offset in range(0, len(audio), BLOCK):
            streamer.feed(audio[offset : offset + BLOCK])
        streamer.cancel()
        assert streamer.finish(timeout=30.0) == []

    def test_un_moteur_qui_leve_n_arrete_pas_la_dictee(self) -> None:
        """Une phrase perdue vaut mieux qu'un thread mort : les suivantes
        doivent continuer d'arriver."""
        audio = build_audio(
            [("silence", 0.5), ("speech", 3.0), ("silence", 2.5), ("speech", 3.0), ("silence", 2.5)]
        )
        appels = {"n": 0}
        erreurs: list[Exception] = []

        def transcribe(chunk: np.ndarray) -> str:
            appels["n"] += 1
            if appels["n"] == 1:
                raise RuntimeError("moteur indisponible")
            return "phrase suivante"

        streamer = PhraseStreamer(
            transcribe=transcribe,
            on_phrase=lambda text, index: None,
            on_error=erreurs.append,
        )
        streamer.start()
        for offset in range(0, len(audio), BLOCK):
            streamer.feed(audio[offset : offset + BLOCK])
        result = streamer.finish(timeout=30.0)

        assert len(erreurs) == 1
        assert result == ["phrase suivante"]

    def test_un_hook_qui_leve_n_arrete_pas_la_dictee(self) -> None:
        audio = build_audio([("silence", 0.5), ("speech", 3.0), ("silence", 2.5)])

        def on_phrase(text: str, index: int) -> None:
            raise RuntimeError("le frontend a lache")

        streamer = PhraseStreamer(transcribe=lambda chunk: "du texte", on_phrase=on_phrase)
        streamer.start()
        for offset in range(0, len(audio), BLOCK):
            streamer.feed(audio[offset : offset + BLOCK])
        assert streamer.finish(timeout=30.0) == ["du texte"]
