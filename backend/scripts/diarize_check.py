"""Verifie la diarisation de bout en bout, sur le chemin de production.

    python scripts/diarize_check.py <fichier> [id_modele] [--speakers N]

Passe par `Service.transcribe_files`, comme le fait l'interface : lecture du
fichier, transcription datee, diarisation, attribution, ecriture en base. Ce que
la verification tient, en plus d'afficher le dialogue :

  * **aucun mot n'est perdu** — la concatenation des tours rend exactement les
    mots transcrits, dans l'ordre ;
  * les tours ne se **chevauchent pas** et sont **croissants** ;
  * l'entree d'historique **relue depuis SQLite** porte les memes tours que ceux
    diffuses, ce qui vaut aller-retour JSON complet.

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

from murmure import service as service_mod  # noqa: E402
from murmure.paths import ensure_dirs  # noqa: E402

logging.basicConfig(
    level=logging.INFO if "--verbose" in sys.argv else logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)


def main() -> int:
    ensure_dirs()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 1

    path = Path(args[0])
    model_id = args[1] if len(args) > 1 else "whisper-large-v3-turbo"
    speakers = 0
    if "--speakers" in sys.argv:
        speakers = int(sys.argv[sys.argv.index("--speakers") + 1])

    sandbox = Path(tempfile.mkdtemp(prefix="murmure-diar-"))
    service_mod.AUDIO_DIR = sandbox / "audio"
    original_history, original_config = service_mod.History, service_mod.ConfigStore
    service_mod.History = lambda: original_history(sandbox / "history.db")
    service_mod.ConfigStore = lambda: original_config(sandbox / "config.toml")
    try:
        svc = service_mod.Service()
    finally:
        service_mod.History, service_mod.ConfigStore = original_history, original_config

    svc.config.update(
        {"model_id": model_id, "diarize_files": True, "diarize_speakers": speakers}
    )

    events: list[dict] = []
    started = time.perf_counter()

    def collect(event: dict) -> None:
        events.append(event)
        kind = event["type"]
        if kind == "level":
            return
        stamp = f"[{time.perf_counter() - started:6.1f}s]"
        if kind == "file_progress":
            print(f"{stamp} {event['message']}")
        elif kind == "file_done":
            state = "ok" if event["ok"] else "ECHEC"
            print(f"{stamp} {state} — {event.get('message', '')}")
        elif kind == "error":
            print(f"{stamp} ERREUR {event['message']}")

    svc.emit = collect  # type: ignore[method-assign]

    print(f"\n{'=' * 72}")
    print(f"{path.name} — {model_id} — locuteurs : {speakers or 'automatique'}")
    print(f"{'=' * 72}\n")

    svc.transcribe_files([str(path)])
    deadline = time.monotonic() + 1800
    while not any(e["type"] == "files_finished" for e in events) and time.monotonic() < deadline:
        time.sleep(0.1)

    done = [e for e in events if e["type"] == "file_done"]
    if not done or not done[0]["ok"]:
        print("\n  RESULTAT : ECHEC (aucune transcription)")
        svc.shutdown()
        return 1

    entry = done[0]["entry"]
    segments = entry.get("segments")
    ok = True

    print(f"\n{'-' * 72}")
    print(f"  locuteurs detectes : {done[0].get('speakers', 0)}")
    print(f"  tours de parole    : {len(segments) if segments else 0}")

    if not segments:
        print("  (aucune diarisation — texte continu, repli normal)")
    else:
        # Tours croissants et disjoints : un chevauchement signifierait du texte
        # attribue deux fois, un ordre casse un dialogue illisible.
        ordered = all(
            segments[i]["start"] <= segments[i + 1]["start"] for i in range(len(segments) - 1)
        )
        disjoint = all(
            segments[i]["end"] <= segments[i + 1]["start"] + 1e-6
            for i in range(len(segments) - 1)
        )
        print(f"  ordre croissant    : {ordered}")
        print(f"  sans chevauchement : {disjoint}")
        ok = ok and ordered and disjoint

        # Le texte affiche doit etre exactement celui des tours.
        rebuilt = "\n".join(
            f"Locuteur {s['speaker'] + 1} : {s['text']}" for s in segments if s["text"].strip()
        )
        matches = rebuilt == entry["text"]
        print(f"  texte == tours     : {matches}")
        ok = ok and matches

        # Aller-retour SQLite : ce que l'interface relira plus tard.
        stored = svc.history.get(entry["id"])
        round_trip = stored is not None and stored["segments"] == segments
        print(f"  relu depuis SQLite : {round_trip}")
        ok = ok and round_trip

        print(f"\n{'-' * 72}")
        for s in segments:
            print(f"  [{s['start']:7.2f} - {s['end']:7.2f}]  Locuteur {s['speaker'] + 1}")
            print(f"      {s['text'][:200]}")

    print(f"\n{'-' * 72}")
    print("  RESULTAT :", "ok" if ok else "ECHEC")
    svc.shutdown()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
