import { useState } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ReferenceLine, ResponsiveContainer,
} from 'recharts';

// ── Data ─────────────────────────────────────────────────────────────────────

const BASE = 500000;

// 无记忆版（local_test_memory）— 起始50万
const NO_MEM_NAV = [
  500000,500000,500000,500000,500000,500000,500000,
  501071,501109,503509,502009,502309,503509,
  512809,512809,514909,515036,515036,515036,515036,515036,
];
// 有记忆v1存事件（with_memory）— 起始52.8万，归一化到50万
const MEM_V1_BASE = 528121;
const MEM_V1_NAV = [
  528121,529360,526860,526296,524359,524060,524110,
  524617,524266,523903,517660,521047,523460,
  524160,521860,523838,525782,527202,528600,528042,527929,
];
// 有记忆v2存规律（with_memory_v2）— 起始50万，缺01/13、01/15用null
const MEM_V2_NAV = [
  500170,501140,502110,501722,501916,501916,501334,
  499976,500558,null,null,508124,507154,
  507348,507585,507585,507585,507936,509220,509500,510974,
];

const DATES = [
  '01/02','01/03','01/04','01/05','01/08','01/09','01/10',
  '01/11','01/12','01/13','01/14','01/16','01/17','01/18','01/19',
  '01/22','01/23','01/24','01/25','01/26','01/29',
];

const N = DATES.length;
// 基准：1月A股整体约-5.7%
const BENCH_FINAL_RET = -0.057;

// 转换成收益率%，方便公平对比
const toRet = (nav, base) => nav == null ? null : +((nav - base) / base * 100).toFixed(2);

const CHART_DATA = DATES.map((date, i) => ({
  date,
  noMemory: toRet(NO_MEM_NAV[i], BASE),
  memoryV1: toRet(MEM_V1_NAV[i], MEM_V1_BASE),
  memoryV2: toRet(MEM_V2_NAV[i], BASE),
  benchmark: +((BENCH_FINAL_RET * i / (N - 1)) * 100).toFixed(2),
}));

const pct = (v) => v == null ? '-' : `${v > 0 ? '+' : ''}${v}%`;

// ── Analyst signals (Feb, memory version) ────────────────────────────────────

const ANALYSTS = [
  {
    name: 'Valuation Analyst',
    note: 'DCF 驱动 · 记忆锚定',
    signals: [
      { date: '02/01', ticker: '600519', s: 'neutral' },
      { date: '02/01', ticker: '601398', s: 'bull' },
      { date: '02/02', ticker: '600519', s: 'bear' },
      { date: '02/05', ticker: '600519', s: 'bear' },
      { date: '02/06', ticker: '600519', s: 'bull' },
      { date: '02/07', ticker: '600519', s: 'bear' },
      { date: '02/08', ticker: '600519', s: 'bear' },
      { date: '02/09', ticker: '600519', s: 'bear' },
    ],
  },
  {
    name: 'Technical Analyst',
    note: '趋势 · 动量信号',
    signals: [
      { date: '02/01', ticker: '600519', s: 'bull' },
      { date: '02/01', ticker: '601398', s: 'bull' },
      { date: '02/02', ticker: '600519', s: 'neutral' },
      { date: '02/05', ticker: '600519', s: 'bull' },
      { date: '02/06', ticker: '601398', s: 'bull' },
      { date: '02/07', ticker: '600519', s: 'bull' },
      { date: '02/08', ticker: '600519', s: 'bull' },
      { date: '02/09', ticker: '600519', s: 'neutral' },
    ],
  },
  {
    name: 'Fundamentals Analyst',
    note: '基本面 · 财报分析',
    signals: [
      { date: '02/01', ticker: '600519', s: 'bull' },
      { date: '02/02', ticker: '600519', s: 'bull' },
      { date: '02/05', ticker: '600519', s: 'bull' },
      { date: '02/06', ticker: '600519', s: 'bull' },
      { date: '02/07', ticker: '600519', s: 'bull' },
      { date: '02/08', ticker: '600519', s: 'bull' },
      { date: '02/09', ticker: '600519', s: 'bull' },
    ],
  },
];

