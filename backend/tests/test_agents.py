# -*- coding: utf-8 -*-
# pylint: disable=W0212
import json
import tempfile
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from agentscope.message import Msg


class TestAnalystAgent:
    def test_init_valid_analyst_type(self):
        from backend.agents.analyst import AnalystAgent

        mock_toolkit = MagicMock()
        mock_model = MagicMock()
        mock_formatter = MagicMock()

        agent = AnalystAgent(
            analyst_type="technical_analyst",
            toolkit=mock_toolkit,
            model=mock_model,
            formatter=mock_formatter,
        )

        assert agent.analyst_type_key == "technical_analyst"
        assert agent.name == "technical_analyst"
        assert agent.analyst_persona == "Technical Analyst"

    def test_init_invalid_analyst_type(self):
        from backend.agents.analyst import AnalystAgent

        mock_toolkit = MagicMock()
        mock_model = MagicMock()
        mock_formatter = MagicMock()

        with pytest.raises(ValueError) as excinfo:
            AnalystAgent(
                analyst_type="invalid_type",
                toolkit=mock_toolkit,
                model=mock_model,
                formatter=mock_formatter,
            )

        assert "Unknown analyst type" in str(excinfo.value)

    def test_init_custom_agent_id(self):
        from backend.agents.analyst import AnalystAgent

        mock_toolkit = MagicMock()
        mock_model = MagicMock()
        mock_formatter = MagicMock()

        agent = AnalystAgent(
            analyst_type="fundamentals_analyst",
            toolkit=mock_toolkit,
            model=mock_model,
            formatter=mock_formatter,
            agent_id="custom_analyst_id",
        )

        assert agent.name == "custom_analyst_id"

    def test_load_system_prompt(self):
        from backend.agents.analyst import AnalystAgent

        mock_toolkit = MagicMock()
        mock_model = MagicMock()
        mock_formatter = MagicMock()

        agent = AnalystAgent(
            analyst_type="sentiment_analyst",
            toolkit=mock_toolkit,
            model=mock_model,
            formatter=mock_formatter,
        )

        prompt = agent._load_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert "Compact Analyst Output" not in prompt

    def test_compact_analyst_output_prompt_enabled(self):
        from backend.agents.analyst import AnalystAgent

        mock_toolkit = MagicMock()
        mock_model = MagicMock()
        mock_formatter = MagicMock()

        with patch.dict("os.environ", {"ANALYST_OUTPUT_MODE": "compact"}):
            agent = AnalystAgent(
                analyst_type="technical_analyst",
                toolkit=mock_toolkit,
                model=mock_model,
                formatter=mock_formatter,
            )
            prompt = agent._load_system_prompt()

        assert "Compact Analyst Output" in prompt
        assert "one signal line per ticker" in prompt
        assert "Do not write long investment philosophy" in prompt

    def test_fundamental_toolkit_includes_a_share_data_tools(self):
        from backend.main import create_toolkit

        toolkit = create_toolkit("fundamentals_analyst")
        tool_names = sorted(toolkit.tools.keys())

        assert "extract_entities_code" in tool_names
        assert "crawl_ths_finance" in tool_names

    def test_technical_toolkit_includes_history_tool(self):
        from backend.main import create_toolkit

        toolkit = create_toolkit("technical_analyst")
        tool_names = sorted(toolkit.tools.keys())

        assert "extract_entities_code" in tool_names
        assert "history_calculate" in tool_names
        assert "execute_code" in tool_names

    def test_sentiment_toolkit_includes_macro_tools(self):
        from backend.main import create_toolkit

        toolkit = create_toolkit("sentiment_analyst")
        tool_names = sorted(toolkit.tools.keys())

        assert "extract_entities_code" in tool_names
        assert "crawl_ths_news" in tool_names
        assert "crawl_ths_concept" in tool_names

    def test_risk_manager_toolkit_includes_risk_tools(self):
        from backend.agents.risk_manager import RiskAgent

        mock_model = MagicMock()
        mock_formatter = MagicMock()

        agent = RiskAgent(
            model=mock_model,
            formatter=mock_formatter,
        )
        tool_names = sorted(agent.toolkit.tools.keys())

        assert "crawl_ths_holder" in tool_names
        assert "crawl_ths_position" in tool_names
        assert "crawl_ths_event" in tool_names


