#!/usr/bin/env python3
"""
诊断 AgentScope _json_loads_with_repair 对 Qwen arguments 的解析行为。
不需要联网，纯本地运行。
"""
import json
from json_repair import repair_json
import agentscope._utils._common as _cm
import agentscope.model._dashscope_model as dm

# ── Patch: 打印每次调用的原始 arguments ──────────────────────────────────────
original_fn = _cm._json_loads_with_repair

def patched(json_str):
    print(f"\n=== _json_loads_with_repair called ===")
    print(f"  len={len(json_str)}")
    print(f"  repr: {repr(json_str[:300])}")
    try:
        repaired = repair_json(json_str, stream_stable=True)
        result   = json.loads(repaired)
        print(f"  parsed type: {type(result)}")
        if isinstance(result, dict):
            print(f"  ✅ OK: {result}")
            return result
        else:
            print(f"  ❌ Not a dict! value={result}")
    except Exception as e:
        print(f"  ❌ Exception: {e}")
    return {}

_cm._json_loads_with_repair = patched
dm._json_loads_with_repair  = patched

# ── 模拟 Qwen 可能返回的各种 arguments 格式 ──────────────────────────────────
test_cases = [
    # 标准格式 - 应该通过
    '{"ticker": "AAPL", "action": "long", "quantity": 217, "confidence": 85, "reasoning": "Strong fundamentals"}',
    # 带换行的格式
    '{\n  "ticker": "AAPL",\n  "action": "long",\n  "quantity": 217\n}',
    # 空字符串 / None → fallback to "{}"
    "{}",
    # Qwen 有时会在 JSON 后面加 reasoning 文字
    '{"ticker": "AAPL", "action": "long", "quantity": 217}\nHere is my reasoning...',
    # 双重 JSON 编码：arguments 本身被再次 JSON encode 成字符串
    '"{\\"ticker\\": \\"AAPL\\", \\"action\\": \\"long\\", \\"quantity\\": 217}"',
    # Markdown 包裹
    '```json\n{"ticker": "AAPL", "action": "long", "quantity": 217}\n```',
]

print("=" * 60)
print("测试 repair_json + json.loads 对各种格式的处理")
print("=" * 60)

for case in test_cases:
    patched(case)

# ── 还原 Patch ────────────────────────────────────────────────────────────────
_cm._json_loads_with_repair = original_fn
dm._json_loads_with_repair  = original_fn
print("\n\n诊断完成。请把上面的输出贴给我。")
