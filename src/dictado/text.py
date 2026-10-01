"""Normalización del texto transcripto antes de pegarlo. Lógica pura."""

from __future__ import annotations

import re
import unicodedata

# Alucinaciones típicas de Whisper sobre silencio/ruido: si el texto ENTERO es una de estas,
# no se pega nada.
_HALLUCINATIONS = {
    "gracias", "gracias por ver el video", "subtítulos realizados por la comunidad de amara.org",
    "thank you", "thanks for watching", "you",
}

_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?…)\]])")


def normalize(text: str) -> str:
    """NFC, espacios colapsados, sin espacio antes de puntuación, sin alucinaciones de silencio."""
    t = unicodedata.normalize("NFC", text or "")
    t = " ".join(t.split())
    t = _SPACE_BEFORE_PUNCT.sub(r"\1", t)
    key = t.lower().strip(" .!¡¿?…")
    if not key or key in _HALLUCINATIONS:
        return ""
    return t
