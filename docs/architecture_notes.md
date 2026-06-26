# EvoTraders 架构笔记

## 完整系统链路

```
【数据来源层】
┌──────────────┬──────────────┬──────────────┐
│   backtest   │     mock     │     live     │
│ akshare/CSV  │   随机生成    │ Finnhub API  │
└──────┬───────┴──────┬───────┴──────┬───────┘
       └──────────────▼──────────────┘
                 MarketService
                （按模式选数据源）
                      │
                      ▼
【Agent 执行层】── 所有 Agent 在同一个 MsgHub 里
                   任何人 reply → 自动 observe() 给所有人

  ┌──────────────────────────────────────────────┐
  │  基本面分析师  ReActAgent                     │
  │  工具：crawl_ths_finance / analyze_*          │
  │  → 跑完立刻 state_sync.on_agent_complete()   │
  ├──────────────────────────────────────────────┤
  │  技术分析师    ReActAgent                     │
  │  工具：history_calculate / execute_code       │
  │    execute_code = LLM写pandas代码 → exec()   │
  │  → 跑完立刻 state_sync.on_agent_complete()   │
  ├──────────────────────────────────────────────┤
  │  情绪分析师    ReActAgent                     │
  │  工具：crawl_ths_news / dashscope_search      │
  │  → 跑完立刻 state_sync.on_agent_complete()   │
  ├──────────────────────────────────────────────┤
  │  估值分析师    ReActAgent                     │
  │  工具：dcf_valuation / ev_ebitda              │
  │  → 跑完立刻 state_sync.on_agent_complete()   │
  ├──────────────────────────────────────────────┤
  │  风控         ReActAgent                      │
  │  综合所有分析师观点 → 输出风险建议             │
  │  → 跑完立刻 state_sync.on_agent_complete()   │
  ├──────────────────────────────────────────────┤
  │  PM           ReActAgent                      │
  │  工具：_make_decision（输出侧，只记录不执行）  │
  │  ┌──────────────────────────────────────────┐ │
  │  │ 第一层风控（逐票实时，局部信息）          │ │
  │  │  · 涨跌停 → 自动改 hold                  │ │
  │  │  · T+1 可卖量 → 自动截断                 │ │
  │  │  · 单笔 40% 预算上限                     │ │
  │  │  · 100股手数对齐（A股）                  │ │
  │  └──────────────────────────────────────────┘ │
  │  → 跑完立刻 state_sync.on_agent_complete()   │
  └──────────────────────────────────────────────┘
              ↓ 所有决策完成后
  ┌──────────────────────────────────────────────┐
  │  第二层风控 RiskGuard（全局，看全部决策）      │
  │  · 单票仓位上限 30%                           │
  │  · 单日亏损熔断 5% → 禁止所有新开仓           │
  │  · 行业集中度上限 60%（需看完所有决策才能算）  │
  └──────────────────────────────────────────────┘
              ↓
         TradeExecutor
         （A股：T+1锁仓状态跨日持久化）
              ↓
【收盘后 Reflection 层】
  推理轨迹（完整对话记录文字）+ 盈亏结果
      → embedding 转向量
      → 写入本地向量数据库（score: 盈利=1.0 亏损=0.0）
  第二天开盘：用当天任务做向量检索
      → 取回最相关的历史经验 → 注入 prompt
              ↓
【通信层】
  state_sync.on_agent_complete()
      → Gateway.broadcast()
      → WebSocket 推送（后端主动推，不是前端来拉）
      → 前端 handlers[evt.type]
      → React 状态更新 → UI 实时渲染
```

---

## 关键设计决策

### 为什么用 WebSocket 而不是 REST
AI Agent 异步运行，每一步完成就要立刻推给前端展示。
REST 的请求-响应模型无法做到"持续推送、顺序不确定"的实时流。

