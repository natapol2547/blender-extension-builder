"""AST-based static checks over the add-on's Python sources.

Each check walks the already-parsed trees on :class:`~blender_validator.target.Analysis`.
Heuristic checks (write-into-install-dir, auto-updater, network-without-guard) are marked
as such in their messages and are suppressible; the clear rule violations are ERROR.
"""

from __future__ import annotations

import ast
import re
from typing import Dict, List

from ..ast_utils import (
    bare_except_handlers,
    call_name,
    docstring_constant_ids,
    dotted_name,
    imported_modules,
    iter_calls,
    iter_str_constants,
    module_level_call_exprs,
    references_name,
    wildcard_imports,
)
from ..models import Finding, Severity
from ..registry import check

E = Severity.ERROR
W = Severity.WARN

NETWORK_MODULES = {"urllib", "urllib.request", "requests", "http", "http.client", "socket", "aiohttp", "httpx", "ftplib", "telnetlib", "smtplib"}
NETWORK_GUARD = {"bpy.app.online_access", "online_access", "is_online"}
# Only genuine mutations of *other* add-ons. Note: preferences.addon_expand /
# addon_refresh only affect UI state (usually the add-on's own panel) and are excluded.
ADDON_MUTATION_RE = re.compile(
    r"(?:preferences\.addon_(?:install|remove|enable|disable)"
    r"|extensions\.(?:package_install|package_uninstall|package_install_files|package_upgrade_all)"
    r"|addon_utils\.(?:enable|disable))$"
)
ABS_PATH_RE = re.compile(r"^(?:[A-Za-z]:\\|/(?:home|Users|mnt|opt|tmp|var|usr|root)/)")
BACKSLASH_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|\\[^\\\s]*\.(?:py|pyc|pyo|png|jpg|jpeg|blend|json|toml|txt|glsl|osl|zip|dll|so|dylib))")
UPDATER_RE = re.compile(r"(?i)(?:check.?for.?update|check_update|update_check|version_check|auto.?updat|latest.?version|newer.?version)")
USER_PATH_MARKERS = ("tempfile", "gettempdir", "temporarydirectory", "mkdtemp", "namedtemporary", "extension_path_user", "user_resource", "expanduser", "bpy.path.abspath", "filepath", "bpy.data.filepath")
WRITE_FUNCS = {"os.remove", "os.unlink", "os.rmdir", "shutil.rmtree", "os.makedirs", "os.mkdir"}


def _iter_trees(ctx):
    for mod in ctx.py_modules:
        if mod.tree is not None:
            yield mod


@check("py.parse_error", E, "Every Python file parses without a syntax error", "code quality")
def parse_error(ctx) -> List[Finding]:
    return [
        Finding("py.parse_error", E, f"syntax error: {mod.parse_error}", path=mod.rel, line=mod.error_line)
        for mod in ctx.py_modules
        if mod.parse_error is not None
    ]


@check("py.no_eval_exec", E, "No eval / exec / compile", "code quality / security")
def no_eval_exec(ctx) -> List[Finding]:
    banned = {"eval", "exec", "compile"}
    findings: List[Finding] = []
    for mod in _iter_trees(ctx):
        for call in iter_calls(mod.tree):
            if call_name(call) in banned:
                findings.append(Finding("py.no_eval_exec", E, f"use of {call_name(call)}()", path=mod.rel, line=call.lineno))
    return findings


# NOTE: there is deliberately no "no subprocess" check. Moderators explicitly *recommend*
# subprocess (and multiprocess) as the safe alternative to threading/queue -- see
# py.no_threading below. Banning subprocess would contradict the review guidance.


