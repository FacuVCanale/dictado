"""Atajo (Carbon) y pegado sin tocar la sesión real: todo con backends falsos."""

import ctypes
from pathlib import Path

import pytest

from dictado.hotkey_carbon import (HOTKEY_SIGNATURE, CarbonAPI, CarbonHotKey, EventHotKeyID,
                                   EventTypeSpec, HotKeyError)
from dictado.hotkey_spec import MOD_ALT, MOD_CMD, MOD_CTRL, parse_hotkey
from dictado.macos import KEY_CMD, KEY_V, Paster, paste_sequence, post_sequence

SRC = Path(__file__).resolve().parents[1] / "src" / "dictado"


# ------------------------------------------------------------------ atajo

class FakeCarbon:
    def __init__(self, register_status=0, event_id=(HOTKEY_SIGNATURE, 1)):
        self.calls = []
        self.register_status = register_status
        self.event_id = event_id
        self.proc = None

    def app_target(self):
        return "TARGET"

    def install_handler(self, target, proc):
        self.proc = proc
        self.calls.append(("install", target))
        return 0, "HANDLER"

    def remove_handler(self, ref):
        self.calls.append(("remove", ref))

    def register(self, keycode, mods, hk_id, target):
        self.calls.append(("register", keycode, mods, hk_id, target))
        return self.register_status, "HOTKEY"

    def unregister(self, ref):
        self.calls.append(("unregister", ref))

    def hotkey_id(self, event):
        return self.event_id


def test_hotkey_registers_exact_combo_and_fires_callback():
    api, presses = FakeCarbon(), []
    hk = CarbonHotKey(parse_hotkey("ctrl+alt+space"), lambda: presses.append(1), api=api)
    hk.register()
    assert ("register", 49, (1 << 12) | (1 << 11), 1, "TARGET") in api.calls
    assert api.proc(None, None, None) == 0  # el sistema llama al handler al apretar el atajo
    assert presses == [1]


def test_hotkey_ignores_other_ids_and_swallows_callback_errors():
    api = FakeCarbon(event_id=(HOTKEY_SIGNATURE, 99))
    presses = []
    hk = CarbonHotKey(parse_hotkey("ctrl+alt+space"), lambda: presses.append(1), api=api)
    hk.register()
    api.proc(None, None, None)
    assert presses == []

    def boom():
        raise ValueError("x")

    api2 = FakeCarbon()
    hk2 = CarbonHotKey(parse_hotkey("ctrl+alt+space"), boom, api=api2)
    hk2.register()
    assert api2.proc(None, None, None) == 0  # la excepción no cruza a C


def test_hotkey_register_failure_cleans_up_and_unregister_releases():
    api = FakeCarbon(register_status=-9878)
    hk = CarbonHotKey(parse_hotkey("ctrl+alt+space"), lambda: None, api=api)
    with pytest.raises(HotKeyError):
        hk.register()
    assert ("remove", "HANDLER") in api.calls

    api = FakeCarbon()
    hk = CarbonHotKey(parse_hotkey("ctrl+alt+space"), lambda: None, api=api)
    hk.register()
    hk.unregister()
    assert ("unregister", "HOTKEY") in api.calls and ("remove", "HANDLER") in api.calls


def test_carbon_bindings_resolve_without_registering():
    api = CarbonAPI()  # carga Carbon y declara firmas; no llama a ninguna función
    for name in ("GetApplicationEventTarget", "InstallEventHandler", "RegisterEventHotKey",
                 "UnregisterEventHotKey", "RemoveEventHandler", "GetEventParameter"):
        assert getattr(api.lib, name) is not None
    assert ctypes.sizeof(EventTypeSpec) == 8 and ctypes.sizeof(EventHotKeyID) == 8


def test_no_keyboard_tap_or_hid_level_posting_in_source():
    """Guarda de regresión de la clase de bug: nada de taps ni de posteo a nivel HID."""
    code = "\n".join(p.read_text() for p in SRC.glob("*.py"))
    assert "CGEventTapCreate" not in code
    assert "kCGHIDEventTap" not in code
    assert "CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState" not in code


# ------------------------------------------------------------------ pegado

