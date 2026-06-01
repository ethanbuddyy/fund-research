#!/usr/bin/env bash
# MCP 扩展服务器安装脚本
# 用法: bash tools/setup_mcp.sh

set -e
TOOLS="$(cd "$(dirname "$0")" && pwd)"

echo "=== 安装 MCP 扩展服务器 ==="

# 1. uv 包管理器
if ! command -v uv &>/dev/null; then
  echo "[1/2] 安装 uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
else
  echo "[1/2] uv 已安装: $(uv --version)"
fi

# 2. StockReport MCP（A股/港股/美股，无需 API Key）
if [ ! -d "$TOOLS/stockreport-mcp" ]; then
  echo "[2/2] 克隆 stockreport-mcp..."
  git clone --depth=1 https://github.com/jamesdingAI/stockreport-mcp "$TOOLS/stockreport-mcp"
else
  echo "[2/2] stockreport-mcp 已存在，跳过克隆"
fi

echo "[2/2] 安装 stockreport-mcp 依赖..."
export PATH="$HOME/.local/bin:$PATH"
pip install --user --break-system-packages baostock akshare fastmcp httpx 2>/dev/null || \
pip install --user baostock akshare fastmcp httpx

echo ""
echo "=== 安装完成 ==="
echo "MCP 服务器配置: .mcp.json"
echo "启动 Claude Code 后，接受提示即可使用以下 MCP："
echo "  - sequential-thinking  (npx，首次自动下载)"
echo "  - yfinance-market      (pip install yfinance-market-mcp)"
echo "  - technical-analysis   (tools/mcp_technical_analysis.py)"
echo "  - stockreport          (tools/stockreport-mcp/)"
