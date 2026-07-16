"""Importing this package registers every check via the ``@check`` decorators.

Import order defines the order checks appear in reports and the generated catalog.
"""

from . import manifest, consistency, python_static, filetree, runtime  # noqa: F401,E402

__all__ = ["manifest", "consistency", "python_static", "filetree", "runtime"]
