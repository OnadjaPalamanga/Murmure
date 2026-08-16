"""Transport WebSocket + HTTP local. Aucune logique metier ici (voir service.py).

Le service n'ecoute que sur 127.0.0.1 : il n'est joignable que depuis la machine.
Cela ne suffit pas — un navigateur atteint `localhost` sans que la politique
d'origine unique ne s'applique aux WebSocket. Tout ce qui touche a l'etat passe
donc par le jeton de session decrit dans `auth.py`.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from . import __version__
from .auth import (
    SUBPROTOCOL,
    TOKEN_HEADER,
    issue_token,
    origin_accepted,
    revoke_token,
    token_accepted,
    token_from_subprotocols,
)
from .service import Service

log = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 8756

# Plafonds sur ce qu'une commande peut demander. Sans eux, un `limit` d'un
# million materialise un million d'entrees en memoire puis les serialise en
# JSON — un client qui se trompe d'unite suffit a figer le service.
MAX_SEARCH_LIMIT = 500
MAX_SEARCH_OFFSET = 1_000_000

# Empreinte du service : ce qui distingue « un service repond » de « LE service
# de cette version repond ». Le port seul ne le dit pas — un service demarre
# des jours plus tot le detient tout aussi bien, et l'interface se retrouve a
# piloter un backend qui ignore la moitie de ses reglages. Mesure sur le vif :
# un service reste ouvert pendant quatre jours affichait des menus vides sans
# la moindre erreur, parce que personne n'avait demande son age.
#
# `SETTINGS_REVISION` monte des qu'un reglage est ajoute, retire ou change de
# sens — et de meme quand une commande apparait : une interface qui compte sur
# `diarize_download` et parle a un service qui l'ignore ne recoit qu'un
# « commande inconnue » au moment ou l'utilisateur clique.
# La version du paquet ne suffit pas : elle bouge trop rarement.
#
# 6 : le WebSocket exige un jeton de session. Une interface anterieure se
# connecte sans, et serait fermee au handshake sans rien pouvoir en dire.
SETTINGS_REVISION = 6

service = Service()

# Taches d'arret en vol. Voir `/shutdown` : sans cette reference forte, la
# boucle asyncio peut ramasser la tache avant qu'elle n'ait fait son travail.
_pending_shutdown: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN001, ARG001
    # Avant tout le reste : le jeton doit exister avant qu'un client puisse se
    # presenter, et l'application attend le fichier pour se connecter.
    issue_token()
    service.bind_loop(asyncio.get_running_loop())
    service.preload()
    try:
        yield
    finally:
        revoke_token()
        service.shutdown()


app = FastAPI(title="Murmure", lifespan=lifespan)


@app.get("/health")
async def health() -> JSONResponse:
    """Etat du service, et surtout de QUELLE version il est.

    L'application compare `settings_revision` a la sienne avant de piloter ce
    service : sans ca, un backend perime repond normalement a tout et l'ecart
    ne se voit que sur des reglages inexplicablement vides.

    Seule route sans jeton, parce que c'est elle qui permet de decider s'il faut
    en demander un — et elle ne rend que de quoi identifier le service. Le
    modele charge et le peripherique d'entree n'y sont plus : ils ne servaient a
    aucun appelant, et n'importe quelle page web pouvait les lire.
    """
    return JSONResponse(
        {
            "ok": True,
            "state": service.state,
            "version": __version__,
            "settings_revision": SETTINGS_REVISION,
        }
    )


@app.post("/shutdown")
async def shutdown(request: Request) -> JSONResponse:
    """Arrete le service. Reserve au remplacement d'une version perimee.

    L'application appelle ceci quand elle trouve sur le port un service d'une
    autre revision : sans quoi il faudrait deviner son PID, et le service lance
    par `run.ps1` n'est pas un enfant de l'application.

    Le jeton est exige ici comme ailleurs. Il etait tentant de laisser la route
    ouverte — « au pire on arrete le service » — mais une page web capable
    d'arreter la dictee a volonte, c'est un deni de service silencieux : le
    raccourci ne repond plus et rien n'explique pourquoi.
    """
    if not token_accepted(request.headers.get(TOKEN_HEADER)):
        log.warning("Arret refuse : jeton absent ou invalide")
        raise HTTPException(status_code=403, detail="Jeton invalide")
    if not origin_accepted(request.headers.get("origin")):
        log.warning("Arret refuse : origine %s", request.headers.get("origin"))
        raise HTTPException(status_code=403, detail="Origine refusee")

    log.info("Arret demande : une autre version prend la main")

    async def stop() -> None:
        # Apres la reponse, sinon l'appelant voit une connexion coupee et ne
        # sait pas si l'arret a ete accepte.
        await asyncio.sleep(0.1)
        signal.raise_signal(signal.SIGTERM)

    # La reference est GARDEE. La boucle asyncio ne retient ses taches que
    # faiblement : une tache dont plus personne ne tient le handle peut etre
    # ramassee avant d'avoir fini. Ici ce serait le SIGTERM qui ne partirait
    # jamais — l'application d'en face attend alors 2 s que le port se libere,
    # renonce, et l'utilisateur se retrouve avec le service perime qu'on
    # croyait avoir remplace.
    _pending_shutdown.add(task := asyncio.create_task(stop()))
    task.add_done_callback(_pending_shutdown.discard)
    return JSONResponse({"ok": True, "stopping": True})


@app.get("/audio/{entry_id}")
async def history_audio(entry_id: str, token: str = "") -> FileResponse:
    """Diffuse uniquement le fichier rattache a une entree connue de l'historique.

    Le jeton passe par la chaine de requete et non par un en-tete : cette URL
    est posee dans le `src` d'une balise `<audio>`, ou rien ne permet d'ajouter
    un en-tete. La chaine reste locale et le journal d'acces d'uvicorn est
    desactive (`access_log=False`), donc elle n'est recopiee nulle part.
    """
    if not token_accepted(token):
        raise HTTPException(status_code=403, detail="Jeton invalide")

    entry = await asyncio.to_thread(service.history.get, entry_id)
    path = Path(entry["audio_path"]) if entry and entry.get("audio_path") else None
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Audio introuvable")
    return FileResponse(path)


def _bounded(value: Any, default: int, ceiling: int) -> int:
    """Entier ramene dans [0, ceiling]. Tout ce qui n'est pas un nombre vaut le defaut.

    `int(command.get(...))` levait sur une chaine, et laissait passer un million
    quand c'en etait un.
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(number, ceiling))


