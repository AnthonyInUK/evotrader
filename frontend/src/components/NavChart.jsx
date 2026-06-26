import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ReferenceLine, ResponsiveContainer,
} from 'recharts';
import { COMBINED, toRetPct } from '../data/navData';

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-[#1a1d26] border border-white/10 rounded-lg p-3 font-mono text-[11px]">
      <p className="text-slate-400 mb-2">2024 · {label}</p>
      {payload.map((p) => {
        const name = p.dataKey === 'memory' ? '有记忆' : p.dataKey === 'noMemory' ? '无记忆' : '基准';
        const ret = toRetPct(p.value);
        return (
          <div key={p.dataKey} className="flex justify-between gap-6 my-0.5">
            <span style={{ color: p.color }}>{name}</span>
            <span style={{ color: p.color }}>
              ¥{p.value?.toLocaleString()} ({ret > 0 ? '+' : ''}{ret}%)
            </span>
          </div>
        );
      })}
    </div>
  );
};

export default function NavChart() {
  return (
    <div className="bg-[#0f1117] border border-white/[0.06] rounded-xl p-6">
      <div className="flex items-start justify-between mb-5">
        <div>
          <p className="text-[13px] font-medium text-slate-200">净值曲线对比</p>
          <p className="text-[12px] text-slate-500 mt-0.5">2024年1月 · 有记忆 vs 无记忆 vs 基准</p>
        </div>
        <div className="flex gap-5">
          {[
            { color: '#00d48a', label: '有记忆', dashed: false },
            { color: '#4da6ff', label: '无记忆', dashed: true },
            { color: '#4a4f5c', label: '基准', dashed: true },
          ].map(({ color, label, dashed }) => (
            <div key={label} className="flex items-center gap-1.5 text-[11px] text-slate-400 font-mono">
              <span
                style={{
                  display: 'inline-block',
                  width: 18,
                  height: dashed ? 0 : 2,
                  borderTop: dashed ? `1.5px dashed ${color}` : 'none',
                  background: dashed ? 'none' : color,
                  borderRadius: 1,
                }}
              />
              {label}
            </div>
          ))}
        </div>
      </div>

      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={COMBINED} margin={{ top: 4, right: 8, left: 4, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 6" stroke="rgba(255,255,255,0.04)" />
          <XAxis
            dataKey="date"
            tick={{ fill: '#3a3f4c', fontSize: 10, fontFamily: 'IBM Plex Mono' }}
            tickLine={false}
            axisLine={{ stroke: 'rgba(255,255,255,0.05)' }}
          />
          <YAxis
            domain={[496000, 517000]}
            tick={{ fill: '#3a3f4c', fontSize: 10, fontFamily: 'IBM Plex Mono' }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v) => `¥${(v / 1000).toFixed(0)}k`}
          />
          <Tooltip content={<CustomTooltip />} />
          <ReferenceLine y={500000} stroke="rgba(255,255,255,0.06)" strokeDasharray="4 4" />
          <Line type="monotone" dataKey="memory" stroke="#00d48a" strokeWidth={2} dot={false} activeDot={{ r: 4, fill: '#00d48a' }} />
          <Line type="monotone" dataKey="noMemory" stroke="#4da6ff" strokeWidth={2} strokeOpacity={0.7} strokeDasharray="5 3" dot={false} activeDot={{ r: 4, fill: '#4da6ff' }} />
          <Line type="monotone" dataKey="benchmark" stroke="#4a4f5c" strokeWidth={1.5} strokeDasharray="3 3" dot={false} />
        </LineChart>
      </ResponsiveContainer>

      <div className="mt-4 px-4 py-3 bg-[#00d48a]/[0.04] border border-[#00d48a]/10 rounded-lg">
        <p className="font-mono text-[11px] text-slate-400">
          <span className="text-[#00d48a]">▲ 关键分叉</span>
          {'　'}1月11日：无记忆版跌至 ¥497,244（−0.55%），有记忆版同日首次建仓后开始上涨
        </p>
      </div>
    </div>
  );
}
