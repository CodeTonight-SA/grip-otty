"""otty-pad — write prompts in a real text editor, ship them into any Otty pane.

The pane can run ANY harness — Claude Code, Codex, OpenCode, a plain shell —
otty-pad just delivers keystrokes (bracketed paste + Enter) to the pane you
pick, via the bundled transport module.

Usage
-----
  otty-pad                     pick a target agent pane, then $EDITOR loop
  otty-pad --target p_xxx      skip the picker, use that pane
  otty-pad --all               broadcast each prompt to ALL agent panes
  otty-pad --send "text"       one-shot send (combine with --target/--all)
  otty-pad --watch notes.md    journal mode: a line `---` ships the block above
  otty-pad --split             open the pad in its own Otty split pane
  otty-pad --list              list panes (agents starred) and exit
  otty-pad --info              fail-soft Otty status and integration hints
  otty-pad --plain             ASCII output for screen readers/plain terminals
  otty-pad --no-submit         type the prompt but do not press Enter

Pad file protocol (both modes)
------------------------------
- Lines starting with `#>` are pad chrome/receipts — never sent.
- A line that is exactly `---` separates prompts (send several in one save).
- Editor mode: save + quit your editor -> every block sends; the pad file
  keeps a receipt journal; press Enter to write the next prompt, q to quit.
- Watch mode: point it at any file you edit in any app (VS Code, Obsidian…).
  Only blocks ABOVE a `---` line are shipped — type freely, then add `---`
  and save to send. Receipts are appended, your text is never rewritten.

Pad journals live under the XDG state dir
(``$XDG_STATE_HOME/otty-pad`` or ``~/.local/state/otty-pad``).

Requires Otty (macOS today). On machines without Otty this exits 3 with a
one-line explanation — safe everywhere, useful where Otty lives.
"""
from __future__ import annotations

import argparse
import itertools
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import transport as ot

CHROME = "#>"
SEP = "---"


def _glyphs(plain: bool) -> dict[str, str]:
    return {
        "agent": "*" if plain else "★",
        "send": "->" if plain else "→",
        "receipt": "OK" if plain else "✓",
        "keyboard": "otty-pad" if plain else "⌨ otty-pad",
        "watch": "watching" if plain else "\U0001f441 watching",
        "dot": "." if plain else "·",
    }


def _state_dir() -> Path:
    root = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    return Path(root) / "otty-pad"


PAD_DIR = _state_dir()


# ------------------------------------------------------------- pure parsing

def parse_blocks(text: str) -> "list[str]":
    """Split pad text into prompt blocks; chrome (`#>`) lines never send."""
    blocks: "list[str]" = []
    current: "list[str]" = []

    def flush() -> None:
        block = "\n".join(current).strip()
        if block:
            blocks.append(block)
        current.clear()

    for line in text.splitlines():
        if line.startswith(CHROME):
            continue
        if line.strip() == SEP:
            flush()
        else:
            current.append(line)
    flush()
    return blocks


def extract_complete(pending: str) -> "tuple[list[str], int]":
    """Watch mode: only blocks terminated by a `---` line are ready to ship.

    Returns (blocks, consumed_chars). Unterminated trailing text stays pending
    so a half-typed thought is never sent by an autosave.
    """
    last_sep_end = None
    offset = 0
    for line in pending.splitlines(keepends=True):
        if line.strip() == SEP and not line.startswith(CHROME):
            last_sep_end = offset + len(line)
        offset += len(line)
    if last_sep_end is None:
        return [], 0
    return parse_blocks(pending[:last_sep_end]), last_sep_end


def receipt(pane_id: str, block: str, when: "datetime | None" = None, *, plain: bool = False) -> str:
    stamp = (when or datetime.now()).strftime("%H:%M:%S")
    preview = " ".join(block.split())[:56]
    g = _glyphs(plain)
    return f"{CHROME} {g['receipt']} {stamp} {g['send']} {pane_id}  {len(block)} chars {g['dot']} {preview}"


def resolve_editor() -> "list[str]":
    """$VISUAL then $EDITOR then vim; multi-word values honoured."""
    raw = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vim"
    return shlex.split(raw)


# ------------------------------------------------------------- pane helpers

def _pane_label(pane: dict) -> str:
    title = str(pane.get("process") or "").strip() or "(shell)"
    return f"{pane.get('id')}  {title[:64]}"


