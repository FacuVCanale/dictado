"""Atajo global con Carbon RegisterEventHotKey.

El sistema le avisa a la app SÓLO cuando se aprieta esa combinación: no hay ningún filtro delante
del resto del teclado y no hace falta el permiso de Monitorización de entrada. Si este proceso se
traba, el teclado sigue andando; lo único que deja de responder es el atajo.

`CarbonAPI` es la capa ctypes; `CarbonHotKey` recibe la API inyectada para poder testearlo sin
registrar nada en la sesión real."""

from __future__ import annotations

import ctypes
import logging

from .hotkey_spec import Hotkey

log = logging.getLogger("dictado")

CARBON_PATH = "/System/Library/Frameworks/Carbon.framework/Carbon"


def fourcc(code: str) -> int:
    return int.from_bytes(code.encode("ascii"), "big")


K_EVENT_CLASS_KEYBOARD = fourcc("keyb")
K_EVENT_HOTKEY_PRESSED = 5
K_EVENT_PARAM_DIRECT_OBJECT = fourcc("----")
TYPE_EVENT_HOTKEY_ID = fourcc("hkid")
HOTKEY_SIGNATURE = fourcc("dctd")
NO_ERR = 0


class EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


class EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


# OSStatus (*EventHandlerProcPtr)(EventHandlerCallRef, EventRef, void *userData)
EventHandlerProc = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)


class CarbonAPI:
    """Bindings mínimos de HIToolbox (CarbonEvents.h)."""

    def __init__(self, path: str = CARBON_PATH) -> None:
        lib = ctypes.CDLL(path)
        self.lib = lib
        lib.GetApplicationEventTarget.restype = ctypes.c_void_p
        lib.GetApplicationEventTarget.argtypes = []
        lib.InstallEventHandler.restype = ctypes.c_int32
        lib.InstallEventHandler.argtypes = [ctypes.c_void_p, EventHandlerProc, ctypes.c_ulong,
                                            ctypes.POINTER(EventTypeSpec), ctypes.c_void_p,
                                            ctypes.POINTER(ctypes.c_void_p)]
        lib.RemoveEventHandler.restype = ctypes.c_int32
        lib.RemoveEventHandler.argtypes = [ctypes.c_void_p]
        lib.RegisterEventHotKey.restype = ctypes.c_int32
        lib.RegisterEventHotKey.argtypes = [ctypes.c_uint32, ctypes.c_uint32, EventHotKeyID,
                                            ctypes.c_void_p, ctypes.c_uint32,
                                            ctypes.POINTER(ctypes.c_void_p)]
        lib.UnregisterEventHotKey.restype = ctypes.c_int32
        lib.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
        lib.GetEventParameter.restype = ctypes.c_int32
        lib.GetEventParameter.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                          ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
                                          ctypes.c_void_p]

    def app_target(self):
        return self.lib.GetApplicationEventTarget()

    def install_handler(self, target, proc) -> tuple[int, object]:
        spec = EventTypeSpec(K_EVENT_CLASS_KEYBOARD, K_EVENT_HOTKEY_PRESSED)
        ref = ctypes.c_void_p()
        st = self.lib.InstallEventHandler(target, proc, 1, ctypes.byref(spec), None, ctypes.byref(ref))
        return st, ref

    def remove_handler(self, ref) -> None:
        self.lib.RemoveEventHandler(ref)

    def register(self, keycode: int, mods: int, hk_id: int, target) -> tuple[int, object]:
        ref = ctypes.c_void_p()
        st = self.lib.RegisterEventHotKey(keycode, mods, EventHotKeyID(HOTKEY_SIGNATURE, hk_id),
                                          target, 0, ctypes.byref(ref))
        return st, ref

    def unregister(self, ref) -> None:
        self.lib.UnregisterEventHotKey(ref)

    def hotkey_id(self, event) -> tuple[int, int] | None:
        out = EventHotKeyID()
        st = self.lib.GetEventParameter(event, K_EVENT_PARAM_DIRECT_OBJECT, TYPE_EVENT_HOTKEY_ID,
                                        None, ctypes.sizeof(out), None, ctypes.byref(out))
        return (out.signature, out.id) if st == NO_ERR else None


class HotKeyError(RuntimeError):
    pass


class CarbonHotKey:
    HOTKEY_ID = 1

    def __init__(self, hotkey: Hotkey, on_press, api=None) -> None:
        self.hotkey = hotkey
        self.on_press = on_press
        self.api = api if api is not None else CarbonAPI()
        self._proc = EventHandlerProc(self._handle)  # referencia viva: si se libera, crashea
        self._handler_ref = None
        self._hotkey_ref = None

    def _handle(self, _call_ref, event, _user_data) -> int:
        try:
            if self.api.hotkey_id(event) == (HOTKEY_SIGNATURE, self.HOTKEY_ID):
                self.on_press()
        except Exception:  # noqa: BLE001 - una excepción no puede cruzar el callback de C
            log.exception("Error en el handler del atajo")
        return NO_ERR

    def register(self) -> None:
        target = self.api.app_target()
        st, self._handler_ref = self.api.install_handler(target, self._proc)
        if st != NO_ERR:
            raise HotKeyError(f"InstallEventHandler devolvió {st}")
        st, ref = self.api.register(self.hotkey.keycode, self.hotkey.carbon_mods, self.HOTKEY_ID, target)
        if st != NO_ERR:
            self.api.remove_handler(self._handler_ref)
            self._handler_ref = None
            raise HotKeyError(
                f"RegisterEventHotKey('{self.hotkey.label}') devolvió {st}. Puede que otra app ya use "
                "ese atajo, o (macOS 15.0–15.1) que use sólo ⌥ o ⌥⇧: probá otro con --hotkey.")
        self._hotkey_ref = ref

    def unregister(self) -> None:
        if self._hotkey_ref is not None:
            self.api.unregister(self._hotkey_ref)
            self._hotkey_ref = None
        if self._handler_ref is not None:
            self.api.remove_handler(self._handler_ref)
            self._handler_ref = None
