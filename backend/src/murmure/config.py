"""Reglages persistes en TOML.

Le fichier ne contient que ce qui differe du defaut : il reste lisible et
modifiable a la main, et un reglage renomme ne casse pas la configuration.

**Tout ce qui entre est valide.** Le fichier s'edite a la main, et les reglages
arrivent aussi par le WebSocket : dans les deux cas la valeur peut etre du
mauvais type. Sans controle, elle etait ecrite telle quelle, relue au demarrage
suivant, et cassait chaque transcription — un `replacements` devenu liste levait
une `AttributeError` a chaque dictee, et redemarrer n'y changeait rien puisque la
valeur etait dans le fichier. Une valeur refusee est ignoree avec un message ;
elle n'est jamais persistee.
"""

from __future__ import annotations

import logging
import shutil
import threading
import tomllib
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from typing import Any

from .diarize import DEFAULT_THRESHOLD as DIARIZE_THRESHOLD
from .models import default_model_id
from .paths import CONFIG_FILE
from .streaming import MAX_PHRASE_S, POLISH_MAX_S, PREVIEW_MS, SILENCE_MS

log = logging.getLogger(__name__)


@dataclass
class Settings:
    # --- moteur ---
    # Le defaut vient du catalogue (`is_default`), il n'est pas recopie ici :
    # ecrire l'identifiant en dur a deja fait diverger l'installation neuve de
    # ce que le catalogue et la documentation annoncaient comme recommande.
    model_id: str = field(default_factory=default_model_id)
    prefer_gpu: bool = True
    preload_on_start: bool = True
    # "auto" = detection automatique. Forcer une langue pousse le decodeur a
    # traduire phonetiquement les mots de l'autre langue reellement prononces.
    language: str = "auto"

    # --- audio ---
    input_device: int | None = None
    preroll_ms: int = 400
    mic_keepalive_s: float = 90.0
    keep_audio: bool = False  # conserver le wav a cote de chaque entree

    # --- raccourci et comportement ---
    hotkey: str = "Ctrl+Space"
    hotkey_mode: str = "hold"  # "hold" (maintenir) | "toggle" (appuyer/re-appuyer)
    copy_to_clipboard: bool = True
    show_review_window: bool = True
    play_sounds: bool = True

    # --- dictee continue ---
    # "differe" : on parle, puis le texte arrive d'un bloc, relisible avant
    # insertion. "continu" : le texte tombe phrase par phrase pendant qu'on
    # parle. Le differe reste le defaut, c'est lui qui protege le texte qui
    # compte — en continu il n'y a plus de relecture avant que ce soit ecrit.
    dictation_mode: str = "differe"  # "differe" | "continu"
    # Frappe le texte a l'endroit du curseur, dans l'application au premier plan.
    inject_at_cursor: bool = False
    # Duree de silence qui marque la fin d'une phrase, et duree au-dela de
    # laquelle on coupe meme sans silence — sinon un monologue ne sort jamais.
    # Les valeurs viennent de `streaming` : les dupliquer ici a deja produit un
    # service qui tournait avec un reglage different de celui qu'on mesurait.
    phrase_silence_ms: int = SILENCE_MS
    max_phrase_s: float = MAX_PHRASE_S
    # Quand la parole retombe, les dernieres phrases repartent au moteur d'un
    # seul bloc : voyant la phrase entiere, il rend la ponctuation, les
    # majuscules et les doublons du mode differe. "aucun" en reste au texte
    # phrase par phrase. La frappe au curseur attend le texte poli — c'est ce
    # qui evite d'avoir a revenir en arriere dans le document de l'utilisateur.
    polish_mode: str = "moteur"  # "moteur" | "aucun"
    polish_max_s: float = POLISH_MAX_S
    # Cadence de l'apercu grise pendant qu'on parle. 0 = pas d'apercu. Inactif
    # de toute facon sans carte graphique : voir `_transcribe_preview`.
    preview_ms: int = PREVIEW_MS

    # --- datation des mots (import de fichiers uniquement) ---
    # Enregistre l'instant de chaque mot, ce qui rend l'entree exportable en
    # SRT, WebVTT ou JSON. Actif par defaut : sans datation au moment de la
    # transcription, il n'y a aucun moyen de l'obtenir apres coup — il faut
    # refaire passer le fichier entier, ce qui est precisement ce qu'on
    # decouvre une heure trop tard. Le surcout est nul sur Parakeet et Canary,
    # dont le decodeur date deja ses jetons, et de l'ordre de 10 % sur Whisper,
    # qui doit aligner apres coup.
    # La dictee, elle, ne date jamais : elle n'a rien a exporter.
    timestamps_files: bool = True

    # --- diarisation (import de fichiers uniquement) ---
    # Identifier qui parle demande d'avoir entendu toute la conversation : on ne
    # peut pas nommer le deuxieme locuteur avant de l'avoir entendu une premiere
    # fois. C'est pourquoi ca ne concerne que les fichiers, jamais la dictee.
    # Desactive par defaut : ca ajoute un calcul et un telechargement de 35 Mo
    # a qui ne transcrit que sa propre voix.
    diarize_files: bool = False
    # 0 = detection automatique. Un nombre >= 2 impose le compte, et c'est
    # nettement plus fiable : sur l'enregistrement de reference, impose il est
    # exact, tandis que l'automatique varie de 2 a 8 selon le seuil.
    diarize_speakers: int = 0
    # Seuil de regroupement des voix, quand le compte n'est pas impose.
    diarize_threshold: float = DIARIZE_THRESHOLD

    # --- mise en forme ---
    trim_trailing_period: bool = False
    replacements: dict[str, str] = None  # type: ignore[assignment]

    # --- surcharges avancees, par modele ---
    model_overrides: dict[str, dict] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.replacements is None:
            self.replacements = {}
        if self.model_overrides is None:
            self.model_overrides = {}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class InvalidSetting(ValueError):
    """Valeur refusee : mauvais type, hors bornes, ou hors du jeu autorise."""


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    raise InvalidSetting("attendu : vrai ou faux")


