"""Tests for otty_pad.transport — fully mocked, no real Otty required.

Argv shapes are asserted EXACTLY (a mutation that drops --bracketed-paste,
key:Enter, or -y fails), and the empty-pane guard is a regression anchor for
a live incident where `pane close --pane ""` acted on the FOCUSED pane.
"""
from __future__ import annotations

import json
import types

import pytest

from otty_pad import transport as ot

FAKE_BIN = "/fake/bin/otty"


class Recorder:
    """Scriptable subprocess.run stand-in that records every argv."""

    def __init__(self, outcomes=None):
        self.calls = []
        self.outcomes = list(outcomes or [])

    def __call__(self, argv, capture_output=True, text=True, timeout=None, input=None):
        self.calls.append(list(argv))
        outcome = self.outcomes.pop(0) if self.outcomes else {"ok": True}
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, tuple):
            rc, stdout, stderr = outcome
        else:
            rc, stdout, stderr = 0, json.dumps({"command": "x", "data": outcome, "ok": True}), ""
        return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)


@pytest.fixture()
def fake_bin(monkeypatch):
    monkeypatch.setattr(ot, "resolve_bin", lambda: FAKE_BIN)


# ---------------------------------------------------------------- availability

def test_not_available_raises_and_never_spawns(monkeypatch):
    monkeypatch.setattr(ot, "resolve_bin", lambda: None)
    rec = Recorder()
    with pytest.raises(ot.OttyNotAvailable) as exc:
        ot.send_prompt("p_1", "hello", runner=rec)
    assert rec.calls == []
    assert "not installed" in str(exc.value)


def test_resolve_bin_none_when_nothing_present(monkeypatch):
    monkeypatch.setattr(ot.shutil, "which", lambda _: None)
    monkeypatch.delenv("OTTY_BIN_DIR", raising=False)

    class NoPath:
        def __init__(self, *_a):
            pass

        def exists(self):
            return False

        def __truediv__(self, _other):
            return self

    monkeypatch.setattr(ot, "Path", NoPath)
    assert ot.resolve_bin() is None


def test_inside_otty_env_markers(monkeypatch):
    monkeypatch.setenv("TERM_PROGRAM", "otty")
    monkeypatch.delenv("OTTY_BIN_DIR", raising=False)
    assert ot.inside_otty() is True
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    assert ot.inside_otty() is False
    monkeypatch.setenv("OTTY_BIN_DIR", "/tmp/x")
    assert ot.inside_otty() is True


# ---------------------------------------------------------------- send_prompt

def test_send_prompt_argv_exact(fake_bin):
    rec = Recorder()
    ot.send_prompt("p_19f2_1", "line one\nline two", runner=rec)
    assert rec.calls == [
        [
            FAKE_BIN, "--format", "json",
            "pane", "send-keys", "--pane", "p_19f2_1",
            "--bracketed-paste", "--", "line one\nline two",
        ],
        [
            FAKE_BIN, "--format", "json",
            "pane", "send-keys", "--pane", "p_19f2_1", "--", "key:Enter",
        ],
    ]


def test_send_prompt_no_submit_drops_enter(fake_bin):
    rec = Recorder()
    ot.send_prompt("p_1", "draft", submit=False, runner=rec)
    assert rec.calls[0][-1] == "draft"
    assert "key:Enter" not in rec.calls[0]
    assert len(rec.calls) == 1


def test_send_prompt_unbracketed(fake_bin):
    rec = Recorder()
    ot.send_prompt("p_1", "hi", bracketed=False, runner=rec)
    assert "--bracketed-paste" not in rec.calls[0]


@pytest.mark.parametrize("bad", ["", "   ", None, 42])
def test_pane_ops_refuse_empty_pane_id(fake_bin, bad):
    """Regression anchor: empty --pane falls back to the FOCUSED pane."""
    rec = Recorder()
    for op in (
        lambda: ot.send_prompt(bad, "x", runner=rec),
        lambda: ot.close_pane(bad, runner=rec),
        lambda: ot.capture(bad, runner=rec),
        lambda: ot.badge(bad, "running", runner=rec),
    ):
        with pytest.raises(ot.OttyError):
            op()
    assert rec.calls == []


def test_send_prompt_refuses_empty_text(fake_bin):
    with pytest.raises(ot.OttyError):
        ot.send_prompt("p_1", "", runner=Recorder())


def test_send_keys_disabled_error_carries_remedy(fake_bin):
    rec = Recorder(outcomes=[(1, "", "error: send-keys is disabled. Set `ipc-allow-send-keys = true` in your config to enable it.")])
    with pytest.raises(ot.OttyError) as exc:
        ot.send_prompt("p_1", "hello", runner=rec)
    msg = str(exc.value)
    assert "ipc-allow-send-keys" in msg
    assert "config reload" in msg  # set without reload is a running-app no-op


# ---------------------------------------------------------------- close/capture

def test_close_pane_argv_has_confirm_skip(fake_bin):
    rec = Recorder()
    ot.close_pane("p_9", runner=rec)
    assert rec.calls == [[FAKE_BIN, "--format", "json", "pane", "close", "--pane", "p_9", "-y"]]


def test_capture_is_raw_text_full_screen_by_default(fake_bin):
    rec = Recorder(outcomes=[(0, "screen text here", "")])
    out = ot.capture("p_2", runner=rec)
    assert out == "screen text here"
    argv = rec.calls[0]
    assert "--format" not in argv  # raw text, not json
    assert "--lines" not in argv  # full screen: --lines N is BOTTOM N rows


