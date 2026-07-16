"""Core value types shared by every check.

A :class:`Finding` is the single primitive of the whole validator: a check function
inspects an add-on and returns ``list[Finding]``. An empty list means "passed". The
pytest layer and the reporter are thin adapters over these objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Severity(str, Enum):
    """How badly a finding should be treated.

    ``ERROR`` fails the build. ``WARN`` is advisory: surfaced but non-fatal unless the
    suite is run with ``--strict`` (which promotes every warning to a hard failure).
    """

    ERROR = "ERROR"
    WARN = "WARN"

    def __str__(self) -> str:  # nicer test-id / report output
        return self.value


@dataclass(frozen=True)
class Finding:
    """A single rule violation located (where possible) at a file and line.

    ``check_id`` must correspond to a registered :class:`~blender_validator.registry.Check`.
    ``path`` is add-on-relative (POSIX) so reports are stable across machines.
    """

    check_id: str
    severity: Severity
    message: str
    path: Optional[str] = None
    line: Optional[int] = None
    col: Optional[int] = None

    @property
    def location(self) -> str:
        """Human-readable ``path:line`` (or ``"-"`` when not file-scoped)."""
        if not self.path:
            return "-"
        if self.line:
            return f"{self.path}:{self.line}"
        return self.path

    def __str__(self) -> str:
        return f"[{self.severity}] {self.check_id} ({self.location}): {self.message}"
