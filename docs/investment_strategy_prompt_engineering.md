# Investment Strategy And Prompt Engineering Playbook

这份文档专门记录 EvoTraders 当前的投资策略设计、PM/分析师提示词工程原则，以及什么问题应该改哪一层。

目标不是写一套“永远正确”的交易口诀，而是避免后续实验越跑越乱：

```text
策略问题、数据问题、工具问题、提示词问题、上下文问题必须分开处理。
```

当前项目还没有进入最终策略定版阶段。现在处于：

```text
8 股票池 A 股多智能体回测
+ PM 市场环境仓位规则 v4
+ PM compact context 实验准备阶段
```

## 当前系统分层

从输入到交易，当前系统大概是这几层：

```text
数据层
=> 工具层
=> 分析师层
=> 风险经理层
=> PM 上下文层
=> PM 决策层
=> RiskGuard 硬约束层
=> A 股执行/结算层
=> 回测归因层
```

每一层应该解决的问题不同。

| 层级 | 负责什么 | 不应该负责什么 |
|---|---|---|
| 数据层 | 价格、财务、新闻、公司信息、缓存 | 投资观点 |
| 工具层 | 指标计算、数据清洗、格式统一 | 策略倾向 |
| 分析师层 | 给出单维度信号和证据 | 最终仓位 |
| 风险经理 | 组合风险提示 | 直接替 PM 下单 |
| PM 上下文层 | 压缩信息、保留关键事实 | 自由发挥投资观点 |
| PM 决策层 | 组合仓位、个股分配、买卖动作 | 修数据 bug |
| RiskGuard | 单票/行业/T+1/现金等硬约束 | 判断投资机会 |
| 执行层 | 实际成交、结算、持仓更新 | 修改 PM 意图 |
| 归因层 | 解释收益来源 | 事后改规则 |

## 当前投资策略雏形

当前策略不是纯量化，也不是纯主观投研，而是：

```text
多分析师信号
+ PM 组合构建
+ A 股硬约束
+ 回测归因反馈
```

核心思想：

```text
先判断市场环境，再决定组合目标仓位，再在股票池里分配仓位。
```

当前股票池：

```text
600519.SH  贵州茅台，高价消费核心
601398.SH  工商银行，低价高股息银行
000858.SZ  五粮液，消费/白酒
000333.SZ  美的集团，家电制造
300750.SZ  宁德时代，新能源成长
600030.SH  中信证券，券商/市场情绪代理
601318.SH  中国平安，保险/深度价值争议
600276.SH  恒瑞医药，医药/创新药转型
```

当前 PM v4 市场环境仓位框架：

| 市场环境 | 目标股票仓位 | 直觉 |
|---|---:|---|
| WEAK | 40%-60% | 保护本金，现金有效 |
| SIDEWAYS_OR_SMALL_UP | 65%-75% | 有选择地参与 |
| REBOUND | 80%-90% | 市场修复时提高风险预算 |

这不是最终结论，而是当前实验假设。

## 当前买卖逻辑

### 投票分析师

PM 主要统计三个投票分析师：

```text
technical_analyst
fundamentals_analyst
sentiment_analyst
```

非投票角色：

```text
valuation_analyst: 估值/仓位参考，不直接投票
risk_manager: 风险参考，不直接投票
RiskGuard: 下单后硬裁剪
```

### 单票信号解释

当前默认规则：

| 信号形态 | PM 应该如何理解 |
|---|---|
| 2-3 个投票分析师 BULL | 可买或加仓 |
| 1 个 BULL，其他 NEUTRAL | weak bull，小仓/谨慎 |
| 多数 NEUTRAL | HOLD 或等待 |
| 多数 BEAR | 不买，已有仓位考虑减仓 |
| Technical BULL 但 Fundamentals BEAR | 只能做战术仓，不能大仓 |
| Fundamentals BULL 但 Technical BEAR | 可以关注，但不能重仓追 |
| Sentiment BULL 但基本面/技术弱 | 题材交易，仓位要小 |

### 估值分析师的作用

估值不是投票成员。

```text
valuation bullish:
  允许正常仓位，说明安全边际更好

valuation neutral:
  不加分，不拦截

valuation bearish:
  降低仓位上限，不能作为唯一买入理由
```

原因：

```text
A 股里估值便宜可能是价值机会，也可能是价值陷阱。
估值贵也不一定马上跌，尤其是高质量成长/品牌资产。
```

