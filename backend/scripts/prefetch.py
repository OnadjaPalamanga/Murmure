"""Telecharge tous les modeles du catalogue a l'avance.

    python scripts/prefetch.py            tout le catalogue
    python scripts/prefetch.py canary-1b-v2-int8 ...   une selection

Chaque modele est reellement instancie apres telechargement : un fichier
present mais tronque serait detecte ici plutot qu'au premier usage.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from murmure import cuda_setup  # noqa: F401,E402  (doit preceder onnxruntime)
from murmure.models import build_engine, list_models  # noqa: E402
from murmure.paths import MODELS_DIR, ensure_dirs  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("prefetch")


def cache_size_mb() -> float:
    hub = MODELS_DIR / "hub"
    if not hub.is_dir():
        return 0.0
    return sum(f.stat().st_size for f in hub.rglob("*") if f.is_file()) / 1024 / 1024


def main(wanted: list[str]) -> int:
    ensure_dirs()
    specs = [s for s in list_models() if not s.is_local]
    if wanted:
        specs = [s for s in specs if s.id in wanted]

    log.info("%d modele(s) a preparer, cache actuel %.0f Mo", len(specs), cache_size_mb())
    failures: list[tuple[str, str]] = []

    for index, spec in enumerate(specs, 1):
        before = cache_size_mb()
        start = time.monotonic()
        log.info("[%d/%d] %s ...", index, len(specs), spec.label)
        try:
            # Le telechargement se produit dans load() ; on decharge aussitot
            # pour ne pas empiler plusieurs modeles en VRAM.
            engine = build_engine(spec, prefer_gpu=False)
            engine.load()
            grown = cache_size_mb() - before
            log.info(
                "[%d/%d] %s pret en %.0f s (+%.0f Mo)",
                index,
                len(specs),
                spec.label,
                time.monotonic() - start,
                grown,
            )
            del engine
        except Exception as exc:  # noqa: BLE001 - on continue les autres
            failures.append((spec.label, f"{type(exc).__name__}: {exc}"))
            log.error("[%d/%d] %s ECHEC : %s", index, len(specs), spec.label, exc)

    log.info("cache final : %.0f Mo", cache_size_mb())
    if failures:
        log.error("%d echec(s) :", len(failures))
        for label, err in failures:
            log.error("  %s -> %s", label, err)
        return 1
    log.info("tous les modeles sont prets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
