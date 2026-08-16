"""Reglages persistes : ce qui est ecrit, ce qui est relu, ce qui est ignore.

Le fichier de configuration est modifiable a la main. Il doit donc survivre a
tout ce qu'un humain — ou une version anterieure — peut y laisser.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from murmure.config import ConfigStore, Settings, apply_text_rules
from murmure.models import CATALOG, default_model_id


@pytest.fixture
def store(tmp_path: Path) -> ConfigStore:
    return ConfigStore(tmp_path / "config.toml")


class TestDefauts:
    def test_le_modele_par_defaut_vient_du_catalogue(self) -> None:
        """Recopier l'identifiant en dur a deja fait diverger l'installation
        neuve du modele que le catalogue annonce comme recommande."""
        assert Settings().model_id == default_model_id()

    def test_le_catalogue_designe_exactement_un_defaut(self) -> None:
        assert sum(1 for spec in CATALOG if spec.is_default) == 1

    def test_les_identifiants_du_catalogue_sont_uniques(self) -> None:
        ids = [spec.id for spec in CATALOG]
        assert len(ids) == len(set(ids))

    def test_le_differe_reste_le_defaut(self) -> None:
        """C'est lui qui protege le texte qui compte : en continu, il n'y a plus
        de relecture avant que ce soit ecrit."""
        assert Settings().dictation_mode == "differe"

    def test_la_langue_est_en_detection_automatique(self) -> None:
        """Forcer une langue pousse le decodeur a transcrire phonetiquement les
        mots de l'autre langue reellement prononces."""
        assert Settings().language == "auto"

    def test_les_dictionnaires_ne_sont_pas_partages_entre_instances(self) -> None:
        """Un defaut mutable partage ferait fuiter les remplacements d'une
        instance dans l'autre."""
        a, b = Settings(), Settings()
        a.replacements["source"] = "cible"
        assert b.replacements == {}


class TestPersistance:
    def test_absence_de_fichier_donne_les_defauts(self, tmp_path: Path) -> None:
        store = ConfigStore(tmp_path / "jamais-ecrit.toml")
        assert store.settings.hotkey == Settings().hotkey

    def test_aller_retour(self, store: ConfigStore) -> None:
        store.update({"hotkey": "Ctrl+Alt+D", "preroll_ms": 250, "keep_audio": True})
        relu = ConfigStore(store.path)
        assert relu.settings.hotkey == "Ctrl+Alt+D"
        assert relu.settings.preroll_ms == 250
        assert relu.settings.keep_audio is True

    def test_seules_les_valeurs_modifiees_sont_ecrites(self, store: ConfigStore) -> None:
        """Le fichier doit rester lisible : y deverser tous les defauts le rend
        illisible et fige des valeurs qu'on voudrait pouvoir faire evoluer."""
        store.update({"hotkey": "Ctrl+Alt+D"})
        contenu = store.path.read_text(encoding="utf-8")
        assert "hotkey" in contenu
        assert "mic_keepalive_s" not in contenu

    def test_revenir_au_defaut_retire_la_ligne(self, store: ConfigStore) -> None:
        store.update({"hotkey": "Ctrl+Alt+D"})
        store.update({"hotkey": Settings().hotkey})
        assert "hotkey" not in store.path.read_text(encoding="utf-8")

    def test_les_dictionnaires_survivent_a_l_aller_retour(self, store: ConfigStore) -> None:
        store.update({"replacements": {"cad": "c'est-a-dire"}})
        assert ConfigStore(store.path).settings.replacements == {"cad": "c'est-a-dire"}

    def test_les_guillemets_sont_echappes(self, store: ConfigStore) -> None:
        """Sans echappement, une valeur contenant un guillemet produirait un
        TOML invalide et la configuration entiere repartirait aux defauts."""
        store.update({"language": 'fr"bizarre\\'})
        assert ConfigStore(store.path).settings.language == 'fr"bizarre\\'


class TestRobustesse:
    def test_un_fichier_illisible_ne_bloque_pas_le_demarrage(self, tmp_path: Path) -> None:
        chemin = tmp_path / "config.toml"
        chemin.write_text("ceci n'est pas du TOML [[[", encoding="utf-8")
        assert ConfigStore(chemin).settings.hotkey == Settings().hotkey

    def test_un_reglage_inconnu_est_ignore(self, tmp_path: Path) -> None:
        """Un reglage retire dans une version ulterieure ne doit pas empecher le
        service de demarrer."""
        chemin = tmp_path / "config.toml"
        chemin.write_text('hotkey = "Ctrl+Alt+D"\nreglage_disparu = 42\n', encoding="utf-8")
        store = ConfigStore(chemin)
        assert store.settings.hotkey == "Ctrl+Alt+D"
        assert not hasattr(store.settings, "reglage_disparu")

    def test_update_ignore_les_cles_inconnues(self, store: ConfigStore) -> None:
        store.update({"hotkey": "Ctrl+Alt+D", "nimporte_quoi": True})
        assert not hasattr(store.settings, "nimporte_quoi")

    def test_le_dossier_parent_est_cree(self, tmp_path: Path) -> None:
        store = ConfigStore(tmp_path / "pas" / "encore" / "la" / "config.toml")
        store.update({"hotkey": "Ctrl+Alt+D"})
        assert store.path.is_file()


class TestMiseEnForme:
    """`apply_text_rules` est partagee par le service et la ligne de commande.

    Deux implementations de la meme regle rendraient deux textes differents
    pour le meme audio, selon qu'on a clique ou tape la commande.
    """

    def test_les_espaces_sont_normalises(self) -> None:
        assert apply_text_rules("  du   texte \n espace ", Settings()) == "du texte espace"

    def test_les_remplacements_s_appliquent(self) -> None:
        settings = Settings(replacements={"murmur": "Murmure"})
        assert apply_text_rules("essai de murmur", settings) == "essai de Murmure"

    def test_une_cle_vide_ne_remplace_rien(self) -> None:
        """`str.replace("", x)` insere x entre chaque caractere."""
        assert apply_text_rules("intact", Settings(replacements={"": "!"})) == "intact"

    def test_le_point_final_peut_etre_retire(self) -> None:
        settings = Settings(trim_trailing_period=True)
        assert apply_text_rules("une phrase.", settings) == "une phrase"

    def test_le_point_final_est_garde_par_defaut(self) -> None:
        assert apply_text_rules("une phrase.", Settings()) == "une phrase."
