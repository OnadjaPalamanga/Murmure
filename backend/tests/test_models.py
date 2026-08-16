"""Catalogue et detection des modeles deposes a la main.

`models/` est un dossier ouvert : l'utilisateur y depose ce qu'il veut, et
Murmure n'y range pas que des modeles de transcription. Distinguer les deux est
ce que ces tests verifient.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from murmure import models
from murmure.models import CATALOG, ModelSpec, default_model_id, list_models, resolve_spec


@pytest.fixture
def models_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Detourne `models/` vers un dossier vide, propre a chaque test."""
    monkeypatch.setattr(models, "MODELS_DIR", tmp_path)
    return tmp_path


def _drop_onnx_model(root: Path, name: str) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "encoder.onnx").write_bytes(b"")
    return folder


class TestDossiersReserves:
    """`models/` heberge aussi ce qui n'est pas un modele de transcription."""

    def test_le_cache_huggingface_n_est_pas_un_modele(self, models_dir: Path) -> None:
        _drop_onnx_model(models_dir, "hub")
        assert [m for m in list_models() if m.is_local] == []

    def test_les_modeles_de_diarisation_ne_sont_pas_un_modele(self, models_dir: Path) -> None:
        """L'extracteur d'empreinte vocale est un .onnx pose a la racine de
        `models/diarization` — exactement la signature d'un modele onnx-asr
        depose a la main. Sans exclusion il apparait dans le catalogue, et le
        choisir casse la dictee."""
        _drop_onnx_model(models_dir, "diarization")
        assert [m for m in list_models() if m.is_local] == []

    def test_un_vrai_modele_depose_est_bien_detecte(self, models_dir: Path) -> None:
        """L'exclusion doit rester une liste courte, pas une regle qui attrape
        aussi ce qu'on veut voir."""
        _drop_onnx_model(models_dir, "mon-modele")
        locaux = [m for m in list_models() if m.is_local]
        assert [m.label for m in locaux] == ["mon-modele"]
        assert locaux[0].engine == "parakeet"

    def test_un_dossier_ctranslate2_est_detecte(self, models_dir: Path) -> None:
        folder = models_dir / "whisper-a-moi"
        folder.mkdir()
        (folder / "model.bin").write_bytes(b"")
        locaux = [m for m in list_models() if m.is_local]
        assert [(m.label, m.engine) for m in locaux] == [("whisper-a-moi", "whisper")]

    def test_un_dossier_sans_modele_est_ignore(self, models_dir: Path) -> None:
        (models_dir / "notes").mkdir()
        (models_dir / "notes" / "lisez-moi.txt").write_text("rien ici", encoding="utf-8")
        assert [m for m in list_models() if m.is_local] == []


class TestDescriptions:
    """L'interface existe en deux langues ; le catalogue doit suivre."""

    def test_chaque_modele_du_catalogue_est_decrit_dans_les_deux_langues(self) -> None:
        muets = [s.id for s in CATALOG if not s.blurb.strip() or not s.blurb_en.strip()]
        assert muets == []

    def test_chaque_modele_annonce_ses_langues_dans_les_deux_langues(self) -> None:
        muets = [s.id for s in CATALOG if not s.languages.strip() or not s.languages_en.strip()]
        assert muets == []

    def test_un_modele_local_sans_description_en_recoit_une_dans_les_deux(
        self, models_dir: Path
    ) -> None:
        _drop_onnx_model(models_dir, "sans-metadonnees")
        (local,) = [m for m in list_models() if m.is_local]
        assert local.blurb and local.blurb_en

    def test_une_description_fournie_dans_une_seule_langue_est_conservee(
        self, models_dir: Path
    ) -> None:
        """Mieux vaut la phrase de l'utilisateur dans sa langue que la notre a
        la place : c'est lui qui sait ce que fait son modele."""
        folder = _drop_onnx_model(models_dir, "decrit-en-francais")
        (folder / "murmure.json").write_text(
            json.dumps({"blurb": "Mon modele a moi."}, ensure_ascii=False), encoding="utf-8"
        )
        (local,) = [m for m in list_models() if m.is_local]
        assert local.blurb == "Mon modele a moi."
        assert local.blurb_en == ""

    def test_un_murmure_json_illisible_ne_fait_pas_disparaitre_le_modele(
        self, models_dir: Path
    ) -> None:
        folder = _drop_onnx_model(models_dir, "metadonnees-cassees")
        (folder / "murmure.json").write_text("{ pas du json", encoding="utf-8")
        assert [m.label for m in list_models() if m.is_local] == ["metadonnees-cassees"]


class TestRepli:
    def test_un_identifiant_inconnu_retombe_sur_le_defaut(self) -> None:
        """Sans repli, `ensure_engine` leve a chaque dictee et l'interface ne
        permet meme plus de changer de modele."""
        assert resolve_spec("modele-qui-n-existe-plus").id == default_model_id()

    def test_un_identifiant_connu_est_rendu_tel_quel(self) -> None:
        assert resolve_spec(default_model_id()).id == default_model_id()

    def test_le_dictionnaire_de_specification_est_serialisable(self) -> None:
        """Il part au frontend tel quel : un champ non serialisable couperait le
        snapshot, donc tout l'ecran."""
        spec: ModelSpec = CATALOG[0]
        json.dumps(spec.to_dict())
