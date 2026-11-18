"""Capability plugin protocol and manifest definitions (ADR-029)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class CapabilityManifest:
    """Declarative metadata describing a modular capability plugin."""

    slug: str
    title_msgid: str
    always_enabled: bool = False
    required_ports: tuple[str, ...] = ()
    optional_ports: tuple[str, ...] = ()
    ui_screens: tuple[str, ...] = ()
    cli_commands: tuple[str, ...] = ()
    mcp_tools: tuple[str, ...] = ()


@runtime_checkable
class CapabilityPlugin(Protocol):
    """Protocol implemented by every capability plugin entry point."""

    def manifest(self) -> CapabilityManifest:
        """Return immutable capability metadata and surface bindings."""
        ...
