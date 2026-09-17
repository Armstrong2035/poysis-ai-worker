#!/bin/bash
# Local MCP server runner for development/testing
# Usage: ./run_mcp_local.sh <user_id>

if [ -z "$1" ]; then
    echo "Usage: ./run_mcp_local.sh <user_id>"
    echo "  user_id: Your Poysis user ID (get from dashboard)"
    echo ""
    echo "Example: ./run_mcp_local.sh abc123-def456"
    exit 1
fi

export POYSIS_API_KEY="$1"
export WORKER_URL="${WORKER_URL:-http://localhost:8000}"

echo "Starting Poysis MCP server..."
echo "  API Key: $POYSIS_API_KEY"
echo "  Worker: $WORKER_URL"
echo ""
echo "Configure in ~/.claude/settings.json:"
echo '  {'
echo '    "mcpServers": {'
echo '      "poysis": {'
echo '        "command": "python",'
echo "        \"args\": [\"$(pwd)/mcp_server.py\"],"
echo '        "env": {'
echo "          \"POYSIS_API_KEY\": \"$POYSIS_API_KEY\","
echo "          \"WORKER_URL\": \"$WORKER_URL\""
echo '        }'
echo '      }'
echo '    }'
echo '  }'
echo ""
echo "Then restart Claude Code."
echo ""

python mcp_server.py
