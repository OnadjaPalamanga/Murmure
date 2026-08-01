"""Point d'entree du service Murmure."""

from __future__ import annotations

import logging
import sys

from .paths import LOG_FILE, ensure_dirs


def _ignore_client_disconnect(record: logging.LogRecord) -> bool:
    """False pour les fermetures de connexion ordinaires, qui ne sont pas des erreurs."""
    text = record.getMessage()
    if "_call_connection_lost" in text:
        return False
    return not (record.exc_info and isinstance(record.exc_info[1], ConnectionResetError))


def main() -> int:
    ensure_dirs()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
    )
    # Ces deux-la sont extremement bavards au niveau INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub").setLevel(logging.WARNING)

    # Un client WebSocket qui se ferme fait remonter une ConnectionResetError
    # depuis le transport Proactor de Windows. C'est normal et sans consequence,
    # mais ca remplit le journal d'ERROR alarmants.
    logging.getLogger("asyncio").addFilter(_ignore_client_disconnect)

    from .server import HOST, PORT, run

    logging.getLogger("murmure").info("Service demarre sur http://%s:%s", HOST, PORT)
    try:
        run()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
