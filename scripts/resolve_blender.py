"""Resolve a Blender release to a Linux x64 download URL.

    python resolve_blender.py --requested latest
    python resolve_blender.py --requested 4.5.3

Scrapes https://download.blender.org/release/ for ``latest``: picks the highest
BlenderX.Y directory that contains a ``blender-X.Y.Z-linux-x64.tar.xz`` build
(walking down to older directories if the newest has none yet). For an explicit
X.Y.Z the URL is constructed directly and verified with a HEAD request.

Writes ``version`` and ``url`` to $GITHUB_OUTPUT (when set) and stdout.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

import requests

RELEASES_INDEX = "https://download.blender.org/release/"
DIR_RE = re.compile(r'href="Blender(\d+)\.(\d+)/?"')
ARCHIVE_RE = re.compile(r'href="blender-(\d+)\.(\d+)\.(\d+)-linux-x64\.tar\.xz"')
TIMEOUT = 30


def emit(key: str, value: str) -> None:
    print(f"{key}={value}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")


def release_dir_url(major: int, minor: int) -> str:
    return f"{RELEASES_INDEX}Blender{major}.{minor}/"


def archive_url(version: tuple[int, int, int]) -> str:
    text = ".".join(str(p) for p in version)
    return f"{release_dir_url(version[0], version[1])}blender-{text}-linux-x64.tar.xz"


def resolve_latest(session: requests.Session) -> tuple[int, int, int]:
    index = session.get(RELEASES_INDEX, timeout=TIMEOUT)
    index.raise_for_status()
    series = sorted({(int(m), int(n)) for m, n in DIR_RE.findall(index.text)}, reverse=True)
    if not series:
        raise RuntimeError(f"no Blender release directories found at {RELEASES_INDEX}")
    for major, minor in series:
        listing = session.get(release_dir_url(major, minor), timeout=TIMEOUT)
        if listing.status_code != 200:
            continue
        builds = [tuple(int(p) for p in match) for match in ARCHIVE_RE.findall(listing.text)]
        if builds:
            return max(builds)  # type: ignore[return-value]
        print(f"note: Blender{major}.{minor}/ has no linux-x64 archive yet, trying older series", file=sys.stderr)
    raise RuntimeError("no linux-x64 Blender archive found in any release directory")


def resolve_explicit(session: requests.Session, requested: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", requested.strip())
    if not match:
        raise RuntimeError(f"blender-version must be 'latest' or MAJOR.MINOR.PATCH, got: {requested!r}")
    version = tuple(int(p) for p in match.groups())
    head = session.head(archive_url(version), timeout=TIMEOUT, allow_redirects=True)
    if head.status_code != 200:
        raise RuntimeError(f"no such Blender release: {archive_url(version)} (HTTP {head.status_code})")
    return version  # type: ignore[return-value]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requested", default="latest", help="'latest' or an explicit MAJOR.MINOR.PATCH.")
    args = parser.parse_args(argv)

    with requests.Session() as session:
        session.headers["User-Agent"] = "blender-extension-builder-action"
        if args.requested.strip().lower() == "latest":
            version = resolve_latest(session)
        else:
            version = resolve_explicit(session, args.requested)

    text = ".".join(str(p) for p in version)
    emit("version", text)
    emit("url", archive_url(version))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
