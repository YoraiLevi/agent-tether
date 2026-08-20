"""Lane routing.

These are the highest-value tests in the suite. A routing mistake is SILENT:
wrongly tethering a headless call gives a terminal UI to something parsing
stdout, and nothing errors. Every case below corresponds to a real vendor
quirk, not a hypothetical.
"""

from __future__ import annotations

import pytest

from tether.registry import Agent, load
from tether.router import Lane, classify, provided_session_id

NO_TTY: dict[str, str] = {}


def agent(**kw) -> Agent:
    base = dict(
        name="x",
        info_flags=["-h", "--help", "--version"],
    )
    base.update(kw)
    return Agent(**base)


# --------------------------------------------------------------------------
# the asymmetry that defines the design
# --------------------------------------------------------------------------


def test_unknown_agent_with_no_tty_is_agent_lane_not_crash():
    a = agent()
    assert classify(a, ["whatever"], env=NO_TTY, tty=False).lane is Lane.AGENT


def test_info_flags_always_pass_through_even_with_a_tty():
    a = agent()
    for flag in ("--help", "-h", "--version"):
        assert classify(a, [flag], env=NO_TTY, tty=True).lane is Lane.PASSTHROUGH


def test_already_inside_zellij_never_nests():
    a = agent()
    d = classify(a, [], env={"ZELLIJ_SESSION_NAME": "s"}, tty=True)
    assert d.lane is Lane.PASSTHROUGH


def test_recursion_guard():
    a = agent()
    assert classify(a, [], env={"TETHER_ACTIVE": "1"}, tty=True).lane is Lane.PASSTHROUGH


def test_disable_escape_hatch():
    a = agent()
    assert classify(a, [], env={"TETHER_DISABLE": "1"}, tty=True).lane is Lane.PASSTHROUGH


# --------------------------------------------------------------------------
# per-vendor quirks: '-p' does NOT universally mean print
# --------------------------------------------------------------------------


def test_claude_dash_p_is_headless():
    a = agent(headless_flags=["-p", "--print"])
    assert classify(a, ["-p", "hello"], env=NO_TTY, tty=True).lane is Lane.PASSTHROUGH


def test_codex_dash_p_is_profile_and_must_still_tether():
    """codex -p is --profile. Treating it as print would untether real work."""
    a = agent(headless_flags=[], value_flags=["-p", "--profile"])
    assert classify(a, ["-p", "myprofile"], env=NO_TTY, tty=True).lane is Lane.HUMAN


def test_codex_exec_subcommand_is_headless():
    a = agent(headless_subcommands=["exec", "review"])
    assert classify(a, ["exec", "do a thing"], env=NO_TTY, tty=True).lane is Lane.PASSTHROUGH


def test_codex_resume_is_a_session_start_not_management():
    a = agent(subcommands=["login", "resume"], session_start_subcommands=["resume"])
    assert classify(a, ["resume", "abc"], env=NO_TTY, tty=True).lane is Lane.HUMAN


def test_management_subcommand_passes_through():
    a = agent(subcommands=["mcp", "login"])
    assert classify(a, ["mcp", "list"], env=NO_TTY, tty=True).lane is Lane.PASSTHROUGH


def test_agy_interactive_flag_beats_headless_flag():
    """agy -i/--prompt-interactive LOOKS headless and is not."""
    a = agent(
        headless_flags=["-p", "--print", "--prompt"],
        interactive_flags=["-i", "--prompt-interactive"],
    )
    assert classify(a, ["-i", "--prompt", "hi"], env=NO_TTY, tty=True).lane is Lane.HUMAN


def test_value_of_a_flag_is_not_mistaken_for_a_subcommand():
    """`claude --model exec` must not be read as the `exec` subcommand."""
    a = agent(headless_subcommands=["exec"], value_flags=["--model"])
    assert classify(a, ["--model", "exec"], env=NO_TTY, tty=True).lane is Lane.HUMAN


def test_equals_form_of_headless_flag_is_detected():
    a = agent(headless_flags=["--print"])
    assert classify(a, ["--print=json"], env=NO_TTY, tty=True).lane is Lane.PASSTHROUGH


# --------------------------------------------------------------------------
# lanes
# --------------------------------------------------------------------------


def test_tty_means_human_no_tty_means_agent():
    a = agent()
    assert classify(a, [], env=NO_TTY, tty=True).lane is Lane.HUMAN
    assert classify(a, [], env=NO_TTY, tty=False).lane is Lane.AGENT


def test_background_flag_is_intercepted_into_agent_lane():
    a = agent(background_flags=["--bg"])
    assert classify(a, ["--bg"], env=NO_TTY, tty=True).lane is Lane.AGENT


def test_force_lane_overrides_everything():
    a = agent()
    d = classify(a, [], env={"TETHER_FORCE_LANE": "agent"}, tty=True)
    assert d.lane is Lane.AGENT


# --------------------------------------------------------------------------
# session id extraction
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["--resume", "abc123"], "abc123"),
        (["--session-id", "u-1"], "u-1"),
        (["-r", "abc"], "abc"),
        (["--resume=abc"], "abc"),
        (["--resume"], None),
        (["--resume", "--model"], None),
        ([], None),
    ],
)
def test_provided_session_id(argv, expected):
    a = agent(resume_flag="--resume", resume_aliases=["-r"], set_id_flag="--session-id")
    assert provided_session_id(a, argv) == expected


def test_bare_subcommand_resume_id():
    a = agent(resume_subcommand="resume")
    assert provided_session_id(a, ["resume", "sess-9"]) == "sess-9"


# --------------------------------------------------------------------------
# the shipped registry must actually encode these quirks
# --------------------------------------------------------------------------


def test_builtin_registry_encodes_the_codex_dash_p_trap():
    reg = load()
    codex = reg.agents["codex"]
    assert "-p" not in codex.headless_flags, "codex -p is --profile, not --print"
    assert "exec" in codex.headless_subcommands
    assert "resume" in codex.session_start_subcommands


def test_builtin_registry_marks_grok_set_id_launch_only():
    reg = load()
    grok = reg.agents["grok"]
    assert grok.set_id_launch_only is True


def test_only_expected_vendors_can_choose_an_id():
    reg = load()
    can = {n for n, a in reg.agents.items() if a.can_choose_id}
    assert can == {"claude", "grok", "gemini", "pi"}


def test_every_builtin_agent_parses_without_errors():
    reg = load()
    assert reg.errors == []
    assert len(reg.names()) >= 11
