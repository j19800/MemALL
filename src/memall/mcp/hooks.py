"""
Legacy re-export module — lifecycle hooks moved to ``core/lifecycle.py``.

All symbols are now defined in ``memall.core.lifecycle``.  This module
re-exports them for backward compatibility.
"""
from memall.core.lifecycle import *  # noqa: F401, F403
from memall.core.lifecycle import _match_tool  # noqa: F401