"""Shared runtime root -- where completed job artifacts get published so
multiple independently-cloned copies of this repo (e.g. a desktop and a
laptop, each with their own local Git clone) can see the same finished
work through a synced folder (OneDrive, in practice, but this module
never assumes that specifically).

WHAT LIVES HERE vs. WHAT DOESN'T
Source code stays in each machine's own Git clone -- this module has
nothing to do with that. Only STABLE, COMPLETED runtime artifacts (see
infra/runtime_publisher.py) ever get copied into the path this module
resolves. Active working directories, temp extraction folders, and
in-progress jobs are never pointed here.

CONFIGURATION
VILICITY_RUNTIME_ROOT, if set, is used verbatim (expanduser()'d, so
"~/OneDrive/Vilicity Runtime" works). This is the ONE mechanism -- no
second config file, no hardcoded per-user path in production logic.

Absent that, the default is a LOCAL, per-repo folder
(var/vilicity_runtime/, already covered by .gitignore's blanket `var/`
rule) -- a safe no-op default that behaves like a single-machine setup
until an operator opts into sharing by setting the env var. This mirrors
infra/listing_workspace.py's WORKSPACES_ROOT pattern (also var/-rooted,
also never hardcoded to a specific user's machine).

To actually share runtime state between a desktop and a laptop, set
VILICITY_RUNTIME_ROOT to the same OneDrive-backed path on both machines,
e.g. (Windows, PowerShell):
    setx VILICITY_RUNTIME_ROOT "%USERPROFILE%\\OneDrive\\Vilicity Runtime"
See docs/shared-runtime.md for the full setup walkthrough.
"""

from __future__ import annotations

import os
import platform
import re
from pathlib import Path

_ROOT_ENV_VAR = "VILICITY_RUNTIME_ROOT"
_MACHINE_ID_ENV_VAR = "VILICITY_MACHINE_ID"

_DEFAULT_RUNTIME_ROOT = Path(__file__).resolve().parent.parent / "var" / "vilicity_runtime"


def resolve_runtime_root() -> Path:
    """The configured shared runtime root, or the safe local default.

    Never raises for a missing/unreachable path -- callers (the publisher
    and the index reader) are responsible for handling "the root doesn't
    exist yet" and "the root exists but OneDrive hasn't finished syncing
    into it," both of which are normal, expected states, not errors.
    """
    raw = os.environ.get(_ROOT_ENV_VAR)
    if raw and raw.strip():
        return Path(raw.strip()).expanduser()
    return _DEFAULT_RUNTIME_ROOT


def is_using_configured_root() -> bool:
    """True if VILICITY_RUNTIME_ROOT is actually set -- i.e. this machine
    has opted into a shared root rather than the local-only default.
    Purely informational (for diagnostics/logging), never gates behavior:
    the local default is fully functional on its own, just not shared."""
    raw = os.environ.get(_ROOT_ENV_VAR)
    return bool(raw and raw.strip())


_SAFE_MACHINE_ID_CHARS = re.compile(r"[^A-Za-z0-9_-]+")


def machine_id() -> str:
    """A short, filesystem-safe identifier for THIS computer, used to keep
    two machines' otherwise-identical local identifiers (e.g. the Mockup
    Generator's run_id, which is only sequential-per-machine-per-day, see
    batch_generate.py's make_run_id()) from colliding when both publish
    into the same shared root.

    VILICITY_MACHINE_ID overrides the OS hostname for anyone who wants a
    friendlier or more stable name than platform.node() provides (a
    hostname can change; this env var, once set, won't). Falls back to
    "unknown-machine" in the (rare) case platform.node() itself returns
    nothing, rather than ever producing an empty/unsafe path segment.
    """
    raw = os.environ.get(_MACHINE_ID_ENV_VAR) or platform.node() or "unknown-machine"
    safe = _SAFE_MACHINE_ID_CHARS.sub("-", raw.strip()).strip("-")
    return safe or "unknown-machine"