### MsgHub 底层机制
```python
# MsgHub 等价于：
x1 = agent1.reply()
agent2.observe(x1)   # 写入 agent2 的短期记忆
agent3.observe(x1)   # 写入 agent3 的短期记忆

# observe() 的实现只有一行：
async def observe(self, msg):
    await self.memory.add(msg)
```
进入 `async with MsgHub(participants=[...])` 时，把所有参与者互相注册为订阅者。
任何 agent reply 后，框架自动调 observe() 把消息写入其他人的记忆。
退出时注销订阅。

### ReActAgent 循环机制
```
for _ in range(max_iters):
    _reasoning()
        → sys_prompt + memory.get_memory() + toolkit.get_json_schemas()
        → 发给 LLM
    LLM 返回 ToolUseBlock → 执行工具 → memory.add(结果) → 继续循环
    LLM 返回纯文字（无 ToolUseBlock）→ break，给出最终答案
```
退出条件不是问 LLM "决定了吗"，而是检测返回消息里有没有 ToolUseBlock。

### Toolkit 工作原理
Python 函数的类型注解 → 参数类型
Python 函数的 docstring → 工具描述和参数说明
↓ 自动生成
LLM function calling 格式的 JSON Schema
↓ 发给 LLM 作为"工具菜单"

### 四种分析师的区别
同一个 ReActAgent 父类，行为不同靠两个配置：
- `sys_prompt`：每种分析师加载不同的提示词，塑造"人格"
- `toolkit`：每种分析师拿到不同的工具集，约束能做什么

### 分析师 vs PM 的工具语义区别
| | 分析师 | PM |
|---|---|---|
| 工具用途 | 输入侧（查数据） | 输出侧（记录决策） |
| 典型工具 | crawl_ths_finance、history_calculate | _make_decision |
| 目的 | 收集信息形成判断 | 结构化输出最终决策 |

### 两层风控的分工原因
第一层（_make_decision 内）：逐票实时，只有局部信息 → 做单票约束
第二层（RiskGuard）：所有决策完成后，有全局信息 → 做跨票约束（行业集中度必须看完所有决策才能算）

### 长期记忆 = RAG
```
【存】收盘后
  完整对话记录（文字）+ 盈亏结果
      → embedding → 向量数据库（附 score）

【取】第二天
  当天任务描述 → embedding → 相似度检索
      → 取回最相关历史经验 → 注入 prompt
```
推理轨迹是完整的对话文字：LLM 的每步思考 + 工具调用 + 工具返回值。

### Settlement 基准对比
```
收盘后同时计算：
  AI 净值
  vs 等权重基准（第一天平均买入，永远不动）
  vs 市值加权基准（按市值比例买入，永远不动）
  vs 动量策略（每月再平衡，做多涨幅前50%）

只有跑赢这些"不需要 AI 就能做"的策略，才说明 AI 创造了价值。
```

---

## 关键文件索引

| 文件 | 作用 |
|------|------|
| `backend/main.py` | 入口，创建所有 Agent 和服务，启动 Gateway |
| `backend/core/pipeline.py` | TradingPipeline，编排 Agent 执行顺序 |
| `backend/services/gateway.py` | WebSocket 服务器，broadcast 给前端 |
| `backend/agents/analyst.py` | AnalystAgent（ReActAgent 子类） |
| `backend/agents/portfolio_manager.py` | PMAgent，含 _make_decision 工具 |
| `backend/core/risk_guard.py` | 第二层硬约束规则引擎 |
| `backend/utils/settlement.py` | 收盘结算，计算基准对比 |
| `backend/utils/baselines.py` | 三个基准策略计算 |
| `backend/data/historical_price_manager.py` | 回测历史数据（akshare/CSV） |
| `backend/agents/prompts/analyst/personas.yaml` | 各分析师工具配置和人格描述 |
| `frontend/src/services/websocket.js` | 前端 WebSocket 客户端 |
| `frontend/src/App.jsx` | 前端消息路由（handlers[evt.type]） |
