"""Small AST helpers shared by the Python static checks.

Kept intentionally simple: each static check does its own targeted walk of the already
parsed :class:`~blender_validator.target.PyModule` trees. For ~50 small files this is
trivial and keeps every check independently readable.
"""

from __future__ import annotations

import ast
from typing import Iterator, List, Optional, Set, Tuple


def dotted_name(node: ast.AST) -> Optional[str]:
    """Return the dotted name of a Name/Attribute chain (``os.path.join``) or ``None``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted_name(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def call_name(call: ast.Call) -> Optional[str]:
    """Dotted name of the thing being called, e.g. ``open`` or ``subprocess.run``."""
    return dotted_name(call.func)


def iter_calls(tree: ast.AST) -> Iterator[ast.Call]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node


def iter_str_constants(tree: ast.AST) -> Iterator[ast.Constant]:
    """Yield every string constant node (skips f-string parts, which are JoinedStr)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node


def imported_modules(tree: ast.AST) -> Set[str]:
    """Top-level module names reached by ``import x`` / ``from x import y``.

    Returns both the full dotted path and its first component (``urllib.request`` ->
    ``{"urllib.request", "urllib"}``) so membership tests are easy.
    """
    modules: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:  # ignore relative (internal) imports
                modules.add(node.module)
                modules.add(node.module.split(".")[0])
    return modules


def wildcard_imports(tree: ast.AST) -> List[ast.ImportFrom]:
    """``from x import *`` statements."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names)
    ]


def module_level_call_exprs(tree: ast.Module) -> List[ast.Call]:
    """Top-level bare call expressions (import-time side effects).

    Excludes the module docstring and any non-call expression. Assignments -- even
    ``logger = get_logger(__name__)`` -- are intentionally allowed; only a naked call
    statement such as ``logger.info(...)`` at module scope is treated as a side effect.
    """
    calls: List[ast.Call] = []
    for stmt in tree.body:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            calls.append(stmt.value)
    return calls


def bare_except_handlers(tree: ast.AST) -> Iterator[Tuple[ast.ExceptHandler, bool]]:
    """Yield ``(handler, swallows)`` for every ``except`` clause.

    ``swallows`` is True when the handler body is only ``pass``/``...``/a bare re-raise-less
    no-op, i.e. the exception is silently discarded. A bare ``except:`` (no type) always
    counts regardless of body.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            body = [s for s in node.body if not _is_docstring(s)]
            swallows = all(
                isinstance(s, ast.Pass)
                or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
                for s in body
            )
            yield node, swallows


def _is_docstring(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(
        stmt.value.value, str
    )


def docstring_constant_ids(tree: ast.AST) -> Set[int]:
    """``id()`` of every module/class/function docstring node in the tree.

    Checks that look for suspicious string *values* use this to skip documentation:
    a docstring is never shown to the user, so it is prose, not data.
    """
    ids: Set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and _is_docstring(body[0]):
                ids.add(id(body[0].value))
    return ids


def references_name(tree: ast.AST, needles: Set[str]) -> bool:
    """True if any attribute/name in the tree matches one of ``needles`` (dotted or bare)."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Attribute, ast.Name)):
            name = dotted_name(node)
            if name and (name in needles or name.split(".")[-1] in needles):
                return True
    return False