class TestPMAgent:
    def test_parse_tickers_from_content_supports_a_share_suffixes(self):
        from backend.agents.portfolio_manager import PMAgent

        tickers = PMAgent._parse_tickers_from_content(
            "Use _make_decision tool for each ticker: 600519.SH, 000001.SZ. "
            "Call it once per ticker with explicit arguments.",
        )

        assert tickers == ["600519.SH", "000001.SZ"]
        assert "SH" not in tickers
        assert "SZ" not in tickers

    def test_init_default(self):
        from backend.agents.portfolio_manager import PMAgent

        mock_model = MagicMock()
        mock_formatter = MagicMock()

        agent = PMAgent(
            model=mock_model,
            formatter=mock_formatter,
        )

        assert agent.name == "portfolio_manager"
        assert agent.portfolio["cash"] == 100000.0
        assert agent.portfolio["positions"] == {}
        assert agent.portfolio["margin_requirement"] == 0.25

    def test_init_custom_cash(self):
        from backend.agents.portfolio_manager import PMAgent

        mock_model = MagicMock()
        mock_formatter = MagicMock()

        agent = PMAgent(
            model=mock_model,
            formatter=mock_formatter,
            initial_cash=50000.0,
            margin_requirement=0.5,
        )

        assert agent.portfolio["cash"] == 50000.0
        assert agent.portfolio["margin_requirement"] == 0.5

    def test_get_portfolio_state(self):
        from backend.agents.portfolio_manager import PMAgent

        mock_model = MagicMock()
        mock_formatter = MagicMock()

        agent = PMAgent(
            model=mock_model,
            formatter=mock_formatter,
            initial_cash=75000.0,
        )

        state = agent.get_portfolio_state()

        assert state["cash"] == 75000.0
        assert state is not agent.portfolio  # Should be a copy

    def test_load_portfolio_state(self):
        from backend.agents.portfolio_manager import PMAgent

        mock_model = MagicMock()
        mock_formatter = MagicMock()

        agent = PMAgent(
            model=mock_model,
            formatter=mock_formatter,
        )

        new_portfolio = {
            "cash": 50000.0,
            "positions": {
                "AAPL": {"long": 100, "short": 0, "long_cost_basis": 150.0},
            },
            "margin_used": 1000.0,
        }

        agent.load_portfolio_state(new_portfolio)

        assert agent.portfolio["cash"] == 50000.0
        assert "AAPL" in agent.portfolio["positions"]

    def test_update_portfolio(self):
        from backend.agents.portfolio_manager import PMAgent

        mock_model = MagicMock()
        mock_formatter = MagicMock()

        agent = PMAgent(
            model=mock_model,
            formatter=mock_formatter,
        )

        agent.update_portfolio({"cash": 80000.0})
        assert agent.portfolio["cash"] == 80000.0

    def _get_text_from_tool_response(self, result):
        """Helper to extract text from ToolResponse content"""
        content = result.content[0]
        if hasattr(content, "text"):
            return content.text
        elif isinstance(content, dict):
            return content.get("text", "")
        return str(content)

    def test_make_decision_long(self):
        from backend.agents.portfolio_manager import PMAgent

        mock_model = MagicMock()
        mock_formatter = MagicMock()

        agent = PMAgent(
            model=mock_model,
            formatter=mock_formatter,
        )

        result = agent._make_decision(
            ticker="AAPL",
            action="long",
            quantity=100,
            confidence=80,
            reasoning="Strong fundamentals",
        )

        text = self._get_text_from_tool_response(result)
        assert "✓ Recorded" in text
        assert agent._decisions["AAPL"]["action"] == "long"
        assert agent._decisions["AAPL"]["quantity"] == 100

    def test_make_decision_small_probe_caps_long_quantity(self):
        from backend.agents.portfolio_manager import PMAgent

        mock_model = MagicMock()
        mock_formatter = MagicMock()

        with patch.dict(
            "os.environ",
            {
                "PM_EXPERIMENT_MODE": "small_probe",
                "PM_PROBE_MAX_POSITION_PCT": "10",
                "PM_MIN_CASH_PCT": "60",
                "PM_MAX_SINGLE_POSITION_PCT": "30",
            },
        ):
            agent = PMAgent(
                model=mock_model,
                formatter=mock_formatter,
                initial_cash=500000.0,
            )
            agent._current_prices = {"000001.SZ": 10.0}
            agent._pending_tickers = ["000001.SZ"]

            result = agent._make_decision(
                ticker="000001.SZ",
                action="long",
                quantity=10000,
                confidence=80,
                reasoning="Small probe experiment",
            )

        text = self._get_text_from_tool_response(result)
        assert "PM小仓试探上限截断" in text
        assert agent._decisions["000001.SZ"]["action"] == "long"
        assert agent._decisions["000001.SZ"]["quantity"] == 5000

    def test_make_decision_lot_aware_probe_allows_one_high_price_lot(self):
        from backend.agents.portfolio_manager import PMAgent

        mock_model = MagicMock()
        mock_formatter = MagicMock()

        with patch.dict(
            "os.environ",
            {
                "PM_EXPERIMENT_MODE": "lot_aware_probe",
                "PM_LOT_AWARE_NORMAL_MAX_PCT": "20",
                "PM_LOT_AWARE_HIGH_PRICE_THRESHOLD_PCT": "20",
                "PM_MIN_CASH_PCT": "60",
                "PM_MAX_SINGLE_POSITION_PCT": "30",
            },
        ):
            agent = PMAgent(
                model=mock_model,
                formatter=mock_formatter,
                initial_cash=500000.0,
            )
            agent._current_prices = {"600519.SH": 1500.0}
            agent._pending_tickers = ["600519.SH"]

            result = agent._make_decision(
                ticker="600519.SH",
                action="long",
                quantity=100,
                confidence=80,
                reasoning="High-quality lot-aware probe",
            )

        text = self._get_text_from_tool_response(result)
        assert "PM手数感知试探" in text
        assert agent._decisions["600519.SH"]["action"] == "long"
        assert agent._decisions["600519.SH"]["quantity"] == 100

    def test_make_decision_lot_aware_probe_blocks_lot_above_single_cap(self):
        from backend.agents.portfolio_manager import PMAgent

        mock_model = MagicMock()
        mock_formatter = MagicMock()

        with patch.dict(
            "os.environ",
            {
                "PM_EXPERIMENT_MODE": "lot_aware_probe",
                "PM_LOT_AWARE_NORMAL_MAX_PCT": "20",
                "PM_LOT_AWARE_HIGH_PRICE_THRESHOLD_PCT": "20",
                "PM_MIN_CASH_PCT": "60",
                "PM_MAX_SINGLE_POSITION_PCT": "30",
            },
        ):
            agent = PMAgent(
                model=mock_model,
                formatter=mock_formatter,
                initial_cash=500000.0,
            )
            agent._current_prices = {"600519.SH": 1700.0}
            agent._pending_tickers = ["600519.SH"]

            result = agent._make_decision(
                ticker="600519.SH",
                action="long",
                quantity=100,
                confidence=80,
                reasoning="High-quality lot-aware probe",
            )

        text = self._get_text_from_tool_response(result)
        assert "PM手数感知上限截断" in text
        assert "不足1手" in text
        assert agent._decisions["600519.SH"]["action"] == "hold"
        assert agent._decisions["600519.SH"]["quantity"] == 0

    def test_regime_target_exposure_prompt_enabled(self):
        from backend.agents.portfolio_manager import PMAgent

        mock_model = MagicMock()
        mock_formatter = MagicMock()

        with patch.dict(
            "os.environ",
            {
                "PM_EXPERIMENT_MODE": "regime_target_exposure",
                "PM_REGIME_WEAK_EQUITY_LOW_PCT": "40",
                "PM_REGIME_WEAK_EQUITY_HIGH_PCT": "60",
                "PM_REGIME_SIDEWAYS_EQUITY_LOW_PCT": "65",
                "PM_REGIME_SIDEWAYS_EQUITY_HIGH_PCT": "75",
                "PM_REGIME_REBOUND_EQUITY_LOW_PCT": "80",
                "PM_REGIME_REBOUND_EQUITY_HIGH_PCT": "90",
            },
        ):
            agent = PMAgent(model=mock_model, formatter=mock_formatter)

        assert "Market-Regime Target Exposure" in agent.sys_prompt
        assert "WEAK: target equity exposure 40%-60%" in agent.sys_prompt
        assert "REBOUND: target equity exposure 80%-90%" in agent.sys_prompt
        assert "Deployment Discipline" in agent.sys_prompt
        assert "target_gap_value" in agent.sys_prompt
        assert "close at least 50%" in agent.sys_prompt
        assert "2-4 viable candidates" in agent.sys_prompt
        assert "reallocate that unused budget" in agent.sys_prompt
        assert "Generic caution is not enough" in agent.sys_prompt

    def test_make_decision_hold(self):
        from backend.agents.portfolio_manager import PMAgent

        mock_model = MagicMock()
        mock_formatter = MagicMock()

        agent = PMAgent(
            model=mock_model,
            formatter=mock_formatter,
        )

        result = agent._make_decision(
            ticker="GOOGL",
            action="hold",
            quantity=0,
            confidence=50,
            reasoning="Neutral outlook",
        )

        text = self._get_text_from_tool_response(result)
        assert "✓ Recorded" in text
        assert agent._decisions["GOOGL"]["action"] == "hold"
        assert agent._decisions["GOOGL"]["quantity"] == 0

    def test_make_decision_invalid_action(self):
        from backend.agents.portfolio_manager import PMAgent

        mock_model = MagicMock()
        mock_formatter = MagicMock()

        agent = PMAgent(
            model=mock_model,
            formatter=mock_formatter,
        )

        result = agent._make_decision(
            ticker="AAPL",
            action="invalid",
            quantity=10,
        )

        text = self._get_text_from_tool_response(result)
        assert "Invalid action" in text

    def test_get_decisions(self):
        from backend.agents.portfolio_manager import PMAgent

        mock_model = MagicMock()
        mock_formatter = MagicMock()

        agent = PMAgent(
            model=mock_model,
            formatter=mock_formatter,
        )

        agent._make_decision("AAPL", "long", 100)
        agent._make_decision("GOOGL", "short", 50)

        decisions = agent.get_decisions()
        assert len(decisions) == 2
        assert decisions["AAPL"]["action"] == "long"
        assert decisions["GOOGL"]["action"] == "short"


