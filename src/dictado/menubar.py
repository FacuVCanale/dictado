"""Ícono en la barra de menú: muestra el estado y tiene "Salir", usable sólo con el mouse.
Es el kill switch si el teclado no responde."""

from __future__ import annotations

from AppKit import NSApp, NSImage, NSMenu, NSMenuItem, NSStatusBar, NSVariableStatusItemLength


class MenuBar:
    def __init__(self, hotkey_label: str) -> None:
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        button = self.item.button()
        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_("mic", "Dictado")
        if img is not None:
            img.setTemplate_(True)
            button.setImage_(img)
        else:
            button.setTitle_("Dictado")
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        title = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(f"Dictado · {hotkey_label}", None, "")
        title.setEnabled_(False)
        menu.addItem_(title)
        self.status = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Cargando modelo…", None, "")
        self.status.setEnabled_(False)
        menu.addItem_(self.status)
        menu.addItem_(NSMenuItem.separatorItem())
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Salir de Dictado", "terminate:", "")
        quit_item.setTarget_(NSApp())
        menu.addItem_(quit_item)
        self.item.setMenu_(menu)

    def set_status(self, text: str) -> None:
        self.status.setTitle_(text)
