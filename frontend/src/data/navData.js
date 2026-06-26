export const BASE = 500000;

export const MEMORY_NAV = [
  { date: '01/02', nav: 500000 },
  { date: '01/03', nav: 500000 },
  { date: '01/04', nav: 500000 },
  { date: '01/05', nav: 500000 },
  { date: '01/08', nav: 500000 },
  { date: '01/09', nav: 500000 },
  { date: '01/10', nav: 500000 },
  { date: '01/11', nav: 501071 },
  { date: '01/12', nav: 501109 },
  { date: '01/16', nav: 503509 },
  { date: '01/17', nav: 502009 },
  { date: '01/18', nav: 502309 },
  { date: '01/19', nav: 503509 },
  { date: '01/22', nav: 512809 },
  { date: '01/23', nav: 512809 },
  { date: '01/24', nav: 514909 },
  { date: '01/25', nav: 515036 },
  { date: '01/26', nav: 515036 },
  { date: '01/29', nav: 515036 },
  { date: '01/30', nav: 515036 },
  { date: '01/31', nav: 515036 },
];

export const NO_MEMORY_NAV = [
  { date: '01/02', nav: 500000 },
  { date: '01/03', nav: 500488 },
  { date: '01/04', nav: 501475 },
  { date: '01/05', nav: 501149 },
  { date: '01/08', nav: 501299 },
  { date: '01/09', nav: 501299 },
  { date: '01/10', nav: 500211 },
  { date: '01/11', nav: 497244 },
  { date: '01/12', nav: 498577 },
  { date: '01/16', nav: 501362 },
  { date: '01/17', nav: 499772 },
  { date: '01/18', nav: 500090 },
  { date: '01/19', nav: 501362 },
  { date: '01/22', nav: 501362 },
  { date: '01/23', nav: 501362 },
  { date: '01/24', nav: 503906 },
  { date: '01/25', nav: 504711 },
  { date: '01/26', nav: 506199 },
  { date: '01/29', nav: 507501 },
  { date: '01/30', nav: 506943 },
  { date: '01/31', nav: 507354 },
];

const BENCHMARK_FINAL = BASE * 1.0087;

export const COMBINED = MEMORY_NAV.map((d, i) => ({
  date: d.date,
  memory: d.nav,
  noMemory: NO_MEMORY_NAV[i].nav,
  benchmark: Math.round(BASE + (BENCHMARK_FINAL - BASE) * i / (MEMORY_NAV.length - 1)),
}));

export const toRetPct = (nav) => ((nav - BASE) / BASE * 100).toFixed(2);

export const ANALYST_SIGNALS = [
  {
    agent: 'Valuation Analyst',
    model: 'qwen-plus-2025-01-25',
    signals: [
      { date: '02/01', ticker: '600519', signal: 'neutral' },
      { date: '02/01', ticker: '601398', signal: 'bull' },
      { date: '02/02', ticker: '600519', signal: 'bear' },
      { date: '02/05', ticker: '600519', signal: 'bear' },
      { date: '02/06', ticker: '600519', signal: 'bull' },
      { date: '02/07', ticker: '600519', signal: 'bear' },
      { date: '02/08', ticker: '600519', signal: 'bear' },
      { date: '02/09', ticker: '600519', signal: 'bear' },
    ],
  },
  {
    agent: 'Technical Analyst',
    model: 'qwen-plus-2025-01-25',
    signals: [
      { date: '02/01', ticker: '600519', signal: 'bull' },
      { date: '02/01', ticker: '601398', signal: 'bull' },
      { date: '02/02', ticker: '600519', signal: 'neutral' },
      { date: '02/05', ticker: '600519', signal: 'bull' },
      { date: '02/06', ticker: '601398', signal: 'bull' },
      { date: '02/07', ticker: '600519', signal: 'bull' },
      { date: '02/08', ticker: '600519', signal: 'bull' },
      { date: '02/09', ticker: '600519', signal: 'neutral' },
    ],
  },
  {
    agent: 'Fundamentals Analyst',
    model: 'qwen-plus-2025-01-25',
    signals: [
      { date: '02/01', ticker: '600519', signal: 'bull' },
      { date: '02/02', ticker: '600519', signal: 'bull' },
      { date: '02/05', ticker: '600519', signal: 'bull' },
      { date: '02/06', ticker: '600519', signal: 'bull' },
      { date: '02/07', ticker: '600519', signal: 'bull' },
      { date: '02/08', ticker: '600519', signal: 'bull' },
      { date: '02/09', ticker: '600519', signal: 'bull' },
    ],
  },
];
