CREATE TABLE IF NOT EXISTS market_data (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    date DATE NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume BIGINT NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'unknown',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (symbol, date, source)
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id BIGSERIAL PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    status TEXT NOT NULL,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS run_stage_traces (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT REFERENCES backtest_runs(id) ON DELETE CASCADE,
    strategy_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    latency_ms DOUBLE PRECISION,
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_type TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS data_quality_reports (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT REFERENCES backtest_runs(id) ON DELETE CASCADE,
    strategy_id TEXT NOT NULL,
    date DATE NOT NULL,
    report JSONB NOT NULL DEFAULT '{}'::jsonb,
    passed BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS signals (
    id BIGSERIAL PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    run_id BIGINT REFERENCES backtest_runs(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    symbol TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS selection_candidates (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT REFERENCES backtest_runs(id) ON DELETE CASCADE,
    strategy_id TEXT NOT NULL,
    date DATE NOT NULL,
    symbol TEXT NOT NULL,
    rank INTEGER NOT NULL,
    bucket TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    features JSONB NOT NULL DEFAULT '{}'::jsonb,
    selected BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS decisions (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT REFERENCES backtest_runs(id) ON DELETE CASCADE,
    strategy_id TEXT NOT NULL,
    date DATE NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    reasoning TEXT NOT NULL DEFAULT '',
    agent_votes JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS risk_events (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT REFERENCES backtest_runs(id) ON DELETE CASCADE,
    strategy_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    detail TEXT NOT NULL,
    triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS selection_attribution (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT REFERENCES backtest_runs(id) ON DELETE CASCADE,
    strategy_id TEXT NOT NULL,
    date DATE NOT NULL,
    symbol TEXT NOT NULL,
    rank INTEGER NOT NULL,
    bucket TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL DEFAULT 0,
    forward_return_1d DOUBLE PRECISION,
    forward_return_5d DOUBLE PRECISION,
    forward_return_10d DOUBLE PRECISION,
    benchmark_return_5d DOUBLE PRECISION,
    excess_return_5d DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS execution_checks (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT REFERENCES backtest_runs(id) ON DELETE CASCADE,
    strategy_id TEXT NOT NULL,
    date DATE NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    approved BOOLEAN NOT NULL,
    adjusted_quantity INTEGER NOT NULL DEFAULT 0,
    rejection_reason TEXT NOT NULL DEFAULT '',
    warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    transaction_cost DOUBLE PRECISION NOT NULL DEFAULT 0,
    capacity_ratio DOUBLE PRECISION,
    slippage_estimate DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS decision_audits (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT REFERENCES backtest_runs(id) ON DELETE CASCADE,
    strategy_id TEXT NOT NULL,
    date DATE NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    audit_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    detail TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_market_data_symbol_date
    ON market_data(symbol, date);
CREATE INDEX IF NOT EXISTS idx_signals_strategy_date
    ON signals(strategy_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_selection_strategy_date
    ON selection_candidates(strategy_id, date DESC, rank ASC);
CREATE INDEX IF NOT EXISTS idx_selection_run_id
    ON selection_candidates(run_id, rank ASC);
CREATE INDEX IF NOT EXISTS idx_decisions_run_id
    ON decisions(run_id);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_strategy_created
    ON backtest_runs(strategy_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_run_stage_traces_run_stage
    ON run_stage_traces(run_id, stage, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_data_quality_run_id
    ON data_quality_reports(run_id);
CREATE INDEX IF NOT EXISTS idx_risk_events_strategy_time
    ON risk_events(strategy_id, triggered_at DESC);
CREATE INDEX IF NOT EXISTS idx_selection_attr_strategy_date
    ON selection_attribution(strategy_id, date DESC, rank ASC);
CREATE INDEX IF NOT EXISTS idx_execution_checks_run_id
    ON execution_checks(run_id, symbol);
CREATE INDEX IF NOT EXISTS idx_decision_audits_run_id
    ON decision_audits(run_id, severity);
