# AgentScope + Qwen 工具调用全部失败？记一次多智能体框架的三连调试

> **系列说明**：本文是《AgentScope 魔改实录》系列的第一篇，记录我在 EvoTraders 这个多智能体交易框架上踩坑、调试、魔改的过程。框架是现成的开源仓库，模型用 qwen-max，边读源码边改。
>
> 这篇讲的是最开始就遇到的三个连锁 bug——每一个单独看都很隐蔽，组合起来让问题极难定位。记录下来，希望同样在用 AgentScope 接国产模型的人少走弯路。

---

## 背景

EvoTraders 是一个基于 AgentScope 的多智能体金融交易框架，核心流程是：

```
分析师 Agent → 风控 Agent → PM Agent（Portfolio Manager）→ 执行器
```

PM Agent 的职责是根据分析师信号做交易决策，通过调用 `_make_decision` 工具来记录每个 ticker 的操作（long / short / hold）。框架基于 `ReActAgent`，即推理-行动循环。

环境：
- 模型：`qwen-max`（阿里云 DashScope）
- 框架：`agentscope` 最新版
- Python：3.12，miniforge 环境

---

## 第一个坑：工具调用全部失败，表现像 Qwen 不会用工具

### 现象

运行 `test_pm.py`，PM Agent 的日志显示它在文字里正确写出了决策，但 `_make_decision` 从未被真正调用：

```
❌ _make_decision 未被调用，decisions 为空
   PM 原始输出: It seems there is a persistent technical issue with the function calls...
```

控制台同时出现大量 WARNING：

```
WARNING | _common:_json_loads_with_repair:63 - Failed to load JSON dict from string:
{"ticker": "TSLA", "action": "long", "quantity": 60, "confidence": 75, "reasoning": "EV leadership"}.
Returning empty dict instead.
```

**奇怪的是**：这条 JSON 字符串是完全合法的，肉眼看毫无问题。

### 定位过程

**第一反应**：Qwen 的工具调用能力有问题，换 prompt 试试。

修了几版 system prompt，加了调用示例、强制说明参数格式，都没用。Qwen 在文字输出里能正确描述决策，就是不走工具调用路径。

**第二步**：往下看日志，注意到这条警告，直觉感觉不对——为什么一个合法 JSON 会解析失败？

找到 AgentScope 源码：

```python
# agentscope/_utils/_common.py
def _json_loads_with_repair(json_str: str) -> dict:
    try:
        repaired = repair_json(json_str, stream_stable=True)  # ← 这里
        result = json.loads(repaired)
        if isinstance(result, dict):
            return result
    except Exception:
        ...
        logger.warning("Failed to load JSON dict from string: ...")
    return {}
```

**第三步**：写了一个诊断脚本，直接调这个函数：

```python
from json_repair import repair_json
repair_json('{"ticker": "TSLA", "action": "long"}', stream_stable=True)
```

输出：

```
❌ Exception: repair_json() got an unexpected keyword argument 'stream_stable'
```

**根因找到了**。

`stream_stable` 是 `json_repair` 较旧版本才有的参数，新版本已经移除。`except Exception` 把这个 `TypeError` 静默吞掉，返回了空 dict `{}`。结果是：

- Qwen **确实生成了完整的工具调用 arguments**
- AgentScope 的解析器**因版本不兼容抛出异常**，把 arguments 全部丢弃
- 工具以空参数被调用，触发 Python 的 `missing required positional arguments` 错误
- Qwen 收到工具调用失败的反馈，认为是"持续的技术问题"，放弃工具调用，改成文字输出

整条调用链断在了框架内部，但所有错误都被静默处理，表现出来的症状是"Qwen 不会调工具"。

### 修复

找到安装的 AgentScope 路径：

```bash
python3 -c "import agentscope._utils._common as m; import inspect; print(inspect.getfile(m))"
```

用 `sed` 直接移除那个不兼容的参数：

```bash
sed -i '' 's/repair_json(json_str, stream_stable=True)/repair_json(json_str)/g' \
  /path/to/agentscope/_utils/_common.py
```

一行改动，工具调用立刻恢复正常。

---

## 第二个坑：ReAct 多轮迭代后工具参数丢失

### 现象

修了解析器之后，工具调用确实开始工作，但不稳定。有时候所有 4 个 ticker 都能成功记录，有时候中途开始出现空 input。日志如下：

```
system: {"type": "tool_result", "name": "_make_decision",
  "output": [{"type": "text", "text": "Error: PMAgent._make_decision()
  missing 3 required positional arguments: 'ticker', 'action', and 'quantity'"}]}

system: {"type": "tool_result", "name": "_make_decision",
  "output": [{"type": "text", "text": "Error: PMAgent._make_decision()
  missing 3 required positional arguments: 'ticker', 'action', and 'quantity'"}]}
```

对应的 Qwen 请求里，tool_use block 是这样的：

```json
{ "type": "tool_use", "name": "_make_decision", "input": {}, "id": "call_xxx" }
{ "type": "tool_use", "name": "_make_decision", "input": {}, "id": "call_yyy" }
{ "type": "tool_use", "name": "_make_decision", "input": {}, "id": "call_zzz" }
```

**具体规律**：第 1 轮调用往往正常，第 2、3、4 轮开始批量出现 `input: {}`，Qwen 收到连续失败后放弃工具调用，改成输出文字。

### 定位过程

查看 AgentScope 的 `_dashscope_model.py`，非流式模式下工具调用的处理路径：

