"""Carga del modelo: primero la caché local sin red; descarga sólo si falta.

La caché de Hugging Face es real (huggingface_hub corre de verdad sobre una caché armada en
tmp_path); lo único falso es el constructor del modelo, para no cargar pesos."""

import logging
import socket

import pytest
from huggingface_hub import constants
from huggingface_hub.utils import _detect_agent

from dictado.config import Config
from dictado.engines import ParakeetEngine, WhisperEngine

WHISPER_REPO = "Systran/faster-whisper-tiny"  # lo que faster-whisper resuelve para "tiny"
PARAKEET_REPO = "mlx-community/parakeet-tdt-0.6b-v3"


class NetworkBlocked(Exception):
    pass


@pytest.fixture
def network(monkeypatch, tmp_path):
    """Cualquier intento de conexión falla y queda registrado."""
    attempts = []

    def deny(*args, **kwargs):
        attempts.append(args)
        raise NetworkBlocked(args)  # no es OSError: huggingface_hub no reintenta con backoff

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    monkeypatch.setattr(constants, "HF_HUB_OFFLINE", False)
    # huggingface_hub cachea (en memoria y en disco) un registro que baja del Hub al armar headers:
    # se resetea para que un request escondido ahí no pase inadvertido por orden de los tests.
    monkeypatch.setattr(_detect_agent, "_registry", None)
    monkeypatch.setattr(constants, "AGENT_HARNESSES_PATH", str(tmp_path / "no-registry.json"))
    return attempts


@pytest.fixture
def hf_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(tmp_path))

    def put(repo, files, commit="0123456789abcdef0123456789abcdef01234567"):
        base = tmp_path / ("models--" + repo.replace("/", "--"))
        (base / "refs").mkdir(parents=True)
        (base / "refs" / "main").write_text(commit)
        snap = base / "snapshots" / commit
        snap.mkdir(parents=True)
        for f in files:
            (snap / f).write_text("{}")
        return snap

    return put


class Recorded:
    """Constructor falso del modelo: registra con qué ruta se lo llamó."""

    def __init__(self):
        self.calls = []

    def __call__(self, path, **kwargs):
        self.calls.append((path, kwargs))
        return object()


WHISPER_FILES = ["config.json", "model.bin", "tokenizer.json", "vocabulary.json"]


def test_whisper_loads_from_cache_without_network(network, hf_cache, caplog):
    snap = hf_cache(WHISPER_REPO, WHISPER_FILES)
    model = Recorded()
    with caplog.at_level(logging.INFO, logger="dictado"):
        WhisperEngine(Config(engine="whisper", model="tiny")).load(model_cls=model)
    assert model.calls[0][0] == str(snap)
    assert network == [] and "descargando" not in caplog.text


def test_parakeet_loads_from_cache_without_network(network, hf_cache, caplog):
    snap = hf_cache(PARAKEET_REPO, ["config.json", "model.safetensors"])
    model = Recorded()
    with caplog.at_level(logging.INFO, logger="dictado"):
        ParakeetEngine(Config(engine="parakeet")).load(from_pretrained=model)
    assert model.calls == [(str(snap), {})]
    assert network == [] and "descargando" not in caplog.text


@pytest.mark.parametrize("engine,kwarg,cached", [
    (WhisperEngine(Config(engine="whisper", model="tiny")), "model_cls", None),
    (ParakeetEngine(Config(engine="parakeet")), "from_pretrained", None),
    # descarga cortada: config sin pesos -> la caché no alcanza y hay que bajarlo
    (ParakeetEngine(Config(engine="parakeet")), "from_pretrained", ["config.json"]),
])
def test_without_cache_tries_to_download(network, hf_cache, caplog, engine, kwarg, cached):
    if cached:
        hf_cache(PARAKEET_REPO, cached)
    model = Recorded()
    with caplog.at_level(logging.INFO, logger="dictado"), pytest.raises(Exception):
        engine.load(**{kwarg: model})  # sin red, la descarga falla
    assert "descargando modelo por primera vez" in caplog.text
    assert network, "sin caché tenía que intentar conectarse al Hub"
    assert model.calls == []


def test_parakeet_from_a_folder_does_not_query_the_hub(network, tmp_path):
    """Lo que asume ParakeetEngine: from_pretrained con una carpeta no toca la red."""
    from parakeet_mlx import from_pretrained

    (tmp_path / "config.json").write_text("{}")
    with pytest.raises(Exception):  # config vacío: falla al armar el modelo, después de no ir a la red
        from_pretrained(str(tmp_path))
    assert network == []
