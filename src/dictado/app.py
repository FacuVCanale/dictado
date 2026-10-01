"""Proceso principal: run loop de AppKit + atajo de Carbon + worker de transcripción."""

from __future__ import annotations

import logging
import math
import queue
import sys
import threading
import time

from .config import Config, ConfigError, build_parser, config_from_args
from .hotkey_spec import parse_hotkey
from .state import Action, Machine
from .text import normalize

log = logging.getLogger("dictado")


class Transcriber(threading.Thread):
    """Un solo hilo carga el modelo y hace TODAS las transcripciones (MLX no tolera
    usar el modelo desde hilos distintos al que lo creó)."""

    def __init__(self, cfg: Config, on_result, on_ready=lambda _err: None) -> None:
        super().__init__(daemon=True, name="transcriber")
        self.cfg = cfg
        self.on_result = on_result  # (text|None, error|None) -> None, llamado en este hilo
        self.on_ready = on_ready    # (load_error|None) -> None, llamado en este hilo
        self.jobs: queue.Queue = queue.Queue()
        self.load_error: str | None = None
        self.ready = threading.Event()

    def run(self) -> None:
        from .engines import load_engine, make_engine

        engine = make_engine(self.cfg)
        try:
            load_engine(engine, log.info)
        except Exception as exc:  # noqa: BLE001
            self.load_error = f"{type(exc).__name__}: {exc}"
            log.error("No se pudo cargar el modelo: %s", self.load_error)
        self.ready.set()
        self.on_ready(self.load_error)
        if self.load_error is None:
            log.info("Listo. Atajo: %s", self.cfg.hotkey)
        while True:
            audio = self.jobs.get()
            if self.load_error is not None:
                self.on_result(None, "modelo no cargó")
                continue
            t = time.monotonic()
            try:
                text = normalize(engine.transcribe(audio))
            except Exception as exc:  # noqa: BLE001
                log.exception("Falló la transcripción")
                self.on_result(None, f"{type(exc).__name__}: {exc}"[:60])
                continue
            peak = float(abs(audio).max()) if len(audio) else 0.0
            log.info("%.1fs de audio (pico %.0f dBFS) en %.2fs -> %r", len(audio) / 16000,
                     20 * math.log10(max(peak, 1e-6)), time.monotonic() - t, text)
            self.on_result(text, None)


class Dictado:
    def __init__(self, cfg: Config, fake_mic=None) -> None:
        from PyObjCTools import AppHelper

        from .audio import FakeRecorder, Recorder
        from .overlay import Overlay

        self.cfg = cfg
        self.AppHelper = AppHelper
        self.machine = Machine(cfg.min_seconds)
        if fake_mic is not None:
            from .engines import read_audio

            self.rec = FakeRecorder(read_audio(fake_mic))
            log.warning("--fake-mic: no se abre ningún micrófono; se transcribe %s.", fake_mic)
        else:
            self.rec = Recorder(cfg.input_device)
            log_input_preview(self.rec)
        self.overlay = Overlay(cfg.position, level_fn=lambda: self.rec.level)
        self.worker = Transcriber(cfg, self._result_from_worker,
                                  lambda err: AppHelper.callAfter(self._status, "Error al cargar el modelo" if err else "Listo"))
        from .macos import NSClipboard, Paster, QuartzKeyboard

        self.paster = Paster(QuartzKeyboard(), NSClipboard(), AppHelper.callLater, self._paste_fallback,
                             restore_delay=cfg.restore_delay)
        self.menubar = None
        self._warned_accessibility = False

    # --- hilo principal ---
    def on_hotkey(self) -> None:
        self.AppHelper.callAfter(self._toggle)  # fuera del callback del tap

    def _toggle(self) -> None:
        action = self.machine.toggle(self.rec.seconds)
        if action is Action.START_RECORDING:
            from .audio import NoMicrophone

            try:
                device = self.rec.start()
            except NoMicrophone as exc:
                log.error("Sin micrófono (%s). El próximo atajo reintenta.", exc)
                self.machine.recording_failed()
                self.overlay.show("error", "Sin micrófono", hide_after=2.5)
                self._status("Sin micrófono")
                return
            except Exception as exc:  # noqa: BLE001
                log.error("No se pudo abrir el micrófono: %s", exc)
                self.machine.recording_failed()
                self.overlay.show("error", "Error de micrófono", hide_after=2.5)
                return
            self.overlay.show("listening", "Escuchando…")
            self._status("Grabando…")
            log.info("Grabando con %s", device)
        elif action is Action.DISCARD:
            self.rec.stop()
            self.overlay.hide()
            self._status("Listo")
            log.info("Grabación < %.2fs: ignorada.", self.cfg.min_seconds)
        elif action is Action.TRANSCRIBE:
            audio = self.rec.stop()
            if audio.size and float(abs(audio).max()) == 0.0:
                log.warning("El audio es silencio digital absoluto: probablemente falta el permiso "
                            "de Micrófono (Ajustes del Sistema > Privacidad y seguridad > Micrófono).")
            label = "Transcribiendo…" if self.worker.ready.is_set() else "Cargando modelo…"
            self.overlay.show("transcribing", label)
            self._status(label)
            self.worker.jobs.put(audio)
        else:
            log.info("Atajo ignorado: todavía transcribiendo.")

    def _result_from_worker(self, text, error) -> None:
        self.AppHelper.callAfter(self._on_result, text, error)

    def _on_result(self, text, error) -> None:
        action = self.machine.transcription_done(text, error)
        self._status("Listo")
        if action is Action.SHOW_ERROR:
            self.overlay.show("error", f"Error: {error}"[:28], hide_after=3.0)
            return
        self.overlay.hide()
        if action is Action.PASTE:
            self.paster.paste(text + " ")

    def _paste_fallback(self, reason: str) -> None:
        """No se pudo pegar de forma segura: el texto quedó en el clipboard."""
        from . import permissions

        if reason == "accesibilidad":
            if not self._warned_accessibility:
                log.error(permissions.missing_message("Accesibilidad", permissions.host_app_hint()))
                self._warned_accessibility = True
        elif reason == "modificadores":
            log.warning("No pegué: seguía habiendo modificadores apretados después de %.1fs.",
                        self.paster.release_timeout)
        log.info("Texto en el clipboard para pegar a mano (%s).", reason)
        self.overlay.show("error", "Copiado: pegá con ⌘V", hide_after=3.0)

    def _status(self, text: str) -> None:
        if self.menubar is not None:
            self.menubar.set_status(text)

    def run(self) -> int:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

        from . import permissions
        from .hotkey_carbon import CarbonHotKey, HotKeyError
        from .menubar import MenuBar

        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        permissions.report(log.info)
        self._warned_accessibility = not permissions.accessibility_ok()  # ya se avisó en el reporte
        hotkey = CarbonHotKey(parse_hotkey(self.cfg.hotkey), self.on_hotkey)
        try:
            hotkey.register()
        except HotKeyError as exc:
            log.error("No se pudo registrar el atajo: %s", exc)
            return 5
        self.menubar = MenuBar(self.cfg.hotkey)
        self.worker.start()
        log.info("engine=%s model=%s lang=%s hotkey=%s", self.cfg.engine, self.cfg.resolved_model,
                 self.cfg.lang, self.cfg.hotkey)
        install_quit_signals(self.AppHelper)
        self.AppHelper.runEventLoop()
        hotkey.unregister()
        log.info("Chau.")
        return 0


