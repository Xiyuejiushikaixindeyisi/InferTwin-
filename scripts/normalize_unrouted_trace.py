#!/usr/bin/env python
"""Normalize an unrouted trace into a single-instance routed trace."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"


def _ensure_src_path() -> None:
    if str(SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(SRC_ROOT))


def main(argv: list[str] | None = None) -> int:
    _ensure_src_path()
    from infertwin.cli.main import main as cli_main

    return cli_main(["normalize-trace", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    raise SystemExit(main())
