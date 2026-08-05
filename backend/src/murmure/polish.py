"""Garde-fous du texte poli : recollement des fenetres, et quand refuser.

Le polissage est un RE-DECODAGE. Une fenetre de plusieurs phrases repart au
moteur d'un seul bloc, et c'est le contexte ainsi retrouve — pas un modele de
plus — qui remet la ponctuation, les majuscules et supprime les reprises. La
sortie reste donc de la reconnaissance vocale : elle ne reformule pas.

Reste deux choses qu'un re-decodage peut rater, et dont ce module s'occupe :

  * il peut DERAILLER — boucle de repetition sur une fenetre longue, ou sortie
    vide sur un passage difficile. `choose()` compare au texte phrase par phrase
    deja obtenu et garde ce dernier si le polissage a trop derive. On ne remplace
    du texte correct que par du texte plausible.
  * il peut RECOUVRIR la fenetre precedente de quelques mots, les marges de fin
    et d'attaque des phrases etant conservees. `merge_seam()` retire ce
    chevauchement.

Fonctions pures, sans etat ni modele : c'est ce qui les rend verifiables.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Au-dela, ce n'est plus une couture : deux fenetres qui partagent huit mots
# signalent un probleme de decoupage, pas une marge conservee.
MAX_SEAM_WORDS = 8

# Bornes de vraisemblance du polissage, en nombre de mots rapporte au texte
# brut. Larges a dessein : un re-decodage contextuel retire legitimement des
# reprises ("c'est... c'est une vision") et retrouve des mots que les phrases
# isolees avaient manques. Ce qu'on veut attraper, c'est la boucle de
# repetition et la fenetre rendue vide, pas une difference de quelques mots.
MIN_RATIO = 0.6
MAX_RATIO = 1.6


def _key(token: str) -> str:
    """Forme comparable d'un mot : la ponctuation et la casse sont justement ce
    que le polissage a le droit de changer, elles ne peuvent pas servir de cle."""
    return "".join(c for c in token.lower() if c.isalnum())


def words(text: str) -> list[str]:
    """Suite des mots d'un texte, ponctuation et casse retirees."""
    return [k for k in (_key(t) for t in text.split()) if k]


def merge_seam(previous: str, new: str, max_overlap: int = MAX_SEAM_WORDS) -> str:
    """Retire de `new` les mots qui repetent la fin de `previous`.

    Cherche le plus long chevauchement, borne a `max_overlap` mots. On compare
    sur les mots nus : la fenetre precedente peut avoir ecrit « ... vision. » la
    ou la nouvelle commence par « Vision ».
    """
    tail_tokens, head_tokens = previous.split(), new.split()
    if not tail_tokens or not head_tokens:
        return new

    limit = min(max_overlap, len(tail_tokens), len(head_tokens))
    for size in range(limit, 0, -1):
        tail = [_key(t) for t in tail_tokens[-size:]]
        head = [_key(t) for t in head_tokens[:size]]
        # `all(tail)` : une suite de jetons purement typographiques se
        # comparerait egale a n'importe quoi et mangerait le debut de la fenetre.
        if all(tail) and tail == head:
            log.debug("Couture : %d mot(s) en double retires", size)
            return " ".join(head_tokens[size:])
    return new


def choose(polished: str, raw: str) -> str:
    """Retient le texte poli s'il est plausible, sinon le texte brut.

    Le brut est ce que l'utilisateur a deja vu tomber phrase par phrase : c'est
    un repli sur, jamais une perte.
    """
    kept, source = len(words(polished)), len(words(raw))
    if source == 0:
        return polished if kept else raw
    if kept == 0:
        log.info("Polissage vide sur %d mot(s) : on garde le texte brut", source)
        return raw

    ratio = kept / source
    if not MIN_RATIO <= ratio <= MAX_RATIO:
        log.info(
            "Polissage ecarte : %d mot(s) pour %d attendus (%.2f×)", kept, source, ratio
        )
        return raw
    return polished
