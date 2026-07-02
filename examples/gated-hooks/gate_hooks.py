#!/usr/bin/env python3
"""Gate machine-local Otty hooks in a shared Claude Code settings.json.

Otty's Settings -> Agents -> "Install Hooks" writes commands with absolute
app paths into ~/.claude/settings.json. If your team commits that file, every
machine WITHOUT Otty then runs a failing command on every hook event.

This script rewrites those entries to call a tiny detection-gated wrapper
(see otty-state-hook.sh next to this file) that exits 0 when Otty is absent
and execs Otty's own hook when present. Idempotent: run it as often as you
like; a second pass changes nothing.

Edit WRAPPER (where you keep the wrapper) and SPECS (which events you want)
for your layout, then:

    python3 gate_hooks.py --check   # report drift, change nothing
    python3 gate_hooks.py --apply   # rewrite atomically

Safety: the write is atomic (temp file + rename) and round-trip-parsed first —
a malformed settings.json silently disables ALL Claude Code hooks.
"""
from __future__ import annotations

import argparse
import copy
import itertools
import json
import os
import tempfile
from pathlib import Path

# ---- edit these two for your layout --------------------------------------
WRAPPER = '/bin/sh "$HOME/.claude/hooks/otty-state-hook.sh"'
SPECS: "tuple[tuple[str, str], ...]" = (
    # (Claude Code hook event, gated command) — mirrors what Otty 1.1.0 installs.
    ("PermissionRequest", f'{WRAPPER} awaiting "$PPID" ctx'),
    ("PreToolUse", f'{WRAPPER} processing "$PPID"'),
    ("PostToolUse", f'{WRAPPER} processing "$PPID"'),
    ("UserPromptSubmit", f'{WRAPPER} processing "$PPID"'),
    ("Stop", f'{WRAPPER} idle "$PPID"'),
    ("SessionStart", f'{WRAPPER} idle "$PPID"'),
)
# ---------------------------------------------------------------------------

# Any command still referencing the app bundle directly is a raw (ungated) entry.
OTTY_RAW_MARK = "/Applications/Otty.app"


def _commands_of(event_matchers: list) -> "list[str]":
    """Flatten one event's matcher list to its command strings."""
    hook_lists = (matcher.get("hooks", []) for matcher in event_matchers)
    return [str(h.get("command", "")) for h in itertools.chain.from_iterable(hook_lists)]


def _is_raw(hook: dict) -> bool:
    return OTTY_RAW_MARK in str(hook.get("command", ""))


def _strip_matcher(matcher: dict) -> "tuple[dict | None, int]":
    """Return (matcher without raw Otty hooks | None if emptied, removed_count)."""
    kept = [h for h in matcher.get("hooks", []) if not _is_raw(h)]
    removed = len(matcher.get("hooks", [])) - len(kept)
    if not kept and not matcher.get("matcher"):
        return None, removed
    slim = dict(matcher)
    slim["hooks"] = kept
    return slim, removed


def _strip_event(matchers: list) -> "tuple[list, int]":
    stripped = [_strip_matcher(m) for m in matchers]
    kept = [m for m, _ in stripped if m is not None]
    removed = sum(n for _, n in stripped)
    return kept, removed


def _strip_raw(hooks: dict, changes: "list[str]") -> None:
    for event in list(hooks):
        kept, removed = _strip_event(hooks[event])
        if removed:
            changes.append(f"{event}: removed {removed} raw Otty entr{'y' if removed == 1 else 'ies'}")
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]


def _inject_gated(hooks: dict, changes: "list[str]") -> None:
    for event, command in SPECS:
        if command in _commands_of(hooks.get(event, [])):
            continue
        hooks.setdefault(event, []).append({"hooks": [{"type": "command", "command": command}]})
        changes.append(f"{event}: added gated hook")


def ensure(settings: dict) -> "tuple[dict, list[str]]":
    """Pure transform: returns (new_settings, changes). Idempotent."""
    result = copy.deepcopy(settings)
    hooks = result.setdefault("hooks", {})
    changes: "list[str]" = []
    _strip_raw(hooks, changes)
    _inject_gated(hooks, changes)
    return result, changes


def check(settings: dict) -> "list[str]":
    """Drift report: raw entries present / gated entries missing. Empty = clean."""
    hooks = settings.get("hooks", {})
    raw_events = [event for event, matchers in hooks.items()
                  if any(OTTY_RAW_MARK in cmd for cmd in _commands_of(matchers))]
    problems = [f"{event}: raw ungated Otty hook (fails on machines without Otty)"
                for event in raw_events]
    problems += [f"{event}: gated hook missing"
                 for event, command in SPECS
                 if command not in _commands_of(hooks.get(event, []))]
    return problems


def _write_atomic(path: Path, settings: dict) -> None:
    rendered = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
    json.loads(rendered)  # round-trip guard
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".gate-hooks-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(rendered)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", default=str(Path.home() / ".claude" / "settings.json"))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    path = Path(args.file).expanduser()
    settings = json.loads(path.read_text(encoding="utf-8"))

    if args.check:
        problems = check(settings)
        for problem in problems:
            print(f"  ✗ {problem}")
        print(f"{path}: {'clean' if not problems else str(len(problems)) + ' problem(s)'}")
        return 1 if problems else 0

    new_settings, changes = ensure(settings)
    if not changes:
        print(f"{path}: already gated — no changes")
        return 0
    for change in changes:
        print(f"  {change}")
    _write_atomic(path, new_settings)
    print(f"{path}: {len(changes)} change(s) applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
