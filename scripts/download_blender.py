"""Download and extract a portable Blender build, printing the executable path.

    python download_blender.py --url https://download.blender.org/.../blender-4.5.3-linux-x64.tar.xz \
                               --dest ~/.blender-portable/4.5.3

Skips the download when the destination already holds a blender executable (an
actions/cache hit restores it there). Writes ``executable`` to $GITHUB_OUTPUT
(when set) and stdout.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import tarfile
import tempfile

import requests

TIMEOUT = 60
CHUNK = 1 << 20  # 1 MiB


def emit(key: str, value: str) -> None:
    print(f"{key}={value}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")


def find_executable(dest: str) -> str | None:
    matches = sorted(glob.glob(os.path.join(dest, "*", "blender")))
    return matches[-1] if matches else None


def download(url: str, target: str) -> None:
    with requests.get(url, stream=True, timeout=TIMEOUT) as response:
        response.raise_for_status()
        total = int(response.headers.get("Content-Length", 0))
        done = 0
        next_mark = 0
        with open(target, "wb") as fh:
            for chunk in response.iter_content(chunk_size=CHUNK):
                fh.write(chunk)
                done += len(chunk)
                if total and done * 10 // total >= next_mark:
                    print(f"  {done // (1 << 20)} / {total // (1 << 20)} MiB", file=sys.stderr)
                    next_mark = done * 10 // total + 1


def extract(archive: str, dest: str) -> None:
    with tarfile.open(archive, mode="r:xz") as tar:
        try:
            tar.extractall(dest, filter="data")
        except TypeError:  # Python < 3.12 has no extraction filters
            tar.extractall(dest)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Blender linux-x64 .tar.xz URL.")
    parser.add_argument("--dest", required=True, help="Directory to extract the build into.")
    args = parser.parse_args(argv)

    dest = os.path.abspath(os.path.expanduser(args.dest))
    executable = find_executable(dest)
    if executable:
        print(f"using cached Blender at {executable}", file=sys.stderr)
        emit("executable", executable)
        return 0

    os.makedirs(dest, exist_ok=True)
    print(f"downloading {args.url}", file=sys.stderr)
    with tempfile.TemporaryDirectory(prefix="blender_dl_") as tmp:
        archive = os.path.join(tmp, os.path.basename(args.url))
        download(args.url, archive)
        print(f"extracting into {dest}", file=sys.stderr)
        extract(archive, dest)

    executable = find_executable(dest)
    if not executable:
        print(f"error: no blender executable found under {dest} after extraction", file=sys.stderr)
        return 1
    os.chmod(executable, os.stat(executable).st_mode | 0o755)
    emit("executable", executable)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
