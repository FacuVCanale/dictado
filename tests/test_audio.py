"""Elección del micrófono y ciclo de vida del stream, sin hardware real (dispositivos falsos)."""

import logging

import numpy as np
import pytest

from dictado.app import Dictado
from dictado.audio import (Candidate, FakeRecorder, NoMicrophone, Recorder, candidate_inputs,
                           list_devices_text)
from dictado.state import Machine, State

AIRPODS, BUILTIN, IPHONE, BLACKHOLE = 0, 2, 4, 5
DEVICES = [
    {"name": "AirPods Pro", "max_input_channels": 1},
    {"name": "AirPods Pro", "max_input_channels": 0},
    {"name": "MacBook Pro Microphone", "max_input_channels": 1},
    {"name": "MacBook Pro Speakers", "max_input_channels": 0},
    {"name": "iPhone Microphone", "max_input_channels": 1},
    {"name": "BlackHole 2ch", "max_input_channels": 2},
]


def order(devices, default, spec=None):
    return [c.index for c in candidate_inputs(devices, default, spec)]


# ------------------------------------------------------------------ orden (lógica pura)

def test_order_explicit_then_system_then_builtin_then_rest():
    assert order(DEVICES, AIRPODS, "blackhole") == [BLACKHOLE, AIRPODS, BUILTIN, IPHONE]
    assert order(DEVICES, AIRPODS, str(IPHONE)) == [IPHONE, AIRPODS, BUILTIN, BLACKHOLE]
    assert order(DEVICES, AIRPODS, IPHONE) == [IPHONE, AIRPODS, BUILTIN, BLACKHOLE]  # entero del TOML


def test_system_input_is_used_even_if_bluetooth():
    cands = candidate_inputs(DEVICES, AIRPODS)
    assert cands[0] == Candidate(AIRPODS, "AirPods Pro", "entrada del sistema")
    assert cands[1].origin == "integrado"


def test_explicit_not_connected_falls_back_to_default_order():
    assert order(DEVICES, IPHONE, "usb mic") == [IPHONE, BUILTIN, AIRPODS, BLACKHOLE]
    assert order(DEVICES, IPHONE, "3") == [IPHONE, BUILTIN, AIRPODS, BLACKHOLE]  # 3 es una salida


def test_no_system_default_starts_with_builtin():
    assert order(DEVICES, None) == [BUILTIN, AIRPODS, IPHONE, BLACKHOLE]


def test_no_inputs_at_all():
    outputs_only = [d for d in DEVICES if d["max_input_channels"] == 0]
    assert candidate_inputs(outputs_only, None) == []
    assert list_devices_text(outputs_only, None) == "(sin dispositivos de entrada)"


def test_list_devices_shows_order_and_choice():
    out = list_devices_text(DEVICES, AIRPODS).splitlines()
    assert out[0] == "1. [0] AirPods Pro (entrada del sistema)  <- se usa ésta"
    assert out[1] == "2. [2] MacBook Pro Microphone (integrado)"
    assert "Speakers" not in "\n".join(out)


# ------------------------------------------------------------------ apertura con fallback

class FakeStream:
    instances: list["FakeStream"] = []
    broken: set[int] = set()  # índices que fallan al abrir

    def __init__(self, **kw):
        self.kw = kw
        self.started = self.closed = False
        FakeStream.instances.append(self)

    def start(self):
        if self.kw["device"] in FakeStream.broken:
            raise OSError("Internal PortAudio error")
        self.started = True

    def stop(self):
        pass

    def close(self):
        self.closed = True


class FakeSystem:
    """Lo que devuelve la re-enumeración de PortAudio; se puede cambiar entre grabaciones."""

    def __init__(self, devices, default):
        self.devices, self.default, self.calls = devices, default, 0

    def __call__(self):
        self.calls += 1
        return self.devices, self.default


@pytest.fixture(autouse=True)
def _reset_streams():
    FakeStream.instances.clear()
    FakeStream.broken = set()


def opened(rec):
    return [s.kw["device"] for s in FakeStream.instances if s.started and not s.closed]


def test_opens_system_input():
    rec = Recorder(None, stream_factory=FakeStream, devices_fn=FakeSystem(DEVICES, AIRPODS))
    assert rec.start() == Candidate(AIRPODS, "AirPods Pro", "entrada del sistema")
    assert opened(rec) == [AIRPODS]


def test_explicit_device_wins():
    rec = Recorder("iphone", stream_factory=FakeStream, devices_fn=FakeSystem(DEVICES, AIRPODS))
    assert rec.start().index == IPHONE


