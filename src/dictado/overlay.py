"""Overlay: NSPanel borderless, non-activating, siempre encima, que ignora el mouse."""

from __future__ import annotations

import math
import time

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSEvent,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMakeRect,
    NSPanel,
    NSScreen,
    NSStatusWindowLevel,
    NSString,
    NSTimer,
    NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)

W, H, MARGIN = 190.0, 36.0, 16.0


class OverlayPanel(NSPanel):
    """Nunca puede ser ventana key ni main: no recibe teclas aunque esté visible."""

    def canBecomeKeyWindow(self):
        return False

    def canBecomeMainWindow(self):
        return False


class OverlayView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(OverlayView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.mode = "listening"
        self.text = "Escuchando…"
        self.level_fn = lambda: 0.0
        self.t0 = time.monotonic()
        return self

    def isOpaque(self):
        return False

    def drawRect_(self, rect):
        b = self.bounds()
        NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.88).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, H / 2, H / 2).fill()

        t = time.monotonic() - self.t0
        if self.mode == "listening":
            alpha = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(t * 2 * math.pi * 1.2))
            dot = NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.23, 0.19, alpha)
        elif self.mode == "transcribing":
            alpha = 0.5 + 0.5 * (0.5 + 0.5 * math.sin(t * 2 * math.pi * 2.0))
            dot = NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.75, 0.1, alpha)
        else:
            dot = NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.4, 0.4, 1.0)
        dot.set()
        r = 6.0
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(14, H / 2 - r, 2 * r, 2 * r)).fill()

        attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_(13),
            NSForegroundColorAttributeName: NSColor.whiteColor(),
        }
        NSString.stringWithString_(self.text).drawAtPoint_withAttributes_((34, H / 2 - 8.5), attrs)

        if self.mode == "listening":  # vúmetro chico a la derecha
            lvl = max(0.0, min(1.0, self.level_fn() * 12))
            NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.18).set()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(W - 42, H / 2 - 2, 28, 4), 2, 2).fill()
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.3, 0.9, 0.45, 1.0).set()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(W - 42, H / 2 - 2, max(2.0, 28 * lvl), 4), 2, 2).fill()


class Overlay:
    def __init__(self, position: str = "top-right", level_fn=lambda: 0.0) -> None:
        self.position = position
        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        self.panel = OverlayPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H), style, NSBackingStoreBuffered, False)
        p = self.panel
        p.setLevel_(NSStatusWindowLevel)
        p.setOpaque_(False)
        p.setBackgroundColor_(NSColor.clearColor())
        p.setHasShadow_(True)
        p.setIgnoresMouseEvents_(True)
        p.setHidesOnDeactivate_(False)
        p.setFloatingPanel_(True)
        p.setBecomesKeyOnlyIfNeeded_(True)
        p.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces
                                 | NSWindowCollectionBehaviorFullScreenAuxiliary
                                 | NSWindowCollectionBehaviorStationary)
        self.view = OverlayView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        self.view.level_fn = level_fn
        p.setContentView_(self.view)
        self.timer = None
        self._hide_at = None

    def _place(self) -> None:
        if self.position == "cursor":
            m = NSEvent.mouseLocation()
            x, y = m.x + 14, m.y - H - 14
        else:
            f = (NSScreen.mainScreen() or NSScreen.screens()[0]).visibleFrame()
            left = "left" in self.position
            top = "top" in self.position
            x = f.origin.x + MARGIN if left else f.origin.x + f.size.width - W - MARGIN
            y = f.origin.y + f.size.height - H - MARGIN if top else f.origin.y + MARGIN
        self.panel.setFrameOrigin_((x, y))

    def show(self, mode: str, text: str, hide_after: float | None = None) -> None:
        self.view.mode, self.view.text = mode, text
        self._hide_at = time.monotonic() + hide_after if hide_after else None
        if not self.panel.isVisible():
            self._place()
            self.panel.orderFrontRegardless()  # visible sin activar la app ni robar foco
        if self.timer is None:
            self.timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                1 / 30, True, lambda _t: self._tick())
        self.view.setNeedsDisplay_(True)

    def _tick(self) -> None:
        if self._hide_at is not None and time.monotonic() >= self._hide_at:
            self.hide()
            return
        self.view.setNeedsDisplay_(True)

    def hide(self) -> None:
        self._hide_at = None
        if self.timer is not None:
            self.timer.invalidate()
            self.timer = None
        self.panel.orderOut_(None)
