from __future__ import annotations

import importlib
import sys
from collections.abc import Sequence
from contextlib import contextmanager
from types import ModuleType


@contextmanager
def patched_argv(command_name: str, args: Sequence[str]):
    previous = sys.argv[:]
    sys.argv = [command_name, *args]
    try:
        yield
    finally:
        sys.argv = previous


def run_module_main(module_name: str, args: Sequence[str]) -> int:
    module: ModuleType = importlib.import_module(module_name)
    main = getattr(module, "main")
    with patched_argv(module_name, args):
        result = main()
    return int(result or 0)
