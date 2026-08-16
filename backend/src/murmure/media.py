"""Lecture de n'importe quel fichier contenant de l'audio, vers du PCM 16 kHz mono.

Deux chemins :
  * `soundfile` pour les formats audio courants (wav, flac, ogg, mp3...) — rapide,
    sans processus externe ;
  * `ffmpeg` en repli, qui couvre tout le reste, y compris les videos dont on
    n'extrait que la piste son.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import numpy as np

from .engines.base import SAMPLE_RATE
from .paths import PROJECT_ROOT

log = logging.getLogger(__name__)

# Extensions proposees dans le selecteur de fichiers, et retenues quand la
# ligne de commande fouille un dossier.
AUDIO_EXT = ["wav", "mp3", "m4a", "aac", "flac", "ogg", "opus", "wma", "aiff", "aif"]
VIDEO_EXT = ["mp4", "mkv", "mov", "avi", "webm", "m4v", "wmv", "flv", "mpg", "mpeg"]


class MediaError(RuntimeError):
    """Fichier illisible ou sans piste audio exploitable."""


def find_ffmpeg() -> str | None:
    """ffmpeg fourni avec le projet, sinon celui du PATH.

    `PROJECT_ROOT` vaut None hors installation editable : il n'y a alors pas de
    dossier `bin/` du projet ou regarder, et seul le PATH repond.
    """
    if PROJECT_ROOT is not None:
        bundled = PROJECT_ROOT / "bin" / "ffmpeg.exe"
        if bundled.exists():
            return str(bundled)
    return shutil.which("ffmpeg")


def _via_soundfile(path: Path) -> np.ndarray:
    # Importe ici et non en tete de module : `AUDIO_EXT`, `VIDEO_EXT` et
    # `find_ffmpeg` sont de simples donnees, et la liste des extensions sert a
    # decider QUELS fichiers traiter — bien avant d'en lire un seul. Charger
    # tout le decodage audio pour repondre a cette question la rendrait
    # indisponible partout ou la pile audio n'est pas installee.
    import soundfile as sf
    import soxr

    audio, sr = sf.read(str(path), dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)  # mono
    if sr != SAMPLE_RATE:
        audio = soxr.resample(audio, sr, SAMPLE_RATE)
    return np.ascontiguousarray(audio, dtype=np.float32)


def _via_ffmpeg(path: Path) -> np.ndarray:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise MediaError(
            "Ce format demande ffmpeg, introuvable. Place ffmpeg.exe dans le "
            "dossier bin/ du projet, ou installe-le dans le PATH."
        )

    # Decodage direct au format cible : ffmpeg fait le reechantillonnage et le
    # mixage mono, on recupere du PCM brut sur la sortie standard.
    command = [
        ffmpeg, "-nostdin", "-loglevel", "error",
        "-i", str(path),
        "-vn",  # ignore la video
        "-f", "f32le", "-acodec", "pcm_f32le",
        "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-",
    ]
    proc = subprocess.run(
        command,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0 or not proc.stdout:
        detail = proc.stderr.decode("utf-8", "replace").strip()[:300]
        raise MediaError(f"ffmpeg n'a pas pu lire ce fichier. {detail}")

    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


def load_audio(path: str | Path) -> np.ndarray:
    """PCM mono float32 a 16 kHz, quel que soit le format d'entree."""
    path = Path(path)
    if not path.exists():
        raise MediaError(f"Fichier introuvable : {path}")

    try:
        return _via_soundfile(path)
    except MediaError:
        raise
    except Exception as exc:  # noqa: BLE001 - soundfile leve des types varies
        log.debug("soundfile a echoue sur %s (%s), repli ffmpeg", path.name, exc)
        return _via_ffmpeg(path)
