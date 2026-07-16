"""Static validation of ``blender_manifest.toml``.

Mirrors the manifest rules Blender's ``extension validate`` and the moderators enforce:
required fields, id/version/tagline formatting, SPDX licensing, allowed tags, and
permissions that each carry a reason.
"""

from __future__ import annotations

import re
import unicodedata
from typing import List

from ..data import addon_tags, spdx_exceptions, spdx_licenses, theme_tags
from ..models import Finding, Severity
from ..registry import check
from ..target import MANIFEST_NAME
from ..versions import BLENDER_EXTENSIONS_MIN, ge, parse_loose, parse_semver, tuple_to_str

E = Severity.ERROR
W = Severity.WARN

REQUIRED_FIELDS = ["id", "version", "name", "tagline", "maintainer", "type", "license", "blender_version_min"]
VALID_TYPES = {"add-on", "theme"}
# Blender extension license policy (docs/advanced/extensions/licenses.html):
# add-ons MUST be GPL-3.0-or-later; themes may use any GPL-compatible license.
ADDON_REQUIRED_LICENSE = "GPL-3.0-or-later"
GPL_COMPATIBLE = {
    "GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0-only", "GPL-3.0-or-later",
    "LGPL-2.1-only", "LGPL-2.1-or-later", "LGPL-3.0-only", "LGPL-3.0-or-later",
    "AGPL-3.0-only", "AGPL-3.0-or-later", "MIT", "MIT-0", "BSD-2-Clause", "BSD-3-Clause",
    "0BSD", "ISC", "Zlib", "BSL-1.0", "Apache-2.0", "MPL-2.0", "Unlicense", "CC0-1.0",
    "WTFPL", "Artistic-2.0",
}
GPL_INCOMPATIBLE = {
    "CC-BY-3.0", "CC-BY-4.0", "CC-BY-SA-3.0", "CC-BY-SA-4.0", "CC-BY-NC-4.0",
    "CC-BY-NC-SA-4.0", "CC-BY-ND-4.0", "EPL-1.0", "EPL-2.0", "CDDL-1.0", "MPL-1.1", "EUPL-1.2",
}
VALID_PERMISSIONS = {"files", "network", "clipboard", "camera", "microphone"}
SUPPORTED_SCHEMA = {"1.0.0"}
ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
SPDX_ID_RE = re.compile(r"^[A-Za-z0-9.+-]+$")
# Licenses whose terms require an attribution / copyright notice.
COPYRIGHT_LICENSE_HINTS = ("GPL", "LGPL", "AGPL", "GFDL", "CC-BY", "MPL", "EPL", "Apache")


def _mf(msg: str, sev: Severity, cid: str) -> Finding:
    return Finding(check_id=cid, severity=sev, message=msg, path=MANIFEST_NAME)


@check("manifest.present", E, "Manifest file exists at the add-on root", "extension validate")
def present(ctx) -> List[Finding]:
    if ctx.manifest_error == "missing":
        return [Finding("manifest.present", E, f"no {MANIFEST_NAME} at the add-on root")]
    return []


@check("manifest.parseable", E, "Manifest is valid TOML", "extension validate")
def parseable(ctx) -> List[Finding]:
    if ctx.manifest is None and ctx.manifest_error and ctx.manifest_error != "missing":
        return [_mf(f"manifest could not be parsed ({ctx.manifest_error})", E, "manifest.parseable")]
    return []


@check("manifest.schema_version", E, "schema_version present and supported", "extension validate")
def schema_version(ctx) -> List[Finding]:
    if ctx.manifest is None:
        return []
    value = ctx.manifest.get("schema_version")
    if value is None:
        return [_mf("schema_version is missing", E, "manifest.schema_version")]
    if value not in SUPPORTED_SCHEMA:
        return [_mf(f"unsupported schema_version {value!r} (expected one of {sorted(SUPPORTED_SCHEMA)})", E, "manifest.schema_version")]
    return []


