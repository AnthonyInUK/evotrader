# -*- coding: utf-8 -*-
from unittest.mock import patch
import json

from backend.data.schema import Price
from backend.tools.analysis_tools import (
    _code_repair_cache_key,
    crawl_ths_concept,
    crawl_ths_event,
    crawl_ths_news,
    crawl_ths_position,
    execute_code,
    history_calculate,
    run_indicator,
)


def _text(result) -> str:
    content = result.content[0]
    if hasattr(content, "text"):
        return content.text
    if isinstance(content, dict):
        return content.get("text", "")
    return str(content)


def _sample_prices():
    return [
        Price(open=10 + i, close=10.5 + i, high=11 + i, low=9.5 + i, volume=1000 + i, time=f"2024-03-{i+1:02d}")
        for i in range(1, 25)
    ]


class TestTechnicalAshareTools:
    @patch("backend.tools.analysis_tools.get_prices")
    def test_history_calculate_accepts_yyyymmdd(self, mock_get_prices):
        mock_get_prices.return_value = _sample_prices()

        result = history_calculate(["300750.SZ"], "20240329")
        text = _text(result)

        assert "Historical Indicator Snapshot (2024-03-29)" in text
        assert "300750.SZ:" in text
        assert "MACD" in text

    @patch("backend.tools.analysis_tools.get_prices")
    def test_execute_code_runs_custom_indicator_logic(self, mock_get_prices):
        mock_get_prices.return_value = _sample_prices()
        code = """
df["SMA3"] = df["close"].rolling(3).mean()
result = f"latest_sma3={df['SMA3'].iloc[-1]:.2f}"
"""

        with patch.dict(
            "os.environ",
            {
                "ENABLE_CODE_REPAIR_CACHE": "0",
                "ENABLE_EXPERIMENT_CODE_CACHE": "0",
                "ENABLE_LLM_CODE_REPAIR": "0",
            },
        ):
            result = execute_code(
                tickers=["300750.SZ"],
                current_date="20240329",
                code=code,
                lookback_days=30,
            )
        text = _text(result)

        assert "execute_code (300750.SZ, 2024-03-29)" in text
        assert "latest_sma3=" in text

    @patch("backend.tools.analysis_tools.get_prices")
    def test_execute_code_captures_print_output(self, mock_get_prices):
        mock_get_prices.return_value = _sample_prices()

        result = execute_code(
            tickers=["300750.SZ"],
            current_date="20240329",
            code='print("latest_close", round(df["close"].iloc[-1], 2))',
            lookback_days=30,
        )
        text = _text(result)

        assert "execute_code (300750.SZ, 2024-03-29)" in text
        assert "latest_close" in text

    @patch("backend.tools.analysis_tools.get_prices")
    def test_execute_code_supports_multi_ticker_dataframe(self, mock_get_prices):
        mock_get_prices.return_value = _sample_prices()
        code = """
result = {}
for ticker in df.columns.levels[0]:
    close = df[ticker]["close"].dropna()
    result[ticker] = round(close.iloc[-1], 2)
"""

        result = execute_code(
            tickers=["600519.SH", "601398.SH"],
            current_date="20240329",
            code=code,
            lookback_days=30,
        )
        text = _text(result)

        assert "execute_code (600519.SH, 2024-03-29)" in text
        assert "600519.SH" in text
        assert "601398.SH" in text

    @patch("backend.tools.analysis_tools.get_prices")
    def test_execute_code_allows_common_safe_builtins(self, mock_get_prices):
        mock_get_prices.return_value = _sample_prices()
        code = """
result = dict(
    (ticker, idx)
    for idx, ticker in enumerate(sorted(tickers))
)
"""

        result = execute_code(
            tickers=["601398.SH", "600519.SH"],
            current_date="20240329",
            code=code,
            lookback_days=30,
        )
        text = _text(result)

        assert "600519.SH" in text
        assert "601398.SH" in text

    @patch("backend.tools.analysis_tools.get_prices")
    def test_execute_code_exposes_common_indicator_columns(self, mock_get_prices):
        mock_get_prices.return_value = _sample_prices()
        code = """
result = {
    "sma20": round(df["SMA20"].iloc[-1], 2),
    "macd": round(df["MACD"].iloc[-1], 4),
    "rsi": round(df["RSI14"].iloc[-1], 2),
}
"""

        result = execute_code(
            tickers=["300750.SZ"],
            current_date="20240329",
            code=code,
            lookback_days=30,
        )
        text = _text(result)

        assert "sma20" in text
        assert "macd" in text
        assert "rsi" in text

    @patch("backend.tools.analysis_tools.get_prices")
    def test_execute_code_exposes_date_column(self, mock_get_prices):
        mock_get_prices.return_value = _sample_prices()
        code = """
ordered = df.sort_values("date")
result = ordered["date"].iloc[-1]
"""

        result = execute_code(
            tickers=["300750.SZ"],
            current_date="20240329",
            code=code,
            lookback_days=30,
        )
        text = _text(result)

        assert "execute_code (300750.SZ, 2024-03-29)" in text
        assert "2024-03-" in text

    @patch("backend.tools.analysis_tools.get_prices")
    def test_execute_code_exposes_common_ohlcv_aliases(self, mock_get_prices):
        mock_get_prices.return_value = _sample_prices()
        code = """
d = df.copy()
d["range_pct"] = (d["High"] - d["Low"]) / d["Close"] * 100
result = round(d["range_pct"].iloc[-1], 2)
"""

        result = execute_code(
            tickers=["300750.SZ"],
            current_date="20240329",
            code=code,
            lookback_days=30,
        )
        text = _text(result)

        assert "execute_code (300750.SZ, 2024-03-29)" in text
        assert "4.35" in text

    @patch("backend.tools.analysis_tools.get_prices")
    def test_execute_code_comprehension_can_see_prior_variables(self, mock_get_prices):
        mock_get_prices.return_value = _sample_prices()
        code = """
recent_20 = df.tail(20)
result = sum(x > recent_20["close"].mean() for x in recent_20["close"])
"""

        result = execute_code(
            tickers=["300750.SZ"],
            current_date="20240329",
            code=code,
            lookback_days=30,
        )
        text = _text(result)

        assert "execute_code (300750.SZ, 2024-03-29)" in text
        assert "[ERROR]" not in text

    @patch("backend.tools.analysis_tools.get_prices")
    def test_execute_code_self_repairs_case_variant_column(self, mock_get_prices):
        mock_get_prices.return_value = _sample_prices()
        code = """
result = round(df["CLOSE"].iloc[-1], 2)
"""

        result = execute_code(
            tickers=["300750.SZ"],
            current_date="20240329",
            code=code,
            lookback_days=30,
        )
        text = _text(result)

        assert "execute_code (300750.SZ, 2024-03-29)" in text
        assert "34.5" in text
        assert "CODE_ERROR" not in text

    @patch("backend.tools.analysis_tools.get_prices")
    def test_execute_code_returns_structured_code_error(self, mock_get_prices):
        mock_get_prices.return_value = _sample_prices()
        code = """
result = totally_missing_variable + 1
"""

        result = execute_code(
            tickers=["300750.SZ"],
            current_date="20240329",
            code=code,
            lookback_days=30,
        )
        text = _text(result)

        assert "CODE_ERROR" in text
        assert "Available columns:" in text
        assert "Recent data preview:" in text

    @patch("backend.tools.analysis_tools.get_prices")
    @patch("backend.tools.analysis_tools._repair_code_with_llm")
    def test_execute_code_can_use_llm_repair_once(
        self,
        mock_repair_code,
        mock_get_prices,
    ):
        mock_get_prices.return_value = _sample_prices()
        mock_repair_code.return_value = 'result = round(df["close"].iloc[-1], 2)'
        code = """
result = totally_missing_variable + 1
"""

        with patch.dict(
            "os.environ",
            {
                "ENABLE_LLM_CODE_REPAIR": "1",
                "MAX_CODE_REPAIR_ATTEMPTS": "1",
                "ENABLE_CODE_REPAIR_CACHE": "0",
                "ENABLE_EXPERIMENT_CODE_CACHE": "0",
            },
        ):
            result = execute_code(
                tickers=["300750.SZ"],
                current_date="20240329",
                code=code,
                lookback_days=30,
            )
        text = _text(result)

        assert "execute_code (300750.SZ, 2024-03-29)" in text
        assert "34.5" in text
        assert "CODE_ERROR" not in text
        mock_repair_code.assert_called_once()

    @patch("backend.tools.analysis_tools.get_prices")
    @patch("backend.tools.analysis_tools._repair_code_with_llm")
    def test_execute_code_uses_cached_repair_before_original_code(
        self,
        mock_repair_code,
        mock_get_prices,
        tmp_path,
    ):
        prices = _sample_prices()
        mock_get_prices.return_value = prices
        code = """
result = totally_missing_variable + 1
"""
        from backend.tools.analysis_tools import _add_basic_technical_indicators
        from backend.tools.data_tools import prices_to_df

        df = _add_basic_technical_indicators(prices_to_df(prices))
        repaired_code = 'result = round(df["close"].iloc[-1], 2)'
        cache_path = tmp_path / "execute_code_repairs.jsonl"
        cache_path.write_text(
            json.dumps(
                {
                    "cache_key": _code_repair_cache_key(code, df),
                    "success": True,
                    "original_code": code.strip(),
                    "repaired_code": repaired_code,
                }
            )
            + "\n",
            encoding="utf-8",
        )

        with patch.dict(
            "os.environ",
            {
                "ENABLE_CODE_REPAIR_CACHE": "1",
                "CODE_REPAIR_CACHE_PATH": str(cache_path),
                "ENABLE_LLM_CODE_REPAIR": "1",
                "MAX_CODE_REPAIR_ATTEMPTS": "1",
            },
        ):
            result = execute_code(
                tickers=["300750.SZ"],
                current_date="20240329",
                code=code,
                lookback_days=30,
            )
        text = _text(result)

        assert "34.5" in text
        assert "CODE_ERROR" not in text
        mock_repair_code.assert_not_called()

    @patch("backend.tools.analysis_tools.get_prices")
    @patch("backend.tools.analysis_tools._repair_code_with_llm")
    def test_execute_code_prefers_experiment_validated_code(
        self,
        mock_repair_code,
        mock_get_prices,
        tmp_path,
    ):
        prices = _sample_prices()
        mock_get_prices.return_value = prices
        code = """
result = totally_missing_variable + 1
"""
        from backend.tools.analysis_tools import _add_basic_technical_indicators
        from backend.tools.data_tools import prices_to_df

        df = _add_basic_technical_indicators(prices_to_df(prices))
        experiment_path = tmp_path / "experiment_code" / "execute_code_validated.jsonl"
        experiment_path.parent.mkdir()
        experiment_path.write_text(
            json.dumps(
                {
                    "cache_key": _code_repair_cache_key(code, df),
                    "success": True,
                    "original_code": code.strip(),
                    "repaired_code": 'result = "experiment_validated"',
                }
            )
            + "\n",
            encoding="utf-8",
        )

        with patch.dict(
            "os.environ",
            {
                "ENABLE_EXPERIMENT_CODE_CACHE": "1",
                "EXPERIMENT_CODE_PATH": str(experiment_path),
                "ENABLE_CODE_REPAIR_CACHE": "0",
                "ENABLE_LLM_CODE_REPAIR": "1",
                "MAX_CODE_REPAIR_ATTEMPTS": "1",
            },
        ):
            result = execute_code(
                tickers=["300750.SZ"],
                current_date="20240329",
                code=code,
                lookback_days=30,
            )
        text = _text(result)

        assert "experiment_validated" in text
        assert "CODE_ERROR" not in text
        mock_repair_code.assert_not_called()

    @patch("backend.tools.analysis_tools.get_prices")
    def test_run_indicator_executes_experiment_fixed_code(
        self,
        mock_get_prices,
        tmp_path,
    ):
        prices = _sample_prices()
        mock_get_prices.return_value = prices
        code = 'result = {"latest_close": round(df["close"].iloc[-1], 2)}'
        from backend.tools.analysis_tools import _add_basic_technical_indicators
        from backend.tools.data_tools import prices_to_df

        df = _add_basic_technical_indicators(prices_to_df(prices))
        experiment_path = tmp_path / "experiment_code" / "execute_code_validated.jsonl"
        experiment_path.parent.mkdir()
        experiment_path.write_text(
            json.dumps(
                {
                    "cache_key": _code_repair_cache_key(code, df),
                    "success": True,
                    "indicator_id": "test_indicator_v1",
                    "description": "test indicator",
                    "lookback_days": 30,
                    "original_code": code,
                    "repaired_code": code,
                }
            )
            + "\n",
            encoding="utf-8",
        )

        with patch.dict(
            "os.environ",
            {
                "ENABLE_EXPERIMENT_CODE_CACHE": "1",
                "EXPERIMENT_CODE_PATH": str(experiment_path),
                "ENABLE_CODE_REPAIR_CACHE": "0",
                "ENABLE_LLM_CODE_REPAIR": "0",
            },
        ):
            result = run_indicator(
                indicator_id="test_indicator_v1",
                tickers=["300750.SZ"],
                current_date="20240329",
            )
        text = _text(result)

        assert "run_indicator (test_indicator_v1)" in text
        assert "latest_close" in text
        assert "34.5" in text

    @patch("backend.tools.analysis_tools.get_prices")
    def test_execute_code_success_is_saved_as_candidate_indicator(
        self,
        mock_get_prices,
        tmp_path,
    ):
        mock_get_prices.return_value = _sample_prices()
        candidate_path = tmp_path / "candidate_indicators.jsonl"
        code = """
result = round(df["close"].iloc[-1], 2)
"""

        with patch.dict(
            "os.environ",
            {
                "ENABLE_CANDIDATE_INDICATOR_CAPTURE": "1",
                "CANDIDATE_INDICATOR_PATH": str(candidate_path),
                "ENABLE_CODE_REPAIR_CACHE": "0",
                "ENABLE_EXPERIMENT_CODE_CACHE": "0",
                "ENABLE_LLM_CODE_REPAIR": "0",
            },
        ):
            result = execute_code(
                tickers=["300750.SZ"],
                current_date="20240329",
                code=code,
                lookback_days=30,
            )
        text = _text(result)
        records = [
            json.loads(line)
            for line in candidate_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

        assert "34.5" in text
        assert len(records) == 1
        assert records[0]["status"] == "candidate"
        assert records[0]["source"] == "generated"
        assert 'df["close"]' in records[0]["executable_code"]


class TestMacroAndRiskTools:
    @patch("backend.tools.analysis_tools.get_company_news_with_trace")
    def test_crawl_ths_news_marks_snapshot_transport(self, mock_get_company_news):
        mock_get_company_news.return_value = (
            [],
            {"transport": "local_snapshot", "source": "akshare"},
        )

        result = crawl_ths_news(["600519.SH"], "2024-03-29")
        text = _text(result)

        assert "[News Evidence] source=akshare, transport=local snapshot" in text

    def test_crawl_ths_concept_returns_local_a_share_tags(self):
        result = crawl_ths_concept(["600519.SH"])
        text = _text(result)

        assert "Concept Snapshot" in text
        assert "600519.SH" in text
        assert "白酒" in text

    def test_crawl_ths_position_summarizes_portfolio_exposure(self):
        result = crawl_ths_position(
            portfolio={
                "cash": 20000.0,
                "margin_used": 5000.0,
                "positions": {
                    "600519.SH": {"long": 10, "short": 0},
                    "300750.SZ": {"long": 5, "short": 0},
                },
            },
            current_prices={"600519.SH": 1600.0, "300750.SZ": 200.0},
        )
        text = _text(result)

        assert "Portfolio Position Snapshot" in text
        assert "Cash: 20,000.00" in text
        assert "Largest Position Weight" in text
        assert "600519.SH" in text

    def test_crawl_ths_position_ignores_extra_llm_arguments(self):
        result = crawl_ths_position(
            portfolio={"cash": 20000.0, "positions": {}},
            current_prices={"600519.SH": 1600.0},
            tickers=["600519.SH"],
            date="2024-01-02",
        )
        text = _text(result)

        assert "Portfolio Position Snapshot" in text
        assert "No open positions" in text

    @patch("backend.tools.analysis_tools.get_company_news_with_trace")
    def test_crawl_ths_event_flags_negative_headlines(self, mock_get_company_news):
        mock_get_company_news.return_value = (
            [
                {"headline": "贵州茅台 receives监管调查 notice", "source": "test", "date": "2024-03-29"},
            ],
            {"transport": "local_snapshot", "source": "akshare"},
        )

        result = crawl_ths_event(["600519.SH"], "2024-03-29")
        text = _text(result)

        assert "Event Risk Snapshot" in text
        assert "Negative Event Count: 1" in text
        assert "监管调查" in text
