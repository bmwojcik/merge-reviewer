#!/usr/bin/env python3
"""Unused: apply/skip paste is the host extension. Keep stdin protocol if re-enabled."""

from __future__ import annotations

import sys


def main() -> int:
    sys.stdin.read()
    sys.stdout.write("{}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
