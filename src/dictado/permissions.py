"""Chequeo de los permisos de TCC (Micrófono y Accesibilidad). Sólo diagnostica: nunca intenta
rodear macOS. El atajo (Carbon RegisterEventHotKey) no necesita ningún permiso."""

from __future__ import annotations

PANEL = "Ajustes del Sistema > Privacidad y seguridad > {}"
URLS = {
    "Micrófono": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
    "Accesibilidad": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
}
WHY = {
    "Micrófono": "grabar la voz",
    "Accesibilidad": "pegar con Cmd+V; sin él, el texto queda en el clipboard",
}


def missing_message(name: str, host: str) -> str:
    return (f"FALTA PERMISO de {name} (para {WHY[name]}). Otorgáselo a '{host}' en "
            f"{PANEL.format(name)}  (abrir: open '{URLS[name]}'), y reiniciá dictado (menú → Salir).")


def mic_status() -> str:
    """'authorized' | 'denied' | 'restricted' | 'not_determined'."""
    from AVFoundation import AVCaptureDevice, AVMediaTypeAudio

    code = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
    return {0: "not_determined", 1: "restricted", 2: "denied", 3: "authorized"}.get(code, str(code))


def accessibility_ok() -> bool:
    from ApplicationServices import AXIsProcessTrusted

    return bool(AXIsProcessTrusted())


def host_app_hint() -> str:
    """Los permisos se le dan a la app que lanzó el proceso (la terminal), no a python."""
    import os
    import subprocess

    parent = subprocess.run(["ps", "-o", "comm=", "-p", str(os.getppid())],
                            capture_output=True, text=True, check=False).stdout.strip()
    if ".app/Contents/MacOS/" in parent:  # lanzado por el launcher de Dictado.app
        return parent.split("/Contents/MacOS/")[0]
    term = os.environ.get("TERM_PROGRAM") or os.environ.get("__CFBundleIdentifier")
    return term or f"el proceso responsable ({parent or 'desconocido'})"


def report(log) -> dict:
    host = host_app_hint()
    st = {
        "Micrófono": mic_status(),
        "Accesibilidad": "authorized" if accessibility_ok() else "denied",
    }
    for name, s in st.items():
        if s == "authorized":
            log(f"permiso {name}: OK")
        elif name == "Micrófono" and s == "not_determined":
            log("permiso Micrófono: sin decidir; macOS lo va a preguntar en la primera grabación.")
        else:
            log(missing_message(name, host))
    return st
