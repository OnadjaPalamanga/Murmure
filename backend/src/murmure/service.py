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

from .align import assign_speakers, count_speakers, dated_words, format_transcript
from .audio import Recorder, list_input_devices
from .config import ConfigStore, apply_text_rules
from .diarize import TOTAL_DOWNLOAD_MB as DIARIZATION_MB
from .diarize import DiarizationUnavailable, Diarizer, ensure_models
from .diarize import clear_models as clear_diarization_models
from .diarize import is_installed as diarization_installed
from .diarize import models_present as diarization_models_present
from .download import DownloadWatcher
from .engines.base import SAMPLE_RATE, Engine
from .exports import FORMATS as EXPORT_FORMATS
from .exports import TIMED_FORMATS, line_ending
from .exports import render as render_export
from .history import History, HistoryClosed
from .media import AUDIO_EXT, VIDEO_EXT, find_ffmpeg, load_audio
from .models import build_engine, get_spec, list_models, resolve_spec
from .paths import AUDIO_DIR, MODELS_DIR
from .streaming import PhraseStreamer, normalise_for_engine

log = logging.getLogger(__name__)

# En dessous, c'est un declenchement accidentel : on ne transcrit pas.
MIN_AUDIO_S = 0.25
SILENCE_PEAK = 0.005

# Confiance exigee pour figer la langue d'une dictee continue (voir
# `_remember_language`).
LANGUAGE_PIN_CONFIDENCE = 0.85


# Repli lisible pour les etapes de diarisation. L'interface traduit la cle ;
# ce texte-ci ne sert que si elle ne la connait pas — un frontend plus ancien —
# et il part aussi dans le journal.
def _diarize_message(key: str, params: dict | None) -> str:
    params = params or {}
    if key == "diarize_download":
        total = params.get("total_mb") or 0
        seen = f"{params.get('done_mb', 0)} Mo"
        return f"Téléchargement ({params.get('part', '')}) — {seen}" + (
            f" / {total} Mo" if total else ""
        )
    return "Identification des locuteurs…"


# Dossiers ou un export n'a aucune raison d'atterrir, et ou un fichier depose
# s'execute ou se charge tout seul. Compares sur le chemin resolu, en minuscules.
_FORBIDDEN_PARTS = (
    "start menu",
    "startup",
    "démarrage",
    "system32",
    "syswow64",
    "windows\\tasks",
)


def _refuse_destination(destination: str, fmt: str) -> str | None:
    """Rend la raison du refus, ou None si le chemin est acceptable.

    Trois regles, qui decrivent toutes ce qu'un dialogue « Enregistrer sous »
    produit necessairement : un chemin absolu, dont le dossier parent existe
    deja, et dont l'extension est celle du format choisi.
    """
    raw = (destination or "").strip()
    if not raw:
        return "aucun chemin"

    try:
        path = Path(raw).expanduser()
    except (OSError, ValueError):
        return "chemin illisible"

    if not path.is_absolute():
        return "chemin relatif"

    # `resolve` ecrase les « .. » : sans lui, un chemin passant par un dossier
    # anodin pourrait ressortir dans un dossier interdit.
    resolved = path.resolve()

    if resolved.suffix.lower().lstrip(".") != fmt:
        return f"extension attendue .{fmt}"

    lowered = str(resolved).lower().replace("/", "\\")
    for part in _FORBIDDEN_PARTS:
        if part in lowered:
            return "dossier système"

    # Le dossier parent doit EXISTER. C'etait `mkdir(parents=True)` : creer une
    # arborescence entiere n'est jamais ce qu'un dialogue natif demande, et
    # c'est ce qui permettait de viser n'importe ou sur le disque.
    if not resolved.parent.is_dir():
        return "dossier introuvable"

    return None


