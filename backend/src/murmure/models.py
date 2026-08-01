"""Catalogue des modeles + scan des modeles deposes a la main.

Le catalogue est une valeur par defaut, pas une prison : tout dossier depose dans
`models/` apparait dans la liste (c'est precisement ce que SuperWhisper interdit).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .engines.base import Engine
from .engines.parakeet import ParakeetEngine
from .engines.whisper import DICTATION_PROMPT, WhisperEngine
from .paths import MODELS_DIR

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ModelSpec:
    id: str
    label: str
    engine: str  # "parakeet" | "whisper"
    source: str  # nom onnx-asr, repo HF, ou chemin local
    blurb: str
    vram_mb: int
    languages: str
    # Depot HuggingFace reel, pour pouvoir telecharger avec une barre de
    # progression avant de confier le modele a la bibliotheque.
    hf_repo: str = ""
    is_default: bool = False
    is_local: bool = False
    options: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


CATALOG: list[ModelSpec] = [
    ModelSpec(
        id="parakeet-tdt-0.6b-v3",
        label="Parakeet v3",
        engine="parakeet",
        source="nemo-parakeet-tdt-0.6b-v3",
        blurb="Le plus rapide, et le meilleur en francais parmi les rapides. "
        "Architecture transducer : n'invente pas de texte sur les silences.",
        vram_mb=1200,
        languages="25 langues (dont FR), detection automatique",
        hf_repo="istupakov/parakeet-tdt-0.6b-v3-onnx",
        is_default=True,
    ),
    ModelSpec(
        id="whisper-large-v3-turbo",
        label="Whisper large-v3-turbo",
        engine="whisper",
        source="large-v3-turbo",
        blurb="Repli multilingue robuste. A privilegier si tu melanges "
        "francais et anglais dans la meme phrase.",
        vram_mb=1600,
        languages="99 langues",
        hf_repo="mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        options={"compute_type": "int8_float16", "beam_size": 1},
    ),
    ModelSpec(
        id="whisper-fr-distil-dec16",
        label="Whisper FR distil (dec16)",
        engine="whisper",
        source="Kelno/whisper-large-v3-french-distil-dec16-ct2",
        blurb="Whisper large-v3 affine sur du francais, decodeur distille a "
        "16 couches. Meilleur sur les accents non hexagonaux.",
        vram_mb=1800,
        languages="francais",
        hf_repo="Kelno/whisper-large-v3-french-distil-dec16-ct2",
        options={"compute_type": "int8_float16", "beam_size": 1},
    ),
    ModelSpec(
        id="whisper-large-v3",
        label="Whisper large-v3",
        engine="whisper",
        source="large-v3",
        blurb="Qualite maximale, nettement plus lent. Pour les fichiers longs "
        "ou l'audio difficile, pas pour la dictee au vol.",
        vram_mb=3100,
        languages="99 langues",
        hf_repo="Systran/faster-whisper-large-v3",
        options={"compute_type": "int8_float16", "beam_size": 5},
    ),
]


def _scan_local_models() -> list[ModelSpec]:
    """Detecte les modeles deposes dans models/ (hors cache HF).

    Un dossier contenant model.bin => CTranslate2. Des .onnx => onnx-asr.
    Un murmure.json a cote permet de surcharger le libelle et les options.
    """
    found: list[ModelSpec] = []
    if not MODELS_DIR.is_dir():
        return found

    for entry in sorted(MODELS_DIR.iterdir()):
        if not entry.is_dir() or entry.name == "hub":
            continue

        if (entry / "model.bin").exists():
            engine = "whisper"
        elif any(entry.glob("*.onnx")):
            engine = "parakeet"
        else:
            continue

        meta = {}
        meta_file = entry / "murmure.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                log.warning("murmure.json illisible dans %s", entry)

        found.append(
            ModelSpec(
                id=meta.get("id", f"local:{entry.name}"),
                label=meta.get("label", entry.name),
                engine=meta.get("engine", engine),
                source=str(entry),
                blurb=meta.get("blurb", "Modele local depose dans models/."),
                vram_mb=int(meta.get("vram_mb", 0)),
                languages=meta.get("languages", "?"),
                is_local=True,
                options=meta.get("options", {}),
            )
        )
    return found


def list_models() -> list[ModelSpec]:
    return [*CATALOG, *_scan_local_models()]


def get_spec(model_id: str) -> ModelSpec:
    for spec in list_models():
        if spec.id == model_id:
            return spec
    raise KeyError(f"Modele inconnu : {model_id}")


def default_model_id() -> str:
    return next((s.id for s in CATALOG if s.is_default), CATALOG[0].id)


def build_engine(spec: ModelSpec, *, prefer_gpu: bool = True) -> Engine:
    """Instancie le moteur correspondant. Ne charge rien en VRAM (voir Engine.load)."""
    if spec.engine == "parakeet":
        path = Path(spec.source)
        return ParakeetEngine(
            model_id=spec.id,
            onnx_name=str(path) if spec.is_local else spec.source,
            quantization=spec.options.get("quantization"),
            prefer_gpu=prefer_gpu,
        )

    if spec.engine == "whisper":
        opts = spec.options
        return WhisperEngine(
            model_id=spec.id,
            repo=spec.source,
            compute_type=opts.get("compute_type", "int8_float16"),
            language=opts.get("language"),
            beam_size=int(opts.get("beam_size", 1)),
            initial_prompt=opts.get("initial_prompt", DICTATION_PROMPT),
            vad_filter=bool(opts.get("vad_filter", True)),
            prefer_gpu=prefer_gpu,
            download_root=str(MODELS_DIR / "hub"),
        )

    raise ValueError(f"Moteur inconnu : {spec.engine}")
