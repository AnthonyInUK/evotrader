# Stock Selection Framework V1

目标：先把选股框架线调清楚，再继续做 PM、分析师 prompt 或结构化决策层。

当前策略不是“从股票池里综合评分直接买”，而是分成两步：

1. 选股：判断哪些股票有资格进入买卖点观察池。
2. 交易：只对入选股票寻找回踩趋势线买点和短线趋势失败卖点。

## 1. 策略原则

用户定义的核心框架：

```text
结构上多头
+ 业绩没问题
+ 半年热点板块
+ 符合买点：回踩趋势线
+ 卖点：5日线防守 / 趋势3天不新高
```

这套框架接近：

- CAN SLIM：基本面 + 行业领导股 + 市场方向 + 买点。
- Weinstein Stage Analysis：只做 Stage 2 上升趋势。
- Minervini / relative strength：优先找右侧强势股，不抄底。

但本项目是 A 股，需要加入：

- T+1
- 100 股一手
- 涨跌停
- 行业/主题轮动
- A 股假突破较多，优先回踩买点而不是追突破

## 2. 选股分层

### 2.1 市场环境过滤

第一版只接两个指数：

```text
000300.SH 沪深300
000001.SH 上证指数
```

用途：

- 沪深300：更贴近大盘核心资产。
- 上证指数：辅助判断整体市场情绪。

第一版规则：

```text
MARKET_BULL:
  沪深300 close > SMA20
  且 SMA20 走平或上行
  且 MACD 不明显空头

MARKET_WEAK:
  沪深300 close < SMA20
  且 SMA20 下行

MARKET_SIDEWAYS:
  其他情况
```

业务含义：

- 市场弱时，即使个股右侧，也降低仓位或只观察。
- 市场转强时，才积极找右侧回踩买点。

### 2.2 结构上多头

只做右侧，不猜底。

第一版日线规则：

```text
RIGHT_SIDE_BULL:
  close > SMA20
  close > SMA60 或 SMA20 >= SMA60
  MACD >= MACD_signal
  10日收益 > 0

RIGHT_SIDE_PULLBACK:
  中期仍多头
  close 接近 SMA20
  但没有有效跌破 SMA60

NOT_RIGHT_SIDE:
  close < SMA20 且 MACD < MACD_signal
  或 close < SMA60 且 SMA20 下行
```

第一版不追求完美，只要能把“明显右侧”和“明显弱势”分开。

### 2.3 业绩没问题

这里不是选最优秀公司，而是排除基本面硬伤。

第一版状态：

```text
FUNDAMENTAL_OK:
  营收/利润没有明显坍塌
  ROE 达到行业最低要求
  经营现金流没有明显恶化
  非金融股资产负债率不过高

FUNDAMENTAL_WEAK:
  有一项或多项弱项
  但没有达到硬性排除

FUNDAMENTAL_BLOCK:
  营收或利润明显坍塌
  或 ROE 过低
  或非金融股资产负债率过高
```

当前固定财务指标：

```text
营收同比 <= -15%        => BLOCK
营收同比 < 0            => WEAK
净利润同比 <= -30%      => BLOCK
净利润同比 < 0          => WEAK
普通公司 ROE < 3%       => BLOCK
普通公司 ROE < 8%       => WEAK
金融公司 ROE < 4%       => BLOCK
金融公司 ROE < 7%       => WEAK
非金融股每股经营现金流 < 0 => WEAK
非金融股资产负债率 > 85%  => BLOCK
```

金融股特殊处理：

- 银行营收小幅负增长，但净利润不下降、ROE 达标时，不直接判弱。
- 保险、券商盈利波动更大，可以进入 `FUNDAMENTAL_WEAK`，但不直接 BLOCK。
- 金融股不因为资产负债率高单独 BLOCK。

数据防未来函数：

```text
默认 report_lag_days = 90
```

也就是回测某一天时，只允许使用报告期至少早于当天 90 天的财务摘要。这样 2024 年 1 月不会偷看 2023 年年报。

### 2.4 半年热点板块

第一版先用手工主题标签和股票池内相对强弱，外部行业/主题指数作为第二层数据逐步接入。

当前实验股票池主题：

| Ticker | Theme |
|---|---|
| 600519.SH | 白酒/消费复苏/核心资产 |
| 000858.SZ | 白酒/消费复苏 |
| 000333.SZ | 家电/消费复苏/出口链 |
| 300750.SZ | 新能源/成长修复 |
| 600030.SH | 券商/活跃资本市场/反弹 beta |
| 601318.SH | 保险/金融估值修复 |
| 601398.SH | 银行/高股息/中特估 |
| 600276.SH | 创新药/医药修复 |

