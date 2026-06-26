# T+1 和涨跌停为什么必须放在执行层：一个反直觉的架构决定

> 本文是「把开源多 Agent 回测框架迁到 A 股」系列的技术章节之一。  
> 源码仓库：EvoTraders（基于 AgentScope 多 Agent 框架）

---

## 一、问题从哪里来

把 EvoTraders 的 PM Agent（Portfolio Manager）接到 A 股时，最先碰到的问题是这个：

PM Agent 的 `_make_decision` 工具负责记录决策——buy 多少、sell 多少。它原本是给美股设计的：  
今天买的股票，明天就能卖；下单能不能成交，完全看流动性，没有硬性价格上下限。

A 股不一样：

- **T+1**：今天买的股票，**今天不能卖**。
- **涨跌停**：当日涨幅 ≥10% 时封板，买单大概率无法成交；跌幅 ≥10% 时封板，卖单无法成交（流动性陷阱）。
- **手数限制**：A 股每笔买入必须是 100 股的整数倍。

一开始我的直觉是：**这是 PM Agent 的问题，应该在 prompt 里教它规则，让它自己不要犯错。**

这个直觉是错的。本文解释为什么，以及实验数据如何证实这一点。

---

## 二、为什么不能只靠 Prompt

先说结论：**LLM 的 prompt 教不会执行层的物理约束。**

原因有三层。

**第一层：LLM 会产生幻觉。**  
A 股的涨跌停价不是固定的，它依赖昨日收盘价实时计算。Prompt 里写"不要买涨停股"，LLM 并不知道今天的涨停价是多少。它没有计算能力，只有模式匹配。

**第二层：上下文窗口有损耗。**  
当一个 cycle 里需要对 10 只股票逐一决策时，PM Agent 的 ReAct 循环会调用 `_make_decision` 工具 10 次。前几次工具调用的信息（包括涨跌停提示）会随着 context 增长逐渐稀释，LLM 对第 8 只股票的决策质量不如第 1 只。

**第三层：就算 LLM 记住了规则，它也可能在推理时"认为"自己在规则边界内，但实际上已经越界了。**  
这是语言模型的固有局限：它无法精确计算 `round(103.0 * 1.10, 2) == 113.3`，更无法在 10 次工具调用之间维护一个精确的 T+1 锁仓状态。

所以 **prompt 只能作为第一道防线（让 LLM 有意识），不能作为唯一防线。** 执行层的硬性拦截是必须的。

---

## 三、三层防护架构

最终采用的方案是三层防护，职责明确、互不替代：

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 1：LLM 感知层（Prompt Context）                        │
│  pipeline 在调用 PM Agent 之前，把每只股票的涨跌停状态         │
│  注入 prompt context，让 LLM 在推理时"知道"这些限制           │
└─────────────────────────────────────────────────────────────┘
                          │ 如果 LLM 忽视了 prompt
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 2：工具级拦截（_make_decision 内部校验）               │
│  每次工具调用都做硬性检查：                                    │
│   - 涨停 + long  → 直接返回错误，不记录决策                   │
│   - 跌停 + short → 直接返回错误，不记录决策                   │
│   - T+1 锁仓     → 截断卖出量或拒绝                          │
└─────────────────────────────────────────────────────────────┘
                          │ 决策通过后进入执行
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 3：执行层约束（ASharePortfolioTradeExecutor）          │
│  最终执行前再做一次校验，也处理盘中价格变动导致的状态漂移       │
│  印花税、手续费也在这里计算                                    │
└─────────────────────────────────────────────────────────────┘
```

---

## 四、核心代码

### 4.1 `build_circuit_status()`：实时计算涨跌停状态

```python
# backend/utils/a_share_constraints.py

