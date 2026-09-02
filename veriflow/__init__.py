"""Deprecated alias: the ``veriflow`` package was renamed to ``glass_ionomer`` in v1.1.x.

Every ``veriflow.*`` name resolves to the SAME module object as ``glass_ionomer.*``
(aliased in ``sys.modules`` before any submodule import), so module-private trust
sentinels keep a single identity and nothing is imported twice. Remove in a
future major release.
"""
import importlib
import pkgutil
import sys
import warnings

import glass_ionomer as _root

warnings.warn("'veriflow' is deprecated; import 'glass_ionomer' instead", DeprecationWarning, stacklevel=2)

for _info in pkgutil.walk_packages(_root.__path__, prefix="glass_ionomer."):
    _mod = importlib.import_module(_info.name)
    sys.modules["veriflow" + _info.name[len("glass_ionomer"):]] = _mod
sys.modules[__name__] = _root
