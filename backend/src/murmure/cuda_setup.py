"""Rend les DLL CUDA/cuDNN livrees par pip visibles pour onnxruntime et ctranslate2.

Les paquets `nvidia-*-cu12` deposent leurs DLL dans site-packages/nvidia/<lib>/bin,
qui n'est pas dans le PATH de recherche de Windows. Sans ca, onnxruntime-gpu
retombe silencieusement sur le CPU et ctranslate2 leve une erreur de chargement.

A importer avant tout `import onnxruntime` / `faster_whisper`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_done = False


def ensure_cuda_dlls() -> list[Path]:
    """Ajoute les repertoires de DLL CUDA au chemin de recherche. Idempotent."""
    global _done
    added: list[Path] = []
    if _done or sys.platform != "win32":
        return added

    for site_dir in map(Path, sys.path):
        nvidia_root = site_dir / "nvidia"
        if not nvidia_root.is_dir():
            continue
        for bin_dir in sorted(nvidia_root.glob("*/bin")):
            if not any(bin_dir.glob("*.dll")):
                continue
            os.add_dll_directory(str(bin_dir))
            # ctranslate2 resout ses dependances via le PATH, pas via add_dll_directory.
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            added.append(bin_dir)

    _done = True
    return added
