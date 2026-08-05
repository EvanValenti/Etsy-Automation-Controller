"""Shared, read-only filesystem helpers used by each concrete EngineAdapter's
`discover()` to ground capability reporting in the real sibling engine
repo's current on-disk state, rather than hardcoded/remembered values.

Design spec Section 4 / Step 2 objective: "The Engine Registry should
gather information exclusively through the Adapter interface. The
Controller must never hardcode engine capabilities." These helpers
perform only read-only filesystem/text inspection — never an import,
never executing any code from a sibling repo, and never writing
anything. A missing or restructured sibling repo must degrade to a
conservative "unknown/false" capability report, never raise.
"""

from __future__ import annotations

import os
from pathlib import Path

# This file lives at automation-controller/infra/adapters/discovery_utils.py.
# parents[0] = infra/adapters, [1] = infra, [2] = automation-controller,
# [3] = the workspace root (sibling of automation-controller, etsy-video-generator,
# Etsy-AI-Image-Generator, etsy-mockup-generator, etc.).
_DEFAULT_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]

WORKSPACE_ROOT = Path(
    os.environ.get("AUTOMATION_CONTROLLER_ENGINES_ROOT", str(_DEFAULT_WORKSPACE_ROOT))
)


def resolve_engine_repo_root(repo_dir_name: str) -> Path | None:
    """Return the sibling engine repo's directory if it exists on disk,
    else None. Never raises — a missing/relocated sibling repo is an
    expected, reportable discovery outcome (health_status="unreachable"),
    not a crash.
    """
    candidate = WORKSPACE_ROOT / repo_dir_name
    return candidate if candidate.is_dir() else None


def read_text_safely(path: Path | None) -> str | None:
    """Read a file's text if it exists and is readable; return None on any
    failure (missing file, permission error, decode error) rather than
    raising — discover() must never crash due to a sibling repo's state.
    """
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
