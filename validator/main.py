"""Standalone CLI for the Blender extension validator (no pytest required).

    python main.py --addon-path PATH [--blender PATH] [--strict] [--report FILE]

Exits non-zero if any ERROR finding is present (or any WARN when --strict is given), so it
can back a GitHub Action directly. Vendored from the blender-extension-validator project.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from blender_validator import Analysis, resolve_target, run_runtime, run_static
from blender_validator.checks.runtime import usable_blender
from blender_validator.models import Severity
from blender_validator.registry import KIND_RUNTIME, all_checks
from blender_validator import report as report_mod


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate a Blender extension against moderator code-quality rules.")
    parser.add_argument("--addon-path", required=True, help="Add-on directory or .zip to validate.")
    parser.add_argument("--blender", default=None, help="Path to a Blender >= 4.2 executable (else auto-detected).")
    parser.add_argument("--strict", action="store_true", help="Treat WARN findings as failures.")
    parser.add_argument("--report", default=None, help="Write a JSON report to this path.")
    args = parser.parse_args(argv)

    if not Path(args.addon_path).exists():
        print(f"error: add-on path not found: {args.addon_path}", file=sys.stderr)
        return 2

    target = resolve_target(args.addon_path)
    try:
        analysis = Analysis(target)
        findings = run_static(analysis)
        blender = usable_blender(args.blender)
        skipped = set()
        if blender:
            findings += run_runtime(analysis, blender)
        else:
            skipped = {c.id for c in all_checks(KIND_RUNTIME)}
    finally:
        target.cleanup()

    checks = all_checks()
    print(report_mod.format_terminal(checks, findings, skipped))
    if args.report:
        report_mod.write_report(checks, findings, Path(args.report), skipped)
        print(f"\nWrote JSON report to {args.report}")

    errors = [f for f in findings if f.severity is Severity.ERROR]
    warns = [f for f in findings if f.severity is Severity.WARN]
    if errors or (args.strict and warns):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
