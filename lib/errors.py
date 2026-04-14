"""Shared error type used by all modules.

pictl commands raise PictlError to signal a user-visible failure. The
CLI entry point catches it, emits `{"error": "..."}` to stdout, and
exits 1.
"""

from __future__ import annotations


class PictlError(Exception):
    """Any error whose message should be surfaced as JSON to the caller."""
