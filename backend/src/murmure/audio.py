"""Capture micro concue pour que le premier mot ne soit jamais perdu.

Deux astuces font toute la latence percue :

1. Le flux reste ouvert `keepalive_s` apres la fin d'une dictee. Ouvrir un
   peripherique WASAPI coute 50-150 ms ; en enchainant les dictees, on ne les paie
   qu'une fois.
2. Un tampon circulaire tourne en permanence pendant que le flux est ouvert. Au
   declenchement on repart `preroll_ms` EN ARRIERE : le debut de mot prononce
   juste avant que la touche soit enfoncee est deja capture.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np
import sounddevice as sd

from .engines.base import SAMPLE_RATE

log = logging.getLogger(__name__)

BLOCK_MS = 20
PREROLL_MS = 400
KEEPALIVE_S = 90.0
MAX_RECORD_S = 600.0


@dataclass(slots=True)
class Device:
    index: int
    name: str
    channels: int
    is_default: bool

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "name": self.name,
            "channels": self.channels,
            "is_default": self.is_default,
        }


def list_input_devices() -> list[Device]:
    devices: list[Device] = []
    try:
        default_in = sd.default.device[0]
    except (TypeError, IndexError):
        default_in = None

    for idx, info in enumerate(sd.query_devices()):
        if info["max_input_channels"] < 1:
            continue
        devices.append(
            Device(
                index=idx,
                name=info["name"],
                channels=int(info["max_input_channels"]),
                is_default=(idx == default_in),
            )
        )
    return devices


class Recorder:
    """Capture mono 16 kHz avec pre-roll. Thread-safe."""

    def __init__(
        self,
        *,
        device: int | None = None,
        preroll_ms: int = PREROLL_MS,
        keepalive_s: float = KEEPALIVE_S,
        on_level=None,
    ) -> None:
        self.device = device
        self.preroll_ms = preroll_ms
        self.keepalive_s = keepalive_s
        self.on_level = on_level

        self._lock = threading.RLock()
        self._stream: sd.InputStream | None = None
        self._blocksize = int(SAMPLE_RATE * BLOCK_MS / 1000)

        preroll_blocks = max(1, int(preroll_ms / BLOCK_MS))
        self._preroll: deque[np.ndarray] = deque(maxlen=preroll_blocks)
        self._captured: list[np.ndarray] = []

        self.is_recording = False
        self._started_at = 0.0
        self._last_use = 0.0
        self._idle_timer: threading.Timer | None = None

    # ---------------------------------------------------------------- flux

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if status:
            log.debug("Statut flux audio : %s", status)

        block = indata[:, 0].copy() if indata.ndim > 1 else indata.copy()

        with self._lock:
            if self.is_recording:
                self._captured.append(block)
            else:
                self._preroll.append(block)

        if self.on_level is not None:
            peak = float(np.abs(block).max()) if block.size else 0.0
            self.on_level(peak)

    def open(self) -> None:
        """Ouvre le flux s'il ne l'est pas. Idempotent."""
        with self._lock:
            self._last_use = time.monotonic()
            if self._stream is not None:
                return
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=self._blocksize,
                device=self.device,
                callback=self._callback,
            )
            self._stream.start()
            log.info("Flux audio ouvert (peripherique=%s)", self.device or "defaut")

    def close(self) -> None:
        with self._lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
            if self._stream is None:
                return
            try:
                self._stream.stop()
                self._stream.close()
            except sd.PortAudioError:
                log.debug("Fermeture du flux audio en erreur", exc_info=True)
            self._stream = None
            self._preroll.clear()
            log.info("Flux audio ferme")

    def _schedule_close(self) -> None:
        """Relache le micro apres inactivite (l'indicateur Windows s'eteint)."""
        with self._lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()

            def maybe_close() -> None:
                with self._lock:
                    idle = time.monotonic() - self._last_use
                    if not self.is_recording and idle >= self.keepalive_s:
                        self.close()

            self._idle_timer = threading.Timer(self.keepalive_s + 1, maybe_close)
            self._idle_timer.daemon = True
            self._idle_timer.start()

    # ------------------------------------------------------------ dictee

    def start(self) -> None:
        self.open()
        with self._lock:
            if self.is_recording:
                return
            # Le pre-roll devient le debut de l'enregistrement.
            self._captured = list(self._preroll)
            self._preroll.clear()
            self.is_recording = True
            self._started_at = time.monotonic()
            self._last_use = self._started_at

    def stop(self) -> np.ndarray:
        """Termine la dictee et renvoie le PCM mono 16 kHz."""
        with self._lock:
            if not self.is_recording:
                return np.zeros(0, dtype=np.float32)
            self.is_recording = False
            blocks, self._captured = self._captured, []
            self._last_use = time.monotonic()

        self._schedule_close()

        if not blocks:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(blocks).astype(np.float32, copy=False)
        return audio[: int(MAX_RECORD_S * SAMPLE_RATE)]

    def cancel(self) -> None:
        with self._lock:
            self.is_recording = False
            self._captured = []
            self._last_use = time.monotonic()
        self._schedule_close()

    @property
    def elapsed_s(self) -> float:
        with self._lock:
            return time.monotonic() - self._started_at if self.is_recording else 0.0

    def set_device(self, device: int | None) -> None:
        with self._lock:
            if device == self.device:
                return
            was_open = self._stream is not None
        self.close()
        self.device = device
        if was_open:
            self.open()
