"""Otty control-CLI transport: one boundary, one API.

Otty (https://otty.sh) is a GPU-native terminal built for code agents. It
exposes a control CLI over a runtime socket: panes can be listed, split,
captured, badged, and — the load-bearing primitive — SENT KEYS, which lets a
script deliver a prompt into any pane running any agent harness (Claude Code,
Codex, OpenCode, anything with a stdin).

Every Otty interaction in this package goes through this module: a single,
mockable surface with plain-language errors and fail-soft detection.

Verified live on Otty 1.1.0 (2026-07-02):
- `otty pane send-keys --pane <id> -- "text" key:Enter` delivers + submits
  (e2e: a zsh pane echoed and executed the sent line).
- send-keys is DISABLED by default; needs `ipc-allow-send-keys = true` AND
  `otty config reload` for a running app to honour it.
- `otty pane split` does NOT return the new pane id -> discover via
  before/after `pane list` diff (encoded in :func:`split_pane`).
- `pane capture --lines N` returns the BOTTOM N rows; full-screen capture is
  the reliable verification read.
- Detection markers inside an Otty shell: TERM_PROGRAM=otty, OTTY_BIN_DIR.

Absent Otty (machines without the app — Windows/Linux today — or CI):
:func:`is_available` is False and every operation raises
:class:`OttyNotAvailable` with a plain explanation — callers that want
silence check availability first. Nothing here ever breaks a session on a
machine without Otty.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

__all__ = [
    "OttyError",
    "OttyNotAvailable",
    "agent_panes",
    "badge",
    "capture",
    "close_pane",
    "edit_in_pane",
    "ensure_send_keys",
    "info",
    "inside_otty",
    "is_available",
    "pane_list",
    "resolve_bin",
    "send_keys_enabled",
    "send_prompt",
    "split_pane",
    "state_report",
    "version",
]

# App-bundle fallbacks (macOS). OTTY_APP_DIR overrides for non-standard installs.
_APP_DIR = Path(os.environ.get("OTTY_APP_DIR", "/Applications/Otty.app"))
_BUNDLE_CLI = _APP_DIR / "Contents" / "MacOS" / "otty-cli"

# Badge kinds accepted by `otty pane badge --kind` (Otty 1.1.0 --help).
BADGE_KINDS = ("running", "completed", "finished", "unread", "error", "awaiting-input")

# Agent kinds accepted by `otty state:<kind>` (Otty 1.1.0 --help).
AGENT_KINDS = ("claude", "codex", "opencode")


class OttyError(RuntimeError):
    """An otty invocation failed; message is plain-language actionable."""


class OttyNotAvailable(OttyError):
    """Otty is not installed / reachable on this machine."""


Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def resolve_bin() -> Optional[str]:
    """Locate the otty control binary; None when Otty is absent."""
    found = shutil.which("otty")
    if found:
        return found
    bin_dir = os.environ.get("OTTY_BIN_DIR")
    if bin_dir and (Path(bin_dir) / "otty").exists():
        return str(Path(bin_dir) / "otty")
    for candidate in ("/usr/local/bin/otty", str(_BUNDLE_CLI)):
        if Path(candidate).exists():
            return candidate
    return None


def is_available() -> bool:
    return resolve_bin() is not None


def inside_otty() -> bool:
    """True when this process runs inside an Otty shell (env markers)."""
    return os.environ.get("TERM_PROGRAM") == "otty" or bool(os.environ.get("OTTY_BIN_DIR"))


def _require_pane(pane_id: object) -> str:
    """Guard: pane-targeting ops NEVER run with an empty/None id.

    Learned live 2026-07-02: `otty pane close --pane ""` acted on the FOCUSED
    pane — on a shared terminal that can be a different session's pane.
    Hard-fail here.
    """
    if not isinstance(pane_id, str) or not pane_id.strip():
        raise OttyError(
            "pane id is required (got %r) — refusing: an empty pane target falls "
            "back to the FOCUSED pane, which may belong to another session" % (pane_id,)
        )
    return pane_id.strip()


def _run(
    args: "list[str]",
    *,
    json_out: bool = True,
    timeout: float = 6.0,
    input_text: Optional[str] = None,
    runner: Runner = subprocess.run,
) -> object:
    """Run otty with args; return parsed JSON ``data`` (or raw text)."""
    binary = resolve_bin()
    if binary is None:
        raise OttyNotAvailable(
            "Otty is not installed on this machine (no `otty` on PATH, no "
            "/Applications/Otty.app). Otty is macOS-only at the time of "
            "writing — Windows/Linux are on Otty's waitlist."
        )
    argv = [binary] + (["--format", "json"] if json_out else []) + args
    try:
        proc = runner(argv, capture_output=True, text=True, timeout=timeout, input=input_text)
    except subprocess.TimeoutExpired as exc:
        raise OttyError(f"otty {' '.join(args[:2])} timed out after {timeout}s") from exc
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "").strip()
        if "send-keys is disabled" in message:
            message += (
                "\nRemedy: run `otty config set ipc-allow-send-keys true` then "
                "`otty config reload` (a running app does not pick the key up "
                "without the reload)."
            )
        raise OttyError(f"otty {' '.join(args[:3])} failed: {message or 'exit ' + str(proc.returncode)}")
    if not json_out:
        return proc.stdout
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return proc.stdout
    return payload.get("data", payload)


def version(*, runner: Runner = subprocess.run) -> str:
    data = _run(["version"], runner=runner)
    if isinstance(data, dict):
        return str(data.get("version") or data)
    return str(data).strip()


def pane_list(*, runner: Runner = subprocess.run) -> "list[dict]":
    data = _run(["pane", "list"], runner=runner)
    return list(data) if isinstance(data, list) else []


# Braille spinner block used by Otty for live agent titles (U+2800-U+28FF),
# plus the markers seen on finished/recording sessions.
_AGENT_TITLE_MARKS = ("✳", "⏺", "●")
_AGENT_WORDS = ("claude", "codex", "opencode")


def _looks_like_agent(process_title: str) -> bool:
    title = (process_title or "").strip()
    if not title:
        return False
    first = title[0]
    if 0x2800 <= ord(first) <= 0x28FF or first in _AGENT_TITLE_MARKS:
        return True
    lowered = title.lower()
    return any(word in lowered for word in _AGENT_WORDS)


def agent_panes(panes: "Optional[list[dict]]" = None, *, runner: Runner = subprocess.run) -> "list[dict]":
    """Panes whose process title looks like a live agent session.

    Heuristic on the title; prefer a structured field the moment Otty adds
    one to `pane list --json`.
    """
    if panes is None:
        panes = pane_list(runner=runner)
    return [p for p in panes if _looks_like_agent(str(p.get("process") or ""))]


def send_prompt(
    pane_id: str,
    text: str,
    *,
    submit: bool = True,
    bracketed: bool = True,
    runner: Runner = subprocess.run,
) -> None:
    """Deliver ``text`` into a pane's stdin; ``submit`` appends Enter.

    ``bracketed`` wraps the text in bracketed-paste so multi-line prompts land
    as one paste in agent TUIs (Claude Code treats it as a single input block).

    Requires the user to have enabled `ipc-allow-send-keys` (off by default —
    a deliberate Otty security choice; see the README's safety note).
    """
    pane_id = _require_pane(pane_id)
    if not text:
        raise OttyError("refusing to send an empty prompt")
    argv = ["pane", "send-keys", "--pane", pane_id]
    if bracketed:
        argv.append("--bracketed-paste")
    argv += ["--", text]
    if submit:
        argv.append("key:Enter")
    _run(argv, runner=runner)


def capture(
    pane_id: str,
    *,
    lines: Optional[int] = None,
    ansi: bool = False,
    trim: bool = False,
    runner: Runner = subprocess.run,
) -> str:
    """Read a pane's screen text. ``lines`` selects the BOTTOM N rows."""
    pane_id = _require_pane(pane_id)
    argv = ["pane", "capture", pane_id]
    if lines is not None:
        argv += ["--lines", str(lines)]
    if ansi:
        argv.append("--ansi")
    if trim:
        argv.append("--trim")
    out = _run(argv, json_out=False, runner=runner)
    return str(out)


def split_pane(
    *,
    direction: str = "right",
    command: Optional[str] = None,
    title: Optional[str] = None,
    cwd: Optional[str] = None,
    size: Optional[int] = None,
    focus: bool = False,
    runner: Runner = subprocess.run,
    discover_delay: float = 0.4,
    discover_tries: int = 5,
) -> Optional[str]:
    """Split and return the NEW pane's id (split itself reports none).

    Discovery = before/after ``pane list`` diff, polled up to
    ``discover_tries`` times (pane registration is async in Otty 1.1.0).
    Delete this dance if a future Otty returns the id from `pane split`.
    """
    before = {p.get("id") for p in pane_list(runner=runner)}
    argv = ["pane", "split", "--direction", direction]
    if command:
        argv += ["--command", command]
    if title:
        argv += ["--title", title]
    if cwd:
        argv += ["--cwd", cwd]
    if size is not None:
        argv += ["--size", str(size)]
    if not focus:
        argv.append("--no-focus")
    _run(argv, runner=runner)
    for _ in range(max(1, discover_tries)):
        time.sleep(discover_delay)
        new = [p.get("id") for p in pane_list(runner=runner) if p.get("id") not in before]
        if new:
            return str(new[0])
    return None


def close_pane(pane_id: str, *, runner: Runner = subprocess.run) -> None:
    pane_id = _require_pane(pane_id)
    _run(["pane", "close", "--pane", pane_id, "-y"], runner=runner)


def badge(
    pane_id: str,
    kind: Optional[str] = None,
    *,
    clear: bool = False,
    runner: Runner = subprocess.run,
) -> None:
    """Set/clear the badge on the pane's tab (kinds: BADGE_KINDS)."""
    pane_id = _require_pane(pane_id)
    argv = ["pane", "badge", pane_id]
    if clear:
        argv.append("--clear")
    elif kind:
        if kind not in BADGE_KINDS:
            raise OttyError(f"unknown badge kind {kind!r}; valid: {', '.join(BADGE_KINDS)}")
        argv += ["--kind", kind]
    else:
        raise OttyError("badge() needs kind=... or clear=True")
    _run(argv, runner=runner)


def state_report(kind: str, *, runner: Runner = subprocess.run, **params: str) -> None:
    """Report an agent lifecycle state: ``otty state:<kind> key=value ...``.

    Mirrors the contract Otty's own bundled agent hook speaks (states observed
    in Otty 1.1.0: processing | idle | awaiting).
    """
    if kind not in AGENT_KINDS:
        raise OttyError(f"unknown agent kind {kind!r}; valid: {', '.join(AGENT_KINDS)}")
    argv = [f"state:{kind}"] + [f"{k.replace('_', '-')}={v}" for k, v in params.items()]
    _run(argv, runner=runner)


def edit_in_pane(
    path: str,
    *,
    direction: Optional[str] = None,
    new_tab: bool = False,
    runner: Runner = subprocess.run,
) -> None:
    """Open a file in Otty's built-in editor pane (`otty edit`)."""
    argv = ["edit", path]
    if new_tab:
        argv.append("--new-tab")
    elif direction in ("left", "right", "top", "bottom"):
        argv.append(f"--{direction}")
    _run(argv, runner=runner)


def send_keys_enabled(*, runner: Runner = subprocess.run) -> bool:
    """Whether `ipc-allow-send-keys` is true in the otty config."""
    try:
        out = _run(["config", "get", "ipc-allow-send-keys"], json_out=False, runner=runner)
    except OttyError:
        return False
    return "true" in str(out).lower()


def ensure_send_keys(*, runner: Runner = subprocess.run) -> bool:
    """Enable ipc-allow-send-keys + reload the running app.

    Returns True if a change was applied. This flips an Otty SECURITY default
    (send-keys allows keystroke injection into any pane) — only call it on an
    explicit user action, never implicitly.
    """
    if send_keys_enabled(runner=runner):
        return False
    _run(["config", "set", "ipc-allow-send-keys", "true"], json_out=False, runner=runner)
    _run(["config", "reload"], json_out=False, runner=runner)
    return True


@dataclass(frozen=True)
class OttyInfo:
    available: bool
    inside: bool
    binary: Optional[str]
    version: Optional[str]


def info() -> OttyInfo:
    """One-shot fail-soft snapshot for status surfaces."""
    binary = resolve_bin()
    ver = None
    if binary is not None:
        try:
            ver = version()
        except OttyError:
            ver = None
    return OttyInfo(available=binary is not None, inside=inside_otty(), binary=binary, version=ver)
