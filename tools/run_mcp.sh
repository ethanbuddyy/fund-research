#!/usr/bin/env bash
set -euo pipefail

TOOLS="$(cd "$(dirname "$0")" && pwd)"
VENV="$TOOLS/.mcp-venv"
SERVER="${1:-}"
shift || true

case "$SERVER" in
  sequential-thinking)
    exec node "$TOOLS/mcp-node/node_modules/@modelcontextprotocol/server-sequential-thinking/dist/index.js" "$@"
    ;;
  yfinance-market)
    exec "$VENV/bin/yfinance-market-mcp" "$@"
    ;;
  technical-analysis)
    exec "$VENV/bin/python" "$TOOLS/mcp_technical_analysis.py" "$@"
    ;;
  stockreport)
    exec "$TOOLS/stockreport-mcp/.venv/bin/python" \
      "$TOOLS/stockreport-mcp/mcp_server.py" "$@"
    ;;
  *)
    echo "unknown MCP server: $SERVER" >&2
    exit 2
    ;;
esac
