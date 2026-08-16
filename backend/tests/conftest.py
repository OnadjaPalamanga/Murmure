"""Bouchons pour les deux dependances que la suite n'a aucune raison d'installer.

`sounddevice` et `soundfile` sont importes en tete de `audio.py` et `service.py`
parce que le service ouvre reellement un micro et ecrit reellement des wav. Les
tests, eux, ne font ni l'un ni l'autre : ils poussent des tableaux numpy dans la
machine a etats et lisent ce qui en sort.

Les installer quand meme couterait la pile audio native complete a chaque
execution de l'integration continue — pour du code qui n'est jamais appele. On
pose donc deux modules vides, et **uniquement s'ils manquent** : sur une machine
de developpement, ou l'application tourne pour de vrai, ce sont les vrais qui
servent, et la suite ne teste alors pas autre chose que ce qui sera livre.
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path

# --------------------------------------------------------------------------
# Les donnees de la suite ne doivent JAMAIS etre celles de la machine.
#
# `paths.py` resout `%APPDATA%\Murmure` a l'import, et `History.__init__` fige
# `HISTORY_DB` comme valeur par defaut au moment ou le module est charge :
# repeindre la constante depuis un test arrive trop tard. Or importer
# `murmure.server` instancie un `Service()`, donc un `ConfigStore` et un
# `History` — la suite ecrivait dans l'historique reel du developpeur.
#
# On deplace donc la racine AVANT tout import de `murmure`. Ce fichier est lu
# par pytest avant les modules de test, ce qui en fait le seul endroit possible.
# --------------------------------------------------------------------------
_sandbox = Path(tempfile.mkdtemp(prefix="murmure-tests-"))
os.environ["APPDATA"] = str(_sandbox / "appdata")
os.environ["LOCALAPPDATA"] = str(_sandbox / "localappdata")
os.environ["XDG_DATA_HOME"] = str(_sandbox / "xdg")
os.environ["MURMURE_MODELS_DIR"] = str(_sandbox / "models")


def _stub(name: str, **attributes: object) -> None:
    if name in sys.modules:
        return
    try:
        __import__(name)
    except ImportError:
        module = types.ModuleType(name)
        for key, value in attributes.items():
            setattr(module, key, value)
        sys.modules[name] = module


class _PortAudioError(Exception):
    """Meme nom que celui de sounddevice : `audio.py` l'attrape par ce nom."""


def _no_devices() -> list[dict]:
    return []


_stub(
    "sounddevice",
    PortAudioError=_PortAudioError,
    query_devices=_no_devices,
    default=types.SimpleNamespace(device=(None, None)),
    InputStream=object,
)
_stub("soundfile", write=lambda *a, **k: None, read=lambda *a, **k: None)
