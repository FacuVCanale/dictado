"""Motores ASR (faster-whisper y parakeet-mlx) sobre audio en memoria (float32 16 kHz mono).

El modelo se carga primero estrictamente desde la caché local de Hugging Face, sin tocar la red;
sólo si falta se descarga (una vez). Así el servicio arranca igual sin conexión."""

from __future__ import annotations

import logging
import tempfile
import time
import wave
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from .config import Config

SAMPLE_RATE = 16_000
PARAKEET_FILES = ("config.json", "model.safetensors")

log = logging.getLogger("dictado")


@contextmanager
def hub_offline():
    """Modo offline de huggingface_hub mientras dura el bloque. `local_files_only` solo no alcanza:
    al armar los headers, huggingface_hub igual baja de la red un registro de "agentes" (una vez
    cada 24 h). En offline no sale ningún request."""
    from huggingface_hub import constants

    prev, constants.HF_HUB_OFFLINE = constants.HF_HUB_OFFLINE, True
    try:
        yield
    finally:
        constants.HF_HUB_OFFLINE = prev


def local_first(name: str, resolve):
    """`resolve(local_only)` devuelve la ruta del modelo. Primero sin red; si no está en caché,
    lo descarga. Cualquier otro error (modelo inexistente, sin red y sin caché) se propaga."""
    from huggingface_hub.errors import LocalEntryNotFoundError

    try:
        with hub_offline():
            return resolve(True)
    except LocalEntryNotFoundError:
        log.info("'%s' no está en la caché local: descargando modelo por primera vez...", name)
    return resolve(False)


class WhisperEngine:
    name = "whisper"

    def __init__(self, cfg: Config) -> None:
        self.model_id = cfg.resolved_model
        self.compute_type = cfg.compute_type
        self.device = cfg.device
        self.lang = cfg.lang
        self.vad = cfg.vad

    def load(self, model_cls=None) -> None:
        if model_cls is None:
            from faster_whisper import WhisperModel as model_cls
        if Path(self.model_id).expanduser().is_dir():
            path = str(Path(self.model_id).expanduser())
        else:
            from faster_whisper.utils import download_model

            path = local_first(self.model_id,
                               lambda local: download_model(self.model_id, local_files_only=local))
        self.model = model_cls(path, device=self.device, compute_type=self.compute_type)

    def transcribe(self, audio: np.ndarray) -> str:
        segments, _info = self.model.transcribe(audio, language=self.lang, vad_filter=self.vad)
        return " ".join(s.text.strip() for s in segments if s.text.strip())


class ParakeetEngine:
    name = "parakeet"

    def __init__(self, cfg: Config) -> None:
        self.model_id = cfg.resolved_model

    def load(self, from_pretrained=None) -> None:
        if from_pretrained is None:
            try:
                from parakeet_mlx import from_pretrained
            except ImportError as exc:
                raise RuntimeError("parakeet-mlx no está disponible (requiere macOS Apple Silicon).") from exc
        if Path(self.model_id).expanduser().is_dir():
            path = str(Path(self.model_id).expanduser())
        else:
            path = local_first(self.model_id, lambda local: parakeet_snapshot(self.model_id, local))
        with hub_offline():  # con una carpeta, parakeet-mlx lee de ahí; offline por las dudas
            self.model = from_pretrained(path)

    def transcribe(self, audio: np.ndarray) -> str:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            path = Path(tmp.name)
        try:
            write_wav(path, audio)
            return self.model.transcribe(str(path)).text.strip()
        finally:
            path.unlink(missing_ok=True)


def parakeet_snapshot(repo_id: str, local_only: bool) -> str:
    """Carpeta del snapshot con los archivos que lee parakeet-mlx. Con local_only exige que
    todos estén en la caché (LocalEntryNotFoundError si falta alguno, p. ej. descarga cortada)."""
    from huggingface_hub import hf_hub_download

    paths = [hf_hub_download(repo_id, f, local_files_only=local_only) for f in PARAKEET_FILES]
    return str(Path(paths[0]).parent)


def write_wav(path: Path, audio: np.ndarray) -> None:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())


def read_audio(path: Path) -> np.ndarray:
    """Decodifica cualquier formato a float32 16 kHz mono con ffmpeg (modo --transcribe-file)."""
    import subprocess

    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-i", str(path), "-ar", str(SAMPLE_RATE), "-ac", "1", "-f", "s16le", "-"],
        capture_output=True, check=False,
    )
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg no pudo decodificar {path}: {r.stderr.decode()[-300:]}")
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0


def make_engine(cfg: Config):
    return WhisperEngine(cfg) if cfg.engine == "whisper" else ParakeetEngine(cfg)


def load_engine(engine, log) -> float:
    log(f"Cargando {engine.name} '{engine.model_id}'...")
    t = time.monotonic()
    engine.load()
    dt = time.monotonic() - t
    log(f"Modelo listo en {dt:.1f}s.")
    return dt