所以估值是仓位调节器，不是方向裁判。

### 风险经理的作用

风险经理应该告诉 PM：

```text
现金是否太低
单票是否太集中
行业是否太集中
涨跌停是否影响成交
T+1 是否影响卖出
当前组合是否已经接近风险边界
```

风险经理不应该直接决定：

```text
买哪只
卖哪只
目标收益多少
市场是不是一定反弹
```

### RiskGuard 硬约束

当前硬约束：

```text
单票上限：30%
行业上限：60%
A 股一手：100 股
T+1：今日买入不可今日卖
涨停：买入可能无法成交
跌停：卖出可能无法成交
现金不能透支
```

RiskGuard 是最后防线。

PM 不应该把会被 RiskGuard 裁成 0 的订单算作已完成部署。

典型例子：

```text
茅台 100 股接近或超过 30% 单票上限
=> PM 不能说“我已经通过买茅台完成目标仓位”
=> 如果一手会被硬约束拦掉，应转向其他候选
```

## 市场环境判断规则

### WEAK

适合判断为 WEAK 的情况：

```text
多数投票信号 bearish/neutral
技术面多数低于关键均线
风险经理提示波动/集中度/流动性风险
sentiment 不支持
technical 或 sentiment timeout/missing
没有多个清晰 bullish 候选
```

WEAK 下 PM 应该：

```text
优先保护现金
目标仓位 40%-60%
不因为估值便宜单独买满
不因为一个基本面 BULL 就大幅建仓
已有弱势仓位可以减仓
```

适合的提示词风格：

```text
defensive
cash-preserving
risk-first
require technical or sentiment confirmation before upgrading regime
```

不适合的提示词：

```text
deploy unused cash aggressively
close deployment gap
do not miss rebound
```

这些词会让 PM 在弱市里过早加仓。

### SIDEWAYS_OR_SMALL_UP

适合判断为 SIDEWAYS_OR_SMALL_UP 的情况：

```text
信号混合
有 2-4 个可买候选
风险不高
技术面不是全面转好，也不是全面转坏
市场没有强反弹证据
```

SIDEWAYS 下 PM 应该：

```text
目标仓位 65%-75%
买最清晰的 2-4 个候选
保留 25%-35% 现金
不频繁换手
冲突信号下用小仓
```

适合的提示词风格：

```text
selective deployment
lower-band target
scale down imperfect candidates
avoid churn
```

不适合的提示词：

```text
full deployment
close all cash gap
RSI cannot block buying
```

这些更适合反弹，不适合震荡。

### REBOUND

适合判断为 REBOUND 的情况：

```text
多个投票分析师同时转多
技术面有明显修复
sentiment/政策/市场情绪改善
风险经理没有重大警告
股票池内部广度改善
已有上涨不是单一股票孤立上涨
```

REBOUND 下 PM 应该：

```text
目标仓位 80%-90%
如果当前仓位低，计算 target_gap_value
至少补一部分仓位缺口
如果第一候选被一手/单票限制卡住，转向下一个候选
RSI 过热只能降权，不能单独阻止买入
```

适合的提示词风格：

```text
deployment-gap sizing
reallocate blocked budget
close at least part of target gap
do not leave cash idle without explicit blockers
```

不适合的提示词：

```text
cash is always a position
wait for perfect confirmation
valuation cheap only as risk reference
```

这些会让 PM 在反弹里踏空。

## 月份和季节性规则

月份信息不应该写成长篇 PM prompt。

错误做法：

```text
把 1 月、2 月、5 月、春节、两会、年报季规则全部塞进 PM system prompt。
```

问题：

```text
prompt 会越来越长
PM 会背口诀
容易过拟合历史月份
不同年份的同一月份环境可能完全不同
```

正确做法：

```text
系统生成 calendar_context
PM 把它作为辅助证据
```

示例：

```json
{
  "calendar_context": {
    "month": 1,
    "season": "pre_spring_festival",
    "liquidity_bias": "slightly_tight",
    "earnings_phase": "annual_report_preview_window",
    "regime_hint": "do not upgrade above SIDEWAYS without technical confirmation"
  }
}
```

不同月份的使用方式：

