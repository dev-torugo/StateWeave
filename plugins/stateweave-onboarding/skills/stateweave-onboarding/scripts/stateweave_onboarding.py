#!/usr/bin/env python3
"""Run the approved StateWeave onboarding CLI surface without a shell."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence

ALLOWED_COMMANDS = frozenset(
    {
        "audit",
        "audit-adoption",
        "audit-continuity",
        "audit-onboarding",
        "backup",
        "candidate-list",
        "candidate-preview",
        "onboarding-apply",
        "onboarding-plan",
        "promote-candidate",
        "reject-candidate",
    }
)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] not in ALLOWED_COMMANDS:
        allowed = ", ".join(sorted(ALLOWED_COMMANDS))
        print(f"usage: stateweave_onboarding.py COMMAND [ARGS]\nallowed: {allowed}")
        return 2
    completed = subprocess.run(
        [sys.executable, "-m", "stateweave.cli", *arguments],
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
