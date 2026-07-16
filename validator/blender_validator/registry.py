"""Check registry: the single source of truth for what the validator inspects.

Each check is a small function that receives the pre-built :class:`Analysis` and returns
``list[Finding]``. Registering with :func:`check` records its id, severity and the
guideline it maps to, so the catalog, the docs and the pytest parametrization all derive
from the same place.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .models import Finding, Severity

KIND_STATIC = "static"
KIND_RUNTIME = "runtime"


@dataclass(frozen=True)
class Check:
    """Metadata for one registered check."""

    id: str
    severity: Severity
    title: str
    source: str  # short reference to the moderator rule / guideline this encodes
    kind: str  # KIND_STATIC | KIND_RUNTIME
    func: Callable

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "severity": str(self.severity),
            "title": self.title,
            "source": self.source,
            "kind": self.kind,
        }


# Insertion-ordered so reports and test ids read top-to-bottom by category.
REGISTRY: "Dict[str, Check]" = {}

_CHECKS_LOADED = False


def check(
    id: str,
    severity: Severity,
    title: str,
    source: str,
    kind: str = KIND_STATIC,
) -> Callable[[Callable], Callable]:
    """Decorator registering a check function under ``id``."""

    def decorator(func: Callable) -> Callable:
        if id in REGISTRY:
            raise ValueError(f"duplicate check id: {id!r}")
        REGISTRY[id] = Check(id=id, severity=severity, title=title, source=source, kind=kind, func=func)
        return func

    return decorator


def _ensure_loaded() -> None:
    """Import the checks package so decorators populate ``REGISTRY`` exactly once."""
    global _CHECKS_LOADED
    if not _CHECKS_LOADED:
        importlib.import_module("blender_validator.checks")
        _CHECKS_LOADED = True


def all_checks(kind: Optional[str] = None) -> List[Check]:
    """Return every registered check (optionally filtered by ``kind``), in order."""
    _ensure_loaded()
    return [c for c in REGISTRY.values() if kind is None or c.kind == kind]


def _run_one(chk: Check, *args) -> List[Finding]:
    """Run a single check, converting an unexpected crash into a visible ERROR finding."""
    try:
        return list(chk.func(*args) or [])
    except Exception as exc:  # a bug in a check must not abort the whole run
        return [
            Finding(
                check_id=chk.id,
                severity=Severity.ERROR,
                message=f"internal error while running check: {type(exc).__name__}: {exc}",
            )
        ]


def run_static(analysis) -> List[Finding]:
    """Run every static check against ``analysis`` and return all findings."""
    findings: List[Finding] = []
    for chk in all_checks(kind=KIND_STATIC):
        findings.extend(_run_one(chk, analysis))
    return findings


def run_runtime(analysis, blender: Optional[str]) -> List[Finding]:
    """Run every runtime check. ``blender`` is the resolved executable path (or ``None``)."""
    findings: List[Finding] = []
    for chk in all_checks(kind=KIND_RUNTIME):
        findings.extend(_run_one(chk, analysis, blender))
    return findings


def export_catalog() -> List[dict]:
    """Serialize the registry (used to keep ``data/rules_catalog.json`` and docs in sync)."""
    return [c.as_dict() for c in all_checks()]
