"""Shim generation and the recursion problem.

The recursion problem is the one that can take an agent CLI off the machine: a
file called `claude` on PATH must find the OTHER file called `claude`. If it
finds itself, the shim calls itself forever.
"""

from __future__ import annotations

import os
import stat

import pytest

from tether import paths, resolve, shims


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.HOME_ENV, str(tmp_path))
    paths.ensure_dirs()
    return tmp_path


def make_fake_binary(directory, name: str) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (f"{name}.cmd" if os.name == "nt" else name)
    path.write_text("#!/bin/sh\necho real\n", encoding="utf-8")
    if os.name != "nt":
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------


def test_generated_shim_carries_the_marker(isolated):
    result = shims.install("claude")
    assert result.action == "created"
    assert shims.is_shim(result.path)
    assert resolve.MARKER in result.path.read_text(encoding="utf-8")


def test_shim_is_executable_on_posix(isolated):
    result = shims.install("claude")
    if os.name != "nt":
        assert os.access(result.path, os.X_OK)


def test_reinstall_is_idempotent(isolated):
    shims.install("claude")
    assert shims.install("claude").action == "unchanged"


def test_install_never_clobbers_a_foreign_file(isolated):
    target = shims.shim_path("claude")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("someone else's file", encoding="utf-8")
    result = shims.install("claude")
    assert result.action == "skipped"
    assert target.read_text(encoding="utf-8") == "someone else's file"


def test_uninstall_never_deletes_a_foreign_file(isolated):
    target = shims.shim_path("claude")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("not ours", encoding="utf-8")
    assert shims.uninstall("claude").action == "skipped"
    assert target.exists()


def test_uninstall_removes_our_own(isolated):
    shims.install("claude")
    assert shims.uninstall("claude").action == "removed"
    assert not shims.shim_path("claude").exists()


def test_installed_lists_only_our_shims(isolated):
    shims.install("claude")
    (paths.bin_dir() / "unrelated.txt").write_text("hi", encoding="utf-8")
    assert shims.installed() == ["claude"]


def test_shim_body_quotes_the_entry_point(isolated, monkeypatch):
    monkeypatch.setenv("TETHER_SHIM_ENTRY", r"C:\Program Files\x\tether-shim.exe")
    body = shims.install("claude").path.read_text(encoding="utf-8")
    assert '"C:\\Program Files\\x\\tether-shim.exe"' in body


# --------------------------------------------------------------------------
# resolution / recursion
# --------------------------------------------------------------------------


def test_resolve_skips_our_shim_and_finds_the_real_binary(isolated, monkeypatch):
    real_dir = isolated / "realbin"
    real = make_fake_binary(real_dir, "claude")
    shims.install("claude")
    monkeypatch.setenv("PATH", os.pathsep.join([str(paths.bin_dir()), str(real_dir)]))
    assert resolve.next_binary("claude") == real


def test_resolve_chains_past_a_foreign_shim_to_the_real_binary(isolated, monkeypatch):
    """Another tool's shim must remain reachable THROUGH ours."""
    other_dir = isolated / "othertool"
    other = make_fake_binary(other_dir, "claude")
    shims.install("claude")
    monkeypatch.setenv("PATH", os.pathsep.join([str(paths.bin_dir()), str(other_dir)]))
    # Their shim is not ours, so we hand off to it rather than skipping it.
    assert resolve.next_binary("claude") == other


def test_env_override_wins(isolated, monkeypatch, tmp_path):
    target = make_fake_binary(tmp_path / "custom", "claude")
    monkeypatch.setenv("TETHER_TARGET_CLAUDE", target)
    assert resolve.next_binary("claude") == target


def test_env_override_pointing_nowhere_is_a_clear_error(isolated, monkeypatch):
    monkeypatch.setenv("TETHER_TARGET_CLAUDE", str(isolated / "nope"))
    with pytest.raises(resolve.BinaryNotFound) as exc:
        resolve.next_binary("claude")
    assert "TETHER_TARGET_CLAUDE" in str(exc.value)


def test_nothing_but_our_own_shim_raises_rather_than_looping(isolated, monkeypatch):
    shims.install("claude")
    monkeypatch.setenv("PATH", str(paths.bin_dir()))
    with pytest.raises(resolve.BinaryNotFound):
        resolve.next_binary("claude")


def test_dash_is_normalised_in_the_env_key(isolated, monkeypatch, tmp_path):
    target = make_fake_binary(tmp_path / "c2", "mimo-code")
    monkeypatch.setenv("TETHER_TARGET_MIMO_CODE", target)
    assert resolve.next_binary("mimo-code") == target


def test_on_path_detection(isolated, monkeypatch):
    monkeypatch.setenv("PATH", str(paths.bin_dir()))
    assert shims.on_path()
    monkeypatch.setenv("PATH", str(isolated / "elsewhere"))
    assert not shims.on_path()
