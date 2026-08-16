"""Ligne de commande : transcrire des fichiers sans passer par l'application.

Un montage video se prepare par lot — trente rushes a sous-titrer — et cliquer
trente fois n'est pas une facon de traiter trente fichiers. Tout passe par les
memes modules que l'interface (`models`, `align`, `exports`) : un SRT produit
ici est exactement celui qu'aurait produit le bouton Exporter.

Trois regles ont dessine ce qui suit.

**L'aide doit se suffire.** Elle est ecrite pour qui n'a pas ouvert le code —
un modele de langue qui pilote l'outil, par exemple. D'ou les formats decrits,
la forme exacte du rapport JSON, les codes de sortie et des exemples complets
plutot que des fragments. Elle est en anglais, comme le README.

**stdout ne porte que le resultat.** L'avancement et les erreurs partent sur
stderr, ce qui laisse `murmure transcribe … --stdout | jq` fonctionner sans
avoir a filtrer quoi que ce soit.

**Rien n'est ecrit qui n'ait ete demande.** Un fichier existant n'est pas
remplace sans `--overwrite`, et la verification a lieu AVANT de charger le
modele : decouvrir le conflit apres vingt minutes de transcription serait une
facon particulierement couteuse de l'apprendre. La ligne de commande ne touche
ni a l'historique ni a la configuration — elle lit les reglages, elle ecrit les
fichiers demandes, rien d'autre.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .exports import (
    FORMATS,
    MAX_CHARS,
    MAX_GAP,
    MAX_SECONDS,
    TIMED_FORMATS,
    build_cues,
    line_ending,
)
from .exports import render as render_export
from .paths import ensure_dirs

log = logging.getLogger("murmure.cli")

# Ce qu'on tape naturellement pour designer un format. Une commande refusee sur
# « --format text » alors que « txt » existe est une friction gratuite, pour
# l'utilisateur comme pour un appelant automatique.
FORMAT_ALIASES = {
    "srt": "srt",
    "subrip": "srt",
    "vtt": "vtt",
    "webvtt": "vtt",
    "json": "json",
    "txt": "txt",
    "text": "txt",
}

# Les crans du catalogue portent des noms francais ; la ligne de commande parle
# anglais. Le JSON, lui, rend l'identifiant brut : c'est ce qui se compare.
TIER_LABELS = {
    "leger": "cpu-only",
    "rapide": "fast",
    "equilibre": "balanced",
    "qualite": "best",
    "custom": "local",
}

DESCRIPTION = """\
Murmure — local speech recognition with word-level timestamps.

Everything runs on this machine: no account, no upload, and no network call
except downloading a model the first time it is used.

Running `murmure` with no command starts the background service the desktop
application talks to. The other commands are meant to be scripted.\
"""

TRANSCRIBE_DESCRIPTION = """\
Transcribe audio or video files and write timed output next to them.

Every word is timed, which is what subtitles are cut from. Video files are
accepted — only the sound track is read. Long recordings are handled: they are
split on silences internally, so a two-hour file costs no more memory than a
two-minute one.

Defaults come from the desktop application's settings (model, spoken language,
GPU, speaker identification); every one of them can be overridden here. The
history and the settings file are never written to.\
"""

TRANSCRIBE_EPILOG = """\
formats (-f, repeatable or comma-separated):
  srt     SubRip subtitles. Every video editor and every player reads it.
  vtt     WebVTT subtitles, for the web.
  json    Every word timed, plus speaker turns, the subtitle cues and the plain
          transcript. This is the one to parse for scripted editing.
  txt     The transcript with an [HH:MM:SS] stamp per line.

  srt and vtt need word timings. If the engine produced none, those two are
  reported as failures rather than written as a file no player would accept.

json report (--json), a single object on stdout:
  {
    "ok": true,
    "murmure": "0.1.0",
    "model": "whisper-large-v3-turbo",
    "device": "cuda",
    "language": "auto",
    "diarize": false,
    "files": [
      {
        "input": "C:\\\\rushes\\\\interview.mp4",
        "ok": true,
        "audio_seconds": 743.2,
        "latency_ms": 21300,
        "realtime_factor": 34.9,
        "language": "fr",
        "words": 2431,
        "cues": 184,
        "speakers": 3,
        "text": "the transcript in full",
        "outputs": {"srt": "C:\\\\rushes\\\\interview.srt"}
      }
    ]
  }
  A file that could not be transcribed carries "ok": false and "error" instead
  of the counts. Speaker identification that failed leaves "speakers": 0 and
  adds "speakers_error": the transcript itself is unaffected and still written.

