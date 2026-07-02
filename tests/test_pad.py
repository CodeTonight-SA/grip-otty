"""Tests for otty_pad.pad — pure parsing + watch semantics, no real Otty.

The safety property under test: an autosave of a HALF-TYPED prompt must never
send (watch mode ships only `---`-terminated blocks), and pad chrome/receipt
lines (`#>`) must never reach an agent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from otty_pad import pad


# ---------------------------------------------------------------- parsing

def test_parse_blocks_strips_chrome_and_splits():
    text = (
        "#> otty-pad → p_1\n"
        "#> chrome line\n"
        "first prompt line one\n"
        "line two\n"
        "---\n"
        "second prompt\n"
        "---\n"
        "#> ✓ receipt never sends\n"
        "   \n"
    )
    assert pad.parse_blocks(text) == ["first prompt line one\nline two", "second prompt"]


def test_parse_blocks_trailing_block_without_separator_sends_in_editor_mode():
    assert pad.parse_blocks("just one prompt, no separator") == ["just one prompt, no separator"]


def test_parse_blocks_empty_and_chrome_only_is_nothing():
    assert pad.parse_blocks("#> header\n\n   \n") == []


def test_separator_inside_text_needs_exact_line():
    text = "prompt with --- inline dashes stays whole"
    assert pad.parse_blocks(text) == [text]


# ---------------------------------------------------------------- watch mode

def test_extract_complete_requires_terminator():
    blocks, consumed = pad.extract_complete("half a thought being typed")
    assert blocks == [] and consumed == 0


def test_extract_complete_ships_terminated_keeps_pending():
    pending = "done block\n---\nstill typing this one"
    blocks, consumed = pad.extract_complete(pending)
    assert blocks == ["done block"]
    assert pending[consumed:] == "still typing this one"


def test_extract_complete_multiple_blocks_last_terminator_wins():
    pending = "a\n---\nb\n---\ntail"
    blocks, consumed = pad.extract_complete(pending)
    assert blocks == ["a", "b"]
    assert pending[consumed:] == "tail"


def test_watch_loop_ships_on_terminator_and_appends_receipts(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(pad.ot, "send_prompt", lambda pane, text, submit=True: sent.append((pane, text, submit)))
    monkeypatch.setattr(pad.time, "sleep", lambda _s: None)
    f = tmp_path / "notes.md"
    f.write_text("", encoding="utf-8")

    calls = {"n": 0}
    real_stat = Path.stat

    def scripted_stat(self, **kw):
        if self == f and calls["n"] == 1 and not f.read_text().startswith("ship"):
            f.write_text("ship me\n---\nhalf typed", encoding="utf-8")
        calls["n"] += 1
        return real_stat(self, **kw)

    monkeypatch.setattr(Path, "stat", scripted_stat)
    pad.watch_loop(f, ["p_9"], submit=True, poll=0, max_loops=3)

    assert sent == [("p_9", "ship me", True)]
    content = f.read_text()
    assert "#> ✓" in content            # receipt appended
    assert "half typed" in content      # pending text untouched


def test_receipt_shape():
    line = pad.receipt("p_1", "hello world " * 10)
    assert line.startswith("#> ✓ ")
    assert "p_1" in line and "chars" in line


# ---------------------------------------------------------------- dispatch

def test_send_blocks_fans_out_targets(monkeypatch):
    sent = []
    monkeypatch.setattr(pad.ot, "send_prompt", lambda pane, text, submit=True: sent.append((pane, text, submit)))
    receipts = pad.send_blocks(["p_1", "p_2"], ["alpha", "beta"], submit=False)
    assert sent == [("p_1", "alpha", False), ("p_2", "alpha", False),
                    ("p_1", "beta", False), ("p_2", "beta", False)]
    assert len(receipts) == 4


def test_resolve_editor_honours_multiword(monkeypatch):
    monkeypatch.setenv("VISUAL", "code -w")
    assert pad.resolve_editor() == ["code", "-w"]
    monkeypatch.delenv("VISUAL")
    monkeypatch.setenv("EDITOR", "nvim")
    assert pad.resolve_editor() == ["nvim"]


def test_state_dir_honours_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert pad._state_dir() == tmp_path / "xdg" / "otty-pad"
    monkeypatch.delenv("XDG_STATE_HOME")
    assert pad._state_dir() == Path.home() / ".local" / "state" / "otty-pad"


def test_main_exits_3_without_otty(monkeypatch, capsys):
    monkeypatch.setattr(pad.ot, "is_available", lambda: False)
    rc = pad.main(["--send", "x", "--target", "p_1"])
    assert rc == 3
    err = capsys.readouterr().err
    assert "waitlist" in err  # plain-language why, matching Otty's platform status


def test_main_send_one_shot(monkeypatch):
    sent = []
    monkeypatch.setattr(pad.ot, "is_available", lambda: True)
    monkeypatch.setattr(pad.ot, "send_prompt", lambda pane, text, submit=True: sent.append((pane, text, submit)))
    rc = pad.main(["--send", "run the tests", "--target", "p_7"])
    assert rc == 0
    assert sent == [("p_7", "run the tests", True)]


def test_main_all_requires_agent_panes(monkeypatch, capsys):
    monkeypatch.setattr(pad.ot, "is_available", lambda: True)
    monkeypatch.setattr(pad.ot, "agent_panes", lambda panes=None: [])
    rc = pad.main(["--send", "x", "--all"])
    assert rc == 1
    assert "no agent panes" in capsys.readouterr().err
