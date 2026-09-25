"""Centralised redaction helpers for any text that might leak credentials."""

from __future__ import annotations

import os
import re

_MAX_LEN = 500


def sanitize_error(message: object, *, max_len: int = _MAX_LEN) -> str:
    """Return ``message`` with credential-looking substrings redacted.

    Redactions applied, in order:
      1. The literal value of ``FOFOCA_DATABASE_URL`` (if set).
      2. ``scheme://user:password@`` inside any URL.
      3. ``password=...`` / ``passwd=...`` / ``pwd=...`` tokens.
    """
    text = str(message)

    # 1. Literal FOFOCA_DATABASE_URL value (most specific first).
    url = os.environ.get("FOFOCA_DATABASE_URL", "").strip()
    if url and url in text:
        text = text.replace(url, "<FOFOCA_DATABASE_URL>")

    # 2. URL userinfo (only when a password component is present).
    text = re.sub(r"://[^:/\s]+:[^@\s]+@", "://***:***@", text)

    # 3. password= style tokens
    text = re.sub(r"(?i)(password|passwd|pwd)\s*=\s*([^\s&]+)", r"\1=***", text)

    if len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text


__all__ = ["sanitize_error"]
