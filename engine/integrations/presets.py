"""Stack presets pairing LLM, search, keyword, feed, extractor, and publisher plugins (ADR-031)."""

from __future__ import annotations

from typing import Any

INTEGRATION_PRESETS: dict[str, dict[str, Any]] = {
    "pocket_free": {
        "name": "pocket_free",
        "label": "Pocket Free (Zero-Key)",
        "description": (
            "Zero-key local stack: Google News RSS, public Autocomplete, RSS, Trafilatura, "
            "and Markdown Export."
        ),
        "ports": {
            "news_search": "gnews_rss",
            "keyword_provider": "autocomplete",
            "feed_source": "rss",
            "extractor": "trafilatura",
            "publisher": "markdown_export",
        },
    },
    "local_ollama": {
        "name": "local_ollama",
        "label": "Local Ollama + Free Stack",
        "description": (
            "Local Ollama LLM + zero-key Google News RSS, Autocomplete, RSS, Trafilatura, "
            "and Markdown Export."
        ),
        "ports": {
            "llm": "ollama",
            "news_search": "gnews_rss",
            "keyword_provider": "autocomplete",
            "feed_source": "rss",
            "extractor": "trafilatura",
            "publisher": "markdown_export",
        },
    },
    "pro_cloud": {
        "name": "pro_cloud",
        "label": "Pro Cloud",
        "description": (
            "Full cloud API stack: OpenRouter, Brave Search, DataForSEO, RSS, Trafilatura, "
            "and HTTP Webhook."
        ),
        "ports": {
            "llm": "openrouter",
            "news_search": "brave",
            "keyword_provider": "dataforseo",
            "feed_source": "rss",
            "extractor": "trafilatura",
            "publisher": "http_webhook",
        },
    },
}


def list_presets() -> list[dict[str, Any]]:
    """Return all predefined integration stack presets in display order."""
    return [
        {
            "name": entry["name"],
            "label": entry["label"],
            "description": entry["description"],
            "ports": dict(entry["ports"]),
        }
        for entry in INTEGRATION_PRESETS.values()
    ]


def get_preset(preset_name: str) -> dict[str, Any]:
    """Return a validated integration stack preset or raise ValueError."""
    normalized = preset_name.strip().lower()
    entry = INTEGRATION_PRESETS.get(normalized)
    if entry is None:
        available = ", ".join(INTEGRATION_PRESETS.keys())
        msg = f"Unknown integration preset '{preset_name}'; expected one of: {available}"
        raise ValueError(msg)
    return {
        "name": entry["name"],
        "label": entry["label"],
        "description": entry["description"],
        "ports": dict(entry["ports"]),
    }
