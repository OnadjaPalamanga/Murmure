"""Diarisation : qui parle, et quand.

Deux modeles travaillent a la suite, et c'est sherpa-onnx qui les enchaine :

  1. une **segmentation** (pyannote 3.0, exporte en ONNX) decoupe l'audio en
     tours de parole et repere les chevauchements ;
  2. un **extracteur d'empreinte vocale** resume chaque tour en un vecteur, et
     les vecteurs proches sont regroupes : un groupe = un locuteur.

Pourquoi sherpa-onnx et pas pyannote.audio, qui est la reference : pyannote
exige PyTorch — 2,5 Go, soit le double de l'installation entiere — et des
modeles a acces restreint qui demandent un jeton HuggingFace et l'acceptation
de conditions. Murmure a justement ete construit sans torch (c'est la raison
d'onnx-asr). sherpa-onnx fait tourner le MEME modele de segmentation, converti
en ONNX, sur le moteur d'inference deja present.

Reserve a l'import de fichiers. En dictee continue, regrouper les locuteurs
demanderait d'avoir entendu toute la conversation — on ne peut pas nommer le
deuxieme locuteur avant de l'avoir entendu une premiere fois.

Ce que la mesure dit, sur 57 s a quatre locuteurs, processeur seul :

  * 6 a 13x le temps reel — largement suffisant pour du fichier ;
  * **nombre de locuteurs connu : exact** ;
  * **detection automatique : approximative**, et le bon seuil depend du modele
    d'empreinte autant que de l'enregistrement. D'ou le reglage : quand
    l'utilisateur sait combien de personnes parlent, le lui demander vaut mieux
    que deviner.
"""

from __future__ import annotations

import logging
import shutil
import tarfile
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .align import SpeakerSegment
from .engines.base import SAMPLE_RATE
from .paths import MODELS_DIR

log = logging.getLogger(__name__)

DIARIZATION_DIR = MODELS_DIR / "diarization"

# Segmentation : pyannote/segmentation-3.0 converti en ONNX par le projet
# sherpa-onnx. Modele sous licence MIT (CNRS), 6 Mo.
SEGMENTATION_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
)
SEGMENTATION_MODEL = DIARIZATION_DIR / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"

# Empreinte vocale : CAM++ entraine sur VoxCeleb. Choisi pour le francais —
# VoxCeleb est un corpus d'interviews multilingue, la ou les modeles 3D-Speaker
# du meme catalogue sont entraines sur du mandarin et generalisent moins bien.
# Il est aussi deux fois plus rapide (mesure : 13x contre 6x le temps reel).
EMBEDDING_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/wespeaker_en_voxceleb_CAM%2B%2B.onnx"
)
EMBEDDING_MODEL = DIARIZATION_DIR / "wespeaker_en_voxceleb_CAMPP.onnx"

# Environ 35 Mo au total, telecharges a la premiere utilisation seulement.
TOTAL_DOWNLOAD_MB = 35

# Seuil de regroupement quand le nombre de locuteurs n'est pas donne. 0,6 est
# la valeur qui retrouve le bon compte sur l'enregistrement de reference avec
# CAM++ ; plus bas on fractionne un locuteur en plusieurs, plus haut on en
# fusionne deux.
DEFAULT_THRESHOLD = 0.6

# En deca, il n'y a pas de quoi caracteriser une voix : la segmentation rend des
# bribes et le regroupement invente des locuteurs.
MIN_DURATION_S = 3.0

# L'avancement remonte en (cle, parametres) plutot qu'en phrase toute faite :
# l'interface existe en deux langues, et c'est elle qui sait laquelle afficher.
# Le service, lui, n'a aucune raison de le savoir.
ProgressFn = Callable[[str, dict], None]


class DiarizationUnavailable(RuntimeError):
    """La diarisation ne peut pas tourner : dependance ou modele manquant.

    `key` designe le cas pour l'interface, qui le traduit ; le message reste
    lisible tel quel dans le journal et sert de repli.
    """

    def __init__(self, message: str, *, key: str = "", params: dict | None = None) -> None:
        super().__init__(message)
        self.key = key
        self.params = params or {}


@dataclass(slots=True)
class DiarizationResult:
    segments: list[SpeakerSegment]
    speakers: int


def is_installed() -> bool:
    """sherpa-onnx est-il disponible ? (dependance installee et importable)"""
    try:
        import sherpa_onnx  # noqa: F401
    except ImportError:
        return False
    return True


def models_present() -> bool:
    return SEGMENTATION_MODEL.is_file() and EMBEDDING_MODEL.is_file()