def _text(*, allow_empty: bool = True) -> Any:
    def check(value: Any) -> str:
        if not isinstance(value, str):
            raise InvalidSetting("attendu : du texte")
        # Les caracteres de controle ne survivent pas a l'ecriture TOML et
        # n'ont aucun sens dans un raccourci ou un identifiant de modele.
        if any(ch < " " for ch in value):
            raise InvalidSetting("caractere de controle interdit")
        if not allow_empty and not value.strip():
            raise InvalidSetting("valeur vide")
        return value

    return check


def _one_of(*allowed: str) -> Any:
    def check(value: Any) -> str:
        if value not in allowed:
            raise InvalidSetting(f"attendu : {' | '.join(allowed)}")
        return value

    return check


def _whole(low: int, high: int, *, none_ok: bool = False) -> Any:
    def check(value: Any) -> int | None:
        if value is None and none_ok:
            return None
        # `bool` est un `int` en Python : sans ce test, `True` deviendrait 1.
        if isinstance(value, bool) or not isinstance(value, int):
            raise InvalidSetting("attendu : un entier")
        if not low <= value <= high:
            raise InvalidSetting(f"attendu : entre {low} et {high}")
        return value

    return check


def _decimal(low: float, high: float) -> Any:
    def check(value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidSetting("attendu : un nombre")
        if not low <= float(value) <= high:
            raise InvalidSetting(f"attendu : entre {low:g} et {high:g}")
        return float(value)

    return check


def _text_table(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise InvalidSetting("attendu : une table de texte")
    checked = _text()
    return {checked(k): checked(v) for k, v in value.items()}


def _nested_table(value: Any) -> dict[str, dict]:
    if not isinstance(value, dict) or not all(isinstance(v, dict) for v in value.values()):
        raise InvalidSetting("attendu : une table de tables")
    return value


# Un validateur par reglage. Un champ absent de cette table serait accepte sans
# controle : le test `test_chaque_reglage_est_valide` interdit l'oubli.
VALIDATORS: dict[str, Any] = {
    "model_id": _text(allow_empty=False),
    "prefer_gpu": _boolean,
    "preload_on_start": _boolean,
    "language": _text(),
    "input_device": _whole(0, 512, none_ok=True),
    "preroll_ms": _whole(0, 5_000),
    "mic_keepalive_s": _decimal(0, 3_600),
    "keep_audio": _boolean,
    "hotkey": _text(allow_empty=False),
    "hotkey_mode": _one_of("hold", "toggle"),
    "copy_to_clipboard": _boolean,
    "show_review_window": _boolean,
    "play_sounds": _boolean,
    "dictation_mode": _one_of("differe", "continu"),
    "inject_at_cursor": _boolean,
    "phrase_silence_ms": _whole(100, 5_000),
    "max_phrase_s": _decimal(1, 120),
    "polish_mode": _one_of("moteur", "aucun"),
    "polish_max_s": _decimal(1, 60),
    "preview_ms": _whole(0, 5_000),
    "timestamps_files": _boolean,
    "diarize_files": _boolean,
    "diarize_speakers": _whole(0, 32),
    "diarize_threshold": _decimal(0, 2),
    "trim_trailing_period": _boolean,
    "replacements": _text_table,
    "model_overrides": _nested_table,
}


def validate(changes: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Trie les changements en (retenus, refuses).

    Les inconnus sont ecartes comme avant — un reglage disparu entre deux
    versions ne doit pas empecher les autres de s'appliquer.
    """
    kept: dict[str, Any] = {}
    rejected: list[str] = []
    for key, value in changes.items():
        check = VALIDATORS.get(key)
        if check is None:
            rejected.append(f"{key} (inconnu)")
            continue
        try:
            kept[key] = check(value)
        except InvalidSetting as exc:
            rejected.append(f"{key} ({exc})")
    return kept, rejected


def apply_text_rules(text: str, settings: Settings) -> str:
    """Normalise les espaces, applique les remplacements et le rognage final.

    Fonction et non methode du service : la ligne de commande transcrit sans
    passer par lui, et deux implementations de la meme regle finiraient par
    rendre deux textes differents pour le meme audio.
    """
    text = " ".join(text.split()).strip()
    for src, dst in (settings.replacements or {}).items():
        if src:
            text = text.replace(src, dst)
    if settings.trim_trailing_period:
        text = text.rstrip(".").rstrip()
    return text


class ConfigStore:
    def __init__(self, path=CONFIG_FILE) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.settings = self._load()

    def _load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        try:
            with self.path.open("rb") as fh:
                raw = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            # Repartir des defauts en silence effacait sans trace tout ce que
            # l'utilisateur avait regle. Le fichier est mis de cote sous un nom
            # date : la perte reste visible, et surtout recuperable.
            log.warning("config.toml illisible (%s)", exc)
            self._quarantine()
            return Settings()

        kept, rejected = validate(raw)
        if rejected:
            log.warning("Reglages ignores a la lecture : %s", ", ".join(sorted(rejected)))
        return Settings(**kept)

    def _quarantine(self) -> None:
        """Ecarte un fichier illisible au lieu de l'ecraser au prochain `save()`."""
        spoilt = self.path.with_suffix(f".{datetime.now():%Y-%m-%d_%H-%M-%S}.corrompu")
        try:
            shutil.move(str(self.path), str(spoilt))
            log.warning("Ancienne configuration conservee sous %s", spoilt.name)
        except OSError:
            log.warning("Configuration illisible non conservee", exc_info=True)

    def update(self, changes: dict[str, Any]) -> Settings:
        """Applique ce qui est valide. Rien d'invalide n'atteint le disque."""
        kept, rejected = validate(changes)
        if rejected:
            log.warning("Reglages refuses : %s", ", ".join(sorted(rejected)))
        if not kept:
            return self.settings
        with self._lock:
            for key, value in kept.items():
                setattr(self.settings, key, value)
            self.save()
        return self.settings

    def save(self) -> None:
        with self._lock:
            defaults = Settings()
            lines = ["# Reglages Murmure. Seules les valeurs modifiees sont ecrites.", ""]
            tables: list[str] = []

            for f in fields(Settings):
                value = getattr(self.settings, f.name)
                if value == getattr(defaults, f.name):
                    continue
                if isinstance(value, dict):
                    if value:
                        tables.append(_render_table(f.name, value))
                else:
                    lines.append(f"{f.name} = {_render_value(value)}")

            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                "\n".join([*lines, "", *tables]).rstrip() + "\n", encoding="utf-8"
            )


# Echappements exiges par une chaine TOML de base. Le saut de ligne est le cas
# qui mordait : ecrit tel quel, il produisait un fichier que `tomllib` refusait
# au demarrage suivant, et TOUS les reglages repartaient aux defauts.
_TOML_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _render_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return '""'
    if isinstance(value, str):
        out = []
        for ch in value:
            if ch in _TOML_ESCAPES:
                out.append(_TOML_ESCAPES[ch])
            elif ch < " " or ch == "\x7f":
                # Les autres caracteres de controle n'ont pas de forme courte.
                out.append(f"\\u{ord(ch):04X}")
            else:
                out.append(ch)
        return '"' + "".join(out) + '"'
    return str(value)


def _render_table(name: str, mapping: dict) -> str:
    rows = [f"[{name}]"]
    for key, value in mapping.items():
        if isinstance(value, dict):
            rows.append(f"\n[{name}.{key}]")
            rows.extend(f"{k} = {_render_value(v)}" for k, v in value.items())
        else:
            rows.append(f'"{key}" = {_render_value(value)}')
    return "\n".join(rows) + "\n"