```python
if message.get("tool_calls"):
    for tool_call in message["tool_calls"]:
        input_ = _json_loads_with_repair(
            tool_call["function"].get("arguments", "{}") or "{}",
        )
```

`_json_loads_with_repair` 已经修好了，所以这里不是问题。那 `input: {}` 是从哪来的？

查看流式模式的处理：

```python
if self.stream_tool_parsing:
    repaired_input = _parse_streaming_json_dict(...)
else:
    repaired_input = {}  # ← 流式中间 chunk 用空 dict 占位
```

再看 `get_agent_model()` 的默认配置：`stream: bool = False`。

所以走的是非流式路径，`input: {}` 不是这里产生的。

继续看，问题其实是 **Qwen 在多轮 ReAct 迭代后注意力衰减**。ReAct 的工作机制是：

```
第1轮：[system] + [user: 任务] → Qwen 推理 → 调用工具
第2轮：[system] + [user: 任务] + [tool_result_1] → 继续推理 → 调用工具
第3轮：[system] + [user: 任务] + [tool_result_1] + [tool_result_2] → ...
```

随着轮数增加，工具定义（schema）被埋在越来越深的上下文里，Qwen 对参数格式的注意力下降，开始生成不带 arguments 的工具调用。

### 修复

让每次 `_make_decision` 的 **ToolResponse 本身成为下一步的提词器**，把工具签名从遥远的 system prompt 移到最近的返回值里：

```python
def _make_decision(self, ticker, action, quantity, ...) -> ToolResponse:
    # ... 记录决策 ...

    remaining = [t for t in self._pending_tickers if t not in self._decisions]
    if remaining:
        next_ticker = remaining[0]
        next_hint = (
            f"\n\nDecided {decided_count}/{total_count}. "
            f"Remaining cash: ${remaining_cash:,.0f}.\n"
            f'Now call: _make_decision(ticker="{next_ticker}", '
            f'action="long"|"short"|"hold", quantity=<int>, ...)'
        )

    return ToolResponse(content=[TextBlock(
        text=f"✓ Recorded: {ticker} → {action} {actual_qty} shares\n{next_hint}"
    )])
```

每次调用成功后，返回值里明确告诉 Qwen 下一步要调什么，不依赖模型自己从上下文里回溯。

---

## 第三个坑：顺序分仓超预算

### 现象

工具调用稳定后，发现最终决策总金额经常超过初始资金：

```
✅ _make_decision 工具调用成功！
  AAPL: LONG 216股 ~$39,960
  SPY:  LONG 108股 ~$50,220
  TSLA: LONG 100股 ~$25,000
  总资金使用: $115,180 / $100,000   ← 超了 $15k
```

### 分析

EvoTraders 设计的是**顺序分仓**：PM 逐个 ticker 决策，每次基于剩余现金计算仓位。这是相对于"一次性提交所有决策"的迭代设计——业务价值在于每个决策都能看到上一个决策用掉了多少钱。

问题在于：原来的 `_make_decision` ToolResponse 里没有反馈实际剩余现金：

```python
decided_cost += 0  # placeholder — pipeline 负责实际扣款
```

Qwen 只能凭自己估算剩余资金，估算不准导致超预算。

### 修复

两步：

**第一步**：在 `reply()` 接收 prices 参数，注入到 `_current_prices`：

```python
async def reply(self, x=None, tickers=None, prices=None):
    self._current_prices = prices or {}
    ...
```

**第二步**：在 `_make_decision` 里用真实价格计算已用资金，并在 ToolResponse 里反馈：

```python
committed = sum(
    d["quantity"] * self._current_prices.get(t, 0)
    for t, d in self._decisions.items()
)
remaining_cash = initial_cash - committed
```

同时加**硬截断**：如果 Qwen 提交的数量超出剩余现金，直接截到可买的最大量：

```python
price_now = self._current_prices.get(ticker, 0)
if price_now > 0:
    max_affordable = int(pre_remaining / price_now)
    if actual_qty > max_affordable:
        actual_qty = max_affordable
```

这样即使 Qwen 的数学不够精确，`_make_decision` 也会强制保证预算不超。

### 结果

```
✅ _make_decision 工具调用成功！
  AAPL: LONG 216股 (信心85%) ~$39,960
  BABA: HOLD  0股  (信心70%) ~$0
  SPY:  LONG 108股 (信心80%) ~$50,220
  TSLA: LONG  39股 (信心75%) ~$9,750
  总资金使用: $99,930 / $100,000   ✓
```

---

## 总结

| 坑 | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | `_make_decision` 从未被调用 | `json_repair` 版本不兼容，`stream_stable` 参数已移除，异常被静默吞掉 | 移除 `stream_stable=True` 参数 |
| 2 | 多轮 ReAct 后工具参数丢失 | Qwen 注意力衰减，工具 schema 被埋在深层上下文 | ToolResponse 里嵌入下一步提词器 |
| 3 | 顺序分仓超预算 | ToolResponse 没反馈剩余现金，Qwen 自己估算不准 | 注入价格、计算实际剩余，并加硬截断 |

**最值得记的教训**：`except Exception: return {}` 这种写法在框架里很常见，但它会把版本兼容性问题彻底隐藏，让调试方向完全偏离——明明是解析器坏了，看起来像是模型能力不足。

---

*本文所有改动均在 EvoTraders 仓库的 `evotraders/` 目录下，修改文件：`backend/agents/portfolio_manager.py`，以及 AgentScope 源文件 `agentscope/_utils/_common.py`。*