| 场景 | 日历信息怎么用 | 不该怎么用 |
|---|---|---|
| 1 月春节前 | 提醒流动性偏紧，弱市不轻易升级 | 机械认为一定跌 |
| 2 月春节后 | 提醒可能有春季躁动/反弹 | 机械满仓 |
| 3-4 月年报季 | 关注业绩预告/年报风险 | 忽视技术和估值 |
| 5 月震荡窗口 | 提醒小涨/横盘可能性 | 强行降低仓位 |
| 政策会议前 | sentiment 权重可提高 | 听到政策就无脑买 |

结论：

```text
月份是 regime evidence，不是交易规则本身。
```

## 提示词工程总原则

### 原则 1：稳定规则放 system prompt

适合放进 system prompt：

```text
A 股 T+1
100 股一手
long/short/hold 定义
valuation 不投票
risk manager 不直接投票
PM 必须看当前持仓
不能超现金
```

不适合放进 system prompt：

```text
某次实验的仓位区间
某个月份的历史规律
某个股票池的临时观察
某次回测失败后的补丁式规则
```

### 原则 2：实验变量放 env/config

适合放 env/config：

```text
PM_EXPERIMENT_MODE
PM_CONTEXT_MODE
WEAK target exposure
SIDEWAYS target exposure
REBOUND target exposure
compact excerpt length
是否启用日历上下文
是否启用降级规则
```

原因：

```text
这样可以 A/B 测，不需要改 prompt 文件。
```

### 原则 3：当天事实放 dynamic context

适合放 dynamic context：

```text
当前价格
当前持仓
现金
股票仓位
分析师结构化信号
风险约束
T+1 可卖量
涨跌停状态
日历上下文
```

不适合放 dynamic context：

```text
分析师完整长文
全部 tool traces
全部 thinking
重复的 conference summary
```

### 原则 4：不要用 prompt 解决工具问题

如果出现：

```text
execute_code 报字段错
tool 多传无害参数
ticker 解析错误
价格数据缺失
固定指标没准备
```

应该改：

```text
工具 schema
数据 adapter
固定指标脚本
preflight 检查
缓存
```

不应该改：

```text
告诉 LLM “请不要写错字段”
告诉 LLM “请不要传 keyword”
告诉 LLM “请小心 ticker”
```

这种提示词会失效，也会浪费 token。

### 原则 5：不要用 prompt 解决执行硬约束

如果出现：

```text
买入超过现金
卖出超过 T+1 可卖量
一手导致仓位超限
行业集中度超限
```

应该改：

```text
RiskGuard
trade executor
PM 工具返回提示
上下文里的 hard constraints
```

PM prompt 可以提醒，但不能作为唯一防线。

## 哪种情况适合改哪种提示词

### 改 PM prompt 的情况

适合改 PM prompt：

```text
PM 明明看到信号但解释错了规则
PM 不知道 valuation 该不该投票
PM 不知道 weak bull 怎么处理
PM 不知道 REBOUND 里目标仓位要提高
PM 把会被 RiskGuard 拦截的订单算作已部署
PM 在已有持仓时频繁无意义换手
```

不适合改 PM prompt：

```text
分析师没有输出信号
技术指标算错
新闻工具报错
数据缺失
模型余额不足
LLM timeout
```

PM prompt 示例方向：

```text
If current equity exposure is below the selected target band, compute the gap
before sizing orders.

Do not count orders likely to be clipped to zero by RiskGuard as deployed
exposure.

If technical or sentiment confirmation is missing, do not upgrade regime above
the maximum allowed regime supplied in regime_evidence.
```

### 改分析师 prompt 的情况

适合改分析师 prompt：

```text
分析师不给 SIGNAL 行
分析师长篇输出但没有结论
分析师混淆短期技术和长期基本面
分析师过度乐观或过度悲观
分析师没有说明数据质量
```

不适合改分析师 prompt：

```text
工具返回字段错
固定指标缺失
PM 仓位太高
RiskGuard 裁单
```

分析师 prompt 示例方向：

```text
For each ticker, output exactly one SIGNAL line:
SIGNAL: BULL|BEAR|NEUTRAL | CONFIDENCE: 0-100 | TICKER: <ticker>

Then provide at most:
- 2 evidence bullets
- 1 risk bullet
- 1 data_quality note
```

### 改 Risk Manager prompt 的情况

适合改 Risk Manager prompt：

```text
风险经理只讲宏观，不讲组合集中度
风险经理忽视 T+1
风险经理没提示现金过低
风险经理没提示单票/行业集中
```

