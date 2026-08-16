"""Recollement des deux moitiés d'une transcription diarisée.

La diarisation dit QUI parle QUAND, la reconnaissance dit QUOI est dit QUAND.
Ni l'une ni l'autre ne sait ce que fait l'autre : ce module les recoud, et c'est
le seul endroit ou la correspondance se decide.

Fonctions pures, sans modele ni etat — c'est ce qui les rend verifiables, et
c'est voulu : l'attribution est exactement le genre de calcul qui se degrade
sans bruit. Un mot mis au mauvais locuteur ne leve aucune erreur, il fait juste
dire a quelqu'un ce qu'il n'a pas dit.

La regle est le RECOUVREMENT MAXIMAL : un mot revient au locuteur avec lequel il
partage le plus de temps. Pas au locuteur qui parle a son instant de debut —
sur une prise de parole qui se chevauche, le debut appartient encore au
precedent alors que le mot entier appartient au suivant.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .engines.base import Word, join_words

log = logging.getLogger(__name__)

# Mot qui ne recouvre aucun tour de parole : il est rattache au tour le plus
# proche si celui-ci est a portee, sinon laisse sans locuteur. Les frontieres de
# la segmentation sont approximatives, un mot peut deborder de quelques
# dixiemes ; au-dela c'est du silence ou de la musique, et inventer un locuteur
# serait pire que ne rien dire.
NEAREST_TOLERANCE_S = 2.0


@dataclass(slots=True)
class SpeakerSegment:
    """Un tour de parole, tel que rendu par la diarisation."""

    start: float
    end: float
    speaker: int


@dataclass(slots=True)
class SpeakerBlock:
    """Un bloc de texte attribue a un locuteur, pret a l'affichage."""

    speaker: int | None
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "speaker": self.speaker,
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "text": self.text,
        }


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """Duree commune a deux intervalles. Zero s'ils ne se touchent pas."""
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def speaker_for(word: Word, segments: list[SpeakerSegment]) -> int | None:
    """Locuteur qui recouvre le plus ce mot, sinon le plus proche a portee."""
    if not segments:
        return None

    best, best_overlap = None, 0.0
    for segment in segments:
        shared = _overlap(word.start, word.end, segment.start, segment.end)
        if shared > best_overlap:
            best, best_overlap = segment.speaker, shared

    if best is not None:
        return best

    # Aucun recouvrement : un mot tombe entre deux tours, ou deborde du dernier.
    # On prend le tour le plus proche, a condition qu'il le soit vraiment.
    nearest, distance = None, None
    for segment in segments:
        gap = max(segment.start - word.end, word.start - segment.end, 0.0)
        if distance is None or gap < distance:
            nearest, distance = segment.speaker, gap
    return nearest if distance is not None and distance <= NEAREST_TOLERANCE_S else None


def assign_speakers(words: list[Word], segments: list[SpeakerSegment]) -> list[SpeakerBlock]:
    """Attribue chaque mot a un locuteur, puis regroupe les mots consecutifs.

    Le regroupement se fait sur les mots ADJACENTS, pas sur le locuteur en
    general : deux passages du meme locuteur separes par quelqu'un d'autre
    restent deux blocs. Fusionner tout ce qu'une personne a dit rendrait le
    dialogue illisible et fausserait l'ordre de la conversation.
    """
    if not words:
        return []

    blocks: list[SpeakerBlock] = []
    for word in words:
        speaker = speaker_for(word, segments)
        if blocks and blocks[-1].speaker == speaker:
            block = blocks[-1]
            block.end = max(block.end, word.end)
            block.words.append(word)
        else:
            blocks.append(
                SpeakerBlock(
                    speaker=speaker,
                    start=word.start,
                    end=word.end,
                    text="",
                    words=[word],
                )
            )

    # Le texte est recolle a la fin, en respectant l'espacement du moteur : une
    # espace entre chaque mot rendrait « l 'application » et « peut -etre ».
    for block in blocks:
        block.text = join_words(block.words)
    return blocks


def dated_words(blocks: list[SpeakerBlock]) -> list[dict]:
    """Mots dates portant chacun son locuteur, prets a etre persistes.

    L'attribution est figee ICI, ou elle vient d'etre calculee. L'export n'a
    donc jamais a rejouer la regle du recouvrement maximal : dupliquee la-bas,
    elle finirait par diverger de celle-ci sans que rien ne le signale.
    """
    return [
        {**word.to_dict(), "speaker": block.speaker}
        for block in blocks
        for word in block.words
    ]


def speaker_label(speaker: int | None, *, name: str = "Locuteur") -> str:
    """« Locuteur 1 », « Locuteur 2 »… Numerote a partir de 1 pour l'affichage.

    sherpa-onnx numerote a partir de 0, ce qui n'a de sens que pour une machine.

    `name` existe pour la ligne de commande, dont les sorties sont lues par des
    outils de montage et non par l'interface : elle prefixe « Speaker ».
    """
    return f"{name} ?" if speaker is None else f"{name} {speaker + 1}"


def format_transcript(blocks: list[SpeakerBlock], *, speaker_name: str = "Locuteur") -> str:
    """Rend le dialogue en texte, un bloc par ligne, prefixe du locuteur.

    C'est cette chaine qui part dans l'historique et dans le presse-papier :
    elle doit rester lisible telle quelle, sans que l'interface ait a la
    reconstruire.
    """
    return "\n".join(
        f"{speaker_label(b.speaker, name=speaker_name)} : {b.text}"
        for b in blocks
        if b.text.strip()
    )


def count_speakers(blocks: list[SpeakerBlock]) -> int:
    """Nombre de locuteurs distincts reellement attribues."""
    return len({b.speaker for b in blocks if b.speaker is not None})
