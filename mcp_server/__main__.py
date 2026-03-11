"""Entry point for ``python -m mcp_server``."""

from config.config_loader import get_config
from mcp_server.server import mcp

mcp.run(transport=get_config().mcp_server.transport)