def _download(url: str, target: Path, on_progress: ProgressFn | None, label: str) -> None:
    """Telecharge vers un fichier temporaire, puis renomme.

    Le renommage final est ce qui rend `models_present()` digne de confiance :
    un telechargement interrompu laisse un `.part`, jamais un modele tronque
    qui se ferait passer pour complet et echouerait au chargement.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310
            total = int(response.headers.get("Content-Length") or 0)
            received = 0
            step = 2 * 1024 * 1024
            next_report = step
            with partial.open("wb") as fh:
                while chunk := response.read(256 * 1024):
                    fh.write(chunk)
                    received += len(chunk)
                    if on_progress and received >= next_report:
                        next_report += step
                        on_progress(
                            "diarize_download",
                            {
                                "part": label,
                                "done_mb": received // 1024 // 1024,
                                "total_mb": total // 1024 // 1024,
                            },
                        )
        partial.replace(target)
    except (urllib.error.URLError, OSError) as exc:
        partial.unlink(missing_ok=True)
        raise DiarizationUnavailable(
            f"Téléchargement du modèle de diarisation impossible ({label}) : {exc}",
            key="diarize_download_failed",
            params={"part": label, "detail": str(exc)},
        ) from exc


def ensure_models(on_progress: ProgressFn | None = None) -> None:
    """Telecharge les deux modeles s'ils manquent. Idempotent."""
    if models_present():
        return

    DIARIZATION_DIR.mkdir(parents=True, exist_ok=True)

    if not SEGMENTATION_MODEL.is_file():
        archive = DIARIZATION_DIR / "segmentation.tar.bz2"
        _download(SEGMENTATION_URL, archive, on_progress, "segmentation")
        try:
            with tarfile.open(archive) as tar:
                # `filter="data"` refuse les chemins absolus et les liens qui
                # sortiraient du dossier — l'archive vient du reseau.
                tar.extractall(DIARIZATION_DIR, filter="data")
        except (tarfile.TarError, OSError) as exc:
            raise DiarizationUnavailable(
                f"Archive de segmentation illisible : {exc}",
                key="diarize_archive_unreadable",
                params={"detail": str(exc)},
            ) from exc
        finally:
            archive.unlink(missing_ok=True)

    if not EMBEDDING_MODEL.is_file():
        _download(EMBEDDING_URL, EMBEDDING_MODEL, on_progress, "embedding")

    if not models_present():
        raise DiarizationUnavailable(
            "Modèles de diarisation absents après téléchargement. "
            f"Attendus dans {DIARIZATION_DIR}.",
            key="diarize_models_missing",
            params={"path": str(DIARIZATION_DIR)},
        )


def clear_models() -> None:
    """Supprime les modeles telecharges (reglages : liberer l'espace)."""
    shutil.rmtree(DIARIZATION_DIR, ignore_errors=True)


class Diarizer:
    """Charge les modeles une fois et les garde residents.

    Meme logique que les moteurs de transcription : recharger 35 Mo a chaque
    fichier d'un lot serait absurde.
    """

    def __init__(self, *, num_speakers: int = 0, threshold: float = DEFAULT_THRESHOLD) -> None:
        self.num_speakers = num_speakers
        self.threshold = threshold
        self._pipeline = None
        self._signature: tuple[int, float] | None = None

    def _build(self):
        try:
            import sherpa_onnx
        except ImportError as exc:  # pragma: no cover - depend de l'installation
            raise DiarizationUnavailable(
                "sherpa-onnx n'est pas installé. Relance : uv pip install -e .",
                key="diarize_not_installed",
            ) from exc

        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=str(SEGMENTATION_MODEL)
                ),
            ),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(EMBEDDING_MODEL)),
            clustering=sherpa_onnx.FastClusteringConfig(
                # -1 = « trouve toi-meme combien ils sont ». Un nombre positif
                # impose le compte, et c'est nettement plus fiable : sur
                # l'enregistrement de reference, impose il est exact, tandis que
                # l'automatique varie de 2 a 8 selon le seuil.
                num_clusters=self.num_speakers if self.num_speakers >= 2 else -1,
                threshold=self.threshold,
            ),
            min_duration_on=0.3,
            min_duration_off=0.5,
        )
        if not config.validate():
            raise DiarizationUnavailable(
                "Configuration de diarisation refusée par sherpa-onnx "
                "(modèle manquant ou illisible).",
                key="diarize_config_rejected",
            )
        return sherpa_onnx.OfflineSpeakerDiarization(config)

    def _pipeline_for(self, num_speakers: int, threshold: float):
        """Le pipeline est reconstruit quand les reglages de regroupement changent.

        Ils sont figes a la construction cote sherpa-onnx : les modeles, eux,
        sont relus a chaque fois, mais ils sont petits et le cas est rare.
        """
        signature = (num_speakers, threshold)
        if self._pipeline is None or self._signature != signature:
            self.num_speakers, self.threshold = num_speakers, threshold
            self._pipeline = self._build()
            self._signature = signature
        return self._pipeline

    def diarize(
        self,
        audio: np.ndarray,
        *,
        num_speakers: int | None = None,
        threshold: float | None = None,
    ) -> DiarizationResult:
        """Rend les tours de parole, tries par instant de debut."""
        wanted = self.num_speakers if num_speakers is None else num_speakers
        level = self.threshold if threshold is None else threshold

        seconds = len(audio) / SAMPLE_RATE
        if seconds < MIN_DURATION_S:
            log.info("Diarisation ignorée : %.1f s, trop court pour distinguer des voix", seconds)
            return DiarizationResult(segments=[], speakers=0)

        pipeline = self._pipeline_for(wanted, level)
        audio = np.ascontiguousarray(audio, dtype=np.float32)
        result = pipeline.process(audio).sort_by_start_time()

        segments = [
            SpeakerSegment(start=float(s.start), end=float(s.end), speaker=int(s.speaker))
            for s in result
        ]
        speakers = len({s.speaker for s in segments})
        log.info(
            "Diarisation : %d locuteur(s), %d tour(s) sur %.1f s",
            speakers,
            len(segments),
            seconds,
        )
        return DiarizationResult(segments=segments, speakers=speakers)
