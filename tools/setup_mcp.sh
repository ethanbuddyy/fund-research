#!/usr/bin/env bash
# MCP 扩展服务器安装脚本
# 用法: bash tools/setup_mcp.sh

set -euo pipefail
TOOLS="$(cd "$(dirname "$0")" && pwd)"
VENV="$TOOLS/.mcp-venv"
STOCKREPORT_COMMIT="b3baeb1054a2153ef1235e29d571e7c11abc65a4"
SEQUENTIAL_THINKING_VERSION="2025.12.18"

echo "=== 安装 MCP 扩展服务器 ==="

if ! command -v uv >/dev/null 2>&1; then
  echo "缺少 uv。请先通过系统包管理器安装 uv，再重新运行本脚本。" >&2
  exit 1
fi

# 1. 隔离 Python 环境，顶层依赖精确固定
uv venv --python 3.12 "$VENV"
uv pip install --python "$VENV/bin/python" \
  "baostock==0.9.1" "akshare==1.18.64" "fastmcp==3.3.1" \
  "httpx==0.28.1" "yfinance-market-mcp==0.3.3"

# 2. StockReport MCP 固定到审计过的提交
if [ ! -d "$TOOLS/stockreport-mcp" ]; then
  echo "[2/2] 克隆 stockreport-mcp..."
  git clone https://github.com/jamesdingAI/stockreport-mcp "$TOOLS/stockreport-mcp"
else
  git -C "$TOOLS/stockreport-mcp" fetch origin "$STOCKREPORT_COMMIT"
fi
git -C "$TOOLS/stockreport-mcp" checkout --detach "$STOCKREPORT_COMMIT"
uv sync --frozen --project "$TOOLS/stockreport-mcp"

# 3. Node MCP 固定版本安装到仓库局部目录，不在启动时下载
npm install --prefix "$TOOLS/mcp-node" --ignore-scripts --save-exact \
  "@modelcontextprotocol/server-sequential-thinking@$SEQUENTIAL_THINKING_VERSION"

chmod +x "$TOOLS/run_mcp.sh"

echo ""
echo "=== 安装完成 ==="
echo "MCP 服务器配置: .mcp.json"
echo "启动 Claude Code 后，接受提示即可使用以下 MCP："
echo "  - sequential-thinking  (本地固定 npm 版本)"
echo "  - yfinance-market      (隔离 venv 中的固定 PyPI 版本)"
echo "  - technical-analysis   (tools/mcp_technical_analysis.py)"
echo "  - stockreport          (tools/stockreport-mcp/)"
