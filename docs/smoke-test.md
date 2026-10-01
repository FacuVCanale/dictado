# Manual smoke test

Needs the permissions, the hotkey and a real session, so it is not part of `pytest`. Run it from a
terminal first (not as a service), with the menu bar kill switch in sight.

1. `say -o /tmp/test.aiff "Hello, this is a dictation test."` (writes a file, plays nothing), then
   `uv run dictado --fake-mic /tmp/test.aiff`. No microphone is opened: on stop it transcribes the
   file. The log says `Listo. Atajo: ctrl+alt+space` and the microphone icon shows in the menu bar.
2. Test the kill switch first: menu → **Salir de Dictado** → the process exits. Start it again.
3. In TextEdit, copy `XYZ` to the clipboard. Press ⌃⌥Space, wait 1 s, press ⌃⌥Space and **keep ⌃⌥
   held for a second**: nothing is pasted until you release them; then the text appears.
4. Type single letters, a lone Space, ⌘A and ⌘Z: everything responds normally (no stuck modifier,
   Space alone does not trigger dictation).
5. ⌘V elsewhere pastes `XYZ` (clipboard restored).
6. Keep ⌃⌥ held for more than 1.5 s after the second press: nothing is pasted, the overlay says
   "Copiado: pegá con ⌘V" and the text is in the clipboard.
7. Only then try a real voice (`uv run dictado`) and the service (`scripts/install-service.sh`).

`uv run dictado --overlay-demo 4` shows the overlay for 4 s and exits.