async def _handle(command: dict[str, Any]) -> dict[str, Any] | None:
    """Traite une commande client. Retourne une reponse directe eventuelle.

    Les operations bloquantes (chargement de modele, acces disque) partent dans un
    thread : la boucle asyncio doit rester libre pour diffuser les niveaux audio.
    """
    kind = command.get("type")

    match kind:
        case "hello" | "get_state":
            return {"type": "snapshot", **await asyncio.to_thread(service.snapshot)}

        case "arm":
            await asyncio.to_thread(service.arm)
            return None

        case "start":
            service.start()
            return None

        case "stop":
            service.stop_and_transcribe()
            return None

        case "cancel":
            service.cancel()
            return None

        case "transcribe_files":
            paths = command.get("paths", [])
            if not paths:
                return {"type": "error", "message": "Aucun fichier fourni."}
            service.transcribe_files(paths)
            return None

        case "set_model":
            model_id = command.get("model_id", "")
            status = await asyncio.to_thread(service.set_model, model_id)
            return {"type": "engine", **status}

        case "update_settings":
            changes = command.get("settings", {})
            snapshot = await asyncio.to_thread(service.update_settings, changes)
            return {"type": "snapshot", **snapshot}

        # Les 35 Mo de modeles de diarisation se telechargent depuis les
        # reglages, pas seulement au premier fichier importe. Dans un thread :
        # le telechargement dure, la boucle asyncio doit rester libre pour
        # diffuser l'avancement qu'il emet.
        case "diarize_download":
            snapshot = await asyncio.to_thread(service.diarization_download)
            return {"type": "snapshot", **snapshot}

        case "diarize_clear":
            snapshot = await asyncio.to_thread(service.diarization_clear)
            return {"type": "snapshot", **snapshot}

        # Ecriture d'un fichier sur le disque, vers un chemin choisi dans un
        # dialogue natif. Dans un thread : lire les mots dates d'une heure
        # d'audio et les mettre en forme prend le temps que prend le disque.
        case "export_entry":
            result = await asyncio.to_thread(
                service.export_entry,
                command["id"],
                command.get("format", ""),
                command.get("path", ""),
            )
            return {"type": "export_done", **result}

        case "history_search":
            entries = await asyncio.to_thread(
                service.history.search,
                command.get("query", ""),
                limit=_bounded(command.get("limit"), 100, MAX_SEARCH_LIMIT),
                offset=_bounded(command.get("offset"), 0, MAX_SEARCH_OFFSET),
            )
            stats = await asyncio.to_thread(service.history.stats)
            return {
                "type": "history",
                "entries": entries,
                "stats": stats,
                "query": command.get("query", ""),
            }

        case "history_update":
            await asyncio.to_thread(
                service.history.update_text, command["id"], command.get("text", "")
            )
            return {"type": "history_updated", "id": command["id"]}

        case "history_pin":
            await asyncio.to_thread(
                service.history.set_pinned, command["id"], bool(command.get("pinned"))
            )
            return {"type": "history_updated", "id": command["id"]}

        case "history_delete":
            await asyncio.to_thread(service.history.delete, command["id"])
            stats = await asyncio.to_thread(service.history.stats)
            return {"type": "history_deleted", "id": command["id"], "stats": stats}

        case _:
            return {"type": "error", "message": f"Commande inconnue : {kind}"}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    # AVANT `accept()`. Un refus apres coup laisserait le client croire un
    # instant qu'il est connecte, et surtout laisserait une page web mesurer la
    # difference entre « jeton faux » et « service absent ».
    offered = ws.scope.get("subprotocols") or []
    if not token_accepted(token_from_subprotocols(offered)):
        log.warning("Connexion refusee : jeton absent ou invalide")
        await ws.close(code=1008, reason="Jeton invalide")
        return
    if not origin_accepted(ws.headers.get("origin")):
        log.warning("Connexion refusee : origine %s", ws.headers.get("origin"))
        await ws.close(code=1008, reason="Origine refusee")
        return

    # Le sous-protocole doit etre renvoye tel quel, sinon le navigateur casse la
    # connexion cote client. On rend le nom, jamais le jeton.
    await ws.accept(subprotocol=SUBPROTOCOL if SUBPROTOCOL in offered else None)

    queue = service.subscribe()
    # Deux taches ecrivent sur cette socket — la pompe d'evenements et les
    # reponses aux commandes. Starlette ne garantit pas l'atomicite d'un envoi
    # entre deux coroutines : sous un flux de niveaux audio a 25 Hz, deux trames
    # peuvent s'entrelacer. Le verrou coute une acquisition non contendue.
    send_lock = asyncio.Lock()

    async def send(payload: dict[str, Any]) -> None:
        async with send_lock:
            await ws.send_json(payload)

    async def pump_events() -> None:
        """Relaie les evenements du service vers ce client."""
        try:
            while True:
                await send(await queue.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            # Sans ce filet, une exception inattendue tuait la tache pendant que
            # la boucle de reception continuait : l'interface restait connectee,
            # acceptait les commandes, et ne recevait plus jamais d'evenement.
            log.exception("Pompe d'evenements interrompue")

    pump = asyncio.create_task(pump_events())
    try:
        await send({"type": "snapshot", **await asyncio.to_thread(service.snapshot)})
        while True:
            command = await ws.receive_json()
            try:
                response = await _handle(command)
            except Exception as exc:  # noqa: BLE001
                log.exception("Commande en echec : %s", command)
                response = {
                    "type": "error",
                    "key": "error_command_failed",
                    "params": {"detail": str(exc)},
                    "message": str(exc),
                }
            if response is not None:
                await send(response)
    except WebSocketDisconnect:
        pass
    finally:
        pump.cancel()
        service.unsubscribe(queue)


def run() -> None:
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT, log_level="warning", access_log=False)
