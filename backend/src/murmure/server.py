"""Transport WebSocket + HTTP local. Aucune logique metier ici (voir service.py).

Le service n'ecoute que sur 127.0.0.1 : il n'est joignable que depuis la machine.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from . import __version__
from .service import Service

log = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 8756

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
SETTINGS_REVISION = 4

service = Service()

# Taches d'arret en vol. Voir `/shutdown` : sans cette reference forte, la
# boucle asyncio peut ramasser la tache avant qu'elle n'ait fait son travail.
_pending_shutdown: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN001, ARG001
    service.bind_loop(asyncio.get_running_loop())
    service.preload()
    yield
    service.shutdown()


app = FastAPI(title="Murmure", lifespan=lifespan)


@app.get("/health")
async def health() -> JSONResponse:
    """Etat du service, et surtout de QUELLE version il est.

    L'application compare `settings_revision` a la sienne avant de piloter ce
    service : sans ca, un backend perime repond normalement a tout et l'ecart
    ne se voit que sur des reglages inexplicablement vides.
    """
    return JSONResponse(
        {
            "ok": True,
            "state": service.state,
            "version": __version__,
            "settings_revision": SETTINGS_REVISION,
            **service.engine_status(),
        }
    )


@app.post("/shutdown")
async def shutdown() -> JSONResponse:
    """Arrete le service. Reserve au remplacement d'une version perimee.

    L'application appelle ceci quand elle trouve sur le port un service d'une
    autre revision : sans quoi il faudrait deviner son PID, et le service lance
    par `run.ps1` n'est pas un enfant de l'application. Le service n'ecoute que
    sur 127.0.0.1 — cette route n'est joignable que depuis la machine.
    """
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
async def history_audio(entry_id: str) -> FileResponse:
    """Diffuse uniquement le fichier rattache a une entree connue de l'historique."""
    entry = await asyncio.to_thread(service.history.get, entry_id)
    path = Path(entry["audio_path"]) if entry and entry.get("audio_path") else None
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Audio introuvable")
    return FileResponse(path)


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

        case "history_search":
            entries = await asyncio.to_thread(
                service.history.search,
                command.get("query", ""),
                limit=int(command.get("limit", 100)),
                offset=int(command.get("offset", 0)),
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
    await ws.accept()
    queue = service.subscribe()

    async def pump_events() -> None:
        """Relaie les evenements du service vers ce client."""
        try:
            while True:
                event = await queue.get()
                await ws.send_json(event)
        except (WebSocketDisconnect, RuntimeError):
            pass

    pump = asyncio.create_task(pump_events())
    try:
        await ws.send_json({"type": "snapshot", **await asyncio.to_thread(service.snapshot)})
        while True:
            command = await ws.receive_json()
            try:
                response = await _handle(command)
            except Exception as exc:  # noqa: BLE001
                log.exception("Commande en echec : %s", command)
                response = {"type": "error", "message": str(exc)}
            if response is not None:
                await ws.send_json(response)
    except WebSocketDisconnect:
        pass
    finally:
        pump.cancel()
        service.unsubscribe(queue)


def run() -> None:
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT, log_level="warning", access_log=False)
