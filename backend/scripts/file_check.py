"""Verifie l'import de fichiers de bout en bout via le service.

    python scripts/file_check.py <fichier> [fichier ...]
"""

from __future__ import annotations

import asyncio
import json
import sys

import websockets

URL = "ws://127.0.0.1:8756/ws"


async def main(paths: list[str]) -> int:
    async with websockets.connect(URL, max_size=8 * 1024 * 1024) as ws:
        snapshot = json.loads(await ws.recv())
        print(f"ffmpeg detecte  : {snapshot['has_ffmpeg']}")
        print(f"formats audio   : {', '.join(snapshot['media_extensions']['audio'])}")
        print(f"formats video   : {', '.join(snapshot['media_extensions']['video'])}")
        print(f"langue reglee   : {snapshot['settings']['language']}")
        print(f"raccourci       : {snapshot['settings']['hotkey']}\n")

        await ws.send(json.dumps({"type": "transcribe_files", "paths": paths}))

        seen_download = 0
        while True:
            event = json.loads(await asyncio.wait_for(ws.recv(), timeout=900))
            kind = event["type"]

            if kind == "model_stage" and event["stage"] == "downloading":
                seen_download = event["downloaded_bytes"]
            elif kind == "model_stage":
                print(f"  [etape] {event['message']}")
            elif kind == "file_progress":
                print(f"  [{event['index']}/{event['total']}] {event['message']}")
            elif kind == "file_done":
                if event["ok"]:
                    text = event["entry"]["text"]
                    print(f"  -> {event['latency_ms']} ms, {event['realtime_factor']}x")
                    print(f"  -> {text[:300]}\n")
                else:
                    print(f"  -> ECHEC : {event['message']}\n")
            elif kind == "files_finished":
                if seen_download:
                    print(f"(telechargement observe : {seen_download // 1024 // 1024} Mo)")
                print(f"termine : {event['total']} fichier(s)")
                return 0
            elif kind == "error":
                print(f"ERREUR : {event['message']}")
                return 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
