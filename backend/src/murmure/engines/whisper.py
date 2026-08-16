"""Moteur Whisper via faster-whisper (CTranslate2).

Les valeurs par defaut ici sont reglees pour de la DICTEE COURTE, pas pour du
fichier long. Les reglages qui comptent vraiment :

  * `condition_on_previous_text=False` : sans ca, Whisper reinjecte son propre
    texte dans le prompt et part en boucle sur les silences.
  * `vad_filter` : supprime les blancs, qui sont le declencheur n1 des
    hallucinations ("Sous-titres realises par la communaute d'Amara.org").
  * `without_timestamps=True` : on jette les timestamps de toute facon.
  * langue : `None` par defaut, c'est-a-dire detection automatique. Forcer "fr"
    pousse le decodeur a franciser phonetiquement les mots anglais reellement
    prononces ; on prefere la fidelite a ce qui est dit.
"""

from __future__ import annotations

import logging
import time

import numpy as np

from ..cuda_setup import ensure_cuda_dlls
from .base import SAMPLE_RATE, Transcript, Word

log = logging.getLogger(__name__)

# Whisper imite le style de son amorce. Celle-ci fait deux choses :
# elle demande une ponctuation normale, et elle contient volontairement des
# anglicismes ECRITS EN ANGLAIS. Sans ce second point, le decodeur transcrit
# "meeting" en "mitine" ou "deadline" en "dedeline" des qu'il se croit en
# francais. On veut ce qui est reellement dit, pas une francisation forcee.
DICTATION_PROMPT = (
    "Voici une note dictée, ponctuée normalement, avec majuscules et virgules. "
    "Elle mélange naturellement le français et l'anglais : un meeting, le workflow, "
    "la deadline, un call, le feedback, le dataset, debug, machine learning."
)

# Sentinelle : distingue « appelant n'a rien precise » de « detection automatique
# demandee explicitement », que None represente.
_UNSET = object()


class WhisperEngine:
    """Enveloppe un modele CTranslate2 et le garde resident."""

    def __init__(
        self,
        model_id: str,
        repo: str,
        *,
        compute_type: str = "int8_float16",
        language: str | None = None,
        beam_size: int = 1,
        initial_prompt: str | None = DICTATION_PROMPT,
        vad_filter: bool = True,
        prefer_gpu: bool = True,
        download_root: str | None = None,
        cpu_threads: int = 0,
    ) -> None:
        self.model_id = model_id
        self.repo = repo
        self.compute_type = compute_type
        self.language = language
        self.beam_size = beam_size
        self.initial_prompt = initial_prompt
        self.vad_filter = vad_filter
        self.prefer_gpu = prefer_gpu
        self.download_root = download_root
        # 0 = laisser CTranslate2 decider, c'est-a-dire prendre tous les coeurs.
        # Les modeles du cran leger bornent ce nombre : une dictee ne doit pas
        # rendre la machine inutilisable, et une charge AVX sur tous les coeurs
        # tire un pic de courant que toutes les alimentations n'encaissent pas.
        self.cpu_threads = cpu_threads
        self.is_loaded = False
        self.device = "cpu"
        self._model = None

    def load(self) -> None:
        if self.is_loaded:
            return

        ensure_cuda_dlls()
        from faster_whisper import WhisperModel

        device = "cuda" if self.prefer_gpu else "cpu"
        compute_type = self.compute_type
        started = time.perf_counter()

        try:
            self._model = WhisperModel(
                self.repo,
                device=device,
                compute_type=compute_type,
                download_root=self.download_root,
                cpu_threads=self.cpu_threads,
            )
        except Exception as exc:  # noqa: BLE001
            # VRAM saturee ou CUDA indisponible : l'i9-9960X encaisse turbo en int8.
            # int8_float16 est un type GPU : sur CPU il faut retomber sur int8.
            log.warning("Chargement CUDA impossible (%s), repli CPU", exc)
            device, compute_type = "cpu", "int8"
            self._model = WhisperModel(
                self.repo,
                device=device,
                compute_type=compute_type,
                download_root=self.download_root,
                cpu_threads=self.cpu_threads,
            )

        self.device = device
        self.is_loaded = True
        log.info(
            "Whisper %s charge sur %s (%s) en %.1fs",
            self.model_id,
            device,
            compute_type,
            time.perf_counter() - started,
        )

    def unload(self) -> None:
        self._model = None
        self.is_loaded = False
        self.device = "cpu"

    def warmup(self) -> None:
        if not self.is_loaded:
            self.load()
        silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
        try:
            segments, _ = self._model.transcribe(silence, language=self.language or "fr")
            list(segments)
        except Exception:  # noqa: BLE001
            log.debug("Warmup Whisper sans effet", exc_info=True)

    def transcribe(
        self, audio: np.ndarray, language=_UNSET, *, timestamps: bool = False
    ) -> Transcript:
        if not self.is_loaded:
            self.load()

        # None passe explicitement = detection automatique demandee.
        resolved = self.language if language is _UNSET else language

        audio = np.ascontiguousarray(audio, dtype=np.float32)
        started = time.perf_counter()

        segments, info = self._model.transcribe(
            audio,
            language=resolved,
            beam_size=self.beam_size,
            initial_prompt=self.initial_prompt,
            condition_on_previous_text=False,
            # Les timestamps ne sont demandes que pour la diarisation. Les
            # activer par defaut ferait payer a chaque dictee un alignement
            # dont elle ne fait rien.
            without_timestamps=not timestamps,
            word_timestamps=timestamps,
            vad_filter=self.vad_filter,
            vad_parameters={"min_silence_duration_ms": 300},
            temperature=[0.0, 0.2, 0.4],
            no_speech_threshold=0.6,
            compression_ratio_threshold=2.4,
        )

        # `segments` est un generateur paresseux : le parcourir une seule fois,
        # en collectant texte et mots au meme passage.
        parts: list[str] = []
        words: list[Word] = []
        for seg in segments:
            parts.append(seg.text.strip())
            for word in getattr(seg, "words", None) or ():
                # faster-whisper porte l'espacement DANS le mot (« l' » puis
                # « application » sans espace initiale). On le retient avant de
                # depouiller, sinon plus rien ne permet de recoller juste.
                label = word.word.strip()
                if label:
                    words.append(
                        Word(
                            float(word.start),
                            float(word.end),
                            label,
                            space_before=word.word[:1].isspace(),
                        )
                    )

        text = " ".join(p for p in parts if p).strip()
        latency_ms = int((time.perf_counter() - started) * 1000)

        return Transcript(
            text=text,
            language=getattr(info, "language", None) or resolved,
            audio_seconds=len(audio) / SAMPLE_RATE,
            latency_ms=latency_ms,
            device=self.device,
            # La dictee continue s'en sert pour ne detecter la langue qu'une
            # fois par dictee, au lieu d'une fois par phrase.
            extra={
                "language_probability": float(
                    getattr(info, "language_probability", 0.0) or 0.0
                )
            },
            words=words,
        )
