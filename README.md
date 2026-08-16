# Murmure

**Local dictation, instantly. Hold a key, speak, the text is there.**

Everything runs on your machine. Your audio and your transcriptions never leave
the computer — there is no server, no account, and no upload.

[![CI](https://github.com/OnadjaPalamanga/Murmure/actions/workflows/ci.yml/badge.svg)](https://github.com/OnadjaPalamanga/Murmure/actions/workflows/ci.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
![Platform: Windows](https://img.shields.io/badge/platform-Windows-lightgrey)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue)

> **A note on language.** The interface is available in **English (default) and
> French**, switchable in Settings ▸ Interface without restarting. The source
> comments and the PowerShell scripts are in French — this README is in English
> so the engineering is legible to a wider audience. What you *speak* is a
> separate setting: the models handle 25 to 99 languages depending on which one
> you pick.

---

## Why it feels instant

The latency is not the model's doing — it is the architecture around it.

| | What it buys |
| --- | --- |
| **The model stays resident in VRAM** | No reload between dictations |
| **The microphone stays open for 90 s** | Opening a WASAPI device costs 50–150 ms; back-to-back dictations pay it once |
| **A 400 ms pre-roll buffer runs continuously** | The start of a word spoken *just before* you press the key is already captured |
| **Long audio is split on silences** | Conformer attention is quadratic — ten 45 s passes cost far less than one 450 s pass |

Measured on an RTX 2080, 474 s of French audio:

| Model | Time | Real-time factor |
| --- | --- | --- |
| Parakeet v3 | 9.3 s | 51× |
| Whisper large-v3-turbo | 10.4 s | 46× |
| Whisper FR distil dec16 | 20.3 s | 23× |

On a 15-second dictation, with a warm model: **111 ms**.

---

## Requirements

- **Windows 10 or 11.** Cursor injection uses `SendInput`, and the audio path
  targets WASAPI. Nothing else is supported today.
- [**uv**](https://docs.astral.sh/uv/), **Node 18+**, **Rust**, and the **MSVC
  Build Tools** (already present if Visual Studio is installed).
- **An NVIDIA GPU is optional.** Without one, use the *no graphics card* tier —
  see [Models](#models).
- Roughly **2.5 GB** for the Python environment (the CUDA runtime dominates),
  plus **0.6–3 GB** per model you download.

## Install

```powershell
.\install.ps1
```

The script checks your toolchain up front, creates the Python environment if it
is missing, compiles the application, and registers it in the Start menu and at
session startup. **No administrator privileges are required** — everything
happens inside your user profile.

```powershell
.\install.ps1 -Uninstall   # removes the shortcuts, leaves your data untouched
.\run.ps1                  # development mode, with frontend hot reload
```

**There is only one thing to launch.** The application starts the Python service
itself if needed, and stops it when you quit from the tray menu.

The project folder *is* the installation: shortcuts point at it, nothing is
copied elsewhere. That is deliberate — the Python environment weighs 2.5 GB and
the models up to 14 GB. An installer duplicating them would create a second
Murmure with its own version, and nothing would stop you launching the wrong one.

The first launch downloads the default model (~600 MB) into `models/hub`, with
progress shown — no frozen screen without explanation.

## Use

Hold **`Ctrl+Space`** — speak — release. The text is copied to the clipboard and
shown for review. **`Esc`** cancels.

> `Ctrl+Space` is also the autocomplete binding in most IDEs, and a global
> shortcut takes it system-wide. Change it in Settings if that bothers you; a
> shortcut already claimed by another application is reported rather than
> failing silently.

Three other ways to trigger it: the **Dictate** button in the main window, the
**Dictate** entry in the tray menu, or the **Files** tab to transcribe existing
audio.

Closing the main window does not quit — the shortcut stays live.

### Interface language

Settings ▸ *Interface* ▸ **Language**: English or French, applied immediately —
no restart, and the overlay follows along, as does the tray menu. The choice is
stored locally alongside the theme, not in the service configuration: it is a
property of the screen you are looking at, not of the transcription.

Two things this setting does **not** touch:

- **the language you speak**, which lives under *Transcription* and stays on
  automatic detection;
- **your existing history**, which is stored as text and is never re-rendered.

---

## Continuous dictation

Settings ▸ *Dictation mode* ▸ **Continuous**. Text now lands sentence by
sentence as you speak, instead of arriving in one block at the end. With **Type
at the cursor**, it is typed into the foreground application, like Windows
dictation.

Three stages work in parallel, fastest to most accurate:

| | When | Where it goes |
| --- | --- | --- |
| **Preview** | every 500 ms, without waiting for the sentence to end | shown greyed out, nothing else |
| **Sentence** | at each phrase boundary | shown in plain text |
| **Polished window** | when you make a real pause | shown, **and typed at the cursor** |

Splitting happens on **phrase boundaries**, never on a clock. Every chunk sent
to the engine is a complete breath group bounded by silence, so the model works
under the same conditions as in batch mode.

The cost is real: **no more review pass**. In batch mode the text is displayed
and editable before you use it; in continuous mode it is already in your
document. That is why batch remains the default.

### Polishing: why this is not another model

A split sentence is a sentence **amputated of its context**. The model therefore
closes it with a period and capitalises the next one — and that, not the words,
is where continuous dictation used to show:

> …je vais essayer de faire une comparaison. **Ou, à certaines époques,
> j'explique. Cette personne vivait.** Qu'est-ce que certaines sociétés
> construisaient ?

When speech drops for good, the last sentences **go back to the engine as one
block**. It then sees the whole sentence and returns what it returns in batch
mode. Same excerpt, same audio, same model:

> …je vais essayer de faire une comparaison **où, à certaines époques,
> j'explique certains endroits, comment est-ce que certaines personnes
> vivaient,** qu'est-ce que certaines sociétés construisaient…

This is **not a language model** running behind. An LLM would rephrase, and no
instruction reliably prevents it. Here the output stays speech recognition: it
cannot say anything other than what was spoken. Re-decoding even recovers words
the isolated sentences had lost — "la condition" becomes "la condition des
noirs" again.

Three safeguards, in [`polish.py`](backend/src/murmure/polish.py):

- **A single-sentence window is not re-decoded.** Its samples are exactly those
  already transcribed: the computation would return the same text at the same
  price. This is the common case for anyone who pauses properly, and it is free.
- **A polish pass that derails is rejected.** Empty output, or a repetition
  loop: if the word count falls outside 0.6×–1.6× of the raw text, the raw text
  stands. Correct text is only ever replaced by plausible text.
- **Seams are de-duplicated.** Consecutive windows share the lead-in and tail
  margins of their sentences; the overlap is stripped.

The "real pause" threshold is **1.6 s**, and it is measured. Across three
minutes of real French dictation, pauses between sentences fall almost entirely
between 0.7 and 1.6 s: the threshold cuts just above the bulk of them and leaves
2–4 sentences per window. Raising it to 2.5 s would group 5–6 — but a
five-sentence window exceeds the 20 s ceiling, and then it is the ceiling that
decides, at an arbitrary point instead of a real stop. **The two values go
together**; changing one without the other achieves nothing.

Over 240 s of continuous dictation: 40 sentences, 22 windows of which 13 were
re-decoded, all between 7.5 and 19.1 s. The GPU overhead is around 3 s.

> **Text is only typed at the cursor once polished.** This is deliberate:
> revising already-typed text would mean sending backspaces into the document,
> and if you clicked elsewhere in the meantime they would eat what you had just
> written. The overlay is the live view; the document only receives final text.

### The preview

A sentence only lands once finished — up to several seconds of apparent
silence. The preview therefore decodes the sentence **in progress** every 500 ms
without closing it, and displays it greyed out. It is never typed at the cursor
nor recorded: it is a witness, not text.

It is the only stage allowed to do nothing. If it cannot take the engine
immediately, **it skips its turn**: a provisional display must never delay a
sentence. Its latency does not count toward the reported figure either, since it
produces no delivered words.

Without a graphics card it stays inactive whatever the setting: on the slowest
tier, eight seconds of audio costs several seconds of compute. It would take
from the dictation exactly the time it claims to save.

Two rules held by the code rather than by documentation:

- **A hesitation is not an end of sentence.** Below `MIN_COMMIT_S` of
  accumulated speech, plain silence validates nothing — a real stop is required.
  Without this guard, "c'est une vision du contenu / extrêmement riche" left in
  two pieces and the isolated fragment came back as "Extrême Maris".
- **One language detection per dictation**, pinned on the first substantial
  sentence and only if confidence exceeds 0.85. Detecting sentence by sentence
  derails the small models: on French dictation, `small` returned "Уплиток,
  мюрмюр" and then Romanian. This is not forcing the language — it is restoring
  the granularity of batch mode.

### Without a graphics card

Continuous mode is comfortable there speed-wise — `whisper-small-cpu` holds 16×
real time on 4 threads. It is **language detection** that gives out, not the
compute:

| Model | Detection on 1st sentence | Result |
| --- | --- | --- |
| Whisper small | `fr` at 0.98 | correct French |
| Whisper base | `ro` at 0.73 | Romanian, then Russian |

On `base` and `tiny`, **force the spoken language in Settings** when using
continuous dictation.

---

## Models

Eight models, arranged in the interface as four notches on a speed/quality
slider. Optimal settings are wired in per model.

| Tier | Model | For what |
| --- | --- | --- |
| **No graphics card** | Whisper tiny / base / small (CPU) | Machines without a GPU |
| **Fast** | Parakeet v3 | Pure French or pure English, immediate response |
| **Balanced** | Whisper large-v3-turbo *(default)* | Everyday dictation |
| **Balanced** | Whisper FR distil dec16 | Non-metropolitan accents |
| **Quality** | Whisper large-v3 | What actually matters |
| **Quality** | Canary 1B v2 | Most accurate, slowest |

**`whisper-large-v3-turbo` is the default** because it is both faster and more
accurate than Parakeet on French mixed with English. Parakeet keeps its place in
the *Fast* tier for its *transducer* architecture, which can emit empty output
instead of forcing a token: it does not invent text over silence, unlike Whisper.

### Language and anglicisms

The language setting is **Automatic** by default, and that is deliberate:
forcing "French" pushes the decoder to phonetically transcribe English words
that were actually spoken ("meeting" → "mitine"). The prompt given to Whisper
deliberately contains anglicisms written in English, precisely so it leaves them
as they are.

But "automatic detection" does not mean "faithful", and the distortion runs both
ways. On French dictation containing a genuine "Wow", measured sentence by
sentence:

| What was said | "c'est quand même super rapide" | "Wow" |
| --- | --- | --- |
| Parakeet v3 | "**it's quite** super rapide" | Wow |
| Whisper large-v3-turbo | ✓ | "Waouh" |
| Whisper FR distil | ✓ | "Waouh" |
| Whisper large-v3 | ✓ | ✓ |
| Canary 1B v2 | ✓ | ✓ |

Parakeet does not preserve anglicisms: its detection switches **word by word**
and replaces French words with near-homophone English ones ("et" → "and",
"riche" → "rich"). The two high-end models are the only ones doing both things
at once: keeping the English actually spoken **and** not anglicising the French.

### Adding your own model

Drop a folder into `models/`:

- a `model.bin` → detected as CTranslate2 (Whisper)
- `.onnx` files → detected as onnx-asr (Parakeet, Canary…)

An optional `murmure.json` alongside lets you name the model and override its
options:

```json
{
  "label": "My fine-tuned model",
  "languages": "français",
  "options": { "beam_size": 2, "initial_prompt": "Notes techniques ponctuées." }
}
```

### Transcribing existing files

**Files** tab: drag and drop, or browse. Audio and video, any format — for a
video, only the sound track is extracted. Common formats go through `soundfile`;
the rest through `ffmpeg`.

> `ffmpeg` is **not bundled** in this repository (it weighs ~80 MB and carries
> its own licence). Put an `ffmpeg.exe` in `bin/`, or install it in your `PATH`.
> Without it, common audio formats still work; video files do not.

An imported file comes back with **each word timed**, which is what makes the
transcript exportable as subtitles — see [Export](#export-subtitles-and-timed-data).
For a batch of files, the same work is one command: see
[Command line](#command-line).

---

## Speaker identification

Tick **Identify speakers** — in Settings ▸ *Speaker identification*, or right
above the drop zone in the *Files* tab — and the transcript comes back as a
dialogue instead of one undifferentiated block:

```
Speaker 1: Là je suis en train de tester la reconnaissance.
Speaker 2: Et ça donne quoi sur une réunion à quatre ?
Speaker 1: Chaque prise de parole est séparée.
```

**Files only** — never live dictation. Grouping voices means deciding that the
person speaking now is the one who spoke two minutes ago, and you cannot make
that call before hearing them a second time. That is not a limitation to lift
later; it is what diarization *is*.

It costs nothing until you use it: the two models (~35 MB) are downloaded on
first use, and the toggle is off by default. If you would rather not discover a
download in the middle of a one-hour file, Settings ▸ *Speaker identification*
▸ **Speaker models** fetches them on demand — and removes them again to
reclaim the space.

### How it works

Two models run in sequence, and neither is a language model:

1. a **segmentation** model (pyannote 3.0) cuts the audio into speech turns;
2. a **voice-embedding** model reduces each turn to a vector, and vectors that
   sit close together are grouped — one group, one speaker.

The transcript is then aligned onto those turns **by maximum overlap**: a word
belongs to whoever it shares the most time with, not to whoever was speaking
when it started. On overlapping turns the start still belongs to the person
finishing their sentence, while the word belongs to the next one.

> **Why not `pyannote.audio`**, which is the reference implementation? It
> requires PyTorch — 2.5 GB, doubling the whole installation — plus gated models
> needing a Hugging Face token. Murmure was deliberately built without torch
> (that is the whole reason for onnx-asr). [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)
> runs the *same* pyannote segmentation model, exported to ONNX, on the
> inference runtime that is already there.

### Tell it how many people there are

The **Number of people** setting is the one that matters. Measured on a
four-speaker reference recording, varying only the clustering threshold:

| Embedding model | 0.5 | 0.6 | 0.7 | 0.8 | 0.9 |
| --- | --- | --- | --- | --- | --- |
| WeSpeaker CAM++ *(used here)* | 5 | **4 ✓** | 3 | 2 | 2 |
| 3D-Speaker (zh-cn) | 7 | 7 | 5 | 5 | **4 ✓** |

Automatic detection lands correctly at the default threshold on that recording,
but the right threshold depends on the embedding model *and* on the audio. When
you know how many people are in the room, saying so removes the guesswork —
and the **Grouping sensitivity** slider, which is that threshold, greys out,
because sherpa-onnx ignores it once the count is fixed.

CAM++ is the default because it is trained on VoxCeleb — multilingual interview
audio, which generalises to French — and because it is twice as fast as the
alternatives (13× real time against 6×, CPU only).

### What to expect

Diarization is best-effort, and honest about it:

- It runs on **CPU at 6–13× real time**, so a one-hour recording costs a few
  minutes on top of transcription.
- Speaker turns are found accurately: on a constructed two-voice file with known
  boundaries at 12.6 s and 18.7 s, the detected changes landed at 12.4 s and
  19.3 s, and the first voice was correctly given **the same label** when it came
  back.
- Auto-detection sometimes **splits one person into two** when their voice
  changes register. Giving the speaker count fixes it.
- **It never costs you a transcription.** Every failure path — missing
  dependency, interrupted download, unreadable model, an engine that cannot
  date its words — falls back to the plain continuous transcript, which has
  already been computed. Losing an hour of transcription because speaker
  labelling failed would be out of all proportion.

---

## Export: subtitles and timed data

Every history entry has an **Export** button. Four formats:

| Format | What it is good for |
|---|---|
| **SRT** | Subtitles. Every video editor and player reads it. |
| **WebVTT** | Subtitles for the web. |
| **JSON** | Every word timed, for scripted editing. |
| **Text** | Timestamped, readable without a tool. |

You pick where the file goes through the normal Windows save dialog. Nothing is
written anywhere you did not name.

### Word-level timing

Subtitles are not cut at speaker turns — a two-minute monologue would make a
two-minute subtitle. They are recut from **individual word timings**, closing a
cue when the speaker changes, when a silence runs past 0.7 s, at 6 seconds, or
at two lines of 42 characters, whichever comes first. Cues shorter than 0.4 s
are stretched, but never past the start of the next one: two subtitles on screen
at once is a visible defect.

The JSON carries three granularities at once, because they answer different
questions — `words` to cut on an exact word (drop a filler, tighten a pause),
`turns` to know who is speaking, `cues` to reproduce the exact segmentation of
the SRT exported beside it.

### It has to be recorded during transcription

Word timings cannot be added afterwards. Getting them for an old entry means
running the whole file through again — which is what you discover an hour too
late. So **Settings ▸ Timestamps and export ▸ Time each word** is on by default.
It costs nothing on Parakeet and Canary, whose decoder already dates its tokens,
and around 10 % on Whisper, which has to align after the fact.

Dictation never records timings: there is nothing to export from a sentence
typed straight at your cursor.

An entry recorded without them still exports to **JSON and Text**; SRT and
WebVTT are greyed out, with the reason on screen rather than an empty file. An
entry that was diarized but not word-timed falls back to one subtitle per
speaker turn.

---

## Command line

Everything the *Files* tab does is also a command, so thirty rushes get
subtitled without clicking thirty times:

```powershell
.\murmure.ps1 transcribe .\rushes\*.mp4 --format srt,json --output .\subtitles
```

`murmure.ps1` is a one-line relay to `backend\.venv\Scripts\murmure.exe`, which
is the real program — call that directly, or `python -m murmure`, if you prefer.

| Command | What it does |
| --- | --- |
| `murmure transcribe FILE…` | audio or video → subtitles and timed data |
| `murmure models` | what `--model` accepts, and what is already downloaded |
| `murmure serve` | the local service — what `murmure` with no command does |

Inputs can be files, wildcards, or directories (`--recursive`). **The wildcards
are expanded by Murmure, not by the shell**: PowerShell hands `*.mp4` to a
native program untouched, and expanding it here is what stops the command
looking for a file literally named `*.mp4`.

Three properties make it safe to drop into a pipeline:

- **stdout carries the result and nothing else.** Progress, warnings and errors
  go to stderr, so `-f json --stdout | jq` needs no filtering. Both streams are
  UTF-8 whatever the console's code page is set to.
- **Nothing is overwritten unless asked** (`--overwrite`, or `--skip-existing`
  to resume a batch). The check runs *before* the model is loaded: discovering
  the conflict after twenty minutes of transcription is an expensive way to
  learn it.
- **The history and `config.toml` are never written to.** The settings are
  *read* — model, spoken language, GPU, speaker identification — and each one
  can be overridden per call. One rule: what the application says, unless you
  say otherwise.

### Driving it from a script, or from a model

`murmure transcribe --help` is written to be enough on its own for a reader who
has never seen the project — an LLM calling the tool included. It carries what
each format is for, **the exact shape of the `--json` report**, the exit codes
and complete examples rather than fragments.

```powershell
.\murmure.ps1 transcribe .\interview.mp4 -f srt,json --json --quiet
```

```json
{
  "ok": true, "model": "whisper-large-v3-turbo", "device": "cuda",
  "files": [{
    "input": "…\\interview.mp4", "ok": true,
    "audio_seconds": 743.2, "latency_ms": 21300, "realtime_factor": 34.9,
    "language": "fr", "words": 2431, "cues": 184, "speakers": 3,
    "text": "…", "outputs": {"srt": "…\\interview.srt"}
  }]
}
```

| Exit | Meaning |
| --- | --- |
| `0` | every file transcribed, every requested output written |
| `1` | at least one file or output failed — the report says which |
| `2` | the command line could not be understood |

A file that fails does not stop the batch: it comes back with `"ok": false` and
an `error`, and the others are still processed. **Speaker identification that
fails never costs the transcript** — the file is written without labels and the
report carries `speakers_error`, exactly as the application degrades.

### Speakers, and the shape of the cues

```powershell
.\murmure.ps1 transcribe .\reunion.wav --speakers 4 -f srt --speaker-label "Voix"
```

`--speakers N` implies `--diarize`, because typing the count and not getting
speakers would be a surprise. The three rules subtitles are cut on are exposed
too, since a subtitling brief often imposes its own:

| | Default | |
| --- | --- | --- |
| `--max-chars` | 84 | longest cue, wrapped over two lines of 42 |
| `--max-seconds` | 6 | longest cue |
| `--max-gap` | 0.7 | silence that closes a cue even with room left |

`srt` and `vtt` **need** word timings. If the engine produced none — a local
model dropped into `models/` that cannot date its output — those two are
reported as failures rather than written as a file no player would accept.
`json` and `txt` still come out.

---

## Architecture

```
backend/                 Python service (the model lives here)
  src/murmure/
    audio.py             microphone capture, ring buffer, pre-roll
    engines/             interchangeable engines (Parakeet ONNX, faster-whisper)
    chunking.py          silence-based splitting
    streaming.py         continuous dictation: VAD segmentation, preview, windows
    polish.py            re-decode safeguards: seams, when to refuse
    diarize.py           speaker diarization (sherpa-onnx, files only)
    align.py             maps words onto speaker turns by maximum overlap
    exports.py           recuts timed words into subtitles (SRT, WebVTT, JSON)
    media.py             reads any audio/video file (soundfile, ffmpeg)
    download.py          download progress by watching the cache grow
    service.py           orchestration: mic → engine → history
    server.py            WebSocket transport (no business logic)
    history.py           SQLite + FTS5 full-text search
    cli.py               the command line: batch transcription, scriptable
  tests/                 pure-logic tests, no model required
frontend/                Tauri 2 application
  src/                   overlay + main window (HTML/CSS/JS)
    i18n.js              English/French catalogue, and the service message keys
  src-tauri/src/lib.rs   global shortcut, tray icon, Python service
  src-tauri/src/inject.rs cursor typing (SendInput Unicode)
```

The frontend talks to the backend only over WebSocket on `127.0.0.1:8756`. The
two restart independently.

### One binary

`target/release/murmure.exe` is the **only launchable executable**, and it is
what `install.ps1` points its shortcuts at. Two things are disabled to keep that
true:

- **No installer is produced.** `bundle.active` is `false` in
  `tauri.conf.json`. A `Murmure_x.y.z_x64-setup.exe` sitting next to it would
  install a second copy with its own version, and nothing would stop you
  double-clicking the wrong one.
- **`target/debug/` is not a place to launch from.** `run.ps1` compiles there
  for development, but that binary goes stale as soon as release is rebuilt, and
  it is just as double-clickable.

`install.ps1` recompiles as soon as a source file is **newer than the last
build**, not merely when the binary is absent: a stale binary launches just as
well as a fresh one, and nothing in the interface reveals its age.

The comparison is against the moment the last build *started*, recorded in a
stamp file, and not against the binary's own timestamp. A file edited while a
two-minute build is running ends up older than the binary that build produces —
comparing to the binary would declare it compiled when it is not, and the next
run would report "already up to date" over a binary that ignores the edit.

### One instance

Double-clicking the executable a second time does not start a second Murmure:
the `single-instance` plugin is registered **before all others**, and the second
instance dies before registering anything. It first asks the running one to show
its window — a second launch means "show me Murmure", not "start another".

Registration order is not a style detail. The real risk is not two windows, it
is **two applications fighting over the same global shortcut**: the second takes
it from the first, which goes silent without reporting anything.

### One service at a time

Port 8756 goes to whoever takes it first, and a service started by `run.ps1`
outlives the console that launched it. Without a guard, the application attaches
to whatever is there: it then drives a backend that ignores half its settings,
and nothing says so — the menus simply come up empty.

Hence `SETTINGS_REVISION`, declared **twice**:

| File | Constant |
| --- | --- |
| `backend/src/murmure/server.py` | `SETTINGS_REVISION` |
| `frontend/src-tauri/src/lib.rs` | `SETTINGS_REVISION` |

At startup the application queries `/health`. Same number: it attaches.
Different number: it stops the service via `/shutdown` and starts its own.

> **Raise both together** whenever a setting is added, removed, or changes
> meaning — and likewise when a WebSocket command appears, since an interface
> that relies on one gets nothing but *unknown command* from a service that
> predates it. Raising only the backend's makes every service unacceptable to
> the installed application; raising only the frontend's kills a perfectly
> valid service on every startup.

### Settings and data

`%APPDATA%\Murmure\config.toml` — only values differing from the defaults are
written, so the file stays readable and hand-editable. History lives in
`history.db` next to it, and optional audio recordings in `audio\`.

The interface language and the theme are **not** in there. They live in the
web view's local storage, shared by both windows, because they describe the
screen rather than the transcription — and because the service, which emits
progress messages as `(key, parameters)` pairs rather than finished sentences,
has no need to know which language you read.

---

## Development

```powershell
.\run.ps1        # service + application, frontend hot reload
```

### Tests

```powershell
cd backend
uv pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
```

The test suite covers the pure logic of the dictation path — segmentation,
seams, polish acceptance bounds, chunking, settings persistence, subtitle
recutting, and which files a command will read and where its outputs land —
and needs **no model and no GPU**: the voice-activity gate is substituted with
a deterministic one. It enforces the invariants the design rests on, notably
that **no audio sample is ever sent to the engine twice**, which is what stops
a word being written into your document in duplicate, and that two subtitle
cues never overlap.

### End-to-end checks

These need a real model, and some need a microphone:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\smoke.py <file.wav> [model_id ...]
.\.venv\Scripts\python.exe scripts\ws_check.py 5          # real microphone dictation
.\.venv\Scripts\python.exe scripts\file_check.py <file>   # file import
.\.venv\Scripts\python.exe scripts\stream_check.py <file.wav> [model_id]
.\.venv\Scripts\python.exe scripts\stream_service_check.py <file.wav> [model_id]

# Diarisation, via le Service entier.
.\.venv\Scripts\python.exe scripts\diarize_check.py <file> [model_id] [--speakers N]

# Export : transcription datee, ecriture en base, relecture, sous-titres.
.\.venv\Scripts\python.exe scripts\export_check.py <file> [model_id] [--diarize]
```

`diarize_check` goes through `Service.transcribe_files` and checks that speaker
turns are ordered and non-overlapping, that the displayed text is exactly the
concatenation of those turns, and that the entry **re-read from SQLite** carries
the same turns — a full JSON round-trip.

`stream_check` shows the segmentation and when each sentence lands (`--realtime`
to judge the feel). `stream_service_check` goes through the whole Service and
verifies the contract the frontend sees: a series of `commit`, `revise` events
that **cover every sentence with no gap and no overlap**, exactly **one**
`final`, and a single history entry whose text is exactly the concatenation of
what was broadcast. A gap would mean text displayed and then never replaced; an
overlap, text written twice into the document. It works in a temporary folder —
your real history is not touched.

---

## Security and privacy

- **Audio and transcriptions never leave the machine.** Recognition is entirely
  local; there is no telemetry and no analytics.
- **The only network access is to Hugging Face**, to download a model and — when
  a model is loaded — to check the cached revision is current. Set
  `HF_HUB_OFFLINE=1` to suppress even that and work strictly from cache.
- Transcriptions are stored **unencrypted** in `%APPDATA%\Murmure\history.db`.
  Audio is only kept if you enable *Keep the audio*.
- Speaker identification runs **locally like everything else**. Voice embeddings
  are computed in memory to group turns within a single file and are never
  stored, never compared across files, and never leave the machine: Murmure can
  tell two people apart inside one recording, it cannot recognise who they are.
- Cursor typing is refused by applications running as administrator — a Windows
  protection, not a bug.

To report a vulnerability, see [SECURITY.md](SECURITY.md). Please do not open a
public issue.

### Why binding to localhost is not enough

The service listens on `127.0.0.1:8756`, which puts it out of reach of the
network. It does **not** put it out of reach of a browser: the same-origin policy
does not apply to WebSocket connections, so a page on any site you visit can open
a socket to `localhost` and talk to whatever answers. Binding locally is a
deployment choice, not a security boundary — and since Murmure starts with your
Windows session, that port is open all day.

Two checks stand in the way instead:

- **A session token.** The service draws a fresh one each time it starts and
  writes it to `%APPDATA%\Murmure\session.token`. Every WebSocket connection and
  every HTTP route except `/health` requires it. A web page cannot read files, so
  it cannot obtain the token. The desktop application reads it through Rust; a
  script you run yourself reads it directly, because it runs as you.
- **An origin check.** Browsers always send an `Origin` header, and the
  application sends `http://tauri.localhost`. Anything else is refused even with
  a valid token.

`/health` stays open deliberately: it is what lets a launching application tell a
current service from a stale one or from an unrelated program, and it returns
nothing but the version and the state. `/shutdown` used to be open for the same
reason — it no longer is, because a page able to stop your dictation at will is a
silent denial of service, with the hotkey simply not responding and nothing to
explain why.

None of this defends against code already running as you: such code can read
`%APPDATA%\Murmure` directly. That is the documented limit of the model.

## Contributing

Issues and pull requests are welcome — see **[CONTRIBUTING.md](CONTRIBUTING.md)**
for the setup, the checks CI runs, and what gets a pull request merged. The short
version:

- run `ruff check .` and `pytest` in `backend/`;
- run `cargo fmt` and `cargo clippy` in `frontend/src-tauri/`;
- if you change a setting's name or meaning, **raise `SETTINGS_REVISION` in both
  files** (see [One service at a time](#one-service-at-a-time)); CI fails if the
  two diverge;
- keep measured claims measured — this project documents what was observed, not
  what was assumed.

Note that **the code is commented in French** while the documentation, interface
and command line are in English. You do not need to write French to contribute;
CONTRIBUTING.md explains how that is handled.

Participation is covered by our [Code of Conduct](CODE_OF_CONDUCT.md).

## Licence

Murmure is free software, licensed under the
**[GNU Affero General Public License v3.0 or later](LICENSE)**.

You may use, study, modify and share it freely. If you distribute a modified
version — **or run one as a network service** — you must release your source
under the same licence. That is the point: Murmure stays public, open and free
for everyone who receives it.

Copyright © 2026 Onadja Palamanga.

### Third-party components

Models and libraries carry their own licences, which are not affected by the
above: [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (MIT),
[onnx-asr](https://github.com/istupakov/onnx-asr) (MIT),
[Tauri](https://tauri.app/) (MIT/Apache-2.0), and the speech models published by
NVIDIA, OpenAI and their respective redistributors. `ffmpeg`, if you supply it,
is distributed under the LGPL or GPL depending on the build.
