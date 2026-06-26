# -*- coding: utf-8 -*-
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.data.schema import CompanyNews, FinancialMetrics
from backend.tools import data_tools


def _sample_metric(ticker: str = "600519.SH") -> FinancialMetrics:
    return FinancialMetrics(
        ticker=ticker,
        report_period="2024-03-29",
        period="ttm",
        currency="CNY",
        market_cap=2.1e12,
        enterprise_value=2.0e12,
        price_to_earnings_ratio=28.5,
        price_to_book_ratio=9.3,
        price_to_sales_ratio=11.4,
        enterprise_value_to_ebitda_ratio=18.2,
        enterprise_value_to_revenue_ratio=10.7,
        free_cash_flow_yield=0.032,
        peg_ratio=1.4,
        gross_margin=0.91,
        operating_margin=0.52,
        net_margin=0.47,
        return_on_equity=0.34,
        return_on_assets=0.22,
        return_on_invested_capital=0.29,
        asset_turnover=0.48,
        inventory_turnover=3.1,
        receivables_turnover=25.0,
        days_sales_outstanding=14.0,
        operating_cycle=118.0,
        working_capital_turnover=1.8,
        current_ratio=4.2,
        quick_ratio=3.8,
        cash_ratio=2.5,
        operating_cash_flow_ratio=1.9,
        debt_to_equity=0.15,
        debt_to_assets=0.08,
        interest_coverage=35.0,
        revenue_growth=0.14,
        earnings_growth=0.16,
        book_value_growth=0.11,
        earnings_per_share_growth=0.15,
        free_cash_flow_growth=0.12,
        operating_income_growth=0.17,
        ebitda_growth=0.13,
        payout_ratio=0.52,
        earnings_per_share=59.3,
        book_value_per_share=177.8,
        free_cash_flow_per_share=42.1,
    )


