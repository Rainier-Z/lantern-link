"""Application version metadata and the runtime source revision."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


APP_VERSION = "0.3.2"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_GIT_SHA_PATTERN = re.compile(r"[0-9a-fA-F]{4,40}")


def resolve_build() -> str:
    """Return the current short Git revision, or ``unknown`` if unavailable.

    The revision is resolved at process startup instead of being copied into
    source by hand.  Only the validated revision is exposed to the API; Git's
    stderr and local paths never become part of the public response.
    """

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_PROJECT_ROOT,
            capture_output=True,
            check=True,
            text=True,
            timeout=2,
        )
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError):
        return "unknown"

    revision = result.stdout.strip()
    return revision if _GIT_SHA_PATTERN.fullmatch(revision) else "unknown"


APP_BUILD = resolve_build()
