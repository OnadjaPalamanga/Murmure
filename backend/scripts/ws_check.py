"""Verifie le protocole WebSocket de bout en bout, capture micro comprise.

Enregistre reellement pendant quelques secondes : sans parler, on doit recevoir
`empty` ; en parlant, un `final` avec le texte.

    python scripts/ws_check.py [secondes]
"""

from __future__ import annotations

import asyncio
import json
import sys

import websockets

URL = "ws://127.0.0.1:8756/ws"


async def main(record_s: float) -> int:
    async with websockets.connect(URL) as ws:
        snapshot = json.loads(await ws.recv())
        assert snapshot["type"] == "snapshot", snapshot
        print(f"instantane      : etat={snapshot['state']} moteur={snapshot['engine']}")
        print(f"modeles         : {[m['id'] for m in snapshot['models']]}")
        print(f"peripheriques   : {len(snapshot['devices'])} entree(s)")
        print(f"reglages        : raccourci={snapshot['settings']['hotkey']}")

        # Attend que le modele soit resident, sinon la 1re dictee paie le chargement.
        while not snapshot["engine"]["loaded"]:
            event = json.loads(await ws.recv())
            if event["type"] in ("model_ready", "snapshot"):
                await ws.send(json.dumps({"type": "get_state"}))
                snapshot = json.loads(await ws.recv())
            elif event["type"] == "error":
                print("ERREUR :", event["message"])
                return 1
        print(f"modele resident : {snapshot['engine']['device']}\n")

        print(f"--- enregistrement {record_s:.0f} s (parle maintenant) ---")
        await ws.send(json.dumps({"type": "start"}))

        levels = 0
        deadline = asyncio.get_running_loop().time() + record_s
        while asyncio.get_running_loop().time() < deadline:
            try:
                remaining = deadline - asyncio.get_running_loop().time()
                event = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
            except TimeoutError:
                break
            if event["type"] == "level":
                levels += 1

        print(f"niveaux recus   : {levels} (la forme d'onde est alimentee)")
        await ws.send(json.dumps({"type": "stop"}))

        while True:
            event = json.loads(await asyncio.wait_for(ws.recv(), timeout=90))
            kind = event["type"]
            if kind == "state":
                print(f"etat            : {event['state']}")
            elif kind == "final":
                print(f"\nlatence         : {event['latency_ms']} ms")
                print(f"temps reel      : {event['realtime_factor']}x sur {event['device']}")
                print(f"texte           : {event['entry']['text']}")
                break
            elif kind == "empty":
                print(f"\nvide            : {event['reason']}")
                print("                  (silence detecte, comportement normal)")
                break
            elif kind == "error":
                print(f"\nERREUR          : {event['message']}")
                return 1

        # L'entree doit etre retrouvable dans l'historique.
        await ws.send(json.dumps({"type": "history_search", "query": "", "limit": 3}))
        while True:
            event = json.loads(await ws.recv())
            if event["type"] == "history":
                print(f"historique      : {len(event['entries'])} entree(s)")
                break

    return 0


if __name__ == "__main__":
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 4.0
    raise SystemExit(asyncio.run(main(seconds)))
