"""Read id / name / version from an add-on's blender_manifest.toml.

    python read_manifest.py --source-dir path/to/addon

Writes ``id``, ``name`` and ``version`` to $GITHUB_OUTPUT (when set) and stdout,
so the action can tag the release with the add-on's own version.
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib

MANIFEST = "blender_manifest.toml"


def emit(key: str, value: str) -> None:
    print(f"{key}={value}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, help="Add-on source directory containing blender_manifest.toml.")
    args = parser.parse_args(argv)

    path = os.path.join(args.source_dir, MANIFEST)
    if not os.path.isfile(path):
        print(f"error: {path} not found — is source-dir pointing at the add-on root?", file=sys.stderr)
        return 1
    try:
        with open(path, "rb") as fh:
            manifest = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        print(f"error: {path} is not valid TOML: {exc}", file=sys.stderr)
        return 1

    missing = [key for key in ("id", "version") if not manifest.get(key)]
    if missing:
        print(f"error: {path} is missing required field(s): {', '.join(missing)}", file=sys.stderr)
        return 1

    emit("id", str(manifest["id"]))
    emit("name", str(manifest.get("name", manifest["id"])))
    emit("version", str(manifest["version"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