@check("py.no_runtime_pip", E, "No runtime install of Python packages", "no runtime install")
def no_runtime_pip(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for mod in _iter_trees(ctx):
        mods = imported_modules(mod.tree)
        if "ensurepip" in mods or "pip" in mods:
            findings.append(Finding("py.no_runtime_pip", E, "imports pip/ensurepip (bundle wheels instead)", path=mod.rel))
        for const in iter_str_constants(mod.tree):
            if re.search(r"pip\s+install|-m\s+pip|install\s+--target", const.value):
                findings.append(Finding("py.no_runtime_pip", E, f"looks like a runtime pip install: {const.value!r}", path=mod.rel, line=const.lineno))
    return findings


@check("py.no_addon_manipulation", E, "Does not install/enable/remove other add-ons", "don't modify other add-ons")
def no_addon_manipulation(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for mod in _iter_trees(ctx):
        for call in iter_calls(mod.tree):
            name = call_name(call)
            if name and ADDON_MUTATION_RE.search(name):
                findings.append(Finding("py.no_addon_manipulation", E, f"mutates other add-ons via {name}()", path=mod.rel, line=call.lineno))
    return findings


@check("py.no_backslash_path_sep", E, "No backslash path separators (breaks Linux/macOS)", "cross-platform paths")
def no_backslash_path_sep(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for mod in _iter_trees(ctx):
        for const in iter_str_constants(mod.tree):
            if "\\" in const.value and BACKSLASH_PATH_RE.search(const.value):
                findings.append(Finding("py.no_backslash_path_sep", E, f"backslash path separator in {const.value!r}", path=mod.rel, line=const.lineno))
    return findings


def _write_mode(call: ast.Call):
    args = list(call.args)
    if len(args) >= 2 and isinstance(args[1], ast.Constant) and isinstance(args[1].value, str):
        return args[1].value
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def _has_user_marker(expr_src: str) -> bool:
    low = expr_src.lower()
    return any(m in low for m in USER_PATH_MARKERS)


@check("py.no_write_install_dir", E, "Does not write into the add-on install directory", "read-only install dir")
def no_write_install_dir(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for mod in _iter_trees(ctx):
        # Only modules that derive paths from their own location can write to the install dir.
        if not references_name(mod.tree, {"__file__"}):
            continue
        for call in iter_calls(mod.tree):
            name = call_name(call) or ""
            path_arg = call.args[0] if call.args else None
            is_write = False
            if name == "open":
                mode = _write_mode(call)
                is_write = bool(mode) and any(c in mode for c in "wax+")
            elif name in WRITE_FUNCS:
                is_write = True
            elif name.endswith(".write_text") or name.endswith(".write_bytes"):
                is_write = True
                path_arg = call.func.value if isinstance(call.func, ast.Attribute) else None
            if not is_write:
                continue
            expr_src = ast.unparse(path_arg) if path_arg is not None else ""
            if _has_user_marker(expr_src):
                continue
            findings.append(
                Finding(
                    "py.no_write_install_dir",
                    E,
                    f"writes to a path in a module that derives locations from __file__ ({name}({expr_src or '...'})); "
                    f"use bpy.utils.extension_path_user() for writable data",
                    path=mod.rel,
                    line=call.lineno,
                )
            )
    return findings


@check("py.no_network_without_guard", W, "Network access checks bpy.app.online_access", "online_access rule")
def no_network_without_guard(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for mod in _iter_trees(ctx):
        if imported_modules(mod.tree) & NETWORK_MODULES and not references_name(mod.tree, NETWORK_GUARD):
            used = sorted(imported_modules(mod.tree) & NETWORK_MODULES)
            findings.append(Finding("py.no_network_without_guard", W, f"uses network module(s) {used} without referencing bpy.app.online_access", path=mod.rel))
    return findings


@check("py.no_hardcoded_abs_path", W, "No hardcoded absolute filesystem paths", "cross-platform")
def no_hardcoded_abs_path(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for mod in _iter_trees(ctx):
        for const in iter_str_constants(mod.tree):
            if ABS_PATH_RE.match(const.value):
                findings.append(Finding("py.no_hardcoded_abs_path", W, f"hardcoded absolute path {const.value!r}", path=mod.rel, line=const.lineno))
    return findings


@check("py.no_wildcard_import", W, "No 'from x import *'", "code quality")
def no_wildcard_import(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for mod in _iter_trees(ctx):
        for node in wildcard_imports(mod.tree):
            findings.append(Finding("py.no_wildcard_import", W, f"wildcard import from {node.module!r}", path=mod.rel, line=node.lineno))
    return findings


@check("py.no_bare_except", W, "No silently-swallowed broad exceptions", "code quality")
def no_bare_except(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for mod in _iter_trees(ctx):
        for handler, swallows in bare_except_handlers(mod.tree):
            is_bare = handler.type is None
            is_broad = isinstance(handler.type, ast.Name) and handler.type.id in {"Exception", "BaseException"}
            if is_bare:
                findings.append(Finding("py.no_bare_except", W, "bare 'except:' clause", path=mod.rel, line=handler.lineno))
            elif is_broad and swallows:
                findings.append(Finding("py.no_bare_except", W, f"'except {handler.type.id}' silently swallows the error", path=mod.rel, line=handler.lineno))
    return findings


@check("py.no_print", W, "No leftover print() debugging", "code quality")
def no_print(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for mod in _iter_trees(ctx):
        for call in iter_calls(mod.tree):
            if call_name(call) == "print":
                findings.append(Finding("py.no_print", W, "print() call (use logging instead)", path=mod.rel, line=call.lineno))
    return findings


@check("py.no_module_side_effects", W, "No side-effecting calls at import time", "import side-effects")
def no_module_side_effects(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for mod in _iter_trees(ctx):
        for call in module_level_call_exprs(mod.tree):
            findings.append(Finding("py.no_module_side_effects", W, f"top-level call {call_name(call) or 'call'}() runs at import time", path=mod.rel, line=call.lineno))
    return findings


@check("py.no_auto_updater", W, "No self-updater (updates go through the platform)", "no auto-updater")
def no_auto_updater(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for mod in _iter_trees(ctx):
        names = [mod.rel]
        for node in mod.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(node.name)
        if any(UPDATER_RE.search(n) for n in names) or references_name(mod.tree, {"urlretrieve"}):
            findings.append(Finding("py.no_auto_updater", W, "looks like an update checker/updater - verify it only reads the platform repo (heuristic)", path=mod.rel))
    return findings


@check("py.register_unregister_present", W, "Top-level module defines register()/unregister()", "structure")
def register_unregister_present(ctx) -> List[Finding]:
    root = ctx.root_init
    if root is None or root.tree is None:
        return [Finding("py.register_unregister_present", W, "no root __init__.py found", path="__init__.py")]
    defined = {n.name for n in root.tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    missing = [name for name in ("register", "unregister") if name not in defined]
    if missing:
        return [Finding("py.register_unregister_present", W, f"root __init__.py does not define {missing}", path="__init__.py")]
    return []


# Modules removed from the Blender Python API. Extensions require Blender >= 4.2,
# where these no longer work; the dict value explains the replacement.
DEPRECATED_MODULES = {
    "bgl": "deprecated since Blender 3.5 and removed in current Blender (non-functional with Metal/Vulkan); use the 'gpu' module",
}
WINDOWS_ONLY_MODULES = {"winreg", "_winreg"}
RELOAD_CALLS = {"importlib.reload", "imp.reload", "reload"}
SYS_PATH_CALLS = {"sys.path.append", "sys.path.insert", "sys.path.extend", "sys.path.remove"}
SYS_MODULES_CALLS = {"sys.modules.pop", "sys.modules.update", "sys.modules.setdefault", "sys.modules.clear"}
SCALAR_PROPS = {
    "BoolProperty", "BoolVectorProperty", "IntProperty", "IntVectorProperty",
    "FloatProperty", "FloatVectorProperty", "StringProperty", "EnumProperty",
}
KEYMAP_MUTATION_RE = re.compile(r"(?:keymap_items|keymaps)\.(?:new|remove|clear)$")
PROMO_DOMAINS = (
    "gumroad.com", "blendermarket.com", "patreon.com", "ko-fi.com", "buymeacoffee.com",
    "discord.gg", "twitter.com", "x.com/", "instagram.com", "facebook.com",
)
# What counts as "the UI" for py.no_promo_links (see _ui_string_scopes).
UI_DRAW_FUNC_RE = re.compile(r"^draw(?:_|$)")
URL_OPEN_RE = re.compile(r"(?:^|\.)url_open(?:_preset)?$")
UI_KEYWORDS = {"text", "text_ctxt", "description", "url", "tooltip", "heading", "name", "items"}
UI_ATTRS = {"bl_label", "bl_description", "bl_info", "url", "text", "description", "tooltip"}


def _iter_imports(tree):
    """Yield ``(node, top_level_module)`` for every absolute import statement."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node, alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                yield node, node.module.split(".")[0]


@check("py.no_deprecated_module", E, "No imports of modules removed from modern Blender (bgl)", "bgl removal (4.x)")
def no_deprecated_module(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for mod in _iter_trees(ctx):
        for node, top in _iter_imports(mod.tree):
            if top in DEPRECATED_MODULES:
                findings.append(Finding("py.no_deprecated_module", E, f"imports {top!r}: {DEPRECATED_MODULES[top]}", path=mod.rel, line=node.lineno))
    return findings


@check("py.no_winreg", E, "No Windows-registry access via winreg", "cross-platform (moderator review)")
def no_winreg(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for mod in _iter_trees(ctx):
        for node, top in _iter_imports(mod.tree):
            if top in WINDOWS_ONLY_MODULES:
                findings.append(Finding("py.no_winreg", E, f"imports {top!r}: the Windows registry is not cross-platform and must not be accessed", path=mod.rel, line=node.lineno))
    return findings


@check("py.no_os_startfile", W, "No os.startfile (Windows-only)", "cross-platform (moderator review)")
def no_os_startfile(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for mod in _iter_trees(ctx):
        for call in iter_calls(mod.tree):
            if call_name(call) in {"os.startfile", "startfile"}:
                findings.append(Finding("py.no_os_startfile", W, "os.startfile() is Windows-only; use bpy.ops.wm.url_open / webbrowser for cross-platform behaviour", path=mod.rel, line=call.lineno))
    return findings


@check("py.no_threading", E, "No threading/queue modules (known Blender crashers)", "moderator review (threading)")
def no_threading(ctx) -> List[Finding]:
    # Moderators reject these outright: "threading and queue modules are crashers for
    # Blender and are not allowed. subprocess should be used instead."
    findings: List[Finding] = []
    for mod in _iter_trees(ctx):
        for node, top in _iter_imports(mod.tree):
            if top in {"threading", "queue"}:
                findings.append(Finding("py.no_threading", E, f"imports {top!r}: threading/queue are known Blender crashers and are not accepted; use subprocess/multiprocess, an operator, or bpy.app.timers instead", path=mod.rel, line=node.lineno))
    return findings


@check("py.no_sys_manipulation", E, "Does not manipulate sys.path / sys.modules", "don't modify the Python environment")
def no_sys_manipulation(ctx) -> List[Finding]:
    findings: List[Finding] = []

    def _flag(node: ast.AST, what: str) -> None:
        findings.append(Finding("py.no_sys_manipulation", E, f"{what} - extensions must not manipulate Blender's Python environment", path=mod.rel, line=node.lineno))

    def _is_sys_target(node: ast.AST) -> bool:
        if dotted_name(node) == "sys.path":
            return True
        return isinstance(node, ast.Subscript) and dotted_name(node.value) in {"sys.path", "sys.modules"}

    for mod in _iter_trees(ctx):
        for node in ast.walk(mod.tree):
            if isinstance(node, ast.Call):
                name = call_name(node) or ""
                if name in SYS_PATH_CALLS or name in SYS_MODULES_CALLS:
                    _flag(node, f"call to {name}()")
            elif isinstance(node, ast.Assign) and any(_is_sys_target(t) for t in node.targets):
                _flag(node, "assignment to sys.path/sys.modules")
            elif isinstance(node, ast.AugAssign) and _is_sys_target(node.target):
                _flag(node, "augmented assignment to sys.path/sys.modules")
            elif isinstance(node, ast.Delete) and any(_is_sys_target(t) for t in node.targets):
                _flag(node, "del on sys.modules/sys.path entry")
    return findings


@check("py.no_module_reload", W, "No custom module reloading", "no custom reload (moderator review)")
def no_module_reload(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for mod in _iter_trees(ctx):
        for call in iter_calls(mod.tree):
            if call_name(call) in RELOAD_CALLS:
                findings.append(Finding("py.no_module_reload", W, f"{call_name(call)}() call - extensions should not reload modules (remove dev-reload shims from the build)", path=mod.rel, line=call.lineno))
    return findings


def _is_main_guard(stmt: ast.stmt) -> bool:
    if not isinstance(stmt, ast.If) or not isinstance(stmt.test, ast.Compare):
        return False
    test = stmt.test
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    sides = [test.left, test.comparators[0]]
    has_name = any(isinstance(s, ast.Name) and s.id == "__name__" for s in sides)
    has_main = any(isinstance(s, ast.Constant) and s.value == "__main__" for s in sides)
    return has_name and has_main


@check("py.no_main_guard", W, "No standalone-script __main__ blocks", "no standalone execution (moderator review)")
def no_main_guard(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for mod in _iter_trees(ctx):
        for stmt in mod.tree.body:
            if _is_main_guard(stmt):
                findings.append(Finding("py.no_main_guard", W, "'if __name__ == \"__main__\":' block - extensions are never run as standalone scripts", path=mod.rel, line=stmt.lineno))
    return findings


@check("py.no_blf_unload_in_unregister", E, "No blf.unload() while unregistering", "font unload crash (moderator review)")
def no_blf_unload_in_unregister(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for mod in _iter_trees(ctx):
        for node in ast.walk(mod.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "unregister":
                for call in iter_calls(node):
                    if call_name(call) == "blf.unload":
                        findings.append(Finding("py.no_blf_unload_in_unregister", E, "blf.unload() during unregister can crash Blender; leave loaded fonts to Blender", path=mod.rel, line=call.lineno))
    return findings


def _is_dunder_name(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == "__name__"


@check("py.preferences_use_package", W, "Preferences are keyed by __package__, not __name__", "__package__ rule (moderator review)")
def preferences_use_package(ctx) -> List[Finding]:
    findings: List[Finding] = []

    def _flag(node: ast.AST, msg: str) -> None:
        findings.append(Finding("py.preferences_use_package", W, msg, path=mod.rel, line=node.lineno))

    for mod in _iter_trees(ctx):
        for node in ast.walk(mod.tree):
            if isinstance(node, ast.Subscript) and _is_dunder_name(node.slice):
                base = dotted_name(node.value) or ""
                if base == "addons" or base.endswith(".addons"):
                    _flag(node, "add-on preferences looked up with __name__; extensions must use __package__")
            elif isinstance(node, ast.Call):
                name = call_name(node) or ""
                if (name == "addons.get" or name.endswith(".addons.get")) and node.args and _is_dunder_name(node.args[0]):
                    _flag(node, "add-on preferences looked up with __name__; extensions must use __package__")
            elif isinstance(node, ast.ClassDef) and any((dotted_name(b) or "").split(".")[-1] == "AddonPreferences" for b in node.bases):
                for stmt in node.body:
                    value = None
                    if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "bl_idname" for t in stmt.targets):
                        value = stmt.value
                    elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.target.id == "bl_idname":
                        value = stmt.value
                    if value is not None and _is_dunder_name(value):
                        _flag(stmt, "AddonPreferences bl_idname = __name__; use __package__ for an extension")
    return findings


@check("py.no_stray_properties", W, "No stray scalar properties on bpy.types IDs", "use PropertyGroup (moderator review)")
def no_stray_properties(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for mod in _iter_trees(ctx):
        for node in ast.walk(mod.tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            prop = (call_name(node.value) or "").split(".")[-1]
            if prop not in SCALAR_PROPS:
                continue
            for target in node.targets:
                name = dotted_name(target) or ""
                if name.startswith("bpy.types."):
                    findings.append(Finding("py.no_stray_properties", W, f"stray {prop} registered on {'.'.join(name.split('.')[:3])}; group properties in a PropertyGroup attached via PointerProperty", path=mod.rel, line=node.lineno))
    return findings


def _is_bpy_ops_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and (call_name(node) or "").startswith("bpy.ops.")


def _operator_classes(tree: ast.Module):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any((dotted_name(b) or "").split(".")[-1] == "Operator" for b in node.bases):
            yield node


@check("py.no_native_op_wrapper", W, "No operators that merely wrap a native bpy.ops call", "redundant operator (moderator review)")
def no_native_op_wrapper(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for mod in _iter_trees(ctx):
        for cls in _operator_classes(mod.tree):
            methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
            if {"invoke", "modal", "draw"} & methods.keys():
                continue
            execute = methods.get("execute")
            if execute is None:
                continue
            body = execute.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                body = body[1:]  # docstring
            wrapped = None
            if len(body) == 1 and isinstance(body[0], ast.Return) and _is_bpy_ops_call(body[0].value):
                wrapped = body[0].value
            elif len(body) == 2 and isinstance(body[1], ast.Return):
                first = body[0]
                if isinstance(first, ast.Expr) and _is_bpy_ops_call(first.value):
                    wrapped = first.value
                elif isinstance(first, ast.Assign) and _is_bpy_ops_call(first.value):
                    wrapped = first.value
            if wrapped is not None:
                findings.append(Finding("py.no_native_op_wrapper", W, f"operator {cls.name!r} only wraps {call_name(wrapped)}(); expose the native operator instead (heuristic)", path=mod.rel, line=execute.lineno))
    return findings


@check("py.no_keymap_operator", W, "No operators that add/remove keymaps", "keymaps belong to the user (moderator review)")
def no_keymap_operator(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for mod in _iter_trees(ctx):
        for cls in _operator_classes(mod.tree):
            for call in iter_calls(cls):
                name = call_name(call) or ""
                if KEYMAP_MUTATION_RE.search(name):
                    findings.append(Finding("py.no_keymap_operator", W, f"operator {cls.name!r} mutates keymaps ({name}()); keymap changes belong in register/unregister and user preferences", path=mod.rel, line=call.lineno))
    return findings


@check("py.no_operator_in_handler", W, "No operators started from persistent handlers", "manual initialization (moderator review)")
def no_operator_in_handler(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for mod in _iter_trees(ctx):
        for node in ast.walk(mod.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorators = {(dotted_name(d) or "").split(".")[-1] for d in node.decorator_list}
            if "persistent" not in decorators:
                continue
            for call in iter_calls(node):
                if _is_bpy_ops_call(call):
                    findings.append(Finding("py.no_operator_in_handler", W, f"persistent handler {node.name!r} starts operator {call_name(call)}(); operators should be initiated by the user", path=mod.rel, line=call.lineno))
    return findings


def _promo_hits(value: str) -> List[str]:
    lowered = value.lower()
    return sorted({d for d in PROMO_DOMAINS if d in lowered})


def _ui_string_scopes(tree: ast.AST):
    """Yield subtrees whose string literals can end up on screen.

    The ToS rule is about ads *in the Blender UI*, not about mentioning a store
    anywhere in the source, so only these count:

    * ``draw*()`` bodies -- panel/menu/header/node drawing, incl. ``draw_callback_px``
    * ``wm.url_open()`` calls -- the link a button actually opens
    * ``bl_label`` / ``bl_description`` and friends -- class metadata Blender renders
    * UI-facing keyword arguments (``text=``, ``description=``, ``url=``, ...)

    Anything else (comments, docstrings, attribution constants, log messages) is
    invisible to the user and is left alone.
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and UI_DRAW_FUNC_RE.match(node.name):
            yield node
        elif isinstance(node, ast.Call):
            name = call_name(node)
            if name and URL_OPEN_RE.search(name):
                yield node
            for kw in node.keywords:
                if kw.arg in UI_KEYWORDS:
                    yield kw.value
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [dotted_name(t) or "" for t in targets]
            if node.value is not None and any(n.split(".")[-1] in UI_ATTRS for n in names):
                yield node.value


def _module_string_constants(tree: ast.AST) -> Dict[str, ast.Constant]:
    """``NAME = "literal"`` assignments, by name.

    Lets the UI-scoped scan follow ``props.url = DONATE_URL`` back to the literal it was
    defined from, which is where add-ons normally keep such a link.
    """
    consts: Dict[str, ast.Constant] = {}
    for node in ast.walk(tree):
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                consts[target.id] = value
    return consts


@check("py.no_promo_links", W, "No store/donation/social links in the UI", "no ads in the Blender UI (ToS)")
def no_promo_links(ctx) -> List[Finding]:
    findings: List[Finding] = []
    for mod in _iter_trees(ctx):
        module_consts = _module_string_constants(mod.tree)
        docstrings = docstring_constant_ids(mod.tree)
        # Keyed by id() so a url_open() call nested in a draw() is not reported twice.
        flagged: Dict[int, ast.Constant] = {}
        for scope in _ui_string_scopes(mod.tree):
            for node in ast.walk(scope):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    const = node if id(node) not in docstrings else None
                elif isinstance(node, ast.Name):
                    const = module_consts.get(node.id)
                else:
                    const = None
                if const is not None and _promo_hits(const.value):
                    flagged[id(const)] = const
        for const in sorted(flagged.values(), key=lambda c: (c.lineno, c.col_offset)):
            hits = ", ".join(_promo_hits(const.value))
            findings.append(Finding("py.no_promo_links", W, f"promotional/social link ({hits}) reaches the UI - ads and social links are not allowed in the Blender UI (heuristic)", path=mod.rel, line=const.lineno))
    return findings
