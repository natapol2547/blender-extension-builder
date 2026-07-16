"""Resolve an add-on (directory or ``.zip``) and build the analysis context once.

Every check reads from a single :class:`Analysis` instance so the manifest is parsed and
each Python file is compiled to an AST exactly once per run.
"""

from __future__ import annotations

import ast
import os
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional

MANIFEST_NAME = "blender_manifest.toml"


class Target:
    """An add-on on disk, rooted at the directory that holds ``blender_manifest.toml``."""

    def __init__(self, root: Path, _tempdir: "Optional[tempfile.TemporaryDirectory]" = None):
        self.root = root.resolve()
        self._tempdir = _tempdir

    # -- lifecycle ---------------------------------------------------------
    def cleanup(self) -> None:
        if self._tempdir is not None:
            self._tempdir.cleanup()
            self._tempdir = None

    def __enter__(self) -> "Target":
        return self

    def __exit__(self, *exc) -> None:
        self.cleanup()

    # -- paths -------------------------------------------------------------
    def rel(self, path: Path) -> str:
        """Return ``path`` relative to the add-on root, as a POSIX string."""
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()

    def iter_all_files(self) -> Iterator[Path]:
        """Yield every file under the root (whatever is physically present)."""
        for dirpath, _dirnames, filenames in os.walk(self.root):
            for name in filenames:
                yield Path(dirpath) / name

    def python_files(self) -> List[Path]:
        return sorted(p for p in self.iter_all_files() if p.suffix == ".py")

    def read_text(self, path: Path) -> str:
        """Read text tolerating a UTF-8 BOM and undecodable bytes."""
        return path.read_text(encoding="utf-8-sig", errors="replace")

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_NAME


@dataclass
class PyModule:
    """One Python source file plus its parsed AST (or the syntax error found)."""

    path: Path
    rel: str
    text: str
    tree: Optional[ast.Module]
    parse_error: Optional[str] = None
    error_line: Optional[int] = None

    @property
    def is_root_init(self) -> bool:
        return self.rel == "__init__.py"


class Analysis:
    """Everything the checks need, computed once for a :class:`Target`."""

    def __init__(self, target: Target):
        self.target = target
        self.manifest: Optional[dict] = None
        self.manifest_error: Optional[str] = None
        self.manifest_text: Optional[str] = None
        self._load_manifest()
        self._py_modules: Optional[List[PyModule]] = None
        self._bl_info: Optional[dict] = None
        self._bl_info_loaded = False

    # -- manifest ----------------------------------------------------------
    def _load_manifest(self) -> None:
        path = self.target.manifest_path
        if not path.is_file():
            self.manifest_error = "missing"
            return
        try:
            self.manifest_text = self.target.read_text(path)
        except OSError as exc:
            self.manifest_error = f"unreadable: {exc}"
            return
        try:
            self.manifest = tomllib.loads(self.manifest_text)
        except tomllib.TOMLDecodeError as exc:
            self.manifest_error = f"invalid TOML: {exc}"

    # -- python ------------------------------------------------------------
    @property
    def py_modules(self) -> List[PyModule]:
        if self._py_modules is None:
            self._py_modules = [self._parse(p) for p in self.target.python_files()]
        return self._py_modules

    def _parse(self, path: Path) -> PyModule:
        rel = self.target.rel(path)
        text = self.target.read_text(path)
        try:
            tree = ast.parse(text, filename=str(path))
            return PyModule(path=path, rel=rel, text=text, tree=tree)
        except SyntaxError as exc:
            return PyModule(
                path=path,
                rel=rel,
                text=text,
                tree=None,
                parse_error=str(exc.msg),
                error_line=exc.lineno,
            )

    @property
    def root_init(self) -> Optional[PyModule]:
        for mod in self.py_modules:
            if mod.is_root_init:
                return mod
        return None

    @property
    def bl_info(self) -> Optional[dict]:
        """The ``bl_info`` dict from the root ``__init__.py`` (``None`` if absent/unparseable)."""
        if not self._bl_info_loaded:
            self._bl_info_loaded = True
            self._bl_info = _extract_bl_info(self.root_init)
        return self._bl_info


def _extract_bl_info(module: Optional[PyModule]) -> Optional[dict]:
    """Statically read a top-level ``bl_info = {...}`` literal without executing the module."""
    if module is None or module.tree is None:
        return None
    for node in module.tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "bl_info":
                    try:
                        value = ast.literal_eval(node.value)
                    except (ValueError, SyntaxError):
                        return None
                    return value if isinstance(value, dict) else None
    return None


def _detect_root(base: Path) -> Path:
    """Pick the directory that actually contains the manifest (base or a lone subdir)."""
    if (base / MANIFEST_NAME).is_file():
        return base
    subdirs_with_manifest = [
        d for d in sorted(base.iterdir()) if d.is_dir() and (d / MANIFEST_NAME).is_file()
    ] if base.is_dir() else []
    if len(subdirs_with_manifest) == 1:
        return subdirs_with_manifest[0]
    return base


def resolve_target(path: "str | Path") -> Target:
    """Resolve a directory or ``.zip`` into a :class:`Target` rooted at the add-on."""
    p = Path(path)
    if p.is_file() and p.suffix.lower() == ".zip":
        tmp = tempfile.TemporaryDirectory(prefix="blv_")
        with zipfile.ZipFile(p) as zf:
            zf.extractall(tmp.name)
        return Target(_detect_root(Path(tmp.name)), _tempdir=tmp)
    if p.is_dir():
        return Target(_detect_root(p))
    # Non-existent path: return as-is so `manifest.present` reports it cleanly.
    return Target(p)