def log_input_preview(rec) -> None:
    """Al arrancar sólo informa qué entrada se usaría: no abre nada y no sale si no hay ninguna."""
    try:
        cands = rec.candidates()
    except Exception as exc:  # noqa: BLE001
        log.warning("No pude enumerar las entradas de audio: %s. El atajo reintenta.", exc)
        return
    if cands:
        log.info("Entrada prevista: %s. Se resuelve de nuevo en cada grabación y el stream se "
                 "abre sólo mientras graba.", cands[0])
    else:
        log.warning("Sin micrófono por ahora: el próximo atajo vuelve a buscar.")


def install_quit_signals(AppHelper) -> None:
    """SIGINT (Ctrl-C) y SIGTERM (launchctl) cortan el run loop limpio."""
    import signal

    from PyObjCTools import MachSignals

    for sig in (signal.SIGINT, signal.SIGTERM):
        MachSignals.signal(sig, lambda _s: AppHelper.stopEventLoop())


def transcribe_file(cfg: Config, path) -> int:
    from .engines import load_engine, make_engine, read_audio

    engine = make_engine(cfg)
    load_engine(engine, log.info)
    audio = read_audio(path)
    t = time.monotonic()
    text = normalize(engine.transcribe(audio))
    log.info("%.1fs de audio en %.2fs", len(audio) / 16000, time.monotonic() - t)
    print(text)
    return 0


def overlay_demo(cfg: Config, seconds: float) -> int:
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
    from PyObjCTools import AppHelper

    from .overlay import Overlay

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    t0 = time.monotonic()
    ov = Overlay(cfg.position, level_fn=lambda: 0.04 * (1 + math.sin((time.monotonic() - t0) * 7)))
    ov.show("listening", "Escuchando…")
    AppHelper.callLater(seconds / 2, lambda: ov.show("transcribing", "Transcribiendo…"))
    AppHelper.callLater(seconds, AppHelper.stopEventLoop)
    AppHelper.runEventLoop(installInterrupt=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S", stream=sys.stderr)
    args = build_parser().parse_args(argv)
    try:
        cfg = config_from_args(args)
    except ConfigError as exc:
        log.error("Config inválida: %s", exc)
        return 2
    if args.transcribe_file:
        return transcribe_file(cfg, args.transcribe_file)
    if args.overlay_demo:
        return overlay_demo(cfg, args.overlay_demo)
    if args.list_devices:
        from .audio import list_devices_text, refresh_devices

        print(list_devices_text(*refresh_devices(), cfg.input_device))
        return 0
    return Dictado(cfg, args.fake_mic).run()


if __name__ == "__main__":
    raise SystemExit(main())