@check("manifest.required_fields", E, "All required manifest fields are present", "manifest rules")
def required_fields(ctx) -> List[Finding]:
    if ctx.manifest is None:
        return []
    return [
        _mf(f"required field {field!r} is missing", E, "manifest.required_fields")
        for field in REQUIRED_FIELDS
        if field not in ctx.manifest
    ]


@check("manifest.id_format", E, "id is lowercase and identifier-safe", "manifest rules")
def id_format(ctx) -> List[Finding]:
    if ctx.manifest is None or "id" not in ctx.manifest:
        return []
    value = ctx.manifest["id"]
    if not isinstance(value, str) or not ID_RE.match(value):
        return [_mf(f"id {value!r} must match {ID_RE.pattern} (lowercase, digits, underscore)", E, "manifest.id_format")]
    return []


@check("manifest.type_valid", E, "type is 'add-on' or 'theme'", "manifest rules")
def type_valid(ctx) -> List[Finding]:
    if ctx.manifest is None or "type" not in ctx.manifest:
        return []
    value = ctx.manifest["type"]
    if value not in VALID_TYPES:
        return [_mf(f"type {value!r} must be one of {sorted(VALID_TYPES)}", E, "manifest.type_valid")]
    return []


@check("manifest.version_semver", E, "version is MAJOR.MINOR.PATCH", "manifest rules")
def version_semver(ctx) -> List[Finding]:
    if ctx.manifest is None or "version" not in ctx.manifest:
        return []
    value = ctx.manifest["version"]
    if parse_semver(value if isinstance(value, str) else "") is None:
        return [_mf(f"version {value!r} is not valid semver (MAJOR.MINOR.PATCH)", E, "manifest.version_semver")]
    return []


@check("manifest.blender_version_min", E, "blender_version_min is valid and >= 4.2.0", "extensions require 4.2+")
def blender_version_min(ctx) -> List[Finding]:
    if ctx.manifest is None or "blender_version_min" not in ctx.manifest:
        return []
    value = ctx.manifest["blender_version_min"]
    parsed = parse_loose(value if isinstance(value, str) else "")
    if parsed is None:
        return [_mf(f"blender_version_min {value!r} is not a valid version", E, "manifest.blender_version_min")]
    if not ge(parsed, BLENDER_EXTENSIONS_MIN):
        return [_mf(f"blender_version_min {value} is below {tuple_to_str(BLENDER_EXTENSIONS_MIN)} (extensions need 4.2+)", E, "manifest.blender_version_min")]
    return []


@check("manifest.blender_version_order", W, "blender_version_min < blender_version_max", "manifest rules")
def blender_version_order(ctx) -> List[Finding]:
    if ctx.manifest is None:
        return []
    vmin = parse_loose(str(ctx.manifest.get("blender_version_min", "")))
    vmax_raw = ctx.manifest.get("blender_version_max")
    if vmax_raw is None or vmin is None:
        return []
    vmax = parse_loose(str(vmax_raw))
    if vmax is not None and not ge(vmax, vmin):
        return [_mf(f"blender_version_max {vmax_raw} is not greater than blender_version_min", W, "manifest.blender_version_order")]
    return []


@check("manifest.license_present", E, "license is a non-empty list", "license rule")
def license_present(ctx) -> List[Finding]:
    if ctx.manifest is None or "license" not in ctx.manifest:
        return []
    value = ctx.manifest["license"]
    if not isinstance(value, list) or not value:
        return [_mf("license must be a non-empty list of SPDX identifiers", E, "manifest.license_present")]
    return []


def _licenses(ctx) -> List[str]:
    value = ctx.manifest.get("license") if ctx.manifest else None
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


@check("manifest.license_spdx_prefix", E, "Each license uses the 'SPDX:' prefix", "extension validate")
def license_spdx_prefix(ctx) -> List[Finding]:
    if ctx.manifest is None:
        return []
    return [
        _mf(f"license {entry!r} must start with 'SPDX:'", E, "manifest.license_spdx_prefix")
        for entry in _licenses(ctx)
        if not entry.startswith("SPDX:")
    ]


