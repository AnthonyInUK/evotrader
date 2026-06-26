import { ANALYST_SIGNALS } from '../data/navData';

const SIGNAL_STYLE = {
  bull:    { bg: 'bg-[#00d48a]/10',  text: 'text-[#00d48a]',  label: 'BULL ▲' },
  bear:    { bg: 'bg-[#ff4d6d]/10',  text: 'text-[#ff4d6d]',  label: 'BEAR ▼' },
  neutral: { bg: 'bg-white/[0.06]',  text: 'text-slate-400',   label: 'NEUTRAL' },
};

function SignalBadge({ signal }) {
  const s = SIGNAL_STYLE[signal] || SIGNAL_STYLE.neutral;
  return (
    <span className={`font-mono text-[10px] px-2 py-0.5 rounded ${s.bg} ${s.text}`}>
      {s.label}
    </span>
  );
}

export default function SignalPanel() {
  return (
    <div className="space-y-4">
      <div className="bg-[#0f1117] border border-[#f5c842]/20 border-l-2 border-l-[#f5c842] rounded-r-xl px-4 py-3">
        <p className="font-mono text-[10px] text-[#f5c842] tracking-widest uppercase mb-1.5">记忆锚定效应 · 2月</p>
        <p className="text-[13px] text-slate-400 leading-relaxed">
          Valuation Analyst <span className="text-slate-200 font-medium">记住了1月底 DCF 高估32%的结论</span>，
          2月持续给出 bear 信号。PM 综合意见后持币观望，
          错过了基准 <span className="text-slate-200 font-medium">+3.69%</span> 的春节行情反弹。
        </p>
      </div>

      <div className="grid grid-cols-1 gap-3">
        {ANALYST_SIGNALS.map((a) => (
          <div key={a.agent} className="bg-[#0f1117] border border-white/[0.06] rounded-xl p-4">
            <div className="flex items-center justify-between mb-3">
              <p className="font-mono text-[11px] text-slate-400 tracking-wider uppercase">{a.agent}</p>
              <p className="font-mono text-[10px] text-slate-600">{a.model}</p>
            </div>
            <div className="space-y-1.5">
              {a.signals.map((s, i) => (
                <div key={i} className="flex items-center justify-between py-1 border-b border-white/[0.04] last:border-0">
                  <span className="font-mono text-[11px] text-slate-500">{s.date}</span>
                  <span className="font-mono text-[11px] text-slate-400">{s.ticker}.SH</span>
                  <SignalBadge signal={s.signal} />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
