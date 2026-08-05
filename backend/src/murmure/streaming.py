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

Decouper reste malgre tout une amputation : une phrase isolee, le modele la
ferme par un point et remet une majuscule a la suivante. « c'est une vision du
contenu / extremement riche » devient « C'est une vision du contenu.
Extremement riche. » D'ou un second passage, le POLISSAGE : quand la parole
retombe pour de bon, la fenetre des dernieres phrases repart au moteur d'un
seul bloc. Le modele voit alors la phrase entiere et rend ce qu'il rendrait en
differe — ponctuation, majuscules, et pas de reprise en double. Ce n'est pas un
modele de plus : c'est le meme, avec son contexte.

Et comme une phrase ne tombe qu'une fois finie, un APERCU decode la phrase en
cours a intervalle regulier, sans la fermer. Provisoire, affiche en grise,
jamais frappe au curseur ni historise.

Quatre etages, chacun sur son thread, pour que rien de lourd ne remonte jusqu'au
thread temps reel de PortAudio :

    feed()      thread audio    empile les blocs, rien d'autre
    _pump       thread VAD      Silero + machine a etats, decoupe les phrases
    _worker     thread moteur   transcrit et polit, dans l'ordre d'emission
    _preview    thread apercu   decode la phrase en cours, sans jamais attendre

Le worker est unique et sequentiel : c'est ce qui garantit qu'une phrase courte
finie avant une longue ne passe pas devant elle, et c'est aussi ce qui garantit
qu'une fenetre de polissage arrive apres les phrases qu'elle recouvre — elle
voyage dans la meme file, aucun verrou n'est necessaire.

L'apercu, lui, est le seul etage qui a le droit de ne rien faire : s'il ne peut
pas prendre le moteur immediatement, il saute son tour. Un affichage provisoire
ne doit jamais retarder une phrase.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable

import numpy as np

from .engines.base import SAMPLE_RATE
from .polish import choose, merge_seam

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

# --------------------------------------------------------------- polissage

# Silence TOTAL depuis le dernier echantillon de parole — pas la duree de
# silence qui a ferme la phrase. Une fin de phrase se joue a 700 ms ; un vrai
# arret, celui apres lequel plus rien de ce qui precede ne changera, se
# reconnait plus tard. C'est ce moment-la qu'on choisit pour re-decoder : le
# moteur est libre, personne n'attend une phrase, le polissage ne coute rien
# de percu.
#
# 1,6 s vient de la mesure, pas du gout. Sur trois minutes de dictee francaise
# reelle, les pauses entre phrases se repartissent presque toutes entre 0,7 et
# 1,6 s ; le seuil coupe donc juste au-dessus du gros du peloton et laisse
# 2,3 a 4 phrases par fenetre. Monter a 2,5 s en grouperait 5 a 6 — mais une
# fenetre de 5 phrases depasse `POLISH_MAX_S`, et c'est alors le plafond qui
# tranche, a un endroit quelconque au lieu d'un vrai arret. Les deux valeurs
# vont ensemble : changer l'une sans l'autre n'apporte rien.
POLISH_SILENCE_MS = 1600
# Un monologue sans vraie pause ne doit pas repousser le polissage
# indefiniment : au-dela, on ferme la fenetre sur la derniere phrase emise.
# Whisper est entraine sur des fenetres de 30 s ; on reste en dessous.
POLISH_MAX_S = 20.0

# ------------------------------------------------------------------ apercu

# Cadence de l'apercu. 0 = pas d'apercu du tout.
PREVIEW_MS = 500
# On ne re-decode que la fin de la phrase en cours : sur un monologue, reprendre
# depuis le debut a chaque tick couterait plus cher que la phrase elle-meme, et
# l'apercu ne sert qu'a montrer ce qui vient d'etre dit.
PREVIEW_MAX_S = 8.0
PREVIEW_MIN_S = 0.35

_STOP = object()


