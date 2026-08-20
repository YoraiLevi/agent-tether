# Installing

`agent-tether` has **two dependencies it does not bundle**. Install both first.

---

## 1. uv

`uv` is the Python package manager this tool ships through. It also installs and manages the Python interpreter, so you do **not** need Python beforehand.

| OS | Command |
|---|---|
| Linux / macOS | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Windows (PowerShell) | `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| macOS (Homebrew) | `brew install uv` |
| Any (pipx) | `pipx install uv` |

Verify:

```console
$ uv --version
uv 0.9.x
```

If `uv: command not found`, the installer put it in `~/.local/bin` (Linux/macOS) or `%USERPROFILE%\.local\bin` (Windows) and that directory is not on your `PATH`. See [PATH](#path) below.

## 2. zellij

`agent-tether` is a thin layer over zellij. Without it, the shims still run your agent — untethered, with a warning — but nothing is durable.

**Version 0.44.0 or newer is required.** Native Windows support landed in 0.44.0.

| OS | Command |
|---|---|
| Any (Rust) | `cargo install --locked zellij` |
| macOS / Linux (Homebrew) | `brew install zellij` |
| Arch | `pacman -S zellij` |
| Nix | `nix profile install nixpkgs#zellij` |
| Windows | `cargo install --locked zellij` (needs [rustup](https://rustup.rs)) |
| Prebuilt | [github.com/zellij-org/zellij/releases](https://github.com/zellij-org/zellij/releases) |

Verify:

```console
$ zellij --version
zellij 0.44.3
```

---

## 3. agent-tether

**Not yet published to PyPI**, so install from git:

```console
$ uv tool install git+https://github.com/YoraiLevi/agent-tether
```

Once it is on PyPI this will work instead:

```console
$ uv tool install agent-tether        # not available yet
```

Try it without installing anything permanently:

```console
$ uvx --from git+https://github.com/YoraiLevi/agent-tether tether doctor
```

From a local checkout, for development:

```console
$ git clone https://github.com/YoraiLevi/agent-tether && cd agent-tether
$ uv sync --extra dev
$ uv run tether doctor
```

Then generate the shims:

```console
$ tether install --all          # every known agent found on PATH
$ tether install claude codex   # or just these
```

Finally:

```console
$ tether doctor
```

`doctor` is the single command that tells you whether the whole chain works. It exits non-zero if anything is wrong, so it is safe to use in a setup script.

---

## PATH

A shim only works if its directory comes **before** the real binary's directory on `PATH`. `tether install` prints the exact line to add if it isn't there yet.

```console
# Linux / macOS - add to ~/.bashrc, ~/.zshrc or ~/.profile
export PATH="$HOME/.local/share/agent-tether/shims:$PATH"

# Windows - then open a NEW terminal
setx PATH "%LOCALAPPDATA%\..\.local\share\agent-tether\shims;%PATH%"
```

Confirm the shim actually wins:

```console
$ tether doctor
shims
  claude       shadowing       -> /home/you/.local/bin/claude
```

`shadowing` means your `claude` reaches the shim first. `NOT shadowing` means it doesn't, and `doctor` says which file is winning instead.

### Why shims don't live in `~/.local/bin`

Because that is often where the real agent CLIs already live, and **a shim cannot sit in the same directory as the binary it shadows**. On Windows, within one directory, `PATHEXT` resolves `.exe` before `.cmd`, so a `claude.cmd` next to `claude.exe` would never run. `PATH` order only breaks ties *between* directories.

So shims get their own directory under the XDG data root.

---

## Where things go

The same layout on every OS, including Windows and macOS — see [docs/CONFIG.md](CONFIG.md) for why.

| | |
|---|---|
| config + your agent drop-ins | `~/.config/agent-tether/` |
| shims | `~/.local/share/agent-tether/shims/` |
| session registry | `~/.local/state/agent-tether/sessions/` |
| zellij sockets | `~/.local/state/agent-tether/run/sock/` |

`tether paths` prints the resolved values on your machine. All of them are overridable; `TETHER_HOME` relocates everything at once.

---

## Upgrading

```console
$ uv tool upgrade agent-tether
```

Shims do not need regenerating — they are two-line launchers that call the installed entry point, so upgrading the package upgrades every shim. Running sessions are unaffected.

If you moved the install or switched Python versions, re-run `tether install --all` and then `tether doctor`.

---

## Uninstalling

```console
$ tether uninstall --all      # remove the shims
$ uv tool uninstall agent-tether
```

`tether uninstall` only deletes files carrying the `agent-tether-shim` marker; anything else in that directory is reported and left alone.

Running sessions are **not** killed. They are ordinary zellij sessions and keep running. To remove them too:

```console
$ tether reap --all
```

Then remove the shim directory from `PATH`, and delete `~/.config/agent-tether`, `~/.local/share/agent-tether` and `~/.local/state/agent-tether` if you want no trace left.

---

## Troubleshooting

Symptom-first guide: [docs/TROUBLESHOOTING.md](TROUBLESHOOTING.md). Start with `tether doctor` — it checks every failure mode listed there.
