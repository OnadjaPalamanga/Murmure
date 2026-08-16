# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — export to subtitles and timed data

- **An Export button on every history entry**, offering SRT, WebVTT, JSON and
  timestamped text. The destination comes from the normal Windows save dialog:
  nothing is written anywhere you did not name.
- Subtitles are **recut from individual word timings**, not from speaker turns —
  a two-minute monologue is one turn, and would have made a two-minute subtitle.
  A cue closes on a speaker change, on a silence past 0.7 s, at 6 seconds or at
  two lines of 42 characters. Cues under 0.4 s are stretched, but never past the
  start of the next one: two subtitles on screen at once is a visible defect.
- The JSON carries `words`, `turns` and `cues` together, because they answer
  different questions: cutting on an exact word, knowing who speaks, and
  reproducing the segmentation of the SRT exported beside it.
- **`timestamps_files`, on by default.** Word timings cannot be added after the
  fact — obtaining them for an old entry means running the whole file through
  again, which is what you discover an hour too late. The cost is nil on
  Parakeet and Canary, whose decoder already dates its tokens, and about 10 % on
  Whisper. Dictation still never dates anything: it has nothing to export.
- The speaker is written **onto each word** as it is persisted, while the
  attribution is fresh. The maximum-overlap rule stays in `align.py` alone; an
  export that replayed it would have been a second copy, free to drift.
- Graceful degradation all the way down: no words but speaker turns exports one
  subtitle per turn; neither exports JSON and text; SRT and WebVTT grey out with
  the reason on screen instead of producing an empty file.

Word timings are **excluded from history listings**. An hour of audio is ten
thousand words, and two hundred entries would have crossed the WebSocket in full
to display one button — the list carries a `has_words` flag, and the words are
read only at export.

`SETTINGS_REVISION` moves 4 → 5: one setting and one command (`export_entry`)
appear.

### Added — the interface speaks English and French

- **Interface language, English by default**, French on request, switchable in
  Settings ▸ Interface with no restart. The overlay and the tray menu follow the
  same choice: both windows share one local-storage key, and the menu, which
  lives in Rust and cannot read the catalogue, is renamed through a command.
- The service no longer emits finished sentences. Progress and errors travel as
  **`(key, parameters)` pairs**, and the interface renders them — which is what
  lets the language change while a one-hour file is being transcribed. The old
  `message` field is still sent and used as a fallback, so a service newer than
  the interface still has something to display.
- Model descriptions exist in both languages, side by side in the catalogue
  rather than in a separate translation file: they are technical claims about
  behaviour, and seeing them together is what keeps them from drifting apart.
- Dates in the history are localised too. `en-GB` rather than `en-US`, so both
  languages order a date the same way and "08/09" never needs reading twice.

The language you *speak* is untouched by all this: it stays under Transcription,
on automatic detection, and the two settings never meet.

### Added — diarization settings

- A full **Speaker identification** group in Settings: the toggle, the number of
  people, the clustering threshold — which had no control at all until now — and
  the models themselves, which can be **downloaded on demand and removed again**.
  Discovering a 35 MB download in the middle of a one-hour file is a surprise
  worth being able to avoid.
- The threshold greys out when the number of people is set, because sherpa-onnx
  ignores it then. An active control with no effect is worse than a greyed one.
- The toggle and the speaker count also stay in the Files tab, above the drop
  zone. They are bound to the same setting through `data-setting` rather than
  through element ids, and mirror each other immediately.
- Downloads are serialised behind a lock. Two concurrent requests — the settings
  button and an imported file asking for the same models — wrote into the same
  `.part` file and could leave a truncated model that `models_present()` would
  have reported as complete.

### Fixed

- **Text rebuilt from timed words lost French elision**: `l 'application`,
  `peut -être`, `j 'ai`. Both engines return bare words and threw away whether a
  space preceded each one, so rejoining inserted a space the sentence never had.
  This already affected **every diarized transcript** — the defect was invisible
  to the tests because the displayed text and the stored turns were rebuilt the
  same wrong way, so they agreed with each other. The engines now record the
  spacing and one shared `join_words` applies it, rather than a punctuation
  heuristic that would guess right in French and wrong elsewhere.
- A range setting whose stored value fell outside the slider's bounds displayed
  the stored value next to a slider pinned at its limit — announcing a setting
  that does not exist. The settings file is meant to be hand-edited, so the
  case is reachable; the readout is taken back from the control.
- `install.ps1` compared sources against the binary's timestamp. A file edited
  while the build was running came out older than the binary that build
  produced, so the next run reported "already up to date" over a binary that
  ignored the edit. The reference is now the instant the last build *started*,
  and the stamp recording it is written only after the build succeeds.
- `models/diarization` was picked up as a transcription model. Its voice
  embedding is a `.onnx` file at the root of a folder under `models/` — exactly
  the signature used to detect a hand-dropped onnx-asr model — so it appeared in
  the catalogue as something selectable, and selecting it broke dictation. Only
  `hub` was excluded; the exclusion list is now explicit.

`SETTINGS_REVISION` moves 3 → 4: two commands appear (`diarize_download`,
`diarize_clear`), and an interface that relies on them gets nothing but *unknown
command* from a service that predates them.

### Added — speaker identification

- **Diarization for imported files.** Transcripts of meetings and interviews
  come back as a dialogue, one turn per speaker, instead of a single block of
  text. Off by default; models (~35 MB) download on first use. Files only —
  grouping voices requires having heard the whole conversation, so it cannot
  work sentence-by-sentence in live dictation.
