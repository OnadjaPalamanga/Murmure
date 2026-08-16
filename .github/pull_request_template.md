## What this changes

<!-- And why. If it fixes an issue, link it. -->

## How it was checked

<!-- Measured claims stay measured: numbers, on what hardware, with which
     model. "Feels faster" is fine as long as it says so. -->

## Checklist

- [ ] `ruff check .` and `pytest` pass in `backend/`
- [ ] `cargo fmt --check` and `cargo clippy --all-targets -- -D warnings` pass in `frontend/src-tauri/`
- [ ] A setting changed name or meaning → `SETTINGS_REVISION` bumped in **both** `server.py` and `lib.rs`
- [ ] Behaviour of the local service changed → covered by a test in `backend/tests/`
- [ ] Documentation updated if this changes what the README describes
