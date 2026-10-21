"""Shared response metadata for database-backed page-number pagination."""
from __future__ import annotations
import math
from typing import Any

def metadata(page: int, page_size: int, total: int | None) -> dict[str, Any]:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer.")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 100:
        raise ValueError("page_size must be between 1 and 100.")
    if total is not None and (isinstance(total, bool) or not isinstance(total, int) or total < 0):
        raise ValueError("total must be a non-negative integer or None.")
    pages = max(1, math.ceil(total / page_size)) if total is not None else None
    return {"page": page, "page_size": page_size, "total": total, "pages": pages,
            "has_previous": page > 1, "has_next": page < pages if pages is not None else None}
