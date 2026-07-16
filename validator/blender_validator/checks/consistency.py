"""Cross-file consistency: manifest vs. legacy ``bl_info`` vs. the license file.

For an *extension*, ``bl_info`` is ignored by Blender (the manifest wins), so a stale or
mismatched ``bl_info`` ships wrong metadata. These checks catch that class of bug.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from ..models import Finding, Severity
from ..registry import check
from ..target import MANIFEST_NAME
from ..versions import parse_loose, parse_semver

E = Severity.ERROR
W = Severity.WARN

LICENSE_FILENAMES = {"license", "license.txt", "license.md", "copying", "copying.txt", "licence"}


def _as_version_tuple(value) -> Optional[Tuple[int, ...]]:
    if isinstance(value, (list, tuple)):
        try:
            return tuple(int(v) for v in value)
        except (ValueError, TypeError):
            return None
    return None


def _pad3(t: Tuple[int, ...]) -> Tuple[int, int, int]:
    t = tuple(t) + (0, 0, 0)
    return t[0], t[1], t[2]


@check("consistency.blinfo_version_matches", E, "bl_info version matches the manifest version", "correctness")
def blinfo_version_matches(ctx) -> List[Finding]:
    if ctx.manifest is None or ctx.bl_info is None:
        return []
    manifest_version = parse_semver(str(ctx.manifest.get("version", "")))
    blinfo_version = _as_version_tuple(ctx.bl_info.get("version"))
    if manifest_version is None or blinfo_version is None:
        return []
    if _pad3(blinfo_version) != _pad3(manifest_version):
        return [
            Finding(
                "consistency.blinfo_version_matches",
                E,
                f"bl_info version {tuple(blinfo_version)} does not match manifest version {ctx.manifest.get('version')!r}",
                path="__init__.py",
            )
        ]
    return []


@check("consistency.blinfo_redundant", W, "bl_info is not needed in an extension", "guideline")
def blinfo_redundant(ctx) -> List[Finding]:
    if ctx.bl_info is None:
        return []
    return [
        Finding(
            "consistency.blinfo_redundant",
            W,
            "bl_info is redundant for extensions (the manifest is authoritative); consider removing it",
            path="__init__.py",
        )
    ]


@check("consistency.blinfo_blender_matches", W, "bl_info blender version matches blender_version_min", "consistency")
def blinfo_blender_matches(ctx) -> List[Finding]:
    if ctx.manifest is None or ctx.bl_info is None:
        return []
    vmin = parse_loose(str(ctx.manifest.get("blender_version_min", "")))
    blinfo_blender = _as_version_tuple(ctx.bl_info.get("blender"))
    if vmin is None or blinfo_blender is None:
        return []
    if _pad3(blinfo_blender) != _pad3(vmin):
        return [
            Finding(
                "consistency.blinfo_blender_matches",
                W,
                f"bl_info blender {tuple(blinfo_blender)} differs from manifest blender_version_min {ctx.manifest.get('blender_version_min')!r}",
                path="__init__.py",
            )
        ]
    return []


@check("consistency.name_matches", W, "bl_info name matches the manifest name", "consistency")
def name_matches(ctx) -> List[Finding]:
    if ctx.manifest is None or ctx.bl_info is None:
        return []
    manifest_name = ctx.manifest.get("name")
    blinfo_name = ctx.bl_info.get("name")
    if isinstance(manifest_name, str) and isinstance(blinfo_name, str) and manifest_name != blinfo_name:
        return [
            Finding(
                "consistency.name_matches",
                W,
                f"bl_info name {blinfo_name!r} differs from manifest name {manifest_name!r}",
                path="__init__.py",
            )
        ]
    return []


@check("consistency.license_file_present", W, "A license text file matching the SPDX id is bundled", "license file rule")
def license_file_present(ctx) -> List[Finding]:
    if ctx.manifest is None:
        return []
    has_file = any(
        p.name.lower() in LICENSE_FILENAMES
        for p in ctx.target.root.iterdir()
        if p.is_file()
    )
    if not has_file and ctx.manifest.get("license"):
        return [
            Finding(
                "consistency.license_file_present",
                W,
                f"declares {ctx.manifest['license']} but no LICENSE/COPYING file is bundled at the root",
                path=MANIFEST_NAME,
            )
        ]
    return []
