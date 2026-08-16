"""Reglages persistes en TOML.

Le fichier ne contient que ce qui differe du defaut : il reste lisible et
modifiable a la main, et un reglage renomme ne casse pas la configuration.
"""

from __future__ import annotations

import logging
import threading
import tomllib
from dataclasses import asdict, dataclass, field, fields
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
            log.warning("config.toml illisible (%s), retour aux defauts", exc)
            return Settings()

        known = {f.name for f in fields(Settings)}
        unknown = set(raw) - known
        if unknown:
            log.info("Reglages ignores (inconnus) : %s", ", ".join(sorted(unknown)))
        return Settings(**{k: v for k, v in raw.items() if k in known})

    def update(self, changes: dict[str, Any]) -> Settings:
        with self._lock:
            known = {f.name for f in fields(Settings)}
            for key, value in changes.items():
                if key in known:
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


def _render_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return '""'
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
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
