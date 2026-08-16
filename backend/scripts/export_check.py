"""Verifie l'export de bout en bout, sur le chemin de production.

    python scripts/export_check.py <fichier> [id_modele] [--speakers N]

Passe par `Service.transcribe_files` puis `Service.export_entry`, comme le fait
l'interface : transcription datee, ecriture en base, relecture, mise en forme,
ecriture du fichier. Ce que la verification tient :

  * les mots dates **survivent a l'aller-retour SQLite** — c'est ce que
    l'interface relira des jours plus tard, et le seul chemin qui compte ;
  * les sous-titres sont **croissants et sans chevauchement** : deux sous-titres
    affiches ensemble est un defaut visible, et c'est exactement ce que produit
    l'allongement d'un mot bref mal borne ;
  * **aucun mot n'est perdu** entre la transcription et les sous-titres ;
  * aucun repere ne **depasse la duree** de l'enregistrement ;
  * les quatre formats sortent, et le JSON se relit.

L'historique et la configuration sont rediriges vers un dossier temporaire :
lancer cette verification ne salit pas les donnees de l'utilisateur.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from murmure import service as service_mod  # noqa: E402
from murmure.engines.base import join_words  # noqa: E402
from murmure.paths import ensure_dirs  # noqa: E402

logging.basicConfig(
    level=logging.INFO if "--verbose" in sys.argv else logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)

CUE_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})"
)


def parse_srt(text: str) -> list[tuple[float, float, str]]:
    """Relit le SRT produit, comme le ferait un logiciel de montage.

    On ne verifie pas la chaine qu'on vient d'ecrire : on la relit, parce que
    c'est ce que fera le lecteur, et qu'un separateur decimal errant ne se voit
    qu'a la relecture.
    """
    cues = []
    for block in text.split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 3:
            continue
        m = CUE_RE.match(lines[1])
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        cues.append((start, end, " ".join(lines[2:])))
    return cues


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
    diarize = "--speakers" in sys.argv or "--diarize" in sys.argv

    sandbox = Path(tempfile.mkdtemp(prefix="murmure-export-"))
    service_mod.AUDIO_DIR = sandbox / "audio"
    original_history, original_config = service_mod.History, service_mod.ConfigStore
    service_mod.History = lambda: original_history(sandbox / "history.db")
    service_mod.ConfigStore = lambda: original_config(sandbox / "config.toml")
    try:
        svc = service_mod.Service()
    finally:
        service_mod.History, service_mod.ConfigStore = original_history, original_config

    svc.config.update(
        {
            "model_id": model_id,
            "timestamps_files": True,
            "diarize_files": diarize,
            "diarize_speakers": speakers,
        }
    )

    events: list[dict] = []
    started = time.perf_counter()

    def collect(event: dict) -> None:
        events.append(event)
        if event["type"] in ("file_progress", "file_done", "error"):
            print(f"[{time.perf_counter() - started:6.1f}s] {event.get('message', '')}")

    svc.emit = collect  # type: ignore[method-assign]

    print(f"\n{'=' * 72}")
    print(f"{path.name} — {model_id} — diarisation : {'oui' if diarize else 'non'}")
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
    duration = float(entry.get("audio_seconds") or 0.0)
    ok = True

    print(f"\n{'-' * 72}")
    # L'evenement ne doit PAS charrier les mots : deux cents entrees d'une heure
    # traverseraient le WebSocket en entier pour n'afficher qu'un bouton.
    lean = "words" not in entry
    print(f"  evenement sans les mots : {lean}")
    ok = ok and lean

    stored = svc.history.get(entry["id"])
    words = (stored or {}).get("words") or []
    print(f"  mots relus depuis SQLite: {len(words)}")
    if not words:
        print("  RESULTAT : ECHEC (aucun mot date en base)")
        svc.shutdown()
        return 1

    outputs = {}
    for fmt in ("srt", "vtt", "json", "txt"):
        dest = sandbox / f"export.{fmt}"
        result = svc.export_entry(entry["id"], fmt, str(dest))
        if not result["ok"]:
            print(f"  ECHEC export {fmt} : {result.get('message')}")
            ok = False
            continue
        outputs[fmt] = dest.read_text(encoding="utf-8")
        print(f"  {fmt:4s} ecrit : {result['bytes']:>7} o")

    if "srt" in outputs:
        cues = parse_srt(outputs["srt"])
        print(f"\n  sous-titres relus  : {len(cues)}")

        ordered = all(cues[i][0] <= cues[i + 1][0] for i in range(len(cues) - 1))
        disjoint = all(cues[i][1] <= cues[i + 1][0] + 1e-6 for i in range(len(cues) - 1))
        print(f"  ordre croissant    : {ordered}")
        print(f"  sans chevauchement : {disjoint}")
        ok = ok and ordered and disjoint

        # Aucun repere ne depasse l'enregistrement : un sous-titre qui commence
        # apres la fin ne s'affiche jamais.
        if duration:
            inside = all(c[1] <= duration + 0.5 for c in cues)
            print(f"  dans la duree      : {inside}  ({duration:.1f} s)")
            ok = ok and inside

        # Aucun mot perdu entre la transcription et les sous-titres. On compare
        # les TEXTES recolles, pas des listes de mots : « l' » et « application »
        # sont deux mots dates qui n'en forment qu'un une fois ecrits, et
        # comparer les listes reprocherait au sous-titre d'etre correct.
        def flat(s: str) -> str:
            return " ".join(s.split())

        expected = flat(join_words(words))
        written = flat(re.sub(r"Locuteur \d+:", " ", " ".join(c[2] for c in cues)))
        lossless = written == expected
        print(f"  aucun mot perdu    : {lossless}  ({len(words)} mots)")
        if not lossless:
            print(f"      transcription : {expected[:110]}")
            print(f"      sous-titres   : {written[:110]}")
        ok = ok and lossless

    if "json" in outputs:
        payload = json.loads(outputs["json"])
        coherent = len(payload["words"]) == len(words)
        print(f"  JSON relisible     : {coherent}  ({len(payload['cues'])} cues)")
        ok = ok and coherent

    print(f"\n{'-' * 72}")
    for line in outputs.get("txt", "").splitlines()[:12]:
        print(f"  {line}")

    print(f"\n{'-' * 72}")
    print(f"  fichiers dans : {sandbox}")
    print("  RESULTAT :", "ok" if ok else "ECHEC")
    svc.shutdown()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
