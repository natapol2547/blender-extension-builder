"""Grouping and formatting of findings for the terminal summary and ``report.json``."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set

from .models import Finding, Severity
from .registry import Check


def group_by_check(findings: List[Finding]) -> "Dict[str, List[Finding]]":
    grouped: "Dict[str, List[Finding]]" = defaultdict(list)
    for f in findings:
        grouped[f.check_id].append(f)
    return grouped


def worst_severity(findings: List[Finding]) -> Severity:
    return Severity.ERROR if any(f.severity is Severity.ERROR for f in findings) else Severity.WARN


def status_for(findings: List[Finding]) -> str:
    """PASS (no findings), WARN (only warnings) or FAIL (>=1 error)."""
    if not findings:
        return "PASS"
    return "FAIL" if worst_severity(findings) is Severity.ERROR else "WARN"


def counts(findings: List[Finding]) -> "Dict[str, int]":
    return {
        "errors": sum(1 for f in findings if f.severity is Severity.ERROR),
        "warnings": sum(1 for f in findings if f.severity is Severity.WARN),
    }


def _status(chk: Check, grouped, skipped: Set[str]) -> str:
    if chk.id in skipped:
        return "SKIP"
    return status_for(grouped.get(chk.id, []))


def format_terminal(checks: List[Check], findings: List[Finding], skipped: Optional[Set[str]] = None) -> str:
    """A compact per-check summary, errors and warnings first."""
    skipped = skipped or set()
    grouped = group_by_check(findings)
    lines: List[str] = []
    order = {"FAIL": 0, "WARN": 1, "SKIP": 2, "PASS": 3}
    rows = [(chk, _status(chk, grouped, skipped)) for chk in checks]
    rows.sort(key=lambda r: (order[r[1]], r[0].id))
    c = counts(findings)
    lines.append(f"Blender Extension Validator - {c['errors']} error(s), {c['warnings']} warning(s)")
    lines.append("-" * 72)
    for chk, status in rows:
        marker = {"FAIL": "x", "WARN": "!", "SKIP": "-", "PASS": "."}[status]
        lines.append(f" [{marker}] {status:<4} {chk.id}")
        for f in grouped.get(chk.id, []):
            lines.append(f"          - {f.location}: {f.message.splitlines()[0]}")
    return "\n".join(lines)


def report_dict(checks: List[Check], findings: List[Finding], skipped: Optional[Set[str]] = None) -> dict:
    skipped = skipped or set()
    grouped = group_by_check(findings)
    return {
        "summary": counts(findings),
        "checks": [
            {
                "id": chk.id,
                "title": chk.title,
                "declared_severity": str(chk.severity),
                "kind": chk.kind,
                "source": chk.source,
                "status": _status(chk, grouped, skipped),
                "findings": [
                    {
                        "severity": str(f.severity),
                        "message": f.message,
                        "path": f.path,
                        "line": f.line,
                    }
                    for f in grouped.get(chk.id, [])
                ],
            }
            for chk in checks
        ],
    }


def write_report(checks: List[Check], findings: List[Finding], path: Path, skipped: Optional[Set[str]] = None) -> None:
    path.write_text(json.dumps(report_dict(checks, findings, skipped), indent=2), encoding="utf-8")
