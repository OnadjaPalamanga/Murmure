"""Dictee continue : le texte tombe phrase par phrase pendant qu'on parle.

Whisper n'est pas un modele de streaming — c'est un encodeur-decodeur entraine
sur des fenetres de 30 s, sans aucune notion d'hypothese partielle. Decouper
l'audio a intervalle fixe donnerait des mots tranches et, surtout, une
ponctuation et des anglicismes en vrac : c'est le contexte de phrase entiere qui
empeche « meeting » de devenir « mitine ».

On decoupe donc sur les FRONTIERES DE PHRASE, pas sur une horloge. Chaque
morceau envoye au moteur est un groupe de souffle complet, borne par du silence :
le modele travaille dans les memes conditions qu'en mode differe, et la qualite
est la meme. Ce qu'on gagne, c'est de voir le texte arriver ; ce qu'on perd,
c'est la relecture avant insertion.

Trois etages, chacun sur son thread, pour que rien de lourd ne remonte jusqu'au
thread temps reel de PortAudio :

    feed()      thread audio    empile les blocs, rien d'autre
    _pump       thread VAD      Silero + machine a etats, decoupe les phrases
    _worker     thread moteur   transcrit, dans l'ordre d'emission

Le worker est unique et sequentiel : c'est ce qui garantit qu'une phrase courte
finie avant une longue ne passe pas devant elle.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable

import numpy as np

from .engines.base import SAMPLE_RATE

log = logging.getLogger(__name__)

# Silero travaille sur des trames de 512 echantillons, soit 32 ms a 16 kHz.
VAD_FRAME = 512
FRAME_MS = VAD_FRAME * 1000 // SAMPLE_RATE

# Le modele est un LSTM dont l'etat est remis a zero a chaque appel. On lui
# redonne donc un peu d'audio deja vu avant les trames a juger, le temps qu'il
# se stabilise ; ces trames de chauffe sont ensuite jetees.
WARMUP_FRAMES = 16  # 512 ms
STEP_FRAMES = 8  # on decide toutes les 256 ms

# Seuils Silero, avec hysteresis : il faut etre plus convaincant pour ouvrir une
# phrase que pour la prolonger. Sans ca, une syllabe faible en milieu de mot
# suffit a couper.
OPEN_PROB = 0.5
KEEP_PROB = 0.35

# Silero attend un niveau de parole ordinaire. Sur un micro faible il ne
# s'accroche pas et hache la phrase en bribes — mesure sur un enregistrement a
# 0,014 de pic : 1,5 s de parole detectee sur 6,5 s, decoupees en sept morceaux
# dont le moteur ne tirait qu'une hallucination. On lui presente donc un signal
# ramene a un niveau utile. Le gain suit un pic glissant plutot que la fenetre
# courante, sinon chaque respiration serait remontee au niveau de la parole.
# En dessous du plancher on ne fait rien : amplifier du silence, c'est fabriquer
# de la fausse parole. Le moteur, lui, recoit toujours l'audio d'origine.
VAD_TARGET_PEAK = 0.25
VAD_GAIN_FLOOR = 0.003
VAD_MAX_GAIN = 25.0
VAD_PEAK_DECAY = 0.9  # par fenetre d'analyse, soit une demi-vie d'environ 1,7 s

SILENCE_MS = 700
# Une hesitation n'est pas une fin de phrase. En dessous de cette duree de
# parole accumulee on ne valide pas sur un simple silence : on attend un vrai
# arret. Sans ce garde-fou, « c'est une vision du contenu / extremement riche »
# part au moteur en deux morceaux, et Whisper rend « Extreme Maris » sur le
# fragment isole — mesure, pas suppose.
MIN_COMMIT_S = 2.5
LONG_SILENCE_MS = 1600
MIN_PHRASE_S = 0.4
MAX_PHRASE_S = 25.0
# Marges conservees autour de la parole detectee : Silero rogne volontiers les
# attaques faibles et les fins de mot soufflees.
LEAD_MS = 250
TAIL_MS = 250

# Niveau auquel on presente une phrase au moteur. Whisper decode mal les
# signaux tres faibles quand il n'a pas de contexte pour se rattraper : sur une
# phrase isolee de 2,5 s a 0,022 de pic, il a invente « Voici une autre video »
# la ou il etait dit « voir ce que ca donne ». Normalisee, la meme phrase rend
# « Voisi que ca donne ». En differe le probleme ne se voit pas, les 20 s de
# contexte suffisent a le rattraper ; en continu il n'y a que la phrase.
PHRASE_TARGET_PEAK = 0.35
PHRASE_MAX_GAIN = 20.0

_STOP = object()


def normalise_for_engine(audio: np.ndarray) -> np.ndarray:
    """Remonte une phrase faible a un niveau exploitable.

    N'amplifie jamais au-dela de `PHRASE_MAX_GAIN` et n'attenue pas : un
    enregistrement deja correct ressort inchange.
    """
    peak = float(np.abs(audio).max()) if audio.size else 0.0
    if peak <= 0.0:
        return audio
    gain = min(PHRASE_TARGET_PEAK / peak, PHRASE_MAX_GAIN)
    if gain <= 1.0:
        return audio
    return np.clip(audio * gain, -1.0, 1.0).astype(np.float32, copy=False)


class _VadGate:
    """Transforme un flux d'echantillons en flux de decisions parole/silence.

    Ne fait que decider. Le decoupage lui-meme est dans `PhraseStreamer`.
    """

    def __init__(self) -> None:
        from faster_whisper.vad import get_vad_model

        # onnxruntime est deja bride a 1 fil dans faster-whisper : une dictee ne
        # doit pas confisquer la machine, et une charge AVX sur tous les coeurs
        # tire un pic de courant que toutes les alimentations n'encaissent pas.
        self._model = get_vad_model()
        self._pending = np.zeros(0, dtype=np.float32)
        self._history = np.zeros(0, dtype=np.float32)
        self._speaking = False
        self._peak = 0.0

    def push(self, block: np.ndarray) -> list[bool]:
        """Ajoute des echantillons, retourne une decision par trame prete."""
        self._pending = np.concatenate((self._pending, block))

        ready = (len(self._pending) // VAD_FRAME) * VAD_FRAME
        if ready < STEP_FRAMES * VAD_FRAME:
            return []

        fresh, self._pending = self._pending[:ready], self._pending[ready:]

        window = np.concatenate((self._history, fresh))
        # Le nombre de trames de chauffe reellement disponibles : au tout debut
        # d'une dictee l'historique est plus court que WARMUP_FRAMES.
        warmup = len(self._history) // VAD_FRAME
        analysed = np.ascontiguousarray(window * self._gain(window), dtype=np.float32)
        probs = self._model(analysed).reshape(-1)

        self._history = window[-WARMUP_FRAMES * VAD_FRAME :]

        decisions = []
        for prob in probs[warmup:]:
            threshold = KEEP_PROB if self._speaking else OPEN_PROB
            self._speaking = bool(prob >= threshold)
            decisions.append(self._speaking)
        return decisions

    def _gain(self, window: np.ndarray) -> float:
        """Facteur a appliquer pour presenter un niveau de parole exploitable."""
        peak = float(np.abs(window).max()) if window.size else 0.0
        self._peak = max(peak, self._peak * VAD_PEAK_DECAY)
        if self._peak < VAD_GAIN_FLOOR:
            return 1.0
        return float(np.clip(VAD_TARGET_PEAK / self._peak, 1.0, VAD_MAX_GAIN))

    def drain(self) -> list[bool]:
        """Vide le reliquat en fin de dictee.

        Complete au silence jusqu'au palier de decision : sans ce bourrage, la
        derniere phrase resterait coincee dans `_pending` et serait perdue.
        """
        if len(self._pending) == 0:
            return []
        target = max(STEP_FRAMES * VAD_FRAME, -(-len(self._pending) // VAD_FRAME) * VAD_FRAME)
        pad = np.zeros(target - len(self._pending), dtype=np.float32)
        return self.push(pad)


class PhraseStreamer:
    """Decoupe le flux micro en phrases et les transcrit au fil de l'eau."""

    def __init__(
        self,
        *,
        transcribe: Callable[[np.ndarray], str],
        on_phrase: Callable[[str, int], None],
        prepare: Callable[[], None] | None = None,
        on_activity: Callable[[bool], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        silence_ms: int = SILENCE_MS,
        max_phrase_s: float = MAX_PHRASE_S,
        min_phrase_s: float = MIN_PHRASE_S,
    ) -> None:
        self._transcribe = transcribe
        self._on_phrase = on_phrase
        self._prepare = prepare
        self._on_activity = on_activity
        self._on_error = on_error

        self._silence_frames = max(1, round(silence_ms / FRAME_MS))
        # Un silence deux fois plus long ferme la phrase quelle que soit sa
        # longueur : sinon un « oui. » isole resterait ouvert indefiniment.
        self._long_silence_frames = max(
            self._silence_frames, round(LONG_SILENCE_MS / FRAME_MS)
        )
        self._min_commit_samples = int(MIN_COMMIT_S * SAMPLE_RATE)
        self._max_samples = int(max_phrase_s * SAMPLE_RATE)
        self._min_samples = int(min_phrase_s * SAMPLE_RATE)
        self._lead_samples = int(LEAD_MS * SAMPLE_RATE / 1000)
        self._tail_samples = int(TAIL_MS * SAMPLE_RATE / 1000)

        self._blocks: queue.Queue = queue.Queue()
        self._phrases: queue.Queue = queue.Queue()

        # Tampon de travail, et position absolue de son premier echantillon :
        # on le rogne au fil des phrases emises pour que la memoire reste bornee.
        self._buf = np.zeros(0, dtype=np.float32)
        self._origin = 0

        self._speech_start: int | None = None  # position absolue
        self._speech_end = 0  # fin de la derniere trame de parole
        self._silence_run = 0
        self._frame_pos = 0  # position absolue de la prochaine trame a juger

        self._texts: list[str] = []
        self._count = 0
        self._cancelled = threading.Event()
        self._pump: threading.Thread | None = None
        self._worker: threading.Thread | None = None

    # ------------------------------------------------------------ cycle de vie

    def start(self) -> None:
        self._pump = threading.Thread(target=self._pump_loop, daemon=True)
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()
        self._pump.start()

    def feed(self, block: np.ndarray) -> None:
        """Appele sur le thread audio : se contente d'empiler."""
        if not self._cancelled.is_set():
            self._blocks.put(block)

    def finish(self, timeout: float = 120.0) -> list[str]:
        """Termine, attend la transcription des phrases en attente, rend le texte."""
        self._blocks.put(_STOP)
        deadline = time.monotonic() + timeout
        for thread in (self._pump, self._worker):
            if thread is not None:
                thread.join(max(0.0, deadline - time.monotonic()))
                if thread.is_alive():
                    log.warning("Fin de dictee continue : %s ne rend pas la main", thread.name)
        return list(self._texts)

    def cancel(self) -> None:
        self._cancelled.set()
        self._blocks.put(_STOP)

    # ------------------------------------------------------------------ etage 2

    def _pump_loop(self) -> None:
        gate = _VadGate()
        try:
            while True:
                item = self._blocks.get()
                if item is _STOP:
                    break
                self._buf = np.concatenate((self._buf, item))
                for speaking in gate.push(item):
                    self._on_frame(speaking)

            for speaking in gate.drain():
                self._on_frame(speaking)
            if not self._cancelled.is_set():
                self._close_phrase(self._frame_pos)  # ce qui restait en cours
        except Exception as exc:  # noqa: BLE001
            log.exception("Segmentation de la dictee continue en echec")
            self._report(exc)
        finally:
            self._phrases.put(_STOP)

    def _on_frame(self, speaking: bool) -> None:
        """Machine a etats sur une trame de 32 ms."""
        start = self._frame_pos
        self._frame_pos += VAD_FRAME

        if speaking:
            if self._speech_start is None:
                self._speech_start = start
                self._notify_activity(True)
            self._speech_end = self._frame_pos
            self._silence_run = 0
        elif self._speech_start is not None:
            self._silence_run += 1
            # Une phrase deja consequente se ferme sur un silence ordinaire ;
            # une phrase encore courte exige un vrai arret, pour ne pas partir
            # au moteur sur un fragment que rien ne permet de decoder.
            substantial = self._speech_end - self._speech_start >= self._min_commit_samples
            needed = self._silence_frames if substantial else self._long_silence_frames
            if self._silence_run >= needed:
                self._close_phrase(self._speech_end)
                return
        else:
            # Silence hors phrase : on rogne le tampon, en gardant de quoi
            # reconstituer la marge d'attaque de la phrase suivante.
            self._trim(start - self._lead_samples)

        if self._speech_start is not None and self._frame_pos - self._speech_start >= self._max_samples:
            # Personne ne s'est tu depuis `max_phrase_s`. Couper est inevitable :
            # autant le faire au point le plus silencieux des dernieres secondes.
            self._close_phrase(self._quietest_cut())
            # La parole continue : la phrase suivante reprend immediatement la ou
            # celle-ci s'arrete. Sans ca on attendrait la prochaine attaque
            # detectee et l'audio d'ici la serait perdu — jusqu'a une seconde et
            # demie, dans le seul cas ou cette coupure sert a quelque chose.
            if self._frame_pos > self._origin:
                self._speech_start = self._origin
                self._speech_end = self._frame_pos
                self._silence_run = 0
                self._notify_activity(True)

    def _quietest_cut(self) -> int:
        """Position absolue du creux d'energie dans la derniere seconde et demie."""
        window = int(1.5 * SAMPLE_RATE)
        hi = self._frame_pos - self._origin
        lo = max(0, hi - window)
        segment = self._buf[lo:hi]
        if len(segment) < VAD_FRAME * 2:
            return self._frame_pos

        usable = (len(segment) // VAD_FRAME) * VAD_FRAME
        frames = segment[:usable].reshape(-1, VAD_FRAME)
        energy = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
        return self._origin + lo + int(np.argmin(energy)) * VAD_FRAME

    def _close_phrase(self, end: int) -> None:
        """Emet la phrase en cours vers le moteur et repart a zero."""
        start = self._speech_start
        self._speech_start = None
        self._silence_run = 0
        self._notify_activity(False)
        if start is None:
            return

        lo = max(self._origin, start - self._lead_samples)
        hi = min(self._origin + len(self._buf), end + self._tail_samples)
        if hi - lo >= self._min_samples:
            self._phrases.put(self._buf[lo - self._origin : hi - self._origin].copy())

        # Jusqu'a `hi`, pas jusqu'a `end` : la marge de fin fait partie de la
        # phrase emise, la laisser dans le tampon la ferait transcrire deux fois.
        self._trim(hi)

    def _trim(self, keep_from: int) -> None:
        """Libere le tampon avant `keep_from` (position absolue)."""
        cut = max(0, min(keep_from, self._origin + len(self._buf)) - self._origin)
        if cut <= 0:
            return
        self._buf = self._buf[cut:]
        self._origin += cut

    def _notify_activity(self, speaking: bool) -> None:
        if self._on_activity is not None:
            try:
                self._on_activity(speaking)
            except Exception:  # noqa: BLE001
                log.debug("Hook on_activity en erreur", exc_info=True)

    # ------------------------------------------------------------------ etage 3

    def _worker_loop(self) -> None:
        # Charger le moteur des maintenant : sans ca la premiere phrase paierait
        # le chargement (voire le telechargement) du modele.
        if self._prepare is not None:
            try:
                self._prepare()
            except Exception as exc:  # noqa: BLE001
                log.exception("Moteur indisponible pour la dictee continue")
                self._report(exc)

        while True:
            audio = self._phrases.get()
            if audio is _STOP:
                return
            if self._cancelled.is_set():
                continue
            try:
                text = self._transcribe(audio)
            except Exception as exc:  # noqa: BLE001
                log.exception("Transcription d'une phrase en echec")
                self._report(exc)
                continue
            if not text or self._cancelled.is_set():
                continue

            self._texts.append(text)
            self._count += 1
            try:
                self._on_phrase(text, self._count)
            except Exception:  # noqa: BLE001
                log.debug("Hook on_phrase en erreur", exc_info=True)

    def _report(self, exc: Exception) -> None:
        if self._on_error is not None:
            try:
                self._on_error(exc)
            except Exception:  # noqa: BLE001
                log.debug("Hook on_error en erreur", exc_info=True)
