# Otty Pad Studio

Otty Pad Studio is a small Tauri Markdown editor for `grip-otty`. It gives you
a pane radar, a Markdown prompt composer, a live preview, and a safe send button
that targets one explicit Otty pane.

## Safety Model

- It never enables `ipc-allow-send-keys` for you.
- It refuses empty pane ids and empty prompts.
- It talks to Otty through argument arrays, not shell-concatenated strings.
- It contains no Otty code and remains an unofficial toolkit.

## Development

```bash
npm install
npm test
npm run build
cargo test --manifest-path src-tauri/Cargo.toml
npm run tauri dev
```

## Manual Smoke

1. Start Otty with at least one agent or shell pane.
2. Run `npm run tauri dev`.
3. Pick a pane from the radar rail.
4. Type a Markdown prompt.
5. Send it and verify it lands in the selected pane.

If Otty is unavailable, the app should show a plain error state rather than
sending anywhere.
