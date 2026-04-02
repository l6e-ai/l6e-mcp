"""l6e-mcp — session-scoped budget enforcement MCP server."""
import os

# Use litellm's bundled cost map by default so the MCP process never blocks
# on a network fetch to raw.githubusercontent.com at import time.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

__version__ = "0.7.0"
