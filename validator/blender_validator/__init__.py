"""Validate Blender extensions against the code-quality rules moderators enforce.

This package is deliberately dependency-free (Python 3.11 standard library only) so
the validator and its pytest suite run anywhere, including a bare CI runner. The
optional ``scraper`` package in the repo root is never imported from here.
"""

from .models import Finding, Severity
from .registry import Check, REGISTRY, check, run_static, run_runtime
from .target import Analysis, PyModule, Target, resolve_target

__all__ = [
    "Finding",
    "Severity",
    "Check",
    "REGISTRY",
    "check",
    "run_static",
    "run_runtime",
    "Analysis",
    "PyModule",
    "Target",
    "resolve_target",
]