// ── Sub-components ────────────────────────────────────────────────────────────

const SIG = {
  bull:    { cls: 'bg-emerald-500/10 text-emerald-400', label: 'BULL ▲' },
  bear:    { cls: 'bg-rose-500/10 text-rose-400',       label: 'BEAR ▼' },
  neutral: { cls: 'bg-white/[0.06] text-slate-400',     label: 'NEUTRAL' },
};

function SignalBadge({ s }) {
  const { cls, label } = SIG[s] || SIG.neutral;
  return (
    <span className={`font-mono text-[10px] px-2 py-0.5 rounded ${cls}`}>{label}</span>
  );
}

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const names = { noMemory: '无记忆', memoryV1: '记忆v1(存事件)', memoryV2: '记忆v2(存规律)', benchmark: '基准' };
  const colors = { noMemory: '#34d399', memoryV1: '#60a5fa', memoryV2: '#a78bfa', benchmark: '#6b7280' };
  return (
    <div className="bg-[#1a1d26] border border-white/10 rounded-lg px-3 py-2.5 font-mono text-[11px] shadow-xl">
      <p className="text-slate-400 mb-1.5">2024 · {label}</p>
      {payload.filter(p => p.value != null).map(p => (
        <div key={p.dataKey} className="flex justify-between gap-5 my-0.5">
          <span style={{ color: colors[p.dataKey] }}>{names[p.dataKey]}</span>
          <span style={{ color: colors[p.dataKey] }}>
            {p.value > 0 ? '+' : ''}{p.value}%
          </span>
        </div>
      ))}
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────

const TABS = [
  { id: 'nav',      label: '净值对比' },
  { id: 'signals',  label: 'Analyst 信号' },
  { id: 'insights', label: '关键洞察' },
];

const METRICS = [
  { label: '无记忆 · 总收益',       value: '+3.01%', sub: '超额 +8.71% vs 基准', color: 'text-emerald-400', bar: 'bg-emerald-400' },
  { label: '记忆v1(存事件)· 总收益', value: '-0.04%', sub: '超额 +5.66% vs 基准', color: 'text-blue-400',    bar: 'bg-blue-400' },
  { label: '记忆v2(存规律)· 总收益', value: '+1.35%', sub: '超额 +7.05% vs 基准', color: 'text-violet-400',  bar: 'bg-violet-400' },
  { label: '基准（买入持有）',       value: '-5.70%', sub: '等权持仓 2024-01',    color: 'text-amber-400',   bar: 'bg-amber-400' },
];

const INSIGHTS = [
  {
    tag: '核心结论 · 记忆质量 > 记忆有无',
    accent: 'border-l-emerald-400',
    tagColor: 'text-emerald-400',
    body: '三组实验均跑赢基准（-5.70%），但记忆v1（存事件）表现最差（-0.04%），无记忆版反而最好（+3.01%）。关键发现：ReMeTask static_control 模式将原始决策轨迹写入记忆库，agent 学到的是"之前经常 HOLD"而不是市场规律，导致过度保守。改为存规律后（v2），超额收益从 +5.66% 提升至 +7.05%。',
  },
  {
    tag: '1月16日关键分叉 · 茅台反弹踏空',
    accent: 'border-l-rose-400',
    tagColor: 'text-rose-400',
    body: '无记忆版在1月16日因大量持有茅台，当天随大盘反弹净值大涨。v2（存规律）版因技术面信号偏空（MACD -14.5，价格低于SMA20/SMA50），判断茅台短期下行趋势未逆转而持币观望，错过了这波涨幅。这说明规律记忆让 agent 更理性，但在动量驱动的反弹行情中反而是劣势。',
  },
  {
    tag: 'AgentScope · 架构设计',
    accent: 'border-l-blue-400',
    tagColor: 'text-blue-400',
    body: '系统基于 AgentScope ReActAgent 构建，4位专业分析师（基本面、技术、情绪、估值）通过 MsgHub 并发独立分析，PM 汇总后决策，RiskGuard 执行硬约束（单票仓位≤30%）。记忆系统使用 ReMeTask 框架，向量化存储历史经验，每日决策前语义检索相关记忆。',
  },
];

export default function BacktestView() {
  const [tab, setTab] = useState('nav');

  return (
    <div className="h-full overflow-y-auto bg-[#0a0c10] px-6 py-8">
      <div className="max-w-4xl mx-auto">

        {/* Header */}
        <div className="flex items-start justify-between mb-8">
          <div>
            <p className="font-mono text-[10px] text-slate-600 tracking-[0.15em] uppercase mb-1">
              Backtest · 2024-01
            </p>
            <h2 className="text-[20px] font-semibold tracking-tight text-slate-100">
              长期记忆对比实验
            </h2>
            <p className="text-[12px] text-slate-500 mt-0.5">
              有记忆版 vs 无记忆版 · 初始资金 ¥500,000
            </p>
          </div>
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 badge-pulse" />
            <span className="font-mono text-[10px] text-emerald-400 tracking-wide">Complete</span>
          </div>
        </div>

        {/* Metrics */}
        <div className="grid grid-cols-4 gap-2.5 mb-6">
          {METRICS.map((m) => (
            <div key={m.label} className="bg-[#0f1117] border border-white/[0.05] rounded-xl p-4 relative overflow-hidden">
              <div className={`absolute top-0 left-0 right-0 h-[2px] ${m.bar}`} />
              <p className="font-mono text-[10px] text-slate-500 uppercase tracking-wide mb-2 leading-tight">{m.label}</p>
              <p className={`font-mono text-[22px] font-medium ${m.color}`}>{m.value}</p>
              <p className="font-mono text-[10px] text-slate-600 mt-1">{m.sub}</p>
            </div>
          ))}
        </div>

        {/* Tabs */}
        <div className="flex gap-1 bg-[#0f1117] border border-white/[0.04] rounded-lg p-1 w-fit mb-5">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`px-4 py-1.5 rounded-md font-mono text-[11px] tracking-wide transition-all cursor-pointer border ${
                tab === t.id
                  ? 'bg-[#1a1d26] text-slate-200 border-white/10'
                  : 'text-slate-500 border-transparent hover:text-slate-300'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* ── Tab: NAV Chart ── */}
        {tab === 'nav' && (
          <div className="bg-[#0f1117] border border-white/[0.05] rounded-xl p-5">
            <div className="flex items-start justify-between mb-4">
              <div>
                <p className="text-[13px] font-medium text-slate-200">净值曲线对比 · 2024年1月</p>
                <p className="text-[11px] text-slate-500 mt-0.5">三组实验对比 · 收益率归一化 · 基准为等权买入持有</p>
              </div>
              <div className="flex gap-4">
                {[
                  { color: '#34d399', label: '无记忆',       dashed: false },
                  { color: '#60a5fa', label: '记忆v1(事件)', dashed: true },
                  { color: '#a78bfa', label: '记忆v2(规律)', dashed: false },
                  { color: '#4b5563', label: '基准',         dashed: true },
                ].map(({ color, label, dashed }) => (
                  <div key={label} className="flex items-center gap-1.5 text-[10px] text-slate-500 font-mono">
                    <span style={{
                      display: 'inline-block',
                      width: 16,
                      height: dashed ? 0 : 2,
                      borderTop: dashed ? `1.5px dashed ${color}` : 'none',
                      background: dashed ? 'none' : color,
                      borderRadius: 1,
                    }} />
                    {label}
                  </div>
                ))}
              </div>
            </div>

            <ResponsiveContainer width="100%" height={280}>
              <LineChart data={CHART_DATA} margin={{ top: 4, right: 8, left: 4, bottom: 4 }}>
                <CartesianGrid strokeDasharray="3 6" stroke="rgba(255,255,255,0.03)" />
                <XAxis
                  dataKey="date"
                  tick={{ fill: '#374151', fontSize: 10, fontFamily: 'IBM Plex Mono' }}
                  tickLine={false}
                  axisLine={{ stroke: 'rgba(255,255,255,0.04)' }}
                />
                <YAxis
                  domain={[-8, 5]}
                  tick={{ fill: '#374151', fontSize: 10, fontFamily: 'IBM Plex Mono' }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v) => `${v > 0 ? '+' : ''}${v}%`}
                />
                <Tooltip content={<CustomTooltip />} />
                <ReferenceLine y={500000} stroke="rgba(255,255,255,0.05)" strokeDasharray="4 4" />
                <Line type="monotone" dataKey="noMemory"  stroke="#34d399" strokeWidth={2}   dot={false} activeDot={{ r: 3, fill: '#34d399' }} connectNulls />
                <Line type="monotone" dataKey="memoryV1"  stroke="#60a5fa" strokeWidth={2}   strokeDasharray="5 3" dot={false} activeDot={{ r: 3, fill: '#60a5fa' }} connectNulls />
                <Line type="monotone" dataKey="memoryV2"  stroke="#a78bfa" strokeWidth={2}   dot={false} activeDot={{ r: 3, fill: '#a78bfa' }} connectNulls />
                <Line type="monotone" dataKey="benchmark" stroke="#4b5563" strokeWidth={1.5} strokeDasharray="3 3" dot={false} />
              </LineChart>
            </ResponsiveContainer>

            <div className="mt-4 px-3 py-2.5 bg-emerald-500/[0.04] border border-emerald-500/10 rounded-lg">
              <p className="font-mono text-[11px] text-slate-400">
                <span className="text-emerald-400">▲ 关键结论</span>
                {'　'}记忆v1（存事件）跑输无记忆版，记忆v2（存规律）居中 · 记忆质量比有无记忆更重要 · 三组均跑赢基准（-5.7%）
              </p>
            </div>
          </div>
        )}

        {/* ── Tab: Signals ── */}
        {tab === 'signals' && (
          <div className="space-y-4">
            <div className="bg-[#0f1117] border border-white/[0.05] border-l-2 border-l-amber-400 rounded-r-xl px-4 py-3.5">
              <p className="font-mono text-[10px] text-amber-400 tracking-widest uppercase mb-1.5">记忆锚定效应 · 2月</p>
              <p className="text-[13px] text-slate-400 leading-relaxed">
                Valuation Analyst 记住了1月底 DCF 高估32%的结论，2月持续给出 bear 信号。
                PM 综合意见后持币观望，错过了基准 <span className="text-slate-200">+3.69%</span> 的春节行情反弹。
              </p>
            </div>

            <div className="grid grid-cols-1 gap-3">
              {ANALYSTS.map((a) => (
                <div key={a.name} className="bg-[#0f1117] border border-white/[0.05] rounded-xl p-4">
                  <div className="flex items-center justify-between mb-3">
                    <p className="font-mono text-[11px] text-slate-300 tracking-wide">{a.name}</p>
                    <p className="font-mono text-[10px] text-slate-600">{a.note}</p>
                  </div>
                  <div className="space-y-1">
                    {a.signals.map((s, i) => (
                      <div key={i} className="flex items-center justify-between py-1 border-b border-white/[0.03] last:border-0">
                        <span className="font-mono text-[10px] text-slate-600">{s.date}</span>
                        <span className="font-mono text-[10px] text-slate-500">{s.ticker}.SH</span>
                        <SignalBadge s={s.s} />
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── Tab: Insights ── */}
        {tab === 'insights' && (
          <div className="space-y-3">
            {INSIGHTS.map((ins) => (
              <div key={ins.tag} className={`bg-[#0f1117] border border-white/[0.05] border-l-2 ${ins.accent} rounded-r-xl px-5 py-4`}>
                <p className={`font-mono text-[10px] tracking-widest uppercase mb-2 ${ins.tagColor}`}>{ins.tag}</p>
                <p className="text-[13px] text-slate-400 leading-relaxed">{ins.body}</p>
              </div>
            ))}
          </div>
        )}

        {/* Footer */}
        <div className="mt-10 pt-4 border-t border-white/[0.04] flex justify-between">
          <span className="font-mono text-[10px] text-slate-700">AgentScope · 4 Analysts + PM + Risk Manager</span>
          <span className="font-mono text-[10px] text-slate-700">2024-01-02 ~ 2024-01-31</span>
        </div>

      </div>
    </div>
  );
}