class TestRiskAgent:
    def test_init_default(self):
        from backend.agents.risk_manager import RiskAgent

        mock_model = MagicMock()
        mock_formatter = MagicMock()

        agent = RiskAgent(
            model=mock_model,
            formatter=mock_formatter,
        )

        assert agent.name == "risk_manager"

    def test_init_custom_name(self):
        from backend.agents.risk_manager import RiskAgent

        mock_model = MagicMock()
        mock_formatter = MagicMock()

        agent = RiskAgent(
            model=mock_model,
            formatter=mock_formatter,
            name="custom_risk_manager",
        )

        assert agent.name == "custom_risk_manager"

    def test_load_system_prompt(self):
        from backend.agents.risk_manager import RiskAgent

        mock_model = MagicMock()
        mock_formatter = MagicMock()

        agent = RiskAgent(
            model=mock_model,
            formatter=mock_formatter,
        )

        prompt = agent._load_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0


class TestStorageService:
    def test_calculate_portfolio_value_cash_only(self):
        from backend.services.storage import StorageService

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = StorageService(
                dashboard_dir=Path(tmpdir),
                initial_cash=100000.0,
            )

            portfolio = {"cash": 100000.0, "positions": {}, "margin_used": 0.0}
            prices = {}

            value = storage.calculate_portfolio_value(portfolio, prices)
            assert value == 100000.0

    def test_calculate_portfolio_value_with_positions(self):
        from backend.services.storage import StorageService

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = StorageService(
                dashboard_dir=Path(tmpdir),
                initial_cash=100000.0,
            )

            portfolio = {
                "cash": 50000.0,
                "positions": {
                    "AAPL": {"long": 100, "short": 0},
                    "GOOGL": {"long": 0, "short": 10},
                },
                "margin_used": 5000.0,
            }
            prices = {"AAPL": 150.0, "GOOGL": 100.0}

            value = storage.calculate_portfolio_value(portfolio, prices)
            assert value == 69000.0

    def test_update_dashboard_after_cycle(self):
        from backend.services.storage import StorageService

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = StorageService(
                dashboard_dir=Path(tmpdir),
                initial_cash=100000.0,
            )

            portfolio = {
                "cash": 90000.0,
                "positions": {"AAPL": {"long": 50, "short": 0}},
                "margin_used": 0.0,
            }
            prices = {"AAPL": 200.0}

            storage.update_dashboard_after_cycle(
                portfolio=portfolio,
                prices=prices,
                date="2024-01-15",
                executed_trades=[
                    {
                        "ticker": "AAPL",
                        "action": "long",
                        "quantity": 50,
                        "price": 200.0,
                    },
                ],
            )

            summary = storage.load_file("summary")
            assert summary is not None
            assert summary["totalAssetValue"] == 100000.0  # 90000 + 50*200

            holdings = storage.load_file("holdings")
            assert holdings is not None
            assert len(holdings) > 0

            trades = storage.load_file("trades")
            assert trades is not None
            assert len(trades) == 1
            assert trades[0]["ticker"] == "AAPL"
            assert trades[0]["qty"] == 50
            assert trades[0]["price"] == 200.0

    def test_generate_summary(self):
        from backend.services.storage import StorageService

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = StorageService(
                dashboard_dir=Path(tmpdir),
                initial_cash=100000.0,
            )

            state = {
                "portfolio_state": {
                    "cash": 50000.0,
                    "positions": {"AAPL": {"long": 100, "short": 0}},
                    "margin_used": 0.0,
                },
                "equity_history": [{"t": 1000, "v": 100000}],
                "all_trades": [],
            }
            prices = {"AAPL": 500.0}

            storage._generate_summary(state, 100000.0, prices)

            summary = storage.load_file("summary")
            assert summary["totalAssetValue"] == 100000.0
            assert summary["totalReturn"] == 0.0

    def test_generate_holdings(self):
        from backend.services.storage import StorageService

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = StorageService(
                dashboard_dir=Path(tmpdir),
                initial_cash=100000.0,
            )

            state = {
                "portfolio_state": {
                    "cash": 50000.0,
                    "positions": {"AAPL": {"long": 100, "short": 0}},
                    "margin_used": 0.0,
                },
            }
            prices = {"AAPL": 500.0}

            storage._generate_holdings(state, prices)

            holdings = storage.load_file("holdings")
            assert len(holdings) == 2  # AAPL + CASH

            aapl_holding = next(
                (h for h in holdings if h["ticker"] == "AAPL"),
                None,
            )
            assert aapl_holding is not None
            assert aapl_holding["quantity"] == 100
            assert aapl_holding["currentPrice"] == 500.0

    def test_generate_stats_with_quant_metrics(self):
        from backend.services.storage import StorageService

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = StorageService(
                dashboard_dir=Path(tmpdir),
                initial_cash=100000.0,
            )

            state = {
                "portfolio_state": {
                    "cash": 40000.0,
                    "positions": {"AAPL": {"long": 100, "short": 0}},
                    "margin_used": 0.0,
                },
                "last_prices": {"AAPL": 700.0},
                "equity_history": [
                    {"t": 1704067200000, "v": 100000.0},
                    {"t": 1704153600000, "v": 102000.0},
                    {"t": 1704240000000, "v": 105000.0},
                    {"t": 1704326400000, "v": 103000.0},
                    {"t": 1704412800000, "v": 110000.0},
                ],
                "baseline_history": [
                    {"t": 1704067200000, "v": 100000.0},
                    {"t": 1704153600000, "v": 101000.0},
                    {"t": 1704240000000, "v": 102000.0},
                    {"t": 1704326400000, "v": 101500.0},
                    {"t": 1704412800000, "v": 104000.0},
                ],
                "baseline_vw_history": [
                    {"t": 1704067200000, "v": 100000.0},
                    {"t": 1704153600000, "v": 100800.0},
                    {"t": 1704240000000, "v": 101500.0},
                    {"t": 1704326400000, "v": 101000.0},
                    {"t": 1704412800000, "v": 103500.0},
                ],
                "momentum_history": [
                    {"t": 1704067200000, "v": 100000.0},
                    {"t": 1704153600000, "v": 101500.0},
                    {"t": 1704240000000, "v": 104000.0},
                    {"t": 1704326400000, "v": 103200.0},
                    {"t": 1704412800000, "v": 107000.0},
                ],
                "all_trades": [{"id": "t1"}, {"id": "t2"}],
            }

            storage._generate_stats(state, 110000.0)
            stats = storage.load_file("stats")

            assert stats["tickerWeights"]["AAPL"] == 0.6364
            assert stats["period"]["tradingDays"] == 4
            assert stats["performance"]["agent"]["totalReturnPct"] == 10.0
            assert stats["performance"]["agent"]["maxDrawdownPct"] < 0
            assert (
                stats["performance"]["comparison"]["equalWeight"][
                    "excessReturnPct"
                ]
                == 6.0
            )

    def test_generate_backtest_report_html(self):
        script_path = (
            Path(__file__).resolve().parents[2]
            / "scripts"
            / "generate_backtest_report.py"
        )
        spec = spec_from_file_location(
            "generate_backtest_report",
            script_path,
        )
        report_module = module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(report_module)

        with tempfile.TemporaryDirectory() as tmpdir:
            dashboard_dir = Path(tmpdir)
            (dashboard_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "totalAssetValue": 110000.0,
                        "totalReturn": 10.0,
                        "equity": [
                            {"t": 1, "v": 100000.0},
                            {"t": 2, "v": 110000.0},
                        ],
                        "baseline": [
                            {"t": 1, "v": 100000.0},
                            {"t": 2, "v": 104000.0},
                        ],
                        "baseline_vw": [
                            {"t": 1, "v": 100000.0},
                            {"t": 2, "v": 103500.0},
                        ],
                        "momentum": [
                            {"t": 1, "v": 100000.0},
                            {"t": 2, "v": 107000.0},
                        ],
                    },
                ),
                encoding="utf-8",
            )
            (dashboard_dir / "stats.json").write_text(
                json.dumps(
                    {
                        "totalAssetValue": 110000.0,
                        "totalReturn": 10.0,
                        "totalTrades": 2,
                        "tickerWeights": {"AAPL": 0.6364},
                        "period": {
                            "startDate": "2024-01-02",
                            "endDate": "2024-01-05",
                            "tradingDays": 4,
                        },
                        "performance": {
                            "agent": {
                                "annualizedReturnPct": 24.5,
                                "sharpe": 1.234,
                                "maxDrawdownPct": -1.9,
                                "calmar": 2.5,
                                "totalReturnPct": 10.0,
                                "volatilityPct": 12.1,
                            },
                            "benchmarks": {
                                "equalWeight": {"totalReturnPct": 4.0},
                                "marketCapWeighted": {"totalReturnPct": 3.5},
                                "momentum": {"totalReturnPct": 7.0},
                            },
                            "comparison": {
                                "equalWeight": {"excessReturnPct": 6.0},
                                "marketCapWeighted": {
                                    "excessReturnPct": 6.5
                                },
                                "momentum": {"excessReturnPct": 3.0},
                            },
                        },
                    },
                ),
                encoding="utf-8",
            )
            (dashboard_dir / "trades.json").write_text(
                json.dumps(
                    [
                        {
                            "trading_date": "2024-01-05",
                            "ticker": "AAPL",
                            "side": "LONG",
                            "qty": 10,
                            "price": 150.0,
                        },
                    ],
                ),
                encoding="utf-8",
            )
            (dashboard_dir / "leaderboard.json").write_text(
                json.dumps(
                    [
                        {
                            "rank": 1,
                            "name": "Technical Analyst",
                            "weightedScore": 0.88,
                            "scoreBreakdown": {
                                "winRate": 0.7,
                                "rm": 0.8,
                                "grounding": 0.9,
                                "audit": 0.85,
                                "presentation": 0.95,
                            },
                        },
                    ],
                ),
                encoding="utf-8",
            )

            html = report_module.build_report_html(
                dashboard_dir=dashboard_dir,
                summary=json.loads(
                    (dashboard_dir / "summary.json").read_text(
                        encoding="utf-8",
                    ),
                ),
                stats=json.loads(
                    (dashboard_dir / "stats.json").read_text(
                        encoding="utf-8",
                    ),
                ),
                trades=json.loads(
                    (dashboard_dir / "trades.json").read_text(
                        encoding="utf-8",
                    ),
                ),
                leaderboard=json.loads(
                    (dashboard_dir / "leaderboard.json").read_text(
                        encoding="utf-8",
                    ),
                ),
            )

            assert "Backtest Report" in html
            assert "Technical Analyst" in html
            assert "AAPL" in html


