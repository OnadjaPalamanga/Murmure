# Security policy

## Reporting a vulnerability

Please report security issues privately, **not** as a public issue.

Use GitHub's [private vulnerability
reporting](https://github.com/OnadjaPalamanga/Murmure/security/advisories/new)
on this repository. If that is unavailable to you, open a normal issue titled
"Security contact request" with no technical detail, and a private channel will
be arranged.

Please include what you have: the version or commit, what you observed, and the
steps to reproduce it. A proof of concept helps but is not required — a clear
description of the weakness is enough to start.

You can expect an acknowledgement within a week. Because this is a single-author
project, fixes are best-effort rather than bound to a service level; you will be
told honestly where a report stands. Credit is offered in the changelog unless
you prefer otherwise.

## What is in scope

Murmure runs entirely on one machine. The interesting boundary is not the
network but **the local service on `127.0.0.1:8756`**, because anything running
on the machine — including a web page open in a browser — can reach that port.

In scope:

- Anything that lets code the user did not run reach the local service, read the
  dictation history, drive the microphone, or write files.
- Bypasses of the session-token or origin checks in
  [`backend/src/murmure/auth.py`](backend/src/murmure/auth.py).
- Paths by which an exported file, a downloaded model, or an imported media file
  can escape the location the user chose.
- Anything that causes audio or transcriptions to leave the machine.

Out of scope:

- An attacker who already has code execution as the user. They can read
  `%APPDATA%\Murmure` directly; the session token does not defend against this
  and is not meant to.
- Vulnerabilities in the speech models themselves, or in third-party libraries,
  unless Murmure's use of them makes the impact worse. Report those upstream.
- Missing hardening with no demonstrable impact (a header, a flag, a lint).

## The local service, and what protects it

The service binds to `127.0.0.1`, which puts it out of reach of the network. It
does **not** put it out of reach of a browser: the same-origin policy does not
apply to WebSocket connections, so a page on any site can open a socket to
`localhost`. Binding locally is therefore not, on its own, a security boundary.

Two checks stand in the way instead, both in `auth.py`:

1. **A session token**, drawn fresh each time the service starts and written to
   `%APPDATA%\Murmure\session.token`. Every WebSocket connection and every HTTP
   route except `/health` requires it. A web page cannot read files, so it
   cannot obtain the token.
2. **An origin check**. Browsers always send an `Origin` header; the desktop
   application sends `http://tauri.localhost`. Anything else is refused, even
   with a valid token. A client that sends no origin at all — a script run by
   the user — is allowed, because it runs under the same session and can read
   the token file anyway.

`/health` is deliberately unauthenticated. It is what lets a newly launched
application discover whether the port is held by a current Murmure, a stale one,
or an unrelated program, and it returns nothing but the version and state.

## Privacy

Audio and transcriptions never leave the machine. The only outbound network
access is to Hugging Face, to download a model on first use and to check a
cached revision; `HF_HUB_OFFLINE=1` suppresses even that.

Transcriptions are stored **unencrypted** in `%APPDATA%\Murmure\history.db`.
Anyone with access to the user account can read them. If this matters for your
threat model, use full-disk or per-folder encryption — Murmure does not
implement its own.
