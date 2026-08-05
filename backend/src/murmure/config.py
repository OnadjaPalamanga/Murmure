"""Reglages persistes en TOML.

Le fichier ne contient que ce qui differe du defaut : il reste lisible et
modifiable a la main, et un reglage renomme ne casse pas la configuration.
"""

from __future__ import annotations

import logging
import threading
import tomllib
from dataclasses import asdict, dataclass, fields
from typing import Any

from .paths import CONFIG_FILE
from .streaming import MAX_PHRASE_S, POLISH_MAX_S, PREVIEW_MS, SILENCE_MS

log = logging.getLogger(__name__)


@dataclass
class Settings:
    # --- moteur ---
    model_id: str = "parakeet-tdt-0.6b-v3"
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
