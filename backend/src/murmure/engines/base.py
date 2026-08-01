"""Interface commune aux moteurs de reconnaissance vocale."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import numpy as np

SAMPLE_RATE = 16_000


@dataclass(slots=True)
class Transcript:
    """Resultat d'une transcription."""

    text: str
    language: str | None = None
    audio_seconds: float = 0.0
    latency_ms: int = 0
    device: str = "cpu"
    extra: dict = field(default_factory=dict)

    @property
    def realtime_factor(self) -> float:
        """Combien de secondes d'audio traitees par seconde de calcul."""
        if self.latency_ms <= 0:
            return 0.0
        return self.audio_seconds / (self.latency_ms / 1000.0)


@runtime_checkable
class Engine(Protocol):
    """Un moteur charge un modele une fois, puis transcrit a la demande.

    `load()` est deliberatement separe de `__init__` : le service instancie tous
    les moteurs declares au demarrage mais ne charge en VRAM que celui qui sert.
    """

    model_id: str
    is_loaded: bool
    device: str

    def load(self) -> None: ...

    def unload(self) -> None: ...

    def transcribe(self, audio: np.ndarray, language: str | None = None) -> Transcript:
        """Transcrit du PCM mono float32 a SAMPLE_RATE, normalise dans [-1, 1]."""
        ...
