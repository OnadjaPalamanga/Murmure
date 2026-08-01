"""Orchestration : micro -> moteur -> historique, plus la diffusion d'evenements.

Toute la logique metier vit ici ; `server.py` n'est qu'un transport WebSocket.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from .audio import Recorder, list_input_devices
from .config import ConfigStore
from .download import DownloadWatcher
from .engines.base import SAMPLE_RATE, Engine
from .history import History
from .media import AUDIO_EXT, VIDEO_EXT, find_ffmpeg, load_audio
from .models import build_engine, get_spec, list_models
from .paths import AUDIO_DIR, MODELS_DIR

log = logging.getLogger(__name__)

# En dessous, c'est un declenchement accidentel : on ne transcrit pas.
MIN_AUDIO_S = 0.25
SILENCE_PEAK = 0.005


class Service:
    def __init__(self) -> None:
        self.config = ConfigStore()
        self.history = History()

        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue] = set()

        self._engine: Engine | None = None
        self._engine_lock = threading.Lock()
        self._loading = False

        self.recorder = Recorder(
            device=self.config.settings.input_device,
            preroll_ms=self.config.settings.preroll_ms,
            keepalive_s=self.config.settings.mic_keepalive_s,
            on_level=self._on_level,
        )
        self.state = "idle"
        self._level_throttle = 0.0

    # ------------------------------------------------------- evenements

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def emit(self, event: dict[str, Any]) -> None:
        """Diffuse un evenement. Appelable depuis n'importe quel thread."""
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._emit_now, event)

    def _emit_now(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Un client lent ne doit pas bloquer la capture audio.
                log.debug("File abonne pleine, evenement abandonne")

    def _set_state(self, state: str, **extra: Any) -> None:
        self.state = state
        self.emit({"type": "state", "state": state, **extra})

    def _on_level(self, peak: float) -> None:
        # ~25 images/s suffisent pour la forme d'onde ; au-dela on sature le WS.
        now = time.monotonic()
        if now - self._level_throttle < 0.04:
            return
        self._level_throttle = now
        self.emit({"type": "level", "value": round(min(peak, 1.0), 4)})

    # ---------------------------------------------------------- modeles

    @property
    def model_id(self) -> str:
        return self.config.settings.model_id

    def engine_status(self) -> dict:
        return {
            "model_id": self.model_id,
            "loaded": bool(self._engine and self._engine.is_loaded),
            "loading": self._loading,
            "device": self._engine.device if self._engine else "cpu",
        }

    def ensure_engine(self) -> Engine:
        """Charge le modele courant si besoin. Bloquant, a appeler hors boucle asyncio."""
        with self._engine_lock:
            if self._engine and self._engine.is_loaded:
                return self._engine

            spec = get_spec(self.model_id)
            self._loading = True
            self.emit({"type": "model_loading", "model_id": spec.id, "label": spec.label})
            try:
                self.emit(
                    {
                        "type": "model_stage",
                        "model_id": spec.id,
                        "stage": "loading",
                        "message": f"Préparation de {spec.label}…",
                    }
                )
                engine = build_engine(spec, prefer_gpu=self.config.settings.prefer_gpu)

                # load() telecharge le modele s'il manque : on observe le cache
                # grossir pendant ce temps pour ne pas laisser l'ecran fige.
                with DownloadWatcher(
                    MODELS_DIR / "hub", on_progress=self._on_download_progress(spec)
                ):
                    engine.load()

                self.emit(
                    {
                        "type": "model_stage",
                        "model_id": spec.id,
                        "stage": "warmup",
                        "message": "Préparation des noyaux GPU…",
                    }
                )
                engine.warmup()
                self._engine = engine
            finally:
                self._loading = False

            self.emit({"type": "model_ready", **self.engine_status()})
            return self._engine

    def _on_download_progress(self, spec):
        """Rapporte les octets reellement recus. Le total est inconnu : chaque
        bibliotheque ne prend qu'une partie de son depot, l'annoncer serait faux."""

        def report(downloaded: int) -> None:
            self.emit(
                {
                    "type": "model_stage",
                    "model_id": spec.id,
                    "stage": "downloading",
                    "downloaded_bytes": downloaded,
                    "total_bytes": 0,
                    "message": f"Téléchargement de {spec.label}…",
                }
            )

        return report

    def set_model(self, model_id: str) -> dict:
        get_spec(model_id)  # leve KeyError si inconnu
        with self._engine_lock:
            if self._engine is not None:
                self._engine.unload()
                self._engine = None
        self.config.update({"model_id": model_id})
        threading.Thread(target=self._safe_preload, daemon=True).start()
        return self.engine_status()

    def _safe_preload(self) -> None:
        try:
            self.ensure_engine()
        except Exception as exc:  # noqa: BLE001
            log.exception("Prechargement du modele impossible")
            self.emit({"type": "error", "message": f"Chargement du modele : {exc}"})

    def preload(self) -> None:
        if self.config.settings.preload_on_start:
            threading.Thread(target=self._safe_preload, daemon=True).start()

    # ----------------------------------------------------------- dictee

    def arm(self) -> None:
        """Ouvre le micro sans enregistrer (au survol du raccourci)."""
        self.recorder.open()

    def start(self) -> None:
        if self.state == "recording":
            return
        self.recorder.start()
        self._set_state("recording")

    def cancel(self) -> None:
        self.recorder.cancel()
        self._set_state("idle")

    def stop_and_transcribe(self) -> None:
        """Termine la dictee et lance la transcription dans un thread."""
        if self.state != "recording":
            return
        audio = self.recorder.stop()
        self._set_state("transcribing", audio_seconds=round(len(audio) / SAMPLE_RATE, 2))
        threading.Thread(target=self._transcribe_worker, args=(audio,), daemon=True).start()

    def _transcribe_worker(self, audio: np.ndarray) -> None:
        try:
            duration = len(audio) / SAMPLE_RATE
            if duration < MIN_AUDIO_S or (audio.size and float(np.abs(audio).max()) < SILENCE_PEAK):
                self._set_state("idle")
                self.emit({"type": "empty", "reason": "trop court ou silencieux"})
                return

            engine = self.ensure_engine()
            result = engine.transcribe(audio, language=self._resolved_language())
            text = self._post_process(result.text)

            if not text:
                self._set_state("idle")
                self.emit({"type": "empty", "reason": "aucune parole detectee"})
                return

            audio_path = self._save_audio(audio) if self.config.settings.keep_audio else None
            entry = self.history.add(
                text=text,
                model_id=self.model_id,
                device=result.device,
                audio_seconds=result.audio_seconds,
                latency_ms=result.latency_ms,
                audio_path=audio_path,
            )

            self._set_state("idle")
            self.emit(
                {
                    "type": "final",
                    "entry": entry,
                    "latency_ms": result.latency_ms,
                    "realtime_factor": round(result.realtime_factor, 1),
                    "device": result.device,
                }
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("Echec de la transcription")
            self._set_state("idle")
            self.emit({"type": "error", "message": str(exc)})

    # ------------------------------------------------------ fichiers importes

    def transcribe_files(self, paths: list[str]) -> None:
        """Transcrit des fichiers audio/video deja sur le disque, en tache de fond."""
        threading.Thread(target=self._files_worker, args=(list(paths),), daemon=True).start()

    def _files_worker(self, paths: list[str]) -> None:
        total = len(paths)
        for index, raw_path in enumerate(paths, start=1):
            path = Path(raw_path)
            try:
                self.emit(
                    {
                        "type": "file_progress",
                        "stage": "reading",
                        "index": index,
                        "total": total,
                        "name": path.name,
                        "message": f"Lecture de {path.name}…",
                    }
                )
                audio = load_audio(path)
                duration = len(audio) / SAMPLE_RATE

                self._set_state("transcribing", audio_seconds=round(duration, 2))
                self.emit(
                    {
                        "type": "file_progress",
                        "stage": "transcribing",
                        "index": index,
                        "total": total,
                        "name": path.name,
                        "audio_seconds": round(duration, 2),
                        "message": f"Transcription de {path.name} ({duration / 60:.1f} min)…",
                    }
                )

                engine = self.ensure_engine()
                result = engine.transcribe(audio, language=self._resolved_language())
                text = self._post_process(result.text)

                if not text:
                    self.emit(
                        {
                            "type": "file_done",
                            "name": path.name,
                            "ok": False,
                            "message": "Aucune parole détectée",
                        }
                    )
                    continue

                entry = self.history.add(
                    text=text,
                    model_id=self.model_id,
                    device=result.device,
                    audio_seconds=result.audio_seconds,
                    latency_ms=result.latency_ms,
                    audio_path=str(path),
                )
                self.emit(
                    {
                        "type": "file_done",
                        "name": path.name,
                        "ok": True,
                        "entry": entry,
                        "latency_ms": result.latency_ms,
                        "realtime_factor": round(result.realtime_factor, 1),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("Echec sur le fichier %s", path)
                self.emit(
                    {"type": "file_done", "name": path.name, "ok": False, "message": str(exc)}
                )

        self._set_state("idle")
        self.emit({"type": "files_finished", "total": total})

    # ------------------------------------------------------------- reglages

    def _resolved_language(self) -> str | None:
        """None = detection automatique, ce qui preserve les anglicismes dits tels quels."""
        value = (self.config.settings.language or "auto").strip().lower()
        return None if value in ("", "auto") else value

    def _post_process(self, text: str) -> str:
        text = " ".join(text.split()).strip()
        for src, dst in (self.config.settings.replacements or {}).items():
            if src:
                text = text.replace(src, dst)
        if self.config.settings.trim_trailing_period:
            text = text.rstrip(".").rstrip()
        return text

    def _save_audio(self, audio: np.ndarray) -> str | None:
        try:
            AUDIO_DIR.mkdir(parents=True, exist_ok=True)
            path = AUDIO_DIR / f"{datetime.now():%Y-%m-%d_%H-%M-%S}.wav"
            sf.write(str(path), audio, SAMPLE_RATE, subtype="PCM_16")
            return str(path)
        except OSError:
            log.warning("Ecriture du wav impossible", exc_info=True)
            return None

    # --------------------------------------------------------- reglages

    def snapshot(self) -> dict:
        return {
            "state": self.state,
            "engine": self.engine_status(),
            "settings": self.config.settings.to_dict(),
            "models": [s.to_dict() for s in list_models()],
            "devices": [d.to_dict() for d in list_input_devices()],
            "stats": self.history.stats(),
            # Le selecteur de fichiers du frontend se construit a partir d'ici.
            "media_extensions": {"audio": AUDIO_EXT, "video": VIDEO_EXT},
            "has_ffmpeg": find_ffmpeg() is not None,
        }

    def update_settings(self, changes: dict) -> dict:
        model_changed = "model_id" in changes and changes["model_id"] != self.model_id
        settings = self.config.update({k: v for k, v in changes.items() if k != "model_id"})

        if "input_device" in changes:
            self.recorder.set_device(settings.input_device)
        if "preroll_ms" in changes:
            self.recorder.preroll_ms = settings.preroll_ms
        if "mic_keepalive_s" in changes:
            self.recorder.keepalive_s = settings.mic_keepalive_s
        if model_changed:
            self.set_model(changes["model_id"])

        return self.snapshot()

    def shutdown(self) -> None:
        self.recorder.close()
        with self._engine_lock:
            if self._engine is not None:
                self._engine.unload()
        self.history.close()
