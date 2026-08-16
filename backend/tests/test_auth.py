"""Le jeton de session et le controle d'origine.

Ce qui est verifie ici n'est pas « le jeton fonctionne » mais **ce qui est
refuse**. Sans ces deux verrous, n'importe quelle page ouverte dans un
navigateur pouvait se connecter au service : la politique d'origine unique ne
s'applique pas aux WebSocket, et `ws.accept()` etait inconditionnel. La page
lisait alors tout l'historique de dictee et, en enchainant `history_update` puis
`export_entry`, ecrivait un fichier dans le dossier Demarrage.

Chaque test correspond a une facon dont l'attaque echoue desormais.
"""

from __future__ import annotations

import pytest

from murmure import auth


@pytest.fixture
def issued(tmp_path, monkeypatch):
    """Un service en marche, avec son jeton fraichement tire."""
    monkeypatch.setattr(auth, "TOKEN_FILE", tmp_path / "session.token")
    token = auth.issue_token()
    yield token
    auth.revoke_token()


class TestJeton:
    def test_le_jeton_est_ecrit_et_relisible(self, issued, tmp_path) -> None:
        assert (tmp_path / "session.token").read_text(encoding="ascii") == issued

    def test_le_jeton_est_long(self, issued) -> None:
        # 32 octets encodes en base64url. Assez pour qu'aucune page web ne le
        # devine, ce qui est tout ce qu'on lui demande.
        assert len(issued) >= 40

    def test_deux_demarrages_donnent_deux_jetons(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(auth, "TOKEN_FILE", tmp_path / "session.token")
        first = auth.issue_token()
        second = auth.issue_token()
        assert first != second
        # Et l'ancien ne vaut plus rien : c'est tout l'interet d'en retirer un
        # neuf a chaque demarrage.
        assert not auth.token_accepted(first)

    def test_le_bon_jeton_passe(self, issued) -> None:
        assert auth.token_accepted(issued)

    @pytest.mark.parametrize("candidate", ["", None, "faux", "murmure.token.x"])
    def test_tout_le_reste_est_refuse(self, issued, candidate) -> None:
        assert not auth.token_accepted(candidate)

    def test_rien_ne_passe_avant_le_demarrage(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(auth, "TOKEN_FILE", tmp_path / "session.token")
        auth.revoke_token()
        # Le cas qui compte : un jeton vide ne doit pas etre accepte par un
        # service qui n'en a pas encore, sinon la porte est grande ouverte
        # pendant la seconde qui suit le lancement.
        assert not auth.token_accepted("")
        assert not auth.token_accepted(None)

    def test_la_revocation_efface_le_fichier(self, issued, tmp_path) -> None:
        auth.revoke_token()
        assert not (tmp_path / "session.token").exists()
        assert not auth.token_accepted(issued)


class TestSousProtocole:
    def test_le_jeton_est_extrait(self) -> None:
        offered = [auth.SUBPROTOCOL, auth.TOKEN_PREFIX + "abc"]
        assert auth.token_from_subprotocols(offered) == "abc"

    def test_sans_jeton_annonce_on_rend_none(self) -> None:
        assert auth.token_from_subprotocols([auth.SUBPROTOCOL]) is None
        assert auth.token_from_subprotocols([]) is None
        assert auth.token_from_subprotocols(None) is None


class TestOrigine:
    def test_l_application_passe(self) -> None:
        assert auth.origin_accepted("http://tauri.localhost")
        assert auth.origin_accepted("https://tauri.localhost")

    def test_une_origine_absente_passe(self) -> None:
        # Un client hors navigateur — les scripts de `scripts/` — n'envoie pas
        # d'origine. Il s'authentifie par le jeton, qu'il lit sur le disque
        # parce qu'il tourne sous la session de l'utilisateur.
        assert auth.origin_accepted(None)

    @pytest.mark.parametrize(
        "origin",
        [
            "https://exemple.fr",
            "http://localhost:3000",
            "null",
            "http://tauri.localhost.exemple.fr",
        ],
    )
    def test_une_page_web_est_refusee(self, origin) -> None:
        assert not auth.origin_accepted(origin)
