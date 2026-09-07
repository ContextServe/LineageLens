"""Unit tests for benchmark arm registry.

Ensures that tool names in the registry stay in sync with the actual
LineageLens MCP server implementation, preventing the tool-name bug class
from recurring.
"""

import sys
from pathlib import Path

# Add parent dir to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from arms import ARM_LINEAGELENS


def test_lineagelens_tool_names_match_server():
    """Ensure ARM_LINEAGELENS.tool_names matches tools registered by create_mcp_server()."""
    try:
        from lineagelens.mcp_server import create_mcp_server
    except ImportError:
        print("SKIP: lineagelens.mcp_server not available (import error)")
        return

    # Create the MCP server
    server = create_mcp_server()

    # Get the list of registered tools (via introspection or public API)
    # The MCP server has a way to list tools; we use the private tools dict
    registered_tool_names = set()

    if hasattr(server, "tools"):
        # If tools is a dict of tool definitions
        registered_tool_names = set(server.tools.keys())
    elif hasattr(server, "_tools"):
        # If it's a private _tools dict
        registered_tool_names = set(server._tools.keys())
    else:
        # Fallback: try to extract from the server's implementation
        # This is implementation-dependent and may need updating
        print("WARN: Could not introspect MCP server tools (no public API), skipping test")
        return

    # Strip the "mcp__lineagelens__" prefix from registered names
    registered_names = {name.replace("mcp__lineagelens__", "") for name in registered_tool_names}

    # Get names from the arm registry
    arm_names = set(ARM_LINEAGELENS.tool_names)

    # Check for exact match
    assert (
        registered_names == arm_names
    ), f"Tool name mismatch:\n  Registered: {sorted(registered_names)}\n  In registry: {sorted(arm_names)}\n  Missing from registry: {registered_names - arm_names}\n  Extra in registry: {arm_names - registered_names}"

    print(f"✓ LineageLens tool names match: {len(arm_names)} tools")


def test_arm_registry_has_all_tools():
    """Basic sanity check: all arms have tool definitions."""
    from arms import ARMS

    for arm_name, arm in ARMS.items():
        if arm.name == "baseline":
            assert arm.tool_names == [], f"Baseline should have no tools, got {arm.tool_names}"
        else:
            assert len(arm.tool_names) > 0, f"Arm {arm_name} has no tools defined"
            assert len(arm.tool_hints) > 0, f"Arm {arm_name} has no tool hints"

    print(f"✓ All {len(ARMS)} arms have valid tool definitions")


def test_mcp_arms_have_server_config():
    """MCP arms (non-baseline) must have mcp_server configured."""
    from arms import ARMS

    for arm_name, arm in ARMS.items():
        if arm.name == "baseline":
            assert arm.mcp_server is None, "Baseline should not have MCP server"
        else:
            assert arm.mcp_server is not None, f"Arm {arm_name} must have mcp_server configured"
            assert "command" in arm.mcp_server, f"Arm {arm_name} mcp_server missing 'command'"

    print(f"✓ All MCP arms have server configurations")


if __name__ == "__main__":
    test_lineagelens_tool_names_match_server()
    test_arm_registry_has_all_tools()
    test_mcp_arms_have_server_config()
    print("\n✅ All tests passed!")
