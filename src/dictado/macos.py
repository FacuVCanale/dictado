"""Pegado: clipboard + Cmd+V sintético, sin dejar el estado de modificadores sucio.

Reglas (cada una cierra una forma de trabar el teclado):
1. No se postea nada mientras haya modificadores FÍSICOS apretados (el usuario todavía tiene
   ⌃⌥ del atajo): se espera a que los suelte, con timeout. Si no los suelta, no se pega: el
   texto queda en el clipboard y se avisa.
2. Fuente de eventos privada (kCGEventSourceStatePrivate): los flags de lo que posteamos son
   sólo los nuestros, sin mezclarse con la tabla de estado del HID.
3. Se postea en la sesión (kCGSessionEventTap), no a nivel HID.
4. Secuencia completa, como pide CGEvent.h ("All keystrokes needed to generate a character must
   be entered, including ... COMMAND keys"): ⌘ down, V down, V up, ⌘ up (flags 0). Si algo
   falla a mitad, se sueltan las teclas que quedaron abajo.
"""

from __future__ import annotations

import logging
import time

from .hotkey_spec import MOD_CMD, MOD_MASK

log = logging.getLogger("dictado")

KEY_V = 9
KEY_CMD = 55  # kVK_Command


def paste_sequence() -> list[tuple[int, bool, int]]:
    """(keycode, keyDown, flags) de un Cmd+V completo que termina con flags 0."""
    return [(KEY_CMD, True, MOD_CMD), (KEY_V, True, MOD_CMD),
            (KEY_V, False, MOD_CMD), (KEY_CMD, False, 0)]


def post_sequence(kb, seq) -> None:
    """Postea la secuencia; si una tecla falla, suelta (flags 0) las que quedaron apretadas."""
    down: list[int] = []
    try:
        for code, is_down, flags in seq:
            kb.post(code, is_down, flags)
            if is_down:
                down.append(code)
            elif code in down:
                down.remove(code)
    except Exception:
        for code in reversed(down):
            try:
                kb.post(code, False, 0)
            except Exception:  # noqa: BLE001
                log.exception("No pude soltar la tecla %d", code)
        raise


class QuartzKeyboard:
    def physical_modifiers(self) -> int:
        import Quartz

        return Quartz.CGEventSourceFlagsState(Quartz.kCGEventSourceStateHIDSystemState) & MOD_MASK

    def trusted(self) -> bool:
        from ApplicationServices import AXIsProcessTrusted

        return bool(AXIsProcessTrusted())

    def post(self, keycode: int, down: bool, flags: int) -> None:
        import Quartz

        if not hasattr(self, "_src"):
            self._src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)
        ev = Quartz.CGEventCreateKeyboardEvent(self._src, keycode, down)
        if ev is None:
            raise RuntimeError(f"CGEventCreateKeyboardEvent({keycode}) devolvió NULL")
        Quartz.CGEventSetFlags(ev, flags)
        Quartz.CGEventPost(Quartz.kCGSessionEventTap, ev)


class NSClipboard:
    def __init__(self) -> None:
        from AppKit import NSPasteboard

        self.pb = NSPasteboard.generalPasteboard()

    def snapshot(self) -> list[dict]:
        saved = []
        for item in self.pb.pasteboardItems() or []:
            d = {}
            for t in item.types():
                data = item.dataForType_(t)
                if data is not None:
                    d[t] = data
            saved.append(d)
        return saved

    def restore(self, saved) -> None:
        from AppKit import NSPasteboardItem

        self.pb.clearContents()
        items = []
        for d in saved:
            it = NSPasteboardItem.alloc().init()
            for t, data in d.items():
                it.setData_forType_(data, t)
            items.append(it)
        if items:
            self.pb.writeObjects_(items)

    def set_text(self, text: str) -> None:
        from AppKit import NSPasteboardTypeString

        self.pb.clearContents()
        self.pb.setString_forType_(text, NSPasteboardTypeString)

    def change_count(self) -> int:
        return self.pb.changeCount()


class Paster:
    """Pone el texto en el clipboard y, si se puede pegar de forma segura, manda Cmd+V y después
    restaura el clipboard. Si no (sin Accesibilidad, modificadores que no se sueltan, error al
    postear), el texto queda en el clipboard y se llama on_fallback(motivo)."""

    def __init__(self, kb, clipboard, call_later, on_fallback, *, restore_delay: float = 0.4,
                 release_timeout: float = 1.5, poll: float = 0.03, clock=time.monotonic) -> None:
        self.kb = kb
        self.clipboard = clipboard
        self.call_later = call_later
        self.on_fallback = on_fallback
        self.restore_delay = restore_delay
        self.release_timeout = release_timeout
        self.poll = poll
        self.clock = clock

    def paste(self, text: str) -> None:
        saved = self.clipboard.snapshot()
        self.clipboard.set_text(text)
        ours = self.clipboard.change_count()
        if not self.kb.trusted():
            self.on_fallback("accesibilidad")
            return
        deadline = self.clock() + self.release_timeout

        def attempt() -> None:
            if self.kb.physical_modifiers():
                if self.clock() >= deadline:
                    self.on_fallback("modificadores")
                    return
                self.call_later(self.poll, attempt)
                return
            try:
                post_sequence(self.kb, paste_sequence())
            except Exception as exc:  # noqa: BLE001
                log.error("Falló el Cmd+V sintético: %s", exc)
                self.on_fallback("error")
                return

            def restore() -> None:
                if self.clipboard.change_count() == ours:  # nadie copió otra cosa entretanto
                    self.clipboard.restore(saved)

            self.call_later(self.restore_delay, restore)

        attempt()
