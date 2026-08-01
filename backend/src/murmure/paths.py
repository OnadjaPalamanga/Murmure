"""Emplacements sur disque. Les modeles vivent a cote du projet (pas dans %APPDATA%)
pour que deposer un modele telecharge soi-meme reste une simple copie de dossier."""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import PlatformDirs

_dirs = PlatformDirs(appname="Murmure", appauthor=False, roaming=True)

DATA_DIR = Path(_dirs.user_data_dir)
CONFIG_FILE = DATA_DIR / "config.toml"
HISTORY_DB = DATA_DIR / "history.db"
AUDIO_DIR = DATA_DIR / "audio"
LOG_FILE = DATA_DIR / "murmure.log"

# <racine du projet>/models : visible, ouvrable dans l'explorateur, versionnable.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODELS_DIR = Path(os.environ.get("MURMURE_MODELS_DIR", PROJECT_ROOT / "models"))


def ensure_dirs() -> None:
    for d in (DATA_DIR, AUDIO_DIR, MODELS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    # Les deux bibliotheques telechargent dans le meme dossier visible.
    os.environ.setdefault("HF_HOME", str(MODELS_DIR / "hub"))
    os.environ.setdefault("ONNX_ASR_CACHE", str(MODELS_DIR / "hub"))
