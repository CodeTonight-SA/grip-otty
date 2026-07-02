# Detection-gated machine-local hooks

**The problem**: tools like Otty install agent-lifecycle hooks into config
files your team COMMITS AND SHARES (e.g. Claude Code's
`~/.claude/settings.json`) — with absolute machine paths like
`/Applications/Otty.app/...`. On any machine without the tool (Windows, Linux,
CI), every hook event then spawns a failing command. All day.

**The pattern**: commit a tiny `/bin/sh` wrapper instead. It exits `0` in a
couple of milliseconds when the tool is absent, and `exec`s the tool's own
hook when present. One committed config, correct on every machine — and when
the tool ships on a new platform, the same entries simply come alive.

## Files

- `otty-state-hook.sh` — the wrapper. Checks for the Otty app bundle
  (override location with `OTTY_APP_DIR`), then execs **Otty's own
  code-signed hook** with stdin passed through. It contains no Otty code.
- `gate_hooks.py` — an idempotent settings.json rewriter: replaces raw
  absolute-path Otty entries with the gated wrapper and injects any missing
  ones. Atomic write with a JSON round-trip guard (a malformed settings.json
  silently disables ALL hooks — guard against it). Edit `WRAPPER` and `SPECS`
  for your layout.

## Usage

```bash
# 1. put the wrapper somewhere stable and committed, e.g. ~/.claude/hooks/
cp otty-state-hook.sh ~/.claude/hooks/
chmod +x ~/.claude/hooks/otty-state-hook.sh

# 2. dry-run, then apply
python3 gate_hooks.py --check
python3 gate_hooks.py --apply
```

Restart your agent sessions afterwards — Claude Code reads hooks at start.

Note: Otty's Settings → Agents panel may show its hooks as "not installed"
once they're gated (it looks for its own raw paths). If you click Install
Hooks again it will re-add raw entries — re-run `gate_hooks.py --apply`.
