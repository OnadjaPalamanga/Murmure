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
        on_block=None,
        on_truncated=None,
    ) -> None:
        self.device = device
        self.keepalive_s = keepalive_s
        self.on_level = on_level
        # Consommateur temps reel des blocs enregistres (dictee continue). Doit
        # se contenter d'empiler : il est appele sur le thread audio.
        self.on_block = on_block
        # Prevenu quand `MAX_RECORD_S` a mordu. Sans lui, la dictee etait
        # tronquee en silence et l'utilisateur decouvrait une transcription qui
        # s'arretait au milieu d'une phrase, sans rien pour l'expliquer.
        self.on_truncated = on_truncated

        self._lock = threading.RLock()
        self._stream: sd.InputStream | None = None
        self._blocksize = int(SAMPLE_RATE * BLOCK_MS / 1000)

        self._preroll: deque[np.ndarray] = deque(maxlen=self._preroll_blocks(preroll_ms))
        self._preroll_ms = preroll_ms
        self._captured: list[np.ndarray] = []

        self.is_recording = False
        self._started_at = 0.0
        self._last_use = 0.0
        self._idle_timer: threading.Timer | None = None

    # ------------------------------------------------------- pre-roll

    @staticmethod
    def _preroll_blocks(preroll_ms: float) -> int:
        return max(1, int(preroll_ms / BLOCK_MS))

    @property
    def preroll_ms(self) -> int:
        return self._preroll_ms

    @preroll_ms.setter
    def preroll_ms(self, value: int) -> None:
        """Redimensionne VRAIMENT le tampon circulaire.

        C'etait un simple attribut, que plus rien ne relisait apres la
        construction : le `maxlen` de la deque restait celui du demarrage, et
        deplacer le curseur dans les reglages n'avait aucun effet jusqu'au
        redemarrage du service. Le reglage etait ecrit, affiche, persiste, et
        ne faisait rien.
        """
        with self._lock:
            self._preroll_ms = value
            wanted = self._preroll_blocks(value)
            if self._preroll.maxlen == wanted:
                return
            # Les blocs deja capturés sont conserves : la deque n'en garde que
            # les plus recents si elle retrecit, ce qui est exactement le sens
            # d'un pre-roll plus court.
            self._preroll = deque(self._preroll, maxlen=wanted)

    # ---------------------------------------------------------------- flux

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if status:
            log.debug("Statut flux audio : %s", status)

        block = indata[:, 0].copy() if indata.ndim > 1 else indata.copy()

        with self._lock:
            recording = self.is_recording
            if recording:
                self._captured.append(block)
                # Sous le verrou, pour que l'ordre des blocs vus par le
                # consommateur soit exactement celui de `_captured` : `start()`
                # lui pousse le pre-roll et ne doit pas etre double par un bloc
                # arrive entre-temps.
                self._notify_block(block)
            else:
                self._preroll.append(block)

        if self.on_level is not None:
            peak = float(np.abs(block).max()) if block.size else 0.0
            self.on_level(peak)

    def _notify_block(self, block: np.ndarray) -> None:
        """Transmet un bloc au consommateur temps reel, sans jamais lui laisser
        casser la capture : une exception ici couperait le flux audio."""
        hook = self.on_block
        if hook is None:
            return
        try:
            hook(block)
        except Exception:  # noqa: BLE001
            log.debug("Hook on_block en erreur", exc_info=True)

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
            # Le consommateur temps reel a droit au pre-roll lui aussi, sinon la
            # dictee continue perd le premier mot que tout ce mecanisme protege.
            for block in self._captured:
                self._notify_block(block)
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

        ceiling = int(MAX_RECORD_S * SAMPLE_RATE)
        if len(audio) > ceiling:
            lost_s = (len(audio) - ceiling) / SAMPLE_RATE
            log.warning(
                "Dictee tronquee a %.0f s : %.0f s ecartees", MAX_RECORD_S, lost_s
            )
            # Le dire, et pas seulement au journal : une transcription qui
            # s'arrete au milieu d'une phrase sans explication est le genre de
            # defaut qu'on met des semaines a comprendre.
            if self.on_truncated is not None:
                try:
                    self.on_truncated(MAX_RECORD_S, lost_s)
                except Exception:  # noqa: BLE001
                    log.debug("Hook on_truncated en erreur", exc_info=True)
            audio = audio[:ceiling]
        return audio

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
