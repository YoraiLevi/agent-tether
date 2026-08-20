"""The session registry: one JSON file per session.

One file per session rather than a single index, for two reasons:

  - concurrent spawns never contend for a lock. An orchestrator starting six
    agents at once is a normal case, not an edge case.
  - a corrupt file costs you ONE session, not all of them.

Writes are atomic (temp file + os.replace) so a crash mid-write can never leave
a half-written record. A crash is exactly the scenario this data exists for, so
"it was being written when the power went out" is a case that must work.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import paths

SCHEMA_VERSION = 1

#: Environment carried into a tethered session and replanted on restore.
#:
#: These identify the PANE to an orchestrator. Orca's hook scripts silently
#: exit 0 when any of them is missing, so dropping one produces no error at
#: all - just a pane that looks permanently idle.
#:
#: Deliberately NOT vendor session vars such as CLAUDE_CODE_*: those describe
#: the conversation of whoever launched us, and inheriting them would make a
#: new agent believe it is part of its parent's session.
CARRY_PREFIXES = ("ORCA_", "CONDUCTOR_", "GHOSTX_", "TETHER_CARRY_")


@dataclass
class SessionRecord:
    session: str
    agent: str
    binary: str
    cwd: str
    lane: str
    argv: list[str] = field(default_factory=list)
    provider_session_id: str = ""
    env: dict[str, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    schema: int = SCHEMA_VERSION

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SessionRecord:
        known = {f for f in cls.__dataclass_fields__}  # noqa: C416
        return cls(**{k: v for k, v in data.items() if k in known})


def carried_env(env: dict[str, str] | None = None) -> dict[str, str]:
    src = os.environ if env is None else env
    return {k: v for k, v in src.items() if k.startswith(CARRY_PREFIXES)}


def _path_for(session: str) -> Path:
    return paths.sessions_dir() / f"{session}.json"


def save(record: SessionRecord) -> Path:
    paths.sessions_dir().mkdir(parents=True, exist_ok=True)
    target = _path_for(record.session)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record.to_json(), fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)  # atomic on POSIX and on Windows
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def load(session: str) -> SessionRecord | None:
    path = _path_for(session)
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            return SessionRecord.from_json(json.load(fh))
    except Exception:
        # A corrupt record costs one session, never the whole registry.
        return None


def all_records() -> list[SessionRecord]:
    directory = paths.sessions_dir()
    if not directory.is_dir():
        return []
    out: list[SessionRecord] = []
    for path in sorted(directory.glob("*.json")):
        if path.name.startswith(".tmp-"):
            continue
        try:
            with path.open(encoding="utf-8") as fh:
                out.append(SessionRecord.from_json(json.load(fh)))
        except Exception:
            continue
    return out


def corrupt_records() -> list[str]:
    """Files that exist but could not be read - reported by `tether doctor`."""
    directory = paths.sessions_dir()
    if not directory.is_dir():
        return []
    bad: list[str] = []
    for path in sorted(directory.glob("*.json")):
        if path.name.startswith(".tmp-"):
            continue
        try:
            with path.open(encoding="utf-8") as fh:
                json.load(fh)
        except Exception:
            bad.append(str(path))
    return bad


def delete(session: str) -> None:
    _path_for(session).unlink(missing_ok=True)
    (paths.layouts_dir() / f"{session}.kdl").unlink(missing_ok=True)


def find(
    records: list[SessionRecord] | None = None,
    *,
    agent: str | None = None,
    cwd: str | None = None,
    under: str | None = None,
    name_contains: str | None = None,
    session_id: str | None = None,
) -> list[SessionRecord]:
    """Filter sessions the way an orchestrator needs to.

    `cwd` matches one project exactly; `under` matches a whole subtree, which
    is what "show me every agent working in this repo" actually means.
    """
    from .naming import canonical_cwd

    items = all_records() if records is None else records
    if agent:
        items = [r for r in items if r.agent == agent]
    if cwd:
        target = canonical_cwd(cwd)
        items = [r for r in items if canonical_cwd(r.cwd) == target]
    if under:
        root = Path(canonical_cwd(under))
        keep = []
        for r in items:
            try:
                Path(canonical_cwd(r.cwd)).relative_to(root)
                keep.append(r)
            except ValueError:
                continue
        items = keep
    if name_contains:
        needle = name_contains.lower()
        items = [r for r in items if needle in r.session.lower()]
    if session_id:
        items = [r for r in items if r.provider_session_id == session_id or r.session == session_id]
    return items
