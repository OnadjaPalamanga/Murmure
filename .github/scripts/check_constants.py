#!/usr/bin/env python3
"""Verifie les constantes qui vivent dans plusieurs langages a la fois.

Deux valeurs sont dupliquees dans ce depot parce qu'aucun mecanisme ne les
partage entre Python, Rust, JSON et TOML :

  * `SETTINGS_REVISION`, qui dit a l'application si le service en face parle
    encore sa langue. Les deux moities doivent monter ensemble ; le README en
    fait une consigne pour les contributeurs, et c'est exactement le genre de
    consigne qu'une pull request exterieure oubliera. Un ecart ne casse rien
    visiblement : l'application se raccroche a un service qui ignore la moitie
    de ses reglages, et les menus sont juste vides.

  * la version, ecrite dans cinq fichiers. Un ecart la rend fausse dans
    `--version`, dans le rapport JSON de la ligne de commande, ou dans les
    metadonnees du binaire, selon lequel on a oublie.

Aucune dependance : ce script tourne avec le Python nu du runner.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def find(relative: str, pattern: str) -> str | None:
    match = re.search(pattern, read(relative), re.MULTILINE)
    return match.group(1) if match else None


def main() -> int:
    problems: list[str] = []

    # --- SETTINGS_REVISION ------------------------------------------------
    revisions = {
        "backend/src/murmure/server.py": find(
            "backend/src/murmure/server.py", r"^SETTINGS_REVISION\s*=\s*(\d+)"
        ),
        "frontend/src-tauri/src/lib.rs": find(
            "frontend/src-tauri/src/lib.rs",
            r"^const SETTINGS_REVISION:\s*u32\s*=\s*(\d+)",
        ),
    }
    missing = [name for name, value in revisions.items() if value is None]
    if missing:
        problems.append(f"SETTINGS_REVISION introuvable dans : {', '.join(missing)}")
    elif len(set(revisions.values())) != 1:
        detail = ", ".join(f"{name} = {value}" for name, value in revisions.items())
        problems.append(f"SETTINGS_REVISION diverge : {detail}")

    # --- version ----------------------------------------------------------
    versions = {
        "backend/pyproject.toml": find("backend/pyproject.toml", r'^version\s*=\s*"([^"]+)"'),
        "backend/src/murmure/__init__.py": find(
            "backend/src/murmure/__init__.py", r'^__version__\s*=\s*"([^"]+)"'
        ),
        "frontend/package.json": find("frontend/package.json", r'"version":\s*"([^"]+)"'),
        "frontend/src-tauri/Cargo.toml": find(
            "frontend/src-tauri/Cargo.toml", r'^version\s*=\s*"([^"]+)"'
        ),
        "frontend/src-tauri/tauri.conf.json": find(
            "frontend/src-tauri/tauri.conf.json", r'"version":\s*"([^"]+)"'
        ),
    }
    missing = [name for name, value in versions.items() if value is None]
    if missing:
        problems.append(f"version introuvable dans : {', '.join(missing)}")
    elif len(set(versions.values())) != 1:
        detail = ", ".join(f"{name} = {value}" for name, value in versions.items())
        problems.append(f"version diverge : {detail}")

    if problems:
        for line in problems:
            print(f"error: {line}", file=sys.stderr)
        return 1

    print(f"SETTINGS_REVISION = {next(iter(revisions.values()))}")
    print(f"version           = {next(iter(versions.values()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
