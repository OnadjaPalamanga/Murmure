# Contributing to Murmure

Issues and pull requests are welcome. This file says what you need to know
before you write code, so that nothing about the review comes as a surprise.

## A note on language

**The code is commented in French. The documentation, the interface and the
command line are in English.** That split is deliberate and it is not going to
change: the comments are where design decisions are argued, and they are argued
in the author's language.

You do not need to write French to contribute. If you are more comfortable in
English, write your comments in English and say so in the pull request — they
will be translated rather than rejected. What matters is that a comment explains
*why*, not *what*; see the existing code for the register.

Issues and pull request descriptions: French or English, whichever you prefer.

## Getting set up

Windows only, for now. You need [uv](https://docs.astral.sh/uv/),
[Node](https://nodejs.org/), [Rust](https://rustup.rs/), and the MSVC Build
Tools ("Desktop development with C++" in the Visual Studio Installer).

```powershell
git clone https://github.com/OnadjaPalamanga/Murmure
cd Murmure
.\install.ps1        # builds everything and installs the shortcuts
.\run.ps1            # or: development mode, with frontend reload
```

The first run downloads several gigabytes of Python environment and a model.

## Before opening a pull request

```powershell
cd backend
uv pip install -e ".[dev]"
ruff check .
pytest

cd ..\frontend\src-tauri
cargo fmt
cargo clippy --all-targets -- -D warnings
```

CI runs the same commands on Windows and Ubuntu, plus a dependency audit and a
check that the cross-language constants agree.

### If you change a setting

`SETTINGS_REVISION` must go up **in both files**:

- `backend/src/murmure/server.py`
- `frontend/src-tauri/src/lib.rs`

It is what tells a running application that the service on port 8756 no longer
speaks its language. Without the bump, the application attaches to a stale
service, half the settings silently do nothing, and the menus are simply empty.
`.github/scripts/check_constants.py` fails the build if the two diverge — run it
locally if you want to check before pushing.

### If you change the version

It appears in five files. The same script checks them; run it and it will tell
you which one you missed.

## What gets a pull request merged

- **Measured claims stay measured.** This project documents what was observed,
  not what was assumed. If you write "faster", say how much faster and on what.
  If you cannot measure it, say that instead.
- **Comments explain decisions, not syntax.** The valuable comment is the one
  that stops the next person from "simplifying" something that is the way it is
  for a reason.
- **Degradation never costs the user their transcription.** Speaker
  identification, polishing, previews and exports may all fail; a dictation that
  was successfully transcribed must survive every one of those failures. This is
  the rule the code bends over backwards for, and it is not negotiable.
- **Anything touching the local service needs a test.** `backend/tests/` has no
  GPU, no microphone and no model in it — `tests/test_service.py` shows how to
  substitute an engine, and `tests/conftest.py` keeps the suite off your real
  `%APPDATA%`.

## Security

Do not open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).

## Licence

Murmure is AGPL-3.0-or-later. By contributing, you agree that your contribution
is licensed under the same terms.
