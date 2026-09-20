# -*- coding: utf-8 -*-
"""PyInstaller runtime hook: neutralise the optional heavyweight imports.

ctranslate2.specs.model_spec does `try: import torch / except ImportError`,
and argostranslate.sbd exposes a lazily-resolved stanza. torch is excluded from
this bundle (it is only used by the model-conversion path we never call), so
register a stub up front: the `try` block succeeds instantly and nothing is
printed about a missing module.
"""

import sys
import types


def _stub(name: str, message: str) -> None:
    if name in sys.modules:
        return
    module = types.ModuleType(name)
    module.__version__ = "0.0.0-not-bundled"       # type: ignore[attr-defined]

    def _unavailable(*_args, **_kwargs):
        raise RuntimeError(message)

    module.__getattr__ = _unavailable              # type: ignore[attr-defined]
    sys.modules[name] = module


# ctranslate2.specs imports this at module level purely to set a boolean.
_stub("torch",
      "torch is not bundled with this build; it is only needed by "
      "ctranslate2 model conversion.")
