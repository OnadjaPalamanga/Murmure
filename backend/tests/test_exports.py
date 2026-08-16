"""Mise en forme des exports : decoupage en sous-titres et formats de sortie.

Aucun modele, aucun fichier : `exports` ne manipule que des listes de mots. Ce
qui est verifie ici, c'est ce qui se degrade sans bruit — un sous-titre qui
chevauche le suivant, un horodatage decale d'une seconde, un locuteur qui deborde
sur la replique d'un autre. Rien de tout cela ne leve d'erreur a l'usage.
"""

from __future__ import annotations

import json

import pytest

from murmure.exports import (
    MAX_CHARS,
    MIN_DURATION,
    build_cues,
    cues_from_turns,
    format_timestamp,
    render,
    to_srt,
    to_vtt,
    wrap,
)


def word(start: float, end: float, text: str, speaker: int | None = None) -> dict:
    item = {"start": start, "end": end, "text": text}
    if speaker is not None:
        item["speaker"] = speaker
    return item


# ------------------------------------------------------------- horodatage


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.0, "00:00:00,000"),
        (1.5, "00:00:01,500"),
        (61.25, "00:01:01,250"),
        (3600.0, "01:00:00,000"),
        (3661.007, "01:01:01,007"),
        (-4.0, "00:00:00,000"),  # jamais de temps negatif dans un fichier
    ],
)
def test_format_timestamp_srt(seconds, expected):
    assert format_timestamp(seconds, sep=",") == expected


def test_format_timestamp_vtt_uses_a_dot():
    """SRT et WebVTT ne different que par ce separateur, et s'y tromper fait
    rejeter le fichier entier par certains lecteurs."""
    assert format_timestamp(1.5, sep=".") == "00:00:01.500"


def test_format_timestamp_rounds_to_the_millisecond():
    assert format_timestamp(0.0006) == "00:00:00,001"


# ------------------------------------------------------------------ lignes


def test_wrap_never_splits_a_word():
    lines = wrap("bonjour tout le monde ici", width=12)
    assert all(len(line) <= 12 for line in lines)
    assert " ".join(lines) == "bonjour tout le monde ici"


def test_wrap_keeps_a_word_longer_than_the_line_intact():
    """Une URL dictee depasse la ligne. Une ligne trop longue vaut mieux qu'un
    mot tronque, qui serait faux."""
    lines = wrap("https://exemple.tres.long.fr/chemin", width=10)
    assert lines == ["https://exemple.tres.long.fr/chemin"]


def test_wrap_merges_the_overflow_into_the_last_line():
    lines = wrap("a b c d e f g h i j k l", width=3, max_lines=2)
    assert len(lines) == 2
    assert " ".join(lines) == "a b c d e f g h i j k l"


# ------------------------------------------------------------ decoupage


def test_cues_break_on_speaker_change():
    """La seule rupture imperative : melanger deux voix dans un sous-titre fait
    dire a quelqu'un ce qu'il n'a pas dit."""
    words = [
        word(0.0, 0.5, "bonjour", 0),
        word(0.6, 1.0, "ca", 0),
        word(1.1, 1.4, "oui", 1),
    ]
    cues = build_cues(words)
    assert len(cues) == 2
    assert cues[0].speaker == 0
    assert cues[0].text == "bonjour ca"
    assert cues[1].speaker == 1
    assert cues[1].text == "oui"


def test_cues_break_on_a_silence():
    words = [word(0.0, 0.5, "un"), word(0.6, 1.0, "deux"), word(5.0, 5.4, "trois")]
    cues = build_cues(words, max_gap=0.7)
    assert [c.text for c in cues] == ["un deux", "trois"]


def test_cues_break_on_length():
    words = [word(i * 0.2, i * 0.2 + 0.15, "mot") for i in range(60)]
    cues = build_cues(words)
    assert len(cues) > 1
    assert all(len(c.text) <= MAX_CHARS for c in cues)


def test_cues_break_on_duration():
    """Un sous-titre court mais tres etale reste illisible : il faut couper sur
    la duree aussi, pas seulement sur le nombre de caracteres."""
    words = [word(i * 2.0, i * 2.0 + 0.3, "a") for i in range(6)]
    cues = build_cues(words, max_seconds=6.0, max_gap=99.0)
    assert len(cues) > 1
    assert all(c.end - c.start <= 6.5 for c in cues)


