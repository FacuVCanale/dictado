"""Configuración: defaults < ~/.config/dictado/config.toml < flags de CLI. Lógica pura."""

from __future__ import annotations

import argparse
import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path

ENGINES = ("whisper", "parakeet")
DEFAULT_MODELS = {
    "whisper": "large-v3",
    "parakeet": "mlx-community/parakeet-tdt-0.6b-v3",
}
DEFAULT_CONFIG_PATH = Path("~/.config/dictado/config.toml").expanduser()
POSITIONS = ("top-right", "top-left", "bottom-right", "bottom-left", "cursor")


@dataclass(frozen=True)
class Config:
    engine: str = "parakeet"
    model: str | None = None  # None -> DEFAULT_MODELS[engine]
    lang: str = "es"
    hotkey: str = "ctrl+alt+space"
    compute_type: str = "int8"
    device: str = "cpu"
    min_seconds: float = 0.3
    position: str = "top-right"
    restore_delay: float = 0.4  # s entre Cmd+V y restaurar el clipboard
    vad: bool = True
    input_device: str | None = None  # None -> entrada de macOS, luego integrado, luego cualquiera

    @property
    def resolved_model(self) -> str:
        return self.model or DEFAULT_MODELS[self.engine]


class ConfigError(ValueError):
    pass


_FIELDS = {f.name for f in fields(Config)}


def load_toml(path: Path) -> dict:
    """Lee el TOML si existe. Claves con guion o guion bajo valen igual."""
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    out: dict = {}
    for k, v in raw.items():
        key = k.replace("-", "_")
        if key not in _FIELDS:
            raise ConfigError(f"{path}: clave desconocida '{k}'. Válidas: {sorted(_FIELDS)}")
        out[key] = v
    return out


def merge(toml_values: dict, cli_values: dict) -> Config:
    """defaults < toml < cli. En cli, None significa 'no pasado'."""
    data = asdict(Config())
    data.update(toml_values)
    cli = {k: v for k, v in cli_values.items() if v is not None}
    # Un --engine por CLI distinto al del TOML sin --model: el model del TOML era para el
    # otro motor, así que se usa el default del motor elegido.
    if "engine" in cli and "model" not in cli and cli["engine"] != data["engine"]:
        data["model"] = None
    data.update(cli)
    cfg = Config(**data)
    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    if cfg.engine not in ENGINES:
        raise ConfigError(f"engine '{cfg.engine}' inválido; opciones: {ENGINES}")
    if cfg.position not in POSITIONS:
        raise ConfigError(f"position '{cfg.position}' inválida; opciones: {POSITIONS}")
    if cfg.min_seconds < 0:
        raise ConfigError("min_seconds no puede ser negativo")
    from .hotkey_spec import parse_hotkey

    try:
        parse_hotkey(cfg.hotkey)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="dictado",
        description="Dictado por atajo global: graba, transcribe local y pega en el campo con foco.",
    )
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH,
                    help=f"Config TOML opcional. Default: {DEFAULT_CONFIG_PATH}")
    ap.add_argument("--engine", choices=ENGINES, help="Motor ASR. Default: parakeet.")
    ap.add_argument("--model", help="whisper: tamaño/ruta/HF id; parakeet: HF id de parakeet-mlx. "
                    f"Default por motor: {DEFAULT_MODELS}")
    ap.add_argument("--lang", help="Idioma (whisper). Default: es. Parakeet detecta solo.")
    ap.add_argument("--hotkey", help="Atajo toggle, p.ej. ctrl+alt+space, cmd+shift+d, f5. Default: ctrl+alt+space.")
    ap.add_argument("--compute-type", help="CTranslate2 compute_type (whisper). Default: int8.")
    ap.add_argument("--device", help="Device de faster-whisper (cpu/auto). Default: cpu.")
    ap.add_argument("--min-seconds", type=float, help="Grabaciones más cortas se ignoran. Default: 0.3.")
    ap.add_argument("--position", choices=POSITIONS, help="Dónde va el overlay. Default: top-right.")
    ap.add_argument("--restore-delay", type=float, help="s antes de restaurar el clipboard. Default: 0.4.")
    ap.add_argument("--no-vad", dest="vad", action="store_const", const=False, default=None,
                    help="Desactiva el VAD de whisper.")
    ap.add_argument("--transcribe-file", type=Path,
                    help="Modo prueba sin UI: carga el modelo, transcribe este audio y sale.")
    ap.add_argument("--input-device", help="Entrada preferida por nombre (substring) o índice. Si "
                    "no está conectada: la entrada de macOS, luego el integrado, luego cualquiera.")
    ap.add_argument("--list-devices", action="store_true",
                    help="Lista las entradas de audio en el orden en que se probarían (no abre ningún stream) y sale.")
    ap.add_argument("--fake-mic", type=Path, metavar="AUDIO",
                    help="Smoke test: al frenar usa este audio en vez del micrófono (atajo, overlay y pegado reales).")
    ap.add_argument("--overlay-demo", type=float, metavar="SEG",
                    help="Modo prueba: muestra el overlay SEG segundos y sale.")
    return ap


CLI_KEYS = ("engine", "model", "lang", "hotkey", "compute_type", "device",
            "min_seconds", "position", "restore_delay", "vad", "input_device")


def config_from_args(args: argparse.Namespace) -> Config:
    cli = {k: getattr(args, k) for k in CLI_KEYS}
    return merge(load_toml(args.config), cli)
