"""The pluggable registry.

The property under test throughout: a user must be able to add or amend an
agent by dropping a file, and a BROKEN file must never break the shim path.
"""

from __future__ import annotations

import pytest

from tether import paths, registry


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.HOME_ENV, str(tmp_path))
    paths.ensure_dirs()
    return tmp_path


def dropin(text: str, name: str = "custom.toml") -> None:
    (paths.agents_dropin_dir() / name).write_text(text, encoding="utf-8")


def test_new_agent_needs_no_code_change(isolated):
    dropin(
        """
        schema = 1
        [agent]
        name = "brandnew"
        headless_flags = ["--oneshot"]
        resume_flag = "--pick-up"
        set_id_flag = "--id"
        """
    )
    reg = registry.load()
    assert "brandnew" in reg.names()
    a = reg.agents["brandnew"]
    assert a.can_choose_id and a.can_resume
    assert a.resume_argv("xyz") == ["--pick-up", "xyz"]


def test_dropin_overrides_one_field_and_inherits_the_rest(isolated):
    dropin(
        """
        schema = 1
        [agent]
        name = "claude"
        binary = "/opt/custom/claude"
        """,
        "claude.toml",
    )
    reg = registry.load()
    claude = reg.agents["claude"]
    assert claude.binary == "/opt/custom/claude"
    # inherited from the builtin, not lost by the override
    assert claude.set_id_flag == "--session-id"
    assert "-p" in claude.headless_flags
    assert "builtin" in claude.source and "user" in claude.source


def test_malformed_toml_is_reported_not_raised(isolated):
    dropin("this is not [valid toml", "broken.toml")
    reg = registry.load()  # must not raise
    assert any("broken.toml" in path for path, _ in reg.errors)
    # and the rest of the registry still works
    assert "claude" in reg.names()


def test_wrong_type_is_reported_not_raised(isolated):
    dropin(
        """
        schema = 1
        [agent]
        name = "bad"
        headless_flags = "should-be-a-list"
        """,
        "bad.toml",
    )
    reg = registry.load()
    assert any("bad" in msg or "bad.toml" in path for path, msg in reg.errors)


def test_future_schema_is_skipped_not_misread(isolated):
    dropin(
        """
        schema = 999
        [agent]
        name = "future"
        resume_flag = "--who-knows"
        """,
        "future.toml",
    )
    reg = registry.load()
    assert "future" not in reg.agents
    assert any("999" in msg for _, msg in reg.errors)


def test_env_override_beats_files(isolated, monkeypatch):
    monkeypatch.setenv("TETHER_TARGET_CLAUDE", "/env/claude")
    reg = registry.load()
    assert reg.agents["claude"].binary == "/env/claude"


def test_unknown_agent_falls_back_instead_of_raising(isolated):
    reg = registry.load()
    a = reg.get("never-heard-of-it")
    assert a.name == "never-heard-of-it"
    assert a.binary == "never-heard-of-it"
    assert a.source == "fallback"
    assert not a.can_choose_id


def test_disabled_agent_is_hidden(isolated):
    dropin(
        """
        schema = 1
        [agent]
        name = "claude"
        enabled = false
        """,
        "claude.toml",
    )
    reg = registry.load()
    assert "claude" not in reg.names()


def test_resume_argv_prefers_subcommand_form(isolated):
    reg = registry.load()
    assert reg.agents["codex"].resume_argv("abc") == ["resume", "abc"]
    assert reg.agents["claude"].resume_argv("abc") == ["--resume", "abc"]


def test_no_session_id_means_no_resume_argv(isolated):
    reg = registry.load()
    assert reg.agents["claude"].resume_argv("") is None


# --------------------------------------------------------------------------
# Regressions found by review. Each of these failed before the fix, and each
# failed SILENTLY, which is why they are pinned here.
# --------------------------------------------------------------------------


def test_dropin_can_clear_a_builtin_list(isolated):
    """Overriding a field back TO its default must work.

    Merging used to infer "was this set?" by comparing against the default, so
    clearing a list was indistinguishable from not mentioning it - and was
    silently ignored.
    """
    dropin(
        """
        schema = 1
        [agent]
        name = "claude"
        headless_flags = []
        """,
        "claude.toml",
    )
    reg = registry.load()
    assert reg.agents["claude"].headless_flags == []
    assert reg.agents["claude"].set_id_flag == "--session-id"  # still inherited


def test_dropin_can_set_a_bool_back_to_false(isolated):
    dropin(
        """
        schema = 1
        [agent]
        name = "claude"
        verified = false
        """,
        "claude.toml",
    )
    assert registry.load().agents["claude"].verified is False


def test_resume_argv_never_emits_a_set_id_flag(isolated):
    """grok's --session-id is launch-only: its own help says it does not resume.

    Emitting it on restore would create a NEW empty conversation while
    reporting success - the exact failure this project exists to prevent.
    """
    reg = registry.load()
    grok = reg.agents["grok"]
    argv = grok.resume_argv("abc123")
    assert argv == ["--resume", "abc123"]
    assert grok.set_id_flag not in argv
    for name, agent in reg.agents.items():
        argv = agent.resume_argv("x") or []
        for flag in agent.all_set_id_flags:
            assert flag not in argv, f"{name} would replay a set-id flag on restore"


def test_env_override_reaches_unknown_agents_too(isolated, monkeypatch):
    monkeypatch.setenv("TETHER_TARGET_NEVERHEARDOF", "/custom/bin")
    agent = registry.load().get("neverheardof")
    assert agent.binary == "/custom/bin"
    assert "env" in agent.source


def test_no_subcommand_is_both_management_and_headless(isolated):
    """Listing a subcommand in both places makes one of the rules unreachable.

    Both branches route to pass-through, so it is not a behaviour bug - but it
    is a data contradiction, and the next person editing the row cannot tell
    which list is authoritative.

    Note the analogous check for FLAGS would be wrong: grok's `-p` really is
    both headless AND value-taking (`-p <PROMPT>`), so that overlap is accurate
    data rather than a contradiction.
    """
    reg = registry.load()
    for name, a in reg.agents.items():
        overlap = set(a.subcommands) & set(a.headless_subcommands)
        assert not overlap, f"{name}: {overlap} listed as both management and headless"


def test_interactive_flags_never_duplicate_headless_flags(isolated):
    """These two lists have opposite meanings; a flag in both is ambiguous."""
    reg = registry.load()
    for name, a in reg.agents.items():
        clash = set(a.interactive_flags) & set(a.headless_flags)
        assert not clash, f"{name}: {clash} is declared both interactive and headless"
