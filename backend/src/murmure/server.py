"""Transport WebSocket + HTTP local. Aucune logique metier ici (voir service.py).

Le service n'ecoute que sur 127.0.0.1 : il n'est joignable que depuis la machine.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from .service import Service

log = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 8756

service = Service()


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN001, ARG001
    service.bind_loop(asyncio.get_running_loop())
    service.preload()
    yield
    service.shutdown()


app = FastAPI(title="Murmure", lifespan=lifespan)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"ok": True, "state": service.state, **service.engine_status()})


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

        case "history_search":
            entries = await asyncio.to_thread(
                service.history.search,
                command.get("query", ""),
                limit=int(command.get("limit", 100)),
                offset=int(command.get("offset", 0)),
            )
            return {"type": "history", "entries": entries, "query": command.get("query", "")}

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
            return {"type": "history_deleted", "id": command["id"]}

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
