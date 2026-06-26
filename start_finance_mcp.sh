#!/usr/bin/env bash
# ============================================================
# start_finance_mcp.sh — 本地启动 finance-mcp SSE 服务
#
# 用法：
#   bash start_finance_mcp.sh               # 默认端口 8040
#   FINANCE_MCP_PORT=9000 bash start_finance_mcp.sh
#
# 所需环境变量（可放在 .env 或 export 设置）：
#   TUSHARE_API_TOKEN    — A股历史行情（history_calculate 工具）
#   DASHSCOPE_API_KEY    — DashScope 搜索 + LLM（搜索/实体识别工具）
#
# 可选环境变量：
#   TAVILY_API_KEY       — Tavily 搜索（不设置则用 DashScope 替代）
#   FINANCE_MCP_PORT     — 监听端口，默认 8040
#   OPENAI_API_KEY       — 若 LLM 走 OpenAI 兼容接口
#   OPENAI_BASE_URL      — LLM 接口地址（默认指向 DashScope）
# ============================================================

set -euo pipefail

# ── 1. 加载 .env（如果存在）────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
if [[ -f "${ENV_FILE}" ]]; then
    echo "📂 加载 .env: ${ENV_FILE}"
    set -a
    # shellcheck source=/dev/null
    source "${ENV_FILE}"
    set +a
else
    echo "⚠️  未找到 .env 文件，使用当前 shell 环境变量"
fi

# ── 2. 端口配置 ────────────────────────────────────────────
PORT="${FINANCE_MCP_PORT:-8040}"

# ── 3. 检查必要依赖 ─────────────────────────────────────────
check_dep() {
    python3 -c "import ${1}" 2>/dev/null || {
        echo "❌ 缺少依赖: ${1}，请先运行: pip install finance-mcp"
        exit 1
    }
}
check_dep "finance_mcp"

# ── 4. 检查 API Key（仅警告，不中断）──────────────────────
if [[ -z "${TUSHARE_API_TOKEN:-}" ]]; then
    echo "⚠️  TUSHARE_API_TOKEN 未设置 → history_calculate 工具将不可用"
    echo "   申请地址: https://tushare.pro/register"
fi

if [[ -z "${DASHSCOPE_API_KEY:-}" ]]; then
    echo "⚠️  DASHSCOPE_API_KEY 未设置 → dashscope_search / extract_entities_code 将不可用"
    echo "   申请地址: https://dashscope.aliyuncs.com"
fi

# ── 5. 设置 LLM（finance-mcp 内部用于 history_calculate 的分析代码生成）──
# 如果没有单独的 OPENAI_API_KEY，复用 DASHSCOPE_API_KEY
if [[ -z "${OPENAI_API_KEY:-}" ]] && [[ -n "${DASHSCOPE_API_KEY:-}" ]]; then
    export OPENAI_API_KEY="${DASHSCOPE_API_KEY}"
    export OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1}"
    echo "🔑 OPENAI_API_KEY → 复用 DASHSCOPE_API_KEY（DashScope 兼容接口）"
fi

# ── 6. 启动 finance-mcp SSE 服务 ──────────────────────────
echo ""
echo "============================================================"
echo "  启动 finance-mcp MCP 服务"
echo "  SSE 地址: http://127.0.0.1:${PORT}/sse"
echo "  工具集:   default + ths（同花顺）"
echo "  禁用流程: tavily_search, mock_search, react_agent"
echo "============================================================"
echo ""

# finance-mcp 使用 hydra 风格 CLI
# config=default,ths 合并两套工具
# disabled_flows 禁用不需要的流程（节省资源）
python3 -m finance_mcp.main \
    config=default,ths \
    "disabled_flows=[\"tavily_search\",\"mock_search\",\"react_agent\"]" \
    mcp.transport=sse \
    mcp.host=127.0.0.1 \
    mcp.port="${PORT}"