@check("manifest.license_spdx_valid", E, "License identifiers are recognized SPDX ids", "SPDX list")
def license_spdx_valid(ctx) -> List[Finding]:
    if ctx.manifest is None:
        return []
    known = spdx_licenses()
    known_exc = spdx_exceptions()
    findings: List[Finding] = []
    for entry in _licenses(ctx):
        expr = entry[len("SPDX:"):] if entry.startswith("SPDX:") else entry
        expr = expr.strip()
        if not expr:
            findings.append(_mf("empty SPDX license identifier", E, "manifest.license_spdx_valid"))
            continue
        # Compound expressions (A OR B / A AND B) are only lightly validated in v1.
        if re.search(r"\b(AND|OR)\b", expr):
            findings.append(_mf(f"compound SPDX expression {expr!r} not fully validated — verify manually", W, "manifest.license_spdx_valid"))
            continue
        lic, _, exc = expr.partition(" WITH ")
        lic = lic.strip()
        if lic not in known:
            sev = E if not SPDX_ID_RE.match(lic) else W
            note = "malformed" if sev is E else "unrecognized (verify against spdx.org)"
            findings.append(_mf(f"SPDX id {lic!r} is {note}", sev, "manifest.license_spdx_valid"))
        if exc and exc.strip() not in known_exc:
            findings.append(_mf(f"SPDX exception {exc.strip()!r} is unrecognized", W, "manifest.license_spdx_valid"))
    return findings


def _bare_license(entry: str) -> str:
    """Strip the ``SPDX:`` prefix and any ``WITH <exception>`` to get the license id."""
    expr = entry[len("SPDX:"):] if entry.startswith("SPDX:") else entry
    return expr.split(" WITH ")[0].strip()


@check("manifest.license_policy", E, "License meets Blender policy (add-ons: GPL-3.0-or-later)", "extension licenses policy")
def license_policy(ctx) -> List[Finding]:
    if ctx.manifest is None:
        return []
    licenses = [_bare_license(e) for e in _licenses(ctx)]
    if not licenses:
        return []
    kind = ctx.manifest.get("type")
    findings: List[Finding] = []
    if kind == "add-on":
        if set(licenses) != {ADDON_REQUIRED_LICENSE}:
            findings.append(_mf(
                f"add-ons must be licensed SPDX:{ADDON_REQUIRED_LICENSE} (found {ctx.manifest.get('license')})",
                E, "manifest.license_policy",
            ))
    elif kind == "theme":
        for lic in licenses:
            if lic in GPL_INCOMPATIBLE:
                findings.append(_mf(f"theme license {lic!r} is not GPL-compatible", E, "manifest.license_policy"))
            elif lic != ADDON_REQUIRED_LICENSE and lic not in GPL_COMPATIBLE:
                findings.append(_mf(f"theme license {lic!r} - verify it is GPL-compatible", W, "manifest.license_policy"))
        if all(lic != ADDON_REQUIRED_LICENSE for lic in licenses) and not any(lic in GPL_INCOMPATIBLE for lic in licenses):
            findings.append(_mf("GPL-3.0-or-later is the recommended theme license", W, "manifest.license_policy"))
    return findings


@check("manifest.tagline_length", E, "tagline is at most 64 characters", "tagline rule")
def tagline_length(ctx) -> List[Finding]:
    if ctx.manifest is None:
        return []
    tagline = ctx.manifest.get("tagline")
    if isinstance(tagline, str) and len(tagline) > 64:
        return [_mf(f"tagline is {len(tagline)} characters (max 64)", E, "manifest.tagline_length")]
    return []


@check("manifest.tagline_no_trailing_period", W, "tagline has no trailing period", "tagline rule")
def tagline_no_trailing_period(ctx) -> List[Finding]:
    if ctx.manifest is None:
        return []
    tagline = ctx.manifest.get("tagline")
    if isinstance(tagline, str) and tagline.rstrip().endswith("."):
        return [_mf("tagline should not end with a period", W, "manifest.tagline_no_trailing_period")]
    return []


