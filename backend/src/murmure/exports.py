"""Mise en forme d'une transcription datee : sous-titres, JSON, texte.

Fonctions pures, sans fichier ni base : on leur donne des mots dates et des
tours de parole, elles rendent une chaine. C'est ce qui les rend verifiables
sans modele ni GPU, et l'export est exactement le genre de code qui se degrade
sans bruit — un sous-titre decale d'une seconde reste un sous-titre valide.

Le format pivot est le MOT date. Un tour de parole ne suffit pas : sur une
minute de monologue il fait un seul bloc, et un sous-titre d'une minute est
illisible. Les mots permettent de recouper au bon endroit — a la pause, avant
la limite de longueur — ce qu'aucun regroupement anterieur ne peut rattraper.

Quand les mots manquent (entree d'historique anterieure, datation desactivee),
on retombe sur les tours de parole : moins fin, mais un fichier utilisable
plutot qu'un echec.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .engines.base import join_words

# Regles de lisibilite d'un sous-titre. Ce ne sont pas des gouts : en dessous
# de ~20 caracteres par seconde on ne suit plus, et deux lignes de 42 est la
# convention que respectent les lecteurs et les logiciels de montage.
MAX_CHARS_PER_LINE = 42
MAX_LINES = 2
MAX_CHARS = MAX_CHARS_PER_LINE * MAX_LINES
MAX_SECONDS = 6.0

# Silence a partir duquel on coupe meme si la place reste. Une respiration est
# une frontiere naturelle : couper ailleurs donne un sous-titre qui commence au
# milieu d'une idee.
MAX_GAP = 0.7

# Duree plancher d'un sous-titre. Un mot isole dure parfois 80 ms, ce qui donne
# un sous-titre qui clignote sans etre lisible.
MIN_DURATION = 0.4

FORMATS = ("srt", "vtt", "json", "txt")

# Formats qui n'ont aucun sens sans datation : ils sont faits de reperes
# temporels. `json` et `txt` restent produits meme sans, avec ce qu'on a.
TIMED_FORMATS = ("srt", "vtt")


@dataclass(slots=True)
class Cue:
    """Un sous-titre : un intervalle, un texte, un locuteur eventuel."""

    start: float
    end: float
    text: str
    speaker: int | None = None
    words: list[dict] = field(default_factory=list)


def format_timestamp(seconds: float, *, sep: str = ",") -> str:
    """`HH:MM:SS,mmm` pour SRT, `HH:MM:SS.mmm` pour WebVTT.

    Les deux formats ne different que par ce separateur, et se tromper de
    virgule fait rejeter le fichier entier par certains lecteurs.
    """
    total_ms = max(0, round(float(seconds) * 1000))
    hours, total_ms = divmod(total_ms, 3_600_000)
    minutes, total_ms = divmod(total_ms, 60_000)
    secs, millis = divmod(total_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{millis:03d}"


def wrap(text: str, *, width: int = MAX_CHARS_PER_LINE, max_lines: int = MAX_LINES) -> list[str]:
    """Repartit un texte sur au plus `max_lines` lignes, sans couper un mot.

    Le decoupage en cues vise deja `width * max_lines` caracteres, donc le
    depassement est rare ; quand il arrive (un mot plus long qu'une ligne, une
    URL dictee), on prefere une ligne trop longue a un mot tronque.
    """
    words = text.split()
    if not words:
        return []

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)

    if len(lines) <= max_lines:
        return lines
    # Trop de lignes : on garde les premieres et on recolle le reste sur la
    # derniere. Perdre du texte serait pire que depasser la hauteur.
    head = lines[: max_lines - 1]
    return [*head, " ".join(lines[max_lines - 1 :])]


def _speaker_of(item: dict) -> int | None:
    speaker = item.get("speaker")
    return None if speaker is None else int(speaker)


def build_cues(
    words: list[dict],
    *,
    max_chars: int = MAX_CHARS,
    max_seconds: float = MAX_SECONDS,
    max_gap: float = MAX_GAP,
) -> list[Cue]:
    """Regroupe des mots dates en sous-titres lisibles.

    Quatre raisons de fermer un sous-titre, dans cet ordre de priorite :
    le locuteur change, un silence depasse `max_gap`, la duree depasse
    `max_seconds`, la longueur depasse `max_chars`. Le changement de locuteur
    est le seul qui soit imperatif — melanger deux voix dans un sous-titre fait
    dire a quelqu'un ce qu'il n'a pas dit.
    """
    cues: list[Cue] = []
    for word in words:
        text = str(word.get("text", "")).strip()
        if not text:
            continue
        start = float(word.get("start", 0.0))
        end = float(word.get("end", start))
        speaker = _speaker_of(word)

        current = cues[-1] if cues else None
        if current is not None:
            # Recolle avec l'espacement du moteur, pas une espace systematique :
            # « l' » et « -etre » se collent au voisin, et la longueur mesuree
            # doit etre celle qui sera affichee.
            candidate = join_words([*current.words, word])
            if (
                current.speaker == speaker
                and start - current.end <= max_gap
                and len(candidate) <= max_chars
                and end - current.start <= max_seconds
            ):
                current.end = max(current.end, end)
                current.text = candidate
                current.words.append(word)
                continue

        cues.append(Cue(start=start, end=end, text=text, speaker=speaker, words=[word]))

    return _enforce_min_duration(cues)


def cues_from_turns(turns: list[dict]) -> list[Cue]:
    """Repli sans mots dates : un sous-titre par tour de parole.

    Grossier — un tour peut durer une minute — mais c'est ce qui permet a une
    entree enregistree avant la datation de sortir quand meme en SRT.
    """
    cues = [
        Cue(
            start=float(turn.get("start", 0.0)),
            end=float(turn.get("end", 0.0)),
            text=str(turn.get("text", "")).strip(),
            speaker=_speaker_of(turn),
        )
        for turn in turns
        if str(turn.get("text", "")).strip()
    ]
    return _enforce_min_duration(cues)


def _enforce_min_duration(cues: list[Cue]) -> list[Cue]:
    """Allonge les sous-titres trop brefs, sans jamais en faire chevaucher deux.

    Un mot isole dure parfois 80 ms. Affiche tel quel il clignote sans etre
    lisible ; allonge sans borne il mordrait sur le suivant et deux sous-titres
    s'afficheraient ensemble.
    """
    for index, cue in enumerate(cues):
        if cue.end - cue.start >= MIN_DURATION:
            continue
        wanted = cue.start + MIN_DURATION
        ceiling = cues[index + 1].start if index + 1 < len(cues) else wanted
        cue.end = max(cue.end, min(wanted, ceiling))
    return cues


def cue_label(cue: Cue, *, speaker_name: str = "Locuteur") -> str:
    """Texte du sous-titre, prefixe du locuteur s'il y en a plusieurs."""
    if cue.speaker is None:
        return cue.text
    return f"{speaker_name} {cue.speaker + 1}: {cue.text}"