第一版支持两种模式：

```text
fixed:
  使用手工主题标签

relative:
  使用股票池内近似 120 交易日相对强弱
```

当前默认使用 `relative`。

第一版相对强弱规则：

```text
1. 对每只股票计算可用 60-120 个交易日收益
2. 对同一主题下股票取平均收益
3. 和股票池中位数收益比较
4. 主题强度 = 主题平均收益 - 全池中位数收益
```

状态：

```text
THEME_HOT:
  主题强度 >= +3%
  或进入当前股票池主题强度前三

THEME_NEUTRAL:
  主题强度不强不弱

THEME_COLD:
  主题强度 <= -5%
```

行业/主题指数目标清单：

- 白酒指数
- 证券指数
- 银行指数
- 保险指数
- 新能源/电池指数
- 医药指数
- 家电指数

当前数据源验证结论：

```text
第一层宽基指数：
  已使用新浪指数接口落地本地缓存。
  覆盖：上证指数、深证成指、创业板指、沪深300、中证500、中证1000。
  时间：2022-01-04 到 2026-06-09。
  用途：市场环境判断，避免只靠股票池内部信号判断 WEAK / SIDEWAYS / REBOUND。

第二层行业/主题指数：
  东方财富行业/概念板块接口在当前环境下大量断连、SSL 错误和 timeout。
  同花顺板块指数可作为替代源，但需要主题名称校准和分批采集。
  已验证成功：白酒。
  银行、证券、保险、电池、创新药等需要继续校准名称、timeout 和备用方案。
```

工程原则：

- 宽基指数必须优先稳定，因为它决定“能不能积极做多”。
- 行业/主题指数不要全量硬跑；先围绕交易框架需要的主题分批验证。
- 第二层如果接口不稳定，可以用主题 ETF 或代表股篮子近似主题强度。
- 主题数据失败时，不允许回测自动退化成“LLM 猜热点”，只能退回股票池内相对强弱。

## 3. 股票分桶

选股输出不是直接买卖，而是分桶：

```text
CORE_CANDIDATE:
  RIGHT_SIDE_BULL
  + FUNDAMENTAL_OK
  + THEME_HOT/THEME_NEUTRAL

TACTICAL_CANDIDATE:
  RIGHT_SIDE_BULL
  + FUNDAMENTAL_WEAK/NEUTRAL
  + THEME_HOT

WATCHLIST:
  业绩或主题不错
  但趋势还没到 RIGHT_SIDE_BULL

AVOID:
  NOT_RIGHT_SIDE
  或 FUNDAMENTAL_BLOCK
  或 severe risk
```

业务含义：

- CORE 可以给较大仓位。
- TACTICAL 只能小仓，卖点更严格。
- WATCH 不买，只等趋势确认或回踩买点。
- AVOID 不进入买卖点模块。
- `THEME_COLD` 即使技术右侧、业绩 OK，也不直接进 CORE，只能进入 WATCHLIST。

## 4. 买点

只对 CORE_CANDIDATE / TACTICAL_CANDIDATE 找买点。

第一版买点：

```text
PULLBACK_TO_TREND:
  trend_state in {RIGHT_SIDE_BULL, RIGHT_SIDE_PULLBACK}
  close 距离 SMA20 在 -1.5% 到 +3.0% 之间
  close >= SMA60
  MACD 不明显恶化
```

可解释为：

```text
趋势仍在，但价格回踩到趋势线附近，不是高位追涨。
```

如果 close 距离 SMA20 太远：

```text
EXTENDED:
  close > SMA20 * 1.06
```

则不追，等待回踩。

### 4.1 反弹环境下的右侧候选

标准买点仍然是 `PULLBACK_TO_TREND`。

但如果市场已经被实验固定为 `REBOUND`，且股票已经进入右侧候选但还没回踩，selector 只标记状态：

```text
REBOUND_RIGHT_SIDE_NO_PULLBACK:
  bucket in {CORE_CANDIDATE, TACTICAL_CANDIDATE}
  exit_signal == NONE
  没有标准回踩买点
```

业务含义：

- 反弹刚确认时，强势股可能不会马上回踩。
- 如果完全等 SMA20 回踩，可能错过反弹第一段。
- 但 selector 不决定是否追、不决定小仓试错、不决定买多少。
- 分析师/PM 可以基于这个状态，再结合组合、仓位、风险决定是否参与。

## 5. 卖点

卖点服务于风控，不等 PM 写长篇判断。

### 5.1 5日线防守

第一版用 SMA5：

```text
SMA5_DEFENSE_SELL:
  close < SMA5
```