exit status:
  0  every file was transcribed and every requested output written
  1  at least one file or one output failed (the report says which)
  2  the command line could not be understood

examples:
  murmure transcribe interview.mp4
  murmure transcribe *.mp4 --format srt,json --output subtitles/
  murmure transcribe ./rushes --recursive -f srt -o ./subs
  murmure transcribe meeting.wav --diarize --speakers 3
  murmure transcribe clip.mov -f json --stdout | jq '.words[0]'
  murmure transcribe talk.mp3 --model whisper-large-v3 --language fr --json
  murmure transcribe teaser.mp4 -f srt --max-seconds 4 --max-chars 60\
"""


# --------------------------------------------------------------- rendu humain


class Reporter:
    """Avancement lisible, sur stderr et jamais sur stdout.

    Volontairement en ASCII : la console Windows rend le texte selon sa page de
    code, et une fleche Unicode dans un journal d'integration continue s'y
    affiche en mojibake. Les fichiers produits, eux, sont en UTF-8 quoi qu'il
    arrive — c'est leur contenu qui compte, pas l'ecran.
    """

    def __init__(self, *, quiet: bool = False) -> None:
        self.quiet = quiet

    def say(self, line: str = "") -> None:
        if not self.quiet:
            print(line, file=sys.stderr, flush=True)

    def warn(self, line: str) -> None:
        """Toujours affiche, meme en --quiet : le resultat est degrade."""
        print(f"warning: {line}", file=sys.stderr, flush=True)

    def fail(self, line: str) -> None:
        print(f"error: {line}", file=sys.stderr, flush=True)


def _duration(seconds: float) -> str:
    return f"{seconds:.1f} s" if seconds < 90 else f"{seconds / 60:.1f} min"


# ------------------------------------------------------- fichiers a traiter


@dataclass(slots=True)
class Job:
    """Un fichier d'entree et les fichiers qu'il doit produire."""

    source: Path
    outputs: dict[str, Path] = field(default_factory=dict)


def _expand(patterns: list[str], *, recursive: bool) -> tuple[list[Path], list[str]]:
    """Developpe jokers et dossiers en une liste de fichiers, sans doublon.

    Le developpement est fait ICI et non par le shell : PowerShell ne developpe
    pas les jokers pour un executable natif, `murmure transcribe *.mp4` y
    arriverait tel quel et echouerait sur un fichier nomme « *.mp4 ».
    """
    from .media import AUDIO_EXT, VIDEO_EXT

    media = {f".{ext}" for ext in (*AUDIO_EXT, *VIDEO_EXT)}
    found: list[Path] = []
    missing: list[str] = []
    seen: set[Path] = set()

    def keep(path: Path) -> None:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            found.append(resolved)

    for raw in patterns:
        path = Path(raw)
        if any(ch in raw for ch in "*?["):
            # `Path.glob` refuse un motif absolu : on repart de la racine du
            # motif, ce qui accepte « *.mp4 » comme « C:\rushes\*.mp4 ».
            base = Path(path.anchor) if path.anchor else Path()
            pattern = str(path.relative_to(path.anchor)) if path.anchor else str(path)
            matches = sorted(p for p in base.glob(pattern) if p.is_file())
            if not matches:
                missing.append(raw)
            for match in matches:
                keep(match)
        elif path.is_dir():
            entries = path.rglob("*") if recursive else path.glob("*")
            matches = sorted(p for p in entries if p.is_file() and p.suffix.lower() in media)
            if not matches:
                missing.append(f"{raw} (no audio or video file in it)")
            for match in matches:
                keep(match)
        elif path.is_file():
            keep(path)
        else:
            missing.append(raw)

    return found, missing


def _destination(source: Path, fmt: str, output: Path | None, *, single: bool) -> Path:
    """Ou ecrire une sortie donnee.

    `--output` designe un dossier, sauf quand il porte l'extension du seul
    format demande pour le seul fichier demande : « -o subs/entretien.srt » est
    ce qu'on ecrit spontanement, et le refuser pour une question de type
    n'apporterait rien a personne.
    """
    if output is None:
        return source.with_suffix(f".{fmt}")
    if single and output.suffix.lower().lstrip(".") == fmt:
        return output
    return output / f"{source.stem}.{fmt}"


def _plan(sources: list[Path], formats: list[str], output: Path | None) -> list[Job]:
    single = len(sources) == 1 and len(formats) == 1
    return [
        Job(
            source=source,
            outputs={fmt: _destination(source, fmt, output, single=single) for fmt in formats},
        )
        for source in sources
    ]


def _resolve_conflicts(jobs: list[Job], args, report: Reporter) -> tuple[list[Job], bool]:
    """Ecarte ou refuse les sorties qui existent deja. Rend (a faire, bloque)."""
    keep: list[Job] = []
    blocked = False
    for job in jobs:
        existing = [p for p in job.outputs.values() if p.exists()]
        if not existing or args.overwrite:
            keep.append(job)
            continue
        if args.skip_existing:
            job.outputs = {f: p for f, p in job.outputs.items() if not p.exists()}
            if job.outputs:
                keep.append(job)
            else:
                report.say(f"skipped (already done): {job.source.name}")
            continue
        for path in existing:
            report.fail(f"{path} already exists — pass --overwrite or --skip-existing")
        blocked = True
    return keep, blocked


# ------------------------------------------------------------- transcription


def _resolve_settings(args, settings) -> dict:
    """Fusionne les reglages de l'application et les options de la commande.

    Une seule regle : ce que dit l'application, sauf si la ligne de commande le
    dit autrement. En avoir deux — des defauts propres a la commande ici, les
    reglages la-bas — obligerait a se demander lequel s'applique a chaque appel.
    """
    language = settings.language if args.language is None else args.language
    language = None if (language or "auto").strip().lower() in ("", "auto") else language.strip()

    diarize = settings.diarize_files if args.diarize is None else args.diarize
    if args.speakers is not None:
        diarize = True

    return {
        "language": language,
        "diarize": bool(diarize),
        "speakers": int(settings.diarize_speakers if args.speakers is None else args.speakers),
        "threshold": float(
            settings.diarize_threshold if args.threshold is None else args.threshold
        ),
        "prefer_gpu": settings.prefer_gpu if args.gpu is None else args.gpu,
    }


def _attribute_speakers(audio, result, *, diarizer, speakers: int, threshold: float, label: str):
    """Attribue le texte aux locuteurs. Rend (texte, tours, nombre, mots dates).

    None quand la diarisation n'a rien trouve. Meme contrat que dans
    l'interface : **elle ne fait jamais perdre une transcription** — l'appelant
    garde le texte continu, deja calcule, plutot que de repartir de zero parce
    que l'attribution des voix a echoue.
    """
    from .align import assign_speakers, count_speakers, dated_words, format_transcript

    diarized = diarizer.diarize(audio, num_speakers=speakers, threshold=threshold)
    if not diarized.segments:
        return None
    blocks = assign_speakers(result.words, diarized.segments)
    if not blocks:
        return None
    return (
        format_transcript(blocks, speaker_name=label),
        [b.to_dict() for b in blocks],
        count_speakers(blocks),
        dated_words(blocks),
    )


def _write(path: Path, fmt: str, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline=line_ending(fmt)) as handle:
        handle.write(content)


def _transcribe_one(job: Job, context: dict, report: Reporter) -> dict:
    """Transcrit un fichier et ecrit ses sorties. Rend sa ligne de rapport.

    Ne leve pas : un fichier illisible au milieu d'un lot de trente ne doit pas
    emporter les vingt-neuf autres. L'echec est une valeur de retour.
    """
    from .config import apply_text_rules
    from .engines.base import SAMPLE_RATE
    from .media import MediaError

    args, settings, engine = context["args"], context["settings"], context["engine"]
    entry: dict = {"input": str(job.source), "ok": False}

    try:
        audio = context["load_audio"](job.source)
    except (MediaError, OSError) as exc:
        entry["error"] = str(exc)
        report.fail(f"{job.source.name}: {exc}")
        return entry

    seconds = len(audio) / SAMPLE_RATE
    report.say(f"      {_duration(seconds)} of audio, transcribing...")

    try:
        result = engine.transcribe(audio, language=context["language"], timestamps=True)
    except Exception as exc:  # noqa: BLE001 - un fichier casse n'arrete pas le lot
        log.exception("Transcription en echec sur %s", job.source)
        entry["error"] = str(exc)
        report.fail(f"{job.source.name}: {exc}")
        return entry

    text = apply_text_rules(result.text, settings)
    if not text:
        entry["error"] = "no speech detected"
        report.fail(f"{job.source.name}: no speech detected")
        return entry

    words = [w.to_dict() for w in result.words]
    turns: list[dict] = []
    speaker_count = 0
    report.say(
        f"      {len(words)} words in {_duration(result.latency_ms / 1000)}"
        f" ({result.realtime_factor:.0f}x real time)"
    )

    if context["diarizer"] is not None:
        if not words:
            report.warn(f"{job.source.name}: this model does not date its words, no speakers")
            entry["speakers_error"] = "the engine produced no word timings"
        else:
            report.say("      identifying speakers...")
            try:
                attributed = _attribute_speakers(
                    audio,
                    result,
                    diarizer=context["diarizer"],
                    speakers=context["speakers"],
                    threshold=context["threshold"],
                    label=args.speaker_label,
                )
            except Exception as exc:  # noqa: BLE001 - jamais au prix du texte
                log.exception("Diarisation en echec sur %s", job.source)
                entry["speakers_error"] = str(exc)
                report.warn(f"{job.source.name}: speaker identification failed ({exc})")
            else:
                if attributed is None:
                    entry["speakers_error"] = "no speaker turn found"
                    report.warn(f"{job.source.name}: no speaker turn found")
                else:
                    text, turns, speaker_count, words = attributed
                    report.say(f"      {speaker_count} speaker(s)")

    cues = build_cues(
        words, max_chars=args.max_chars, max_seconds=args.max_seconds, max_gap=args.max_gap
    )
    meta = {
        "source": str(job.source),
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model_id": engine.model_id,
        "device": engine.device,
        "language": result.language,
        "audio_seconds": round(seconds, 2),
    }

    written: dict[str, str] = {}
    for fmt, destination in job.outputs.items():
        # Un SRT sans repere temporel n'est pas un SRT degrade : c'est un
        # fichier que le lecteur refusera. On le dit plutot que de l'ecrire.
        if fmt in TIMED_FORMATS and not words and not turns:
            entry.setdefault("errors", {})[fmt] = "no word timings from this engine"
            report.fail(f"{job.source.name}: {fmt} needs word timings, none were produced")
            continue

        content = render_export(
            fmt,
            words=words,
            turns=turns,
            text=text,
            meta=meta,
            speaker_name=args.speaker_label,
            max_chars=args.max_chars,
            max_seconds=args.max_seconds,
            max_gap=args.max_gap,
        )
        if args.stdout:
            sys.stdout.write(content)
            written[fmt] = "-"
            continue
        try:
            _write(destination, fmt, content)
        except OSError as exc:
            entry.setdefault("errors", {})[fmt] = str(exc)
            report.fail(f"could not write {destination}: {exc}")
            continue
        written[fmt] = str(destination)
        detail = f" ({len(cues)} cues)" if fmt in TIMED_FORMATS else ""
        report.say(f"      -> {destination}{detail}")

    entry.update(
        {
            "ok": bool(written) and "errors" not in entry,
            "audio_seconds": round(seconds, 2),
            "latency_ms": result.latency_ms,
            "realtime_factor": round(result.realtime_factor, 1),
            "language": result.language,
            "words": len(words),
            "cues": len(cues),
            "speakers": speaker_count,
            "text": text,
            "outputs": written,
        }
    )
    return entry


def _validate(args, formats: list[str], parser: argparse.ArgumentParser) -> None:
    """Refuse les combinaisons contradictoires, avant tout travail.

    Une contradiction se dit : la trancher en silence produirait un resultat
    correct pour la moitie de ce qui a ete demande, et personne ne saurait
    laquelle.
    """
    if args.stdout:
        if args.json:
            parser.error("--stdout and --json both write to stdout; pick one")
        if len(formats) != 1 or len(args.files) != 1:
            parser.error("--stdout takes exactly one input file and one --format")
    if args.speakers is not None and args.diarize is False:
        parser.error("--speakers asks for speaker identification, --no-diarize refuses it")


def _cmd_transcribe(args) -> int:
    from .config import ConfigStore
    from .media import load_audio
    from .models import build_engine, get_spec, list_models, resolve_spec

    parser = args.parser
    report = Reporter(quiet=args.quiet)
    formats = args.format or ["srt"]
    _validate(args, formats, parser)

    sources, missing = _expand(args.files, recursive=args.recursive)
    for name in missing:
        report.fail(f"no such file: {name}")
    if not sources:
        return 1

    ensure_dirs()
    settings = ConfigStore().settings
    resolved = _resolve_settings(args, settings)

    # `get_spec` et non `resolve_spec` quand le modele est demande a la main :
    # l'application se rabat sur le defaut pour rester utilisable, un script
    # doit apprendre qu'il a demande un modele qui n'existe pas.
    try:
        spec = get_spec(args.model) if args.model else resolve_spec(settings.model_id)
    except KeyError:
        parser.error(
            f"unknown model « {args.model} ». Available: "
            + ", ".join(s.id for s in list_models())
        )

    jobs, blocked = _resolve_conflicts(
        _plan(sources, formats, None if args.stdout else args.output), args, report
    )
    if blocked:
        return 1
    if not jobs:
        report.say("nothing to do.")
        return 0

    engine = build_engine(spec, prefer_gpu=resolved["prefer_gpu"])
    report.say(f"loading {spec.id} (downloaded on first use)...")
    try:
        engine.load()
    except Exception as exc:  # noqa: BLE001 - le message vaut mieux qu'une trace
        log.exception("Chargement du modele impossible")
        report.fail(f"could not load {spec.id}: {exc}")
        return 1
    report.say(f"{spec.id} ready on {engine.device}")

    diarizer = None
    if resolved["diarize"]:
        from .diarize import DiarizationUnavailable, Diarizer, ensure_models

        try:
            report.say("fetching the speaker models if needed...")
            ensure_models()
            diarizer = Diarizer()
        except DiarizationUnavailable as exc:
            report.warn(f"speaker identification unavailable ({exc}); transcribing without it")

    context = {
        "args": args,
        "settings": settings,
        "engine": engine,
        "load_audio": load_audio,
        "language": resolved["language"],
        "diarizer": diarizer,
        "speakers": resolved["speakers"],
        "threshold": resolved["threshold"],
    }

    results: list[dict] = []
    started = time.perf_counter()
    for index, job in enumerate(jobs, start=1):
        report.say(f"\n[{index}/{len(jobs)}] {job.source.name}")
        results.append(_transcribe_one(job, context, report))

    elapsed = time.perf_counter() - started
    transcribed = sum(float(r.get("audio_seconds") or 0.0) for r in results)
    ok = all(r["ok"] for r in results)
    report.say(
        f"\n{len(results)} file(s), {_duration(transcribed)} of audio in {_duration(elapsed)}"
    )

    if args.json:
        json.dump(
            {
                "ok": ok,
                "murmure": __version__,
                "model": spec.id,
                "device": engine.device,
                "language": resolved["language"] or "auto",
                "diarize": diarizer is not None,
                "files": results,
            },
            sys.stdout,
            ensure_ascii=False,
            indent=2,
        )
        sys.stdout.write("\n")

    return 0 if ok else 1


# ------------------------------------------------------------------ modeles


def _downloaded(spec) -> bool:
    """Le modele est-il deja dans le cache local ?

    Les deux bibliotheques ne rangent pas au meme endroit : faster-whisper
    telecharge dans `models/hub`, onnx-asr suit `HF_HOME` et atterrit dans
    `models/hub/hub`. On regarde les deux — annoncer absent un modele present
    enverrait un script attendre un telechargement qui n'aura pas lieu.
    """
    from .paths import MODELS_DIR

    if spec.is_local:
        return True
    if not spec.hf_repo:
        return False
    folder = "models--" + spec.hf_repo.replace("/", "--")
    return any((MODELS_DIR / "hub" / extra / folder).is_dir() for extra in ("", "hub"))


def _cmd_models(args) -> int:
    from .config import ConfigStore
    from .models import list_models

    ensure_dirs()
    current = ConfigStore().settings.model_id
    specs = list_models()

    if args.json:
        json.dump(
            [
                {**spec.to_dict(), "downloaded": _downloaded(spec), "current": spec.id == current}
                for spec in specs
            ],
            sys.stdout,
            ensure_ascii=False,
            indent=2,
        )
        sys.stdout.write("\n")
        return 0

    width = max(len(spec.id) for spec in specs)
    print(f"  {'model id':{width}}  {'tier':9}  {'vram':>7}  {'cached':6}  languages")
    for spec in specs:
        print(
            f"{'*' if spec.id == current else ' '} {spec.id:{width}}"
            f"  {TIER_LABELS.get(spec.tier, spec.tier):9}"
            f"  {(f'{spec.vram_mb} MB' if spec.vram_mb else 'cpu'):>7}"
            f"  {'yes' if _downloaded(spec) else 'no':6}"
            f"  {spec.languages_en or spec.languages}"
        )
    print("\n* the model the desktop application is set to use.")
    return 0


# ------------------------------------------------------------------ service


def _ignore_client_disconnect(record: logging.LogRecord) -> bool:
    """False pour les fermetures de connexion ordinaires, qui ne sont pas des erreurs."""
    text = record.getMessage()
    if "_call_connection_lost" in text:
        return False
    return not (record.exc_info and isinstance(record.exc_info[1], ConnectionResetError))


def _cmd_serve(_args=None) -> int:
    from .paths import LOG_FILE

    ensure_dirs()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
    )
    # Ces deux-la sont extremement bavards au niveau INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub").setLevel(logging.WARNING)

    # Un client WebSocket qui se ferme fait remonter une ConnectionResetError
    # depuis le transport Proactor de Windows. C'est normal et sans consequence,
    # mais ca remplit le journal d'ERROR alarmants.
    logging.getLogger("asyncio").addFilter(_ignore_client_disconnect)

    from .server import HOST, PORT, run

    logging.getLogger("murmure").info("Service demarre sur http://%s:%s", HOST, PORT)
    try:
        run()
    except KeyboardInterrupt:
        return 0
    return 0


# --------------------------------------------------------------- arguments


def _format_list(value: str) -> list[str]:
    names = []
    for chunk in value.replace(";", ",").split(","):
        name = chunk.strip().lower().lstrip(".")
        if not name:
            continue
        if name not in FORMAT_ALIASES:
            raise argparse.ArgumentTypeError(
                f"unknown format « {chunk.strip()} » — pick from {', '.join(FORMATS)}"
            )
        names.append(FORMAT_ALIASES[name])
    if not names:
        raise argparse.ArgumentTypeError("empty --format")
    return names


def _above_zero(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="murmure",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"murmure {__version__}")
    subcommands = parser.add_subparsers(dest="command", metavar="COMMAND")

    transcribe = subcommands.add_parser(
        "transcribe",
        help="transcribe audio/video files to subtitles or timed data",
        description=TRANSCRIBE_DESCRIPTION,
        epilog=TRANSCRIBE_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    transcribe.add_argument(
        "files",
        nargs="+",
        metavar="FILE",
        help="audio or video files, wildcards, or directories (see --recursive)",
    )
    transcribe.add_argument(
        "-f",
        "--format",
        type=_format_list,
        action="extend",
        metavar="FMT",
        help="srt, vtt, json, txt — repeatable or comma-separated (default: srt)",
    )
    transcribe.add_argument(
        "-o",
        "--output",
        type=Path,
        metavar="DIR",
        help="where to write: a directory, or a file path when there is one input"
        " and one format (default: beside each input file)",
    )
    transcribe.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="descend into sub-directories when an input is a directory",
    )
    transcribe.add_argument(
        "--stdout",
        action="store_true",
        help="write the result to stdout instead of a file; one file, one format",
    )
    transcribe.add_argument(
        "--overwrite", action="store_true", help="replace existing output files"
    )
    transcribe.add_argument(
        "--skip-existing",
        action="store_true",
        help="leave existing output files alone and move on to the next file",
    )
    transcribe.add_argument(
        "--json",
        action="store_true",
        help="print a machine-readable report on stdout (shape described below)",
    )

    engine = transcribe.add_argument_group("engine")
    engine.add_argument(
        "-m",
        "--model",
        metavar="ID",
        help="model to use; see `murmure models` (default: the application's)",
    )
    engine.add_argument(
        "-l",
        "--language",
        metavar="CODE",
        help="spoken language: auto, fr, en… Automatic keeps borrowed words as"
        " pronounced, forcing a language respells them phonetically"
        " (default: the application's)",
    )
    engine.add_argument(
        "--gpu",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="run on the GPU when one is usable (default: the application's)",
    )

    speakers = transcribe.add_argument_group("speaker identification")
    speakers.add_argument(
        "--diarize",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="split the transcript by who is speaking; downloads ~35 MB of models"
        " on first use (default: the application's)",
    )
    speakers.add_argument(
        "--speakers",
        type=int,
        metavar="N",
        help="how many people speak. Implies --diarize, and is clearly more"
        " reliable than letting it guess (default: 0, guess)",
    )
    speakers.add_argument(
        "--threshold",
        type=float,
        metavar="F",
        help="voice grouping sensitivity when the count is guessed. Lower: one"
        " voice splits in two. Higher: two voices merge (default: 0.6)",
    )
    speakers.add_argument(
        "--speaker-label",
        default="Speaker",
        metavar="NAME",
        help='what each turn is prefixed with, numbered from 1 (default: "Speaker")',
    )

    cutting = transcribe.add_argument_group("subtitle cutting (srt and vtt)")
    cutting.add_argument(
        "--max-chars",
        type=int,
        default=MAX_CHARS,
        metavar="N",
        help=f"longest cue in characters, wrapped over two lines (default: {MAX_CHARS})",
    )
    cutting.add_argument(
        "--max-seconds",
        type=_above_zero,
        default=MAX_SECONDS,
        metavar="S",
        help=f"longest cue in seconds (default: {MAX_SECONDS:g})",
    )
    cutting.add_argument(
        "--max-gap",
        type=_above_zero,
        default=MAX_GAP,
        metavar="S",
        help=f"silence that closes a cue even with room left (default: {MAX_GAP:g})",
    )

    transcribe.add_argument("-q", "--quiet", action="store_true", help="no progress on stderr")
    transcribe.add_argument(
        "-v", "--verbose", action="store_true", help="log what the engine is doing, on stderr"
    )
    transcribe.set_defaults(run=_cmd_transcribe, parser=transcribe)

    models = subcommands.add_parser(
        "models",
        help="list the models usable with --model",
        description="List the models available to `--model`, and whether each one is"
        " already downloaded. Drop a folder into models/ to add your own.",
    )
    models.add_argument("--json", action="store_true", help="print the full catalogue as JSON")
    models.set_defaults(run=_cmd_models)

    serve = subcommands.add_parser(
        "serve",
        help="start the local service the desktop application talks to",
        description="Start the WebSocket service on 127.0.0.1:8756. This is what running"
        " `murmure` with no command does, and what the desktop application starts"
        " by itself — you rarely need to type it.",
    )
    serve.set_defaults(run=_cmd_serve)

    return parser


def run(argv: list[str] | None = None) -> int:
    """Point d'entree unique. Sans argument : le service, comme avant.

    L'application Tauri lance `python -m murmure` sans rien d'autre et
    `install.ps1` pointe dessus : ce cas doit continuer de demarrer le service,
    quoi qu'on ajoute a cote.
    """
    argv = sys.argv[1:] if argv is None else list(argv)

    # Le francais des sous-titres traverse un tube : sans ca, Windows encode la
    # sortie dans la page de code de la console et le premier « é » leve une
    # UnicodeEncodeError au milieu d'un fichier.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(OSError, ValueError):  # flux deja redirige
                reconfigure(encoding="utf-8")

    if not argv:
        return _cmd_serve()

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    # Le service configure sa propre journalisation, fichier compris : la poser
    # ici la lui confisquerait, `basicConfig` ne faisant rien la seconde fois.
    if args.command != "serve":
        logging.basicConfig(
            level=logging.INFO if getattr(args, "verbose", False) else logging.WARNING,
            format="%(levelname)s %(name)s: %(message)s",
            stream=sys.stderr,
        )

    try:
        return args.run(args)
    except KeyboardInterrupt:
        print("\ninterrupted.", file=sys.stderr)
        return 130
