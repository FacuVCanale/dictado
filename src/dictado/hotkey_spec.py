"""Parseo de atajos ('ctrl+alt+space') a keycode + modificadores. Lógica pura."""

from __future__ import annotations

from dataclasses import dataclass

# Máscaras kCGEventFlagMask* (CGEventTypes.h): se usan para leer el estado de modificadores
# físicos antes de pegar. Fijas para no depender de Quartz acá.
MOD_SHIFT = 1 << 17
MOD_CTRL = 1 << 18
MOD_ALT = 1 << 19
MOD_CMD = 1 << 20
MOD_MASK = MOD_SHIFT | MOD_CTRL | MOD_ALT | MOD_CMD

_MODS = {
    "shift": MOD_SHIFT,
    "ctrl": MOD_CTRL, "control": MOD_CTRL, "⌃": MOD_CTRL,
    "alt": MOD_ALT, "opt": MOD_ALT, "option": MOD_ALT, "⌥": MOD_ALT,
    "cmd": MOD_CMD, "command": MOD_CMD, "⌘": MOD_CMD,
    "⇧": MOD_SHIFT,
}

# Modificadores de Carbon para RegisterEventHotKey (HIToolbox/Events.h: cmdKeyBit=8,
# shiftKeyBit=9, optionKeyBit=11, controlKeyBit=12).
_TO_CARBON = {MOD_CMD: 1 << 8, MOD_SHIFT: 1 << 9, MOD_ALT: 1 << 11, MOD_CTRL: 1 << 12}

# Virtual keycodes ANSI (HIToolbox/Events.h).
KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "9": 25, "7": 26, "8": 28, "0": 29,
    "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45, "m": 46,
    "return": 36, "enter": 36, "tab": 48, "space": 49, "espacio": 49, "escape": 53, "esc": 53,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100,
    "f9": 101, "f10": 109, "f11": 103, "f12": 111, "f13": 105, "f14": 107, "f15": 113,
    "f16": 106, "f17": 64, "f18": 79, "f19": 80, "f20": 90,
}


@dataclass(frozen=True)
class Hotkey:
    keycode: int
    mods: int
    label: str

    @property
    def carbon_mods(self) -> int:
        return sum(carbon for cg, carbon in _TO_CARBON.items() if self.mods & cg)


def parse_hotkey(spec: str) -> Hotkey:
    parts = [p.strip().lower() for p in spec.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ValueError(f"atajo vacío: {spec!r}")
    *mod_names, key = parts
    mods = 0
    for m in mod_names:
        if m not in _MODS:
            raise ValueError(f"modificador desconocido '{m}' en {spec!r}; válidos: shift, ctrl, alt/opt, cmd")
        mods |= _MODS[m]
    if key not in KEYCODES:
        raise ValueError(f"tecla desconocida '{key}' en {spec!r}")
    is_fkey = key.startswith("f") and key[1:].isdigit()
    if mods == 0 and not is_fkey:
        raise ValueError(f"{spec!r}: sin modificadores sólo se permiten F1–F20 (si no, se come la tecla al tipear)")
    return Hotkey(KEYCODES[key], mods, spec)