class TestTradeExecutor:
    def test_execute_trade_long(self):
        from backend.utils.trade_executor import PortfolioTradeExecutor

        executor = PortfolioTradeExecutor(
            initial_portfolio={
                "cash": 100000.0,
                "positions": {},
                "margin_requirement": 0.25,
                "margin_used": 0.0,
            },
        )

        result = executor.execute_trade(
            ticker="AAPL",
            action="long",
            quantity=10,
            price=150.0,
        )

        assert result["status"] == "success"
        assert executor.portfolio["positions"]["AAPL"]["long"] == 10
        assert executor.portfolio["cash"] == 98500.0  # 100000 - 10*150

    def test_execute_trade_short(self):
        from backend.utils.trade_executor import PortfolioTradeExecutor

        executor = PortfolioTradeExecutor(
            initial_portfolio={
                "cash": 100000.0,
                "positions": {
                    "AAPL": {
                        "long": 50,
                        "short": 0,
                        "long_cost_basis": 100.0,
                        "short_cost_basis": 0.0,
                    },
                },
                "margin_requirement": 0.25,
                "margin_used": 0.0,
            },
        )

        result = executor.execute_trade(
            ticker="AAPL",
            action="short",
            quantity=30,
            price=150.0,
        )

        assert result["status"] == "success"
        assert executor.portfolio["positions"]["AAPL"]["long"] == 20  # 50 - 30

    def test_execute_trade_hold(self):
        from backend.utils.trade_executor import PortfolioTradeExecutor

        executor = PortfolioTradeExecutor()

        result = executor.execute_trade(
            ticker="AAPL",
            action="hold",
            quantity=0,
            price=150.0,
        )

        assert result["status"] == "success"
        assert result["message"] == "No trade needed"


