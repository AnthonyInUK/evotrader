# -*- coding: utf-8 -*-
"""
实验：FilteredMCPClient 工具权限隔离验证

验证问题：FilteredMCPClient 是否真的让每个分析师只能看到自己的工具？

实验设计：
  1. 构造 StubMCPClient（16 个工具，无需真实 MCP 服务器）
     - 使用真实的 mcp.types.Tool Pydantic 对象，而非 unittest.mock
     - 实现 list_tools() + get_callable_function()，可以走完整注册路径
  2. 用 personas.yaml 白名单创建 4 个 FilteredMCPClient
  3. 对每个分析师调用 agentscope 的 toolkit.register_mcp_client()
     - 这是生产代码实际走的路径，而不是绕过它
  4. 用 toolkit.get_json_schemas() 取注册结果，断言严格等于白名单

运行方式：
    cd evotraders/backend
    python experiments/experiment_filtered_mcp_client.py
"""
import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, List

import mcp.types

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ──────────────────────────────────────────────────────────────────
# 全量工具定义（16 个，覆盖 4 个分析师的所有白名单）
# ──────────────────────────────────────────────────────────────────

ALL_TOOLS_SPEC = [
    # 共享工具
    ("extract_entities_code",  "解析A股代码/公司名 → 标准化为 600519.SH 格式"),
    ("history_calculate",      "A股历史K线 + 技术指标计算"),
    # 基本面专属
    ("crawl_ths_finance",      "财务报表（资产负债/利润/现金流）"),
    ("crawl_ths_operate",      "经营分析（主营构成/客户供应商）"),
    ("crawl_ths_company",      "公司资料（高管/参控股公司）"),
    ("crawl_ths_field",        "行业对比（行业地位/同业竞争）"),
    ("crawl_ths_capital",      "资本运作（募资/收购/质押）"),
    # 技术分析专属
    ("execute_code",           "自定义技术指标代码沙箱"),
    # 情绪分析专属
    ("crawl_ths_news",         "新闻公告（公告列表/研报/热点）"),
    ("crawl_ths_event",        "公司大事（处罚/调研/高管变动）"),
    ("crawl_ths_concept",      "概念题材（热点板块/概念对比）"),
    ("crawl_ths_holder",       "股东研究（十大股东/机构持仓变动）"),
    ("crawl_ths_position",     "主力持仓（机构持股汇总/举牌）"),
    # 估值分析专属
    ("crawl_ths_worth",        "内在价值估算（同花顺估值模型）"),
    ("crawl_ths_bonus",        "分红历史（股息率/派息记录）"),
]


def make_tool(name: str, description: str) -> mcp.types.Tool:
    """构造一个标准的 mcp.types.Tool 对象（Pydantic model，非 mock）"""
    return mcp.types.Tool(
        name=name,
        description=description,
        inputSchema={
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "A股代码"}
            },
            "required": ["ticker"],
        },
    )


ALL_TOOLS: List[mcp.types.Tool] = [
    make_tool(name, desc) for name, desc in ALL_TOOLS_SPEC
]
ALL_TOOL_NAMES = {t.name for t in ALL_TOOLS}


# ──────────────────────────────────────────────────────────────────
# StubMCPClient：不需要 SSE 连接，但实现完整接口
# ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def _dummy_client_gen(**kwargs):
    """占位用的 client generator，仅用于 MCPToolFunction 初始化（注册时不实际调用）"""
    yield (None, None)


class StubMCPClient:
    """
    模拟 HttpStatelessClient，返回预设的 16 个工具。

    为什么不用 unittest.mock：
      - mock 会绕过 FilteredMCPClient 的真实代码路径
      - 这里使用真实的 mcp.types.Tool 对象 + 真实的 FilteredMCPClient 逻辑
      - StubMCPClient 只是"换掉网络层"，注册逻辑完全真实
    """
    stateful: bool = False  # agentscope 的 StatefulClientBase 检查需要这个

    def __init__(self, name: str = "stub-mcp"):
        self.name = name

    async def list_tools(self) -> List[mcp.types.Tool]:
        return list(ALL_TOOLS)

    async def get_callable_function(
        self,
        func_name: str,
        wrap_tool_result: bool = True,
        execution_timeout: Any = None,
    ):
        """返回真实的 MCPToolFunction（携带 inputSchema，支持生成 JSON schema）"""
        from agentscope.mcp import MCPToolFunction

        # 找到对应的 Tool 对象
        target = next((t for t in ALL_TOOLS if t.name == func_name), None)
        if target is None:
            raise ValueError(f"Tool '{func_name}' not found in stub")

        return MCPToolFunction(
            mcp_name=self.name,
            tool=target,
            wrap_tool_result=wrap_tool_result,
            client_gen=_dummy_client_gen,
        )


