import React from 'react';

const LAYERS = [
  {
    k: '01',
    title: 'Agent Decision Layer',
    subtitle: '4 analysts + Risk Manager + PM',
    body: 'ReActAgent / MsgHub 编排日级流程：分析师并行分析，会议阶段串行讨论，PM 汇总结构化交易决策。',
    tags: ['Parallel analysis', 'Conference transcript', 'Structured decision']
  },
  {
    k: '02',
    title: 'Tool & Data Layer',
    subtitle: 'Python tools + finance-mcp + PostgreSQL',
    body: '按角色隔离工具白名单，覆盖行情、财务、研报、股东、题材、技术指标与估值；数据库落选股候选、市场数据、信号、决策、回测 run 和风险事件。',
    tags: ['24 local tools', '21 MCP allowlist items', 'A-share data']
  },
  {
    k: '03',
    title: 'Strategy Config Layer',
    subtitle: 'YAML-driven multi-strategy setup',
    body: '用 YAML 定义 momentum_v1 / contrarian_v1 等策略，配置固定股票池或 selector 选股、lookback、rebalance、risk limits 和 Agent 角色组合。',
    tags: ['universe selection', 'momentum_v1', 'risk limits']
  },
  {
    k: '04',
    title: 'Risk & Evaluation Layer',
    subtitle: 'Risk guard + quant metrics',
    body: '交易前检查仓位、回撤、相关性、集中度与 A 股成交约束；回测后输出 Sharpe、最大回撤、IC、ICIR、胜率，并沉淀选股归因和失败案例复盘。',
    tags: ['Execution checks', 'Selection attribution', 'Decision audit']
  },
  {
    k: '05',
    title: 'Service & Scheduling Layer',
    subtitle: 'FastAPI + APScheduler',
    body: 'FastAPI 提供运行策略、查信号、查指标、策略对比、查风险事件与失败重试；APScheduler 支持工作日 16:00 自动跑每日分析。',
    tags: ['/analysis/run', '/analysis/{id}/retry', '16:00 weekday job']
  },
  {
    k: '06',
    title: 'Governance Layer',
    subtitle: 'Trace + data quality + auth',
    body: '按 run_id 记录 selection、market data、Agent analysis、risk/execution、persistence 等 stage 的耗时和状态；输出数据质量报告，并支持 API token 鉴权。',
    tags: ['stage trace', 'data quality', 'x-api-key']
  }
];

const ENDPOINTS = [
  'POST /api/v1/analysis/run',
  'POST /api/v1/analysis/run-all',
  'POST /api/v1/analysis/:run_id/retry',
  'GET /api/v1/runs/:run_id/trace',
  'GET /api/v1/runs/:run_id/data-quality',
  'GET /api/v1/selection/latest',
  'GET /api/v1/research/selection-attribution',
  'GET /api/v1/research/execution-checks/:run_id',
  'GET /api/v1/research/decision-audits/:run_id',
  'GET /api/v1/signals/latest',
  'GET /api/v1/strategies/compare',
  'GET /api/v1/risk-events'
];

