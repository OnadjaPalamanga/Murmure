"""Interface commune aux moteurs de reconnaissance vocale."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import numpy as np

SAMPLE_RATE = 16_000


@dataclass(slots=True)
class Word:
    """Un mot situe dans le temps.

    Denominateur commun aux deux moteurs, qui ne datent pas la meme chose :
    faster-whisper rend un debut ET une fin par mot ; onnx-asr rend un instant
    par *jeton* de sous-mot, sans fin. Les moteurs se chargent de la conversion,
    pour que l'alignement des locuteurs n'ait qu'une seule forme a connaitre.
    """

    start: float
    end: float
    text: str
    # Une espace precedait-elle ce mot dans le texte du moteur ? Les deux
    # moteurs rendent des mots nus, mais l'elision et le trait d'union font que
    # « l' », « -etre » ou « 'application » se collent au voisin. Sans cette
    # information, tout recollage rend « l 'application » et « peut -etre ».
    space_before: bool = True

    def to_dict(self) -> dict:
        """Forme persistee. Arrondi a la milliseconde : en dessous c'est du
        bruit de modele, et une heure d'audio fait dix mille mots — les
        decimales inutiles se paient sur chaque ligne de la base.

        `space_before` n'est ecrit que lorsqu'il est faux : c'est le cas rare,
        et l'omission garde la base lisible autant que compacte.
        """
        item = {"start": round(self.start, 3), "end": round(self.end, 3), "text": self.text}
        if not self.space_before:
            item["space_before"] = False
        return item


def join_words(words) -> str:
    """Recolle des mots en respectant l'espacement d'origine.

    Une espace systematique donnerait « l 'application » et « peut -etre » : en
    francais l'apostrophe et le trait d'union appartiennent au mot suivant, et
    c'est le moteur — pas nous — qui sait lesquels. Une heuristique sur la
    ponctuation marcherait sur ces deux cas et echouerait sur le troisieme.

    Accepte indifferemment des `Word` et des dictionnaires relus de la base :
    la regle est la meme, et elle ne doit exister qu'a un seul endroit.
    """
    parts: list[str] = []
    for word in words:
        if isinstance(word, dict):
            text, spaced = word.get("text", ""), word.get("space_before", True)
        else:
            text, spaced = word.text, word.space_before
        if parts and spaced:
            parts.append(" ")
        parts.append(text)
    return "".join(parts)


@dataclass(slots=True)
class Transcript:
    """Resultat d'une transcription."""

    text: str
    language: str | None = None
    audio_seconds: float = 0.0
    latency_ms: int = 0
    device: str = "cpu"
    extra: dict = field(default_factory=dict)
    # Rempli seulement si `timestamps=True` a ete demande. Dater les mots coute
    # du calcul et la dictee n'en a aucun usage : elle ne le demande jamais.
    words: list[Word] = field(default_factory=list)

    @property
    def realtime_factor(self) -> float:
        """Combien de secondes d'audio traitees par seconde de calcul."""
        if self.latency_ms <= 0:
            return 0.0
        return self.audio_seconds / (self.latency_ms / 1000.0)


@runtime_checkable
class Engine(Protocol):
    """Un moteur charge un modele une fois, puis transcrit a la demande.

    `load()` est deliberatement separe de `__init__` : le service instancie tous
    les moteurs declares au demarrage mais ne charge en VRAM que celui qui sert.
    """

    model_id: str
    is_loaded: bool
    device: str

    def load(self) -> None: ...

    def unload(self) -> None: ...

    def warmup(self) -> None:
        """Force l'allocation des noyaux de calcul, pour que la 1re dictee soit rapide.

        Fait partie du contrat : `Service.ensure_engine` l'appelle sur tout
        moteur qu'il vient de charger. Un moteur qui ne l'implemente pas ferait
        echouer le chargement, pas la seule optimisation.
        """
        ...

    def transcribe(
        self, audio: np.ndarray, language: str | None = None, *, timestamps: bool = False
    ) -> Transcript:
        """Transcrit du PCM mono float32 a SAMPLE_RATE, normalise dans [-1, 1].

        `timestamps=True` remplit `Transcript.words`. Reserve a la diarisation :
        dater les mots ralentit le decodage, et la dictee n'en fait rien.
        """
        ...
