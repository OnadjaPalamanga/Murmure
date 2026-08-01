"""Moteur Parakeet / Canary via onnx-asr (ONNX Runtime, sans torch ni NeMo).

Architecture transducer (TDT) : le modele peut emettre une sortie vide au lieu de
forcer un token, ce qui le rend nettement moins sujet aux hallucinations sur les
silences et les clips courts que Whisper. C'est la raison principale pour laquelle
c'est le moteur par defaut en dictee.
"""

from __future__ import annotations

import logging
import time

import numpy as np

from ..chunking import split_on_silence
from ..cuda_setup import ensure_cuda_dlls
from .base import SAMPLE_RATE, Transcript

log = logging.getLogger(__name__)


class ParakeetEngine:
    """Enveloppe un modele onnx-asr et le garde resident."""

    def __init__(
        self,
        model_id: str,
        onnx_name: str,
        *,
        quantization: str | None = None,
        prefer_gpu: bool = True,
        needs_language: bool = False,
        default_language: str = "fr",
    ) -> None:
        self.model_id = model_id
        self.onnx_name = onnx_name
        self.quantization = quantization
        self.prefer_gpu = prefer_gpu
        # Canary n'a pas de detection automatique : son prompt est cable en
        # anglais et, sans langue source explicite, il *traduit* au lieu de
        # transcrire. Parakeet, lui, detecte seul et ne doit rien recevoir.
        self.needs_language = needs_language
        self.default_language = default_language
        self.is_loaded = False
        self.device = "cpu"
        self._model = None

    def load(self) -> None:
        if self.is_loaded:
            return

        ensure_cuda_dlls()
        import onnx_asr

        providers: list = ["CPUExecutionProvider"]
        if self.prefer_gpu:
            import onnxruntime as ort

            if "CUDAExecutionProvider" in ort.get_available_providers():
                providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]

        started = time.perf_counter()
        self._model = onnx_asr.load_model(
            self.onnx_name,
            quantization=self.quantization,
            providers=providers,
        )
        # Le provider effectif n'est connu qu'apres coup : CUDA peut echouer
        # silencieusement (DLL manquante, VRAM insuffisante) et retomber sur CPU.
        self.device = "cuda" if self._resolve_device(providers) else "cpu"
        self.is_loaded = True
        log.info(
            "Parakeet %s charge sur %s en %.1fs",
            self.model_id,
            self.device,
            time.perf_counter() - started,
        )

    def _resolve_device(self, requested: list) -> bool:
        """True si onnxruntime utilise reellement CUDA."""
        if "CUDAExecutionProvider" not in requested:
            return False
        for attr in ("_sessions", "_session"):
            sessions = getattr(self._model, attr, None)
            if sessions is None:
                continue
            candidates = sessions if isinstance(sessions, (list, tuple)) else [sessions]
            for sess in candidates:
                get = getattr(sess, "get_providers", None)
                if get and "CUDAExecutionProvider" in get():
                    return True
        # onnx-asr n'expose pas toujours ses sessions : on fait confiance au
        # provider demande, ensure_cuda_dlls() ayant deja valide les DLL.
        return True

    def unload(self) -> None:
        self._model = None
        self.is_loaded = False
        self.device = "cpu"

    def warmup(self) -> None:
        """Force l'allocation des kernels CUDA pour que la 1re vraie dictee soit rapide."""
        if not self.is_loaded:
            self.load()
        silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
        opts = (
            {"language": self.default_language, "target_language": self.default_language}
            if self.needs_language
            else {}
        )
        try:
            self._model.recognize(silence, sample_rate=SAMPLE_RATE, **opts)
        except Exception:  # noqa: BLE001 - un echec de warmup n'est pas fatal
            log.debug("Warmup Parakeet sans effet", exc_info=True)

    def transcribe(self, audio: np.ndarray, language: str | None = None) -> Transcript:
        if not self.is_loaded:
            self.load()

        audio = np.ascontiguousarray(audio, dtype=np.float32)
        started = time.perf_counter()

        # Parakeet v3 detecte la langue seul : lui passer `language` degraderait
        # le resultat sur les phrases qui melangent francais et anglais.
        # Canary, a l'inverse, exige une langue source, sans quoi il traduit.
        opts: dict = {}
        if self.needs_language:
            source = language or self.default_language
            # target = source : c'est ce qui distingue transcrire de traduire.
            opts = {"language": source, "target_language": source}

        chunks = split_on_silence(audio, sample_rate=SAMPLE_RATE)
        if len(chunks) == 1:
            text = self._model.recognize(chunks[0], sample_rate=SAMPLE_RATE, **opts)
        else:
            # recognize() accepte une liste et traite le lot en une passe.
            parts = self._model.recognize(chunks, sample_rate=SAMPLE_RATE, **opts)
            text = " ".join(p.strip() for p in parts if p and p.strip())

        latency_ms = int((time.perf_counter() - started) * 1000)

        return Transcript(
            text=(text or "").strip(),
            language=language,
            audio_seconds=len(audio) / SAMPLE_RATE,
            latency_ms=latency_ms,
            device=self.device,
        )
