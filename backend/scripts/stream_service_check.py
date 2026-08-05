"""Verifie la dictee continue au niveau du Service, sans micro ni interface.

    python scripts/stream_service_check.py <fichier.wav> [id_modele] [--realtime] [--verbose]

`--realtime` rejoue le wav a sa vraie vitesse. C'est plus lent, mais c'est la
seule facon de voir l'apercu et le polissage se declencher comme au micro : a
pleine vitesse, la segmentation a fini avant que le moteur n'ait rendu la
premiere phrase.

Le wav est pousse dans le Recorder par le meme chemin que le micro reel
(`on_block`), et tous les evenements diffuses sont affiches dans l'ordre. C'est
ce que verra le frontend : `state`, `speech`, `commit`, `revise`, puis un
`final` unique.

Ce que la verification tient, en plus du nombre d'evenements :

  * les fenetres de polissage RECOUVRENT toutes les phrases, sans trou ni
    chevauchement — `from_index` reprend exactement ou la precedente s'arrete.
    Un trou signifierait du texte affiche puis jamais remplace ; un
    chevauchement, du texte ecrit deux fois au curseur.
  * le texte de l'entree d'historique est exactement la concatenation de ce qui
    a ete diffuse. C'est le contrat sur lequel le frontend s'appuie pour ne pas
    reinjecter la dictee une seconde fois.

L'historique et la configuration sont rediriges vers un dossier temporaire :
lancer cette verification ne salit pas les donnees de l'utilisateur.
"""

from __future__ import annotations

import logging
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import soxr  # noqa: E402

from murmure import service as service_mod  # noqa: E402
from murmure.engines.base import SAMPLE_RATE  # noqa: E402
from murmure.paths import ensure_dirs  # noqa: E402

logging.basicConfig(
    level=logging.INFO if "--verbose" in sys.argv else logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)

BLOCK = SAMPLE_RATE * 20 // 1000


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
        return 1

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    realtime = "--realtime" in sys.argv
    wav = Path(args[0])
    model_id = args[1] if len(args) > 1 else "whisper-large-v3-turbo"
    audio = load_audio(wav)

    # Isolation des donnees. Les valeurs par defaut de `History()` et
    # `ConfigStore()` sont figees a la definition des fonctions : reaffecter
    # `paths.HISTORY_DB` ne changerait rien, il faut passer le chemin.
    sandbox = Path(tempfile.mkdtemp(prefix="murmure-check-"))
    service_mod.AUDIO_DIR = sandbox / "audio"

    # `service.py` importe les deux classes par leur nom : c'est la, et pas dans
    # leurs modules d'origine, qu'il faut les remplacer.
    original_history, original_config = service_mod.History, service_mod.ConfigStore
    service_mod.History = lambda: original_history(sandbox / "history.db")
    service_mod.ConfigStore = lambda: original_config(sandbox / "config.toml")
    try:
        svc = service_mod.Service()
    finally:
        service_mod.History, service_mod.ConfigStore = original_history, original_config

    assert svc.history.db_path == sandbox / "history.db", "isolation de la base ratee"
    assert svc.config.path == sandbox / "config.toml", "isolation de la config ratee"
    svc.config.update({"model_id": model_id, "dictation_mode": "continu"})

    events: list[dict] = []
    started = time.perf_counter()

    def collect(event: dict) -> None:
        events.append(event)
        kind = event["type"]
        if kind == "level":
            return
        stamp = f"[{time.perf_counter() - started:6.2f}s]"
        if kind == "commit":
            print(f"{stamp} commit  #{event['index']}  {event['text']}")
        elif kind == "revise":
            span = f"{event['from_index']}-{event['to_index']}"
            print(f"{stamp} revise  #{span:7} {event['text']}")
        elif kind == "preview":
            print(f"{stamp} apercu  {event['text']}")
        elif kind == "state":
            print(f"{stamp} state   {event['state']}")
        elif kind == "speech":
            print(f"{stamp} speech  {'debut' if event['speaking'] else 'fin'}")
        elif kind == "final":
            print(f"{stamp} final   streamed={event.get('streamed')}")
        elif kind in ("error", "empty"):
            print(f"{stamp} {kind}   {event.get('message') or event.get('reason')}")

    svc.emit = collect  # type: ignore[method-assign]

    # Le micro est remplace, tout le reste du chemin est celui de production.
    captured: list[np.ndarray] = []
    svc.recorder.start = lambda: None  # type: ignore[method-assign]
    svc.recorder.stop = lambda: (  # type: ignore[method-assign]
        np.concatenate(captured) if captured else np.zeros(0, dtype=np.float32)
    )

    print(f"\n{'=' * 72}\n{wav.name} — {len(audio) / SAMPLE_RATE:.1f} s — {model_id}\n{'=' * 72}\n")

    svc.start()
    assert svc.state == "streaming", svc.state

    # L'horloge du rejeu suit le debut de la lecture, pas le bloc precedent :
    # cumuler des `sleep` derive, et la derive se voit sur une dictee d'une minute.
    playback = time.monotonic()
    for offset in range(0, len(audio), BLOCK):
        block = audio[offset : offset + BLOCK]
        captured.append(block)
        svc.recorder.on_block(block)
        if realtime:
            due = playback + (offset + len(block)) / SAMPLE_RATE
            time.sleep(max(0.0, due - time.monotonic()))

    svc.stop_and_transcribe()

    deadline = time.monotonic() + 180
    while svc.state != "idle" and time.monotonic() < deadline:
        time.sleep(0.05)

    finals = [e for e in events if e["type"] == "final"]
    commits = [e for e in events if e["type"] == "commit"]
    revises = [e for e in events if e["type"] == "revise"]
    previews = [e for e in events if e["type"] == "preview"]
    polishing = svc.config.settings.polish_mode != "aucun"

    print(f"\n{'-' * 72}")
    print(f"  commits         : {len(commits)}")
    print(f"  fenetres polies : {len(revises)}")
    print(f"  apercus         : {len(previews)}")
    print(f"  finals          : {len(finals)} (doit valoir 1)")

    ok = len(finals) == 1 and svc.state == "idle"

    if polishing:
        # Chaque fenetre reprend a la phrase suivant celle ou s'arretait la
        # precedente, et la derniere va jusqu'au bout : ni trou, ni doublon.
        expected = 1
        covered = True
        for event in revises:
            covered = covered and event["from_index"] == expected
            expected = event["to_index"] + 1
        covered = covered and expected == len(commits) + 1
        print(f"  couverture      : {covered} (les fenetres recouvrent 1-{len(commits)})")
        ok = ok and covered
        pieces = [e["text"] for e in revises]
    else:
        pieces = [c["text"] for c in commits]

    if finals:
        entry = finals[0]["entry"]
        joined = " ".join(p for p in pieces if p).strip()
        stored = len(svc.history.search("", limit=10))
        print(f"  entrees en base : {stored} (doit valoir 1)")
        print(f"  texte == diffuse: {entry['text'] == joined}")
        print(f"\n  {entry['text']}\n")
        ok = ok and entry["text"] == joined and stored == 1
    else:
        ok = False

    svc.shutdown()
    print("  RESULTAT        :", "ok" if ok else "ECHEC")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
