"""Moteur Whisper via faster-whisper (CTranslate2).

Les valeurs par defaut ici sont reglees pour de la DICTEE COURTE, pas pour du
fichier long. Les quatre reglages qui comptent vraiment :

  * `condition_on_previous_text=False` : sans ca, Whisper reinjecte son propre
    texte dans le prompt et part en boucle sur les silences.
  * langue forcee : sur un clip de 10 s, la detection automatique se trompe
    regulierement et bascule en anglais ou en italien.
  * `vad_filter` : supprime les blancs, qui sont le declencheur n1 des
    hallucinations ("Sous-titres realises par la communaute d'Amara.org").
  * `without_timestamps=True` : on jette les timestamps de toute facon.
"""

from __future__ import annotations

import logging
import time

import numpy as np

from ..cuda_setup import ensure_cuda_dlls
from .base import SAMPLE_RATE, Transcript

log = logging.getLogger(__name__)

# Amorce la ponctuation et les majuscules : Whisper imite le style du prompt.
FRENCH_PROMPT = (
    "Bonjour, voici une note dictee. Elle est correctement ponctuee, "
    "avec des virgules, des points et des majuscules."
)


class WhisperEngine:
    """Enveloppe un modele CTranslate2 et le garde resident."""

    def __init__(
        self,
        model_id: str,
        repo: str,
        *,
        compute_type: str = "int8_float16",
        language: str | None = "fr",
        beam_size: int = 1,
        initial_prompt: str | None = FRENCH_PROMPT,
        vad_filter: bool = True,
        prefer_gpu: bool = True,
        download_root: str | None = None,
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
            )
        except Exception as exc:  # noqa: BLE001
            # VRAM saturee ou CUDA indisponible : l'i9-9960X encaisse turbo en int8.
            log.warning("Chargement CUDA impossible (%s), repli CPU", exc)
            device, compute_type = "cpu", "int8"
            self._model = WhisperModel(
                self.repo,
                device=device,
                compute_type=compute_type,
                download_root=self.download_root,
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
            segments, _ = self._model.transcribe(silence, language=self.language)
            list(segments)
        except Exception:  # noqa: BLE001
            log.debug("Warmup Whisper sans effet", exc_info=True)

    def transcribe(self, audio: np.ndarray, language: str | None = None) -> Transcript:
        if not self.is_loaded:
            self.load()

        audio = np.ascontiguousarray(audio, dtype=np.float32)
        started = time.perf_counter()

        segments, info = self._model.transcribe(
            audio,
            language=language or self.language,
            beam_size=self.beam_size,
            initial_prompt=self.initial_prompt,
            condition_on_previous_text=False,
            without_timestamps=True,
            vad_filter=self.vad_filter,
            vad_parameters={"min_silence_duration_ms": 300},
            temperature=[0.0, 0.2, 0.4],
            no_speech_threshold=0.6,
            compression_ratio_threshold=2.4,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        latency_ms = int((time.perf_counter() - started) * 1000)

        return Transcript(
            text=text,
            language=getattr(info, "language", None) or self.language,
            audio_seconds=len(audio) / SAMPLE_RATE,
            latency_ms=latency_ms,
            device=self.device,
        )
