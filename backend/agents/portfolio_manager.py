# -*- coding: utf-8 -*-
"""
Portfolio Manager Agent - Based on AgentScope ReActAgent
Responsible for decision-making (NOT trade execution)
"""

from typing import Any, Dict, Optional

import os
import re
from agentscope.agent import ReActAgent
from agentscope.memory import InMemoryMemory, LongTermMemoryBase
from agentscope.message import Msg, TextBlock
from agentscope.tool import Toolkit, ToolResponse
from agentscope.token import CharTokenCounter

from ..utils.progress import progress
from .prompt_loader import PromptLoader
from ..data.historical_price_manager import _is_a_share

_prompt_loader = PromptLoader()


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


class PMAgent(ReActAgent):
    """
    Portfolio Manager Agent - Makes investment decisions

    Key features:
    1. PM outputs decisions only (action + quantity per ticker)
    2. Trade execution happens externally (in pipeline/executor)
    3. Supports both backtest and live modes
    """

    def __init__(
        self,
        name: str = "portfolio_manager",
        model: Any = None,
        formatter: Any = None,
        initial_cash: float = 100000.0,
        margin_requirement: float = 0.25,
        config: Optional[Dict[str, Any]] = None,
        long_term_memory: Optional[LongTermMemoryBase] = None,
    ):
        self.config = config or {}

        # Portfolio state
        self.portfolio = {
            "cash": initial_cash,
            "positions": {},
            "margin_used": 0.0,
            "margin_requirement": margin_requirement,
        }

        # Decisions made in current cycle
        self._decisions: Dict[str, Dict] = {}

        # Tickers expected this cycle (populated in reply())
        self._pending_tickers: list = []

        # Current cycle prices, used for remaining-cash calculation in _make_decision
        self._current_prices: dict = {}

        # A股扩展：涨跌停状态 + T+1 可卖量（由 pipeline 注入）
        self._circuit_breakers: dict = {}   # {ticker: {status, can_buy, can_sell, warning, ...}}
        self._unlocked_quantities: dict = {}  # {ticker: int}  当日可卖股数（T+1 已解锁）

        # Create toolkit with _make_decision tool
        toolkit = self._create_toolkit()

        sys_prompt = _prompt_loader.load_prompt("portfolio_manager", "system")
        if self._lot_aware_probe_enabled():
            sys_prompt += self._lot_aware_probe_prompt_block()
        elif self._small_probe_enabled():
            sys_prompt += self._small_probe_prompt_block()
        if self._regime_target_enabled():
            sys_prompt += self._regime_target_prompt_block()

        # 压缩机制依赖 Qwen 结构化输出，同样不可靠，关闭
        # Pydantic 要求字段存在即使 enable=False
        compression_config = ReActAgent.CompressionConfig(
            enable=False,
            agent_token_counter=CharTokenCounter(),
            trigger_threshold=99999,
        )

        kwargs = {
            "name": name,
            "sys_prompt": sys_prompt,
            "model": model,
            "formatter": formatter,
            "toolkit": toolkit,
            "memory": InMemoryMemory(),
            "max_iters": 1,
            "compression_config": compression_config,
        }
        if long_term_memory:
            kwargs["long_term_memory"] = long_term_memory
            kwargs["long_term_memory_mode"] = "both"

        super().__init__(**kwargs)

    def _create_toolkit(self) -> Toolkit:
        """Create toolkit with decision recording tool"""
        toolkit = Toolkit()
        toolkit.register_tool_function(self._make_decision)
        return toolkit

    @staticmethod
    def _small_probe_enabled() -> bool:
        return (
            os.getenv("PM_EXPERIMENT_MODE", "").strip().lower()
            in {"small_probe", "probe"}
            or _env_flag("PM_ENABLE_SMALL_PROBE", False)
        )

    @staticmethod
    def _lot_aware_probe_enabled() -> bool:
        return (
            os.getenv("PM_EXPERIMENT_MODE", "").strip().lower()
            in {"lot_aware_probe", "lot-aware-probe", "lot_aware"}
            or _env_flag("PM_ENABLE_LOT_AWARE_PROBE", False)
        )

    @staticmethod
    def _regime_target_enabled() -> bool:
        return (
            os.getenv("PM_EXPERIMENT_MODE", "").strip().lower()
            in {
                "regime_target_exposure",
                "regime-target-exposure",
                "regime_target",
                "market_regime",
                "regime_rebound_beta",
                "regime-rebound-beta",
                "rebound_beta",
            }
            or _env_flag("PM_ENABLE_REGIME_TARGET_EXPOSURE", False)
        )

    @staticmethod
    def _rebound_beta_enabled() -> bool:
        return (
            os.getenv("PM_EXPERIMENT_MODE", "").strip().lower()
            in {"regime_rebound_beta", "regime-rebound-beta", "rebound_beta"}
            or _env_flag("PM_ENABLE_REBOUND_BETA_TACTICAL", False)
        )

    @staticmethod
    def _small_probe_prompt_block() -> str:
        probe_pct = _env_float("PM_PROBE_MAX_POSITION_PCT", 10.0)
        min_cash_pct = _env_float("PM_MIN_CASH_PCT", 60.0)
        max_single_pct = _env_float("PM_MAX_SINGLE_POSITION_PCT", 30.0)
        return f"""

---

## Experiment Mode: PM Small-Probe Positioning

This run is testing whether the PM is too conservative. Keep all normal A-share
rules, but add one controlled exception:

- If Fundamentals is BULL with confidence >= 75 AND Valuation is BULL/undervalued
  with confidence >= 65 AND risk is not elevated, you may open a small left-side
  probe even when technical confirmation is incomplete.
- This is a probe, not a conviction position. Do not pyramid repeatedly.
- Prefer 5%-10% target exposure. Hard cap for a new probe: {probe_pct:.1f}% of
  total equity, subject to A-share lot constraints.
- Minimum cash after all buy decisions: {min_cash_pct:.1f}% of total equity.
- Maximum single-name exposure after the trade: {max_single_pct:.1f}% of total
  equity.
- If the minimum 100-share lot would violate these caps, choose HOLD and explain
  that A-share lot size makes a clean small probe impossible.

The goal is to test controlled deployment of idle cash, not to become aggressive.
"""

    @staticmethod
    def _lot_aware_probe_prompt_block() -> str:
        normal_max_pct = _env_float("PM_LOT_AWARE_NORMAL_MAX_PCT", 20.0)
        high_price_threshold_pct = _env_float(
            "PM_LOT_AWARE_HIGH_PRICE_THRESHOLD_PCT",
            20.0,
        )
        min_cash_pct = _env_float("PM_MIN_CASH_PCT", 60.0)
        max_single_pct = _env_float("PM_MAX_SINGLE_POSITION_PCT", 30.0)
        return f"""

---

## Experiment Mode: PM Lot-Aware Probe Positioning

This run tests an A-share-aware position sizing rule. Keep all normal voting,
risk, T+1, and circuit-breaker rules, but size probes with board-lot feasibility:

- Ordinary probe target: 10%-{normal_max_pct:.1f}% of total equity.
- For high-priced A-shares, if the minimum 100-share lot is greater than
  {high_price_threshold_pct:.1f}% of total equity, only allow one minimum lot
  when the evidence is stronger:
  - Fundamentals is BULL with high confidence.
  - Valuation is BULL/undervalued with high confidence.
  - Risk is not elevated.
  - There is no clear bearish majority among voting analysts.
- Minimum cash after all buy decisions: {min_cash_pct:.1f}% of total equity.
- Maximum single-name exposure after the trade: {max_single_pct:.1f}% of total
  equity.
- If one lot would violate the cash floor or single-name cap, choose HOLD and
  explicitly say that A-share lot size makes the trade infeasible.

The goal is not to become aggressive. It is to avoid a fixed percentage cap
making high-priced A-shares impossible to trade.
"""

    @staticmethod
    def _regime_target_prompt_block() -> str:
        weak_low = _env_float("PM_REGIME_WEAK_EQUITY_LOW_PCT", 40.0)
        weak_high = _env_float("PM_REGIME_WEAK_EQUITY_HIGH_PCT", 60.0)
        sideways_low = _env_float("PM_REGIME_SIDEWAYS_EQUITY_LOW_PCT", 65.0)
        sideways_high = _env_float("PM_REGIME_SIDEWAYS_EQUITY_HIGH_PCT", 75.0)
        rebound_low = _env_float("PM_REGIME_REBOUND_EQUITY_LOW_PCT", 80.0)
        rebound_high = _env_float("PM_REGIME_REBOUND_EQUITY_HIGH_PCT", 90.0)
        rebound_initial_floor = _env_float(
            "PM_REBOUND_INITIAL_DEPLOY_FLOOR_PCT",
            65.0,
        )
        rebound_beta_block = (
            PMAgent._rebound_beta_prompt_block()
            if PMAgent._rebound_beta_enabled()
            else ""
        )
        return f"""

---

## Experiment Mode: Market-Regime Target Exposure

This run tests a PM rule learned from the 8-stock market-regime experiments:

1. First classify the market regime.
2. Then choose the portfolio target equity exposure.
3. Then allocate within the stock pool.

Do this before calling `_make_decision`. Your first reasoning for the day must
state the chosen regime, current exposure from `portfolio_exposure`, target
exposure, and what trades move the portfolio toward that target.

### Regime Classification

Use only the current context. Do not invent index data if it is absent.

If `regime_evidence` is present, use it as the first source of truth:

- Start from `regime_evidence.suggested_regime`.
- Do not choose a regime above `regime_evidence.max_allowed_regime`.
- You may choose a more defensive regime if portfolio/risk constraints justify it.
- Your first reasoning must cite `technical_breadth`, `sentiment_status`,
  `clean_bullish_count`, `conflict_count`, `bearish_majority_count`, and
  `downgrade_reasons`.
- If `regime_evidence.max_allowed_regime` is WEAK, do not deploy toward a
  SIDEWAYS or REBOUND target band.

Classify from:
- Breadth of voting analyst signals across the whole stock pool.
- Technical direction and momentum comments across the pool.
- Risk manager warnings.
- Number of high-conviction candidates with aligned Technical/Fundamentals/Sentiment.
- Current exposure and cash from `portfolio_exposure`.

Regime labels:

- **WEAK**: broad bearish/neutral signals, risk warnings elevated, few clean bullish
  candidates, or most candidates are below key moving averages.
- **SIDEWAYS_OR_SMALL_UP**: mixed signals, some bullish candidates but no broad
  upside consensus, market not clearly weak.
- **REBOUND**: broad improvement, multiple strong bullish candidates, technical
  repair across the pool, and risk warnings not elevated.

### Target Equity Exposure

- WEAK: target equity exposure {weak_low:.0f}%-{weak_high:.0f}%.
- SIDEWAYS_OR_SMALL_UP: target equity exposure {sideways_low:.0f}%-{sideways_high:.0f}%.
- REBOUND: target equity exposure {rebound_low:.0f}%-{rebound_high:.0f}%.

Use the lower end of the band when signals are mixed or risk is high. Use the
upper end only when breadth and risk both support it.

### Allocation Rules

- If current equity exposure is below the target band, prioritize buying or
  adding the best-ranked candidates until you approach the band.
- If current equity exposure is above the target band, reduce weakest or most
  overvalued holdings first.
- In REBOUND, do not trim winners only because RSI is overbought. Trim only if
  there is a bearish signal, valuation/risk is elevated, concentration is too
  high, or the position violates constraints.
- In WEAK, cash defense remains valid. Do not force deployment just to reach a
  high exposure.
- In SIDEWAYS_OR_SMALL_UP, add selectively; do not churn small positions without
  a clear signal.

### Deployment Discipline

This is the key rule being tested. A regime label is not enough; your orders must
move the portfolio toward the target band.

- Before sizing orders, compute the deployment gap:
  `target_gap_pct = target_lower_equity_pct - current_equity_exposure_pct`.
  `target_gap_value = max(0, target_gap_pct / 100 * total_equity)`.
  Use `portfolio_exposure.total_equity` and `portfolio_exposure.equity_exposure_pct`
  for this calculation.
- If you classify the regime as REBOUND and current equity exposure is below
  {rebound_low:.0f}%, you must deploy cash toward at least the lower end of the
  band unless all viable candidates are blocked by hard constraints or clear
  risk rejection.
- In early REBOUND from low exposure, do not stay defensive. If current equity
  exposure is below {rebound_initial_floor:.0f}% and there are at least 2 viable
  candidates, the current session must raise exposure to about
  {rebound_initial_floor:.0f}% or explain hard blockers. This is the rebound
  starting core; it is not full risk-on.
- In REBOUND, close at least 50% of `target_gap_value` in the current session,
  or close the full gap if enough viable candidates and cash are available.
- If the top candidate is blocked by A-share lot size, single-name limit, or cash
  allocation limit, immediately reallocate that unused budget to the next-ranked
  viable candidates. Do not leave the budget idle only because the first choice
  was infeasible.
- Do not count an intended order toward target exposure if it is likely to be
  clipped to zero by `risk_guard_constraints` or A-share 100-share lot rules.
  For example, if one lot would exceed the single-position limit, treat that
  ticker as unavailable for this session and move to the next candidate.
- Spread the deployment gap across 2-4 viable candidates when one candidate
  cannot absorb enough size. A single tiny tactical buy is not enough if the
  portfolio remains far below the target band.
- If a candidate is bullish but imperfect, scale the position down; do not turn
  it into HOLD unless the reason is explicit: bearish majority, elevated risk,
  valuation cap with no margin of safety, T+1/circuit issue, single-name cap, or
  industry concentration cap.
- In REBOUND, RSI overbought alone is not a sell/skip reason. It can reduce size,
  but it should not prevent deployment into otherwise strong candidates.
- In SIDEWAYS_OR_SMALL_UP, if current exposure is below {sideways_low:.0f}%,
  deploy enough to approach the lower band using the best-ranked candidates, but
  keep position sizes smaller than in REBOUND.
- If you finish below the target band, your final reasoning must list the exact
  blockers for the unused cash. Generic caution is not enough.

Practical sizing guide when below target:

- Strong consensus candidate: target 15%-25% position, subject to caps.
- Weak bull / tactical candidate: target 8%-12% position in REBOUND, or 5%-10%
  in SIDEWAYS_OR_SMALL_UP.
- Diversifier with neutral technicals but strong fundamentals: target 8%-12% in
  REBOUND, or 5%-10% in SIDEWAYS_OR_SMALL_UP.
- Avoid leaving more than the target cash band unless the above blockers apply.
- If the remaining deployment gap is large, size viable candidates from the gap
  budget first, then round down to A-share 100-share lots and hard caps.
- Your final reasoning must explicitly state: current equity exposure, target
  lower bound, target gap value, amount actually deployed, and exact blockers for
  any remaining gap.

### Ranking Candidates

When deploying cash, rank candidates by:

1. Voting analyst consensus from Technical, Fundamentals, and Sentiment.
2. Risk manager approval and no circuit-breaker liquidity issue.
3. Valuation as a sizing cap, not a vote.
4. Diversification and single-name concentration.
5. A-share 100-share lot feasibility.

The goal is not to maximize trading frequency. The goal is to make cash level
consistent with market regime.
{rebound_beta_block}
"""

    @staticmethod
    def _rebound_beta_prompt_block() -> str:
        max_pos_pct = _env_float("PM_REBOUND_BETA_MAX_POSITION_PCT", 10.0)
        sleeve_cap_pct = _env_float("PM_REBOUND_BETA_SLEEVE_CAP_PCT", 25.0)
        min_conf = _env_float("PM_REBOUND_BETA_MIN_TECH_CONF", 65.0)
        initial_floor = _env_float("PM_REBOUND_INITIAL_DEPLOY_FLOOR_PCT", 65.0)
        return f"""

### Experimental Add-on: Rebound Tactical Beta Sleeve

This add-on is enabled only for this experiment. It tests whether the PM misses
too much upside in confirmed rebound windows because fundamental/valuation
analysts suppress high-beta recovery trades.

Use this add-on only when all are true:

- The chosen regime is REBOUND, or `regime_evidence.suggested_regime` is REBOUND
  and `regime_evidence.max_allowed_regime` is REBOUND.
- Current equity exposure is below the REBOUND target lower bound.
- The portfolio still has deployable cash after hard risk constraints.

First-session deployment rule:

- When the portfolio starts the REBOUND session below {initial_floor:.0f}% equity
  exposure, first build a rebound core near {initial_floor:.0f}% before making
  fine-grained stock-picking distinctions.
- Allocate that core across 3-5 viable names when possible: 1-2 quality anchors
  plus 1-3 tactical beta candidates.
- Do not let a blocked high-price quality anchor, such as a large 100-share lot,
  delay the rebound core. Reallocate that budget immediately.

Tactical beta candidate definition:

- Technical analyst is BULL with confidence >= {min_conf:.0f}, or technical
  evidence shows clear market-rebound participation.
- Sentiment analyst is BULL/positive, or the stock is a high-beta rebound proxy
  such as brokerage, insurance, EV/growth, or another market-sensitive recovery
  name discussed by analysts.
- There is no voting-analyst bearish majority.
- Risk manager does not flag severe risk, circuit-breaker risk, ST/delisting
  risk, or a hard liquidity problem.

Tactical beta ranking:

When choosing among eligible tactical beta names, rank by this order during
confirmed REBOUND:

1. Brokerage / market-activity proxy if technical is BULL: most direct benefit
   from rising turnover and risk appetite.
2. Insurance / financial re-rating proxy if technical is BULL and risk is not
   severe: benefits from broad valuation repair.
3. EV/growth/high-beta leader if technical is BULL or recovering and valuation
   is not a hard blocker.
4. High-dividend bank/value repair name if technical is BULL or sentiment is
   positive: lower beta, useful as stabilizer.
5. Quality consumer anchors: good core holdings, but do not let them crowd out
   the tactical beta sleeve in a rebound-capture experiment.

If the top-ranked beta name is rejected, state whether the blocker is bearish
majority, severe risk, one-lot/cash feasibility, concentration, or sector cap.
Do not reject it only because fundamentals or valuation is weaker than quality
consumer names.

How to treat fundamentals and valuation:

- Fundamentals BEAR or valuation BEAR is not an automatic veto for this sleeve.
  It reduces size and requires a clearer technical/sentiment justification.
- Do not use this add-on for structurally broken names with severe risk warnings,
  clear negative events, or deteriorating liquidity.
- Valuation remains a sizing cap, not a vote.

Sizing:

- Target 5%-{max_pos_pct:.0f}% per tactical beta position.
- Total tactical beta sleeve should not exceed {sleeve_cap_pct:.0f}% of total
  equity.
- If fundamentals or valuation is BEAR, prefer the lower half of the size range.
- Still obey A-share 100-share lots, cash limits, single-name cap, industry cap,
  and RiskGuard.

Final reasoning requirement:

- If this add-on is used, explicitly label the order as `rebound tactical beta`.
- If you reject an eligible tactical beta candidate while still below REBOUND
  target exposure, explain the hard blocker. Generic caution is not enough.
"""

    def _portfolio_market_value(self) -> float:
        value = float(self.portfolio.get("cash", 0) or 0)
        for ticker, pos in (self.portfolio.get("positions", {}) or {}).items():
            if not isinstance(pos, dict):
                continue
            qty = float(pos.get("long", pos.get("quantity", 0)) or 0)
            price = float(self._current_prices.get(ticker, 0) or 0)
            value += qty * price
        return value

    def _current_position_value(self, ticker: str) -> float:
        pos = (self.portfolio.get("positions", {}) or {}).get(ticker, {})
        if not isinstance(pos, dict):
            return 0.0
        qty = float(pos.get("long", pos.get("quantity", 0)) or 0)
        price = float(self._current_prices.get(ticker, 0) or 0)
        return qty * price

    @staticmethod
    def _parse_tickers_from_content(content: str) -> list[str]:
        found = re.findall(r"\b(?:\d{6}\.(?:SH|SZ|BJ)|[A-Z]{1,5})\b", content)
        if not found:
            found = re.findall(r"\b([A-Z]{2,5})\b", content)

        stop_words = {
            "USE", "FOR", "EACH", "THE", "AND", "BUY", "SELL", "HOLD",
            "CALL", "ONCE", "PER", "WITH", "ALL", "NOW", "NEXT",
            "EV", "AI", "US", "EU", "IPO", "ETF", "CEO", "CFO", "CTO",
        }
        return list(dict.fromkeys(t for t in found if t not in stop_words))

    def _make_decision(
        self,
        ticker: str,
        action: str,
        quantity: int,
        confidence: int = 50,
        reasoning: str = "",
    ) -> ToolResponse:
        """
        Record a trading decision for a ticker.

        Args:
            ticker: Stock ticker symbol (e.g., "AAPL")
            action: Decision - "long", "short" or "hold"
            quantity: Number of shares to trade (0 for hold)
            confidence: Confidence level 0-100
            reasoning: Explanation for this decision

        Returns:
            ToolResponse confirming decision recorded
        """
        if action not in ["long", "short", "hold"]:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Invalid action: {action}. "
                        "Must be 'long', 'short', or 'hold'.",
                    ),
                ],
            )

        # long/short 但 quantity=0 在语义上等于 hold，统一标准化
        if action in ("long", "short") and quantity == 0:
            action = "hold"

        actual_qty = quantity if action != "hold" else 0
        extra_warnings: list = []

        # ── A股涨跌停校验 ──────────────────────────────────────────────────────
        cb = self._circuit_breakers.get(ticker, {})
        if cb:
            if action == "long" and not cb.get("can_buy", True):
                # 涨停板买不进，改为 hold 并警告
                return ToolResponse(
                    content=[TextBlock(type="text", text=(
                        f"⚠️ {ticker} 当前涨停（价格={cb.get('current_price')}，"
                        f"涨停价={cb.get('limit_up_price')}），"
                        f"买单极大概率无法成交。已自动改为 hold。\n"
                        f"若仍想买入，请在下一交易日开盘时操作。"
                    ))],
                )
            if action == "short" and not cb.get("can_sell", True):
                # 跌停板卖不出，改为 hold 并警告
                return ToolResponse(
                    content=[TextBlock(type="text", text=(
                        f"⚠️ {ticker} 当前跌停（价格={cb.get('current_price')}，"
                        f"跌停价={cb.get('limit_down_price')}），"
                        f"卖单极大概率无法成交（流动性陷阱）。已自动改为 hold。\n"
                        f"当日退出受阻，建议关注次日开盘情况。"
                    ))],
                )
            if cb.get("warning"):
                extra_warnings.append(cb["warning"])

        # ── T+1 可卖量校验（仅对 short 生效）──────────────────────────────────
        if action == "short" and actual_qty > 0:
            unlocked = self._unlocked_quantities.get(ticker)
            if unlocked is not None:
                if actual_qty > unlocked:
                    old_qty = actual_qty
                    actual_qty = unlocked
                    if actual_qty == 0:
                        # 全部是今日新买，无法卖出
                        return ToolResponse(
                            content=[TextBlock(type="text", text=(
                                f"⚠️ {ticker} 所有持仓均为今日买入（T+1锁仓），"
                                f"当日无法卖出任何股份。已自动改为 hold。\n"
                                f"可卖量将在明日开盘后恢复。"
                            ))],
                        )
                    extra_warnings.append(
                        f"T+1截断：卖出数量从 {old_qty} 股减至 {actual_qty} 股"
                        f"（今日可卖={unlocked}）"
                    )

        # ── 预算强制截断 ──────────────────────────────────────────────────────
        initial_cash = self.portfolio.get("cash", 0)
        pre_committed = 0.0
        for t, d in self._decisions.items():
            price = self._current_prices.get(t, 0)
            pre_committed += d["quantity"] * price
        pre_remaining = initial_cash - pre_committed

        if action == "long" and actual_qty > 0:
            price_now = self._current_prices.get(ticker, 0)
            if price_now > 0:
                # 单笔建仓上限：剩余现金的 40%（与 system prompt 保持一致）
                max_by_40pct = int(pre_remaining * 0.4 / price_now)
                # 绝对上限：不超过剩余现金总量
                max_affordable = min(max_by_40pct, int(pre_remaining / price_now))
                if self._small_probe_enabled() or self._lot_aware_probe_enabled():
                    total_value = max(self._portfolio_market_value(), initial_cash)
                    current_value = self._current_position_value(ticker)
                    min_cash_value = (
                        _env_float("PM_MIN_CASH_PCT", 60.0) / 100.0 * total_value
                    )
                    single_cap_value = (
                        _env_float("PM_MAX_SINGLE_POSITION_PCT", 30.0)
                        / 100.0
                        * total_value
                    )
                    if self._lot_aware_probe_enabled():
                        normal_cap_value = (
                            _env_float("PM_LOT_AWARE_NORMAL_MAX_PCT", 20.0)
                            / 100.0
                            * total_value
                        )
                        high_price_threshold_value = (
                            _env_float(
                                "PM_LOT_AWARE_HIGH_PRICE_THRESHOLD_PCT",
                                20.0,
                            )
                            / 100.0
                            * total_value
                        )
                        min_lot_qty = 100 if _is_a_share(ticker) else 1
                        min_lot_value = min_lot_qty * price_now
                        max_by_normal = int(normal_cap_value / price_now)
                        max_by_cash_floor = int(
                            max(pre_remaining - min_cash_value, 0) / price_now
                        )
                        max_by_single = int(
                            max(single_cap_value - current_value, 0) / price_now
                        )
                        experiment_cap = min(
                            max_by_normal,
                            max_by_cash_floor,
                            max_by_single,
                        )
                        if (
                            _is_a_share(ticker)
                            and min_lot_value > high_price_threshold_value
                            and min_lot_qty <= max_by_cash_floor
                            and min_lot_qty <= max_by_single
                        ):
                            experiment_cap = max(experiment_cap, min_lot_qty)
                            extra_warnings.append(
                                "PM手数感知试探："
                                f"最小一手约¥{min_lot_value:,.0f}，"
                                f"超过普通{_env_float('PM_LOT_AWARE_HIGH_PRICE_THRESHOLD_PCT', 20.0):.1f}%阈值；"
                                "允许最多一手，但需PM在理由中满足强信号条件"
                            )
                        if experiment_cap < max_affordable:
                            extra_warnings.append(
                                "PM手数感知上限截断："
                                f"normal≤{_env_float('PM_LOT_AWARE_NORMAL_MAX_PCT', 20.0):.1f}%, "
                                f"cash≥{_env_float('PM_MIN_CASH_PCT', 60.0):.1f}%, "
                                f"single≤{_env_float('PM_MAX_SINGLE_POSITION_PCT', 30.0):.1f}% "
                                f"→ 上限 {experiment_cap} 股"
                            )
                    else:
                        probe_cap_value = (
                            _env_float("PM_PROBE_MAX_POSITION_PCT", 10.0)
                            / 100.0
                            * total_value
                        )
                        max_by_probe = int(probe_cap_value / price_now)
                        max_by_cash_floor = int(
                            max(pre_remaining - min_cash_value, 0) / price_now
                        )
                        max_by_single = int(
                            max(single_cap_value - current_value, 0) / price_now
                        )
                        experiment_cap = min(
                            max_by_probe,
                            max_by_cash_floor,
                            max_by_single,
                        )
                        if experiment_cap < max_affordable:
                            extra_warnings.append(
                                "PM小仓试探上限截断："
                                f"probe≤{_env_float('PM_PROBE_MAX_POSITION_PCT', 10.0):.1f}%, "
                                f"cash≥{_env_float('PM_MIN_CASH_PCT', 60.0):.1f}%, "
                                f"single≤{_env_float('PM_MAX_SINGLE_POSITION_PCT', 30.0):.1f}% "
                                f"→ 上限 {experiment_cap} 股"
                            )
                    max_affordable = min(max_affordable, experiment_cap)
                if actual_qty > max_affordable:
                    old_qty = actual_qty
                    actual_qty = max_affordable
                    extra_warnings.append(
                        f"40%仓位上限截断：{old_qty} 股 → {actual_qty} 股"
                        f"（上限 ¥{pre_remaining * 0.4:,.0f} / ¥{price_now} = {max_by_40pct}股）"
                    )
        # ─────────────────────────────────────────────────────────────────────

        # ── A股手数对齐（100股最小买卖单位，向下取整）─────────────────────────
        if action != "hold" and actual_qty > 0 and _is_a_share(ticker):
            snapped = (actual_qty // 100) * 100
            if snapped != actual_qty:
                extra_warnings.append(
                    f"A股手数对齐：{actual_qty} 股 → {snapped} 股（100股整手）"
                )
            if snapped == 0:
                # 数量不足1手，自动降为 hold（避免 LLM 死循环重试）
                action = "hold"
                actual_qty = 0
                extra_warnings.append(
                    f"不足1手（100股），已自动降为 hold"
                )
            else:
                actual_qty = snapped
        # ─────────────────────────────────────────────────────────────────────

        self._decisions[ticker] = {
            "action": action,
            "quantity": actual_qty,
            "confidence": confidence,
            "reasoning": reasoning,
        }

        # 记录决策后重新计算剩余现金
        committed = pre_committed + actual_qty * self._current_prices.get(ticker, 0)
        remaining_cash = initial_cash - committed

        # 确定下一个未决策的 ticker
        remaining = [t for t in self._pending_tickers if t not in self._decisions]
        decided_count = len(self._decisions)
        total_count = len(self._pending_tickers)

        if remaining:
            next_ticker = remaining[0]
            next_price = self._current_prices.get(next_ticker, 0)
            # A股：买入数量取整到 100 股
            max_lots = int(remaining_cash * 0.4 / next_price / 100) if next_price > 0 else 0
            max_shares = max_lots * 100

            # 下一个 ticker 的涨跌停状态提示
            next_cb = self._circuit_breakers.get(next_ticker, {})
            next_cb_hint = ""
            if next_cb.get("warning"):
                next_cb_hint = f"\n  {next_cb['warning']}"
            next_unlocked = self._unlocked_quantities.get(next_ticker)
            if next_unlocked is not None:
                next_cb_hint += f"\n  T+1 可卖量: {next_unlocked} 股"

            next_hint = (
                f"\n\nDecided {decided_count}/{total_count}. "
                f"Remaining cash: ¥{remaining_cash:,.0f}. "
                f"Still need: {remaining}.\n"
                f"For {next_ticker} (price=¥{next_price}): "
                f"max 40% = {max_shares} shares (¥{max_shares * next_price:,.0f}, "
                f"must be multiple of 100).{next_cb_hint}\n"
                f"Now call: _make_decision("
                f'ticker="{next_ticker}", '
                f'action="long"|"short"|"hold", '
                f"quantity=<int multiple of 100>, "
                f"confidence=<0-100>, "
                f'reasoning="<why>")'
            )
        else:
            next_hint = (
                f"\n\nAll {total_count} tickers decided. "
                "You are done — do NOT call _make_decision again."
            )

        # 拼接当前决策的额外警告
        warnings_text = ("\n  注意: " + " | ".join(extra_warnings)) if extra_warnings else ""

        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        f"✓ Recorded: {ticker} → {action} {actual_qty} shares "
                        f"(confidence: {confidence}%)"
                        f"{warnings_text}"
                        f"{next_hint}"
                    ),
                ),
            ],
        )

    async def reply(
        self,
        x: Msg = None,
        structured_model=None,
        tickers: list = None,
        prices: dict = None,
        circuit_breakers: dict = None,
        unlocked_quantities: dict = None,
    ) -> Msg:
        """
        Make investment decisions

        Args:
            x: Input message with analyst signals
            tickers: Explicit list of tickers to decide on. If provided,
                     used directly instead of parsing from message content.

        Returns:
            Msg with decisions in metadata
        """
        if x is None:
            return Msg(
                name=self.name,
                content="No input provided",
                role="assistant",
            )

        # Clear previous decisions
        self._decisions = {}

        # 注入价格，供 _make_decision 计算真实剩余现金
        self._current_prices: dict = prices or {}

        # 注入 A股约束上下文（涨跌停状态 + T+1 可卖量）
        self._circuit_breakers = circuit_breakers or {}
        self._unlocked_quantities = unlocked_quantities or {}

        if tickers:
            # 调用方显式传入，最准确
            self._pending_tickers = list(tickers)
        else:
            # 从消息内容解析：只匹配 "ticker: " 格式，避免误抓正文词汇
            content_str = x.content if isinstance(x.content, str) else str(x.content)
            self._pending_tickers = self._parse_tickers_from_content(content_str)

        progress.update_status(
            self.name,
            None,
            "Analyzing and making decisions",
        )

        result = await super().reply(x, structured_model=structured_model)

        progress.update_status(self.name, None, "Completed")

        if self._pending_tickers and set(self._pending_tickers).issubset(
            self._decisions.keys()
        ):
            summary_lines = [
                "All PM decisions were recorded by _make_decision.",
                "Structured decisions:",
            ]
            for ticker in self._pending_tickers:
                decision = self._decisions[ticker]
                summary_lines.append(
                    "- {ticker}: {action} {quantity} shares "
                    "(confidence: {confidence}%)".format(
                        ticker=ticker,
                        action=decision.get("action"),
                        quantity=decision.get("quantity", 0),
                        confidence=decision.get("confidence", 0),
                    )
                )
            result = Msg(
                name=self.name,
                content="\n".join(summary_lines),
                role="assistant",
                metadata=result.metadata,
            )

        # Attach decisions to metadata
        if result.metadata is None:
            result.metadata = {}
        result.metadata["decisions"] = self._decisions.copy()
        result.metadata["portfolio"] = self.portfolio.copy()

        return result

    def get_decisions(self) -> Dict[str, Dict]:
        """Get decisions from current cycle"""
        return self._decisions.copy()

    def get_portfolio_state(self) -> Dict[str, Any]:
        """Get current portfolio state"""
        return self.portfolio.copy()

    def load_portfolio_state(self, portfolio: Dict[str, Any]):
        """Load portfolio state"""
        if not portfolio:
            return
        self.portfolio = {
            "cash": portfolio.get("cash", self.portfolio["cash"]),
            "positions": portfolio.get("positions", {}).copy(),
            "margin_used": portfolio.get("margin_used", 0.0),
            "margin_requirement": portfolio.get(
                "margin_requirement",
                self.portfolio["margin_requirement"],
            ),
        }

    def update_portfolio(self, portfolio: Dict[str, Any]):
        """Update portfolio after external execution"""
        self.portfolio.update(portfolio)
