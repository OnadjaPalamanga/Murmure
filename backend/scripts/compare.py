"""Compare plusieurs modeles sur un meme extrait, texte et vitesse cote a cote.

    python scripts/compare.py <fichier> [--seconds 45] [modele ...]

Contrairement a smoke.py qui traite le fichier entier, on se limite a un extrait
pour pouvoir iterer vite quand on arbitre entre deux modeles.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from murmure import cuda_setup  # noqa: F401,E402  (doit preceder onnxruntime)
from murmure.media import load_audio  # noqa: E402
from murmure.models import build_engine, get_spec, list_models  # noqa: E402
from murmure.paths import ensure_dirs  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("models", nargs="*")
    parser.add_argument("--seconds", type=float, default=45.0)
    parser.add_argument("--language", default=None)
    parser.add_argument("--chars", type=int, default=400)
    args = parser.parse_args()

    ensure_dirs()
    audio = load_audio(Path(args.audio))
    if args.seconds > 0:
        audio = audio[: int(16000 * args.seconds)]

    ids = args.models or [s.id for s in list_models() if not s.is_local]
    print(f"\nextrait de {len(audio) / 16000:.0f} s — langue demandee : {args.language or 'auto'}")
    print("=" * 72)

    for model_id in ids:
        spec = get_spec(model_id)
        try:
            engine = build_engine(spec)
            engine.load()
            start = time.perf_counter()
            result = engine.transcribe(audio, language=args.language)
            elapsed = time.perf_counter() - start
        except Exception as exc:  # noqa: BLE001 - on veut voir les autres
            print(f"\n--- {spec.label} : ECHEC {type(exc).__name__}: {exc}")
            continue

        rtf = (len(audio) / 16000) / max(elapsed, 1e-6)
        print(f"\n--- {spec.label} [{spec.tier}] — {elapsed:.1f} s, {rtf:.0f}x temps reel")
        print(f"    {result.text[: args.chars]}")
        del engine

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