def _echoes_prompt(text: str, prompt: str | None) -> bool:
    """Le modele a-t-il recopie son amorce au lieu de transcrire ?

    Whisper imite le style de son amorce ; sur une phrase courte il lui arrive
    de la restituer telle quelle. Mesure sur `whisper-base-cpu` : une phrase de
    2,5 s a rendu « Voici une note dictee, ponctuee normalement, avec majuscules
    et virgules. » — l'amorce, mot pour mot. En dictee continue ce texte serait
    frappe dans le document de l'utilisateur.

    Le seuil de longueur evite de jeter un vrai « Voici. » qui se trouverait
    etre un prefixe de l'amorce.
    """
    if not prompt or len(text) < 20:
        return False

    def flatten(value: str) -> str:
        return " ".join("".join(c if c.isalnum() else " " for c in value.lower()).split())

    return flatten(text) in flatten(prompt)


class Service:
    def __init__(self) -> None:
        self.config = ConfigStore()
        self.history = History()

        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue] = set()

        self._engine: Engine | None = None
        self._engine_lock = threading.Lock()
        # Serialise les decodages. Une phrase et une fenetre de polissage
        # l'attendent ; l'apercu, jamais — il saute son tour plutot que de
        # retarder du texte definitif.
        self._engine_busy = threading.Lock()
        self._loading = False

        self.recorder = Recorder(
            device=self.config.settings.input_device,
            preroll_ms=self.config.settings.preroll_ms,
            keepalive_s=self.config.settings.mic_keepalive_s,
            on_level=self._on_level,
            on_truncated=self._on_truncated,
        )
        self.state = "idle"
        self._level_throttle = 0.0
        self._streamer: PhraseStreamer | None = None
        # Somme des temps moteur des phrases, et langue detectee sur la premiere
        # phrase consequente : seul le thread du streamer y touche.
        self._stream_latency_ms = 0
        self._stream_language: str | None = None
        self._preview_off_logged = False
        # Charge a la premiere diarisation seulement, puis garde resident : un
        # lot de fichiers ne doit pas relire les modeles a chaque piste.
        self._diarizer: Diarizer | None = None
        # Un seul telechargement des modeles de locuteurs a la fois. Deux
        # demandes concurrentes — le bouton des reglages et un fichier importe
        # qui les reclame au passage — ecriraient dans le meme fichier `.part`
        # et se termineraient par un modele tronque que `models_present()`
        # declarerait complet.
        self._diarize_download_lock = threading.Lock()

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

    def _on_truncated(self, limit_s: float, lost_s: float) -> None:
        """La dictee a atteint le plafond d'enregistrement : le dire tout de suite."""
        self.emit(
            {
                "type": "truncated",
                "key": "record_truncated",
                "params": {"minutes": round(limit_s / 60), "lost_seconds": round(lost_s)},
                "message": f"Enregistrement tronqué à {limit_s / 60:.0f} min.",
            }
        )

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

            # `resolve_spec` et non `get_spec` : un identifiant devenu invalide
            # ne doit pas rendre l'application inutilisable a chaque dictee. Si
            # le repli a servi, on repare la configuration tout de suite —
            # sinon l'avertissement reviendrait a chaque chargement et
            # l'interface continuerait d'afficher un modele qui n'existe pas.
            spec = resolve_spec(self.model_id)
            if spec.id != self.model_id:
                self.config.update({"model_id": spec.id})
            self._loading = True
            self.emit({"type": "model_loading", "model_id": spec.id, "label": spec.label})
            try:
                self.emit(
                    {
                        "type": "model_stage",
                        "model_id": spec.id,
                        "stage": "loading",
                        # `key` + `params` pour l'interface, qui existe en deux
                        # langues ; `message` reste le repli lisible.
                        "key": "stage_loading",
                        "params": {"label": spec.label},
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
                        "key": "stage_warmup",
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
                    "key": "stage_downloading",
                    "params": {"label": spec.label},
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
            self.emit(
                {
                    "type": "error",
                    "key": "error_model_load",
                    "params": {"detail": str(exc)},
                    "message": f"Chargement du modele : {exc}",
                }
            )

    def preload(self) -> None:
        if self.config.settings.preload_on_start:
            threading.Thread(target=self._safe_preload, daemon=True).start()

    # ----------------------------------------------------------- dictee

    def arm(self) -> None:
        """Ouvre le micro sans enregistrer (au survol du raccourci)."""
        self.recorder.open()

    def start(self) -> None:
        if self.state in ("recording", "streaming"):
            return
        if self.config.settings.dictation_mode == "continu":
            self._start_stream()
            return
        self.recorder.start()
        self._set_state("recording")

    def cancel(self) -> None:
        streamer, self._streamer = self._streamer, None
        self.recorder.cancel()
        self.recorder.on_block = None
        if streamer is not None:
            streamer.cancel()
        self._set_state("idle")

    def stop_and_transcribe(self) -> None:
        """Termine la dictee et lance la transcription dans un thread."""
        if self.state == "streaming":
            self._stop_stream()
            return
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
                self.emit(
                    {
                        "type": "empty",
                        "key": "empty_too_short",
                        "reason": "trop court ou silencieux",
                    }
                )
                return

            engine = self.ensure_engine()
            result = engine.transcribe(audio, language=self._resolved_language())
            text = self._post_process(result.text)

            if not text:
                self._set_state("idle")
                self.emit(
                    {"type": "empty", "key": "empty_no_speech", "reason": "aucune parole detectee"}
                )
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
        except HistoryClosed:
            # Le service s'arrete pendant qu'un thread « daemon » finissait
            # son travail. Ce n'est pas une panne : rien a signaler a une
            # interface qui est en train de disparaitre.
            log.info("Arret en cours : resultat non enregistre")
        except Exception as exc:  # noqa: BLE001
            log.exception("Echec de la transcription")
            self._set_state("idle")
            self.emit({"type": "error", "message": str(exc)})

    # -------------------------------------------------------- dictee continue

    def _start_stream(self) -> None:
        """Dictee continue : les phrases partent au moteur au fil de la parole."""
        settings = self.config.settings
        self._stream_latency_ms = 0
        self._stream_language = None
        self._preview_off_logged = False
        polishing = settings.polish_mode != "aucun"

        streamer = PhraseStreamer(
            transcribe=self._transcribe_phrase,
            on_phrase=lambda text, index: self.emit(
                {"type": "commit", "text": text, "index": index}
            ),
            # Charge le modele des le declenchement, en parallele des premiers
            # mots, au lieu de faire payer le chargement a la premiere phrase.
            prepare=self.ensure_engine,
            on_activity=lambda speaking: self.emit({"type": "speech", "speaking": speaking}),
            on_error=lambda exc: self.emit({"type": "error", "message": str(exc)}),
            polish=self._polish_window if polishing else None,
            on_revise=(
                lambda text, first, last: self.emit(
                    {"type": "revise", "text": text, "from_index": first, "to_index": last}
                )
            )
            if polishing
            else None,
            preview=self._transcribe_preview,
            on_preview=lambda text: self.emit({"type": "preview", "text": text}),
            silence_ms=settings.phrase_silence_ms,
            max_phrase_s=settings.max_phrase_s,
            polish_max_s=settings.polish_max_s,
            preview_ms=settings.preview_ms,
        )
        streamer.start()
        self._streamer = streamer

        # Avant `start()` : c'est lui qui pousse le pre-roll au consommateur.
        self.recorder.on_block = streamer.feed
        self.recorder.start()
        self._set_state("streaming")

    def _transcribe_phrase(self, audio: np.ndarray) -> str:
        """Transcrit une phrase. Appele en serie par l'unique thread du streamer."""
        if audio.size and float(np.abs(audio).max()) < SILENCE_PEAK:
            return ""
        engine = self.ensure_engine()
        with self._engine_busy:
            result = engine.transcribe(
                normalise_for_engine(audio),
                language=self._resolved_language() or self._stream_language,
            )
        self._remember_language(result, len(audio) / SAMPLE_RATE)
        self._stream_latency_ms += result.latency_ms

        text = self._post_process(result.text)
        # Sur un souffle ou une syllabe isolee, Whisper rend volontiers « ... »,
        # « - » ou « Merci. ». En differe c'est noye dans le reste ; en continu
        # ce serait frappe tel quel dans le document. Une phrase sans aucune
        # lettre ni chiffre n'a rien a y faire.
        if not any(ch.isalnum() for ch in text):
            return ""
        if _echoes_prompt(text, getattr(engine, "initial_prompt", None)):
            log.info("Phrase ignoree : le modele a recopie son amorce")
            return ""
        return text

    def _polish_window(self, audio: np.ndarray) -> str:
        """Re-decode d'un bloc une fenetre de plusieurs phrases deja emises.

        C'est le CONTEXTE, et rien d'autre, qui repare la ponctuation : le meme
        modele, sur le meme audio, mais voyant la phrase entiere au lieu d'un
        groupe de souffle. Il rend ici ce qu'il rend en mode differe.

        La langue reste celle epinglee pour la dictee : une fenetre re-detectee
        a part ferait exactement le derapage que `_remember_language` evite.
        """
        if audio.size and float(np.abs(audio).max()) < SILENCE_PEAK:
            return ""
        engine = self.ensure_engine()
        with self._engine_busy:
            result = engine.transcribe(
                normalise_for_engine(audio),
                language=self._resolved_language() or self._stream_language,
            )
        # Ce calcul-la produit le texte livre : il compte dans la latence
        # annoncee, sans quoi le chiffre flatterait le mode continu.
        self._stream_latency_ms += result.latency_ms

        text = self._post_process(result.text)
        if _echoes_prompt(text, getattr(engine, "initial_prompt", None)):
            log.info("Fenetre ignoree : le modele a recopie son amorce")
            return ""
        return text

    def _transcribe_preview(self, audio: np.ndarray) -> str:
        """Apercu de la phrase en cours. Provisoire : ni frappe, ni historise.

        Ne prend jamais le moteur en attente. Si une phrase ou une fenetre est
        en cours de decodage, l'apercu saute son tour : un affichage ne fait
        pas patienter du texte definitif. Sa latence n'entre donc pas non plus
        dans celle de la dictee — il n'en produit aucun mot livre.
        """
        engine = self._engine
        if engine is None or not engine.is_loaded:
            return ""  # le modele charge encore : les premiers mots attendront
        if engine.device != "cuda":
            # Sur processeur, huit secondes coutent plusieurs secondes de calcul
            # au cran le plus lent : l'apercu prendrait a la dictee le temps
            # qu'il pretend lui faire gagner.
            if not self._preview_off_logged:
                self._preview_off_logged = True
                log.info("Apercu inactif : le moteur tourne sur processeur")
            return ""
        if not self._engine_busy.acquire(blocking=False):
            return ""
        try:
            result = engine.transcribe(
                normalise_for_engine(audio),
                language=self._resolved_language() or self._stream_language,
            )
        finally:
            self._engine_busy.release()

        text = self._post_process(result.text)
        # Memes rejets qu'une vraie phrase : sur une syllabe isolee ou une
        # amorce recopiee, afficher la sortie brute donnerait a croire que la
        # dictee derape alors que ce texte n'aurait jamais ete retenu.
        if not any(ch.isalnum() for ch in text):
            return ""
        if _echoes_prompt(text, getattr(engine, "initial_prompt", None)):
            return ""
        return text

    def _remember_language(self, result, seconds: float) -> None:
        """Fige la langue detectee pour le reste de la dictee.

        En differe, une dictee entiere donne lieu a UNE detection. En continu,
        detecter phrase par phrase fait derailler les petits modeles : sur une
        dictee francaise de 20 s, `whisper-small-cpu` a rendu « Уплиток, мюрмюр »
        puis du roumain. Detecter une fois par dictee, ce n'est pas forcer la
        langue — c'est retrouver la granularite du mode differe.

        Parakeet et Canary ne rapportent jamais de langue detectee : ils
        renvoient celle qu'on leur passe. Rien ne s'epingle donc pour eux, et
        leur detection interne reste intacte.
        """
        if self._stream_language is not None or seconds < 2.0:
            return
        language = getattr(result, "language", None)
        confidence = (getattr(result, "extra", None) or {}).get("language_probability", 0.0)
        # Seuil haut : figer une detection douteuse est pire que ne rien figer.
        # Sur la meme dictee francaise, `small` annonce fr a 0,98 et `base`
        # annonce ro a 0,73 — la barre passe entre les deux.
        if language and confidence >= LANGUAGE_PIN_CONFIDENCE:
            self._stream_language = language
            log.info("Dictee continue : langue fixee sur %s (%.0f%%)", language, confidence * 100)

    def _stop_stream(self) -> None:
        audio = self.recorder.stop()
        self.recorder.on_block = None
        streamer, self._streamer = self._streamer, None
        if streamer is None:
            self._set_state("idle")
            return

        # La derniere phrase est encore au moteur : on le dit, plutot que de
        # laisser croire que la dictee est finie alors qu'il manque une ligne.
        self._set_state("transcribing", audio_seconds=round(len(audio) / SAMPLE_RATE, 2))
        threading.Thread(
            target=self._stream_finish_worker, args=(streamer, audio), daemon=True
        ).start()

    def _stream_finish_worker(self, streamer: PhraseStreamer, audio: np.ndarray) -> None:
        try:
            text = " ".join(p for p in streamer.finish() if p).strip()
            if not text:
                self._set_state("idle")
                self.emit(
                    {"type": "empty", "key": "empty_no_speech", "reason": "aucune parole detectee"}
                )
                return

            audio_seconds = len(audio) / SAMPLE_RATE
            latency_ms = self._stream_latency_ms
            audio_path = self._save_audio(audio) if self.config.settings.keep_audio else None
            device = self._engine.device if self._engine else "cpu"

            # Une entree d'historique par dictee, pas une par phrase : c'est la
            # dictee que l'utilisateur voudra retrouver, pas ses fragments.
            entry = self.history.add(
                text=text,
                model_id=self.model_id,
                device=device,
                audio_seconds=audio_seconds,
                latency_ms=latency_ms,
                audio_path=audio_path,
            )

            self._set_state("idle")
            self.emit(
                {
                    "type": "final",
                    "entry": entry,
                    "latency_ms": latency_ms,
                    "realtime_factor": round(audio_seconds / (latency_ms / 1000), 1)
                    if latency_ms
                    else 0.0,
                    "device": device,
                    # Le texte a deja ete transmis phrase par phrase : l'overlay
                    # ne doit pas le reinjecter une seconde fois au curseur.
                    "streamed": True,
                }
            )
        except HistoryClosed:
            # Le service s'arrete pendant qu'un thread « daemon » finissait
            # son travail. Ce n'est pas une panne : rien a signaler a une
            # interface qui est en train de disparaitre.
            log.info("Arret en cours : resultat non enregistre")
        except Exception as exc:  # noqa: BLE001
            log.exception("Fin de dictee continue en echec")
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
                        "key": "file_reading",
                        "params": {"name": path.name},
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
                        "key": "file_transcribing",
                        "params": {"name": path.name, "minutes": round(duration / 60, 1)},
                        "message": f"Transcription de {path.name} ({duration / 60:.1f} min)…",
                    }
                )

                diarizing = bool(self.config.settings.diarize_files)
                keep_words = bool(self.config.settings.timestamps_files)
                engine = self.ensure_engine()
                # Dater les mots coute du calcul : on ne le demande que si
                # quelqu'un va s'en servir — pour attribuer les locuteurs, ou
                # pour rendre l'entree exportable en sous-titres.
                result = engine.transcribe(
                    audio,
                    language=self._resolved_language(),
                    timestamps=diarizing or keep_words,
                )
                text = self._post_process(result.text)

                if not text:
                    self.emit(
                        {
                            "type": "file_done",
                            "name": path.name,
                            "ok": False,
                            "key": "file_no_speech",
                            "message": "Aucune parole détectée",
                        }
                    )
                    continue

                segments = None
                speakers = 0
                # La diarisation date les mots pour son propre compte, mais les
                # conserver quand le reglage dit de ne pas les garder ecrirait
                # dans la base ce que l'utilisateur a refuse.
                words = [w.to_dict() for w in result.words] if keep_words and result.words else None
                if diarizing:
                    # `text` sert de repli : il est deja mis en forme, et
                    # `_diarize_file` ne doit pas repasser dessus. Appliquer les
                    # remplacements deux fois sur le meme texte les composerait
                    # — une regle « a » -> « aa » donnerait « aaaa ».
                    text, segments, speakers, attributed = self._diarize_file(
                        audio, result, path, fallback=text, index=index, total=total
                    )
                    # Les memes mots, portant desormais leur locuteur. Absents
                    # si la diarisation a echoue : on garde alors les mots nus,
                    # qui suffisent a un export sans nom de locuteur.
                    if keep_words and attributed:
                        words = attributed

                entry = self.history.add(
                    text=text,
                    model_id=self.model_id,
                    device=result.device,
                    audio_seconds=result.audio_seconds,
                    latency_ms=result.latency_ms,
                    audio_path=str(path),
                    segments=segments,
                    words=words,
                )
                self.emit(
                    {
                        "type": "file_done",
                        "name": path.name,
                        "ok": True,
                        "entry": entry,
                        "latency_ms": result.latency_ms,
                        "realtime_factor": round(result.realtime_factor, 1),
                        "speakers": speakers,
                    }
                )
            except HistoryClosed:
                log.info("Arret en cours : lot de fichiers interrompu")
                return
            except Exception as exc:  # noqa: BLE001
                log.exception("Echec sur le fichier %s", path)
                self.emit(
                    {"type": "file_done", "name": path.name, "ok": False, "message": str(exc)}
                )

        self._set_state("idle")
        self.emit({"type": "files_finished", "total": total})

    def _diarize_file(
        self, audio: np.ndarray, result, path: Path, *, fallback: str, index: int, total: int
    ) -> tuple[str, list[dict] | None, int, list[dict] | None]:
        """Attribue le texte aux locuteurs.

        Rend (texte, tours de parole, nombre de locuteurs, mots dates). Les mots
        rendus portent leur locuteur, contrairement a ceux du moteur.

        **Ne fait jamais perdre une transcription.** Tout ce qui peut echouer ici
        — dependance absente, telechargement coupe, modele illisible — se solde
        par un retour a `fallback`, le texte continu deja obtenu et parfaitement
        utilisable. Perdre une heure de transcription parce que l'attribution
        des locuteurs a echoue serait hors de proportion.
        """
        settings = self.config.settings
        name = path.name

        def stage(key: str, params: dict | None = None) -> None:
            self.emit(
                {
                    "type": "file_progress",
                    "stage": "diarizing",
                    "index": index,
                    "total": total,
                    "name": name,
                    "key": key,
                    "params": {"name": name, **(params or {})},
                    "message": _diarize_message(key, params),
                }
            )

        try:
            if not result.words:
                # Le moteur n'a pas date les mots : sans eux il n'y a rien a
                # attribuer. Cas d'un modele local depose a la main.
                log.info("Diarisation impossible sur %s : aucun mot date", name)
                return fallback, None, 0, None

            stage("diarize_running")
            # Le verrou attend : si les reglages sont en train de telecharger
            # les memes modeles, ce fichier repartira une fois qu'ils seront la,
            # plutot que de retelecharger par-dessus.
            with self._diarize_download_lock:
                ensure_models(on_progress=stage)

            if self._diarizer is None:
                self._diarizer = Diarizer()
            diarized = self._diarizer.diarize(
                audio,
                num_speakers=int(settings.diarize_speakers or 0),
                threshold=float(settings.diarize_threshold),
            )
            if not diarized.segments:
                return fallback, None, 0, None

            blocks = assign_speakers(result.words, diarized.segments)
            if not blocks:
                return fallback, None, 0, None

            # Les remplacements et le rognage s'appliquent bloc par bloc : les
            # appliquer a la transcription mise en forme risquerait de manger un
            # prefixe « Locuteur 2 : ».
            for block in blocks:
                block.text = self._post_process(block.text)

            text = format_transcript(blocks)
            if not text.strip():
                return fallback, None, 0, None

            return text, [b.to_dict() for b in blocks], count_speakers(blocks), dated_words(blocks)

        except DiarizationUnavailable as exc:
            log.warning("Diarisation indisponible sur %s : %s", name, exc)
            self.emit(
                {
                    "type": "error",
                    "key": exc.key or "error_diarize_unavailable",
                    "params": {**exc.params, "detail": str(exc)},
                    "message": f"Diarisation indisponible : {exc}",
                }
            )
        except Exception as exc:  # noqa: BLE001 - jamais au prix de la transcription
            log.exception("Diarisation en echec sur %s", name)
            self.emit(
                {
                    "type": "error",
                    "key": "error_diarize_failed",
                    "params": {"detail": str(exc)},
                    "message": f"Diarisation en échec : {exc}",
                }
            )

        return fallback, None, 0, None

    # --------------------------------------- modeles de diarisation (reglages)

    def diarization_download(self) -> dict:
        """Telecharge les modeles depuis les reglages, sans attendre un fichier.

        Les faire venir au premier import marche, mais 35 Mo au milieu d'une
        transcription d'une heure surprennent. Ici, l'utilisateur choisit le
        moment — et peut verifier que ca a marche avant d'en avoir besoin.
        """

        def stage(key: str, params: dict | None = None) -> None:
            self.emit(
                {
                    "type": "diarization_progress",
                    "key": key,
                    "params": params or {},
                    "message": _diarize_message(key, params),
                }
            )

        # Sans attendre : un second clic pendant le telechargement ne doit pas
        # en lancer un deuxieme, il doit ne rien faire.
        if not self._diarize_download_lock.acquire(blocking=False):
            log.info("Telechargement des modeles de diarisation deja en cours")
            return self.snapshot()

        try:
            stage("diarize_download", {"part": "segmentation", "done_mb": 0, "total_mb": 0})
            ensure_models(on_progress=stage)
        except DiarizationUnavailable as exc:
            log.warning("Telechargement des modeles de diarisation en echec : %s", exc)
            self.emit(
                {
                    "type": "error",
                    "key": exc.key or "error_diarize_unavailable",
                    "params": {**exc.params, "detail": str(exc)},
                    "message": str(exc),
                }
            )
        finally:
            self._diarize_download_lock.release()
            # L'interface a une barre de progression a refermer, et elle doit
            # l'apprendre meme quand le telechargement a echoue.
            self.emit(
                {"type": "diarization_done", "ready": diarization_models_present()}
            )
        return self.snapshot()

    def diarization_clear(self) -> dict:
        """Supprime les modeles telecharges.

        Le diarizeur resident est lache AVANT l'effacement : onnxruntime garde
        les fichiers ouverts tant qu'une session vit, et Windows refuse de
        supprimer un fichier ouvert — on effacerait a moitie.
        """
        self._diarizer = None
        clear_diarization_models()
        return self.snapshot()

    # ------------------------------------------------------ export d'une entree

    def export_entry(self, entry_id: str, fmt: str, destination: str) -> dict:
        """Ecrit une entree d'historique en sous-titres, en JSON ou en texte.

        Le chemin vient d'un dialogue « Enregistrer sous » natif : c'est
        l'utilisateur qui l'a designe. On ecrit donc ou il a dit — mais on
        verifie que le chemin ressemble bien a ce qu'un dialogue produit. Sans
        ce controle, la commande etait une primitive d'ecriture arbitraire :
        `history_update` mettait le contenu voulu dans une entree, `export_entry`
        l'ecrivait dans le dossier Demarrage, et c'etait une execution de code au
        demarrage suivant.

        Aucune exception ne remonte : un disque plein ou un dossier devenu
        inaccessible se dit dans l'interface, il ne casse pas la connexion.
        """
        fmt = (fmt or "").lower().strip()
        if fmt not in EXPORT_FORMATS:
            return {
                "ok": False,
                "key": "export_bad_format",
                "params": {"format": fmt},
                "message": f"Format d'export inconnu : {fmt}",
            }

        refusal = _refuse_destination(destination, fmt)
        if refusal is not None:
            log.warning("Export refuse vers %s : %s", destination, refusal)
            return {
                "ok": False,
                "key": "export_bad_path",
                "params": {"detail": refusal},
                "message": f"Emplacement refusé : {refusal}",
            }

        entry = self.history.get(entry_id)
        if entry is None:
            return {
                "ok": False,
                "key": "export_missing_entry",
                "params": {},
                "message": "Cette dictée n'est plus dans l'historique.",
            }

        words = entry.get("words") or []
        turns = entry.get("segments") or []
        # SRT et WebVTT sont faits de reperes temporels : sans datation il n'y a
        # pas de version degradee a proposer, seulement un fichier vide.
        if fmt in TIMED_FORMATS and not words and not turns:
            return {
                "ok": False,
                "key": "export_no_timestamps",
                "params": {},
                "message": "Cette dictée n'a pas de repères temporels.",
            }

        content = render_export(
            fmt,
            words=words,
            turns=turns,
            text=entry.get("text", ""),
            meta={
                "id": entry.get("id"),
                "created_at": entry.get("created_at"),
                "model_id": entry.get("model_id"),
                "audio_seconds": entry.get("audio_seconds"),
                "source": entry.get("audio_path"),
            },
        )

        path = Path(destination).expanduser().resolve()
        try:
            with path.open("w", encoding="utf-8", newline=line_ending(fmt)) as handle:
                handle.write(content)
        except OSError as exc:
            log.warning("Export impossible vers %s : %s", path, exc)
            return {
                "ok": False,
                "key": "export_write_failed",
                "params": {"detail": str(exc)},
                "message": f"Écriture du fichier impossible : {exc}",
            }

        log.info("Entree %s exportee en %s vers %s", entry_id, fmt, path)
        return {
            "ok": True,
            "key": "export_done",
            "params": {"name": path.name, "format": fmt},
            "message": f"Exporté vers {path.name}",
            "path": str(path),
            "bytes": len(content.encode("utf-8")),
        }

    # ------------------------------------------------------------- reglages

    def _resolved_language(self) -> str | None:
        """None = detection automatique, ce qui preserve les anglicismes dits tels quels."""
        value = (self.config.settings.language or "auto").strip().lower()
        return None if value in ("", "auto") else value

    def _post_process(self, text: str) -> str:
        return apply_text_rules(text, self.config.settings)

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
            # L'interface doit pouvoir dire ce qui manque AVANT qu'on lance un
            # fichier d'une heure : la dependance, ou seulement les modeles
            # (35 Mo, telecharges au premier usage).
            "diarization": {
                "available": diarization_installed(),
                "models_ready": diarization_models_present(),
                "download_mb": DIARIZATION_MB,
            },
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
        if self._streamer is not None:
            self._streamer.cancel()
            self._streamer = None
        self.recorder.close()
        with self._engine_lock:
            if self._engine is not None:
                self._engine.unload()
        self.history.close()