def test_paste_sequence_is_complete_and_ends_clean():
    seq = paste_sequence()
    assert seq[0] == (KEY_CMD, True, MOD_CMD)
    assert (KEY_V, True, MOD_CMD) in seq and (KEY_V, False, MOD_CMD) in seq
    assert seq[-1] == (KEY_CMD, False, 0)
    downs = [c for c, d, _ in seq if d]
    ups = [c for c, d, _ in seq if not d]
    assert sorted(downs) == sorted(ups)  # toda tecla apretada se suelta


class FakeKB:
    def __init__(self, mods_sequence=(0,), trusted=True, fail_on=None):
        self.mods = list(mods_sequence)
        self._trusted = trusted
        self.fail_on = fail_on
        self.posted = []

    def physical_modifiers(self):
        return self.mods.pop(0) if len(self.mods) > 1 else self.mods[0]

    def trusted(self):
        return self._trusted

    def post(self, code, down, flags):
        if (code, down) == self.fail_on:
            raise RuntimeError("falló")
        self.posted.append((code, down, flags))


def test_post_sequence_releases_keys_left_down_on_failure():
    kb = FakeKB(fail_on=(KEY_V, True))
    with pytest.raises(RuntimeError):
        post_sequence(kb, paste_sequence())
    assert kb.posted == [(KEY_CMD, True, MOD_CMD), (KEY_CMD, False, 0)]


class FakeClipboard:
    def __init__(self):
        self.content = "ORIGINAL"
        self.count = 0

    def snapshot(self):
        return self.content

    def restore(self, saved):
        self.content = saved
        self.count += 1

    def set_text(self, text):
        self.content = text
        self.count += 1

    def change_count(self):
        return self.count


class Loop:
    """call_later falso con reloj simulado: ejecuta en orden de tiempo."""

    def __init__(self):
        self.now = 0.0
        self.pending = []

    def clock(self):
        return self.now

    def call_later(self, delay, fn):
        self.pending.append((self.now + delay, fn))

    def run(self, max_steps=10_000):
        steps = 0
        while self.pending:
            steps += 1
            assert steps < max_steps, "el pegado sigue esperando sin timeout"
            self.pending.sort(key=lambda x: x[0])
            t, fn = self.pending.pop(0)
            self.now = t
            fn()


def make_paster(kb, cb, loop, fallbacks):
    return Paster(kb, cb, loop.call_later, fallbacks.append, restore_delay=0.4,
                  release_timeout=1.5, poll=0.03, clock=loop.clock)


def test_waits_for_physical_modifiers_release_before_posting():
    held = MOD_CTRL | MOD_ALT
    kb = FakeKB(mods_sequence=(held, held, held, 0))
    cb, loop, fb = FakeClipboard(), Loop(), []
    p = make_paster(kb, cb, loop, fb)
    p.paste("hola ")
    assert kb.posted == [] and cb.content == "hola "  # ⌃⌥ apretados: todavía nada posteado
    loop.run()
    assert kb.posted == paste_sequence()
    assert cb.content == "ORIGINAL" and fb == []      # clipboard restaurado


def test_never_posts_if_modifiers_stay_down():
    kb = FakeKB(mods_sequence=(MOD_CTRL,))
    cb, loop, fb = FakeClipboard(), Loop(), []
    make_paster(kb, cb, loop, fb).paste("hola ")
    loop.run()
    assert kb.posted == [] and fb == ["modificadores"]
    assert cb.content == "hola "  # queda para pegar a mano
    assert loop.now <= 1.5 + 0.03 + 1e-9


def test_without_accessibility_leaves_text_in_clipboard():
    kb = FakeKB(trusted=False)
    cb, loop, fb = FakeClipboard(), Loop(), []
    make_paster(kb, cb, loop, fb).paste("hola ")
    loop.run()
    assert kb.posted == [] and fb == ["accesibilidad"] and cb.content == "hola "


def test_restore_skipped_if_user_copied_something_else():
    kb = FakeKB()
    cb, loop, fb = FakeClipboard(), Loop(), []
    make_paster(kb, cb, loop, fb).paste("hola ")
    cb.set_text("otra cosa")  # el usuario copia antes de que se restaure
    loop.run()
    assert cb.content == "otra cosa"


def test_overlay_panel_cannot_take_keyboard():
    from AppKit import NSBackingStoreBuffered, NSMakeRect

    from dictado.overlay import OverlayPanel

    p = OverlayPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, 10, 10), 0, NSBackingStoreBuffered, True)
    assert not p.canBecomeKeyWindow() and not p.canBecomeMainWindow()