def pick_target(*, plain: bool = False) -> str:
    panes = ot.pane_list()
    agents = ot.agent_panes(panes)
    others = [p for p in panes if p not in agents]
    ordered = agents + others
    if not ordered:
        raise ot.OttyError("no Otty panes found — is the Otty app running?")
    print("\n  otty-pad · pick a target pane\n")
    g = _glyphs(plain)
    for i, pane in enumerate(ordered, 1):
        star = g["agent"] if pane in agents else " "
        print(f"   {i:>2} {star} {_pane_label(pane)}")
    choice = input("\n  target [1]: ").strip() or "1"
    try:
        return str(ordered[int(choice) - 1]["id"])
    except (ValueError, IndexError):
        raise ot.OttyError(f"invalid choice {choice!r}")


def resolve_targets(args) -> "list[str]":
    if args.all:
        agents = ot.agent_panes()
        if not agents:
            raise ot.OttyError("--all found no agent panes (nothing that looks "
                               "like a Claude/Codex/OpenCode session)")
        return [str(p["id"]) for p in agents]
    if args.target:
        return [args.target]
    return [pick_target(plain=getattr(args, "plain", False))]


def send_blocks(targets: "list[str]", blocks: "list[str]", *, submit: bool, plain: bool = False) -> "list[str]":
    # Broadcast is an inherent cartesian product: every block to every target
    # (both lists are tiny — panes on one screen, prompts in one save).
    receipts = []
    g = _glyphs(plain)
    for block, pane_id in itertools.product(blocks, targets):
        ot.send_prompt(pane_id, block, submit=submit)
        receipts.append(receipt(pane_id, block, plain=plain))
        print(f"  {g['send']} {len(block)} chars {g['send']} {pane_id}")
    return receipts


# ------------------------------------------------------------- editor mode

def _pad_template(target_desc: str) -> str:
    return (
        f"{CHROME} otty-pad → {target_desc}\n"
        f"{CHROME} Write prompt(s) below. `---` on its own line separates prompts.\n"
        f"{CHROME} Save + quit to send. Lines starting `{CHROME}` never send.\n\n"
    )


