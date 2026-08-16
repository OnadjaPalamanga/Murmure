"""Le transport : ce qui est refuse au handshake, et ce qui est borne.

Ces tests montent la vraie application FastAPI. C'est le seul endroit ou l'on
verifie l'ORDRE des operations — un controle pose apres `accept()` laisserait
une page web mesurer la difference entre « jeton faux » et « service absent »,
et surtout ouvrirait la connexion avant de la refermer.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi", reason="transport non installe")
pytest.importorskip("httpx", reason="TestClient a besoin de httpx")

from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from murmure import auth  # noqa: E402
from murmure.server import _bounded, app  # noqa: E402


@pytest.fixture
def token(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "TOKEN_FILE", tmp_path / "session.token")
    issued = auth.issue_token()
    yield issued
    auth.revoke_token()


def _subprotocols(token: str) -> list[str]:
    return [auth.SUBPROTOCOL, auth.TOKEN_PREFIX + token]


class TestHandshake:
    def test_sans_jeton_la_connexion_est_refusee(self, token) -> None:
        client = TestClient(app)
        # C'est exactement ce que faisait une page web : se connecter sans rien.
        with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws"):
            pass

    def test_un_mauvais_jeton_est_refuse(self, token) -> None:
        client = TestClient(app)
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect("/ws", subprotocols=_subprotocols("faux")),
        ):
            pass

    def test_une_origine_de_navigateur_est_refusee(self, token) -> None:
        client = TestClient(app)
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(
                "/ws",
                subprotocols=_subprotocols(token),
                headers={"origin": "https://exemple.fr"},
            ),
        ):
            pass

    def test_l_application_est_acceptee(self, token) -> None:
        client = TestClient(app)
        with client.websocket_connect(
            "/ws",
            subprotocols=_subprotocols(token),
            headers={"origin": "http://tauri.localhost"},
        ) as ws:
            # Le premier message est toujours l'instantane.
            assert ws.receive_json()["type"] == "snapshot"


class TestRoutesHttp:
    def test_health_repond_sans_jeton(self, token) -> None:
        # Seule route ouverte : c'est elle qui permet de decider s'il faut
        # demander un jeton, et elle ne rend que de quoi identifier le service.
        body = TestClient(app).get("/health").json()
        assert body["ok"] is True
        assert "settings_revision" in body
        # Le modele charge n'a plus a se lire depuis n'importe quelle page.
        assert "model_id" not in body

    def test_l_audio_exige_un_jeton(self, token) -> None:
        assert TestClient(app).get("/audio/nimporte-quoi").status_code == 403

    def test_l_arret_exige_un_jeton(self, token) -> None:
        assert TestClient(app).post("/shutdown").status_code == 403

    def test_l_arret_refuse_un_mauvais_jeton(self, token) -> None:
        response = TestClient(app).post(
            "/shutdown", headers={auth.TOKEN_HEADER: "faux"}
        )
        assert response.status_code == 403


class TestBornes:
    """`int(command.get("limit", 100))` levait sur une chaine et laissait
    passer un million quand c'en etait un."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, 100),
            ("beaucoup", 100),
            ({}, 100),
            (-5, 0),
            (10, 10),
            (10**9, 500),
        ],
    )
    def test_la_limite_est_ramenee_dans_les_clous(self, value, expected) -> None:
        assert _bounded(value, 100, 500) == expected
