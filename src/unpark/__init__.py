"""Public API for unpark."""

# Keep the prototype's import surface available while the package is split
# into focused modules. Attribute writes are forwarded too, preserving the
# small amount of test/user monkey-patching supported by the old module.
import sys
from types import ModuleType

from . import app as _app


class _Facade(ModuleType):
    def __getattr__(self, name):
        return getattr(_app, name)

    def __setattr__(self, name, value):
        if hasattr(_app, name):
            setattr(_app, name, value)
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _Facade
