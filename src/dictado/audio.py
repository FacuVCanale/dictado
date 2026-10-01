"""Grabación a float32 16 kHz mono. El stream de entrada existe SÓLO mientras se graba.

El micrófono se resuelve en CADA grabación, en este orden:
  (a) el `input_device` explícito (config/CLI), si está configurado y conectado;
  (b) la entrada seleccionada en macOS (Ajustes > Sonido > Entrada), sea cual sea;
  (c) si no se puede abrir, el micrófono integrado;
  (d) si tampoco, cualquier otra entrada.
Antes de cada grabación se re-enumera PortAudio (que cachea la lista al inicializarse), así que
conectar auriculares, cambiar la entrada o cerrar la tapa no deja el servicio sin micrófono."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 16_000
BUILTIN_HINTS = (("macbook", "microphone"), ("macbook", "micrófono"), ("imac", "microphone"),
                 ("built-in", "microphone"), ("integrado", "micrófono"))

log = logging.getLogger("dictado")


class NoMicrophone(RuntimeError):
    """No hay ninguna entrada de audio que se pueda abrir."""


@dataclass(frozen=True)
class Candidate:
    index: int
    name: str
    origin: str  # "configurada" | "entrada del sistema" | "integrado" | "otra entrada"

    def __str__(self) -> str:
        return f"[{self.index}] {self.name} ({self.origin})"


def is_builtin_mic(name: str) -> bool:
    n = name.lower()
    return any(all(h in n for h in hints) for hints in BUILTIN_HINTS)


def _inputs(devices: list[dict]) -> list[tuple[int, str]]:
    return [(i, d["name"]) for i, d in enumerate(devices) if d.get("max_input_channels", 0) > 0]


def match_spec(devices: list[dict], spec) -> tuple[int, str] | None:
    """`spec` es un índice ("3" o 3) o un substring del nombre. None si ninguna entrada coincide."""
    s = str(spec).strip()
    for i, name in _inputs(devices):
        if (s.isdigit() and i == int(s)) or (not s.isdigit() and s.lower() in name.lower()):
            return i, name
    return None


def candidate_inputs(devices: list[dict], default_index: int | None, spec=None) -> list[Candidate]:
    """Entradas en el orden en que se intentan abrir, sin repetidos. Lógica pura."""
    inputs = _inputs(devices)
    out: list[Candidate] = []

    def add(i: int, name: str, origin: str) -> None:
        if all(c.index != i for c in out):
            out.append(Candidate(i, name, origin))

    if spec is not None and (hit := match_spec(devices, spec)) is not None:
        add(*hit, "configurada")
    for i, name in inputs:
        if i == default_index:
            add(i, name, "entrada del sistema")
    for i, name in inputs:
        if is_builtin_mic(name):
            add(i, name, "integrado")
    for i, name in inputs:
        add(i, name, "otra entrada")
    return out


def refresh_devices() -> tuple[list[dict], int | None]:
    """Re-enumera PortAudio y devuelve (dispositivos, índice de la entrada del sistema).

    PortAudio cachea la lista y el default al inicializarse: sin esto, un dispositivo conectado
    después de arrancar no existe y uno desconectado sigue figurando. Se llama sólo sin ningún
    stream abierto. No abre ningún stream."""
    import sounddevice as sd

    sd._terminate()
    sd._initialize()
    devices = [dict(d) for d in sd.query_devices()]
    try:
        default = int(sd.query_devices(kind="input")["index"])
    except Exception:  # noqa: BLE001  sin entrada por defecto
        default = None
    return devices, default


def list_devices_text(devices: list[dict], default_index: int | None, spec=None) -> str:
    cands = candidate_inputs(devices, default_index, spec)
    if not cands:
        return "(sin dispositivos de entrada)"
    lines = []
    for order, c in enumerate(cands, 1):
        mark = "  <- se usa ésta" if order == 1 else ""
        lines.append(f"{order}. [{c.index}] {c.name} ({c.origin}){mark}")
    return "\n".join(lines)


def _sd_input_stream(**kw):
    import sounddevice as sd

    return sd.InputStream(**kw)


class Recorder:
    """Abre el stream al empezar a grabar y lo cierra al frenar: en idle no hay micrófono abierto."""

    def __init__(self, spec=None, stream_factory=_sd_input_stream, devices_fn=refresh_devices) -> None:
        self.spec = spec
        self._factory = stream_factory
        self._devices_fn = devices_fn
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream = None
        self.device: Candidate | None = None  # el de la grabación en curso o la última
        self.level = 0.0  # RMS del último bloque

    @property
    def is_open(self) -> bool:
        return self._stream is not None

    def candidates(self) -> list[Candidate]:
        """Re-enumera y ordena las entradas. Sin streams abiertos (lo garantiza start)."""
        devices, default = self._devices_fn()
        cands = candidate_inputs(devices, default, self.spec)
        if self.spec is not None and (not cands or cands[0].origin != "configurada"):
            log.warning("input_device '%s' no está conectado: sigo el orden por defecto.", self.spec)
        return cands

    def start(self) -> Candidate:
        """Abre la primera entrada que funcione, en el orden del módulo. NoMicrophone si ninguna."""
        if self._stream is not None:
            return self.device
        cands = self.candidates()
        if not cands:
            raise NoMicrophone("no hay ninguna entrada de audio")
        self._chunks = []
        self.level = 0.0
        errors = []
        for c in cands:
            stream = None
            try:
                stream = self._factory(device=c.index, samplerate=SAMPLE_RATE, channels=1,
                                       dtype="float32", blocksize=800, callback=self._cb)
                stream.start()
            except Exception as exc:  # noqa: BLE001
                log.warning("No pude abrir %s: %s", c, exc)
                errors.append(f"{c.name}: {exc}")
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:  # noqa: BLE001
                        pass
                continue
            self._stream, self.device = stream, c
            return c
        raise NoMicrophone("ninguna entrada se pudo abrir: " + "; ".join(errors))

    def _cb(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        block = np.asarray(indata)[:, 0].copy()
        with self._lock:
            self._chunks.append(block)
        self.level = float(np.sqrt(np.mean(block * block))) if len(block) else 0.0

    def stop(self) -> np.ndarray:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()
        with self._lock:
            audio = np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)
            self._chunks = []
        self.level = 0.0
        return audio

    @property
    def seconds(self) -> float:
        with self._lock:
            return sum(len(c) for c in self._chunks) / SAMPLE_RATE


class FakeRecorder:
    """Smoke test sin hardware (--fake-mic): no abre ningún micrófono; al frenar devuelve
    el audio de un archivo. La duración cuenta el tiempo real entre start y stop."""

    def __init__(self, audio: np.ndarray) -> None:
        self.audio = audio
        self._t0: float | None = None
        self.level = 0.0
        self.is_open = False
        self.device = Candidate(-1, "archivo de --fake-mic", "fake")

    def start(self) -> Candidate:
        self._t0 = time.monotonic()
        return self.device

    def stop(self) -> np.ndarray:
        self._t0 = None
        return self.audio

    @property
    def seconds(self) -> float:
        return 0.0 if self._t0 is None else time.monotonic() - self._t0