def editor_loop(targets: "list[str]", *, submit: bool, plain: bool = False) -> int:
    PAD_DIR.mkdir(parents=True, exist_ok=True)
    pad = PAD_DIR / f"pad-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    target_desc = ", ".join(targets)
    journal: "list[str]" = []
    pad.write_text(_pad_template(target_desc), encoding="utf-8")
    editor = resolve_editor()
    g = _glyphs(plain)
    print(f"\n  {g['keyboard']} {g['send']} {target_desc}\n  pad file: {pad}\n")
    while True:
        subprocess.call(editor + [str(pad)])
        blocks = parse_blocks(pad.read_text(encoding="utf-8"))
        if blocks:
            journal += send_blocks(targets, blocks, submit=submit, plain=plain)
        else:
            print("  (nothing to send)")
        body = _pad_template(target_desc) + "".join(line + "\n" for line in journal) + "\n"
        pad.write_text(body, encoding="utf-8")
        try:
            separator = "." if plain else "·"
            answer = input(f"  Enter = next prompt {separator} q = quit: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "q"
        if answer == "q":
            print(f"  journal kept: {pad}")
            return 0


# ------------------------------------------------------------- watch mode

def watch_loop(path: Path, targets: "list[str]", *, submit: bool,
               poll: float = 0.5, max_loops: "int | None" = None,
               plain: bool = False) -> int:
    """Tail a user-owned file; ship `---`-terminated blocks as they appear."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_pad_template(", ".join(targets)), encoding="utf-8")
    offset = path.stat().st_size
    g = _glyphs(plain)
    print(f"\n  {g['watch']} {path} {g['send']} {', '.join(targets)}"
          f"\n  end a prompt with a `---` line + save to send · Ctrl-C to stop\n")
    loops = 0
    try:
        while max_loops is None or loops < max_loops:
            loops += 1
            time.sleep(poll)
            size = path.stat().st_size
            if size < offset:  # file truncated/rewritten: restart from top
                offset = 0
            if size == offset:
                continue
            with path.open("r", encoding="utf-8") as fh:
                fh.seek(offset)
                pending = fh.read()
            blocks, consumed = extract_complete(pending)
            if not blocks:
                continue
            receipts = send_blocks(targets, blocks, submit=submit, plain=plain)
            offset += consumed
            with path.open("a", encoding="utf-8") as fh:
                fh.write("".join(line + "\n" for line in receipts))
            offset = path.stat().st_size  # receipts are ours: skip them
    except KeyboardInterrupt:
        print("\n  watch stopped")
    return 0


# ------------------------------------------------------------- entrypoints

def cmd_list(*, plain: bool = False) -> int:
    panes = ot.pane_list()
    agents = {p.get("id") for p in ot.agent_panes(panes)}
    g = _glyphs(plain)
    for pane in panes:
        star = g["agent"] if pane.get("id") in agents else " "
        print(f" {star} {_pane_label(pane)}")
    return 0


def cmd_info(*, plain: bool = False) -> int:
    info = ot.info()
    g = _glyphs(plain)
    print("otty-pad info")
    print(f"  available: {info.available}")
    print(f"  inside_otty: {info.inside}")
    print(f"  binary: {info.binary or '(not found)'}")
    print(f"  version: {info.version or '(unknown)'}")
    if info.available:
        print(f"  send_keys_enabled: {ot.send_keys_enabled()}")
        print(f"  agent_detection: structured metadata if present, otherwise title heuristic")
        print(f"  otty_1_2_2_notes: background --pane targeting improved; AppleScript automation is documented by Otty, not wrapped here yet")
    else:
        print("  status: Otty not found; commands fail soft on this machine")
    print(f"  prompt_flow: editor {g['send']} otty-pad {g['send']} pane send-keys")
    return 0


def cmd_split(args) -> int:
    """Re-launch this pad inside a fresh Otty split (the 'pane' experience)."""
    inner = [shlex.quote(sys.executable), "-m", "otty_pad"]
    if args.target:
        inner += ["--target", shlex.quote(args.target)]
    if args.all:
        inner.append("--all")
    if args.no_submit:
        inner.append("--no-submit")
    pane_id = ot.split_pane(direction=args.direction, command=" ".join(inner),
                            title="otty-pad", size=args.size, focus=True)
    print(f"otty-pad pane opened{f' ({pane_id})' if pane_id else ''}")
    return 0


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(prog="otty-pad", add_help=True,
                                     description=__doc__.splitlines()[0])
    parser.add_argument("--target", help="pane id to send to (see --list)")
    parser.add_argument("--all", action="store_true", help="broadcast to all agent panes")
    parser.add_argument("--send", metavar="TEXT", help="one-shot send, no editor")
    parser.add_argument("--watch", metavar="FILE", help="watch a file you edit elsewhere")
    parser.add_argument("--split", action="store_true", help="open the pad as an Otty split pane")
    parser.add_argument("--direction", default="right", choices=["left", "right", "top", "bottom"])
    parser.add_argument("--size", type=int, default=35, help="split size %% (with --split)")
    parser.add_argument("--list", action="store_true", help="list panes and exit")
    parser.add_argument("--info", action="store_true", help="show Otty availability and integration notes")
    parser.add_argument("--plain", action="store_true", help="ASCII output for screen readers/plain terminals")
    parser.add_argument("--no-submit", action="store_true", help="type but do not press Enter")
    args = parser.parse_args(argv)

    if args.info:
        return cmd_info(plain=args.plain)

    if not ot.is_available():
        print("otty-pad: Otty is not installed on this machine (macOS app + CLI "
              "required — Windows/Linux builds are on Otty's waitlist). "
              "Nothing to do.", file=sys.stderr)
        return 3

    submit = not args.no_submit
    try:
        if args.list:
            return cmd_list(plain=args.plain)
        if args.split:
            return cmd_split(args)
        targets = resolve_targets(args)
        if args.send:
            send_blocks(targets, [args.send], submit=submit, plain=args.plain)
            return 0
        if args.watch:
            return watch_loop(Path(args.watch).expanduser(), targets, submit=submit, plain=args.plain)
        return editor_loop(targets, submit=submit, plain=args.plain)
    except ot.OttyNotAvailable as exc:
        print(f"otty-pad: {exc}", file=sys.stderr)
        return 3
    except ot.OttyError as exc:
        print(f"otty-pad: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