执行建议：

- TACTICAL 仓：减仓或退出。
- CORE 仓：先减仓，除非基本面/主题继续很强。

### 5.2 趋势 3 天不新高

第一版定义：

```text
THREE_DAY_NO_NEW_HIGH:
  最近3个交易日 high 没有超过前高
  且 close 低于最近3日最高 close
  且 3日收益 <= 0
```

执行建议：

- 停止加仓。
- 对 TACTICAL 仓减仓。

### 5.3 卖点冲突

如果候选股同时触发 `exit_signal`，selector 只标记：

```text
exit_signal != NONE
=> selection_status = BLOCKED_BY_EXIT_SIGNAL
```

业务含义：

- 候选股出现买卖点冲突。
- 如果一只股票右侧结构不错，但当天已经跌破 5 日线或 3 天不新高，就不应被 selector 当成优先入场候选。
- 对已有持仓，是否减仓、观察还是继续持有，交给分析师/PM。

## 6. 离线验证纪律

选股框架先用离线 replay 验证，不直接跑 LLM。

原因：

- 选股规则是稳定框架，不应该每次依赖模型临场发挥。
- 先确认价格、均线、MACD、分桶、买卖点正确，再让 PM 做组合分配。
- 如果基础数据错了，后面 PM prompt 调得再细也会被带偏。

### 6.1 价格数据要求

`scripts/replay_stock_selection.py` 只允许读取覆盖当前分析日期的 parquet 缓存：

```text
分析 2024-02-20
=> 必须使用 end_date >= 2024-02-20 的缓存
=> 不允许用只到 2024-01-31 的旧缓存代替
```

业务含义：

- 回看 2 月反弹时，不能拿 1 月底价格当作 2 月价格。
- 计算 SMA20/SMA60 时，只需要当前日前足够多的最近交易日，不要求缓存覆盖一个过早的自然日 start。

### 6.2 当前离线命令

弱市样本：

```bash
A_SHARE_PRICE_CACHE_ONLY=1 python scripts/replay_stock_selection.py \
  --market-regime WEAK \
  --start 2024-01-02 --end 2024-01-05 \
  --tickers 600519.SH,601398.SH,000858.SZ,000333.SZ,300750.SZ,600030.SH,601318.SH,600276.SH \
  --json-out outputs/stock_selection/stock_selection_202401_weak_0102_0105.json
```

反弹样本：

```bash
A_SHARE_PRICE_CACHE_ONLY=1 python scripts/replay_stock_selection.py \
  --market-regime REBOUND \
  --start 2024-02-20 --end 2024-02-23 \
  --tickers 600519.SH,601398.SH,000858.SZ,000333.SZ,300750.SZ,600030.SH,601318.SH,600276.SH \
  --json-out outputs/stock_selection/stock_selection_202402_rebound_0220_0223.json
```

### 6.3 第一轮观察

1 月弱市：

- 工行连续进入 `CORE_CANDIDATE`，且多日出现 `PULLBACK_TO_TREND`。
- 美的也是 `CORE_CANDIDATE`，但更接近高位，不是理想回踩买点。
- 白酒、新能源、券商、保险大多停留在 `WATCHLIST`，说明弱市里不应大面积进攻。

2 月反弹：

- 多数股票进入 `RIGHT_SIDE_BULL`，说明市场广度改善。
- 中信证券、平安被识别为 `TACTICAL_CANDIDATE`，符合反弹 beta 的业务直觉。
- 五粮液、美的、平安等部分标的触发 `EXTENDED_WAIT_PULLBACK`，说明“选出来”和“马上买”已经被拆开。
- 对 CORE 仓观察是否跌破 SMA5/SMA20。

## 7. 第一版输出结构

离线 selector 每天输出：

```json
{
  "ticker": "600030.SH",
  "trend_state": "RIGHT_SIDE_BULL",
  "fundamental_state": "FUNDAMENTAL_WEAK",
  "theme_state": "THEME_HOT",
  "bucket": "TACTICAL_CANDIDATE",
  "entry_signal": "PULLBACK_TO_TREND",
  "exit_signal": "NONE",
  "selection_status": "ENTRY_SETUP_PULLBACK",
  "notes": ["brokerage beta", "technical right side"]
}
```

## 8. 实验纪律

不要先接 PM，不要先重跑 LLM。

顺序：

1. 离线跑当前 8 股，检查分桶是否符合直觉。
2. 离线跑 1 月弱市和 2 月反弹，看是否过度交易。
3. 再扩到 12-16 只候选池。
4. 只有 selector 输出稳定后，才接 PM context。
