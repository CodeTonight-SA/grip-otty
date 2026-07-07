# Otty 1.2.2 Notes

These are `grip-otty` field notes for Otty 1.2.2. They are intentionally narrow:
only behaviours that affect this package are tracked here.

## Live-Verified On 2026-07-07

- `otty version` returned `otty 1.2.2`.
- `otty pane list --json` returned pane records with `id`, `window_id`, `tab_id`,
  `index`, `active`, `cwd`, `process`, `cols`, and `rows`.
- No structured `agent` field was observed in `pane list --json`, so
  `agent_panes()` still needs title heuristics as a fallback.
- `otty pane split --help` shows an anchor pane argument and `--pane`, but the
  help text does not prove a new pane id is returned from the split command.
- `otty config get ipc-allow-send-keys` returned `true` on the test machine.
- `osascript -e 'tell application "Otty" to get name'` returned `Otty`.

## Not Live-Verified Yet

- Exact AppleScript dictionary syntax. `/usr/bin/sdef /Applications/Otty.app`
  was blocked because `sdef` required full Xcode while this machine only had
  Command Line Tools selected.
- Whether `pane split --format json` returns a new pane id in some 1.2.2 paths.
  The code now supports a direct id if present and keeps the old diff fallback.

## Original 1.1.0 Feedback Status

| Feedback | 1.2.2 status in this repo |
|---|---|
| `pane split` should return the new pane id | Unknown. Code now accepts direct ids and falls back to before/after diff. |
| `pane list --json` should expose structured agent metadata | Not observed locally. Code now prefers structured metadata if future Otty adds it. |
| Empty `--pane` should error, not target focused pane | Package guard remains mandatory for old-version safety. |
| `config set ipc-allow-send-keys true` needs reload hint | Hint remains. No implicit enabling was added. |

## AppleScript Adapter Posture

Otty 1.2.2's AppleScript automation is promising: open tabs, run commands, read
visible contents/history, check busy, and set custom titles. `grip-otty` should
only wrap it after exact syntax is verified from Otty's dictionary or official
examples. The intended shape is a small stdlib-only module with one `osascript`
subprocess boundary and fail-soft availability checks.