def build_circuit_status(
    tickers: List[str],
    prices: Dict[str, float],
    prev_closes: Dict[str, float],
) -> Dict[str, Dict]:
    """
    根据当前价格和昨收价，计算每只股票的涨跌停状态。
    返回供 PM Agent 直接使用的 context dict。
    """
    result = {}
    for ticker in tickers:
        price = prices.get(ticker, 0.0)
        prev  = prev_closes.get(ticker, 0.0)

        if not price or not prev:
            result[ticker] = {
                "status": "unknown", "can_buy": True, "can_sell": True,
                "warning": "⚠️ 缺少昨收价，无法计算涨跌停价格"
            }
            continue

        limit_up   = calc_limit_up_price(ticker, prev)    # 科创板/创业板 ±20%，其余 ±10%
        limit_down = calc_limit_down_price(ticker, prev)

        if price >= limit_up - 0.01:
            status, can_buy, can_sell = "limit_up", False, True
            warning = "⚠️ 涨停：买单可能无法成交，不建议追涨"
        elif price <= limit_down + 0.01:
            status, can_buy, can_sell = "limit_down", True, False
            warning = "⚠️ 跌停：卖单可能无法成交，当日退出受阻"
        else:
            status, can_buy, can_sell, warning = "normal", True, True, ""

        result[ticker] = {
            "status": status, "current_price": round(price, 2),
            "limit_up_price": limit_up, "limit_down_price": limit_down,
            "can_buy": can_buy, "can_sell": can_sell, "warning": warning,
        }
    return result
```

### 4.2 `_make_decision()`：工具级三道卡口

```python
# backend/agents/portfolio_manager.py

def _make_decision(self, ticker, action, quantity, confidence=50, reasoning=""):
    """PM Agent 的唯一决策工具，内置 A 股约束校验。"""

    # ── 卡口 1：涨跌停拦截 ───────────────────────────────────────
    cb = self._circuit_breakers.get(ticker, {})
    if cb:
        if action == "long" and not cb.get("can_buy", True):
            return ToolResponse(content=[TextBlock(type="text", text=(
                f"⚠️ {ticker} 当前涨停（价格={cb['current_price']}，"
                f"涨停价={cb['limit_up_price']}），"
                f"买单极大概率无法成交。已自动改为 hold。"
            ))])
        if action == "short" and not cb.get("can_sell", True):
            return ToolResponse(content=[TextBlock(type="text", text=(
                f"⚠️ {ticker} 当前跌停（价格={cb['current_price']}，"
                f"跌停价={cb['limit_down_price']}），"
                f"卖单极大概率无法成交（流动性陷阱）。已自动改为 hold。"
            ))])

    # ── 卡口 2：T+1 可卖量截断 ───────────────────────────────────
    actual_qty = quantity if action != "hold" else 0
    if action == "short" and actual_qty > 0:
        unlocked = self._unlocked_quantities.get(ticker)
        if unlocked is not None:
            if actual_qty > unlocked:
                actual_qty = unlocked
                if actual_qty == 0:
                    return ToolResponse(content=[TextBlock(type="text", text=(
                        f"⚠️ {ticker} 所有持仓均为今日买入（T+1锁仓），"
                        f"当日无法卖出任何股份。已自动改为 hold。"
                    ))])

    # ── 卡口 3：预算强制截断 ─────────────────────────────────────
    # （略，见完整源码）

    self._decisions[ticker] = {
        "action": action, "quantity": actual_qty,
        "confidence": confidence, "reasoning": reasoning,
    }
    return ToolResponse(...)
```

### 4.3 T+1 锁仓追踪：按日期记录新买仓位

```python
# backend/utils/a_share_constraints.py（ASharePortfolioTradeExecutor）

def _record_buy(self, ticker: str, quantity: int, date: str):
    """每次买入时记录锁仓股数（T+1，当日不可卖）"""
    if date not in self._locked_shares:
        self._locked_shares[date] = {}
    self._locked_shares[date][ticker] = (
        self._locked_shares[date].get(ticker, 0) + quantity
    )

def get_available_shares(self, ticker: str, current_date: str) -> int:
    """返回今日可卖数量 = 总持仓 - 今日买入"""
    total = self.portfolio["positions"].get(ticker, {}).get("quantity", 0)
    locked_today = self._locked_shares.get(current_date, {}).get(ticker, 0)
    return max(0, total - locked_today)
```

---

## 五、实验验证：约束不是"额外限制"，而是"避免虚假收益"

为了在发布前确认这套约束真的在工作，我构造了一个专门触发三类场景的实验。

### 实验设计

使用一支虚拟股票 `000001.SZ`，初始资金 ¥1,000,000，设计 9 条 PM 指令，覆盖三个主动植入的场景：

| 场景 | 日期 | 触发条件 | PM 指令 |
|------|------|----------|---------|
| 涨停追买 | 2024-03-04 | 价格 ¥113.3 = 103×1.10（涨停价） | long 100股 @¥113.3 |
| T+1 当日卖 | 2024-03-07 | 当天从零买入 100 股后立刻卖出 | short 100股 @¥110.0 |
| 跌停止损 | 2024-03-12 | 价格 ¥102.6 = 114×0.90（跌停价） | short 200股 @¥102.6 |

对比两个执行器：
- **无约束执行器**（`NaiveExecutor`）：只检查资金/持仓是否够，不管涨跌停和 T+1。
- **A 股约束执行器**（`ASharePortfolioTradeExecutor`）：完整约束。

### 实验结果

```
================================================================================
  实验 B：A股约束触发记录  —  无约束执行器 vs A股约束执行器
  股票代码: 000001.SZ    初始资金: ¥1,000,000
