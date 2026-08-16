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


# Les crans du curseur vitesse/qualite presente dans l'interface.
# TIER_CPU n'est pas « le cran le plus lent » mais un axe a part : ces modeles
# visent une machine sans carte graphique, ou tout le reste du catalogue est
# inutilisable.
TIER_CPU = "leger"
TIER_FAST = "rapide"
TIER_BALANCED = "equilibre"
TIER_BEST = "qualite"
TIER_ORDER = [TIER_CPU, TIER_FAST, TIER_BALANCED, TIER_BEST]

# Sous-dossiers de `models/` qui ne sont PAS des modeles de transcription.
# `hub` est le cache HuggingFace ; `diarization` contient l'identification des
# locuteurs, dont l'extracteur d'empreinte vocale est un .onnx pose a la racine
# du dossier — exactement la signature qu'on utilise pour reconnaitre un modele
# onnx-asr depose a la main. Sans cette liste, il apparait dans le catalogue
# comme un modele de transcription qu'on peut selectionner, et le choisir
# casse la dictee.
RESERVED_DIRS = frozenset({"hub", "diarization"})

# Une dictee ne doit pas confisquer la machine. Quatre fils suffisent aux petits
# modeles et laissent l'ordinateur reactif ; au-dela le gain est faible et le
# pic de consommation, lui, ne l'est pas.
CPU_THREADS = 4


@dataclass(slots=True)
class ModelSpec:
    id: str
    label: str
    engine: str  # "parakeet" | "whisper"
    source: str  # nom onnx-asr, repo HF, ou chemin local
    blurb: str
    vram_mb: int
    languages: str
    # L'interface existe en francais et en anglais. Les deux versions vivent
    # cote a cote plutot que dans un fichier de traduction separe : ce sont des
    # descriptions techniques, et les voir ensemble est ce qui evite qu'elles
    # divergent quand un modele change de comportement. Vide = repli sur le
    # francais, ce qui laisse un modele local se decrire dans une seule langue.
    blurb_en: str = ""
    languages_en: str = ""
    tier: str = TIER_BALANCED
    # Depot HuggingFace reel, pour pouvoir telecharger avec une barre de
    # progression avant de confier le modele a la bibliotheque.
    hf_repo: str = ""
    is_default: bool = False
    is_local: bool = False
    options: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