不适合改 Risk Manager prompt：

```text
实际单票限制没执行
订单没有被裁剪
PM 不看风险提示
```

Risk Manager prompt 示例方向：

```text
Always report:
- cash pct
- equity exposure pct
- top position weight
- sector concentration
- T+1 sellable quantity risks
- whether new buys should be blocked or only size-reduced
```

### 改 PM context compactor 的情况

适合改 compactor：

```text
PM 输入过长
PM 被分析师长篇叙事带偏
PM 读不到 timeout/missing 状态
PM 错过 bull/bear 冲突
PM 需要审计摘录但不需要全文
```

不适合改 compactor：

```text
分析师本身没有事实
工具没有返回数据
PM 规则本身错了
```

compact context 应该包含：

```text
signal/confidence/ticker
per-analyst short evidence
missing/timeout status
conflict_tickers
conflict_excerpt
risk_excerpt
raw_context_stats
```

### 改工具/代码的情况

适合改工具/代码：

```text
execute_code NameError / KeyError
date 字段不一致
High/Low/Close 大小写不一致
LLM 多传 keyword/tickers 这类无害参数
固定指标没准备
价格缓存不足
东方财富接口不可用
```

原则：

```text
能用代码保证的，不用 prompt 请求模型自觉。
```

## 当前已遇到的问题和处理结果

### 1. DeepSeek API key / balance

问题：

```text
401 invalid api key
402 insufficient balance
```

处理：

```text
401 是 key 加载/覆盖问题或 key 错误
402 是账户余额不足，不是代码 bug
```

后续：

```text
长实验前先跑 1-2 天 smoke
启用 compact context 降低 PM token
避免 timeout 重试烧钱
```

### 2. execute_code 动态代码错误

遇到过：

```text
print not defined
date 字段不存在
recent_20 作用域问题
High/Low/Close 大小写问题
```

处理方向：

```text
固定指标 run_indicator
prepare_experiment_code
validated code cache
candidate indicator 保存
代码修复只作为兜底，不作为主要路径
```

结论：

```text
回测期间尽量让技术分析师使用固定 indicator_id，
不要每次让 LLM 现场写 pandas。
```

### 3. 工具参数不宽容

遇到过：

```text
dashscope_search(keyword=...)
crawl_ths_position(tickers=...)
```

处理方向：

```text
工具接受常见别名和无害多余参数
不要用 prompt 要求模型永远传对参数名
```

### 4. PM max iteration / 日志噪音

问题：

```text
PM 已经调用工具记录完所有 ticker 决策，
但模型还想输出总结，
系统到达 max iterations。
```

处理：

```text
如果所有 ticker 决策已记录，用确定性 summary 收尾。
健康检查把 DSML/PM max-iter 残留作为 advisory，而不是硬错误。
```

### 5. RiskGuard 行业映射

问题：

```text
很多股票没行业映射，被归到 “其他”，导致行业集中度误拦。
```

处理：

```text
给 8 股票池补显式行业：
消费、家电制造、新能源、金融、医药
```

结论：

```text
风控硬约束必须有正确元数据，否则会误伤策略。
```

### 6. PM v3 反弹月仓位不足

问题：

```text
PM 判断 REBOUND，但仓位只有约 55%-60%。
茅台被一手/单票限制拦住后，PM 没有把预算充分转给其他候选。
```

处理：

```text
PM v4 加入 deployment gap：
target_gap_value = target_lower - current_exposure
top candidate blocked => reallocate to next viable candidates
```

结果：

```text
2 月反弹 v4 两天后仓位达到约 83%，进入 REBOUND 目标区间。
```

### 7. PM v4 弱市可能偏乐观

问题：

```text
1 月弱市前两天，PM 可能把环境判断为 SIDEWAYS_OR_SMALL_UP，
仓位约 65.8%，高于 WEAK 目标上沿。
```

原因：

```text
fundamentals/valuation 多个股票偏多
technical/sentiment timeout 或缺失
PM 没把缺失确认视为降级证据
```

待处理：

```text
生成 regime_evidence.max_allowed_regime。
如果 technical/sentiment missing，不能升级到 REBOUND。
如果技术广度弱，应降级到 WEAK 或 SIDEWAYS 下沿。
```

### 8. PM 动态上下文过长

问题：

