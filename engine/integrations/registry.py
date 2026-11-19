"""Plugin discovery and registry via Python entry points (ADR-005)."""

import importlib
import importlib.metadata
import tomllib
from pathlib import Path
from typing import Any

PORTS: tuple[str, ...] = (
    "llm",
    "feed_source",
    "news_search",
    "extractor",
    "keyword_provider",
    "publisher",
    "analytics_source",
)

DEFAULT_SUPPORTED_LOCALES: tuple[str, ...] = ("pt-BR", "en-US")


class IntegrationMissing(Exception):
    """Raised when an action requires an unconfigured port integration."""

    def __init__(self, port: str) -> None:
        self.port = port
        super().__init__(
            f"Integration missing for port '{port}'. Please configure it in Integrations settings."
        )


class UnsupportedLocale(ValueError):
    """Raised when a plugin does not support the requested campaign locale (ADR-015, F02 AC2)."""

    def __init__(self, plugin: str, locale: str, market: str | None = None) -> None:
        self.plugin = plugin
        self.locale = locale
        self.market = market
        target = f"{locale}/{market}" if market else locale
        super().__init__(f"Plugin '{plugin}' does not support locale '{target}'")


def _load_pyproject_port_plugins(port: str, existing: dict[str, type[Any]]) -> None:
    pyproject = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
    if not pyproject.is_file():  # pragma: no cover
        return
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    eps = data.get("project", {}).get("entry-points", {}).get(f"macy.{port}", {})
    for name, target in eps.items():
        if name in existing:
            continue
        mod_path, attr = str(target).split(":", 1)
        mod = importlib.import_module(mod_path)
        existing[name] = getattr(mod, attr)


def discover_plugins(port: str | None = None) -> dict[str, dict[str, type[Any]]]:
    """Discover registered plugins across ports via entry points."""
    target_ports = (port,) if port else PORTS
    discovered: dict[str, dict[str, type[Any]]] = {p: {} for p in target_ports}

    for p in target_ports:
        group_name = f"macy.{p}"
        entry_points = importlib.metadata.entry_points(group=group_name)
        for ep in entry_points:
            try:
                plugin_cls = ep.load()
                discovered[p][ep.name] = plugin_cls
            except Exception:  # noqa: S112
                continue
        _load_pyproject_port_plugins(p, discovered[p])

    return discovered


def get_plugin(port: str, plugin_name: str) -> type[Any]:
    """Retrieve a registered plugin class by port and name, or raise IntegrationMissing."""
    plugins_by_port = discover_plugins(port).get(port, {})
    if plugin_name not in plugins_by_port:
        raise IntegrationMissing(port)
    return plugins_by_port[plugin_name]


def list_plugins() -> list[dict[str, str]]:
    """List all available plugins across all ports."""
    discovered = discover_plugins()
    results: list[dict[str, str]] = []

    for port, plugins in discovered.items():
        for name, cls in plugins.items():
            results.append(
                {
                    "port": port,
                    "plugin": name,
                    "class": f"{cls.__module__}:{cls.__qualname__}",
                }
            )

    return sorted(results, key=lambda x: (x["port"], x["plugin"]))
