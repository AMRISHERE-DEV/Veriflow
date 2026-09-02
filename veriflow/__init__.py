"""Deprecated alias: the ``veriflow`` package was renamed to ``project_saturn`` in v1.1.0.

Every ``veriflow.*`` name resolves to the SAME module object as ``project_saturn.*``
(aliased in ``sys.modules`` before any submodule import), so module-private trust
sentinels keep a single identity and nothing is imported twice. Remove in a
future major release.
"""
import importlib
import pkgutil
import sys
import warnings

import project_saturn as _root

warnings.warn("'veriflow' is deprecated; import 'project_saturn' instead", DeprecationWarning, stacklevel=2)

for _info in pkgutil.walk_packages(_root.__path__, prefix="project_saturn."):
    _mod = importlib.import_module(_info.name)
    sys.modules["veriflow" + _info.name[len("project_saturn"):]] = _mod
sys.modules[__name__] = _root