def _prepare(cues: list[Cue], *, with_speakers: bool, speaker_name: str) -> list[tuple[Cue, str]]:
    prepared = []
    for cue in cues:
        text = cue_label(cue, speaker_name=speaker_name) if with_speakers else cue.text
        text = "\n".join(wrap(text))
        if text:
            prepared.append((cue, text))
    return prepared


def to_srt(cues: list[Cue], *, with_speakers: bool = True, speaker_name: str = "Locuteur") -> str:
    """SubRip. Le format que tout logiciel de montage sait importer.

    Numerotation a partir de 1, virgule decimale, une ligne vide entre chaque
    bloc et un saut de ligne final : les lecteurs stricts refusent le reste.
    """
    parts = []
    prepared = _prepare(cues, with_speakers=with_speakers, speaker_name=speaker_name)
    for index, (cue, text) in enumerate(prepared, 1):
        start = format_timestamp(cue.start, sep=",")
        end = format_timestamp(cue.end, sep=",")
        parts.append(f"{index}\n{start} --> {end}\n{text}\n")
    return "\n".join(parts)


def to_vtt(cues: list[Cue], *, with_speakers: bool = True, speaker_name: str = "Locuteur") -> str:
    """WebVTT. Meme decoupage que SRT, en-tete obligatoire et point decimal."""
    parts = ["WEBVTT\n"]
    for cue, text in _prepare(cues, with_speakers=with_speakers, speaker_name=speaker_name):
        start = format_timestamp(cue.start, sep=".")
        end = format_timestamp(cue.end, sep=".")
        parts.append(f"{start} --> {end}\n{text}\n")
    return "\n".join(parts)