```text
8 股票池下，PM 日志段可达 180-220 KB/天。
PM 重读全部分析师长文、工具结果、会议摘要。
```

处理：

```text
新增 PM_CONTEXT_MODE=compact。
默认 raw，不影响旧实验。
compact 模式下 PM 吃结构化事实 + 短摘录 + 冲突加长。
```

待验证：

```text
compact 是否明显省 token
compact 是否遗漏关键风险
compact 是否改变 PM 行为过多
```

已补充的工程约束：

```text
compact parser/schema/formatter 小修，不直接重跑 LLM。
先用 scripts/replay_pm_compact_context.py 对已有 reasoning log 离线重放。
只有 replay 显示 PM 事实输入发生重要变化，才考虑重新跑 1 天或 2 天 smoke。
```

已经验证的 parser 问题：

```text
technical analyst 使用 BULLISH / BEARISH，而旧 parser 只认 BULL / BEAR。
fundamentals analyst 有时用 Markdown 表格给信号，而不是 SIGNAL: ... 格式。
普通英文里的 "no room for error" 不能被误判为分析失败。
```

当前处理：

```text
BULLISH / BEARISH / UP / DOWN 已归一为 BULL / BEAR。
Markdown 表格行已能解析为 ticker / signal / confidence。
失败检测只认 traceback、timeout、network error、[ERROR] 等真实失败痕迹。
```

离线验证结果：

```text
backtest_202401_regime_pm_v4_weak_compact_smoke
2024-01-02: missing signals old=8 new=0
2024-01-03: missing signals old=8 new=0
```

对实验设计的影响：

```text
这类修复先不重跑回测。
先确认 PM 看到的结构化事实已经正确。
等多个小修合并后，再跑 1 天 behavior smoke。
不要为了单个 parser 小修重复烧 2 天 LLM 成本。
```

### 9. 市场环境证据不要完全交给 PM 自由判断

问题：

```text
PM 读长文后可能把弱市误判成 SIDEWAYS。
同一批分析师信号里，基本面偏多、技术偏弱时，PM 容易被优质资产叙事带偏。
```

处理：

```text
pipeline 生成 regime_evidence。
PM 先看 regime_evidence.suggested_regime 和 max_allowed_regime。
PM 不能选择高于 max_allowed_regime 的市场环境。
```

v1 规则：

```text
technical_breadth weak:
  max_allowed_regime = WEAK

sentiment missing_or_sparse:
  max_allowed_regime <= SIDEWAYS_OR_SMALL_UP

risk score >= 70:
  max_allowed_regime = WEAK

risk score >= 60:
  max_allowed_regime <= SIDEWAYS_OR_SMALL_UP

clean bullish candidates 足够多 + technical strong + risk not elevated:
  suggested_regime = REBOUND
```

边界：

```text
组合集中度风险影响仓位调整，不等于市场弱。
比如 2 月反弹中，如果技术广度 strong，只因为五粮液仓位 29.9%，
不应该把市场环境从 REBOUND 直接降成 WEAK。
```

离线 replay 结论：

```text
1 月 1/02、1/03:
  suggested=WEAK, max=WEAK, target=40-60%

2 月 2/20、2/21、2/23:
  suggested=REBOUND, max=REBOUND, target=80-90%

2 月 2/22:
  suggested=SIDEWAYS_OR_SMALL_UP
  原因是 sentiment missing_or_sparse，不是技术弱。
```

### 10. 分析师输出压缩

问题：

```text
PM compact 后仍然会被分析师长输出污染日志和上下文。
分析师经常重复工具结果、写市场长文、写投资哲学反思。
```

处理：

```text
ANALYST_OUTPUT_MODE=compact
```

要求：

```text
SIGNAL 行必须在最前面。
每个 ticker 最多 2 条短 bullet。
不写长篇市场论文。
不重复完整工具结果。
缺数据写 DATA_GAP。
SUMMARY 不超过 80 词。
```

实验边界：

```text
这是新的实验变量。
不能和 PM 策略大改混在同一次实验里判断效果。
建议先跑 1 天 smoke，比对日志体积、MISSING 信号、PM 行为是否离谱。
```

### 11. Reasoning log 压缩

问题：

```text
ANALYST_OUTPUT_MODE=compact 只能压最终回答。
daily reasoning log 仍会保存 thinking/tool_use/tool_result。
工具结果和 DSML 残留会让日志很大，也会干扰健康检查阅读。
```

