"""Read MCP result fields under both SDK spellings.

mcp 1.x models expose camelCase attributes (``mimeType``, ``structuredContent``,
``isError``); mcp 2.x renamed them to snake_case and keeps camelCase only as
the wire alias. Production code only constructs these models (by alias, which
both accept), so only the tests read them back.
"""

from __future__ import annotations

import re
from typing import Any


def field(model: Any, camel_name: str) -> Any:
    if hasattr(model, camel_name):
        return getattr(model, camel_name)
    return getattr(model, re.sub(r"(?<!^)(?=[A-Z])", "_", camel_name).lower())
