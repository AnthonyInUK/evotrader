# -*- coding: utf-8 -*-
"""
finance_mcp_service.py — EvoTraders × finance-mcp 连接层

职责：
  - 懒加载 finance-mcp 远端 Toolkit（每个进程只初始化一次）
  - 带重试 + jitter（防止并发启动时惊群）
  - 连接失败时降级到本地 Python 工具，不崩溃

使用方式（在 main.py 的 create_toolkit 里）：
    toolkit = await get_finance_mcp_toolkit()
    if toolkit is None:
        toolkit = create_local_toolkit(analyst_type)  # 降级

环境变量：
    FINANCE_MCP_URL           — MCP 服务地址，默认 http://127.0.0.1:8040/sse
    FINANCE_MCP_AUTH_TOKEN    — 可选 Bearer Token
    FINANCE_MCP_INIT_JITTER   — 最大抖动秒数，默认 2（多进程并发时防惊群）
    FINANCE_MCP_MAX_RETRIES   — 最大重试次数，默认 3
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 进程级单例 ──────────────────────────────────────────────
_TOOLKIT: Optional[object] = None         # agentscope.tool.Toolkit（完整工具集，向后兼容）
_TOOLKIT_LOCK: Optional[asyncio.Lock] = None
_INIT_FAILED: bool = False                # 曾经连接失败，不再重试

# ── 按角色过滤的单例 ──────────────────────────────────────────
_BASE_CLIENT: Optional[object] = None     # 共享 HttpStatelessClient
_ANALYST_TOOLKITS: Optional[Dict[str, object]] = None  # {analyst_type: Toolkit}
_ANALYST_TOOLKITS_LOCK: Optional[asyncio.Lock] = None


def _get_lock() -> asyncio.Lock:
    """懒加载锁（避免模块导入时就创建事件循环）"""
    global _TOOLKIT_LOCK
    if _TOOLKIT_LOCK is None:
        _TOOLKIT_LOCK = asyncio.Lock()
    return _TOOLKIT_LOCK


def _get_analyst_lock() -> asyncio.Lock:
    """按角色 Toolkit 的懒加载锁"""
    global _ANALYST_TOOLKITS_LOCK
    if _ANALYST_TOOLKITS_LOCK is None:
        _ANALYST_TOOLKITS_LOCK = asyncio.Lock()
    return _ANALYST_TOOLKITS_LOCK


# ── FilteredMCPClient ─────────────────────────────────────────
class FilteredMCPClient:
    """
    包装 HttpStatelessClient，只暴露 allowed_tools 中的工具。

    工作原理：
        register_mcp_client(client) 内部会先调用 client.list_tools()，
        再为每个工具生成 callable。
        只要我们覆写 list_tools() 返回子集，注册结果就只含允许的工具。
        call_tool() / 其他方法直接委托底层 client，无需改动。

    Args:
        client: HttpStatelessClient 实例（底层 MCP 传输）
        allowed_tools: 该分析师允许使用的工具名列表
    """

    def __init__(self, client, allowed_tools: List[str]) -> None:
        self._client = client
        self._allowed: frozenset = frozenset(allowed_tools)

    async def list_tools(self):
        """只返回白名单内的工具"""
        all_tools = await self._client.list_tools()
        filtered = [t for t in all_tools if t.name in self._allowed]
        logger.debug(
            "FilteredMCPClient: %d/%d 工具通过白名单 %s",
            len(filtered), len(all_tools), sorted(self._allowed),
        )
        return filtered

    def __getattr__(self, name: str):
        """其他所有方法/属性委托给底层 client"""
        return getattr(self._client, name)


# ── personas.yaml 工具白名单读取 ──────────────────────────────
def _load_analyst_tool_allowlists() -> Dict[str, List[str]]:
    """
    从 personas.yaml 读取每个分析师的 finance_mcp_tools 列表。

    Returns:
        {
            "fundamentals": ["extract_entities_code", "crawl_ths_finance", ...],
            "technical":    ["extract_entities_code", "history_calculate", ...],
            "sentiment":    [...],
            "valuation":    [...],
        }
    """
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML 未安装，无法读取 personas.yaml，将使用空白名单")
        return {}

    personas_path = (
        Path(__file__).parent.parent
        / "agents" / "prompts" / "analyst" / "personas.yaml"
    )
    if not personas_path.exists():
        logger.warning("personas.yaml 不存在: %s", personas_path)
        return {}

    with open(personas_path, encoding="utf-8") as f:
        personas = yaml.safe_load(f) or {}

    # persona key → short analyst_type 的映射
    persona_to_type = {
        "fundamentals_analyst": "fundamentals",
        "technical_analyst":    "technical",
        "sentiment_analyst":    "sentiment",
        "valuation_analyst":    "valuation",
    }

    result: Dict[str, List[str]] = {}
    for persona_key, analyst_type in persona_to_type.items():
        persona = personas.get(persona_key, {})
        tools = persona.get("finance_mcp_tools", [])
        # YAML 注释已由解析器去掉，值本身是干净的字符串
        result[analyst_type] = [str(t).strip() for t in tools if t]

    return result


def is_finance_mcp_enabled() -> bool:
    """
    判断是否应该尝试连接 finance-mcp。

    条件：FINANCE_MCP_URL 环境变量已设置（非空）。
    不设置 = 用户明确选择只用本地工具。
    """
    return bool(os.getenv("FINANCE_MCP_URL", "").strip())


async def get_finance_mcp_toolkit():
    """
    获取 finance-mcp Toolkit（懒加载，单例）。

    Returns:
        agentscope.tool.Toolkit — 成功时返回已注册的 Toolkit
        None                    — 未启用或连接失败（已降级）

    ⚠️  Notes:
    - 第一次调用时会阻塞直到连接成功或重试耗尽
    - 连接失败后置 _INIT_FAILED=True，后续调用直接返回 None（不再重试）
    - 多进程环境下，每个进程独立维护自己的 singleton
    """
    global _TOOLKIT, _INIT_FAILED

    # 快速路径：已初始化 / 已知失败
    if _TOOLKIT is not None:
        return _TOOLKIT
    if _INIT_FAILED:
        return None
    if not is_finance_mcp_enabled():
        return None

    async with _get_lock():
        # double-checked locking
        if _TOOLKIT is not None:
            return _TOOLKIT
        if _INIT_FAILED:
            return None

        result = await _init_toolkit()
        if result is None:
            _INIT_FAILED = True
        else:
            _TOOLKIT = result
        return _TOOLKIT


async def _init_toolkit():
    """
    实际初始化逻辑（带重试 + jitter）。
    """
    try:
        from agentscope.tool import Toolkit
        from agentscope.mcp import HttpStatelessClient
    except ImportError:
        logger.error("agentscope 未安装，无法使用 finance-mcp 工具")
        return None

    url = os.getenv("FINANCE_MCP_URL", "http://127.0.0.1:8040/sse")
    transport = os.getenv("FINANCE_MCP_TRANSPORT", "sse")
    max_retries = int(os.getenv("FINANCE_MCP_MAX_RETRIES", "3"))
    jitter_max = float(os.getenv("FINANCE_MCP_INIT_JITTER", "2"))

    logger.info("⏳ 连接 finance-mcp: %s (transport=%s)", url, transport)

    # 多进程启动时加随机 jitter，避免同时发起连接
    if jitter_max > 0:
        await asyncio.sleep(random.uniform(0, jitter_max))

    # 构造 client
    headers: Dict[str, str] = {}
    auth_token = os.getenv("FINANCE_MCP_AUTH_TOKEN")
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    try:
        client = HttpStatelessClient(
            name="finance-mcp",
            transport=transport,
            url=url,
            headers=headers,
            timeout=60,
            sse_read_timeout=600,
        )
    except TypeError:
        # 旧版 agentscope 不支持 timeout 参数
        client = HttpStatelessClient(
            name="finance-mcp",
            transport=transport,
            url=url,
            headers=headers,
        )

    last_err: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            tools_list = await client.list_tools()
            if not tools_list:
                raise ValueError("list_tools 返回空列表，服务可能未完全启动")

            tool_names = [t.name for t in tools_list]
            logger.info(
                "✅ finance-mcp 连接成功！共 %d 个工具: %s",
                len(tool_names),
                tool_names,
            )

            toolkit = Toolkit()
            toolkit.create_tool_group(
                group_name="finance-mcp",
                description="A股金融工具集（同花顺数据 + 历史行情 + 搜索）",
                active=True,
            )
            await toolkit.register_mcp_client(client, group_name="finance-mcp")

            logger.info(
                "✅ finance-mcp Toolkit 初始化完成，已注册 %d 个工具",
                len(toolkit.get_json_schemas()),
            )
            return toolkit

        except Exception as exc:
            last_err = exc
            backoff = min(2 ** (attempt - 1), 8)
            sleep_s = backoff + random.uniform(0, 0.5)
            logger.warning(
                "⚠️  finance-mcp 连接失败 (attempt %d/%d): %s — %.1fs 后重试",
                attempt, max_retries, repr(exc), sleep_s,
            )
            if attempt < max_retries:
                await asyncio.sleep(sleep_s)

    logger.error(
        "❌ finance-mcp 连接失败（已重试 %d 次）: %s → 降级到本地工具",
        max_retries, repr(last_err),
    )
    return None


async def get_analyst_toolkits() -> Dict[str, object]:
    """
    返回按角色过滤的 Toolkit 字典（懒加载，单例）。

    每个分析师只能使用 personas.yaml 中 finance_mcp_tools 列出的工具，
    底层共用同一个 HttpStatelessClient 连接（不重复建立 SSE 连接）。

    Returns:
        {
            "fundamentals": Toolkit,   # 只含基本面工具
            "technical":    Toolkit,   # 只含技术分析工具
            "sentiment":    Toolkit,   # 只含情绪分析工具
            "valuation":    Toolkit,   # 只含估值工具
        }
        连接失败或未启用时返回 {}

    Usage in main.py:
        analyst_toolkits = await get_analyst_toolkits()
        toolkit = analyst_toolkits.get("fundamentals")  # None → 降级本地工具
    """
    global _BASE_CLIENT, _ANALYST_TOOLKITS

    if _ANALYST_TOOLKITS is not None:
        return _ANALYST_TOOLKITS
    if _INIT_FAILED:
        return {}
    if not is_finance_mcp_enabled():
        return {}

    async with _get_analyst_lock():
        # double-checked locking
        if _ANALYST_TOOLKITS is not None:
            return _ANALYST_TOOLKITS

        result = await _init_analyst_toolkits()
        _ANALYST_TOOLKITS = result if result else {}
        return _ANALYST_TOOLKITS


async def _init_analyst_toolkits() -> Optional[Dict[str, object]]:
    """
    实际初始化逻辑：
    1. 建立共享 HttpStatelessClient
    2. 验证连通性（list_tools 一次）
    3. 读 personas.yaml 的白名单
    4. 为每个分析师创建 FilteredMCPClient + Toolkit
    """
    try:
        from agentscope.tool import Toolkit
        from agentscope.mcp import HttpStatelessClient
    except ImportError:
        logger.error("agentscope 未安装，无法初始化 analyst toolkits")
        return None

    global _BASE_CLIENT

    url = os.getenv("FINANCE_MCP_URL", "http://127.0.0.1:8040/sse")
    transport = os.getenv("FINANCE_MCP_TRANSPORT", "sse")
    max_retries = int(os.getenv("FINANCE_MCP_MAX_RETRIES", "3"))
    jitter_max = float(os.getenv("FINANCE_MCP_INIT_JITTER", "2"))

    # 多进程 jitter
    if jitter_max > 0:
        await asyncio.sleep(random.uniform(0, jitter_max))

    headers: Dict[str, str] = {}
    auth_token = os.getenv("FINANCE_MCP_AUTH_TOKEN")
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    # ── 1. 创建共享 client ──
    try:
        base_client = HttpStatelessClient(
            name="finance-mcp-base",
            transport=transport,
            url=url,
            headers=headers,
            timeout=60,
            sse_read_timeout=600,
        )
    except TypeError:
        base_client = HttpStatelessClient(
            name="finance-mcp-base",
            transport=transport,
            url=url,
            headers=headers,
        )

    # ── 2. 验证连通性 ──
    last_err: Optional[Exception] = None
    all_tool_names: List[str] = []
    for attempt in range(1, max_retries + 1):
        try:
            tools_list = await base_client.list_tools()
            if not tools_list:
                raise ValueError("list_tools 返回空列表，服务可能未完全启动")
            all_tool_names = [t.name for t in tools_list]
            logger.info("✅ finance-mcp 连通验证成功，共 %d 个工具", len(all_tool_names))
            break
        except Exception as exc:
            last_err = exc
            backoff = min(2 ** (attempt - 1), 8) + random.uniform(0, 0.5)
            logger.warning("⚠️  验证失败 (attempt %d/%d): %s — %.1fs 后重试",
                           attempt, max_retries, repr(exc), backoff)
            if attempt < max_retries:
                await asyncio.sleep(backoff)
    else:
        logger.error("❌ finance-mcp 连接失败（已重试 %d 次）: %s", max_retries, repr(last_err))
        return None

    _BASE_CLIENT = base_client

    # ── 3. 读白名单 ──
    allowlists = _load_analyst_tool_allowlists()
    if not allowlists:
        logger.warning("⚠️  personas.yaml 白名单为空，按角色过滤 Toolkit 无法创建")
        return None

    # ── 4. 按角色创建 FilteredMCPClient + Toolkit ──
    analyst_toolkits: Dict[str, object] = {}
    for analyst_type, allowed_tools in allowlists.items():
        if not allowed_tools:
            logger.warning("⚠️  %s 的 finance_mcp_tools 列表为空，跳过", analyst_type)
            continue

        # 只保留服务端实际存在的工具（避免注册不存在工具时报错）
        valid_tools = [t for t in allowed_tools if t in all_tool_names]
        unknown = set(allowed_tools) - set(all_tool_names)
        if unknown:
            logger.warning("⚠️  %s: 以下工具不存在于 finance-mcp 服务中，已跳过: %s",
                           analyst_type, unknown)
        if not valid_tools:
            logger.warning("⚠️  %s: 白名单过滤后工具列表为空，跳过", analyst_type)
            continue

        filtered_client = FilteredMCPClient(base_client, valid_tools)
        toolkit = Toolkit()
        toolkit.create_tool_group(
            group_name=f"finance-mcp-{analyst_type}",
            description=f"A股金融工具集 — {analyst_type} 分析师专用",
            active=True,
        )
        await toolkit.register_mcp_client(filtered_client,
                                          group_name=f"finance-mcp-{analyst_type}")

        registered = [s.get("function", {}).get("name")
                      for s in toolkit.get_json_schemas()]
        logger.info("✅ %s toolkit: %d 个工具 %s", analyst_type, len(registered), registered)
        analyst_toolkits[analyst_type] = toolkit

    logger.info("✅ 按角色 Toolkit 初始化完成，共 %d 个分析师", len(analyst_toolkits))
    return analyst_toolkits


async def verify_finance_mcp_connection() -> dict:
    """
    验证 finance-mcp 连接状态，用于健康检查 / 测试脚本。

    Returns:
        {
            "enabled": bool,          # 是否配置了 FINANCE_MCP_URL
            "connected": bool,        # 是否成功连接
            "tool_count": int,        # 工具数量
            "tool_names": List[str],  # 工具名列表
            "url": str,               # 服务地址
            "error": str | None,      # 失败原因
        }
    """
    url = os.getenv("FINANCE_MCP_URL", "")
    result = {
        "enabled": bool(url),
        "connected": False,
        "tool_count": 0,
        "tool_names": [],
        "url": url or "未设置 FINANCE_MCP_URL",
        "error": None,
    }

    if not url:
        result["error"] = "FINANCE_MCP_URL 未设置"
        return result

    try:
        from agentscope.mcp import HttpStatelessClient
        client = HttpStatelessClient(
            name="finance-mcp-verify",
            transport="sse",
            url=url,
        )
        tools = await client.list_tools()
        result["connected"] = True
        result["tool_count"] = len(tools)
        result["tool_names"] = [t.name for t in tools]
    except Exception as exc:
        result["error"] = str(exc)

    return result