# ──────────────────────────────────────────────────────────────────
# 实验主体
# ──────────────────────────────────────────────────────────────────

async def run_experiment():
    from agentscope.tool import Toolkit
    from backend.services.finance_mcp_service import (
        FilteredMCPClient,
        _load_analyst_tool_allowlists,
    )

    print()
    print("=" * 70)
    print("  实验：FilteredMCPClient 工具权限隔离验证")
    print(f"  StubMCPClient 工具总数: {len(ALL_TOOLS)}")
    print("=" * 70)

    # ── Step 1：读取白名单 ────────────────────────────────────────
    allowlists = _load_analyst_tool_allowlists()
    print(f"\n[Step 1] 从 personas.yaml 读取白名单：{len(allowlists)} 个分析师")
    for analyst, tools in allowlists.items():
        print(f"  {analyst:15s} → {len(tools):2d} 个工具: {tools}")

    # ── Step 2：为每个分析师创建 FilteredMCPClient + 注册 Toolkit ─
    print("\n[Step 2] 调用 register_mcp_client()（生产代码路径）")
    base_client = StubMCPClient()

    results = {}
    for analyst_type, allowed_tools in allowlists.items():
        filtered_client = FilteredMCPClient(base_client, allowed_tools)
        toolkit = Toolkit()
        toolkit.create_tool_group(
            group_name=f"finance-mcp-{analyst_type}",
            description=f"A股金融工具集 — {analyst_type} 专用",
            active=True,
        )
        await toolkit.register_mcp_client(
            filtered_client,
            group_name=f"finance-mcp-{analyst_type}",
        )
        registered_names = {
            s["function"]["name"]
            for s in toolkit.get_json_schemas()
        }
        results[analyst_type] = {
            "expected": set(allowed_tools),
            "registered": registered_names,
        }
        print(f"  {analyst_type:15s} → 注册了 {len(registered_names)} 个工具")

    # ── Step 3：断言验证 ─────────────────────────────────────────
    print("\n[Step 3] 验证断言")
    all_passed = True
    for analyst_type, data in results.items():
        expected  = data["expected"]
        registered = data["registered"]

        # 断言 1：注册工具集 == 白名单（精确相等，无多无少）
        extra   = registered - expected    # 注册了白名单外的工具
        missing = expected - registered    # 白名单里的工具没被注册

        if extra or missing:
            all_passed = False
            print(f"  ❌ {analyst_type}")
            if extra:
                print(f"     多注册了: {extra}")
            if missing:
                print(f"     漏注册了: {missing}")
        else:
            print(f"  ✅ {analyst_type:15s} 精确匹配 ({len(expected)} 个工具)")

    # ── Step 4：隔离性验证——工具不应跨角色可见 ────────────────────
    print("\n[Step 4] 隔离性验证（工具不应跨角色可见）")
    isolation_ok = True

    # "execute_code" 是 technical 专属，不应出现在其他 Toolkit 中
    for analyst_type, data in results.items():
        if analyst_type == "technical":
            continue
        if "execute_code" in data["registered"]:
            isolation_ok = False
            print(f"  ❌ execute_code 不应在 {analyst_type} 中出现！")

    # "crawl_ths_worth" 是 valuation 专属
    for analyst_type, data in results.items():
        if analyst_type == "valuation":
            continue
        if "crawl_ths_worth" in data["registered"]:
            isolation_ok = False
            print(f"  ❌ crawl_ths_worth 不应在 {analyst_type} 中出现！")

    if isolation_ok:
        print("  ✅ 专属工具严格隔离，无跨角色泄漏")

    # ── Step 5：汇总报告 ─────────────────────────────────────────
    print("\n[Step 5] 汇总报告")
    header = f"  {'分析师':15s}  {'白名单数':>6}  {'注册数':>6}  {'匹配':>5}  工具列表"
    print(header)
    print("  " + "-" * 70)
    for analyst_type, data in results.items():
        expected   = data["expected"]
        registered = data["registered"]
        match = "✅" if expected == registered else "❌"
        tools_str = ", ".join(sorted(registered))
        print(f"  {analyst_type:15s}  {len(expected):>6}  {len(registered):>6}  {match:>5}  {tools_str}")

    print()
    print(f"  全部白名单工具数量（去重）: {len(set().union(*[d['expected'] for d in results.values()]))}")
    print(f"  StubMCPClient 提供工具数量: {len(ALL_TOOLS)}")

    print()
    if all_passed and isolation_ok:
        print("  🎉 实验通过：FilteredMCPClient 工具权限隔离验证成功")
    else:
        print("  💥 实验失败：存在上述问题")
    print("=" * 70)
    print()

    return all_passed and isolation_ok


if __name__ == "__main__":
    success = asyncio.run(run_experiment())
    sys.exit(0 if success else 1)
