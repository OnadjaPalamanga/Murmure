"""Validation de bout en bout : charge un modele, transcrit un wav, mesure la latence.

    python scripts/smoke.py <fichier.wav> [id_modele ...]
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
from murmure.models import build_engine, get_spec, list_models  # noqa: E402
from murmure.paths import ensure_dirs  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def load_audio(path: Path) -> np.ndarray:
    audio, sr = sf.read(str(path), dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    if sr != SAMPLE_RATE:
        audio = soxr.resample(audio, sr, SAMPLE_RATE)
    return np.ascontiguousarray(audio, dtype=np.float32)


def main() -> int:
    ensure_dirs()

    if len(sys.argv) < 2:
        print(__doc__)
        print("Modeles disponibles :", ", ".join(s.id for s in list_models()))
        return 1

    wav = Path(sys.argv[1])
    model_ids = sys.argv[2:] or ["parakeet-tdt-0.6b-v3"]

    audio = load_audio(wav)
    duration = len(audio) / SAMPLE_RATE
    print(f"\n{'=' * 70}\nAudio : {wav.name} — {duration:.1f} s\n{'=' * 70}")

    for model_id in model_ids:
        spec = get_spec(model_id)
        engine = build_engine(spec)

        t0 = time.perf_counter()
        engine.load()
        load_s = time.perf_counter() - t0

        engine.warmup()

        # Deux passes : la 1re peut encore payer des allocations paresseuses.
        best = None
        for _ in range(2):
            result = engine.transcribe(audio)
            if best is None or result.latency_ms < best.latency_ms:
                best = result

        print(f"\n--- {spec.label} [{spec.id}] ---")
        print(f"  peripherique     : {best.device}")
        print(f"  chargement       : {load_s:.1f} s (une seule fois, au demarrage)")
        print(f"  transcription    : {best.latency_ms} ms")
        print(f"  facteur temps reel : {best.realtime_factor:.0f}x")
        print(f"  texte : {best.text[:400]}")

        engine.unload()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