@check("manifest.tagline_redundant", W, "tagline avoids redundant words (add-on/Blender/name)", "guideline nicety")
def tagline_redundant(ctx) -> List[Finding]:
    if ctx.manifest is None:
        return []
    tagline = ctx.manifest.get("tagline")
    if not isinstance(tagline, str):
        return []
    lowered = tagline.lower()
    redundant = [w for w in ("add-on", "addon", "blender") if w in lowered]
    name = ctx.manifest.get("name")
    if isinstance(name, str) and name.lower() in lowered:
        redundant.append(f"name ({name!r})")
    if redundant:
        return [_mf(f"tagline contains redundant term(s): {', '.join(redundant)}", W, "manifest.tagline_redundant")]
    return []


TRADEMARK_RE = re.compile(r"(?i)\bblender\b")
# A commented-out TOML field ("# id = ...") or section header ("# [build]").
COMMENTED_FIELD_RE = re.compile(r"^\s*#\s*(?:[A-Za-z_][A-Za-z0-9_.-]*\s*=|\[[A-Za-z])")


@check("manifest.name_no_trademark", E, "Name does not use the 'Blender' trademark", "trademark rule (moderator review)")
def name_no_trademark(ctx) -> List[Finding]:
    if ctx.manifest is None:
        return []
    name = ctx.manifest.get("name")
    if isinstance(name, str) and TRADEMARK_RE.search(name):
        return [_mf(f"name {name!r} contains 'Blender' - the trademark may not be used in extension names", E, "manifest.name_no_trademark")]
    return []


def _disallowed_name_chars(text: str) -> List[str]:
    out: List[str] = []
    seen = set()
    for ch in text:
        if 0x20 <= ord(ch) <= 0x7E:  # printable ASCII
            continue
        if unicodedata.category(ch)[0] in ("L", "N", "M"):  # letters/digits/marks in any script
            continue
        if ch not in seen:
            seen.add(ch)
            out.append(ch)
    return out


@check("manifest.name_special_chars", W, "Name avoids emoji / symbols / control characters", "listing rendering (moderator review)")
def name_special_chars(ctx) -> List[Finding]:
    if ctx.manifest is None:
        return []
    name = ctx.manifest.get("name")
    if not isinstance(name, str):
        return []
    bad = _disallowed_name_chars(name)
    if bad:
        desc = ", ".join(f"U+{ord(c):04X} ({unicodedata.name(c, 'unnamed')})" for c in bad)
        return [_mf(f"name contains characters that may render badly on the listing: {desc}", W, "manifest.name_special_chars")]
    return []


@check("manifest.no_commented_out_fields", W, "Manifest has no commented-out fields", "manifest readability (moderator review)")
def no_commented_out_fields(ctx) -> List[Finding]:
    if ctx.manifest_text is None:
        return []
    findings: List[Finding] = []
    for lineno, line in enumerate(ctx.manifest_text.splitlines(), start=1):
        if COMMENTED_FIELD_RE.match(line):
            findings.append(Finding("manifest.no_commented_out_fields", W, f"commented-out manifest line: {line.strip()!r}", path=MANIFEST_NAME, line=lineno))
    return findings


@check("manifest.tags_present", W, "At least one tag is set", "tags rule")
def tags_present(ctx) -> List[Finding]:
    if ctx.manifest is None:
        return []
    tags = ctx.manifest.get("tags")
    if not tags:
        return [_mf("no tags set (helps discoverability)", W, "manifest.tags_present")]
    return []


@check("manifest.tags_allowed", E, "Tags are from the allowed per-type list", "tags allowlist")
def tags_allowed(ctx) -> List[Finding]:
    if ctx.manifest is None:
        return []
    tags = ctx.manifest.get("tags")
    if not isinstance(tags, list):
        return []
    is_theme = ctx.manifest.get("type") == "theme"
    allowed = theme_tags() if is_theme else addon_tags()
    # Theme tag list is less certain -> warn rather than fail.
    sev = W if is_theme else E
    return [
        _mf(f"tag {tag!r} is not in the allowed {'theme' if is_theme else 'add-on'} tag list", sev, "manifest.tags_allowed")
        for tag in tags
        if tag not in allowed
    ]


