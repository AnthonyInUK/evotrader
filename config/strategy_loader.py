from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator


class RiskLimits(BaseModel):
    max_drawdown: float = Field(gt=0, lt=1)
    max_position_size: float = Field(gt=0, le=1)
    max_correlation: float = Field(gt=0, le=1)


class AgentConfig(BaseModel):
    role: str
    model: str

    @field_validator("role", "model")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("field must not be empty")
        return value


class SelectorConfig(BaseModel):
    top_n: int = Field(default=8, gt=0)
    lookback_days: int = Field(default=180, gt=0)
    theme_lookback_days: int = Field(default=120, gt=0)
    top_sectors: int = Field(default=8, gt=0)
    report_lag_days: int = Field(default=90, ge=0)
    require_financial: bool = False
    enable_regime_gating: bool = False
    regime_index: str = "sh000001"
    weak_allowed_buckets: list[str] = Field(
        default_factory=lambda: ["TECHNICAL_CANDIDATE"],
        min_length=1,
    )
    sideways_allowed_buckets: list[str] = Field(
        default_factory=lambda: ["TECHNICAL_CANDIDATE"],
        min_length=1,
    )
    rebound_allowed_buckets: list[str] = Field(
        default_factory=lambda: [
            "CORE_CANDIDATE",
            "ENTRY_SETUP_PULLBACK",
            "TECHNICAL_CANDIDATE",
        ],
        min_length=1,
    )
    allowed_buckets: list[str] = Field(
        default_factory=lambda: [
            "CORE_CANDIDATE",
            "ENTRY_SETUP_PULLBACK",
            "TACTICAL_CANDIDATE",
            "TECHNICAL_CANDIDATE",
        ],
        min_length=1,
    )

    @field_validator(
        "allowed_buckets",
        "weak_allowed_buckets",
        "sideways_allowed_buckets",
        "rebound_allowed_buckets",
    )
    @classmethod
    def _normalize_buckets(cls, value: list[str]) -> list[str]:
        buckets = [item.strip().upper() for item in value if item.strip()]
        if not buckets:
            raise ValueError("allowed_buckets must contain at least one bucket")
        return buckets


class StrategyConfig(BaseModel):
    strategy_id: str
    description: str
    universe: list[str] = Field(min_length=1)
    universe_mode: Literal["fixed", "selector"] = "fixed"
    selector: SelectorConfig | None = None
    lookback_days: int = Field(gt=0)
    rebalance_freq: Literal["daily", "weekly", "monthly"]
    risk_limits: RiskLimits
    agents: list[AgentConfig] = Field(min_length=1)

    @field_validator("strategy_id")
    @classmethod
    def _strategy_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("strategy_id must not be empty")
        return value

    @field_validator("universe")
    @classmethod
    def _normalize_universe(cls, value: list[str]) -> list[str]:
        symbols = [item.strip() for item in value if item.strip()]
        if not symbols:
            raise ValueError("universe must contain at least one symbol")
        return symbols


def load_strategy(path: str | Path) -> StrategyConfig:
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return StrategyConfig.model_validate(raw)


def load_all_strategies(directory: str | Path) -> list[StrategyConfig]:
    strategy_dir = Path(directory)
    if not strategy_dir.exists():
        return []
    return [
        load_strategy(path)
        for path in sorted(strategy_dir.glob("*.yaml"))
        if path.is_file()
    ]


def validate_config(config: StrategyConfig | dict) -> StrategyConfig:
    if isinstance(config, StrategyConfig):
        return config
    return StrategyConfig.model_validate(config)
