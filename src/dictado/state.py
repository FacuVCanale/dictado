"""Máquina de estados idle -> recording -> transcribing -> idle. Lógica pura, sin UI."""

from __future__ import annotations

from enum import Enum


class State(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"


class Action(Enum):
    START_RECORDING = "start_recording"
    TRANSCRIBE = "transcribe"      # frenar y mandar a transcribir
    DISCARD = "discard"            # frenar y tirar (grabación demasiado corta)
    IGNORE = "ignore"              # atajo mientras transcribe: no se encola nada
    PASTE = "paste"
    SHOW_ERROR = "show_error"
    NOTHING = "nothing"            # transcripción vacía


class Machine:
    def __init__(self, min_seconds: float = 0.3) -> None:
        self.state = State.IDLE
        self.min_seconds = min_seconds

    def toggle(self, recorded_seconds: float = 0.0) -> Action:
        """Pulsación del atajo. recorded_seconds sólo importa al frenar."""
        if self.state is State.IDLE:
            self.state = State.RECORDING
            return Action.START_RECORDING
        if self.state is State.RECORDING:
            if recorded_seconds < self.min_seconds:
                self.state = State.IDLE
                return Action.DISCARD
            self.state = State.TRANSCRIBING
            return Action.TRANSCRIBE
        return Action.IGNORE

    def recording_failed(self) -> Action:
        """El micrófono no arrancó (permiso o dispositivo)."""
        self.state = State.IDLE
        return Action.SHOW_ERROR

    def transcription_done(self, text: str | None, error: str | None = None) -> Action:
        if self.state is not State.TRANSCRIBING:
            raise RuntimeError(f"transcription_done en estado {self.state.value}")
        self.state = State.IDLE
        if error is not None:
            return Action.SHOW_ERROR
        return Action.PASTE if text else Action.NOTHING
