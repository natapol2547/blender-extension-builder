"""Tiny version parsing/comparison helpers (stdlib only, no ``packaging`` dependency)."""

from __future__ import annotations

import re
from typing import Optional, Tuple

_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

# Minimum Blender version that supports the extensions system / manifest.
BLENDER_EXTENSIONS_MIN: Tuple[int, int, int] = (4, 2, 0)


def parse_semver(text: str) -> Optional[Tuple[int, int, int]]:
    """Parse a strict ``MAJOR.MINOR.PATCH`` string, or ``None`` if it does not match."""
    if not isinstance(text, str):
        return None
    m = _SEMVER.match(text.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def parse_loose(text: str) -> Optional[Tuple[int, ...]]:
    """Parse a dotted numeric version (``4.2`` or ``4.2.0``) leniently into a tuple."""
    if not isinstance(text, str):
        return None
    parts = text.strip().split(".")
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


def tuple_to_str(version: Tuple[int, ...]) -> str:
    return ".".join(str(p) for p in version)


def ge(a: Tuple[int, ...], b: Tuple[int, ...]) -> bool:
    """``a >= b`` comparing tuples of possibly-different length (zero-padded)."""
    length = max(len(a), len(b))
    a = tuple(a) + (0,) * (length - len(a))
    b = tuple(b) + (0,) * (length - len(b))
    return a >= b
