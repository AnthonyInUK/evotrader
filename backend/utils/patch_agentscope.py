# -*- coding: utf-8 -*-
"""
修复 AgentScope 与本地 json_repair 版本不兼容的问题。
_json_loads_with_repair 使用 stream_stable=True 在某些版本下会抛 Exception，
导致所有 tool call arguments 解析失败，返回空 dict {}。

在入口文件（test_pm.py、run_backtest.py）import 任何 agentscope 模块之前调用：
    from backend.utils.patch_agentscope import apply_patches
    apply_patches()
"""

import json
import logging

log = logging.getLogger(__name__)


def _robust_json_loads(json_str: str) -> dict:
    """
    替代 AgentScope 的 _json_loads_with_repair。
    去掉 stream_stable=True 参数，兼容各版本 json_repair。
    """
    if not json_str:
        return {}

    # 1. 直接尝试标准解析（完整合法 JSON 应走这条路）
    try:
        result = json.loads(json_str)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. 用 json_repair 修复后再解析（不带 stream_stable）
    try:
        from json_repair import repair_json
        repaired = repair_json(json_str)
        result = json.loads(repaired)
        if isinstance(result, dict):
            return result
    except Exception:
        pass

    log.warning(
        "robust_json_loads: failed to parse as dict: %s",
        json_str[:120] + ("..." if len(json_str) > 120 else ""),
    )
    return {}


def apply_patches() -> None:
    """在进程启动时调用一次，替换掉有问题的解析函数。"""
    try:
        import agentscope._utils._common as _cm
        import agentscope.model._dashscope_model as dm

        _cm._json_loads_with_repair = _robust_json_loads
        dm._json_loads_with_repair = _robust_json_loads
        log.debug("apply_patches: _json_loads_with_repair patched OK")
    except ImportError as e:
        log.warning("apply_patches: could not patch agentscope: %s", e)
