"""Allow ``python -m mgia_realtime``."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":  # pragma: no cover - exercised by subprocess smoke test
    raise SystemExit(main())
