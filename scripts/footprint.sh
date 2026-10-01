#!/bin/sh
# Mide el footprint de dictado: código, dependencias directas, memoria del servicio en idle y
# carga del modelo. Es la fuente de los números del README. La RSS cuenta las páginas del modelo
# sólo mientras están residentes, así que varía según la presión de memoria.
#
# Uso: scripts/footprint.sh [--engine whisper|parakeet] [--model ID] [--config PATH]
#   Los flags (y ~/.config/dictado/config.toml) eligen qué modelo se mide, igual que en `dictado`.
#   No abre ningún micrófono ni reproduce sonido: el modelo transcribe 1 s de silencio en memoria.
#   La memoria se lee con `ps` del proceso `python -m dictado.app` que ya esté corriendo (read-only).
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || { echo "ERROR: falta $PY (corré: uv sync)" >&2; exit 1; }

printf 'machine        %s, macOS %s\n' "$(sysctl -n machdep.cpu.brand_string)" "$(sw_vers -productVersion)"
py_files=$(ls "$REPO"/src/dictado/*.py | wc -l | tr -d ' ')
py_lines=$(cat "$REPO"/src/dictado/*.py | grep -cv '^[[:space:]]*$')
c_lines=$(grep -cv '^[[:space:]]*$' "$REPO/macos-app/launcher.c")
printf 'code           %s lines of Python in %s files + %s lines of C (app launcher), blank lines excluded\n' \
    "$py_lines" "$py_files" "$c_lines"

"$PY" - "$REPO/pyproject.toml" <<'EOF'
import sys, tomllib
deps = tomllib.load(open(sys.argv[1], "rb"))["project"]["dependencies"]
names = [d.split(";")[0].split(">")[0].split("<")[0].split("=")[0].strip() for d in deps]
print(f"dependencies   {len(deps)} direct: {', '.join(names)}")
EOF

# Si hay varios (p. ej. uno lanzado a mano), se informa el de mayor RSS: el que tiene el modelo.
ps -axo pid=,rss=,command= | awk '
    / -m dictado\.app/ && !/awk/ && $2 > rss { rss = $2; pid = $1 }
    /Dictado\.app\/Contents\/MacOS\/Dictado/ && !/awk/ {
        printf "launcher RSS   %.1f MB (pid %s, Dictado.app)\n", $2 / 1024, $1 }
    END { if (pid) printf "service RSS    %.0f MB idle (pid %s, python -m dictado.app)\n", rss / 1024, pid
          else print "service RSS    n/a (the service is not running)" }'

"$PY" - "$@" <<'EOF'
import logging, sys, time
from pathlib import Path

import numpy as np

from dictado.config import build_parser, config_from_args
from dictado.engines import hub_offline, make_engine, parakeet_snapshot

logging.disable(logging.CRITICAL)
cfg = config_from_args(build_parser().parse_args(sys.argv[1:]))
engine = make_engine(cfg)
if cfg.engine == "whisper":
    engine.vad = False  # con VAD, el silencio se descarta y no se mediría ninguna inferencia
t0 = time.monotonic()
engine.load()
t_load = time.monotonic() - t0
t0 = time.monotonic()
engine.transcribe(np.zeros(16_000, np.float32))
t_first = time.monotonic() - t0

if Path(cfg.resolved_model).expanduser().is_dir():
    folder = Path(cfg.resolved_model).expanduser()
else:
    with hub_offline():
        if cfg.engine == "whisper":
            from faster_whisper.utils import download_model
            folder = Path(download_model(cfg.resolved_model, local_files_only=True))
        else:
            folder = Path(parakeet_snapshot(cfg.resolved_model, local_only=True))
size = sum(p.stat().st_size for p in folder.iterdir() if p.is_file())  # stat sigue los symlinks
print(f"model          {cfg.engine} {cfg.resolved_model}: {size / 1e9:.2f} GB on disk")
lazy = " (MLX reads the weights lazily on first use)" if cfg.engine == "parakeet" else ""
print(f"model load     {t_load:.2f} s from the local cache + {t_first:.2f} s first transcription of 1 s{lazy}")
EOF
