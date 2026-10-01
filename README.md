# dictado

Push-to-talk dictation for macOS: press a hotkey, speak, press it again, and the text is transcribed on your Mac with the model you choose and pasted into the focused field.

## Why it is light

One Python process with the model resident, a 56-line C launcher and nothing else: no Electron, no web view, no account, no network once the model is cached. You pick the model, from Whisper `tiny` (80 MB) to Parakeet or Whisper `large-v3`.

Measured with `scripts/footprint.sh` on an Apple M5 Pro, macOS 26.6.2:

| | |
|---|---|
| Code | 1,172 lines of Python in 13 files, plus the 56-line C launcher |
| Direct dependencies | 9 (`pyproject.toml`) |
| Memory of the running service, idle | 168 MB RSS (Python process) + 0.7 MB (launcher) |
| Model load from the local cache | Parakeet TDT 0.6B v3: 0.18 s, then 0.13 s for the first 1 s transcription |
| | Whisper `large-v3` (int8, CPU): 1.49 s, then 4.76 s for the first 1 s transcription |
| | Whisper `tiny`: 0.20 s, then 0.15 s |

RSS counts the model's memory-mapped weights only while they are resident, so it grows while transcribing and macOS reclaims it when idle. Run `scripts/footprint.sh [--engine whisper --model large-v3]` to measure your own setup.

## Install

Tested on Apple Silicon with macOS 26. Requires [uv](https://docs.astral.sh/uv/) and Xcode command line tools (`clang`). `ffmpeg` is only needed for `--transcribe-file`.

```sh
git clone https://github.com/FacuVCanale/dictado && cd dictado
scripts/build-app.sh          # uv sync + builds ~/Applications/Dictado.app (ad-hoc signed)
```

Grant **Accessibility** to the app (needed to paste with ⌘V): System Settings → Privacy & Security → Accessibility → `+` → `~/Applications/Dictado.app` (⇧⌘G to type the path) → enable it. The microphone permission is requested by macOS on the first recording. The hotkey needs no permission.

```sh
scripts/install-service.sh    # LaunchAgent: starts at login, restarts only after a crash
tail -f ~/Library/Logs/dictado.log
```

Press ⌃⌥Space to start, ⌃⌥Space again to transcribe and paste. Without Accessibility the text is left in the clipboard. The bundle id (and LaunchAgent label) defaults to `io.github.dictado`; set `DICTADO_BUNDLE_ID` when building to change it.

## Choose a model

```sh
uv run dictado                                                   # Parakeet TDT 0.6B v3 (default, MLX)
uv run dictado --engine parakeet --model mlx-community/parakeet-tdt-0.6b-v2   # English-only v2
uv run dictado --engine whisper --model large-v3 --lang es       # faster-whisper, CPU int8
uv run dictado --engine whisper --model small --lang en
uv run dictado --transcribe-file note.m4a                        # load the model, transcribe a file, exit
```

Parakeet takes any [parakeet-mlx](https://github.com/senstella/parakeet-mlx) Hugging Face id. Whisper takes a size (`tiny` to `large-v3`), a CTranslate2 Hugging Face id or a local folder. The model is loaded from the local Hugging Face cache without touching the network; it is downloaded only the first time. To make the service use another model, put it in the config file and restart the service.

## Configuration

`~/.config/dictado/config.toml` (see `config.example.toml`). Precedence: defaults < TOML < command-line flags (`uv run dictado --help`).

| Key | Default | |
|---|---|---|
| `engine` | `parakeet` | `parakeet` or `whisper` |
| `model` | per engine | see above |
| `lang` | `es` | Whisper only; Parakeet detects the language |
| `hotkey` | `ctrl+alt+space` | `ctrl/alt/shift/cmd` + key, or a bare F-key |
| `input_device` | none | preferred microphone (name substring or index) |
| `position` | `top-right` | overlay position, or `cursor` |
| `min_seconds` | `0.3` | shorter recordings are dropped |
| `compute_type`, `device` | `int8`, `cpu` | faster-whisper options |

The microphone is resolved again on every recording: `input_device` if it is connected, else the input selected in System Settings → Sound, else the built-in microphone, else any input. The stream is open only while recording. With no microphone the overlay says "Sin micrófono" and the next hotkey press retries. `uv run dictado --list-devices` prints the order without opening any stream. The UI and logs are in Spanish.

## Kill switch

Dictado never taps the keyboard: the hotkey is a Carbon `RegisterEventHotKey` registration and pasting waits for you to release the modifiers. If anything still goes wrong, quit without the keyboard: menu bar microphone icon → **Salir de Dictado**. A clean exit is not restarted by launchd. Alternatives: Activity Monitor → Dictado → Force Quit, or from another machine over SSH:

```sh
launchctl bootout gui/$(id -u)/io.github.dictado     # or your DICTADO_BUNDLE_ID
```

## Troubleshooting

- **Paste stopped working after a rebuild.** `build-app.sh` only re-signs when the launcher, `Info.plist.in`, the bundle id, the version or the Python path change; then macOS invalidates the Accessibility grant. Remove the old Dictado entry in Privacy & Security → Accessibility, add the app again and restart the service (`launchctl kickstart -k gui/$(id -u)/io.github.dictado`). Editing the Python code never needs a rebuild: restart the service.
- **The service does not start.** The app runs `.venv/bin/python -m dictado.app` from the repository, so the repository and its `.venv` must stay where they were when you built the app. If you move it, run `scripts/build-app.sh` and `scripts/install-service.sh` again.
- **Hotkey taken.** If another app owns the combination, dictado logs it and exits with code 5. Pick another `hotkey`.

## Development

```sh
uv run pytest -q
```

Tests run without a microphone, keyboard events or network: devices, Carbon, the keyboard and the clipboard are faked, and a guard fails if an event tap or HID-level posting appears in the source. The checks that need a real session are in [docs/smoke-test.md](docs/smoke-test.md).

## License

MIT, Facundo Canale.
