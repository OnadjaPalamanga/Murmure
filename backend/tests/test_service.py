"""Le service, avec un moteur factice.

`Service` porte toute la logique metier et n'avait aucun test : c'est
precisement la que vivaient les defauts trouves a l'audit. Rien ici ne demande
de GPU, de micro ni de modele — le protocole `Engine` existe justement pour
qu'on puisse en substituer un.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from murmure.engines.base import SAMPLE_RATE, Transcript, Word
from murmure.service import Service, _refuse_destination


class FakeEngine:
    """Un moteur qui rend un texte fixe. Meme contrat que les vrais."""

    def __init__(self, text: str = "bonjour le monde", words: list[Word] | None = None) -> None:
        self.model_id = "fake"
        self.is_loaded = True
        self.device = "cpu"
        self.text = text
        self.words = words or []
        self.calls: list[dict] = []

    def load(self) -> None:
        self.is_loaded = True

    def unload(self) -> None:
        self.is_loaded = False

    def warmup(self) -> None:
        pass

    def transcribe(self, audio, language=None, *, timestamps: bool = False) -> Transcript:
        self.calls.append({"language": language, "timestamps": timestamps, "samples": len(audio)})
        return Transcript(
            text=self.text,
            language=language or "fr",
            audio_seconds=len(audio) / SAMPLE_RATE,
            latency_ms=10,
            device="cpu",
            words=list(self.words) if timestamps else [],
        )


@pytest.fixture
def service(tmp_path, monkeypatch):
    """Un service isole : sa configuration et son historique vivent dans tmp.

    On substitue les DEUX classes telles que `service.py` les a importees, et
    non les constantes de chemin : `ConfigStore(path=CONFIG_FILE)` et
    `History(db_path=HISTORY_DB)` figent leur valeur par defaut a l'import du
    module. Repeindre la constante apres coup ne change rien, et le test irait
    ecrire dans le vrai `%APPDATA%\\Murmure` de la machine.
    """
    import murmure.service as service_module
    from murmure.config import ConfigStore
    from murmure.history import History

    monkeypatch.setattr(
        service_module, "ConfigStore", lambda: ConfigStore(path=tmp_path / "config.toml")
    )
    monkeypatch.setattr(
        service_module, "History", lambda: History(db_path=tmp_path / "history.db")
    )

    svc = Service()
    svc._engine = FakeEngine()
    yield svc
    svc.history.close()


# ------------------------------------------------------------------ export


class TestExportRefuseLesMauvaisChemins:
    """`export_entry` ecrivait ou on lui disait, sans aucune borne.

    Combine a `history_update`, c'etait une primitive d'ecriture arbitraire :
    on mettait le contenu voulu dans une entree, on l'exportait vers le dossier
    Demarrage, et c'etait une execution de code au demarrage suivant.
    """

    def test_un_chemin_normal_passe(self, tmp_path) -> None:
        assert _refuse_destination(str(tmp_path / "sous-titres.srt"), "srt") is None

    def test_le_dossier_demarrage_est_refuse(self) -> None:
        cible = r"C:\Users\x\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\z.txt"
        assert _refuse_destination(cible, "txt") == "dossier système"

    def test_system32_est_refuse(self) -> None:
        assert _refuse_destination(r"C:\Windows\System32\drivers\etc\hosts.txt", "txt") == (
            "dossier système"
        )

    def test_un_chemin_relatif_est_refuse(self) -> None:
        assert _refuse_destination("sous-titres.srt", "srt") == "chemin relatif"

    def test_l_extension_doit_correspondre_au_format(self, tmp_path) -> None:
        # Sinon on ecrit du texte brut dans un `.bat`, ce qui est exactement le
        # pas manquant entre « ecrire un fichier » et « executer du code ».
        assert _refuse_destination(str(tmp_path / "charge.bat"), "txt") == "extension attendue .txt"

    def test_un_dossier_inexistant_est_refuse(self, tmp_path) -> None:
        cible = tmp_path / "pas" / "encore" / "la" / "x.srt"
        assert _refuse_destination(str(cible), "srt") == "dossier introuvable"

    def test_les_remontees_sont_resolues_avant_le_controle(self, tmp_path) -> None:
        # `..` ne doit pas permettre de contourner la liste des dossiers refuses.
        piege = str(tmp_path / "a" / ".." / ".." / "ailleurs.srt")
        assert _refuse_destination(piege, "srt") in (None, "dossier introuvable")

    def test_le_service_refuse_sans_lever(self, service) -> None:
        entry = service.history.add(text="bonjour", model_id="fake")
        result = service.export_entry(entry["id"], "txt", "relatif.txt")
        assert result["ok"] is False
        assert result["key"] == "export_bad_path"

    def test_un_export_legitime_ecrit_bien(self, service, tmp_path) -> None:
        entry = service.history.add(text="bonjour le monde", model_id="fake")
        cible = tmp_path / "note.txt"
        result = service.export_entry(entry["id"], "txt", str(cible))
        assert result["ok"] is True, result
        assert "bonjour le monde" in cible.read_text(encoding="utf-8")


# ------------------------------------------------------------- historique


class TestArretPropre:
    def test_ecrire_apres_fermeture_leve_une_erreur_nommee(self, service) -> None:
        from murmure.history import HistoryClosed

        service.history.close()
        # Avant : `ProgrammingError: Cannot operate on a closed database`, leve
        # depuis un thread daemon en pleine transcription. Desormais une erreur
        # qu'on peut distinguer d'une panne de disque, et traiter en silence.
        with pytest.raises(HistoryClosed):
            service.history.add(text="trop tard", model_id="fake")

    def test_fermer_deux_fois_ne_leve_pas(self, service) -> None:
        service.history.close()
        service.history.close()


# ---------------------------------------------------------------- reglages


class TestReglages:
    def test_un_reglage_invalide_n_atteint_pas_le_service(self, service) -> None:
        service.update_settings({"dictation_mode": "n'importe quoi"})
        assert service.config.settings.dictation_mode == "differe"

    def test_le_pre_enregistrement_redimensionne_vraiment_le_tampon(self, service) -> None:
        # Le defaut vaut 400 ms, soit 20 blocs de 20 ms.
        assert service.recorder._preroll.maxlen == 20
        service.update_settings({"preroll_ms": 1000})
        # C'etait un simple attribut que plus rien ne relisait : le `maxlen`
        # restait a 20 et le reglage ne faisait rien jusqu'au redemarrage.
        assert service.recorder._preroll.maxlen == 50

    def test_un_reglage_valide_est_persiste(self, service) -> None:
        service.update_settings({"hotkey": "Ctrl+Alt+D"})
        assert service.config.settings.hotkey == "Ctrl+Alt+D"
        assert "Ctrl+Alt+D" in Path(service.config.path).read_text(encoding="utf-8")


# ------------------------------------------------------------ transcription


class TestTranscription:
    def test_une_dictee_arrive_dans_l_historique(self, service) -> None:
        audio = np.full(SAMPLE_RATE, 0.3, dtype=np.float32)
        service._transcribe_worker(audio)

        entries = service.history.search("")
        assert len(entries) == 1
        assert entries[0]["text"] == "bonjour le monde"

    def test_un_audio_trop_court_ne_transcrit_rien(self, service) -> None:
        audio = np.full(int(SAMPLE_RATE * 0.1), 0.3, dtype=np.float32)
        service._transcribe_worker(audio)
        assert service.history.search("") == []

    def test_un_audio_silencieux_ne_transcrit_rien(self, service) -> None:
        audio = np.zeros(SAMPLE_RATE * 2, dtype=np.float32)
        service._transcribe_worker(audio)
        assert service.history.search("") == []

    def test_les_regles_de_texte_sont_appliquees(self, service) -> None:
        service.update_settings({"replacements": {"monde": "Monde"}})
        service._engine = FakeEngine(text="bonjour le monde")
        service._transcribe_worker(np.full(SAMPLE_RATE, 0.3, dtype=np.float32))
        assert service.history.search("")[0]["text"] == "bonjour le Monde"

    def test_la_dictee_ne_demande_jamais_de_datation(self, service) -> None:
        # Dater les mots coute du calcul, et la dictee n'en fait rien : c'est
        # une promesse de la documentation, elle merite d'etre verrouillee.
        service._transcribe_worker(np.full(SAMPLE_RATE, 0.3, dtype=np.float32))
        assert service._engine.calls[0]["timestamps"] is False