@check("manifest.permission_keys_valid", E, "Permission keys are recognized", "permissions rule")
def permission_keys_valid(ctx) -> List[Finding]:
    if ctx.manifest is None:
        return []
    perms = ctx.manifest.get("permissions")
    if not isinstance(perms, dict):
        return []
    return [
        _mf(f"unknown permission {key!r} (allowed: {sorted(VALID_PERMISSIONS)})", E, "manifest.permission_keys_valid")
        for key in perms
        if key not in VALID_PERMISSIONS
    ]


@check("manifest.permissions_have_reason", E, "Each permission states a reason", "permissions rule")
def permissions_have_reason(ctx) -> List[Finding]:
    if ctx.manifest is None:
        return []
    perms = ctx.manifest.get("permissions")
    if not isinstance(perms, dict):
        return []
    return [
        _mf(f"permission {key!r} has no reason string", E, "manifest.permissions_have_reason")
        for key, reason in perms.items()
        if not (isinstance(reason, str) and reason.strip())
    ]


@check("manifest.permission_reason_style", W, "Permission reasons are short single sentences without a trailing period", "permissions rule")
def permission_reason_style(ctx) -> List[Finding]:
    if ctx.manifest is None:
        return []
    perms = ctx.manifest.get("permissions")
    if not isinstance(perms, dict):
        return []
    findings: List[Finding] = []
    for key, reason in perms.items():
        if not isinstance(reason, str):
            continue
        text = reason.strip()
        if text.endswith("."):
            findings.append(_mf(f"permission {key!r} reason should not end with a period", W, "manifest.permission_reason_style"))
        if len(text) > 72 or "\n" in text:
            findings.append(_mf(f"permission {key!r} reason should be one short sentence", W, "manifest.permission_reason_style"))
    return findings


@check("manifest.copyright_when_required", W, "copyright present for attribution-requiring licenses", "license rule")
def copyright_when_required(ctx) -> List[Finding]:
    if ctx.manifest is None:
        return []
    needs = any(any(h in entry for h in COPYRIGHT_LICENSE_HINTS) for entry in _licenses(ctx))
    has_copyright = bool(ctx.manifest.get("copyright"))
    if needs and not has_copyright:
        return [_mf("license requires attribution but no 'copyright' field is set", W, "manifest.copyright_when_required")]
    return []


@check("manifest.website_present", W, "A support / report-issues URL is provided", "moderator review (report-issues link)")
def website_present(ctx) -> List[Finding]:
    # By far the most common moderator request in the approval queue is a working link
    # where users can report issues (the manifest `website` field / detail-page support link).
    if ctx.manifest is None:
        return []
    if not ctx.manifest.get("website"):
        return [_mf("no 'website' set; moderators require a working link where users can report issues/get support", W, "manifest.website_present")]
    return []


@check("manifest.website_url_valid", W, "website is a valid http(s) URL", "manifest rules")
def website_url_valid(ctx) -> List[Finding]:
    if ctx.manifest is None:
        return []
    website = ctx.manifest.get("website")
    if website is None:
        return []
    if not (isinstance(website, str) and re.match(r"^https?://", website)):
        return [_mf(f"website {website!r} should be a valid http(s) URL", W, "manifest.website_url_valid")]
    return []


@check("manifest.wheels_declared_exist", E, "Declared wheels exist in the package", "wheels rule")
def wheels_declared_exist(ctx) -> List[Finding]:
    if ctx.manifest is None:
        return []
    wheels = ctx.manifest.get("wheels")
    if not isinstance(wheels, list):
        return []
    findings: List[Finding] = []
    for rel in wheels:
        if not isinstance(rel, str):
            continue
        wheel_path = ctx.target.root / rel.lstrip("/")
        if not wheel_path.is_file():
            findings.append(_mf(f"declared wheel {rel!r} does not exist in the package", E, "manifest.wheels_declared_exist"))
    return findings
