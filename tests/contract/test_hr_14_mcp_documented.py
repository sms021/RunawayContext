"""HR-14 contract: every MCP tool has an inputSchema + description."""

import pytest
from runaway_context.mcp_server import serve_test


@pytest.mark.contract
def test_hr_14_mcp_tools_documented():
    """HR-14: every MCP tool returned by tools/list has description + inputSchema."""
    [resp] = serve_test([{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])
    tools = resp["result"]["tools"]
    assert len(tools) == 13, f"expected 13 tools, got {len(tools)}"
    for t in tools:
        assert t.get("name"), f"tool missing name: {t}"
        assert t.get("description"), f"tool {t['name']} missing description"
        assert t.get("inputSchema"), f"tool {t['name']} missing inputSchema"
        schema = t["inputSchema"]
        assert schema.get("type") == "object", f"tool {t['name']} inputSchema not object"
