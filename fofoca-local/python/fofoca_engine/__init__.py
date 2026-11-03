"""Compatibility import; no second native extension or engine implementation."""
import sys
from fofoca_local.engine import *
from fofoca_local.engine import __all__, _native

sys.modules[__name__ + "._native"] = _native
