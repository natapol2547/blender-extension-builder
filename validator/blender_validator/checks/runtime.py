"""Runtime checks that shell out to a real Blender (>= 4.2).

These run only when a ``blender`` executable is found; the pytest layer skips them
otherwise. ``rt.extension_validate`` is the authoritative structural gate (the same tool
moderators/authors run); ``rt.build`` confirms the extension actually packages.

A headless register/unregister smoke test is intentionally deferred: enabling a local
extension programmatically is version-sensitive and the source folder name need not be an
importable module name, so a naive import would give misleading results. See docs/checks.md.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import tempfile
from typing import List, Optional, Tuple

from ..models import Finding, Severity
from ..registry import KIND_RUNTIME, check
from ..versions import BLENDER_EXTENSIONS_MIN, ge, parse_loose

E = Severity.ERROR
W = Severity.WARN

_VERSION_RE = re.compile(r"Blender\s+(\d+\.\d+(?:\.\d+)?)")
_DEFAULT_GLOBS = [
    r"C:\Program Files\Blender Foundation\Blender */blender.exe",
    r"C:\Program Files\Blender Foundation\*/blender.exe",
    "/Applications/Blender.app/Contents/MacOS/Blender",
    "/opt/blender*/blender",
    "/usr/bin/blender",
    "/usr/local/bin/blender",
    "/snap/bin/blender",
]


def find_blender(explicit: Optional[str] = None) -> Optional[str]:
    """Locate a Blender executable: explicit arg -> env -> PATH -> OS default globs."""
    for candidate in (explicit, os.environ.get("BLENDER_EXECUTABLE")):
        if candidate and os.path.isfile(candidate):
            return candidate
    on_path = shutil.which("blender")
    if on_path:
        return on_path
    for pattern in _DEFAULT_GLOBS:
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[-1]  # prefer the highest-sorted (usually newest) install
    return None


def blender_version(path: str) -> Optional[Tuple[int, ...]]:
    """Return the Blender version tuple by invoking ``blender --version``."""
    try:
        proc = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    match = _VERSION_RE.search(proc.stdout or "")
    return parse_loose(match.group(1)) if match else None


def usable_blender(explicit: Optional[str] = None) -> Optional[str]:
    """Return a Blender path only if it is found and is >= 4.2 (the extensions floor)."""
    path = find_blender(explicit)
    if not path:
        return None
    version = blender_version(path)
    if version is None or not ge(version, BLENDER_EXTENSIONS_MIN):
        return None
    return path


def _tail(text: str, limit: int = 800) -> str:
    text = (text or "").strip()
    return text[-limit:]


@check("rt.extension_validate", E, "`blender --command extension validate` passes", "official validator", kind=KIND_RUNTIME)
def extension_validate(ctx, blender: Optional[str]) -> List[Finding]:
    if not blender:
        return []
    try:
        proc = subprocess.run(
            [blender, "--command", "extension", "validate", str(ctx.target.root)],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return [Finding("rt.extension_validate", E, "extension validate timed out")]
    except OSError as exc:
        return [Finding("rt.extension_validate", E, f"could not run Blender: {exc}")]
    if proc.returncode != 0:
        detail = _tail(proc.stdout + "\n" + proc.stderr)
        return [Finding("rt.extension_validate", E, f"extension validate failed (exit {proc.returncode}):\n{detail}", path="blender_manifest.toml")]
    return []


@check("rt.build", W, "`blender --command extension build` succeeds", "official builder", kind=KIND_RUNTIME)
def build(ctx, blender: Optional[str]) -> List[Finding]:
    if not blender:
        return []
    with tempfile.TemporaryDirectory(prefix="blv_build_") as out_dir:
        try:
            proc = subprocess.run(
                [blender, "--command", "extension", "build", "--output-dir", out_dir],
                cwd=str(ctx.target.root),
                capture_output=True,
                text=True,
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            return [Finding("rt.build", W, "extension build timed out")]
        except OSError as exc:
            return [Finding("rt.build", W, f"could not run Blender: {exc}")]
        if proc.returncode != 0:
            return [Finding("rt.build", W, f"extension build failed (exit {proc.returncode}):\n{_tail(proc.stdout + proc.stderr)}")]
    return []