处理：

```text
REASONING_LOG_MODE=compact  # 默认
```

compact reasoning log 保留：

```text
PM 最终决策
PM 对 regime / exposure / blockers 的说明
每个 agent 的 SIGNAL / DATA_GAP / SUMMARY
tool_use_count / tool_result_count
短错误片段
```

compact reasoning log 删除：

```text
完整 tool_result
长 thinking
DSML/tool-call 原文残留
大段 K 线/财务表原文
```

需要深度排查工具时再开：

```text
REASONING_LOG_MODE=raw
```

实验影响：

```text
这是日志层优化，不改变 PM 买卖逻辑。
旧日志不会自动变小；只有新跑的实验日志会变小。
```

## 实验阶段该用什么提示词

### Level 0: Code And Data Check

目标：

```text
确认能跑，不看收益。
```

适合提示词：

```text
Be concise.
Use fixed indicators first.
Report data_quality.
Do not invent unavailable data.
```

不适合提示词：

```text
复杂市场环境判断
仓位优化
月份规律
长篇投资哲学
```

如果 Level 0 失败：

```text
优先修工具、schema、数据、preflight。
不要改策略。
```

### Level 1: Behavior Smoke

目标：

```text
看 PM 是否按规则买卖。
```

适合提示词：

```text
Explicitly state regime, target exposure, current exposure, and blockers.
For each ticker, decide exactly once.
If holding cash below/above target, explain why.
```

不适合提示词：

```text
要求最大化收益
要求跑赢基准
要求寻找 alpha
```

### Level 2: Short Trend Check

目标：

```text
看交易频率、仓位稳定性、是否过拟合。
```

适合提示词：

```text
Avoid churn.
Only add if new evidence changes the position.
Track whether the original thesis is intact.
```

### Level 3: Full-Month Validation

目标：

```text
看完整周期下的收益、回撤、归因。
```

适合提示词：

```text
Use stable rules.
Do not change strategy mid-run.
Record blockers and thesis changes.
```

不适合在 Level 3 临时加入：

```text
新月份规则
新仓位规则
新分析师 persona
新工具行为
```

## 当前推荐的下一步实验

### Experiment A: compact context smoke

目的：

```text
验证 PM_CONTEXT_MODE=compact 是否降低 token，同时不丢关键风险。
```

窗口：

```text
2024-01-02 ~ 2024-01-03
```

唯一变量：

```text
PM_CONTEXT_MODE=compact
```

固定：

```text
PM_EXPERIMENT_MODE=regime_target_exposure
股票池不变
分析师不变
模型不变
数据不变
RiskGuard 不变
```

成功标准：

```text
PM 日志显著下降
PM 仍识别 technical/sentiment timeout
PM 不遗漏估值/技术冲突
交易结果没有明显离谱
```

失败标准：

```text
PM 看不到关键风险
PM 误以为所有 missing 是 neutral
PM 因信息过少乱买或不买
```

### Experiment B: regime downgrade evidence

目的：

```text
修复 1 月弱市被误判为 SIDEWAYS 的问题。
```

设计：

```text
新增 regime_evidence:
- technical_breadth
- sentiment_status
- missing_or_timeout_agents
- max_allowed_regime
- downgrade_reason
```

规则：

```text
technical 或 sentiment missing:
  max_allowed_regime <= SIDEWAYS_OR_SMALL_UP

technical breadth weak:
  prefer WEAK or SIDEWAYS lower bound

multiple bullish fundamentals but no technical confirmation:
  do not classify as REBOUND
```

成功标准：

```text
1 月前两天仓位不超过 60%，除非 technical/sentiment 明确修复。
2 月反弹仍能升到 80% 附近。
```

## 最终目标状态

最终希望 PM prompt 工程变成：

```text
短 system prompt
+ 结构化 regime_policy
+ 结构化 regime_evidence
+ 结构化 analyst facts
+ 短 conflict excerpts
+ 风控硬约束
```

而不是：

```text
巨大 system prompt
+ 巨大分析师长文
+ 巨大会议摘要
+ 事后不断补丁式规则
```

一句话总结：

```text
提示词工程不是把规则越写越长。
好的提示词工程是把稳定规则、实验变量、当天事实、硬约束分层，
让 PM 看到足够的信息，但不被长文和叙事淹没。
```
