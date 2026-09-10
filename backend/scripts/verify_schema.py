#!/usr/bin/env python3
"""Exit non-zero unless the database is at the revision this build expects.

The application performs this check on startup too, and that is the one that
matters in production. This exists because `uvicorn --reload` supervises the
app in a child process: when the app refuses to start, the reloader keeps
running and waits for a file change, so the container stays *Up* while serving
nothing. A container that looks alive and answers no requests is precisely the
failure the startup check was meant to avoid, so in dev the check runs first,
in the container's main process, where exiting actually stops the container.

    python scripts/verify_schema.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.postgres import engine  # noqa: E402
from app.db.schema import SchemaNotReady, verify_schema  # noqa: E402


def main() -> int:
    try:
        revision = verify_schema(engine)
    except SchemaNotReady as exc:
        print(f"refusing to start: {exc}", file=sys.stderr)
        return 1
    print(f"schema verified at revision {revision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
