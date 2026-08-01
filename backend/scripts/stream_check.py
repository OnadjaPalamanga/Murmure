"""Verifie la dictee continue en rejouant un wav comme s'il sortait du micro.

    python scripts/stream_check.py <fichier.wav> [id_modele]

Le fichier est pousse dans le streamer par blocs de 20 ms, exactement comme le
fait le Recorder. Chaque phrase s'affiche avec le moment ou elle est tombee,
ce qui permet de juger deux choses d'un coup d'oeil : le decoupage (les phrases
sont-elles des phrases ?) et la latence percue (combien de temps apres avoir
parle ?).

`--realtime` fait defiler a la vitesse reelle. Sans lui le wav est pousse a
fond, ce qui verifie le decoupage mais pas le ressenti.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import soxr  # noqa: E402

from murmure.engines.base import SAMPLE_RATE  # noqa: E402
from murmure.models import build_engine, get_spec  # noqa: E402
from murmure.paths import ensure_dirs  # noqa: E402
from murmure.streaming import PhraseStreamer  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

BLOCK = SAMPLE_RATE * 20 // 1000  # 20 ms, comme audio.BLOCK_MS


def load_audio(path: Path) -> np.ndarray:
    audio, sr = sf.read(str(path), dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    if sr != SAMPLE_RATE:
        audio = soxr.resample(audio, sr, SAMPLE_RATE)
    return np.ascontiguousarray(audio, dtype=np.float32)


def main() -> int:
    ensure_dirs()

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    realtime = "--realtime" in sys.argv
    if not args:
        print(__doc__)
        return 1

    wav = Path(args[0])
    model_id = args[1] if len(args) > 1 else "whisper-large-v3-turbo"

    audio = load_audio(wav)
    duration = len(audio) / SAMPLE_RATE

    engine = build_engine(get_spec(model_id))
    print(f"Chargement de {model_id}…")
    engine.load()
    engine.warmup()

    print(f"\n{'=' * 72}")
    print(f"{wav.name} — {duration:.1f} s — {model_id} sur {engine.device}")
    print(f"{'=' * 72}\n")

    started = time.perf_counter()
    stats = {"phrases": 0, "audio_s": 0.0, "engine_ms": 0}

    def transcribe(chunk: np.ndarray) -> str:
        result = engine.transcribe(chunk, language=None)
        stats["audio_s"] += result.audio_seconds
        stats["engine_ms"] += result.latency_ms
        return result.text.strip()

    def on_phrase(text: str, index: int) -> None:
        stats["phrases"] = index
        print(f"[{time.perf_counter() - started:6.2f}s] {text}")

    streamer = PhraseStreamer(
        transcribe=transcribe,
        on_phrase=on_phrase,
        on_error=lambda exc: print(f"!! {exc}"),
    )
    streamer.start()

    for offset in range(0, len(audio), BLOCK):
        streamer.feed(audio[offset : offset + BLOCK])
        if realtime:
            time.sleep(BLOCK / SAMPLE_RATE)

    phrases = streamer.finish()
    elapsed = time.perf_counter() - started

    print(f"\n{'-' * 72}")
    print(f"  phrases          : {len(phrases)}")
    print(f"  audio decoupe    : {stats['audio_s']:.1f} s sur {duration:.1f} s")
    print(f"  temps moteur     : {stats['engine_ms']} ms cumules")
    print(f"  duree du test    : {elapsed:.1f} s{' (temps reel)' if realtime else ''}")
    if stats["engine_ms"]:
        print(f"  facteur t. reel  : {stats['audio_s'] / (stats['engine_ms'] / 1000):.0f}x")
    print(f"\n  texte complet :\n  {' '.join(phrases)}\n")

    engine.unload()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
