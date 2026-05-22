"""Back-compat alias — see ``_response_byte_cache.py``.

Issue #70 introduced this class for ``/api/nodes/directory``. Issue #71
generalized the class to ``ResponseByteCache`` and reused it for
``/api/nodes/geojson`` and ``/api/network/topology``. The original
name is preserved here so existing imports and tests keep working
without churn.
"""

from __future__ import annotations

from utils._response_byte_cache import ResponseByteCache

# Original name — keep so tests/callers that import the old symbol
# don't need to update. New code should import ResponseByteCache directly.
DirectoryResponseCache = ResponseByteCache

__all__ = ["DirectoryResponseCache"]
