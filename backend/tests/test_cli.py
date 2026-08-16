"""Ligne de commande : ce qui est decide AVANT que le modele soit charge.

Aucun modele, aucun audio. Ce qui se joue ici est la partie de la commande qui
peut detruire du travail ou en produire au mauvais endroit : quels fichiers ont
ete designes, ou leurs sorties vont atterrir, et lesquelles seraient ecrasees.
Une erreur y est silencieuse — un lot de trente rushes qui ecrit trente fois
au meme endroit termine sans rien signaler.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from murmure.cli import (
    Reporter,
    _destination,
    _expand,
    _format_list,
    _plan,
    _resolve_conflicts,
    _resolve_settings,
    _validate,
    build_parser,
)
from murmure.config import Settings


@pytest.fixture
def quiet() -> Reporter:
    return Reporter(quiet=True)


def touch(path: Path, name: str) -> Path:
    target = path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"")
    return target


# ------------------------------------------------------------ les formats


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("srt", ["srt"]),
        ("SRT", ["srt"]),
        (".srt", ["srt"]),
        ("srt,json", ["srt", "json"]),
        ("srt, json ", ["srt", "json"]),
        # Les noms qu'on tape spontanement valent les identifiants exacts.
        ("text", ["txt"]),
        ("webvtt", ["vtt"]),
        ("subrip", ["srt"]),
    ],
)
def test_les_noms_de_format_acceptes(given, expected):
    assert _format_list(given) == expected


def test_un_format_inconnu_est_refuse_avec_la_liste():
    with pytest.raises(argparse.ArgumentTypeError, match="srt, vtt, json, txt"):
        _format_list("docx")


def test_les_formats_se_cumulent_sur_plusieurs_options():
    args = build_parser().parse_args(["transcribe", "a.wav", "-f", "srt,json", "-f", "txt"])
    assert args.format == ["srt", "json", "txt"]


# ------------------------------------------------------- fichiers designes


def test_un_joker_est_developpe_par_la_commande(tmp_path):
    """PowerShell ne developpe pas les jokers pour un executable natif : sans
    ce developpement, `transcribe *.mp4` cherche un fichier nomme « *.mp4 »."""
    touch(tmp_path, "a.mp4")
    touch(tmp_path, "b.mp4")
    touch(tmp_path, "c.txt")

    found, missing = _expand([str(tmp_path / "*.mp4")], recursive=False)

    assert [p.name for p in found] == ["a.mp4", "b.mp4"]
    assert missing == []


def test_un_joker_sans_correspondance_est_signale(tmp_path):
    found, missing = _expand([str(tmp_path / "*.mkv")], recursive=False)
    assert found == []
    assert missing == [str(tmp_path / "*.mkv")]


def test_un_dossier_ne_prend_que_les_medias(tmp_path):
    touch(tmp_path, "prise.wav")
    touch(tmp_path, "notes.txt")
    touch(tmp_path, "sous-dossier/enfoui.mp4")

    found, _ = _expand([str(tmp_path)], recursive=False)
    assert [p.name for p in found] == ["prise.wav"]

    deep, _ = _expand([str(tmp_path)], recursive=True)
    assert {p.name for p in deep} == {"prise.wav", "enfoui.mp4"}


def test_un_fichier_nomme_explicitement_passe_quelle_que_soit_son_extension(tmp_path):
    """Designer un fichier a la main vaut mieux que la liste d'extensions :
    elle sert a fouiller un dossier, pas a refuser ce qu'on demande."""
    odd = touch(tmp_path, "prise.bizarre")
    found, missing = _expand([str(odd)], recursive=False)
    assert found == [odd.resolve()]
    assert missing == []


def test_un_fichier_designe_deux_fois_n_est_traite_qu_une(tmp_path):
    """Sinon le second passage refuserait d'ecraser ce que le premier vient
    d'ecrire, et le lot echouerait sur son propre travail."""
    media = touch(tmp_path, "prise.wav")
    found, _ = _expand([str(media), str(tmp_path / "*.wav")], recursive=False)
    assert found == [media.resolve()]


def test_un_fichier_absent_est_signale_et_ne_bloque_pas_les_autres(tmp_path):
    media = touch(tmp_path, "prise.wav")
    found, missing = _expand([str(media), str(tmp_path / "fantome.wav")], recursive=False)
    assert found == [media.resolve()]
    assert missing == [str(tmp_path / "fantome.wav")]


# --------------------------------------------------- ou les sorties vont


def test_sans_output_la_sortie_se_pose_a_cote_de_l_entree(tmp_path):
    source = tmp_path / "rushes" / "entretien.mp4"
    assert _destination(source, "srt", None, single=True) == tmp_path / "rushes" / "entretien.srt"


def test_output_est_un_dossier(tmp_path):
    source = tmp_path / "entretien.mp4"
    assert _destination(source, "srt", tmp_path / "subs", single=True) == (
        tmp_path / "subs" / "entretien.srt"
    )


def test_output_peut_etre_un_fichier_quand_il_n_y_a_qu_une_sortie(tmp_path):
    source = tmp_path / "entretien.mp4"
    wanted = tmp_path / "subs" / "final.srt"
    assert _destination(source, "srt", wanted, single=True) == wanted


def test_output_reste_un_dossier_des_qu_il_y_a_plusieurs_sorties(tmp_path):
    """« -o final.srt » sur trois fichiers ecrirait trois fois au meme endroit
    et ne laisserait que le dernier."""
    source = tmp_path / "entretien.mp4"
    wanted = tmp_path / "final.srt"
    assert _destination(source, "srt", wanted, single=False) == wanted / "entretien.srt"


