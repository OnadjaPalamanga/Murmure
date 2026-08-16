"""Point d'entree de Murmure.

Sans argument, c'est le service : `python -m murmure` est ce que lance
l'application Tauri, et ce sur quoi pointe `install.ps1`. Tout le reste est la
ligne de commande, decrite dans `cli.py`.
"""

from __future__ import annotations

import sys


def main() -> int:
    from .cli import run

    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