def to_txt(cues: list[Cue], *, with_speakers: bool = True, speaker_name: str = "Locuteur") -> str:
    """Texte brut horodate, une ligne par sous-titre. Lisible sans outil."""
    lines = []
    for cue, _ in _prepare(cues, with_speakers=with_speakers, speaker_name=speaker_name):
        stamp = format_timestamp(cue.start, sep=".")[:-4]  # HH:MM:SS
        label = cue_label(cue, speaker_name=speaker_name) if with_speakers else cue.text
        lines.append(f"[{stamp}] {label}")
    return "\n".join(lines) + ("\n" if lines else "")


def to_json(
    *,
    words: list[dict],
    turns: list[dict],
    cues: list[Cue],
    meta: dict | None = None,
) -> str:
    """Sortie structuree, pensee pour le montage automatique.

    Les trois granularites sont livrees ensemble parce qu'elles ne servent pas
    a la meme chose : `words` pour couper au mot pres (retirer une hesitation,
    resserrer un silence), `turns` pour savoir qui parle, `cues` pour reprendre
    exactement le decoupage des sous-titres exportes a cote.
    """
    payload = {
        "murmure": meta or {},
        "words": [
            {
                "start": round(float(w.get("start", 0.0)), 3),
                "end": round(float(w.get("end", 0.0)), 3),
                "text": str(w.get("text", "")),
                **({"speaker": _speaker_of(w)} if w.get("speaker") is not None else {}),
                # Necessaire pour reconstruire le texte exact : sans lui, un
                # montage scripte recolle « l 'application ».
                **({} if w.get("space_before", True) else {"space_before": False}),
            }
            for w in words
        ],
        "turns": turns,
        "cues": [
            {
                "start": round(cue.start, 3),
                "end": round(cue.end, 3),
                "text": cue.text,
                "speaker": cue.speaker,
            }
            for cue in cues
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def render(
    fmt: str,
    *,
    words: list[dict] | None = None,
    turns: list[dict] | None = None,
    text: str = "",
    meta: dict | None = None,
    speaker_name: str = "Locuteur",
) -> str:
    """Produit le contenu du fichier demande, en choisissant la meilleure source.

    Les mots dates priment sur les tours de parole, qui priment sur le texte
    brut : on descend d'un cran a chaque fois que la source la plus fine
    manque, plutot que d'echouer.
    """
    fmt = fmt.lower().strip()
    if fmt not in FORMATS:
        raise ValueError(f"Format inconnu : {fmt}")

    words = words or []
    turns = turns or []
    cues = build_cues(words) if words else cues_from_turns(turns)
    # Prefixer « Locuteur 1 » sur un enregistrement a une voix n'apporte rien
    # et encombre chaque sous-titre.
    with_speakers = any(cue.speaker is not None for cue in cues) and (
        len({cue.speaker for cue in cues}) > 1
    )

    if fmt == "json":
        return to_json(words=words, turns=turns, cues=cues, meta=meta)
    if not cues:
        # Ni mots ni tours : il reste la transcription continue. SRT et VTT
        # sont refuses en amont dans ce cas, `txt` rend le texte tel quel.
        return text if text.endswith("\n") or not text else text + "\n"
    if fmt == "srt":
        return to_srt(cues, with_speakers=with_speakers, speaker_name=speaker_name)
    if fmt == "vtt":
        return to_vtt(cues, with_speakers=with_speakers, speaker_name=speaker_name)
    return to_txt(cues, with_speakers=with_speakers, speaker_name=speaker_name)
