"""Contract tests for the primary verification core.

These modules use bare module-level test functions. This load_tests hook wraps
them in unittest.FunctionTestCase so the README's documented command
(python -m unittest discover -s tests -t .) collects and runs every one.
"""
from __future__ import annotations

import importlib
import pkgutil
import unittest


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite(tests)
    for mod_info in pkgutil.iter_modules(__path__):
        if not mod_info.name.startswith("test_"):
            continue
        module = importlib.import_module(f"{__name__}.{mod_info.name}")
        for name in sorted(dir(module)):
            fn = getattr(module, name)
            if name.startswith("test_") and callable(fn):
                suite.addTest(unittest.FunctionTestCase(fn, description=f"{mod_info.name}.{name}"))
    return suite