export default function PlatformView() {
  return (
    <div style={{
      width: '100%',
      height: '100%',
      overflowY: 'auto',
      background: '#ffffff',
      color: '#111111',
      fontFamily: '"IBM Plex Mono", "Courier New", monospace',
      padding: '26px 34px'
    }}>
      <div style={{ maxWidth: 1080, margin: '0 auto' }}>
        <div style={{
          display: 'grid',
          gridTemplateColumns: '1.2fr 0.8fr',
          gap: 20,
          alignItems: 'stretch',
          marginBottom: 22
        }}>
          <div style={{
            border: '2px solid #000',
            padding: '18px 20px',
            background: '#fff'
          }}>
            <div style={{ fontSize: 11, letterSpacing: 2, color: '#666', marginBottom: 8 }}>
              PLATFORM OVERVIEW
            </div>
            <h2 style={{
              fontSize: 28,
              lineHeight: 1.12,
              margin: 0,
              letterSpacing: 0,
              fontWeight: 800
            }}>
              Multi-strategy Quant Agent Platform
            </h2>
            <p style={{
              fontSize: 13,
              lineHeight: 1.7,
              margin: '12px 0 0',
              color: '#444',
              maxWidth: 680
            }}>
              这个页用来回答“项目不是一个前端架子”。交易室展示 Agent 讨论，Backtest 展示实验结果，这里展示后端工程层次：Agent 编排、工具数据、策略配置、风控评估、服务调度。
            </p>
          </div>

          <div style={{
            border: '2px solid #000',
            padding: '18px 18px',
            background: '#f7f7f7'
          }}>
            <div style={{ fontSize: 11, letterSpacing: 2, color: '#666', marginBottom: 12 }}>
              VERIFIED SURFACE
            </div>
            <Metric label="DB smoke" value="11 tables" sub="trace / data quality / selection / attribution / execution / audit / decisions" />
            <Metric label="Tests" value="13 files" sub="200 test functions in repo context" />
            <Metric label="Replay" value="40 events" sub="4 daily Agent meetings loaded offline" />
          </div>
        </div>

        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(3, minmax(220px, 1fr))',
          gap: 12,
          marginBottom: 18
        }}>
          {LAYERS.map(layer => (
            <div key={layer.k} style={{
              border: '2px solid #111',
              background: '#fff',
              minHeight: 250,
              display: 'flex',
              flexDirection: 'column'
            }}>
              <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                borderBottom: '2px solid #111',
                padding: '9px 10px'
              }}>
                <span style={{ fontSize: 12, color: '#777' }}>{layer.k}</span>
                <span style={{
                  width: 9,
                  height: 9,
                  background: '#10b981',
                  border: '1px solid #111'
                }} />
              </div>
              <div style={{ padding: '12px 12px 14px', flex: 1 }}>
                <h3 style={{ fontSize: 15, lineHeight: 1.25, margin: 0, fontWeight: 800 }}>
                  {layer.title}
                </h3>
                <div style={{ fontSize: 11, color: '#666', marginTop: 6, lineHeight: 1.4 }}>
                  {layer.subtitle}
                </div>
                <p style={{ fontSize: 12, color: '#333', lineHeight: 1.65, margin: '12px 0 14px' }}>
                  {layer.body}
                </p>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 'auto' }}>
                  {layer.tags.map(tag => (
                    <span key={tag} style={{
                      border: '1px solid #bbb',
                      padding: '3px 6px',
                      fontSize: 10,
                      color: '#333',
                      background: '#fafafa'
                    }}>
                      {tag}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          ))}
        </div>

        <div style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 14
        }}>
          <div style={{ border: '2px solid #111', padding: 16, background: '#fff' }}>
            <div style={{ fontSize: 12, fontWeight: 800, marginBottom: 10 }}>
              API SURFACE
            </div>
            {ENDPOINTS.map(endpoint => (
              <div key={endpoint} style={{
                display: 'flex',
                justifyContent: 'space-between',
                gap: 10,
                padding: '8px 0',
                borderBottom: '1px solid #e5e5e5',
                fontSize: 12
              }}>
                <span>{endpoint}</span>
                <span style={{ color: '#10b981' }}>READY</span>
              </div>
            ))}
          </div>

          <div style={{ border: '2px solid #111', padding: 16, background: '#fff' }}>
            <div style={{ fontSize: 12, fontWeight: 800, marginBottom: 10 }}>
              RESUME ANGLE
            </div>
            <p style={{ fontSize: 13, lineHeight: 1.75, color: '#333', margin: 0 }}>
              可以说成：我把单次回测脚本升级成可配置、可服务化、可落库、可观测的多策略 Agent 量化平台；从 A 股缓存数据筛选股票池，进入 Agent 分析、PM 决策、RiskGuard 风控和 A 股执行约束，再把选股归因、交易检查、失败复盘、stage trace 与数据质量报告沉淀到 PostgreSQL 和 API/前端展示。
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value, sub }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ fontSize: 10, color: '#666', letterSpacing: 1.2, textTransform: 'uppercase' }}>
        {label}
      </div>
      <div style={{ fontSize: 24, fontWeight: 800, lineHeight: 1.1 }}>
        {value}
      </div>
      <div style={{ fontSize: 10, color: '#666', marginTop: 3 }}>
        {sub}
      </div>
    </div>
  );
}