def test_capture_lines_tail_opt_in(fake_bin):
    rec = Recorder(outcomes=[(0, "tail", "")])
    ot.capture("p_2", lines=40, trim=True, runner=rec)
    argv = rec.calls[0]
    assert argv[argv.index("--lines") + 1] == "40"
    assert "--trim" in argv


# ---------------------------------------------------------------- agent panes

def test_agent_pane_heuristic():
    panes = [
        {"id": "a", "process": "⠐ Fix example build pipeline"},
        {"id": "b", "process": "✳ Recall previous session work"},
        {"id": "c", "process": "opencode: refactor"},
        {"id": "d", "process": "?"},
        {"id": "e", "process": ""},
        {"id": "f", "process": "vim notes.md"},
        {"id": "g", "process": "vim claude-notes.md"},
    ]
    got = [p["id"] for p in ot.agent_panes(panes)]
    assert got == ["a", "b", "c"]


def test_agent_pane_prefers_structured_metadata():
    panes = [
        {"id": "a", "process": "vim claude-notes.md", "agent": "claude"},
        {"id": "b", "process": "plain shell", "harness": "opencode"},
        {"id": "c", "process": "vim claude-notes.md"},
    ]
    got = [p["id"] for p in ot.agent_panes(panes)]
    assert got == ["a", "b"]


# ---------------------------------------------------------------- split

def test_split_discovers_new_pane_id(fake_bin):
    listing_before = [{"id": "p_a"}]
    listing_after = [{"id": "p_a"}, {"id": "p_b"}]
    rec = Recorder(outcomes=[listing_before, {"msg": "Pane split"}, listing_after])
    new_id = ot.split_pane(direction="right", title="pad", size=30, runner=rec,
                           discover_delay=0, discover_tries=1)
    assert new_id == "p_b"
    split_argv = rec.calls[1]
    assert split_argv[3:6] == ["pane", "split", "--direction"]
    assert "--no-focus" in split_argv  # default: never steal focus
    assert "--title" in split_argv


def test_split_prefers_id_from_response(fake_bin):
    rec = Recorder(outcomes=[[{"id": "p_a"}], {"id": "p_new"}])
    new_id = ot.split_pane(runner=rec, discover_delay=0, discover_tries=1)
    assert new_id == "p_new"
    assert len(rec.calls) == 2  # before list + split; no fallback polling needed


def test_split_extracts_nested_pane_id(fake_bin):
    rec = Recorder(outcomes=[[{"id": "p_a"}], {"pane": {"pane_id": "p_nested"}}])
    assert ot.split_pane(runner=rec, discover_delay=0, discover_tries=1) == "p_nested"


def test_split_ignores_existing_or_non_pane_response_id(fake_bin):
    rec = Recorder(outcomes=[
        [{"id": "p_a"}],
        {"id": "p_a", "tab_id": "t_new"},
        [{"id": "p_a"}, {"id": "p_b"}],
    ])
    assert ot.split_pane(runner=rec, discover_delay=0, discover_tries=1) == "p_b"


def test_split_accepts_future_non_prefixed_pane_id(fake_bin):
    rec = Recorder(outcomes=[[{"id": "p_a"}], {"pane_id": "pane-123"}])
    assert ot.split_pane(runner=rec, discover_delay=0, discover_tries=1) == "pane-123"


def test_split_returns_none_when_no_new_pane(fake_bin):
    rec = Recorder(outcomes=[[{"id": "p_a"}], {"msg": "ok"}, [{"id": "p_a"}]])
    assert ot.split_pane(runner=rec, discover_delay=0, discover_tries=1) is None


# ---------------------------------------------------------------- badge/state

def test_badge_valid_kind_and_clear(fake_bin):
    rec = Recorder()
    ot.badge("p_1", "running", runner=rec)
    assert rec.calls[0][-2:] == ["--kind", "running"]
    ot.badge("p_1", clear=True, runner=rec)
    assert rec.calls[1][-1] == "--clear"


def test_badge_invalid_kind_lists_valid(fake_bin):
    with pytest.raises(ot.OttyError) as exc:
        ot.badge("p_1", "sparkles", runner=Recorder())
    assert "awaiting-input" in str(exc.value)


def test_state_report_argv_matches_otty_hook_contract(fake_bin):
    rec = Recorder()
    ot.state_report("claude", session_id="abc", state="processing", runner=rec)
    assert rec.calls == [[FAKE_BIN, "--format", "json", "state:claude",
                          "session-id=abc", "state=processing"]]


def test_state_report_rejects_unknown_kind(fake_bin):
    with pytest.raises(ot.OttyError):
        ot.state_report("cursor", state="idle", runner=Recorder())


# ---------------------------------------------------------------- send-keys cfg

def test_ensure_send_keys_noop_when_enabled(fake_bin):
    rec = Recorder(outcomes=[(0, "true\n", "")])
    assert ot.ensure_send_keys(runner=rec) is False
    assert len(rec.calls) == 1  # only the get


def test_ensure_send_keys_sets_and_reloads_when_disabled(fake_bin):
    rec = Recorder(outcomes=[(0, "false\n", ""), (0, "ok", ""), (0, "Config reloaded", "")])
    assert ot.ensure_send_keys(runner=rec) is True
    joined = [" ".join(c) for c in rec.calls]
    assert any("config set ipc-allow-send-keys true" in c for c in joined)
    assert any("config reload" in c for c in joined)  # set without reload = live no-op