def test_cues_ignore_empty_words():
    cues = build_cues([word(0.0, 0.4, "  "), word(0.5, 0.9, "seul")])
    assert [c.text for c in cues] == ["seul"]


def test_no_cues_from_no_words():
    assert build_cues([]) == []


# --------------------------------------------------------- duree plancher


def test_a_very_short_cue_is_extended():
    cues = build_cues([word(1.0, 1.08, "oui")])
    # `approx` parce que 1.4 - 1.0 vaut 0.39999... en binaire : l'ecart est
    # mille fois plus petit que la milliseconde ou s'arrete l'horodatage.
    assert cues[0].end - cues[0].start == pytest.approx(MIN_DURATION)


def test_extension_never_overlaps_the_next_cue():
    """Deux sous-titres qui se chevauchent s'affichent ensemble : c'est un
    defaut visible, et l'allongement d'un mot bref est exactement ce qui le
    provoque."""
    words = [word(0.0, 0.05, "oui", 0), word(0.2, 1.2, "non", 1)]
    cues = build_cues(words)
    assert len(cues) == 2
    assert cues[0].end <= cues[1].start


# ------------------------------------------------------------------ SRT


def test_srt_structure():
    words = [word(0.0, 1.0, "bonjour"), word(3.0, 4.0, "monde")]
    out = to_srt(build_cues(words), with_speakers=False)
    blocks = [b for b in out.split("\n\n") if b.strip()]
    assert len(blocks) == 2
    first = blocks[0].splitlines()
    assert first[0] == "1"
    assert first[1] == "00:00:00,000 --> 00:00:01,000"
    assert first[2] == "bonjour"
    # La numerotation repart de 1 et s'incremente : un lecteur strict s'arrete
    # sur un index manquant.
    assert blocks[1].splitlines()[0] == "2"


def test_srt_prefixes_the_speaker_when_asked():
    words = [word(0.0, 1.0, "bonjour", 0), word(2.0, 3.0, "salut", 1)]
    out = to_srt(build_cues(words), with_speakers=True, speaker_name="Locuteur")
    assert "Locuteur 1: bonjour" in out
    assert "Locuteur 2: salut" in out


def test_srt_numbers_speakers_from_one():
    """sherpa-onnx numerote a partir de zero, ce qui n'a de sens que pour une
    machine. Un « Locuteur 0 » dans un sous-titre est un defaut visible."""
    out = to_srt(build_cues([word(0.0, 1.0, "a", 0)]), with_speakers=True)
    assert "Locuteur 1:" in out
    assert "Locuteur 0:" not in out


# ------------------------------------------------------------------ VTT


def test_vtt_starts_with_the_mandatory_header():
    out = to_vtt(build_cues([word(0.0, 1.0, "bonjour")]))
    assert out.startswith("WEBVTT\n")


def test_vtt_uses_dots_and_no_index():
    out = to_vtt(build_cues([word(0.0, 1.0, "bonjour")]), with_speakers=False)
    assert "00:00:00.000 --> 00:00:01.000" in out
    assert "\n1\n" not in out


# ------------------------------------------------- repli sur les tours


def test_cues_from_turns_when_no_words():
    """Une entree enregistree avant la datation doit sortir quand meme, a la
    granularite du tour de parole."""
    turns = [
        {"speaker": 0, "start": 0.0, "end": 4.0, "text": "bonjour"},
        {"speaker": 1, "start": 4.2, "end": 6.0, "text": "salut"},
    ]
    cues = cues_from_turns(turns)
    assert [c.text for c in cues] == ["bonjour", "salut"]
    assert [c.speaker for c in cues] == [0, 1]


def test_render_falls_back_to_turns_without_words():
    turns = [{"speaker": 0, "start": 0.0, "end": 2.0, "text": "bonjour"}]
    out = render("srt", words=[], turns=turns, text="bonjour")
    assert "00:00:00,000 --> 00:00:02,000" in out


# --------------------------------------------------------------- render


def test_render_rejects_an_unknown_format():
    with pytest.raises(ValueError, match="Format inconnu"):
        render("docx", words=[word(0.0, 1.0, "a")])


def test_render_omits_speaker_labels_on_a_single_voice():
    """Prefixer « Locuteur 1 » sur un enregistrement a une voix n'apporte rien
    et encombre chaque sous-titre."""
    out = render("srt", words=[word(0.0, 1.0, "bonjour", 0), word(1.2, 2.0, "seul", 0)])
    assert "Locuteur" not in out


