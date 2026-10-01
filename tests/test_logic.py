from pathlib import Path

import pytest

from dictado.config import Config, ConfigError, build_parser, config_from_args, load_toml, merge
from dictado.hotkey_spec import MOD_ALT, MOD_CMD, MOD_CTRL, MOD_SHIFT, parse_hotkey
from dictado.state import Action, Machine, State
from dictado.text import normalize


# ---------------------------------------------------------------- config

def test_defaults_resolve_model_per_engine():
    cfg = merge({}, {})
    assert cfg.engine == "parakeet"
    assert cfg.resolved_model == "mlx-community/parakeet-tdt-0.6b-v3"
    assert merge({}, {"engine": "whisper"}).resolved_model == "large-v3"
    assert cfg.lang == "es" and cfg.hotkey == "ctrl+alt+space"


def test_cli_overrides_toml_overrides_defaults():
    cfg = merge({"lang": "en", "hotkey": "cmd+shift+d", "min_seconds": 0.5},
                {"lang": "pt", "hotkey": None, "min_seconds": None})
    assert cfg.lang == "pt"             # cli pisa toml
    assert cfg.hotkey == "cmd+shift+d"  # toml pisa default; None en cli = no pasado
    assert cfg.min_seconds == 0.5


def test_cli_engine_change_drops_toml_model_of_other_engine():
    toml = {"engine": "parakeet", "model": "mlx-community/parakeet-tdt-0.6b-v2"}
    assert merge(toml, {"engine": "whisper"}).resolved_model == "large-v3"
    assert merge(toml, {"engine": "whisper", "model": "small"}).resolved_model == "small"
    assert merge(toml, {"engine": "parakeet"}).resolved_model == "mlx-community/parakeet-tdt-0.6b-v2"


@pytest.mark.parametrize("bad", [{"engine": "vosk"}, {"position": "centro"},
                                 {"min_seconds": -1}, {"hotkey": "ctrl+ñ"}])
def test_invalid_values_rejected(bad):
    with pytest.raises(ConfigError):
        merge({}, bad)


def test_toml_file_and_parser(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text('engine = "whisper"\nmodel = "medium"\ncompute-type = "float16"\n')
    assert load_toml(p) == {"engine": "whisper", "model": "medium", "compute_type": "float16"}
    args = build_parser().parse_args(["--config", str(p), "--lang", "en", "--no-vad"])
    cfg = config_from_args(args)
    assert (cfg.engine, cfg.model, cfg.compute_type, cfg.lang, cfg.vad) == \
        ("whisper", "medium", "float16", "en", False)
    assert load_toml(tmp_path / "no-existe.toml") == {}


def test_toml_unknown_key_rejected(tmp_path: Path):
    p = tmp_path / "c.toml"
    p.write_text('modelo = "x"\n')
    with pytest.raises(ConfigError):
        load_toml(p)


# ---------------------------------------------------------------- hotkey

def test_parse_hotkey():
    hk = parse_hotkey("ctrl+alt+space")
    assert hk.keycode == 49 and hk.mods == MOD_CTRL | MOD_ALT
    assert parse_hotkey("Cmd+Shift+D").mods == MOD_CMD | MOD_SHIFT
    assert parse_hotkey("f5").mods == 0


def test_hotkey_carbon_modifiers():
    # HIToolbox/Events.h: cmdKey=1<<8, shiftKey=1<<9, optionKey=1<<11, controlKey=1<<12
    assert parse_hotkey("ctrl+alt+space").carbon_mods == (1 << 12) | (1 << 11)
    assert parse_hotkey("cmd+shift+d").carbon_mods == (1 << 8) | (1 << 9)
    assert parse_hotkey("f5").carbon_mods == 0


@pytest.mark.parametrize("spec", ["", "space", "a", "hyper+space", "ctrl+"])
def test_parse_hotkey_rejects(spec):
    with pytest.raises(ValueError):
        parse_hotkey(spec)


# ---------------------------------------------------------------- texto

@pytest.mark.parametrize("raw,expected", [
    ("  hola   mundo  ", "hola mundo"),
    ("hola , qué tal ?", "hola, qué tal?"),
    ("Gracias.", ""),
    ("  ", ""),
    ("Muchas gracias por venir.", "Muchas gracias por venir."),
])
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def test_normalize_nfc():
    assert normalize("café") == "café"


# ---------------------------------------------------------------- estados

def test_happy_path_cycle():
    m = Machine(min_seconds=0.3)
    assert m.toggle() is Action.START_RECORDING and m.state is State.RECORDING
    assert m.toggle(1.2) is Action.TRANSCRIBE and m.state is State.TRANSCRIBING
    assert m.transcription_done("hola") is Action.PASTE and m.state is State.IDLE


def test_short_recording_discarded():
    m = Machine(min_seconds=0.3)
    m.toggle()
    assert m.toggle(0.29) is Action.DISCARD and m.state is State.IDLE


def test_hotkey_while_transcribing_is_ignored():
    m = Machine()
    m.toggle()
    m.toggle(1.0)
    assert m.toggle() is Action.IGNORE and m.state is State.TRANSCRIBING


def test_error_and_empty_results_return_to_idle():
    m = Machine()
    m.toggle(); m.toggle(1.0)
    assert m.transcription_done(None, "boom") is Action.SHOW_ERROR and m.state is State.IDLE
    m.toggle(); m.toggle(1.0)
    assert m.transcription_done("") is Action.NOTHING and m.state is State.IDLE


def test_recording_failure_and_bad_transition():
    m = Machine()
    m.toggle()
    assert m.recording_failed() is Action.SHOW_ERROR and m.state is State.IDLE
    with pytest.raises(RuntimeError):
        m.transcription_done("x")


def test_config_is_frozen_dataclass():
    with pytest.raises(Exception):
        Config().engine = "whisper"  # type: ignore[misc]