================================================================================

  日期          方向  数量  价格     PM意图                    无约束执行器   A股约束执行器
  ─────────────────────────────────────────────────────────────────────────────
  2024-03-01  long   200  ¥102.0  [场景1] 正常建仓 200 股      ✅ 成交      ✅ 成交
  2024-03-04  long   100  ¥113.3  [场景1] 涨停追买 100 股      ✅ 成交      🚫 涨停板，买入无法成交
  >>> 差异：A股约束拦截，无约束放行

  2024-03-05  short  200  ¥115.0  [场景1] 涨停次日正常卖出     ✅ 成交      ✅ 成交
  2024-03-07  long   100  ¥110.0  [场景2] 从零建仓 100 股      ✅ 成交      ✅ 成交
  2024-03-07  short  100  ¥110.0  [场景2] 当天立刻卖出         ✅ 成交      🚫 T+1 锁仓，无可卖股份
  >>> 差异：A股约束拦截，无约束放行

  2024-03-08  short  100  ¥110.0  [场景2] T+1 次日正常卖出     ✅ 成交      ✅ 成交
  2024-03-11  long   200  ¥113.0  [场景3] 建仓 200 股          ✅ 成交      ✅ 成交
  2024-03-12  short  200  ¥102.6  [场景3] 跌停止损             ✅ 成交      🚫 跌停板，卖出无法成交
  >>> 差异：A股约束拦截，无约束放行

  2024-03-13  short  200  ¥104.0  [场景3] 跌停次日正常卖出     ❌ 持仓不足  ✅ 成交
  ─────────────────────────────────────────────────────────────────────────────
```

**最终 P&L 对比：**

|  | 无约束执行器 | A 股约束执行器 |
|--|------------|--------------|
| 最终资产总值 | ¥1,000,124 | ¥1,000,709 |
| P&L | +¥124（+0.01%） | +¥709（+0.07%） |

三条关键差异指令全部被 A 股约束执行器拦截，无约束执行器全部放行。

**约束触发统计：**
- 总指令数：9 笔
- A 股执行器拦截：3 笔
- 关键差异（A 股拦截 & 无约束放行）：3 笔（精确匹配预设场景）

### 一个反直觉的结论

注意最后一行：`2024-03-13` 的正常卖出，**无约束执行器反而失败了**（持仓不足 0 股），而 A 股约束执行器成功了。

原因：
- 无约束执行器在 2024-03-12 **"成功"卖出了跌停板上的 200 股**，于是到 2024-03-13 持仓已归零。
- A 股约束执行器在 2024-03-12 拦截了跌停卖出，持仓保留到次日，2024-03-13 以 ¥104.0 正常成交，比跌停价 ¥102.6 **多回收 ¥280**（200 股 × ¥1.4）。

这说明：**执行约束不只是"合规"需求，它同时纠正了回测的虚假交易，让回测结果更接近真实市场的可达收益。**

无约束执行器的 +¥124 包含了一笔现实中不可能发生的跌停板卖出；A 股约束执行器的 +¥709 是完全可以在真实市场复现的路径。

---

## 六、这个架构决定对项目的影响

**好处：**
1. PM Agent 的决策逻辑不需要关心 A 股规则的细节，它只管输出"想做什么"。
2. 约束规则集中在执行层，便于单独测试（见 `test_pm_circuit_breaker_context.py`，13 个测试覆盖全部场景）。
3. 切换市场（比如 A 股 → 港股 → 美股）只需替换执行器，PM Agent 和分析师 Agent 不需要改动。

**代价：**
1. 执行层拦截后，PM Agent 的 ReAct 循环不会自动重试——它只会看到一条错误消息，然后继续下一只股票。如果需要"重新决策"，还需要额外的反馈机制。
2. 三层防护之间存在轻微冗余：工具层和执行层都检查了涨跌停。目前保留冗余是有意为之（防御性编程），但在生产中可以视情况简化。

---

## 七、踩坑记录

实现这套三层防护的过程中，踩了四个真实的坑，记录下来省得后来者重走。

---

### 坑 1：T+1 实验第一版跑不出来

**现象**：把 T+1 触发场景设计成"第 5 天买入 100 股，同日立刻卖出"，但 A 股约束执行器没有拦截，卖出成功了。

**原因**：第 5 天之前有一笔 200 股的建仓（场景 1 遗留），`get_available_shares()` 返回的是 `总持仓 - 今日买入 = 200 - 100 = 100`，大于 0，所以直接放行了。

**根因**：实验各场景之间没有做状态隔离，场景 1 的持仓"泄漏"到了场景 2。

**修复**：把三个场景重新设计成完全独立的时间段，场景 2 在 T+1 测试之前专门做一次清仓（场景 1 的 Day 3 全卖），确保场景 2 进入时持仓归零。

**教训**：设计约束触发实验时，每个场景的前置状态要明确，不要依赖上一个场景的自然收尾。

---

### 坑 2：浮点精度导致涨停判断失效

**现象**：`103.0 * 1.10` 在 Python 里等于 `113.30000000000001`，而价格数据存的是 `113.3`。如果用 `price >= limit_up` 做精确比较，条件永远不成立——明明涨停了，但状态返回 `normal`。

**代码复现**：
```python
>>> 103.0 * 1.10
113.30000000000001
>>> 113.3 >= 113.30000000000001
False  # 💀 明明是涨停，判断成"正常"
```

**修复**：比较时留 0.01 的容差：
```python
if price >= limit_up - 0.01:   # 而不是 price >= limit_up
    status = "limit_up"
