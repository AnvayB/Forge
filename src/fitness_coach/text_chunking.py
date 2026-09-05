"""Utilities for splitting long text to fit Discord's message-length limit."""

from __future__ import annotations

DISCORD_MESSAGE_LIMIT = 2000


def chunk_message(text: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    """Split text on line breaks into chunks no longer than `limit` characters.

    Falls back to a hard split for any single line that alone exceeds the limit.
    """

    chunks: list[str] = []
    chunk = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if chunk:
                chunks.append(chunk)
                chunk = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(chunk) + len(line) > limit:
            chunks.append(chunk)
            chunk = ""
        chunk += line
    if chunk:
        chunks.append(chunk)
    return chunks