CATALOG: list[ModelSpec] = [
    # --- sans carte graphique -------------------------------------------------
    # compute_type "int8" et non "int8_float16" : ce dernier est un type GPU,
    # sur CPU CTranslate2 le refuse et retombe en silence sur autre chose.
    ModelSpec(
        id="whisper-base-cpu",
        label="Whisper base (CPU)",
        engine="whisper",
        source="base",
        blurb="Le defaut quand il n'y a pas de carte graphique. Etonnamment "
        "juste pour sa taille : garde la structure des phrases et les mots "
        "anglais. Se trompe sur les noms propres et le vocabulaire rare.",
        blurb_en="The default when there is no graphics card. Surprisingly "
        "accurate for its size: it holds sentence structure together and keeps "
        "English words. It gets proper nouns and rare vocabulary wrong.",
        vram_mb=0,
        languages="99 langues, detection automatique",
        languages_en="99 languages, auto-detected",
        hf_repo="Systran/faster-whisper-base",
        tier=TIER_CPU,
        options={"compute_type": "int8", "beam_size": 1, "cpu_threads": CPU_THREADS},
    ),
    ModelSpec(
        id="whisper-small-cpu",
        label="Whisper small (CPU)",
        engine="whisper",
        source="small",
        blurb="Nettement plus precis que base, pour deux fois et demie le temps. "
        "Le bon choix sur CPU des que la dictee compte.",
        blurb_en="Clearly more accurate than base, for two and a half times "
        "the time. The right choice on CPU as soon as the dictation matters.",
        vram_mb=0,
        languages="99 langues, detection automatique",
        languages_en="99 languages, auto-detected",
        hf_repo="Systran/faster-whisper-small",
        tier=TIER_CPU,
        options={"compute_type": "int8", "beam_size": 1, "cpu_threads": CPU_THREADS},
    ),
    ModelSpec(
        id="whisper-tiny-cpu",
        label="Whisper tiny (CPU)",
        engine="whisper",
        source="tiny",
        blurb="Pour une machine vraiment modeste. Qualite juste suffisante pour "
        "des notes courtes, a ne pas utiliser pour du texte qui compte.",
        blurb_en="For a genuinely modest machine. Quality just good enough for "
        "short notes; do not use it for text that matters.",
        vram_mb=0,
        languages="99 langues, detection automatique",
        languages_en="99 languages, auto-detected",
        hf_repo="Systran/faster-whisper-tiny",
        tier=TIER_CPU,
        # Sans amorce : tiny est trop petit pour la suivre, il la recopie puis
        # part en boucle (« la question de la question de la question… »).
        options={
            "compute_type": "int8",
            "beam_size": 1,
            "cpu_threads": CPU_THREADS,
            "initial_prompt": None,
        },
    ),
    # --- avec carte graphique -------------------------------------------------
    ModelSpec(
        id="parakeet-tdt-0.6b-v3",
        label="Parakeet v3",
        engine="parakeet",
        source="nemo-parakeet-tdt-0.6b-v3",
        blurb="Tres rapide, et n'invente rien sur les silences (transducer). "
        "Mais sa detection de langue bascule mot a mot : sur du francais "
        "melange d'anglais, il ecrit « it's quite » pour « c'est quand meme ». "
        "A reserver au francais pur ou a l'anglais pur.",
        blurb_en="Very fast, and invents nothing during silences (transducer). "
        "But its language detection switches word by word: on French mixed "
        "with English it writes « it's quite » for "
        "« c'est quand même ». Keep it for pure French or pure "
        "English.",
        vram_mb=1200,
        languages="25 langues (dont FR), detection automatique",
        languages_en="25 languages (incl. French), auto-detected",
        hf_repo="istupakov/parakeet-tdt-0.6b-v3-onnx",
        tier=TIER_FAST,
    ),
    ModelSpec(
        id="canary-1b-v2",
        label="Canary 1B v2",
        engine="parakeet",  # meme moteur onnx-asr
        source="nemo-canary-1b-v2",
        blurb="Le plus precis du catalogue, et il garde tes anglicismes tels "
        "quels au lieu de les franciser. Le plus lent aussi. "
        "Quantifie en int8 pour tenir dans la VRAM.",
        blurb_en="The most accurate in the catalogue, and it keeps your "
        "English words as you said them instead of respelling them in French. "
        "The slowest too. Quantised to int8 to fit in VRAM.",
        vram_mb=1400,
        languages="25 langues, langue source a preciser (fr par defaut)",
        languages_en="25 languages, source language required (French by default)",
        hf_repo="istupakov/canary-1b-v2-onnx",
        tier=TIER_BEST,
        options={"quantization": "int8", "needs_language": True, "default_language": "fr"},
    ),
    ModelSpec(
        id="whisper-large-v3-turbo",
        label="Whisper large-v3-turbo",
        engine="whisper",
        source="large-v3-turbo",
        blurb="Le defaut : rapide et juste en meme temps. Tient la phrase "
        "francaise la ou Parakeet part en anglais. Seul defaut connu, il "
        "ecrit parfois « Waouh » la ou tu as dit « Wow ».",
        blurb_en="The default: fast and accurate at the same time. Holds the "
        "French sentence together where Parakeet drifts into English. Its one "
        "known flaw is writing « Waouh » where you said « Wow ».",
        vram_mb=1600,
        languages="99 langues, detection automatique",
        languages_en="99 languages, auto-detected",
        hf_repo="mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        tier=TIER_BALANCED,
        is_default=True,
        options={"compute_type": "int8_float16", "beam_size": 1},
    ),
    ModelSpec(
        id="whisper-fr-distil-dec16",
        label="Whisper FR distil (dec16)",
        engine="whisper",
        source="Kelno/whisper-large-v3-french-distil-dec16-ct2",
        blurb="Whisper large-v3 affine sur du francais, decodeur distille a "
        "16 couches. Meilleur sur les accents non hexagonaux.",
        blurb_en="Whisper large-v3 fine-tuned on French, decoder distilled to "
        "16 layers. Better on accents from outside mainland France.",
        vram_mb=1800,
        languages="francais",
        languages_en="French",
        hf_repo="Kelno/whisper-large-v3-french-distil-dec16-ct2",
        tier=TIER_BALANCED,
        options={"compute_type": "int8_float16", "beam_size": 1},
    ),
    ModelSpec(
        id="whisper-large-v3",
        label="Whisper large-v3",
        engine="whisper",
        source="large-v3",
        blurb="La reference Whisper, avec recherche en faisceau a 5. Garde tes "
        "anglicismes tels quels, comme Canary, et reste deux a trois fois "
        "plus rapide que lui. Plus lent que turbo pour un gain marginal.",
        blurb_en="The Whisper reference, with beam search at 5. Keeps your "
        "English words as they are, like Canary, and stays two to three times "
        "faster than it. Slower than turbo for a marginal gain.",
        vram_mb=3100,
        languages="99 langues, detection automatique",
        languages_en="99 languages, auto-detected",
        hf_repo="Systran/faster-whisper-large-v3",
        tier=TIER_BEST,
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
        if not entry.is_dir() or entry.name in RESERVED_DIRS:
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

        # Une description fournie a la main dans une seule langue reste
        # affichee telle quelle : mieux vaut la phrase de l'utilisateur dans sa
        # langue que rien du tout. Seul le texte par defaut, qui est le notre,
        # existe dans les deux.
        described = "blurb" in meta or "blurb_en" in meta
        found.append(
            ModelSpec(
                id=meta.get("id", f"local:{entry.name}"),
                label=meta.get("label", entry.name),
                engine=meta.get("engine", engine),
                source=str(entry),
                blurb=meta.get("blurb", "" if described else "Modele local depose dans models/."),
                blurb_en=meta.get(
                    "blurb_en", "" if described else "Local model dropped in models/."
                ),
                vram_mb=int(meta.get("vram_mb", 0)),
                languages=meta.get("languages", "?"),
                languages_en=meta.get("languages_en", ""),
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


def resolve_spec(model_id: str) -> ModelSpec:
    """Comme `get_spec`, mais retombe sur le defaut au lieu de lever.

    Un identifiant peut disparaitre entre deux versions : entree de catalogue
    renommee, ou dossier depose dans `models/` que l'utilisateur a efface. La
    configuration, elle, garde l'ancien nom. Sans repli, `ensure_engine` leve
    une `KeyError` a CHAQUE dictee et l'application ne s'en sort plus toute
    seule : le modele courant est celui qui n'existe pas, et rien dans
    l'interface ne permet d'en changer puisque plus rien ne repond.

    On prefere donc dicter avec le modele recommande et le dire dans le
    journal. L'appelant remet la configuration d'aplomb.
    """
    try:
        return get_spec(model_id)
    except KeyError:
        fallback = get_spec(default_model_id())
        log.warning(
            "Modele « %s » introuvable (renomme ou supprime) : repli sur « %s »",
            model_id,
            fallback.id,
        )
        return fallback


def build_engine(spec: ModelSpec, *, prefer_gpu: bool = True) -> Engine:
    """Instancie le moteur correspondant. Ne charge rien en VRAM (voir Engine.load)."""
    if spec.engine == "parakeet":
        path = Path(spec.source)
        return ParakeetEngine(
            model_id=spec.id,
            onnx_name=str(path) if spec.is_local else spec.source,
            quantization=spec.options.get("quantization"),
            prefer_gpu=prefer_gpu,
            needs_language=bool(spec.options.get("needs_language")),
            default_language=str(spec.options.get("default_language", "fr")),
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
            cpu_threads=int(opts.get("cpu_threads", 0)),
        )

    raise ValueError(f"Moteur inconnu : {spec.engine}")