class TestPipelineExecution:
    def test_execute_decisions(self):
        from backend.core.pipeline import TradingPipeline
        from backend.agents.portfolio_manager import PMAgent

        mock_model = MagicMock()
        mock_formatter = MagicMock()

        pm = PMAgent(
            model=mock_model,
            formatter=mock_formatter,
            initial_cash=100000.0,
        )

        pipeline = TradingPipeline(
            analysts=[],
            risk_manager=MagicMock(),
            portfolio_manager=pm,
            max_comm_cycles=0,
        )

        decisions = {
            "AAPL": {"action": "long", "quantity": 10},
            "GOOGL": {"action": "short", "quantity": 5},
        }
        prices = {"AAPL": 150.0, "GOOGL": 100.0}

        result = pipeline._execute_decisions(decisions, prices, "2024-01-15")

        assert len(result["executed_trades"]) == 2
        assert result["executed_trades"][0]["ticker"] == "AAPL"
        assert result["executed_trades"][0]["quantity"] == 10
        assert pm.portfolio["positions"]["AAPL"]["long"] == 10

    def test_a_share_pipeline_uses_constraint_executor(self):
        from backend.core.pipeline import TradingPipeline
        from backend.agents.portfolio_manager import PMAgent
        from backend.utils.a_share_constraints import ASharePortfolioTradeExecutor

        mock_model = MagicMock()
        mock_formatter = MagicMock()

        pm = PMAgent(
            model=mock_model,
            formatter=mock_formatter,
            initial_cash=1000000.0,
        )

        pipeline = TradingPipeline(
            analysts=[],
            risk_manager=MagicMock(),
            portfolio_manager=pm,
            max_comm_cycles=0,
        )

        result = pipeline._execute_decisions(
            decisions={"600519.SH": {"action": "long", "quantity": 100}},
            prices={"600519.SH": 1050.0},
            date="2024-01-15",
            prev_closes={"600519.SH": 1000.0},
        )

        assert isinstance(pipeline._executor, ASharePortfolioTradeExecutor)
        assert len(result["executed_trades"]) == 1
        assert pm.portfolio["positions"]["600519.SH"]["long"] == 100

    def test_a_share_pipeline_blocks_limit_up_buy(self):
        from backend.core.pipeline import TradingPipeline
        from backend.agents.portfolio_manager import PMAgent

        mock_model = MagicMock()
        mock_formatter = MagicMock()

        pm = PMAgent(
            model=mock_model,
            formatter=mock_formatter,
            initial_cash=1000000.0,
        )

        pipeline = TradingPipeline(
            analysts=[],
            risk_manager=MagicMock(),
            portfolio_manager=pm,
            max_comm_cycles=0,
        )

        result = pipeline._execute_decisions(
            decisions={"600519.SH": {"action": "long", "quantity": 100}},
            prices={"600519.SH": 1100.0},
            date="2024-01-15",
            prev_closes={"600519.SH": 1000.0},
        )

        assert len(result["executed_trades"]) == 0
        assert pm.portfolio["positions"]["600519.SH"]["long"] == 0


class TestMsgContentIsString:
    def test_msg_content_string(self):
        msg = Msg(name="test", content="simple string", role="user")
        assert isinstance(msg.content, str)

    def test_msg_content_json_string(self):
        data = {"key": "value", "nested": {"a": 1}}
        msg = Msg(name="test", content=json.dumps(data), role="user")
        assert isinstance(msg.content, str)

        parsed = json.loads(msg.content)
        assert parsed["key"] == "value"

    def test_msg_content_should_not_be_dict(self):
        data = {"key": "value"}
        msg = Msg(name="test", content=json.dumps(data), role="assistant")

        assert not isinstance(msg.content, dict)
        assert isinstance(msg.content, str)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
