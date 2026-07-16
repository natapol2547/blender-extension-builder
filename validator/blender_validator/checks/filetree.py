"""File-tree / packaging checks.

Operates on the files that would actually ship: anything matched by the manifest's
``[build].paths_exclude_pattern`` (or Blender's defaults when ``[build]`` is absent) is
ignored, because it never reaches the published extension.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from ..models import Finding, Severity
from ..registry import check
from ..target import MANIFEST_NAME

E = Severity.ERROR
W = Severity.WARN

HEAVY_FILE_BYTES = 1 * 1024 * 1024  # 1 MB single-asset warning
TOTAL_PAYLOAD_BYTES = 5 * 1024 * 1024  # 5 MB total non-code warning

# Extensions we treat as "source/text" (excluded from the binary payload budget).
CODE_TEXT_EXTS = {
    ".py", ".pyi", ".toml", ".md", ".rst", ".txt", ".cfg", ".ini",
    ".glsl", ".osl", ".frag", ".vert", ".html", ".css", ".js",
    ".json", ".xml", ".yaml", ".yml",
}
VCS_DIRS = {".git", ".svn", ".hg", ".github", ".gitlab"}
DEV_FILES = {"pyproject.toml", "uv.lock", "poetry.lock", "package.json", "package-lock.json", ".gitignore", ".prettierrc"}
BLENDER_DEFAULT_EXCLUDES = ["__pycache__/", "/.git/", "/*.zip"]


def _effective_excludes(ctx) -> List[str]:
    build = ctx.manifest.get("build") if ctx.manifest else None
    if isinstance(build, dict) and isinstance(build.get("paths_exclude_pattern"), list):
        return [p for p in build["paths_exclude_pattern"] if isinstance(p, str)]
    return BLENDER_DEFAULT_EXCLUDES


def _excluded(rel: str, patterns: List[str]) -> bool:
    parts = rel.split("/")
    basename = parts[-1]
    for pat in patterns:
        anchored = pat.startswith("/")
        body = pat[1:] if anchored else pat
        if body.endswith("/"):  # directory pattern
            d = body[:-1]
            if anchored:
                if rel == d or rel.startswith(d + "/"):
                    return True
            elif any(seg == d for seg in parts):
                return True
        else:  # file / glob pattern
            if anchored:
                if fnmatch.fnmatch(rel, body):
                    return True
            elif fnmatch.fnmatch(rel, body) or fnmatch.fnmatch(basename, body) or fnmatch.fnmatch(rel, "*/" + body):
                return True
    return False


def _shipped(ctx) -> List[Tuple[str, Path]]:
    """(relpath, path) for every file that would ship (not excluded)."""
    patterns = _effective_excludes(ctx)
    out: List[Tuple[str, Path]] = []
    for path in ctx.target.iter_all_files():
        rel = ctx.target.rel(path)
        if not _excluded(rel, patterns):
            out.append((rel, path))
    return out


def _is_binary_payload(rel: str) -> bool:
    return Path(rel).suffix.lower() not in CODE_TEXT_EXTS and Path(rel).name.lower() != "license"


@check("pkg.no_pycache", E, "No __pycache__ or compiled Python is shipped", "packaging")
def no_pycache(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for rel, _path in _shipped(ctx):
        if "__pycache__" in rel.split("/") or Path(rel).suffix.lower() in {".pyc", ".pyo", ".pyd"}:
            findings.append(Finding("pkg.no_pycache", E, f"compiled/cache artifact would ship: {rel}", path=rel))
    return findings


@check("pkg.no_vcs_dirs", W, "No version-control directories are shipped", "packaging")
def no_vcs_dirs(ctx) -> List[Finding]:
    seen = set()
    findings: List[Finding] = []
    for rel, _path in _shipped(ctx):
        for seg in rel.split("/")[:-1]:
            if seg in VCS_DIRS and seg not in seen:
                seen.add(seg)
                findings.append(Finding("pkg.no_vcs_dirs", W, f"version-control directory would ship: {seg}/", path=rel))
    return findings


@check("pkg.no_dev_files", W, "No development-only files are shipped", "packaging")
def no_dev_files(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for rel, _path in _shipped(ctx):
        if Path(rel).name in DEV_FILES:
            findings.append(Finding("pkg.no_dev_files", W, f"development file would ship: {rel}", path=rel))
    return findings


@check("pkg.no_gif", W, "No .gif assets (they bloat the listing)", "no heavy gifs")
def no_gif(ctx) -> List[Finding]:
    return [
        Finding("pkg.no_gif", W, f".gif asset would ship: {rel}", path=rel)
        for rel, _path in _shipped(ctx)
        if Path(rel).suffix.lower() == ".gif"
    ]


@check("pkg.heavy_asset", W, "No single asset larger than 1 MB", "listing bloat")
def heavy_asset(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for rel, path in _shipped(ctx):
        if not _is_binary_payload(rel):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > HEAVY_FILE_BYTES:
            findings.append(Finding("pkg.heavy_asset", W, f"{rel} is {size / 1024 / 1024:.1f} MB (> 1 MB)", path=rel))
    return findings


@check("pkg.total_binary_payload", W, "Total non-code payload under 5 MB", "listing bloat")
def total_binary_payload(ctx) -> List[Finding]:
    total = 0
    for rel, path in _shipped(ctx):
        if not _is_binary_payload(rel):
            continue
        try:
            total += path.stat().st_size
        except OSError:
            continue
    if total > TOTAL_PAYLOAD_BYTES:
        return [Finding("pkg.total_binary_payload", W, f"total non-code payload is {total / 1024 / 1024:.1f} MB (> 5 MB)")]
    return []


@check("pkg.no_backslash_in_names", W, "No file/dir names contain a backslash", "cross-platform")
def no_backslash_in_names(ctx) -> List[Finding]:
    return [
        Finding("pkg.no_backslash_in_names", W, f"path component contains a backslash: {rel}", path=rel)
        for rel, _path in _shipped(ctx)
        if "\\" in rel
    ]


@check("pkg.structure_sane", W, "Manifest at root with a Python entry point", "structure")
def structure_sane(ctx) -> List[Finding]:
    if ctx.manifest is None:
        return []
    if ctx.root_init is None:
        return [Finding("pkg.structure_sane", W, "no __init__.py entry point at the add-on root")]
    return []


# The extension platform's supported platform identifiers.
BLENDER_PLATFORMS = {"windows-x64", "windows-arm64", "macos-x64", "macos-arm64", "linux-x64"}
# Python packages already bundled with Blender's Python; wheels for them must not ship.
BLENDER_BUNDLED_DISTS = {"numpy", "requests", "urllib3", "certifi", "charset-normalizer", "idna", "zstandard"}
COMPILED_LIB_EXTS = {".dll", ".so", ".dylib"}
NATIVE_SOURCE_EXTS = {".c", ".cpp", ".cc", ".cxx", ".rs"}
VERSIONED_SO_RE = re.compile(r"\.so(?:\.\d+)+$")


def _wheel_platform_tags(filename: str) -> Optional[List[str]]:
    """Platform tags from a wheel filename ({dist}-{version}(-{build})?-{py}-{abi}-{platform}.whl)."""
    if not filename.lower().endswith(".whl"):
        return None
    parts = filename[:-4].rsplit("-", 3)
    if len(parts) != 4:
        return None
    return parts[3].split(".")  # compound tags are dot-joined


def _manifest_wheel_basenames(ctx) -> List[str]:
    wheels = ctx.manifest.get("wheels") if ctx.manifest else None
    if not isinstance(wheels, list):
        return []
    return [w.rstrip("/").rsplit("/", 1)[-1] for w in wheels if isinstance(w, str)]


def _tag_to_blender_platforms(tag: str) -> Optional[Set[str]]:
    """Blender platform ids covered by a wheel tag. None = pure Python; empty set = unmappable."""
    t = tag.lower()
    if t == "any":
        return None
    if t.startswith("win_amd64"):
        return {"windows-x64"}
    if t.startswith("win_arm64"):
        return {"windows-arm64"}
    if t.startswith(("manylinux", "musllinux", "linux")):
        return {"linux-x64"} if t.endswith("x86_64") else set()
    if t.startswith("macosx"):
        if t.endswith("universal2"):
            return {"macos-x64", "macos-arm64"}
        if t.endswith("arm64"):
            return {"macos-arm64"}
        if t.endswith(("x86_64", "intel", "fat64")):
            return {"macos-x64"}
    return set()


def _pw(msg: str) -> Finding:
    return Finding("pkg.platform_wheels_consistency", W, msg, path=MANIFEST_NAME)


@check("pkg.platform_wheels_consistency", W, "platforms and wheels declarations line up", "platform builds (moderator review)")
def platform_wheels_consistency(ctx) -> List[Finding]:
    if ctx.manifest is None:
        return []
    findings: List[Finding] = []
    platforms = ctx.manifest.get("platforms")
    declared: List[str] = []
    if isinstance(platforms, list):
        for p in platforms:
            if isinstance(p, str) and p not in BLENDER_PLATFORMS:
                findings.append(_pw(f"unknown platform {p!r} (allowed: {sorted(BLENDER_PLATFORMS)})"))
        declared = [p for p in platforms if isinstance(p, str) and p in BLENDER_PLATFORMS]

    dist_cover: Dict[str, Set[str]] = {}
    has_platform_specific = False
    for basename in _manifest_wheel_basenames(ctx):
        tags = _wheel_platform_tags(basename)
        if tags is None:
            continue
        mapped = [_tag_to_blender_platforms(t) for t in tags]
        if all(m is None for m in mapped):
            continue  # pure-Python wheel covers every platform
        has_platform_specific = True
        dist = basename.split("-")[0].lower().replace("_", "-")
        dist_cover.setdefault(dist, set()).update(*[m or set() for m in mapped])

    if declared and not has_platform_specific:
        findings.append(_pw("[platforms] restricts the extension but no bundled wheel is platform-specific; drop [platforms] unless the restriction is intentional (e.g. OS-specific APIs)"))

    effective = set(declared) if declared else set(BLENDER_PLATFORMS)
    for dist, cover in sorted(dist_cover.items()):
        if not cover:
            continue  # only unmappable tags - stay silent rather than guess
        missing = effective - cover
        if missing:
            findings.append(_pw(f"wheel {dist!r} has builds for {sorted(cover)} but the extension targets {sorted(effective)}; add wheels for {sorted(missing)} or restrict [platforms]"))
    return findings


@check("pkg.no_bundled_wheels", W, "No wheels for packages Blender already bundles", "bundled wheels (moderator review)")
def no_bundled_wheels(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for basename in _manifest_wheel_basenames(ctx):
        if not basename.lower().endswith(".whl"):
            continue
        dist = basename.split("-")[0].lower().replace("_", "-")
        if dist in BLENDER_BUNDLED_DISTS:
            findings.append(Finding("pkg.no_bundled_wheels", W, f"wheel {basename!r} ships {dist!r}, which Blender's Python already bundles", path=MANIFEST_NAME))
    return findings


@check("pkg.no_compiled_libs", E, "No loose compiled libraries or native sources", "self-contained Python only (moderator review)")
def no_compiled_libs(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for rel, _path in _shipped(ctx):
        name = Path(rel).name.lower()
        suffix = Path(rel).suffix.lower()
        if suffix in COMPILED_LIB_EXTS or VERSIONED_SO_RE.search(name):
            findings.append(Finding("pkg.no_compiled_libs", E, f"compiled library would ship: {rel} (binary dependencies must come as declared wheels)", path=rel))
        elif suffix in NATIVE_SOURCE_EXTS:
            findings.append(Finding("pkg.no_compiled_libs", W, f"native source file would ship: {rel} (C/C++/Rust components cannot be hosted; exclude build sources)", path=rel))
    return findings