def test_chaque_format_a_sa_propre_sortie(tmp_path):
    jobs = _plan([tmp_path / "a.mp4"], ["srt", "json"], None)
    assert jobs[0].outputs == {
        "srt": tmp_path / "a.srt",
        "json": tmp_path / "a.json",
    }


# ------------------------------------------------- ce qui existe deja


def options(**overrides) -> SimpleNamespace:
    base = {"overwrite": False, "skip_existing": False}
    return SimpleNamespace(**{**base, **overrides})


def test_une_sortie_existante_bloque_tout_le_lot(tmp_path, quiet):
    """Et le lot est refuse AVANT le chargement du modele : apprendre le
    conflit apres vingt minutes de transcription serait couteux."""
    touch(tmp_path, "a.srt")
    jobs, blocked = _resolve_conflicts(
        _plan([tmp_path / "a.mp4", tmp_path / "b.mp4"], ["srt"], None), options(), quiet
    )
    assert blocked is True
    assert [j.source.name for j in jobs] == ["b.mp4"]


def test_overwrite_reprend_tout(tmp_path, quiet):
    touch(tmp_path, "a.srt")
    jobs, blocked = _resolve_conflicts(
        _plan([tmp_path / "a.mp4"], ["srt"], None), options(overwrite=True), quiet
    )
    assert blocked is False
    assert len(jobs) == 1


def test_skip_existing_ne_garde_que_les_formats_manquants(tmp_path, quiet):
    """Un fichier deja sous-titre mais pas encore exporte en JSON ne doit pas
    etre retranscrit pour rien, ni voir son SRT refait."""
    touch(tmp_path, "a.srt")
    jobs, blocked = _resolve_conflicts(
        _plan([tmp_path / "a.mp4"], ["srt", "json"], None), options(skip_existing=True), quiet
    )
    assert blocked is False
    assert list(jobs[0].outputs) == ["json"]


def test_skip_existing_saute_un_fichier_entierement_fait(tmp_path, quiet):
    touch(tmp_path, "a.srt")
    jobs, blocked = _resolve_conflicts(
        _plan([tmp_path / "a.mp4"], ["srt"], None), options(skip_existing=True), quiet
    )
    assert blocked is False
    assert jobs == []


# ------------------------------------------- reglages de l'application


def settings(**overrides) -> Settings:
    base = Settings()
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def command(**overrides) -> SimpleNamespace:
    base = {"language": None, "diarize": None, "speakers": None, "threshold": None, "gpu": None}
    return SimpleNamespace(**{**base, **overrides})


def test_sans_option_les_reglages_de_l_application_s_appliquent():
    resolved = _resolve_settings(
        command(), settings(language="fr", diarize_files=True, diarize_speakers=3, prefer_gpu=False)
    )
    assert resolved == {
        "language": "fr",
        "diarize": True,
        "speakers": 3,
        "threshold": pytest.approx(0.6),
        "prefer_gpu": False,
    }


def test_chaque_option_remplace_le_reglage():
    resolved = _resolve_settings(
        command(language="en", diarize=False, gpu=True),
        settings(language="fr", diarize_files=True, prefer_gpu=False),
    )
    assert resolved["language"] == "en"
    assert resolved["diarize"] is False
    assert resolved["prefer_gpu"] is True


@pytest.mark.parametrize("value", ["auto", "AUTO", "", "  "])
def test_auto_devient_none_ce_que_le_moteur_lit_comme_detection(value):
    assert _resolve_settings(command(language=value), settings())["language"] is None


def test_donner_le_nombre_de_personnes_suffit_a_demander_la_diarisation():
    """Taper « --speakers 3 » sans « --diarize » est ce qu'on fait
    spontanement ; l'ignorer rendrait un transcript sans locuteurs."""
    resolved = _resolve_settings(command(speakers=3), settings(diarize_files=False))
    assert resolved["diarize"] is True
    assert resolved["speakers"] == 3


# ------------------------------------------- combinaisons contradictoires


def parsed(*argv) -> SimpleNamespace:
    return build_parser().parse_args(["transcribe", *argv])


def test_speakers_et_no_diarize_ensemble_sont_refuses(capsys):
    args = parsed("a.wav", "--speakers", "3", "--no-diarize")
    with pytest.raises(SystemExit) as exit_code:
        _validate(args, ["srt"], args.parser)
    assert exit_code.value.code == 2
    assert "--no-diarize" in capsys.readouterr().err


def test_stdout_refuse_plusieurs_fichiers(capsys):
    """Deux JSON colles bout a bout ne se relisent pas : mieux vaut le dire
    que rendre une sortie que le maillon suivant du pipeline rejettera."""
    args = parsed("a.wav", "b.wav", "-f", "json", "--stdout")
    with pytest.raises(SystemExit):
        _validate(args, ["json"], args.parser)
    assert "--stdout" in capsys.readouterr().err


def test_stdout_et_json_ecrivent_au_meme_endroit(capsys):
    args = parsed("a.wav", "-f", "json", "--stdout", "--json")
    with pytest.raises(SystemExit):
        _validate(args, ["json"], args.parser)
    assert "--json" in capsys.readouterr().err


def test_une_commande_coherente_passe():
    args = parsed("a.wav", "-f", "srt", "--speakers", "2")
    _validate(args, ["srt"], args.parser)


# ------------------------------------------------------- compatibilite


def test_la_commande_par_defaut_reste_le_service():
    """L'application Tauri lance `python -m murmure` sans argument et
    `install.ps1` pointe dessus : ce cas doit rester le service."""
    assert build_parser().parse_args([]).command is None


def test_les_trois_commandes_existent():
    parser = build_parser()
    assert parser.parse_args(["transcribe", "a.wav"]).command == "transcribe"
    assert parser.parse_args(["models"]).command == "models"
    assert parser.parse_args(["serve"]).command == "serve"
