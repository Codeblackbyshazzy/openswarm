#!/usr/bin/env python3
"""Naming-convention gate: runs the no-underscore-names + p-private AST checks (Haik's checks,
brought over from haik/refactor/delete-fluff) against backend/ and prints violations + counts.
This is the authoritative gate for the leading-_ -> p_/P_/public migration: green here means the
codebase follows the access-modifier convention to a tea. Run from the linter/ dir.

Usage: python check_naming.py [--summary] [<path-prefix-filter>]
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from checks.no_underscore_names import run_underscore_check
from checks.p_private import run_p_private_check

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.load(open(Path(__file__).resolve().parent / "config" / "config.json"))


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    summary = "--summary" in sys.argv
    prefix = args[0] if args else ""

    exceptions = CONFIG.get("exceptions", {})
    excludes = CONFIG["exclude"]

    underscore = run_underscore_check(ROOT, exceptions, excludes, None)
    pprivate = run_p_private_check(ROOT, exceptions, excludes, None)

    def keep(e: str) -> bool:
        return e.startswith(prefix) if prefix else True

    underscore = [e for e in underscore if keep(e)]
    pprivate = [e for e in pprivate if keep(e)]

    print(f"no-underscore-names: {len(underscore)}   p-private: {len(pprivate)}")
    if summary:
        return 1 if (underscore or pprivate) else 0

    for e in underscore:
        print(e)
    for e in pprivate:
        print(e)
    return 1 if (underscore or pprivate) else 0


if __name__ == "__main__":
    sys.exit(main())