def test_system_input_fails_to_open_then_builtin():
    FakeStream.broken = {AIRPODS}
    rec = Recorder(None, stream_factory=FakeStream, devices_fn=FakeSystem(DEVICES, AIRPODS))
    assert rec.start().origin == "integrado"
    assert opened(rec) == [BUILTIN]
    assert FakeStream.instances[0].closed  # el que falló quedó cerrado


def test_builtin_also_fails_then_any_other():
    FakeStream.broken = {AIRPODS, BUILTIN}
    rec = Recorder(None, stream_factory=FakeStream, devices_fn=FakeSystem(DEVICES, AIRPODS))
    assert rec.start() == Candidate(IPHONE, "iPhone Microphone", "otra entrada")


def test_nothing_opens_raises_no_microphone():
    FakeStream.broken = {AIRPODS, BUILTIN, IPHONE, BLACKHOLE}
    rec = Recorder(None, stream_factory=FakeStream, devices_fn=FakeSystem(DEVICES, AIRPODS))
    with pytest.raises(NoMicrophone):
        rec.start()
    assert not rec.is_open and all(s.closed for s in FakeStream.instances)


def test_no_inputs_raises_no_microphone_without_opening():
    rec = Recorder(None, stream_factory=FakeStream, devices_fn=FakeSystem([], None))
    with pytest.raises(NoMicrophone):
        rec.start()
    assert FakeStream.instances == []


def test_device_resolved_again_on_every_recording():
    system = FakeSystem(DEVICES, AIRPODS)
    rec = Recorder(None, stream_factory=FakeStream, devices_fn=system)
    assert rec.start().index == AIRPODS
    rec.stop()
    # se desconectan los AirPods (tapa cerrada / Bluetooth) y la entrada pasa a ser el iPhone
    system.devices = [d if i != AIRPODS else {**d, "max_input_channels": 0} for i, d in enumerate(DEVICES)]
    system.default = IPHONE
    assert rec.start().index == IPHONE
    rec.stop()
    assert system.calls == 2


def test_explicit_not_connected_logs_warning(caplog):
    rec = Recorder("usb mic", stream_factory=FakeStream, devices_fn=FakeSystem(DEVICES, BUILTIN))
    with caplog.at_level(logging.WARNING, logger="dictado"):
        assert rec.start().index == BUILTIN
    assert "usb mic" in caplog.text


def test_stream_open_only_while_recording():
    rec = Recorder(None, stream_factory=FakeStream, devices_fn=FakeSystem(DEVICES, BUILTIN))
    assert not rec.is_open and FakeStream.instances == []   # idle: nada abierto
    rec.start()
    s = FakeStream.instances[0]
    assert rec.is_open and s.started and s.kw["device"] == BUILTIN and s.kw["samplerate"] == 16000
    s.kw["callback"](np.full((800, 1), 0.5, np.float32), 800, None, None)
    s.kw["callback"](np.zeros((800, 1), np.float32), 800, None, None)
    assert rec.seconds == pytest.approx(0.1)
    audio = rec.stop()
    assert s.closed and not rec.is_open
    assert audio.shape == (1600,) and audio[0] == 0.5 and audio[-1] == 0.0


def test_fake_recorder_returns_file_audio():
    audio = np.ones(16000, np.float32)
    r = FakeRecorder(audio)
    r.start()
    assert r.seconds >= 0
    assert r.stop() is audio and r.seconds == 0.0


# ------------------------------------------------------------------ la app no sale sin micrófono

class FakeOverlay:
    def __init__(self):
        self.shown = []

    def show(self, kind, text, hide_after=None):  # noqa: ARG002
        self.shown.append(text)

    def hide(self):
        pass


def make_app(rec):
    app = object.__new__(Dictado)  # sin AppKit: sólo la lógica del atajo
    app.cfg, app.rec, app.machine, app.overlay = None, rec, Machine(0.3), FakeOverlay()
    app.menubar = None
    return app


def test_hotkey_without_microphone_shows_message_and_retries():
    system = FakeSystem([], None)
    app = make_app(Recorder(None, stream_factory=FakeStream, devices_fn=system))
    app._toggle()
    assert app.overlay.shown == ["Sin micrófono"] and app.machine.state is State.IDLE
    system.devices, system.default = DEVICES, BUILTIN  # se conecta / vuelve el micrófono
    app._toggle()
    assert app.overlay.shown[-1] == "Escuchando…" and app.machine.state is State.RECORDING
    assert opened(app.rec) == [BUILTIN]