def test_render_keeps_speaker_labels_on_several_voices():
    out = render("srt", words=[word(0.0, 1.0, "a", 0), word(2.0, 3.0, "b", 1)])
    assert "Locuteur 1:" in out
    assert "Locuteur 2:" in out


def test_render_txt_is_readable_without_a_tool():
    out = render("txt", words=[word(65.0, 66.0, "bonjour")])
    assert out.strip() == "[00:01:05] bonjour"


def test_render_txt_without_any_timing_returns_the_plain_text():
    out = render("txt", words=[], turns=[], text="une dictee sans datation")
    assert out.strip() == "une dictee sans datation"


# ----------------------------------------------------------------- JSON


def test_json_carries_the_three_granularities():
    """`words` pour couper au mot pres, `turns` pour savoir qui parle, `cues`
    pour reprendre le decoupage des sous-titres exportes a cote."""
    words = [word(0.0, 1.0, "bonjour", 0), word(2.0, 3.0, "salut", 1)]
    turns = [{"speaker": 0, "start": 0.0, "end": 1.0, "text": "bonjour"}]
    payload = json.loads(render("json", words=words, turns=turns, meta={"model_id": "x"}))

    assert payload["murmure"]["model_id"] == "x"
    assert len(payload["words"]) == 2
    assert payload["words"][0] == {"start": 0.0, "end": 1.0, "text": "bonjour", "speaker": 0}
    assert payload["turns"] == turns
    assert len(payload["cues"]) == 2


def test_json_omits_the_speaker_key_when_undated():
    payload = json.loads(render("json", words=[word(0.0, 1.0, "seul")]))
    assert "speaker" not in payload["words"][0]


def test_json_is_valid_without_any_content():
    payload = json.loads(render("json", words=[], turns=[], text=""))
    assert payload["words"] == []
    assert payload["cues"] == []


# ------------------------------------------------- recollage des mots
#
# Regression : les deux moteurs rendent des mots NUS, et recoller avec une
# espace systematique donnait « l 'application », « peut -etre », « j 'ai ».
# Constate sur une transcription reelle de 92 s, ou les seize sous-titres
# portaient tous le defaut sans qu'aucune assertion ne s'en apercoive.


def test_join_words_respecte_l_elision():
    from murmure.engines.base import join_words

    mots = [
        {"text": "de", "space_before": True},
        {"text": "l'", "space_before": True},
        {"text": "application", "space_before": False},
    ]
    assert join_words(mots) == "de l'application"


def test_join_words_respecte_le_trait_d_union():
    from murmure.engines.base import join_words

    mots = [
        {"text": "peut", "space_before": True},
        {"text": "-être", "space_before": False},
    ]
    assert join_words(mots) == "peut-être"


def test_join_words_par_defaut_separe():
    """Un mot relu d'une base anterieure n'a pas la marque : l'espace reste le
    comportement par defaut, sinon tout un historique se recollerait en bloc."""
    from murmure.engines.base import join_words

    assert join_words([{"text": "bonjour"}, {"text": "monde"}]) == "bonjour monde"


def test_les_sous_titres_ne_collent_pas_d_espace_avant_l_apostrophe():
    cues = build_cues(
        [
            word(0.0, 0.3, "Je"),
            word(0.3, 0.6, "teste"),
            word(0.6, 0.8, "l'"),
            {"start": 0.8, "end": 1.4, "text": "application", "space_before": False},
        ]
    )
    assert cues[0].text == "Je teste l'application"


def test_la_longueur_mesuree_est_celle_qui_sera_affichee():
    """Le decoupage compte les caracteres du texte recolle. Compter une espace
    qui ne sera pas ecrite ferait couper un cran trop tot."""
    mots = [word(i * 0.1, i * 0.1 + 0.05, "a") for i in range(40)]
    for m in mots[1:]:
        m["space_before"] = False
    cues = build_cues(mots)
    assert len(cues) == 1, "sans espaces, les 40 lettres tiennent dans un seul sous-titre"
    assert cues[0].text == "a" * 40


def test_le_json_conserve_l_espacement():
    payload = json.loads(
        render(
            "json",
            words=[
                word(0.0, 0.4, "l'"),
                {"start": 0.4, "end": 0.9, "text": "essai", "space_before": False},
            ],
        )
    )
    assert payload["words"][0].get("space_before") is None, "vrai = omis, la base reste compacte"
    assert payload["words"][1]["space_before"] is False
