"""Session naming.

Three schemes, one per lane, because they answer different questions:

  id present  the caller already named the conversation. Derive from it, so a
              vendor-issued `--resume <id>` reattaches to the SAME tether
              session instead of forking a second one for one conversation.

  human       per directory. `claude` in a project is then idempotent: you
              always return to that project's agent. This is the single
              property that makes the daily loop feel like nothing happened.

  agent       fresh uuid. An orchestrator may want six agents in one repo, and
              they must not collide.

Session names become file names (zellij writes one socket marker per session),
so they are restricted to a conservative charset and a bounded length.
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
import uuid
from pathlib import Path

PREFIX = "tether"
MAX_NAME = 64

_SAFE = re.compile(r"[^a-z0-9]+")


def canonical_cwd(cwd: str | os.PathLike[str]) -> str:
    """A stable key for "this project".

    resolve() follows symlinks and normalises case-insensitive drive letters,
    so /home/me/proj and a symlink to it, or C:\\Proj and c:\\proj, map to one
    session instead of silently becoming two.
    """
    try:
        return str(Path(cwd).resolve())
    except OSError:
        return str(Path(cwd).absolute())


def slug(text: str, limit: int = 24) -> str:
    """Lowercase ASCII slug. Non-ASCII project names are common and must work."""
    norm = unicodedata.normalize("NFKD", text)
    ascii_only = norm.encode("ascii", "ignore").decode("ascii")
    out = _SAFE.sub("-", ascii_only).strip("-").lower()
    if len(out) > limit:
        out = out[:limit].strip("-")
    return out or "x"


def short_hash(text: str, length: int = 8) -> str:
    """Case-insensitive digest.

    Case folding matters: on Windows and macOS the same directory can be
    spelled with different case, and two names for one project would create
    two sessions.
    """
    return hashlib.sha256(text.lower().encode("utf-8")).hexdigest()[:length]


def for_session_id(agent: str, session_id: str) -> str:
    return _clip(f"{PREFIX}-{agent}-{short_hash(session_id, 12)}")


def for_cwd(agent: str, cwd: str) -> str:
    canon = canonical_cwd(cwd)
    leaf = Path(canon).name or "root"
    return _clip(f"{PREFIX}-{agent}-{slug(leaf)}-{short_hash(canon, 8)}")


def for_new(agent: str) -> str:
    return _clip(f"{PREFIX}-{agent}-{uuid.uuid4().hex[:12]}")


def new_session_id() -> str:
    """A vendor session id we choose ourselves.

    Plain UUID4: claude and gemini require a valid UUID, and pi's charset is a
    superset of it, so one format satisfies every vendor that can be told.
    """
    return str(uuid.uuid4())


def _clip(name: str) -> str:
    if len(name) <= MAX_NAME:
        return name
    keep = MAX_NAME - 9
    return f"{name[:keep]}-{short_hash(name, 8)}"


def is_tether_session(name: str) -> bool:
    return name.startswith(f"{PREFIX}-")
