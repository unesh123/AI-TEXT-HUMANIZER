"""Load provider credentials from a local .env file (pure stdlib).

The app never hardcodes API keys. At startup (server / CLI entry points) we
read ``KEY=VALUE`` lines from ``.env.local`` (falling back to ``.env``) and
put them into ``os.environ`` **without** overwriting values already set in
the process environment, so exported variables always win.

Loading is explicit — call ``load_envfile()`` from an entry point. It is
*not* done at import time, so library imports (and the test suite) stay
hermetic and never pick up real credentials by accident.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

_ENV_FILES = (".env.local", ".env")


def _parse_value(raw: str) -> str:
    value = raw.strip()
    # Strip one layer of matching single/double quotes.
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def load_envfile(path: Optional[str] = None) -> Path:
    """Load the first existing env file into ``os.environ``.

    Existing environment variables are never overwritten. Returns the path
    that was loaded (or ``None`` if no file exists).
    """
    candidates = [path] if path else list(_ENV_FILES)
    for name in candidates:
        if not name:
            continue
        p = Path(name)
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            key, sep, value = line.partition("=")
            key = key.strip()
            if not sep or not key:
                continue
            os.environ.setdefault(key, _parse_value(value))
        return p
    return None
