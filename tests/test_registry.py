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
