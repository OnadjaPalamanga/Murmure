"""Jeton de session : ce qui distingue l'application d'une page web quelconque.

Le service n'ecoute que sur `127.0.0.1`, ce qui le met hors de portee du reseau
— mais **pas hors de portee d'un navigateur**. La politique d'origine unique ne
s'applique pas aux WebSocket : n'importe quelle page ouverte dans Chrome peut
ecrire `new WebSocket("ws://127.0.0.1:8756/ws")` et se retrouver avec les memes
droits que l'application. Sans le controle qui suit, cela suffisait a lire tout
l'historique de dictee, a declencher le micro, et — en enchainant
`history_update` puis `export_entry` — a ecrire un fichier dans le dossier
Demarrage. C'est le piege classique du « ca n'ecoute que sur localhost ».

Deux verrous, qui ne protegent pas de la meme chose :

  * le **jeton**, tire au demarrage du service et depose dans un fichier que
    seule la session Windows de l'utilisateur peut lire. Une page web n'a aucun
    moyen d'atteindre le disque : c'est lui qui ferme la porte.
  * l'**origine**, verifiee quand l'en-tete est present. Un navigateur envoie
    toujours `Origin` ; l'application Tauri annonce `http://tauri.localhost`.
    Une page qui aurait le jeton par un autre biais echoue quand meme ici.

Un client sans navigateur — les scripts de `scripts/` — n'envoie pas d'origine
et s'authentifie par le seul jeton. C'est voulu : il tourne sous la meme session
que l'utilisateur, donc il peut lire le fichier, donc il en a le droit.
"""

from __future__ import annotations

import logging
import os
import secrets
import stat

from .paths import TOKEN_FILE

log = logging.getLogger(__name__)

# Sous-protocole annonce par l'application. Le jeton voyage a cote, dans la même
# liste : l'API WebSocket des navigateurs ne permet pas d'ajouter un en-tete, et
# la chaine de requete finirait recopiee dans les journaux d'acces.
SUBPROTOCOL = "murmure.v1"
TOKEN_PREFIX = "murmure.token."

# En-tete equivalent pour les routes HTTP appelees hors navigateur (`/shutdown`,
# poste par le cote Rust).
TOKEN_HEADER = "x-murmure-token"

# Origines acceptees quand l'en-tete est present. Tauri v2 sert les fichiers
# locaux depuis `tauri.localhost` sous Windows, `tauri://localhost` ailleurs.
ALLOWED_ORIGINS = frozenset(
    {
        "http://tauri.localhost",
        "https://tauri.localhost",
        "tauri://localhost",
    }
)

_token: str | None = None


def issue_token() -> str:
    """Tire un jeton neuf et l'ecrit. Appele une fois, au demarrage du service.

    Neuf a chaque demarrage : un jeton qui survivrait au processus resterait
    valable pour tout ce qui l'aurait lu une fois, longtemps apres.
    """
    global _token
    _token = secrets.token_urlsafe(32)

    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    # `os.open` avec O_CREAT et 0o600 plutot qu'un `write_text` suivi d'un
    # `chmod` : entre les deux, le fichier existerait un instant avec les
    # permissions par defaut. Sous Windows le mode est largement ignore — c'est
    # l'appartenance du dossier `%APPDATA%` qui protege reellement — mais le
    # service tourne aussi sous WSL et sur les machines de developpement.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    descriptor = os.open(TOKEN_FILE, flags, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(_token)
    return _token


def current_token() -> str | None:
    """Le jeton de ce service. `None` tant que `issue_token()` n'a pas tourne."""
    return _token


def revoke_token() -> None:
    """Retire le fichier a l'arret : un jeton qui traine ne vaut rien de bon."""
    global _token
    _token = None
    try:
        TOKEN_FILE.unlink(missing_ok=True)
    except OSError:
        log.debug("Jeton de session non efface", exc_info=True)


def token_accepted(candidate: str | None) -> bool:
    """Le jeton presente est-il celui de ce service ?

    `compare_digest` et non `==` : la comparaison naive s'arrete au premier
    caractere different, et cette duree se mesure.
    """
    expected = _token
    if not expected or not candidate:
        return False
    return secrets.compare_digest(candidate, expected)


def origin_accepted(origin: str | None) -> bool:
    """Une origine absente passe (client hors navigateur) ; une origine
    inconnue est refusee, meme avec un jeton valide."""
    if origin is None:
        return True
    return origin.rstrip("/").lower() in ALLOWED_ORIGINS


def token_from_subprotocols(offered: list[str] | None) -> str | None:
    """Extrait le jeton de la liste de sous-protocoles annoncee au handshake."""
    for item in offered or []:
        name = item.strip()
        if name.startswith(TOKEN_PREFIX):
            return name[len(TOKEN_PREFIX) :]
    return None