class _PolishTask:
    """Une fenetre a re-decoder d'un bloc.

    Voyage dans la meme file que les phrases : c'est ce qui garantit qu'elle
    atteint le moteur APRES les phrases qu'elle recouvre, sans aucun verrou.

    `audio` vaut None quand la fenetre ne couvre qu'une seule phrase : ses
    echantillons sont alors exactement ceux deja transcrits, au meme niveau et
    dans la meme langue. Re-decoder rendrait le meme texte pour le meme prix.
    La tache reste emise malgre tout — c'est elle, et non le commit, qui fait
    passer le texte au curseur.
    """

    __slots__ = ("audio",)

    def __init__(self, audio: np.ndarray | None) -> None:
        self.audio = audio


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
        polish: Callable[[np.ndarray], str] | None = None,
        on_revise: Callable[[str, int, int], None] | None = None,
        preview: Callable[[np.ndarray], str] | None = None,
        on_preview: Callable[[str], None] | None = None,
        silence_ms: int = SILENCE_MS,
        max_phrase_s: float = MAX_PHRASE_S,
        min_phrase_s: float = MIN_PHRASE_S,
        polish_max_s: float = POLISH_MAX_S,
        preview_ms: int = PREVIEW_MS,
    ) -> None:
        self._transcribe = transcribe
        self._on_phrase = on_phrase
        self._prepare = prepare
        self._on_activity = on_activity
        self._on_error = on_error
        self._polish = polish
        self._on_revise = on_revise
        self._preview = preview
        self._on_preview = on_preview

        # Les deux etages supplementaires s'activent par la presence de leurs
        # deux hooks : sans consommateur, decoder pour rien serait absurde.
        self._polishing = polish is not None and on_revise is not None
        self._previewing = preview is not None and on_preview is not None and preview_ms > 0

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
        self._polish_max_samples = int(polish_max_s * SAMPLE_RATE)
        self._polish_silence_samples = int(POLISH_SILENCE_MS * SAMPLE_RATE / 1000)
        self._preview_s = preview_ms / 1000
        self._preview_max_samples = int(PREVIEW_MAX_S * SAMPLE_RATE)
        self._preview_min_samples = int(PREVIEW_MIN_S * SAMPLE_RATE)

        self._blocks: queue.Queue = queue.Queue()
        self._phrases: queue.Queue = queue.Queue()

        # Tampon de travail, et position absolue de son premier echantillon :
        # on le rogne au fil des phrases emises pour que la memoire reste bornee.
        self._buf = np.zeros(0, dtype=np.float32)
        self._origin = 0
        # Le meme couple, publie d'un seul coup pour le thread d'apercu : lire
        # `_buf` et `_origin` separement pourrait attraper l'un avant un rognage
        # et l'autre apres. Une affectation de tuple, elle, est indivisible.
        self._view: tuple[np.ndarray, int] = (self._buf, self._origin)

        self._speech_start: int | None = None  # position absolue
        self._speech_end = 0  # fin de la derniere trame de parole
        self._phrase_end = 0  # fin de la derniere phrase emise, marge comprise
        self._silence_run = 0
        self._frame_pos = 0  # position absolue de la prochaine trame a juger
        # Fin de la derniere parole entendue, toutes phrases confondues : c'est
        # sur elle que se mesure le vrai arret qui declenche le polissage.
        self._last_speech_end = 0

        # Fenetre de polissage en cours : bornes absolues de l'audio contigu
        # couvrant les phrases deja emises mais pas encore re-decodees.
        self._window_start: int | None = None
        self._window_end = 0
        self._window_phrases = 0
        self._polished_upto = 0  # nombre de phrases absorbees par une fenetre
        self._blocks_text: list[str] = []

        self._texts: list[str] = []
        self._count = 0
        self._cancelled = threading.Event()
        # Leve des que l'etage VAD a rendu la main : c'est le signal d'arret du
        # thread d'apercu, qui n'a plus rien a montrer une fois la parole finie.
        self._finished = threading.Event()
        self._pump: threading.Thread | None = None
        self._worker: threading.Thread | None = None
        self._previewer: threading.Thread | None = None

    # ------------------------------------------------------------ cycle de vie

    def start(self) -> None:
        self._pump = threading.Thread(target=self._pump_loop, daemon=True)
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()
        self._pump.start()
        if self._previewing:
            self._previewer = threading.Thread(target=self._preview_loop, daemon=True)
            self._previewer.start()

    def feed(self, block: np.ndarray) -> None:
        """Appele sur le thread audio : se contente d'empiler."""
        if not self._cancelled.is_set():
            self._blocks.put(block)

    def finish(self, timeout: float = 120.0) -> list[str]:
        """Termine, attend la transcription des phrases en attente, rend le texte."""
        self._blocks.put(_STOP)
        deadline = time.monotonic() + timeout
        for thread in (self._pump, self._worker, self._previewer):
            if thread is not None:
                thread.join(max(0.0, deadline - time.monotonic()))
                if thread.is_alive():
                    log.warning("Fin de dictee continue : %s ne rend pas la main", thread.name)
        return self.result()

    def result(self) -> list[str]:
        """Le texte de la dictee, dans l'etat le plus abouti disponible.

        Avec le polissage, ce sont les fenetres re-decodees qui font foi ; la
        queue de phrases qu'aucune fenetre n'a encore absorbee les complete.
        `finish()` ferme la derniere fenetre, cette queue y est donc vide.
        """
        if not self._polishing:
            return list(self._texts)
        return [*self._blocks_text, *self._texts[self._polished_upto :]]

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
                self._publish()
                for speaking in gate.push(item):
                    self._on_frame(speaking)

            for speaking in gate.drain():
                self._on_frame(speaking)
            if not self._cancelled.is_set():
                self._close_phrase(self._frame_pos)  # ce qui restait en cours
                # Dans cet ordre : la derniere fenetre doit recouvrir la phrase
                # qu'on vient d'emettre, sinon elle sortirait non polie.
                self._flush_window()
        except Exception as exc:  # noqa: BLE001
            log.exception("Segmentation de la dictee continue en echec")
            self._report(exc)
        finally:
            self._phrases.put(_STOP)
            self._finished.set()

    def _on_frame(self, speaking: bool) -> None:
        """Machine a etats sur une trame de 32 ms."""
        start = self._frame_pos
        self._frame_pos += VAD_FRAME

        if speaking:
            if self._speech_start is None:
                self._speech_start = start
                self._notify_activity(True)
            self._speech_end = self._frame_pos
            self._last_speech_end = self._frame_pos
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
            # Silence hors phrase. C'est ici, et seulement ici, qu'on polit :
            # plus personne ne parle, aucune phrase n'attend le moteur, le
            # re-decodage de la fenetre ne retarde donc rien.
            if self._frame_pos - self._last_speech_end >= self._polish_silence_samples:
                self._flush_window()
            # Puis on rogne le tampon, en gardant de quoi reconstituer la marge
            # d'attaque de la phrase suivante. Dans cet ordre : `_trim` ne libere
            # pas l'audio d'une fenetre encore ouverte.
            self._trim(start - self._lead_samples)

        if self._speech_start is not None and self._frame_pos - self._speech_start >= self._max_samples:
            # Personne ne s'est tu depuis `max_phrase_s`. Couper est inevitable :
            # autant le faire au point le plus silencieux des dernieres secondes.
            self._close_phrase(self._quietest_cut())
            # La parole continue : la phrase suivante reprend immediatement la ou
            # celle-ci s'arrete. Sans ca on attendrait la prochaine attaque
            # detectee et l'audio d'ici la serait perdu — jusqu'a une seconde et
            # demie, dans le seul cas ou cette coupure sert a quelque chose.
            # `_phrase_end`, et surtout pas `_origin` : depuis que le tampon
            # retient l'audio de la fenetre de polissage, son origine est en
            # amont de la phrase qu'on vient d'emettre. Repartir de la
            # renverrait au moteur des mots deja transcrits — le doublon a la
            # couture, precisement ce que tout ce decoupage evite.
            if self._frame_pos > self._phrase_end:
                self._speech_start = self._phrase_end
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

        # `_phrase_end` borne la marge d'attaque : sans lui, elle mordrait de
        # 250 ms sur la phrase precedente quand les deux se touchent — c'est le
        # cas apres une coupe a `max_phrase_s`, et ces 250 ms partiraient deux
        # fois au moteur. `_origin` jouait ce role tant qu'il valait la fin de la
        # derniere phrase ; il est desormais retenu en amont par la fenetre de
        # polissage et ne protege plus rien.
        lo = max(self._origin, self._phrase_end, start - self._lead_samples)
        hi = min(self._origin + len(self._buf), end + self._tail_samples)
        # Fin de ce qui vient de partir au moteur, marge comprise : c'est le
        # point ou une phrase suivante doit reprendre. Affecte apres `lo`, qui
        # a besoin de la valeur precedente.
        self._phrase_end = hi
        if hi - lo >= self._min_samples:
            # Le plafond se verifie AVANT d'ajouter la phrase, jamais apres :
            # une phrase peut durer `max_phrase_s`, et celle qui fait deborder
            # la fenetre y resterait. Vingt secondes plus vingt-cinq, c'est bien
            # au-dela des trente sur lesquelles Whisper est entraine — le
            # plafond ne protegerait plus rien.
            #
            # Et avant de l'empiler, pas seulement avant de la compter : la
            # tache de polissage doit sortir de la file APRES les phrases
            # qu'elle recouvre et AVANT celles qu'elle ne recouvre pas. C'est
            # cet ordre, et lui seul, qui fait correspondre ses indices a son
            # audio.
            if (
                self._polishing
                and self._window_start is not None
                and hi - self._window_start > self._polish_max_samples
            ):
                self._flush_window()

            self._phrases.put(self._buf[lo - self._origin : hi - self._origin].copy())
            if self._polishing:
                # La fenetre couvre l'audio CONTIGU des phrases qu'elle recouvre,
                # pas leur concatenation : les silences entre elles y restent, et
                # ce sont eux qui disent au modele ou finit une phrase.
                if self._window_start is None:
                    self._window_start = lo
                self._window_end = hi
                self._window_phrases += 1

        # Jusqu'a `hi`, pas jusqu'a `end` : la marge de fin fait partie de la
        # phrase emise, la laisser dans le tampon la ferait transcrire deux fois.
        self._trim(hi)

    def _flush_window(self) -> None:
        """Envoie la fenetre courante au re-decodage. Sans effet s'il n'y en a pas."""
        start, self._window_start = self._window_start, None
        phrases, self._window_phrases = self._window_phrases, 0
        if start is None or phrases == 0:
            return

        # Une seule phrase : la fenetre est mot pour mot l'audio deja transcrit.
        # On passe la main sans deranger le moteur — c'est le cas courant chez
        # qui marque de vraies pauses entre ses phrases, et il ne doit rien
        # couter. Le polissage n'a de sens que la ou il y a plusieurs phrases a
        # remettre dans une seule.
        if phrases < 2:
            self._phrases.put(_PolishTask(None))
            return

        lo = max(self._origin, start)
        hi = min(self._origin + len(self._buf), self._window_end)
        if hi - lo < self._min_samples:
            self._phrases.put(_PolishTask(None))
            return

        log.info(
            "Polissage : %d phrases sur %.1f s d'audio", phrases, (hi - lo) / SAMPLE_RATE
        )
        self._phrases.put(_PolishTask(self._buf[lo - self._origin : hi - self._origin].copy()))

    def _trim(self, keep_from: int) -> None:
        """Libere le tampon avant `keep_from` (position absolue).

        Ne descend jamais en dessous du debut de la fenetre de polissage : cet
        audio-la doit rester entier jusqu'a son re-decodage. Le cout est borne
        par `polish_max_s`, soit 1,3 Mo pour vingt secondes.
        """
        if self._window_start is not None:
            keep_from = min(keep_from, self._window_start)
        cut = max(0, min(keep_from, self._origin + len(self._buf)) - self._origin)
        if cut <= 0:
            return
        self._buf = self._buf[cut:]
        self._origin += cut
        self._publish()

    def _publish(self) -> None:
        """Rend le tampon courant lisible d'un seul coup par le thread d'apercu."""
        self._view = (self._buf, self._origin)

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
            if isinstance(audio, _PolishTask):
                self._run_polish(audio)
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

    def _run_polish(self, task: _PolishTask) -> None:
        """Re-decode une fenetre et remplace les phrases qu'elle recouvre.

        Sur le meme thread que les phrases : quand cette tache sort de la file,
        toutes les phrases de la fenetre sont deja transcrites, par construction.
        """
        first, last = self._polished_upto + 1, self._count
        if last < first:
            return  # la fenetre n'a produit aucun texte : rien a remplacer

        raw = " ".join(self._texts[first - 1 : last])
        if task.audio is None:
            text = raw
        else:
            try:
                text = self._polish(task.audio)
            except Exception as exc:  # noqa: BLE001
                log.exception("Polissage d'une fenetre en echec")
                self._report(exc)
                text = ""
            # Le texte brut est ce que l'utilisateur a deja vu tomber : s'y
            # replier ne lui coute rien, tandis qu'un re-decodage parti en
            # boucle lui couterait la fenetre entiere.
            text = choose(text, raw)
        # Deux fenetres consecutives partagent les marges d'attaque et de fin des
        # phrases a leur couture : quelques mots peuvent sortir deux fois.
        previous = next((block for block in reversed(self._blocks_text) if block), "")
        text = merge_seam(previous, text)

        self._polished_upto = last
        self._blocks_text.append(text)
        # Emis meme vide : le frontend doit savoir que ces phrases sont absorbees,
        # sans quoi elles resteraient affichees en double sous la fenetre polie.
        try:
            self._on_revise(text, first, last)
        except Exception:  # noqa: BLE001
            log.debug("Hook on_revise en erreur", exc_info=True)

    # ------------------------------------------------------------------ etage 4

    def _preview_loop(self) -> None:
        """Decode la phrase en cours a intervalle regulier, sans la fermer.

        Le seul etage qui a le droit d'echouer en silence : son resultat est
        provisoire, il ne part ni au curseur ni a l'historique.
        """
        while not self._finished.wait(self._preview_s):
            start = self._speech_start
            if start is None or self._cancelled.is_set():
                continue

            buf, origin = self._view
            hi = len(buf)
            lo = max(0, start - self._lead_samples - origin, hi - self._preview_max_samples)
            if hi - lo < self._preview_min_samples:
                continue

            try:
                text = self._preview(buf[lo:hi].copy())
            except Exception:  # noqa: BLE001
                log.debug("Apercu en echec", exc_info=True)
                continue

            # La phrase a pu se fermer pendant le decodage : son texte definitif
            # est alors deja parti, et cet apercu ne vaut plus rien. Les positions
            # ne font que croitre, l'egalite suffit a le savoir.
            if not text or self._speech_start != start or self._cancelled.is_set():
                continue
            try:
                self._on_preview(text)
            except Exception:  # noqa: BLE001
                log.debug("Hook on_preview en erreur", exc_info=True)

    def _report(self, exc: Exception) -> None:
        if self._on_error is not None:
            try:
                self._on_error(exc)
            except Exception:  # noqa: BLE001
                log.debug("Hook on_error en erreur", exc_info=True)
