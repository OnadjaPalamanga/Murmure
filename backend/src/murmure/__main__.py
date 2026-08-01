"""Point d'entree du service Murmure."""

from __future__ import annotations

import logging
import sys

from .paths import LOG_FILE, ensure_dirs


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

    from .server import HOST, PORT, run

    logging.getLogger("murmure").info("Service demarre sur http://%s:%s", HOST, PORT)
    try:
        run()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
