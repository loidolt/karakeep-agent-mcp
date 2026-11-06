"""A curated remote MCP server for Karakeep."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("karakeep-agent-mcp")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "0.0.0"