- Word-level timestamps in both engines, behind a `timestamps=` flag so
  dictation never pays for an alignment it does not use. faster-whisper provides
  them natively; onnx-asr dates sub-word *tokens*, which `words_from_tokens`
  regroups into words using the leading-space convention, offsetting each chunk
  of a split recording back onto the full timeline.
- `align.py`: maps words onto speaker turns by **maximum overlap** rather than
  by start instant — on consecutive turns the start still belongs to the person
  finishing their sentence, while the word belongs to the next one.
- **Schema migration for the history database**, which had no migration
  mechanism at all. Existing histories gain the new column in place, and rows
  written before this version read back as "not diarized" rather than failing.
- `scripts/diarize_check.py`, checking the whole path through `Service`: turns
  ordered and non-overlapping, displayed text exactly equal to the concatenation
  of turns, and a full JSON round-trip through SQLite.
- 42 further tests covering alignment, token grouping, history migration and the
  JSON round-trip — none of which need a model or a GPU.

Diarization never costs a transcription: a missing dependency, an interrupted
download, an unreadable model or an engine that cannot date its words all fall
back to the plain continuous transcript.

`SETTINGS_REVISION` moved 2 → 3 in both `server.py` and `lib.rs`.

### Added

- Test suite covering the pure logic of the dictation path: phrase
  segmentation, seam merging, polish acceptance bounds, silence-based chunking
  and settings persistence. The streaming tests substitute a deterministic
  voice-activity gate for Silero, so they run without a model and enforce the
  invariant the design depends on — **no audio sample is ever sent to the
  engine twice**.
- `ruff` and `pytest` configuration, plus a `dev` extra that installs them.
- Prerequisite checks in `install.ps1`: missing `uv`, Node or Rust is reported
  up front with an install link, instead of surfacing as a "command not found"
  several minutes into a build.
- `.gitattributes` pinning line endings, so a clone is byte-identical across
  platforms and `.ps1` files keep the CRLF that Windows tooling expects.

### Fixed

- A fresh install started on Parakeet while the catalogue and the documentation
  both advertised `whisper-large-v3-turbo` as the default. The default is now
  read from the catalogue's `is_default` flag rather than duplicated as a
  hard-coded string.
- A model id that no longer exists — a renamed catalogue entry, or a local
  folder the user deleted — raised `KeyError` on every transcription with no
  way out from the interface. It now falls back to the recommended model and
  repairs the stored configuration.
- `/shutdown` created its `asyncio` task without keeping a reference. The event
  loop only holds tasks weakly, so it could be collected before raising
  `SIGTERM` — leaving the caller waiting for a port that never frees, and the
  user running the stale service the handshake was meant to replace.
- `soxr` was imported by `media.py` and three scripts but never declared as a
  dependency; it resolved only because another package happened to pull it in.
  A different resolution would have broken file import with no warning.
- `.gitignore` listed `!models/.gitkeep` under an excluded `models/` directory.
  Git does not descend into excluded directories, so the negation never applied
  and a fresh clone was missing both `models/` and `bin/`.

### Security

- Removed the `shell` plugin. It was compiled in, registered at startup and
  granted `shell:allow-open` — the capability to open arbitrary paths and URLs
  — while no part of the frontend ever called it.
- Removed the `clipboard-manager:allow-read-text` permission. Murmure only ever
  writes to the clipboard; nothing reads it.
- `install.ps1` now treats `Cargo.toml` and `capabilities/` as sources when
  deciding whether the binary is stale. Without them, removing a plugin or a
  permission left the installed executable untouched — still carrying the
  capability you believed you had dropped.

### Changed

- `Engine.warmup()` is now part of the protocol. `Service.ensure_engine` calls
  it on every engine it loads, so an engine without it failed at load time
  rather than skipping an optimisation.
- Removed the Android, iOS, macOS and Microsoft Store icon sets. Murmure is a
  Windows desktop application and bundles no installer; `npx tauri icon
  icons/source.png` regenerates the full set in one command if that changes.
- `ruff` is configured with `RUF100` disabled on purpose. That rule deletes
  `# noqa` directives it considers redundant, and takes the justification
  written beside them with it — the project uses those lines to record why a
  broad `except` is deliberate.

## [0.1.0]

First working version.

### Added

- Push-to-talk dictation with a global hotkey, a resident model, a 90-second
  microphone keep-alive and a 400 ms pre-roll buffer so the first word spoken
  before the key is pressed is already captured.
- Continuous dictation: text arrives sentence by sentence while you speak, in
  three stages — a grey preview every 500 ms, committed sentences at phrase
  boundaries, and a re-decoded polish window at every real pause. Only polished
  text is typed at the cursor, so nothing ever has to be taken back.
- Eight models across four speed/quality tiers, including a CPU-only tier for
  machines without a graphics card. Per-model settings are wired in rather than
  exposed as knobs.
- File transcription for any audio or video format, via `soundfile` with an
  `ffmpeg` fallback.
- History in SQLite with FTS5 full-text search, optional audio retention, and
  an in-app player.
- Settings persisted as TOML, writing only the values that differ from the
  defaults so the file stays readable and hand-editable.
- A settings-revision handshake between application and service, so a stale
  backend holding port 8756 is replaced instead of silently driving the UI.
- Single-instance enforcement, registered before every other plugin, so a
  second launch cannot steal the global hotkey from the running instance.

[Unreleased]: https://github.com/OnadjaPalamanga/Murmure/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/OnadjaPalamanga/Murmure/releases/tag/v0.1.0
