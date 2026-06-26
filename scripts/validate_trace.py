#!/usr/bin/env python
"""Validate that a trace can be parsed by HitFloor."""

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
    from hitfloor.cli.main import main as cli_main

    args = list(sys.argv[1:] if argv is None else argv)
    if "--trace" in args:
        args[args.index("--trace")] = "--input"
    return cli_main(["validate-trace", *args])


if __name__ == "__main__":
    raise SystemExit(main())
