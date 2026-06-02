"""Helpers for building Anthropic content blocks with/without prompt caching."""


def build_cached_block(text: str) -> dict:
    """Return a text content block with ephemeral cache_control."""
    return {
        "type": "text",
        "text": text,
        "cache_control": {"type": "ephemeral"},
    }


def build_uncached_block(text: str) -> dict:
    """Return a plain text content block (no caching)."""
    return {"type": "text", "text": text}