```

**教训**：所有涉及股价精确比较的地方，一律加容差。`round()` 不是万能的，因为 `round(103.0 * 1.10, 2)` 得到的是 `113.3`，但原始乘法结果已经是带精度误差的浮点数了。

---

### 坑 3：`prev_closes` 在 pipeline 里传了三层才到目的地

**现象**：`build_circuit_status()` 需要 `prev_closes`，但调用链是：

```
run_cycle()
  └─ _run_pm_with_sync()
       └─ build_circuit_status()
            └─ pm.reply(circuit_breakers=...)
```

写好 `build_circuit_status()` 之后跑测试，结果每只股票都返回 `status: unknown`，因为 `prev_closes` 一直是空的。

**原因**：`run_cycle()` 里有 `prev_closes` 变量，但调用 `_run_pm_with_sync()` 时没有传进去，只传了 `prices`。`_run_pm_with_sync()` 的签名里也没有 `prev_closes` 这个参数。

**修复**：在 `_run_pm_with_sync()` 的签名里加 `prev_closes=None`，调用链上每层都补上这个参数透传。

**教训**：加新的 context 数据时，要从入口（`run_cycle`）往下逐层检查每个函数的签名，不要假设"中间层应该会传的"。这类 bug 不报错，只是静默地用默认值运行。

---

### 坑 4：AgentScope 的 `TextBlock` 是 dict，不是对象

**现象**：写完工具级拦截的单元测试，跑测试时报 `AttributeError: 'dict' object has no attribute 'text'`。

**代码**：
```python
text = result.content[0].text   # 💀 AttributeError
```

**原因**：AgentScope 在某些场景下对 `TextBlock` 做了序列化，`result.content[0]` 实际上是一个 `{"type": "text", "text": "..."}` 的 dict，而不是 `TextBlock` 对象。

**修复**：写一个兼容两种形态的辅助函数：
```python
def _get_text(result) -> str:
    item = result.content[0]
    if isinstance(item, dict):
        return item.get("text", "")
    return getattr(item, "text", str(item))
```

**教训**：在测试 AgentScope 工具返回值时，不要假设 `content` 里的对象类型，先 `print(type(result.content[0]))` 确认一下再写断言。

---

## 八、一句话总结（可用于面试或 DEVLOG）

> A 股的 T+1 和涨跌停不是"业务规则"，而是物理约束——LLM 没有维护精确状态的能力，所以这类约束只能在执行层用代码强制兜底，而不能只靠 prompt 教 LLM"自律"。

---

*下一篇：Risk Manager Agent 的 A 股风险评分体系（跌停锁仓 = CRITICAL，ST 股 = 特殊警告）*
