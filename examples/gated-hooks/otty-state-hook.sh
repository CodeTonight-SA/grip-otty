#!/bin/sh
# Otty lifecycle bridge (detection-gated).
#
# Wraps Otty's own code-signed Claude hook (Otty.app/Contents/Resources/
# agent-integration/claude/otty-hook.sh) so a shared, committed
# settings.json never hardcodes an absolute app path that fails on machines
# without Otty (Windows/Linux/CI today).
#
# Args: $1 processing|idle|awaiting   $2 claude pid ($PPID)   $3 optional "ctx"
# Env:  OTTY_APP_DIR overrides the app bundle location (default /Applications/Otty.app)
#
# Inert exit 0 (a couple of ms) when Otty is absent; otherwise exec's Otty's
# hook with stdin (the Claude hook JSON payload) passed straight through and
# OTTY_CLI injected exactly like Otty's own installer does.
OTTY_APP="${OTTY_APP_DIR:-/Applications/Otty.app}"
HOOK="$OTTY_APP/Contents/Resources/agent-integration/claude/otty-hook.sh"
CLI="$OTTY_APP/Contents/MacOS/otty-cli"
[ -f "$HOOK" ] || exit 0
[ -x "$CLI" ] || exit 0
OTTY_CLI="$CLI" exec /bin/sh "$HOOK" "$@"
