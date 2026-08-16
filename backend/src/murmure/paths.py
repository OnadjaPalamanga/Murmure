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
# Jeton de session : ce qui distingue l'application d'une page web quelconque.
# Voir `auth.py`. Dans DATA_DIR parce que ce dossier est deja propre a
# l'utilisateur — c'est la meme protection que celle qui couvre l'historique.
TOKEN_FILE = DATA_DIR / "session.token"


def _find_project_root() -> Path | None:
    """Racine du depot, si le paquet tourne depuis une installation editable.

    `parents[3]` donne la racine parce que le module vit dans
    `backend/src/murmure/`. Ce calcul n'a de sens que pour un
    `uv pip install -e .` : depuis un wheel installe normalement, il designe un
    dossier quelconque au-dessus de `site-packages`, ou l'on irait ecrire
    quatorze gigaoctets de modeles. On verifie donc que le dossier trouve
    ressemble vraiment au depot avant de le croire.
    """
    root = Path(__file__).resolve().parents[3]
    if (root / "backend" / "pyproject.toml").is_file() and (root / "frontend").is_dir():
        return root
    return None


# <racine du projet>/models : visible, ouvrable dans l'explorateur, versionnable.
# Hors installation editable, il n'y a pas de depot ou se ranger : les modeles
# vont alors a cote des donnees, seul emplacement dont on soit sur qu'il est
# accessible en ecriture.
PROJECT_ROOT = _find_project_root()
_DEFAULT_MODELS = (PROJECT_ROOT / "models") if PROJECT_ROOT else (DATA_DIR / "models")
MODELS_DIR = Path(os.environ.get("MURMURE_MODELS_DIR", _DEFAULT_MODELS))


def ensure_dirs() -> None:
    for d in (DATA_DIR, AUDIO_DIR, MODELS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    # Les deux bibliotheques telechargent dans le meme dossier visible.
    os.environ.setdefault("HF_HOME", str(MODELS_DIR / "hub"))
    os.environ.setdefault("ONNX_ASR_CACHE", str(MODELS_DIR / "hub"))
