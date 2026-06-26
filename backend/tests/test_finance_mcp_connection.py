# -*- coding: utf-8 -*-
"""
finance-mcp 连接测试

前提：finance-mcp 服务已在本地运行
    bash evotraders/start_finance_mcp.sh

运行方式（完整集成测试，需要服务在跑）：
    cd evotraders/backend
    FINANCE_MCP_URL=http://127.0.0.1:8040/sse pytest tests/test_finance_mcp_connection.py -v -s

普通 CI（不需要服务，只跑降级逻辑）：
    cd evotraders/backend
    pytest tests/test_finance_mcp_connection.py::TestFallbackBehavior -v

诊断脚本：
    cd evotraders/backend
    FINANCE_MCP_URL=http://127.0.0.1:8040/sse python tests/test_finance_mcp_connection.py
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest

# 把 backend 加入路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# ── 期望的完整 19 个工具列表 ──────────────────────────────────
EXPECTED_TOOLS = {
    # 实体 & 计算
    "extract_entities_code",
    "history_calculate",
    # 通用能力
    "execute_code",
    "execute_shell",
    "crawl_url",
    # 同花顺专项（13 个）
    "crawl_ths_company",
    "crawl_ths_holder",
    "crawl_ths_operate",
    "crawl_ths_equity",
    "crawl_ths_capital",
    "crawl_ths_worth",
    "crawl_ths_news",
    "crawl_ths_concept",
    "crawl_ths_position",
    "crawl_ths_finance",
    "crawl_ths_bonus",
    "crawl_ths_event",
    "crawl_ths_field",
}

# 单独的 skip 条件，只用在需要真实服务的测试方法上
_needs_server = pytest.mark.skipif(
    not os.getenv("FINANCE_MCP_URL"),
    reason="需要设置 FINANCE_MCP_URL 并运行 finance-mcp 服务（bash start_finance_mcp.sh）",
)


# ── fixtures ─────────────────────────────────────────────────

@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
async def mcp_status(event_loop):
    """获取一次连接状态，模块内复用"""
    from backend.services.finance_mcp_service import verify_finance_mcp_connection
    return await verify_finance_mcp_connection()


# ──────────────────────────────────────────────────────────────
# 集成测试（需要真实 finance-mcp 服务）
# ──────────────────────────────────────────────────────────────

class TestFinanceMcpConnection:
    """验证 finance-mcp 服务连通性（需要服务在跑）"""

    @_needs_server
    def test_service_is_reachable(self):
        """服务应当可以连接"""
        async def _run():
            from backend.services.finance_mcp_service import verify_finance_mcp_connection
            status = await verify_finance_mcp_connection()
            assert status["connected"], (
                f"无法连接 finance-mcp: {status['error']}\n"
                f"URL: {status['url']}\n"
                f"请先运行: bash evotraders/start_finance_mcp.sh"
            )
        asyncio.run(_run())

    @_needs_server
    def test_tool_count_not_empty(self):
        """工具数量应大于 0"""
        async def _run():
            from backend.services.finance_mcp_service import verify_finance_mcp_connection
            status = await verify_finance_mcp_connection()
            assert status["tool_count"] > 0, "服务返回了 0 个工具"
        asyncio.run(_run())

    @_needs_server
    def test_core_tools_present(self):
        """核心工具应该都在"""
        async def _run():
            from backend.services.finance_mcp_service import verify_finance_mcp_connection
            status = await verify_finance_mcp_connection()
            core_tools = {"history_calculate", "crawl_ths_finance"}
            actual_tools = set(status["tool_names"])
            missing = core_tools - actual_tools
            assert not missing, f"缺少核心工具: {missing}"
        asyncio.run(_run())

    @_needs_server
    def test_all_ths_tools_present(self):
        """所有 crawl_ths_* 工具应当存在"""
        async def _run():
            from backend.services.finance_mcp_service import verify_finance_mcp_connection
            status = await verify_finance_mcp_connection()
            ths_tools = {t for t in EXPECTED_TOOLS if t.startswith("crawl_ths_")}
            actual_tools = set(status["tool_names"])
            missing = ths_tools - actual_tools
            assert not missing, f"缺少同花顺工具: {missing}"
        asyncio.run(_run())

    @_needs_server
    def test_full_toolkit_registration(self):
        """测试完整的 Toolkit 注册流程（包括 HttpStatelessClient）"""
        async def _run():
            # 重置 singleton 以便测试
            import backend.services.finance_mcp_service as svc
            svc._TOOLKIT = None
            svc._INIT_FAILED = False
            toolkit = await svc.get_finance_mcp_toolkit()
            assert toolkit is not None, "Toolkit 初始化失败"

            schemas = toolkit.get_json_schemas()
            assert len(schemas) > 0, "Toolkit 中没有注册任何工具"

            tool_names = [s.get("function", {}).get("name") for s in schemas]
            assert "crawl_ths_finance" in tool_names, "crawl_ths_finance 未在 Toolkit 中"
        asyncio.run(_run())


# ──────────────────────────────────────────────────────────────
# 降级逻辑测试（不依赖运行中的服务，always run）
# ──────────────────────────────────────────────────────────────

class TestFallbackBehavior:
    """验证降级逻辑，不依赖 finance-mcp 服务，始终运行"""

    def test_is_finance_mcp_enabled_false_when_unset(self):
        """未设置 FINANCE_MCP_URL 时应返回 False"""
        from backend.services.finance_mcp_service import is_finance_mcp_enabled
        original = os.environ.pop("FINANCE_MCP_URL", None)
        try:
            assert not is_finance_mcp_enabled(), "未设置 FINANCE_MCP_URL 时应返回 False"
        finally:
            if original:
                os.environ["FINANCE_MCP_URL"] = original

    def test_is_finance_mcp_enabled_true_when_set(self):
        """设置 FINANCE_MCP_URL 后应返回 True"""
        from backend.services.finance_mcp_service import is_finance_mcp_enabled
        original = os.environ.pop("FINANCE_MCP_URL", None)
        try:
            os.environ["FINANCE_MCP_URL"] = "http://127.0.0.1:8040/sse"
            assert is_finance_mcp_enabled(), "设置 FINANCE_MCP_URL 后应返回 True"
        finally:
            os.environ.pop("FINANCE_MCP_URL", None)
            if original:
                os.environ["FINANCE_MCP_URL"] = original

    def test_create_toolkit_returns_local_when_no_mcp(self):
        """analyst_toolkits={} 时，create_toolkit 应返回本地 Python 工具集"""
        original = os.environ.pop("FINANCE_MCP_URL", None)
        try:
            from backend.main import create_toolkit
            # analyst_toolkits 为空字典 → 降级到本地工具
            toolkit = create_toolkit("fundamentals_analyst", analyst_toolkits={})
            assert toolkit is not None, "本地 Toolkit 不应为 None"
        finally:
            if original:
                os.environ["FINANCE_MCP_URL"] = original

    def test_get_finance_mcp_toolkit_returns_none_when_disabled(self):
        """未设置 FINANCE_MCP_URL 时，get_finance_mcp_toolkit 应返回 None"""
        async def _run():
            import backend.services.finance_mcp_service as svc
            # 重置 singleton
            svc._TOOLKIT = None
            svc._INIT_FAILED = False
            original = os.environ.pop("FINANCE_MCP_URL", None)
            try:
                result = await svc.get_finance_mcp_toolkit()
                assert result is None, "未启用时应返回 None"
            finally:
                if original:
                    os.environ["FINANCE_MCP_URL"] = original
                # 重置 singleton
                svc._TOOLKIT = None
                svc._INIT_FAILED = False

        asyncio.run(_run())


# ── 直接运行时：打印诊断报告 ─────────────────────────────────

async def _print_diagnosis():
    url = os.getenv("FINANCE_MCP_URL")
    if not url:
        print("\n⚠️  FINANCE_MCP_URL 未设置")
        print("  请先运行: bash evotraders/start_finance_mcp.sh")
        print("  然后: FINANCE_MCP_URL=http://127.0.0.1:8040/sse python tests/test_finance_mcp_connection.py")
        return

    print(f"\n🔍 诊断 finance-mcp 连接: {url}")
    print("-" * 50)

    from backend.services.finance_mcp_service import verify_finance_mcp_connection
    status = await verify_finance_mcp_connection()

    print(f"  启用状态:   {'✅ 已启用' if status['enabled'] else '❌ 未启用'}")
    print(f"  连接状态:   {'✅ 成功' if status['connected'] else '❌ 失败'}")
    print(f"  服务地址:   {status['url']}")
    print(f"  工具数量:   {status['tool_count']}")

    if status["connected"]:
        print("  工具列表:")
        for name in sorted(status["tool_names"]):
            tag = "✅" if name in EXPECTED_TOOLS else "⚠️ "
            print(f"    {tag} {name}")

        actual = set(status["tool_names"])
        missing = EXPECTED_TOOLS - actual
        if missing:
            print(f"\n  ⚠️  缺少以下工具（可能需要 config=default,ths）:")
            for name in sorted(missing):
                print(f"       - {name}")
        else:
            print(f"\n  ✅ 所有 {len(EXPECTED_TOOLS)} 个预期工具均已注册")
    else:
        print(f"\n  错误详情: {status['error']}")
        print("\n  排查步骤:")
        print("    1. 确认服务已启动: bash evotraders/start_finance_mcp.sh")
        print("    2. 确认端口正确: curl http://127.0.0.1:8040/sse")
        print("    3. 检查 TUSHARE_API_TOKEN 和 DASHSCOPE_API_KEY 是否设置")


if __name__ == "__main__":
    asyncio.run(_print_diagnosis())