class TestFinancialSnapshotCache:
    def test_save_and_load_financial_snapshot(self, tmp_path, monkeypatch):
        monkeypatch.setattr(data_tools, "_FINANCIAL_SNAPSHOT_DIR", tmp_path)

        metric = _sample_metric()
        data_tools._save_financial_snapshot(
            ticker="600519.SH",
            end_date="2024-03-29",
            period="ttm",
            limit=10,
            metrics=[metric],
        )

        loaded = data_tools._load_financial_snapshot(
            ticker="600519.SH",
            end_date="2024-03-29",
            period="ttm",
            limit=10,
        )

        assert len(loaded) == 1
        assert loaded[0].ticker == "600519.SH"
        assert loaded[0].market_cap == pytest.approx(2.1e12)

    def test_get_financial_metrics_falls_back_to_local_snapshot(
        self,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.setattr(data_tools, "_FINANCIAL_SNAPSHOT_DIR", tmp_path)

        metric = _sample_metric()
        data_tools._save_financial_snapshot(
            ticker="600519.SH",
            end_date="2024-03-29",
            period="ttm",
            limit=10,
            metrics=[metric],
        )

        monkeypatch.setattr(
            data_tools,
            "get_config",
            lambda: SimpleNamespace(
                source="financial_datasets",
                api_key="fake-key",
            ),
        )

        # A股走 akshare 路径，离线时加载器返回空 → 应回退到本地快照。
        # （快照只补缺失字段，故需 akshare 完全无数据才能验证纯快照回退。）
        monkeypatch.setattr(
            data_tools,
            "_load_a_share_financial_metrics_from_akshare",
            lambda *_a, **_k: [],
        )
        # 让任何残留缓存未命中，确保走实际回退逻辑
        monkeypatch.setattr(
            data_tools._cache, "get_financial_metrics", lambda *_a, **_k: None
        )

        metrics = data_tools.get_financial_metrics(
            ticker="600519.SH",
            end_date="2024-03-29",
        )

        assert len(metrics) == 1
        assert metrics[0].ticker == "600519.SH"
        assert metrics[0].price_to_earnings_ratio == pytest.approx(28.5)


class TestCompanyNewsSnapshotCache:
    def test_save_and_load_company_news_snapshot(self, tmp_path, monkeypatch):
        monkeypatch.setattr(data_tools, "_COMPANY_NEWS_SNAPSHOT_DIR", tmp_path)

        news = [
            CompanyNews(
                ticker="600519.SH",
                title="茅台渠道价格企稳",
                source="Eastmoney",
                date="2024-03-28",
                url="https://example.com/1",
                summary="渠道价格企稳，终端需求平稳。",
                category="a_share_news",
            ),
            CompanyNews(
                ticker="600519.SH",
                title="茅台一季报预期改善",
                source="Eastmoney",
                date="2024-03-20",
                url="https://example.com/2",
                summary="机构上调盈利预期。",
                category="a_share_news",
            ),
        ]

        data_tools._save_company_news_snapshot(
            ticker="600519.SH",
            start_date="2024-03-01",
            end_date="2024-03-29",
            limit=10,
            source="akshare",
            news=news,
        )

        loaded = data_tools._load_company_news_snapshot(
            ticker="600519.SH",
            start_date="2024-03-01",
            end_date="2024-03-29",
            limit=10,
            source="akshare",
        )

        assert len(loaded) == 2
        assert loaded[0].title == "茅台渠道价格企稳"
        assert loaded[1].title == "茅台一季报预期改善"

    def test_load_company_news_snapshot_can_reuse_nearest_file(
        self,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.setattr(data_tools, "_COMPANY_NEWS_SNAPSHOT_DIR", tmp_path)

        news = [
            CompanyNews(
                ticker="600519.SH",
                title="茅台渠道价格企稳",
                source="Eastmoney",
                date="2024-03-28",
                url="https://example.com/1",
                summary="渠道价格企稳，终端需求平稳。",
                category="a_share_news",
            ),
            CompanyNews(
                ticker="600519.SH",
                title="茅台一季报预期改善",
                source="Eastmoney",
                date="2024-03-20",
                url="https://example.com/2",
                summary="机构上调盈利预期。",
                category="a_share_news",
            ),
            CompanyNews(
                ticker="600519.SH",
                title="四月新闻不应泄漏到三月回测",
                source="Eastmoney",
                date="2024-04-05",
                url="https://example.com/3",
                summary="这条新闻日期晚于请求截止日。",
                category="a_share_news",
            ),
        ]

        data_tools._save_company_news_snapshot(
            ticker="600519.SH",
            start_date=None,
            end_date="2024-04-30",
            limit=50,
            source="akshare",
            news=news,
        )

        loaded = data_tools._load_company_news_snapshot(
            ticker="600519.SH",
            start_date="2024-03-01",
            end_date="2024-03-29",
            limit=10,
            source="akshare",
        )

        assert len(loaded) == 2
        assert all(item.date <= "2024-03-29" for item in loaded if item.date)
        assert all("四月新闻" not in item.title for item in loaded)

    def test_get_company_news_with_trace_prefers_local_snapshot(
        self,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.setattr(data_tools, "_COMPANY_NEWS_SNAPSHOT_DIR", tmp_path)

        news = [
            CompanyNews(
                ticker="600519.SH",
                title="茅台渠道价格企稳",
                source="Eastmoney",
                date="2024-03-28",
                url="https://example.com/1",
                summary="渠道价格企稳，终端需求平稳。",
                category="a_share_news",
            ),
        ]
        data_tools._save_company_news_snapshot(
            ticker="600519.SH",
            start_date=None,
            end_date="2024-03-29",
            limit=5,
            source="akshare",
            news=news,
        )

        loaded, trace = data_tools.get_company_news_with_trace(
            ticker="600519.SH",
            end_date="2024-03-29",
            limit=5,
        )

        assert len(loaded) == 1
        assert loaded[0].title == "茅台渠道价格企稳"
        assert trace["transport"] == "local_snapshot"
        assert trace["source"] == "akshare"
