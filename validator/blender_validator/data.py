"""Loaders for the pinned allowlist data files under ``data/``."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Set

_DATA_DIR = Path(__file__).parent / "data"


@lru_cache(maxsize=None)
def _load(name: str) -> dict:
    return json.loads((_DATA_DIR / name).read_text(encoding="utf-8"))


def addon_tags() -> Set[str]:
    return set(_load("tags_addon.json")["tags"])


def theme_tags() -> Set[str]:
    return set(_load("tags_theme.json")["tags"])


def spdx_licenses() -> Set[str]:
    return set(_load("spdx_licenses.json")["licenses"])


def spdx_exceptions() -> Set[str]:
    return set(_load("spdx_licenses.json")["exceptions"])
