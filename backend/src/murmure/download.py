"""Progression de telechargement, par observation du cache.

Pourquoi pas `snapshot_download` avec une barre tqdm : onnx-asr et faster-whisper
ne recuperent qu'un sous-ensemble de leur depot (une seule variante de
quantification sur les cinq publiees). Pre-telecharger le depot entier pour
pouvoir compter les octets ferait rapatrier 3 Go la ou 600 Mo suffisent.

On laisse donc chaque bibliotheque telecharger ce dont elle a besoin, et on
observe la taille du cache pendant ce temps. Le total exact est inconnu, mais le
nombre d'octets deja recus est reel — et une barre indeterminee honnete vaut
mieux qu'un pourcentage invente.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

ProgressFn = Callable[[int], None]
POLL_S = 0.5
# En dessous, c'est du bruit de systeme de fichiers, pas un telechargement.
MIN_DELTA_BYTES = 256 * 1024


def directory_size(path: Path) -> int:
    """Octets sur disque sous `path`. Les erreurs de parcours sont ignorees :
    des fichiers apparaissent et disparaissent pendant un telechargement."""
    total = 0
    try:
        for entry in path.rglob("*"):
            try:
                if entry.is_file():
                    total += entry.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total


class DownloadWatcher:
    """Signale la croissance d'un dossier tant qu'un travail est en cours.

    S'utilise en gestionnaire de contexte autour de l'appel qui telecharge :

        with DownloadWatcher(cache_dir, on_progress=emit):
            engine.load()
    """

    def __init__(self, path: Path | str, *, on_progress: ProgressFn) -> None:
        self.path = Path(path)
        self.on_progress = on_progress
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._baseline = 0

    def __enter__(self) -> DownloadWatcher:
        self._baseline = directory_size(self.path)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> None:  # noqa: ANN002
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        last_reported = 0
        while not self._stop.wait(POLL_S):
            grown = max(0, directory_size(self.path) - self._baseline)
            if grown - last_reported >= MIN_DELTA_BYTES:
                last_reported = grown
                try:
                    self.on_progress(grown)
                except Exception:  # noqa: BLE001 - un abonne casse n'arrete rien
                    log.debug("Rapport de progression en echec", exc_info=True)
