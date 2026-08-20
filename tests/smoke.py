"""End-to-end smoke test of the real shim, on any OS, with no zellij required.

This is the test that would have caught the two worst bugs found by hand:
a shim that mangles its arguments, and a shim that fails to find the real
binary and calls itself instead.

It builds a fake "agent" that simply echoes its argv as JSON, shims it, then
compares what the fake agent saw through the shim against what it saw when
invoked directly. Anything other than an exact match is a fidelity bug.

Run it directly:  python -m tests.smoke
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

WINDOWS = os.name == "nt"

FAKE_AGENT = """\
import json, sys
print(json.dumps({"argv": sys.argv[1:]}))
sys.exit(7 if "--fail" in sys.argv else 0)
"""

CASES: list[list[str]] = [
    ["--version"],
    ["-p", "hello world"],
    ["-p", 'quotes "inside" here'],
    ["-p", "trailing backslash\\"],
    ["-p", "semi;colon & amp | pipe"],
    ["-p", "unicode: café проект"],
    ["-p", ""],
    ["-p", "a b  c"],
    ["--print", "--", "-p", "not-a-flag"],
    ["--fail", "-p", "x"],
]


def build_fake_agent(root: Path, name: str) -> Path:
    """A stand-in vendor CLI that reports exactly what argv it received."""
    script = root / f"{name}_impl.py"
    script.write_text(FAKE_AGENT, encoding="utf-8")
    real_dir = root / "realbin"
    real_dir.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        launcher = real_dir / f"{name}.cmd"
        launcher.write_text(
            f'@echo off\r\n"{sys.executable}" "{script}" %*\r\nexit /b %ERRORLEVEL%\r\n',
            encoding="utf-8",
            newline="",
        )
    else:
        launcher = real_dir / name
        launcher.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8"
        )
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return real_dir


def run(cmd: list[str], env: dict[str, str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout.strip()


def main() -> int:
    name = "fakeagent"
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        home = root / "tetherhome"
        real_dir = build_fake_agent(root, name)

        env = dict(os.environ)
        env["TETHER_HOME"] = str(home)
        env["TETHER_QUIET"] = "1"
        env.pop("TETHER_DISABLE", None)
        env.pop("ZELLIJ_SESSION_NAME", None)
        env.pop("ZELLIJ", None)

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from tether import paths, shims  # noqa: E402

        os.environ["TETHER_HOME"] = str(home)
        paths.ensure_dirs()

        # Teach the registry about our fake agent, purely by dropping a file -
        # exactly the extension path a user would take.
        (paths.agents_dropin_dir() / f"{name}.toml").write_text(
            'schema = 1\n[agent]\nname = "fakeagent"\n'
            'headless_flags = ["-p", "--print"]\n'
            'info_flags = ["--version", "-h", "--help"]\n',
            encoding="utf-8",
        )

        entry = shutil.which("tether-shim") or f'"{sys.executable}" -m tether.shim'
        result = shims.install(name, paths.bin_dir(), entry=entry)
        if result.action == "skipped":
            print(f"FAIL: could not install shim: {result.detail}")
            return 1

        env["PATH"] = os.pathsep.join([str(paths.bin_dir()), str(real_dir), env.get("PATH", "")])
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

        shim_exe = str(shims.shim_path(name, paths.bin_dir()))
        real_exe = str(real_dir / (f"{name}.cmd" if WINDOWS else name))

        for case in CASES:
            want_rc, want_out = run([real_exe, *case], env)
            got_rc, got_out = run([shim_exe, *case], env)

            if want_rc != got_rc:
                failures.append(f"exit code differs for {case}: real={want_rc} shim={got_rc}")
            if want_out != got_out:
                failures.append(f"argv differs for {case}:\n  real={want_out}\n  shim={got_out}")

        # stdout must stay clean enough to parse
        rc, out = run([shim_exe, "-p", "x"], env)
        try:
            json.loads(out)
        except Exception:
            failures.append(f"shim polluted stdout: {out!r}")

        # TETHER_DISABLE must be a true bypass
        env2 = dict(env)
        env2["TETHER_DISABLE"] = "1"
        rc, out = run([shim_exe, "--version"], env2)
        if rc != 0:
            failures.append(f"TETHER_DISABLE bypass failed: rc={rc} out={out!r}")

    if failures:
        # Encoding-safe: the cases deliberately include non-ASCII, and a
        # UnicodeEncodeError in the REPORTER hides the failure it is reporting.
        out = sys.stdout
        enc = getattr(out, "encoding", None) or "utf-8"
        print(f"SMOKE FAILED ({len(failures)})")
        for f in failures:
            safe = f.encode(enc, "backslashreplace").decode(enc)
            out.write(f"  - {safe}\n")
        return 1

    print(f"smoke ok: {len(CASES)} argv cases passed through byte-identically")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
